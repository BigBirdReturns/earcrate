from __future__ import annotations

from pathlib import Path

from earcrate.live.runtime import live_build_session
from earcrate.midi.codec import midi_read
from test_live_dj_runtime import _write_source


def test_long_set_replans_personas_without_runtime_library_scans(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    build = live_build_session(
        midi_read(source),
        target_bars=128,
        persona="club",
        seed=2026,
        controls=[
            {"at_bar": 32, "command": "set_persona", "value": "girl_talk"},
            {"at_bar": 64, "command": "set_persona", "value": "pretty_lights"},
            {"at_bar": 96, "command": "set_persona", "value": "minimal"},
            {"at_bar": 112, "command": "set_energy", "value": 0.72},
        ],
        beam_width=8,
        candidate_limit=6,
    )
    decisions = build["session"]["decisions"]
    assert len(decisions) == 128
    assert {row["persona"] for row in decisions} == {"club", "girl_talk", "pretty_lights", "minimal"}
    assert all(row["alternatives"] for row in decisions)
    execution = build["cpu_execution"]
    assert execution["complete"] is True
    assert execution["patterns_scanned_during_execution"] == 0
    assert execution["materials_scanned_during_execution"] == 0
    assert execution["runtime_operation_count"] == execution["selected_command_count"]
    assert execution["activity_delta"]["domains"]["cpu_execution"]["cpu_command"] == execution["selected_command_count"]
    assert execution["runtime_operation_count"] < 128 * 24
    assert build["activity_delta"]["domains"]["control"]["planning"] > 0
    assert build["midi_lowering"]["output_statistics"]["note_on_count"] == len(build["midi_lowering"]["event_provenance"])
