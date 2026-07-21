from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from earcrate.midi.codec import midi_read, midi_roundtrip
from earcrate.midi.model import MIDI_LEDGER_KIND, MIDI_LEDGER_SCHEMA_VERSION, midi_seal_ledger, midi_statistics
from earcrate.midi.render import midi_compile_note_spans, midi_render_file, midi_render_ledger


def _write_fixture(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=192)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=600_000, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=400_000, time=384))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    lead = mido.MidiTrack()
    lead.append(mido.MetaMessage("track_name", name="Lead", time=0))
    lead.append(mido.Message("program_change", channel=0, program=4, time=0))
    lead.append(mido.Message("control_change", channel=0, control=7, value=100, time=0))
    lead.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
    lead.append(mido.Message("pitchwheel", channel=0, pitch=2048, time=96))
    lead.append(mido.Message("control_change", channel=0, control=64, value=127, time=96))
    lead.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=96))
    lead.append(mido.Message("control_change", channel=0, control=64, value=0, time=96))
    lead.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(lead)

    drums = mido.MidiTrack()
    drums.append(mido.MetaMessage("text", text="Drums", time=0))
    drums.append(mido.Message("note_on", channel=9, note=36, velocity=110, time=192))
    drums.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=96))
    drums.append(mido.MetaMessage("end_of_track", time=96))
    midi.tracks.append(drums)
    midi.save(path)


def test_midi_roundtrip_preserves_semantic_event_ledger(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mid"
    output = tmp_path / "roundtrip.mid"
    _write_fixture(source)
    before = midi_read(source)
    receipt = midi_roundtrip(source, output)
    after = midi_read(output)
    stats = midi_statistics(before)
    assert receipt["ok"] is True
    assert before["semantic_sha256"] == after["semantic_sha256"]
    assert receipt["output"]["byte_sha256"] == receipt["input"]["byte_sha256"]
    assert stats["declared_track_count"] == 3
    assert stats["occupied_note_track_count"] == 2
    assert stats["note_on_count"] == 2
    assert stats["note_off_count"] == 2
    assert stats["control_change_count"] == 3
    assert stats["pitchwheel_count"] == 1
    assert stats["tempo_event_count"] == 2
    assert stats["duration_seconds"] == 1.2
    assert stats["tracks"][2]["name"] == "Drums"


def test_neutral_render_stems_sum_to_master(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mid"
    output = tmp_path / "mix.wav"
    stems = tmp_path / "stems"
    _write_fixture(source)
    receipt = midi_render_file(source, output, stems_dir=stems, sample_rate=8_000)
    master, master_rate = sf.read(output, always_2d=True)
    stem_sum = np.zeros_like(master)
    for stem_path in sorted(stems.glob("*.wav")):
        stem, stem_rate = sf.read(stem_path, always_2d=True)
        assert stem_rate == master_rate
        assert stem.shape == master.shape
        stem_sum += stem
    assert receipt["ok"] is True
    assert receipt["note_span_count"] == 2
    assert receipt["declared_track_count"] == 3
    assert receipt["rendered_track_count"] == 2
    assert receipt["compile_diagnostics"]["sustain_release_count"] == 1
    assert len(receipt["stems"]) == 2
    assert float(np.max(np.abs(master - stem_sum))) < 1e-6


def test_sparse_ten_thousand_track_deck_only_materializes_occupied_tracks(tmp_path: Path) -> None:
    tracks = [{"track_index": index, "name": f"Track {index + 1}", "events": []} for index in range(10_000)]
    tracks[-1]["name"] = "Only occupied track"
    tracks[-1]["events"] = [
        {"tick": 0, "order": 0, "is_meta": False, "message": {"type": "note_on", "channel": 0, "note": 60, "velocity": 100}},
        {"tick": 96, "order": 1, "is_meta": False, "message": {"type": "note_off", "channel": 0, "note": 60, "velocity": 0}},
    ]
    ledger = midi_seal_ledger({
        "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
        "kind": MIDI_LEDGER_KIND,
        "midi_type": 1,
        "ticks_per_beat": 192,
        "tracks": tracks,
    })
    compiled = midi_compile_note_spans(ledger)
    receipt = midi_render_ledger(ledger, tmp_path / "sparse.wav", sample_rate=8_000)
    assert compiled["diagnostics"]["declared_track_count"] == 10_000
    assert compiled["diagnostics"]["occupied_track_count"] == 1
    assert compiled["diagnostics"]["note_span_count"] == 1
    assert receipt["declared_track_count"] == 10_000
    assert receipt["rendered_track_count"] == 1
    assert receipt["first_pass"]["rendered"] == 1


def test_type_two_render_refuses_async_sequences(tmp_path: Path) -> None:
    ledger = midi_seal_ledger({
        "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
        "kind": MIDI_LEDGER_KIND,
        "midi_type": 2,
        "ticks_per_beat": 192,
        "tracks": [
            {
                "track_index": 0,
                "name": "Sequence A",
                "events": [
                    {"tick": 0, "order": 0, "is_meta": False, "message": {"type": "note_on", "channel": 0, "note": 60, "velocity": 100}},
                    {"tick": 96, "order": 1, "is_meta": False, "message": {"type": "note_off", "channel": 0, "note": 60, "velocity": 0}},
                ],
            },
            {
                "track_index": 1,
                "name": "Sequence B",
                "events": [
                    {"tick": 0, "order": 0, "is_meta": False, "message": {"type": "note_on", "channel": 1, "note": 67, "velocity": 100}},
                    {"tick": 192, "order": 1, "is_meta": False, "message": {"type": "note_off", "channel": 1, "note": 67, "velocity": 0}},
                ],
            },
        ],
    })
    with pytest.raises(ValueError, match="asynchronous sequences"):
        midi_render_ledger(ledger, tmp_path / "type2.wav", sample_rate=8_000)
