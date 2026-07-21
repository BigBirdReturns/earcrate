from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from earcrate.live.crate import live_compile_crate_atlas
from earcrate.live.engine import live_engine_new
from earcrate.midi.codec import midi_read
from test_live_crate_runtime import _atoms, _write_source


def test_module_live_audio_phrase_writes_exact_next_state_and_pcm(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    source = tmp_path / "source.mid"
    _write_source(source)
    compiled = live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        sample_rate=8_000,
        compile_sfz=False,
    )
    crate_path = Path(compiled["write"]["path"])
    state = live_engine_new(compiled["atlas"]["live_material_atlas"], persona="club", seed=29)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    controls = tmp_path / "controls.json"
    controls.write_text(
        json.dumps(
            [
                {"command": "set_persona", "value": "pretty_lights"},
                {"command": "force_technique", "value": "hard_cut"},
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "phrase"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "earcrate",
            "live-audio",
            "phrase",
            str(crate_path),
            str(state_path),
            str(output),
            "--controls",
            str(controls),
            "--beam-width",
            "10",
            "--candidate-limit",
            "6",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ok"] is True and payload["complete"] is True
    assert payload["bars"] == 4
    assert payload["materials_scanned_during_render"] == 0
    assert payload["samples_decoded_during_callback"] == 0
    assert Path(payload["paths"]["audio"]).is_file()
    next_state = json.loads(Path(payload["paths"]["state"]).read_text(encoding="utf-8"))
    assert next_state["current_bar_index"] == 4
    assert next_state["current_persona"] == "pretty_lights"
    receipt = json.loads(Path(payload["paths"]["receipt"]).read_text(encoding="utf-8"))
    assert receipt["selected_event_count"] == receipt["executed_event_count"]
    assert receipt["activity_delta"]["domains"]["control"]["planning"] == 1
    assert receipt["activity_delta"]["domains"]["phrase_render"]["binding"] == 1
    assert receipt["activity_delta"]["domains"]["phrase_render"]["sample_decode"] > 0
    assert all(operator == "hard_cut" for operator in receipt["operators"])


def test_single_file_reports_audio_callback_boundary(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "live-audio", "capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ok"] is True
    contract = payload["prepared_stream"]["callback_contract"]
    assert sorted(contract["forbidden"]) == ["binding", "library_search", "planning", "sample_decode"]
    assert payload["audio_device"]["queue_model"] == "single_producer_single_consumer_fixed_ring"
    assert payload["audio_device"]["completion_history"] == "fixed_ring"
