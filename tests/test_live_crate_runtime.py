from __future__ import annotations

import json
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from earcrate.live.crate import (
    live_compile_crate_atlas,
    live_run_crate_session,
    live_write_crate_session,
)
from earcrate.live.model import LiveError
from earcrate.midi.codec import midi_read

PPQ = 192
BAR = PPQ * 4


def _priority(message: mido.Message | mido.MetaMessage) -> int:
    if getattr(message, "is_meta", False) and message.type == "track_name":
        return 0
    if message.type == "program_change":
        return 1
    if message.type in {"control_change", "pitchwheel"}:
        return 2
    if message.type == "note_off" or (message.type == "note_on" and int(message.velocity) == 0):
        return 3
    if message.type == "note_on":
        return 4
    if getattr(message, "is_meta", False) and message.type == "end_of_track":
        return 9
    return 5


def _track(name: str, absolute: list[tuple[int, mido.Message | mido.MetaMessage]]) -> mido.MidiTrack:
    rows = [(0, mido.MetaMessage("track_name", name=name, time=0)), *absolute]
    rows.sort(key=lambda row: (row[0], _priority(row[1]), str(row[1])))
    track = mido.MidiTrack()
    previous = 0
    for tick, message in rows:
        track.append(message.copy(time=int(tick) - previous))
        previous = int(tick)
    return track


def _note(rows: list[tuple[int, mido.Message | mido.MetaMessage]], start: int, duration: int, note: int, velocity: int, channel: int) -> None:
    rows.append((start, mido.Message("note_on", channel=channel, note=note, velocity=velocity, time=0)))
    rows.append((start + duration, mido.Message("note_off", channel=channel, note=note, velocity=0, time=0)))


def _write_source(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    midi.tracks.append(
        _track(
            "Conductor",
            [
                (0, mido.MetaMessage("set_tempo", tempo=500_000, time=0)),
                (0, mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)),
                (16 * BAR, mido.MetaMessage("end_of_track", time=0)),
            ],
        )
    )
    bass: list[tuple[int, mido.Message | mido.MetaMessage]] = [(0, mido.Message("program_change", channel=0, program=33, time=0))]
    drums: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    lead: list[tuple[int, mido.Message | mido.MetaMessage]] = [(0, mido.Message("program_change", channel=1, program=80, time=0))]
    pad: list[tuple[int, mido.Message | mido.MetaMessage]] = [(0, mido.Message("program_change", channel=2, program=88, time=0))]
    fx: list[tuple[int, mido.Message | mido.MetaMessage]] = [(0, mido.Message("program_change", channel=3, program=96, time=0))]
    for bar in range(16):
        root = 36 + (bar % 4) * 2
        for step, interval in enumerate((0, 0, 3, 7)):
            _note(bass, bar * BAR + step * PPQ, 96, root + interval, 82, 0)
        if bar >= 4:
            for offset, note, velocity in ((0, 36, 112), (192, 42, 78), (384, 38, 106), (576, 42, 82)):
                _note(drums, bar * BAR + offset, 48, note, velocity, 9)
        if bar >= 8:
            for offset, note in ((0, 72), (192, 74), (384, 79), (576, 76)):
                _note(lead, bar * BAR + offset, 144, note + (bar % 2), 108, 1)
        if bar < 4 or bar >= 12:
            _note(pad, bar * BAR, BAR - 24, 60 + (bar % 2) * 5, 64, 2)
        if bar in {7, 11, 15}:
            _note(fx, bar * BAR + 3 * PPQ, PPQ, 84, 96, 3)
    for rows in (bass, drums, lead, pad, fx):
        rows.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Fingered Bass", bass))
    midi.tracks.append(_track("Drums", drums))
    midi.tracks.append(_track("Synth Lead", lead))
    midi.tracks.append(_track("Synth Pad", pad))
    midi.tracks.append(_track("Synth FX", fx))
    midi.save(path)


def _write_audio(path: Path, frequency: float, seconds: float, *, decay: bool = False, sample_rate: int = 8_000) -> None:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    envelope = np.exp(-time / 0.055) if decay else np.ones_like(time)
    audio = (0.32 * envelope * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _atoms(tmp_path: Path) -> list[dict]:
    specs = [
        ("bass", 82.4069, 1.2, "BASS_RIFF", "bass", 40, {"bass_score": 0.96, "low_share": 0.78, "loopability": 0.90}),
        ("drums", 74.0, 0.22, "DRUM_BREAK", "drum_anchor", None, {"floor_score": 0.96, "transient_density": 0.95, "low_share": 0.66, "mid_share": 0.62, "high_share": 0.54, "loopability": 0.10}),
        ("lead", 659.255, 1.0, "RIFF_ID", "harmony", 76, {"hook_score": 0.91, "spark_score": 0.78, "mid_share": 0.72, "loopability": 0.82}),
        ("pad", 293.665, 1.1, "BED_CHORD", "harmony", 62, {"bed_score": 0.96, "mid_share": 0.74, "loopability": 0.96}),
        ("fx", 1046.5, 0.8, "TEXTURE", "fx", 84, {"spark_score": 0.94, "high_share": 0.82, "transient_density": 0.62, "loopability": 0.72}),
    ]
    out = []
    for name, frequency, seconds, ear_role, render_role, root_midi, metrics in specs:
        path = tmp_path / f"{name}.wav"
        _write_audio(path, frequency, seconds, decay=name == "drums")
        row = {
            "atom_id": f"approved-{name}",
            "atom_status": "approved",
            "path": str(path),
            "start_s": 0.0,
            "end_s": seconds,
            "ear_role": ear_role,
            "render_role": render_role,
            "key_root": 0 if root_midi is None else root_midi % 12,
            "root_midi": root_midi,
            "root_pitch_confidence": 0.99 if root_midi is not None else 0.0,
            "score": 0.94,
            "source_audio_sha256": name * 8,
            **metrics,
        }
        out.append(row)
    return out


def test_precompiled_crate_runs_live_without_scanning_the_library(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    compiled = live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        taste_profile="test",
        sample_rate=8_000,
        compile_sfz=False,
    )
    atlas = compiled["atlas"]
    assert atlas["rack_revisions"]
    assert atlas["rack_build"]["complete"] is True
    result = live_run_crate_session(
        atlas,
        target_bars=16,
        persona="pretty_lights",
        seed=17,
        render_path=tmp_path / "live-crate.wav",
        stems_dir=tmp_path / "live-crate-stems",
    )
    session = result["session"]
    assert session["complete"] is True
    assert session["generated_event_count"] == session["bound_event_count"]
    assert session["library_materials_scanned_during_execution"] == 0
    assert result["render"]["complete_execution"] is True
    assert result["render"]["selected_event_count"] == session["generated_event_count"]
    writes = live_write_crate_session(result, tmp_path / "session-artifacts")
    assert Path(writes["midi"]["path"]).is_file()
    assert json.loads(Path(writes["session"]["path"]).read_text(encoding="utf-8"))["complete"] is True


def test_precompiled_crate_refuses_mutated_compiled_sample(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    compiled = live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        sample_rate=8_000,
        compile_sfz=False,
    )
    atlas = compiled["atlas"]
    sample = Path(atlas["rack_revisions"][0]["zones"][0]["sample"]["path"])
    _write_audio(sample, 111.0, 0.25)
    try:
        live_run_crate_session(atlas, target_bars=8, persona="club")
    except Exception as exc:
        assert "changed" in str(exc) or "identity" in str(exc)
    else:
        raise AssertionError("mutated precompiled rack sample was accepted")
