from __future__ import annotations

from pathlib import Path

import mido

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
    changed = mido.MidiFile(right)
    changed_note = None
    for track in changed.tracks:
        for message in track:
            if message.type == "note_on" and int(message.velocity) > 0:
                changed_note = int(message.note)
                message.note = min(127, changed_note + 1)
                break
        if changed_note is not None:
            break
    assert changed_note is not None
    # Change the matching first note-off so the modified performance remains valid.
    for track in changed.tracks:
        for message in track:
            if message.type in {"note_off", "note_on"} and int(message.note) == changed_note and (
                message.type == "note_off" or int(message.velocity) == 0
            ):
                message.note = min(127, changed_note + 1)
                changed.save(right)
                left_atlas = live_atlas_from_midi(midi_read(left))
                right_atlas = live_atlas_from_midi(midi_read(right))
                assert left_atlas["atlas_sha256"] != right_atlas["atlas_sha256"]
                state = live_engine_new(left_atlas, persona="club")
                try:
                    live_engine_step(right_atlas, state)
                except Exception as exc:
                    assert "atlas" in str(exc).lower() or "sha" in str(exc).lower()
                else:
                    raise AssertionError("live engine accepted a state from another atlas")
                return
    raise AssertionError("could not find matching note-off in MIDI fixture")
