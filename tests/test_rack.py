from __future__ import annotations

import json
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from earcrate.midi.codec import midi_read
from earcrate.rack.binding import rack_compile_binding
from earcrate.rack.demand import rack_compile_demands
from earcrate.rack.model import RackError, rack_seal_draft, rack_verify_sources
from earcrate.rack.render import rack_render_ledger
from earcrate.rack.sfz import rack_compile_sfz


def _write_sine(path: Path, frequency: float, seconds: float, sample_rate: int = 8_000) -> None:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    audio = (0.25 * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _write_hit(path: Path, sample_rate: int = 8_000) -> None:
    frames = int(0.12 * sample_rate)
    envelope = np.exp(-np.arange(frames, dtype=np.float64) / (0.022 * sample_rate))
    audio = (0.6 * envelope * np.sin(2.0 * np.pi * 72.0 * np.arange(frames) / sample_rate)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _write_midi(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=192)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Fingered Bass", time=0))
    bass.append(mido.Message("program_change", channel=0, program=33, time=0))
    bass.append(mido.Message("note_on", channel=0, note=48, velocity=105, time=0))
    bass.append(mido.Message("pitchwheel", channel=0, pitch=1024, time=96))
    bass.append(mido.Message("note_off", channel=0, note=48, velocity=0, time=288))
    bass.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(bass)

    drums = mido.MidiTrack()
    drums.append(mido.MetaMessage("track_name", name="Drums", time=0))
    drums.append(mido.Message("note_on", channel=9, note=36, velocity=118, time=96))
    drums.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=24))
    drums.append(mido.MetaMessage("end_of_track", time=264))
    midi.tracks.append(drums)
    midi.save(path)


def _racks(tmp_path: Path) -> tuple[dict, dict]:
    bass_path = tmp_path / "bass.wav"
    kick_path = tmp_path / "kick.wav"
    _write_sine(bass_path, 65.406, 0.30)
    _write_hit(kick_path)
    bass = rack_seal_draft(
        {
            "rack_id": "crate-bass",
            "name": "Crate Bass",
            "mode": "pitched",
            "metadata": {"tags": ["bass", "fingered"]},
            "created_by": {"actor": "test", "reason": "substitute bass"},
            "zones": [
                {
                    "zone_id": "bass-zone",
                    "sample_path": str(bass_path),
                    "key_range": [36, 60],
                    "velocity_range": [1, 127],
                    "root_key": 48,
                    "trigger_mode": "gate",
                    "loop": {"enabled": True, "start_frame": 320, "end_frame": 2200, "crossfade_frames": 32},
                    "attack_ms": 3.0,
                    "release_ms": 18.0,
                }
            ],
        }
    )
    drums = rack_seal_draft(
        {
            "rack_id": "crate-drums",
            "name": "Crate Drums",
            "mode": "trigger",
            "metadata": {"tags": ["drums", "kick"]},
            "created_by": {"actor": "test", "reason": "substitute kick"},
            "zones": [
                {
                    "zone_id": "kick-36",
                    "sample_path": str(kick_path),
                    "key_range": [36, 36],
                    "velocity_range": [1, 127],
                    "root_key": 36,
                    "trigger_mode": "one_shot",
                    "attack_ms": 0.0,
                    "release_ms": 5.0,
                }
            ],
        }
    )
    return bass, drums


def test_demand_binding_and_rack_render_are_complete(tmp_path: Path) -> None:
    midi_path = tmp_path / "arrangement.mid"
    _write_midi(midi_path)
    ledger = midi_read(midi_path)
    bass, drums = _racks(tmp_path)

    demand = rack_compile_demands(ledger)
    assert demand["selected_event_count"] == 2
    assert demand["slot_count"] == 2
    assert {slot["role_hint"] for slot in demand["slots"]} == {"bass", "drums"}
    assert all(slot["search_query"]["mode"] in {"pitched", "trigger"} for slot in demand["slots"])

    binding = rack_compile_binding(ledger, [drums, bass])
    assert binding["complete"] is True
    assert binding["selected_event_count"] == binding["bound_event_count"] == 2
    assert len(binding["event_bindings"]) == 2
    assert {row["rack_id"] for row in binding["event_bindings"]} == {"crate-bass", "crate-drums"}

    output = tmp_path / "rack.wav"
    stems = tmp_path / "stems"
    receipt = rack_render_ledger(ledger, binding, [bass, drums], output, stems_dir=stems, sample_rate=8_000)
    assert receipt["ok"] is True
    assert receipt["complete_execution"] is True
    assert receipt["selected_event_count"] == receipt["executed_event_count"] == 2
    assert receipt["truncated_event_count"] == receipt["refused_event_count"] == 0

    execution = json.loads(Path(receipt["execution_path"]).read_text(encoding="utf-8"))
    assert execution["complete_execution"] is True
    assert len(execution["events"]) == 2
    assert all(event["status"] == "executed" for event in execution["events"])
    assert all(event["rack_sha256"] and event["zone_id"] and event["source_slice_pcm_sha256"] for event in execution["events"])

    master, rate = sf.read(output, always_2d=True)
    stem_sum = np.zeros_like(master)
    for path in sorted(stems.glob("*.wav")):
        stem, stem_rate = sf.read(path, always_2d=True)
        assert stem_rate == rate
        assert stem.shape == master.shape
        stem_sum += stem
    assert len(receipt["stems"]) == 2
    assert float(np.max(np.abs(master - stem_sum))) < 1e-6


def test_binding_preserves_missing_substitute_as_refusal(tmp_path: Path) -> None:
    midi_path = tmp_path / "arrangement.mid"
    _write_midi(midi_path)
    ledger = midi_read(midi_path)
    bass, _drums = _racks(tmp_path)
    binding = rack_compile_binding(ledger, [bass])
    assert binding["complete"] is False
    assert binding["bound_event_count"] == 1
    assert len(binding["unresolved"]) == 1
    assert binding["unresolved"][0]["reason"] == "no_compatible_rack"
    try:
        rack_render_ledger(ledger, binding, [bass], tmp_path / "must-refuse.wav", sample_rate=8_000)
    except RackError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete binding rendered instead of refusing")


def test_rack_source_mutation_is_detected_before_render(tmp_path: Path) -> None:
    bass, _drums = _racks(tmp_path)
    assert rack_verify_sources(bass)["ok"] is True
    source = Path(bass["zones"][0]["sample"]["path"])
    _write_sine(source, 98.0, 0.30)
    receipt = rack_verify_sources(bass, raise_on_error=False)
    assert receipt["ok"] is False
    try:
        rack_verify_sources(bass)
    except RackError as exc:
        assert "identity changed" in str(exc)
    else:
        raise AssertionError("mutated rack source was accepted")


def test_sfz_compilation_is_revision_bound(tmp_path: Path) -> None:
    bass, _drums = _racks(tmp_path)
    output = tmp_path / "crate-bass.sfz"
    receipt = rack_compile_sfz(bass, output)
    text = output.read_text(encoding="utf-8")
    assert receipt["rack_sha256"] == bass["rack_sha256"]
    assert f"rack_sha256={bass['rack_sha256']}" in text
    assert "zone_id=bass-zone" in text
    assert "pitch_keycenter=48" in text
    assert "loop_mode=loop_continuous" in text
    assert "loop_start=" in text and "loop_end=" in text and "loop_crossfade=" in text
