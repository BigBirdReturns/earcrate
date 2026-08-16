from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "earcrate_album_sprint_preflight.py"
CAMPAIGN = ROOT / "configs" / "album_one" / "sprint-01" / "campaign.v1.json"
PREFLIGHT = ROOT / "configs" / "album_one" / "sprint-01" / "executable-preflight.v1.json"
RUNNER = ROOT / "scripts" / "RUN_ALBUM_ONE_SPRINT_01.ps1"

_spec = importlib.util.spec_from_file_location("earcrate_album_sprint_preflight", SCRIPT)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def report_with_no_bindings():
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    bindings = module.load_bindings(None, campaign, verify_bytes=False)
    return campaign, preflight, module.build_report(campaign, preflight, bindings)


def test_preflight_contract_is_sealed_and_bound_to_campaign() -> None:
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    assert campaign["contract_sha256"] == "d6950f41246629762a717e66765a4b869afe4c500318cfccc46732c28bffcb2c"
    assert preflight["base_campaign_sha256"] == campaign["contract_sha256"]
    assert preflight["preflight_contract_sha256"] == "7e17d7ddc1657fe4d69cf0f04b491ad81fefcc520761f019898e398329458ba6"
    assert preflight["boundary"]["estate_execution_default"] == "closed"
    assert "machine_work_ready" not in campaign["terminal_states"]
    assert all((track.get("entrypoint") or {}).get("template") is None for track in campaign["tracks"])


def test_repo_only_retry_refuses_all_seven_lanes_for_exact_reasons() -> None:
    _campaign, _preflight, report = report_with_no_bindings()
    assert report["music_producing_lane_count"] == 0
    assert report["performance_realization_ready_count"] == 0
    assert report["estate_execution_authorized"] is False
    assert report["authorized_track_ids"] == []
    assert report["bindings_bytes_verified"] is False
    tracks = report["tracks"]

    a101 = tracks["A1-01"]
    assert a101["observations"]["retained_candidate_duration_seconds"] < 60.0
    assert {row["kind"] for row in a101["blockers"]} >= {
        "blocked_adapter_implementation", "blocked_full_form_adapter"
    }

    a102 = tracks["A1-02"]
    pack = next(row for row in a102["blockers"] if row["kind"] == "blocked_exact_artifact_pack")
    assert pack["missing_artifact_ids"] == [
        "score_pdf", "score_extraction", "score_reconstruction_midi",
        "score_proof_receipt", "mix_score", "mix_execution_ledger",
    ]
    assert next(row for row in a102["blockers"] if row["kind"] == "blocked_exact_source")["missing_binding_ids"] == ["exact_reference_audio"]
    assert next(row for row in a102["blockers"] if row["kind"] == "blocked_performance_material")["missing_binding_ids"] == ["approved_rack_library"]
    assert any(row["kind"] == "blocked_representative_invocation" for row in a102["blockers"])
    assert any(row["kind"] == "blocked_performance_adapter" for row in a102["blockers"])

    a103 = tracks["A1-03"]
    assert a103["symbolic_evidence_ready"] is True
    assert a103["observations"]["decoded_float_pcm_max_abs"] == 0.0
    assert a103["observations"]["witness_duration_seconds"] < 120.0
    assert a103["observations"]["executable_note_events_present"] is False
    source_blocker = next(row for row in a103["blockers"] if row["kind"] == "blocked_exact_source")
    assert source_blocker["missing_binding_ids"] == [
        "exact_bad_plus_performance_audio", "approved_piano_bass_drum_racks"
    ]

    for track_id in ("A1-04", "A1-05"):
        row = tracks[track_id]
        assert any(item["kind"] == "blocked_full_form_adapter" for item in row["blockers"])
        assert any(item["kind"] == "blocked_representative_invocation" for item in row["blockers"])
        assert row["observations"]["campaign_template_switches"] == []
        assert any(item["kind"] == "blocked_adapter_implementation" for item in row["blockers"])

    assert any(row["kind"] == "blocked_adapter_implementation" for row in tracks["A1-06"]["blockers"])
    assert any(row["kind"] == "blocked_full_form_adapter" for row in tracks["A1-07"]["blockers"])
    assert any(row["kind"] == "blocked_representative_invocation" for row in tracks["A1-07"]["blockers"])
    assert tracks["A1-07"]["observations"]["positive_arc_reapplication_deferred"] is True


def test_complete_verified_fake_bindings_do_not_launder_missing_music_adapters() -> None:
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw = {
            "kind": "earcrate_album_sprint_private_bindings",
            "contract_sha256": campaign["contract_sha256"],
            "tracks": {},
        }
        for track_id, spec in preflight["tracks"].items():
            rows = []
            for binding_id in spec["required_bindings"]:
                artifact = root / f"{track_id}-{binding_id}.bin"
                artifact.write_bytes(b"fixture")
                rows.append({"binding_id": binding_id, "artifact_path": str(artifact)})
            raw["tracks"][track_id] = {"bindings": rows}
        path = root / "bindings.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        bindings = module.load_bindings(path, campaign, verify_bytes=True)
        report = module.build_report(
            campaign,
            preflight,
            bindings,
            bindings_bytes_verified=True,
        )
        assert report["bindings_bytes_verified"] is True
        assert report["music_producing_lane_count"] == 0
        assert report["estate_execution_authorized"] is False
        assert report["authorized_track_ids"] == []


def test_workspace_application_removes_all_unauthorized_instructions() -> None:
    _campaign, _preflight, report = report_with_no_bindings()
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        for track_id in report["tracks"]:
            root = workspace / "tracks" / track_id
            root.mkdir(parents=True, exist_ok=True)
            (root / "NEXT_COMMAND.ps1").write_text("Write-Host should-not-run\n", encoding="utf-8")
            (root / "NEXT_COMMAND.txt").write_text("agent should-not-run\n", encoding="utf-8")
        removed = module.apply_workspace(report, workspace)
        assert len(removed) == 14
        assert not list(workspace.rglob("NEXT_COMMAND.ps1"))
        assert not list(workspace.rglob("NEXT_COMMAND.txt"))
        stored = json.loads((workspace / "PREFLIGHT.json").read_text(encoding="utf-8"))
        assert stored["report_sha256"] == report["report_sha256"]


def test_powershell_uses_preflight_authority_before_execution() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "earcrate_album_sprint_preflight.py" in text
    assert "PreflightOnly" in text
    assert "estate_execution_authorized" in text
    assert "authorized_track_ids" in text
    assert "ExecuteReadyAdapters requires -VerifyBytes" in text
    assert "No complete music-producing Album adapter passed preflight" in text
    assert "Get-ChildItem" not in text or "NEXT_COMMAND.ps1 -Recurse" not in text
