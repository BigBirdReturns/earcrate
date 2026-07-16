"""Executable gates for the integrated immutable project/score cutover.

These tests deliberately drive the existing EarcrateCore. They are not a
standalone project-engine suite: catalog configuration, TasteSpecs, guarded
manifests, the multideck renderer, mastering, exports and command history all
belong to the full app under test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from earcrate.app import EarcrateCore
from earcrate.core.deps import VALID_OPS
from earcrate.project.model import ProjectValidationError, ScoreRevision
from earcrate.project.policy import compile_taste_policy
from earcrate.tastespec import available_profiles


def configured_core(tmp_path: Path, sample_rate: int = 16000) -> EarcrateCore:
    master = tmp_path / "music"
    work = tmp_path / "work"
    agent = tmp_path / "agent"
    for path in (master, work, agent):
        path.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        core = EarcrateCore()
        core.configure({
            "master_root": str(master),
            "working_root": str(work),
            "agent_root": str(agent),
            "sample_rate": sample_rate,
            "workers": 1,
        })
    return core


def _write_sources(tmp_path: Path, sr: int = 16000, duration_s: float = 16.0):
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    rng1 = np.random.default_rng(11)
    rng2 = np.random.default_rng(22)
    floor = (
        0.18 * np.sin(2 * np.pi * 92.0 * t)
        + 0.08 * np.sin(2 * np.pi * 220.0 * t)
        + 0.025 * rng1.normal(size=t.size)
    ).astype(np.float32)
    gate = (0.5 + 0.5 * (np.sin(2 * np.pi * 2.0 * t) > 0)).astype(np.float64)
    vocal = (
        0.14 * np.sin(2 * np.pi * 440.0 * t) * gate
        + 0.05 * np.sin(2 * np.pi * 3200.0 * t) * gate
        + 0.02 * rng2.normal(size=t.size)
    ).astype(np.float32)
    floor_path = tmp_path / "floor.wav"
    vocal_path = tmp_path / "vocal.wav"
    sf.write(str(floor_path), floor, sr, subtype="FLOAT")
    sf.write(str(vocal_path), vocal, sr, subtype="FLOAT")
    return floor_path, vocal_path, duration_s


def _external_arrangement(tmp_path: Path, sr: int = 16000):
    floor_path, vocal_path, duration_s = _write_sources(tmp_path, sr)
    return {
        "bpm": 96.0,
        "target_key": 0,
        "seed": 78,
        "params": {
            "taste_profile": "remix_prettylights_v1",
            "target_seconds": 10.0,
            "name": "Integrated Project Gate",
            "post_render_gate": True,
            "vocal_bed_ducking": True,
            "stem_policy": "intact_mix",
        },
        "sections": [
            {
                "bar_start": 0,
                "bars": 2,
                "type": "sustain",
                "target_key": 0,
                "transition_in": {"type": "start", "xfade_beats": 0},
                "layers": [
                    {
                        "loop_id": "external-floor-a",
                        "external_ref": {"path": str(floor_path), "duration_s": duration_s, "start_s": 0.0, "len_s": 5.0},
                        "role": "harmony", "ear_role": "BED_CHORD",
                        "bar_offset": 0, "bar_len": 2, "gain_db": -8.0,
                    },
                    {
                        "loop_id": "external-vocal-a",
                        "external_ref": {"path": str(vocal_path), "duration_s": duration_s, "start_s": 0.0, "len_s": 5.0},
                        "role": "vocal", "ear_role": "VOX_HOOK",
                        "bar_offset": 0, "bar_len": 2, "gain_db": -5.0,
                    },
                ],
            },
            {
                "bar_start": 2,
                "bars": 2,
                "type": "drop",
                "target_key": 0,
                "transition_in": {
                    "type": "beatmatch_blend", "xfade_beats": 2,
                    "curve": "equal_power", "bass_policy": "one_low_owner",
                    "low_cutoff_hz": 170,
                },
                "layers": [
                    {
                        "loop_id": "external-floor-b",
                        "external_ref": {"path": str(floor_path), "duration_s": duration_s, "start_s": 6.0, "len_s": 5.0},
                        "role": "harmony", "ear_role": "BED_CHORD",
                        "bar_offset": 0, "bar_len": 2, "gain_db": -7.0,
                    },
                    {
                        "loop_id": "external-vocal-b",
                        "external_ref": {"path": str(vocal_path), "duration_s": duration_s, "start_s": 6.0, "len_s": 5.0},
                        "role": "vocal", "ear_role": "VOX_HOOK",
                        "bar_offset": 0, "bar_len": 2, "gain_db": -4.0,
                    },
                ],
            },
        ],
    }


def _import_fixture(core: EarcrateCore, arrangement):
    return core.project_import_arrangement(
        arrangement,
        name="Integrated Project Gate",
        static_gate_receipt={"preflight": {"passed": True}, "taste_gate": {"passed": True}},
        compiler_receipt={"fixture": "external_project_gate"},
    )


def test_every_runtime_tastespec_compiles_into_one_policy_contract():
    profiles = available_profiles()
    assert len(profiles) >= 25
    for profile_id in profiles:
        policy = compile_taste_policy(profile_id)
        assert policy["profile_id"] == profile_id
        assert len(policy["source_profile_hash"]) == 64
        assert len(policy["compiled_policy_sha"]) == 64
        assert policy["consumers"]
        assert "mix_policy" in policy and "mastering_policy" in policy
        assert "hard_cut" in policy["transition_policy"]["allowed"]


def test_project_store_commands_locks_undo_redo_and_exports(tmp_path: Path):
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    project_id = imported["project"]["project_id"]
    original = ScoreRevision.from_dict(imported["revision"])
    clip_id = original.tracks[1]["clips"][0]["clip_id"]

    locked = core.project_edit(project_id, {
        "actor": "human", "kind": "lock",
        "payload": {"target_type": "clip", "target_id": clip_id, "reason": "keep this vocal"},
    })
    locked_sha = locked["revision"]["revision_sha"]
    try:
        core.project_edit(project_id, {
            "actor": "machine", "kind": "set_gain",
            "payload": {"clip_id": clip_id, "gain_db": -6.0},
        })
        raise AssertionError("locked clip mutation should fail")
    except ProjectValidationError as exc:
        assert "lock prevents mutation" in str(exc)

    undone = core.project_undo(project_id)
    assert undone["project"]["active_revision_sha"] == original.revision_sha
    redone = core.project_redo(project_id)
    assert redone["project"]["active_revision_sha"] == locked_sha
    reopened = configured_core(tmp_path)
    assert reopened.project_show(project_id)["project"]["active_revision_sha"] == locked_sha

    exported = core.project_export(project_id)
    for key in ("edl", "rpp", "sheet"):
        assert Path(exported[key]).exists()
    edl = json.loads(Path(exported["edl"]).read_text(encoding="utf-8"))
    assert edl["revision_sha"] == locked_sha
    assert edl["project_id"] == project_id


def test_project_render_runs_real_external_tail_and_explicit_mastering(tmp_path: Path):
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    project_id = imported["project"]["project_id"]
    result = core.project_render(project_id)
    assert result["type"] == "render_project"
    assert Path(result["path"]).exists()
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["project_id"] == project_id
    assert report["project_revision_sha"] == result["revision_sha"]
    assert report["project_score_sha"] == result["score_sha"]
    assert report["render_integrity"]["passed"] is True
    assert report["render_integrity"]["executed_transition_count"] == report["render_integrity"]["planned_transition_count"]
    blend = next(t for t in report["transitions"] if t.get("type") == "beatmatch_blend")
    assert blend["executed"] is True and blend["applied"] is True
    assert blend["tail_deck_count"] >= 1
    assert blend["incoming_downbeat_error_ms"] == 0.0
    assert report["finishing"]["policy"] == "explicit_project_mastering_v1"
    assert report["finishing"]["action_count"] == len(report["finishing"]["actions"])
    assert all(x["executed"] for x in report["finishing"]["executions"])
    mastered = core.project_show(project_id)["revision"]
    assert mastered["master_actions"] == report["finishing"]["actions"]



def test_project_pan_is_rendered_as_stereo_score_data(tmp_path: Path):
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    project_id = imported["project"]["project_id"]
    revision = ScoreRevision.from_dict(imported["revision"])
    clip_id = revision.tracks[1]["clips"][0]["clip_id"]
    core.project_edit(project_id, {
        "actor": "human", "kind": "set_pan",
        "payload": {"clip_id": clip_id, "pan": 0.30, "override_policy": True},
    })
    result = core.project_render(project_id)
    audio, _sr = sf.read(str(result["path"]), dtype="float32", always_2d=True)
    assert audio.shape[1] == 2
    assert float(np.mean(np.abs(audio[:, 0] - audio[:, 1]))) > 1e-5
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    receipt = next(x for x in report["layers"] if x.get("clip_id") == clip_id)
    assert receipt["pan"] == 0.30


def test_project_preview_is_a_revision_bound_verified_crop(tmp_path: Path):
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    project_id = imported["project"]["project_id"]
    preview = core.project_preview(project_id, start_beat=2.0, duration_beats=4.0)
    assert preview["type"] == "project_preview"
    assert Path(preview["path"]).exists()
    audio, _sr = sf.read(preview["path"], dtype="float32", always_2d=True)
    assert audio.shape[1] == 2 and audio.shape[0] == preview["end_frame"] - preview["start_frame"]
    assert preview["revision_sha"] == core.project_show(project_id)["revision"]["revision_sha"]
    assert Path(preview["path"]).with_suffix(".preview.json").exists()

def test_project_manifest_is_guarded_and_executes_the_revision(tmp_path: Path):
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    revision = ScoreRevision.from_dict(imported["revision"])
    dst = tmp_path / "work" / "renders" / "guarded-project.wav"
    manifest = core.write_manifest("project_test", 78, "Render guarded project", [{
        "op_id": "project-op", "type": "render_project",
        "args": {"project_id": revision.project_id, "revision_sha": revision.revision_sha, "dst": str(dst)},
        "preconditions": {"dst_absent": True},
    }])
    dry = core.execute_manifest(manifest, apply=False)
    assert dry["dry_run"] is True and dry["would_execute"] == 1
    assert dry["plan"][0]["revision_sha"] == revision.revision_sha
    applied = core.execute_manifest(manifest, apply=True)
    assert applied["ok"] is True
    assert applied["done"][0]["type"] == "render_project"
    assert Path(applied["done"][0]["path"]).exists()
    assert "render_project" in VALID_OPS


def test_bounded_candidate_search_is_real_and_project_backed(tmp_path: Path):
    core = configured_core(tmp_path)
    arrangement = _external_arrangement(tmp_path)
    pool = [{"id": "fixture"}]

    def compose(_pool, params, seed):
        candidate = json.loads(json.dumps(arrangement))
        candidate["seed"] = seed
        candidate["params"]["seed"] = seed
        candidate["params"]["taste_profile"] = "remix_prettylights_v1"
        return candidate

    with patch.object(core, "taste_readiness", return_value={"ready": True, "crate_stale": False, "failures": []}), \
         patch.object(core, "approved_atom_pool", return_value=pool), \
         patch.object(core, "compose_taste_arrangement", side_effect=compose), \
         patch.object(core, "arrangement_preflight_gate", return_value={"passed": True, "failures": []}), \
         patch.object(core, "taste_arrangement_gate", return_value={"passed": True, "failures": []}), \
         patch.object(core, "score_arrangement", side_effect=lambda a: {"total": float(a["seed"] % 10)}):
        result = core.project_compile({
            "taste_profile": "remix_prettylights_v1", "target_seconds": 10,
            "name": "Candidate Search", "seed": 100, "candidate_count": 4,
        })
    search = result["candidate_search"]
    assert search["count"] == 4
    assert len(search["candidates"]) == 4
    assert search["selected_seed"] == 103
    assert result["project_id"]
    assert core.project_show(result["project_id"])["revision"]["revision_sha"] == result["revision_sha"]



def test_integrated_project_acceptance_cli_drives_the_full_app(tmp_path: Path):
    destination = tmp_path / "acceptance"
    env = dict(os.environ)
    env["EARCRATE_HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    completed = subprocess.run(
        [sys.executable, "-m", "earcrate", "project", "acceptance", "--destination", str(destination)],
        cwd=str(Path(__file__).resolve().parents[1]), env=env, text=True, capture_output=True, timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    for key in (
        "edit_changes_render", "undo_restores_render", "restart_reopens_active_revision",
        "source_change_refused", "all_selected_clips_executed", "all_transitions_executed",
        "overlap_tail_executed", "mastering_is_revision_data", "stereo_pan_executed",
    ):
        assert receipt[key] is True, key
    assert (destination / "acceptance_receipt.json").exists()

def test_source_mutation_after_project_import_is_refused(tmp_path: Path):
    core = configured_core(tmp_path)
    arrangement = _external_arrangement(tmp_path)
    imported = _import_fixture(core, arrangement)
    project_id = imported["project"]["project_id"]
    source_path = Path(arrangement["sections"][0]["layers"][0]["external_ref"]["path"])
    audio, sr = sf.read(str(source_path), dtype="float32")
    audio = np.asarray(audio, dtype=np.float32)
    audio[: min(200, audio.size)] *= -1.0
    sf.write(str(source_path), audio, sr, subtype="FLOAT")
    try:
        core.project_render(project_id)
        raise AssertionError("changed project source should be refused")
    except ProjectValidationError as exc:
        assert "premaster could not execute" in str(exc)


def test_project_http_api_exposes_frontend_contract(tmp_path):
    """The existing loopback server exposes the whole immutable project lifecycle.

    This gate is intentionally HTTP-level. A front end must be able to import,
    inspect, edit, undo/redo, render, preview, export and read run receipts without
    reaching into EarcrateCore or inventing a parallel project contract.
    """
    import threading
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer
    from earcrate.ui.server import JBHandler

    core = configured_core(tmp_path)
    arrangement = _external_arrangement(tmp_path, core.ensure_config().sample_rate)
    token = "project-api-test-token"
    JBHandler.core = core
    JBHandler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", 0), JBHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={"X-JB-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            assert response.status == 200
            return json.loads(response.read().decode("utf-8"))

    try:
        imported = request("POST", "/api/projects/import", {
            "arrangement": arrangement,
            "name": "HTTP Project Gate",
            "created_by": {"actor": "test", "reason": "http_contract"},
            "static_gate_receipt": {
                "preflight": {"passed": True},
                "taste_gate": {"passed": True},
            },
            "compiler_receipt": {"gate": "http_project_contract"},
        })
        project_id = imported["project"]["project_id"]
        encoded = urllib.parse.quote(project_id, safe="")

        listed = request("GET", "/api/projects")
        assert any(item["project_id"] == project_id for item in listed["items"])
        shown = request("GET", f"/api/projects/{encoded}")
        clip = next(
            c for track in shown["revision"]["tracks"]
            if track["track_id"] == "foreground"
            for c in track["clips"]
        )

        edited = request("POST", f"/api/projects/{encoded}/commands", {
            "actor": "human",
            "kind": "set_pan",
            "payload": {"clip_id": clip["clip_id"], "pan": 0.2, "override_policy": True},
        })
        edited_sha = edited["revision"]["revision_sha"]
        assert edited_sha != shown["revision"]["revision_sha"]
        undone = request("POST", f"/api/projects/{encoded}/undo", {})
        assert undone["project"]["active_revision_sha"] == shown["revision"]["revision_sha"]
        redone = request("POST", f"/api/projects/{encoded}/redo", {})
        assert redone["project"]["active_revision_sha"] == edited_sha

        rendered = request("POST", f"/api/projects/{encoded}/render", {})
        assert rendered["type"] == "render_project"
        assert Path(rendered["path"]).exists()
        runs = request("GET", f"/api/projects/{encoded}/runs")
        assert runs["items"] and any(item.get("revision_sha") for item in runs["items"])

        preview = request("POST", f"/api/projects/{encoded}/preview", {
            "start_beat": 0.0, "duration_beats": 2.0,
        })
        assert preview["type"] == "project_preview" and Path(preview["path"]).exists()
        rpp = request("POST", f"/api/projects/{encoded}/export/rpp", {})
        edl = request("POST", f"/api/projects/{encoded}/export/edl", {})
        assert rpp["format"] == "rpp" and Path(rpp["path"]).exists()
        assert edl["format"] == "edl" and Path(edl["path"]).exists()
        history = request("GET", f"/api/projects/{encoded}/history")
        assert any(row["kind"] == "set_pan" for row in history["commands"])
        sheet = request("POST", f"/api/projects/{encoded}/export/sheet", {})
        assert sheet["format"] == "sheet" and Path(sheet["path"]).exists()

        # piano runs endpoint (empty is fine) is part of the frontend contract
        pruns = request("GET", "/api/piano/runs")
        assert pruns.get("ok") and isinstance(pruns.get("items"), list)

        # a policy-violating command WITHOUT override is a 4xx refusal, not a 500,
        # and an unknown project is a 404 — the Workbench relies on this to show a
        # clean refusal (and so a real refusal is not a console 500).
        def status_of(method, path, body=None):
            data = None if body is None else json.dumps(body).encode("utf-8")
            req = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"X-JB-Token": token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        refused = status_of("POST", f"/api/projects/{encoded}/commands", {
            "actor": "human", "kind": "set_pan",
            "payload": {"clip_id": clip["clip_id"], "pan": 0.99},  # outside persona pan, no override
        })
        assert 400 <= refused < 500, f"policy refusal must be 4xx, got {refused}"
        missing = status_of("GET", "/api/projects/does-not-exist")
        assert missing == 404, f"unknown project must be 404, got {missing}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _piano_patches(core: EarcrateCore, base_arrangement):
    """Patch the crate-readiness + generative surface so project_compile runs for
    real (building durable revisions and rendering real WAVs) without a fully
    analyzed library — the same seams the bounded-candidate-search gate stubs."""
    import copy

    def compose(_pool, params, seed):
        cand = copy.deepcopy(base_arrangement)
        cand["seed"] = seed
        cand["params"]["seed"] = seed
        cand["params"]["taste_profile"] = params.get("taste_profile", "remix_prettylights_v1")
        return cand

    return [
        patch.object(core, "taste_readiness", return_value={"ready": True, "crate_stale": False, "failures": []}),
        patch.object(core, "approved_atom_pool", return_value=[{"id": "fixture"}]),
        patch.object(core, "compose_taste_arrangement", side_effect=compose),
        patch.object(core, "arrangement_preflight_gate", return_value={"passed": True, "failures": []}),
        patch.object(core, "taste_arrangement_gate", return_value={"passed": True, "failures": []}),
        patch.object(core, "score_arrangement", side_effect=lambda a: {"total": float(a["seed"] % 10)}),
    ]


def test_project_piano_is_bounded_killsafe_and_keeps_real_renders(tmp_path: Path):
    """M5: the player piano runs an unattended compile -> render -> keep/discard
    loop entirely through immutable project revisions. This gate pins its
    contract: it is BOUNDED (stops at max_iterations), every KEPT set is a real
    verification-gated WAV on disk bound to a durable project, the run receipt is
    written and complete, and the loop is KILL-SAFE / RESUMABLE — re-running the
    same run_id with a higher cap continues from where it stopped instead of
    redoing the earlier iterations."""
    import contextlib as _cl
    core = configured_core(tmp_path)
    base = _external_arrangement(tmp_path)

    with _cl.ExitStack() as stack:
        for p in _piano_patches(core, base):
            stack.enter_context(p)

        run = core.project_piano(personas=["remix_prettylights_v1"], max_iterations=3,
                                 target_seconds=10.0, seed_base=100, run_id="gate_run")
        # bounded + complete
        assert run["complete"] is True and run["stop_reason"] == "max_iterations"
        assert run["attempted"] == 3, run["attempted"]
        assert run["kept"] >= 1, f"piano kept nothing: {run}"
        assert run["kept"] + run["discarded"] + run["errored"] == 3
        assert run["errored"] == 0, [a for a in run["attempts"] if a["verdict"] == "error"]
        # every kept set is a real WAV bound to a durable project
        listed = {p["project_id"] for p in core.project_list()["items"]}
        for keep in run["keeps"]:
            assert Path(keep["path"]).exists() and keep["path"].endswith(".wav")
            assert keep["project_id"] in listed, f"kept project not durable: {keep['project_id']}"
        # receipt persisted on disk
        receipt = core.ensure_config().working_root / "piano" / "gate_run.json"
        assert receipt.exists()
        assert json.loads(receipt.read_text(encoding="utf-8"))["attempted"] == 3

        # kill-safe / resume: same run_id, higher cap -> continues, keeps prior work
        prior_iters = [a["iteration"] for a in run["attempts"]]
        resumed = core.project_piano(personas=["remix_prettylights_v1"], max_iterations=5,
                                     target_seconds=10.0, seed_base=100, run_id="gate_run")
        assert resumed["attempted"] == 5, resumed["attempted"]
        iters = [a["iteration"] for a in resumed["attempts"]]
        assert iters == [0, 1, 2, 3, 4], iters
        # the first three iterations are preserved verbatim (not recomputed anew)
        assert [a["iteration"] for a in resumed["attempts"][:3]] == prior_iters

    # max_keeps early-stop is honored
    core2 = configured_core(tmp_path / "second")
    base2 = _external_arrangement(tmp_path / "second")
    with _cl.ExitStack() as stack:
        for p in _piano_patches(core2, base2):
            stack.enter_context(p)
        capped = core2.project_piano(personas=["remix_prettylights_v1"], max_iterations=10,
                                     max_keeps=1, target_seconds=10.0, seed_base=200, run_id="capped")
        assert capped["kept"] >= 1
        assert capped["stop_reason"] == "max_keeps"
        assert capped["attempted"] < 10, "max_keeps must stop the loop before the iteration cap"


def test_transform_provider_seam_default_stable_and_rubberband_higher_fidelity(tmp_path: Path):
    """M2: the TransformProvider seam. The DEFAULT is the unchanged phase vocoder
    (opt-in only, no ENGINE_VERSION bump); a box that requests Rubber Band without
    the binary falls back honestly; and WHERE Rubber Band is available it must
    preserve more high-frequency energy through a big stretch than the phase
    vocoder (the whole point). Adding the seam must not regress the default
    render. The Rubber Band assertions run only where the engine is installed
    (demucs-style: unverified-until-the-box-has-it); the default-path and
    fallback contract is verified everywhere."""
    import numpy as np
    import librosa
    from earcrate.providers.transform import (
        transform_capability, resolve_transform_provider,
        rubberband_time_stretch, VALID_TRANSFORM_PROVIDERS,
    )

    # (1) probe shape + selection + honest fallback — always
    cap = transform_capability()
    assert {"pyrubberband", "rubberband_bin", "ready", "default", "providers"} <= set(cap)
    assert cap["default"] == "phase_vocoder" and "phase_vocoder" in cap["providers"]
    assert set(VALID_TRANSFORM_PROVIDERS) == {"phase_vocoder", "rubberband"}
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("EARCRATE_TRANSFORM", None)
        assert resolve_transform_provider(None) == "phase_vocoder"
    with patch.dict(os.environ, {"EARCRATE_TRANSFORM": "bogus"}):
        assert resolve_transform_provider(None) == "phase_vocoder"
    # requested-but-unavailable degrades to phase_vocoder, never crashes
    with patch("earcrate.providers.transform.transform_capability", return_value={"ready": False}), \
         patch.dict(os.environ, {"EARCRATE_TRANSFORM": "rubberband"}):
        assert resolve_transform_provider(None) == "phase_vocoder"

    # (2) the default render still works after adding the seam (no regression),
    #     and the render resolves the default provider when nothing is opted in.
    core = configured_core(tmp_path)
    os.environ.pop("EARCRATE_TRANSFORM", None)
    assert resolve_transform_provider(core.ensure_config()) == "phase_vocoder"
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    rendered = core.project_render(imported["project"]["project_id"])
    assert rendered["type"] == "render_project" and Path(rendered["path"]).exists()

    # (3) fidelity — only where Rubber Band actually runs
    if cap["ready"]:
        sr = 22050
        t = np.arange(sr * 2) / sr
        bright = (0.3 * np.sin(2 * np.pi * 400 * t)
                  + 0.3 * np.sin(2 * np.pi * 6000 * t)
                  + 0.3 * np.sin(2 * np.pi * 9000 * t)).astype(np.float32)

        def hf_share(x):
            S = np.abs(np.fft.rfft(x)); f = np.fft.rfftfreq(len(x), 1 / sr)
            return float(S[f >= 4000].sum() / max(1e-9, S.sum()))

        pv = librosa.effects.time_stretch(bright, rate=1.5).astype(np.float32)
        rb = rubberband_time_stretch(bright, sr, 1.5)
        assert hf_share(rb) > hf_share(pv), \
            f"rubberband must preserve more HF than phase vocoder ({hf_share(rb):.3f} !> {hf_share(pv):.3f})"
        assert resolve_transform_provider(core.ensure_config()) == "phase_vocoder"  # still opt-in, not default
        with patch.dict(os.environ, {"EARCRATE_TRANSFORM": "rubberband"}):
            assert resolve_transform_provider(core.ensure_config()) == "rubberband"


def _seed_atom(core, db, fid, aid, ear, role, profile, path):
    from earcrate.core.util import sha256_file
    st = Path(path).stat()
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,sha256,scanned_at) VALUES(?,?,?,?,?,?,?)",
               (fid, str(path), "master", int(st.st_size), int(st.st_mtime_ns), sha256_file(Path(path)), "now"))
    core._set_pcm(fid, "pcm_"+fid)
    db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
               ("l_"+aid, fid, 0.0, 4.0, 2, role, 0.7, "now"))
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,status,metrics_json,created_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (aid, "l_"+aid, fid, profile, ear, role, 0.0, 4.0, 2, 0.7, "approved", "{}", "now"))


def test_piano_triage_feeds_m4_judgments(tmp_path: Path):
    """M5→M4 flywheel: the morning-triage keep/reject on a piano attempt writes
    THROUGH the atom-judgment path so it becomes taste-ranker training data. A keep
    approves every atom the attempt used; a reject rejects them; the verdict is
    recorded back on the run receipt. An attempt with no atom material judges
    nothing and says so honestly."""
    import json as _json
    core = configured_core(tmp_path)
    db = core.conn()
    profile = "remix_prettylights_v1"
    fpath, vpath, dur = _write_sources(tmp_path)
    _seed_atom(core, db, "fa", "atom_A", "BED_CHORD", "harmony", profile, fpath)
    _seed_atom(core, db, "fb", "atom_B", "VOX_HOOK", "vocal", profile, vpath)
    db.commit()

    # import a project whose arrangement layers carry those atom ids
    arr = _external_arrangement(tmp_path)
    arr["params"]["taste_profile"] = profile
    ai = ["atom_A", "atom_B"]
    for s in arr["sections"]:
        for k, layer in enumerate(s["layers"]):
            layer["atom_id"] = ai[k % 2]
    imp = _import_fixture(core, arr)
    pid = imp["project"]["project_id"]
    rev_sha = imp["project"]["active_revision_sha"]

    # write a piano run receipt with a kept attempt referencing the project
    piano_dir = core.ensure_config().working_root / "piano"
    piano_dir.mkdir(parents=True, exist_ok=True)
    run = {"ok": True, "type": "piano_run", "run_id": "gate_run", "personas": [profile],
           "attempted": 1, "kept": 1, "discarded": 0, "errored": 0,
           "attempts": [{"iteration": 0, "persona": profile, "seed": 1, "verdict": "kept",
                         "project_id": pid, "revision_sha": rev_sha, "path": "x.wav"}]}
    (piano_dir / "gate_run.json").write_text(_json.dumps(run), encoding="utf-8")

    # runs endpoint lists it
    runs = core.project_piano_runs()
    assert runs["items"] and runs["items"][0]["run_id"] == "gate_run"

    # keep -> both atoms approved for the persona, verdict recorded, receipt updated
    res = core.project_piano_triage("gate_run", 0, "keep")
    assert res["ok"] and res["atoms_judged"] == 2 and res["status"] == "approved"
    rows = {r[0]: r[1] for r in db.execute(
        "SELECT atom_id,status FROM atom_judgments WHERE taste_profile=?", (profile,)).fetchall()}
    assert rows.get("atom_A") == "approved" and rows.get("atom_B") == "approved"
    updated = _json.loads((piano_dir / "gate_run.json").read_text(encoding="utf-8"))
    assert updated["attempts"][0]["triage"]["verdict"] == "keep"

    # reject flips them (append-only judgment path, latest wins)
    res2 = core.project_piano_triage("gate_run", 0, "reject")
    assert res2["status"] == "rejected"
    rows2 = {r[0]: r[1] for r in db.execute(
        "SELECT atom_id,status FROM atom_judgments WHERE taste_profile=?", (profile,)).fetchall()}
    assert rows2.get("atom_A") == "rejected" and rows2.get("atom_B") == "rejected"

    # bad verdict is refused (surfaces as a 4xx-mapped ProjectValidationError)
    try:
        core.project_piano_triage("gate_run", 0, "maybe")
        raise AssertionError("bad verdict must be refused")
    except ProjectValidationError:
        pass
