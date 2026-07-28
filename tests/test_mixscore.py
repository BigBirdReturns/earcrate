from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.mix.model import (
    MixScoreError,
    mixscore_capability,
    mixscore_load,
    mixscore_seal,
)
from earcrate.mix.render import mixscore_build_demo, mixscore_render


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(audio, dtype=np.float64))) + 1e-18))


def test_mixscore_renders_independent_decks_and_executes_dj_operations(tmp_path: Path) -> None:
    result = mixscore_build_demo(tmp_path, sample_rate=12_000)
    receipt = result["receipt"]
    assert result["ok"] is True and result["complete"] is True
    assert receipt["complete"] is True
    assert receipt["deck_count"] == 2
    assert receipt["selected_event_count"] == receipt["executed_event_count"] == 15
    assert receipt["refused_event_count"] == 0
    assert receipt["stem_reconciliation_max_abs"] == 0.0

    master, sample_rate = sf.read(result["output_path"], dtype="float32", always_2d=True)
    stem_a, rate_a = sf.read(result["stem_paths"]["A"], dtype="float32", always_2d=True)
    stem_b, rate_b = sf.read(result["stem_paths"]["B"], dtype="float32", always_2d=True)
    assert sample_rate == rate_a == rate_b == 12_000
    assert master.shape == stem_a.shape == stem_b.shape
    assert np.max(np.abs(master - (stem_a + stem_b))) == 0.0

    frames_per_beat = int(round(sample_rate * 60.0 / 120.0))
    assert np.max(np.abs(stem_b[: 4 * frames_per_beat])) == 0.0
    assert _rms(stem_b[7 * frames_per_beat : 8 * frames_per_beat]) > (
        _rms(stem_b[4 * frames_per_beat : 5 * frames_per_beat]) * 4.0
    )
    assert _rms(stem_a[8 * frames_per_beat : 12 * frames_per_beat]) > 0.01
    assert _rms(stem_b[8 * frames_per_beat : 12 * frames_per_beat]) > 0.005
    assert np.max(np.abs(stem_a[20 * frames_per_beat :])) == 0.0
    assert _rms(stem_b[20 * frames_per_beat :]) > 0.005

    first_loop = stem_b[12 * frames_per_beat : 14 * frames_per_beat, 0].astype(np.float64)
    second_loop = stem_b[14 * frames_per_beat : 16 * frames_per_beat, 0].astype(np.float64)
    first_loop /= max(1e-12, np.linalg.norm(first_loop))
    second_loop /= max(1e-12, np.linalg.norm(second_loop))
    assert float(np.dot(first_loop, second_loop)) > 0.90

    ledger = json.loads(Path(result["execution_ledger_path"]).read_text(encoding="utf-8"))
    assert ledger["complete"] is True
    assert all(row["status"] == "executed" for row in ledger["events"])
    operations = [row["op"] for row in ledger["events"]]
    for required in ("play", "fade", "crossfade", "jump", "loop", "exit_loop", "cut"):
        assert required in operations


def test_mixscore_is_deterministic_and_refuses_changed_source_identity(tmp_path: Path) -> None:
    demo = mixscore_build_demo(tmp_path, sample_rate=8_000)
    score, base_dir = mixscore_load(demo["score_path"])
    first = mixscore_render(score, base_dir=base_dir)
    second = mixscore_render(score, base_dir=base_dir)
    assert first["receipt"]["receipt_sha256"] == second["receipt"]["receipt_sha256"]
    assert first["receipt"]["master_pcm_f32le_sha256"] == second["receipt"]["master_pcm_f32le_sha256"]
    assert first["execution_ledger"]["ledger_sha256"] == second["execution_ledger"]["ledger_sha256"]
    assert np.array_equal(first["audio"], second["audio"])

    source = Path(demo["asset_paths"][0])
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    audio[0, 0] = np.float32(audio[0, 0] + 0.125)
    sf.write(source, audio, sample_rate, subtype="FLOAT")
    try:
        mixscore_render(score, base_dir=base_dir)
    except MixScoreError as exc:
        assert "source identity changed" in str(exc)
    else:
        raise AssertionError("modified source identity was not refused")


def test_mixscore_capability_and_cli_demo_use_the_real_renderer(tmp_path: Path) -> None:
    capability = mixscore_capability()
    assert capability["ready"] is True
    assert capability["features"]["independent_playheads"] is True
    assert capability["features"]["simultaneous_decks"] is True
    assert capability["requires_network"] is False

    output = tmp_path / "cli-demo"
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    run = subprocess.run(
        [sys.executable, "-m", "earcrate.mix", "demo", str(output), "--sample-rate", "8000"],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ok"] is True and payload["complete"] is True
    assert Path(payload["output_path"]).is_file()
    assert Path(payload["receipt_path"]).is_file()
    assert sorted(payload["stem_paths"]) == ["A", "B"]


def test_mixscore_operation_contract_and_n_deck_routing_are_exercised(tmp_path: Path) -> None:
    demo = mixscore_build_demo(tmp_path / "base", sample_rate=8_000)
    demo_ledger = json.loads(Path(demo["execution_ledger_path"]).read_text(encoding="utf-8"))
    sealed, base_dir = mixscore_load(demo["sealed_score_path"])
    score = mixscore_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_mix_score",
            "title": "operation and N-deck gate",
            "clock": deepcopy(sealed["clock"]),
            "end_beat": 8.0,
            "peak_ceiling": 0.92,
            "master_gain_db": -3.0,
            "assets": deepcopy(sealed["assets"]),
            "decks": [
                {"deck_id": "A", "crossfader_side": "A", "gain_db": -3.0, "pan": 0.0},
                {"deck_id": "B", "crossfader_side": "B", "gain_db": -3.0, "pan": 0.0},
                {"deck_id": "C", "crossfader_side": "NONE", "gain_db": -9.0, "pan": 0.0},
            ],
            "events": [
                {"at_beat": 0.0, "deck_id": "A", "op": "load", "asset_id": "deck-a-source"},
                {"at_beat": 0.0, "deck_id": "A", "op": "play", "cue": "start", "sync": True},
                {"at_beat": 0.0, "deck_id": "B", "op": "load", "asset_id": "deck-b-source"},
                {"at_beat": 0.0, "deck_id": "B", "op": "play", "cue": "start", "sync": True},
                {"at_beat": 0.0, "deck_id": "C", "op": "load", "asset_id": "deck-b-source"},
                {"at_beat": 0.0, "deck_id": "C", "op": "play", "cue": "hook", "sync": True},
                {"at_beat": 0.0, "op": "set_crossfader", "position": -1.0},
                {"at_beat": 1.0, "deck_id": "C", "op": "set_pan", "pan": -1.0},
                {"at_beat": 2.0, "deck_id": "A", "op": "set_rate", "rate": 0.5, "sync": False},
                {"at_beat": 2.0, "deck_id": "C", "op": "mute"},
                {"at_beat": 3.0, "deck_id": "A", "op": "nudge", "delta_source_beats": 1.0},
                {"at_beat": 3.0, "deck_id": "C", "op": "unmute"},
                {"at_beat": 4.0, "deck_id": "A", "op": "stop"},
                {"at_beat": 5.0, "deck_id": "A", "op": "play", "source_beat": 8.0, "sync": True},
                {"at_beat": 6.0, "deck_id": "A", "op": "seek", "source_beat": 12.0},
                {"at_beat": 6.0, "op": "set_crossfader", "position": 1.0},
                {"at_beat": 7.0, "deck_id": "A", "op": "cut"},
            ],
        }
    )
    rendered = mixscore_render(score, base_dir=base_dir)
    receipt = rendered["receipt"]
    assert receipt["deck_count"] == 3
    assert receipt["selected_event_count"] == receipt["executed_event_count"] == 17
    assert receipt["stem_reconciliation_max_abs"] == 0.0

    frames_per_beat = int(round(receipt["sample_rate"] * 60.0 / 120.0))
    stem_a = rendered["stems"]["A"]
    stem_c = rendered["stems"]["C"]
    assert np.max(np.abs(stem_a[4 * frames_per_beat : 5 * frames_per_beat])) == 0.0
    assert _rms(stem_a[5 * frames_per_beat : 6 * frames_per_beat]) > 0.005
    assert np.max(np.abs(stem_c[2 * frames_per_beat : 3 * frames_per_beat])) == 0.0
    assert _rms(stem_c[3 * frames_per_beat : 4 * frames_per_beat]) > 0.001
    assert np.max(np.abs(stem_c[frames_per_beat:, 1])) < 1e-7

    exercised = {row["op"] for row in demo_ledger["events"]}
    exercised.update(row["op"] for row in rendered["execution_ledger"]["events"])
    assert exercised == set(mixscore_capability()["operations"])


def test_mixscore_schema_artifacts_match_runtime_kinds() -> None:
    root = Path(__file__).resolve().parent.parent
    expected = {
        "earcrate_mix_score_v1.schema.json": "earcrate_mix_score",
        "earcrate_mix_execution_ledger_v1.schema.json": "earcrate_mix_execution_ledger",
        "earcrate_mix_render_receipt_v1.schema.json": "earcrate_mix_render_receipt",
    }
    for filename, kind in expected.items():
        schema = json.loads((root / "schemas" / filename).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["schema_version"]["const"] == 1
        assert schema["properties"]["kind"]["const"] == kind


def test_single_file_package_executes_mix_demo(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    build_script = root / "build" / "make_singlefile.py"
    assert build_script.is_file()
    build = subprocess.run(
        [sys.executable, str(build_script)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    artifact = root / "dist" / "earcrate.py"
    output = tmp_path / "singlefile-mix-demo"
    run = subprocess.run(
        [
            sys.executable,
            str(artifact),
            "mix",
            "demo",
            str(output),
            "--sample-rate",
            "8000",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ok"] is True and payload["complete"] is True
    assert payload["receipt"]["selected_event_count"] == 15
    assert payload["receipt"]["executed_event_count"] == 15
    assert Path(payload["output_path"]).is_file()
    assert Path(payload["receipt_path"]).is_file()
    assert sorted(payload["stem_paths"]) == ["A", "B"]
