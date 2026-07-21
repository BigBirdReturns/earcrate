from __future__ import annotations

from pathlib import Path

from earcrate.live.engine import live_engine_new, live_engine_step
from earcrate.live.planner import live_atlas_from_midi
from earcrate.midi.codec import midi_read
from test_live_dj_runtime import _write_source


def test_incremental_engine_applies_controls_and_commits_one_phrase(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    atlas = live_atlas_from_midi(midi_read(source))
    state = live_engine_new(atlas, persona="club", seed=41)
    first = live_engine_step(
        atlas,
        state,
        controls=[
            {"command": "set_persona", "value": "pretty_lights"},
            {"command": "force_technique", "value": "hard_cut"},
        ],
        beam_width=12,
        candidate_limit=6,
    )
    assert first["state_before"]["current_bar_index"] == 0
    assert first["state_after"]["current_bar_index"] == 4
    assert first["state_after"]["current_persona"] == "pretty_lights"
    assert all(row["operator"] == "hard_cut" for row in first["plan"]["committed_decisions"])
    assert len(first["applied_controls"]) == 2

    second = live_engine_step(
        atlas,
        first["state_after"],
        controls=[{"command": "set_density", "value": 0.65}],
        beam_width=12,
        candidate_limit=6,
    )
    assert second["state_before"]["state_sha256"] == first["state_after"]["state_sha256"]
    assert second["state_after"]["current_bar_index"] == 8
    assert second["state_after"]["density"] == 0.65
    assert second["step_sha256"] != first["step_sha256"]


def test_incremental_engine_refuses_state_from_another_atlas(tmp_path: Path) -> None:
    left = tmp_path / "left.mid"
    right = tmp_path / "right.mid"
    _write_source(left)
    _write_source(right)
    # Change the second source without changing the fixture's structure.
    data = bytearray(right.read_bytes())
    data[-1] = (data[-1] + 1) % 256
    right.write_bytes(bytes(data))
    left_atlas = live_atlas_from_midi(midi_read(left))
    try:
        right_atlas = live_atlas_from_midi(midi_read(right))
    except Exception:
        # A byte-level mutation may make the SMF invalid; use a valid policy mismatch
        # by modifying a sealed atlas identity instead.
        right_atlas = dict(left_atlas)
        right_atlas["atlas_sha256"] = "0" * 64
    state = live_engine_new(left_atlas, persona="club")
    try:
        live_engine_step(right_atlas, state)
    except Exception as exc:
        assert "atlas" in str(exc).lower() or "sha" in str(exc).lower()
    else:
        raise AssertionError("live engine accepted a state from another atlas")
