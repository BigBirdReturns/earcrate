from __future__ import annotations

import json
from pathlib import Path

import mido

from earcrate.midi.anatomy import midi_arrangement_anatomy, midi_write_arrangement_anatomy
from earcrate.midi.codec import midi_read

PPQ = 192
BAR = PPQ * 4


def _note(track: mido.MidiTrack, note: int, velocity: int, delay: int, duration: int, channel: int = 0) -> None:
    track.append(mido.Message("note_on", note=note, velocity=velocity, channel=channel, time=delay))
    track.append(mido.Message("note_off", note=note, velocity=0, channel=channel, time=duration))


def _bass_track() -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message("program_change", channel=0, program=33, time=0))
    cursor = 0
    for bar in range(16):
        bar_start = bar * BAR
        for offset, note in ((0, 36), (192, 36), (384, 39), (576, 43)):
            absolute = bar_start + offset
            _note(track, note, 78, absolute - cursor, 96, 0)
            cursor = absolute + 96
    track.append(mido.MetaMessage("end_of_track", time=16 * BAR - cursor))
    return track


def _drum_track() -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    cursor = 0
    for bar in range(4, 12):
        bar_start = bar * BAR
        for offset, note, velocity in ((0, 36, 106), (192, 42, 76), (384, 38, 102), (576, 42, 78)):
            absolute = bar_start + offset
            _note(track, note, velocity, absolute - cursor, 48, 9)
            cursor = absolute + 48
    track.append(mido.MetaMessage("end_of_track", time=16 * BAR - cursor))
    return track


def _lead_track() -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Synth Lead", time=0))
    track.append(mido.Message("program_change", channel=1, program=80, time=0))
    cursor = 0
    for bar in range(8, 12):
        bar_start = bar * BAR
        for offset, note in ((0, 72), (192, 74), (384, 79), (576, 76)):
            absolute = bar_start + offset
            _note(track, note, 112, absolute - cursor, 144, 1)
            cursor = absolute + 144
    track.append(mido.MetaMessage("end_of_track", time=16 * BAR - cursor))
    return track


def _pad_track() -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Synth Pad", time=0))
    track.append(mido.Message("program_change", channel=2, program=88, time=0))
    cursor = 0
    for bar in range(12, 16):
        absolute = bar * BAR
        _note(track, 60 + (bar % 2) * 5, 58, absolute - cursor, BAR, 2)
        cursor = absolute + BAR
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _write_arrangement(path: Path, order: tuple[str, ...] = ("bass", "drums", "lead", "pad")) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=16 * BAR))
    midi.tracks.append(conductor)
    makers = {"bass": _bass_track, "drums": _drum_track, "lead": _lead_track, "pad": _pad_track}
    for name in order:
        midi.tracks.append(makers[name]())
    midi.save(path)


def _compile(path: Path) -> dict:
    return midi_arrangement_anatomy(
        midi_read(path),
        minimum_section_bars=2,
        maximum_section_bars=8,
        section_penalty=0.08,
        boundary_reward=1.0,
    )


def test_anatomy_accounts_for_every_event_and_recovers_layer_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "arrangement.mid"
    _write_arrangement(source)
    anatomy = _compile(source)

    assert anatomy["bar_count"] == 16
    assert anatomy["selected_event_count"] == anatomy["mapped_event_count"]
    assert anatomy["selected_event_count"] == len(anatomy["event_assignments"])
    assert len({row["event_id"] for row in anatomy["event_assignments"]}) == anatomy["selected_event_count"]
    assert anatomy["bars"][4]["entering_slot_ids"]
    assert anatomy["bars"][8]["entering_slot_ids"]
    assert anatomy["bars"][12]["entering_slot_ids"]
    boundaries = {section["start_bar_index"] for section in anatomy["sections"][1:]}
    assert {4, 8, 12} <= boundaries
    assert anatomy["section_count"] == 4
    assert any(motif["role"] == "bass" and motif["occurrence_count"] == 16 for motif in anatomy["motifs"])
    assert anatomy["fingerprint"]["recurring_motif_count"] >= 3


def test_structural_hash_is_independent_of_track_storage_order(tmp_path: Path) -> None:
    left_path = tmp_path / "left.mid"
    right_path = tmp_path / "right.mid"
    _write_arrangement(left_path)
    _write_arrangement(right_path, ("pad", "lead", "drums", "bass"))
    left = _compile(left_path)
    right = _compile(right_path)

    assert left["semantic_sha256"] != right["semantic_sha256"]
    assert left["anatomy_sha256"] != right["anatomy_sha256"]
    assert left["structural_sha256"] == right["structural_sha256"]
    assert left["fingerprint"] == right["fingerprint"]


def test_anatomy_write_is_atomic_and_revision_bound(tmp_path: Path) -> None:
    source = tmp_path / "arrangement.mid"
    output = tmp_path / "arrangement.anatomy.json"
    _write_arrangement(source)
    receipt = midi_write_arrangement_anatomy(
        midi_read(source),
        output,
        minimum_section_bars=2,
        maximum_section_bars=8,
        section_penalty=0.08,
        boundary_reward=1.0,
    )
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["anatomy_sha256"] == stored["anatomy_sha256"]
    assert receipt["structural_sha256"] == stored["structural_sha256"]
    assert receipt["selected_event_count"] == stored["selected_event_count"]
    assert receipt["section_count"] == 4
