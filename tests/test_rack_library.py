from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from earcrate.midi.codec import midi_read
from earcrate.rack.demand import rack_compile_demands
from earcrate.rack.library import (
    rack_materialize_library_proposal,
    rack_propose_from_atoms,
)
from earcrate.rack.render_fix import rack_render_ledger


def _write_source(path: Path, frequency: float, seconds: float, sample_rate: int = 8_000, *, decay: bool = False) -> None:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    envelope = np.exp(-time / 0.035) if decay else np.ones_like(time)
    audio = (0.35 * envelope * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _write_performance(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=192)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    bass.append(mido.Message("program_change", channel=0, program=33, time=0))
    bass.append(mido.Message("note_on", channel=0, note=48, velocity=105, time=0))
    bass.append(mido.Message("note_off", channel=0, note=48, velocity=0, time=384))
    bass.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(bass)

    drums = mido.MidiTrack()
    drums.append(mido.MetaMessage("track_name", name="Drums", time=0))
    drums.append(mido.Message("note_on", channel=9, note=36, velocity=118, time=96))
    drums.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=24))
    drums.append(mido.MetaMessage("end_of_track", time=264))
    midi.tracks.append(drums)
    midi.save(path)


def _atoms(tmp_path: Path) -> list[dict]:
    bass = tmp_path / "library-bass.wav"
    kick = tmp_path / "library-kick.wav"
    wrong = tmp_path / "rejected.wav"
    _write_source(bass, 65.406, 1.25)
    _write_source(kick, 72.0, 0.16, decay=True)
    _write_source(wrong, 220.0, 0.50)
    return [
        {
            "atom_id": "atom-bass-approved",
            "atom_status": "approved",
            "path": str(bass),
            "start_s": 0.0,
            "end_s": 1.25,
            "ear_role": "BASS_RIFF",
            "render_role": "bass",
            "key_root": 0,
            "score": 0.94,
            "bass_score": 0.96,
            "low_share": 0.72,
            "loopability": 0.88,
            "artist": "Owned Artist",
            "title": "Owned Bass",
            "source_audio_sha256": "a" * 64,
        },
        {
            "atom_id": "atom-kick-approved",
            "atom_status": "approved",
            "path": str(kick),
            "start_s": 0.0,
            "end_s": 0.16,
            "ear_role": "DRUM_BREAK",
            "render_role": "drum_anchor",
            "score": 0.91,
            "floor_score": 0.95,
            "low_share": 0.78,
            "transient_density": 0.93,
            "loopability": 0.05,
            "artist": "Owned Artist",
            "title": "Owned Kick",
            "source_audio_sha256": "b" * 64,
        },
        {
            "atom_id": "atom-rejected-decoy",
            "atom_status": "rejected",
            "path": str(wrong),
            "start_s": 0.0,
            "end_s": 0.50,
            "ear_role": "BASS_RIFF",
            "render_role": "bass",
            "key_root": 0,
            "score": 1.0,
            "bass_score": 1.0,
            "low_share": 1.0,
            "loopability": 1.0,
        },
    ]


def test_library_proposal_is_deterministic_and_excludes_rejected_atoms(tmp_path: Path) -> None:
    midi_path = tmp_path / "performance.mid"
    _write_performance(midi_path)
    demand = rack_compile_demands(midi_read(midi_path))
    atoms = _atoms(tmp_path)
    left = rack_propose_from_atoms(demand, atoms, taste_profile="test", top_k=4)
    right = rack_propose_from_atoms(demand, list(reversed(atoms)), taste_profile="test", top_k=4)
    assert left["proposal_sha256"] == right["proposal_sha256"]
    assert left["complete"] is True
    assert left["atom_pool_count"] == 2
    selected = [choice["atom_id"] for slot in left["slots"] for choice in slot["selected"]]
    assert set(selected) == {"atom-bass-approved", "atom-kick-approved"}
    assert "atom-rejected-decoy" not in selected
    assert all(slot["candidate_groups"] for slot in left["slots"])
    assert all(group["candidates"][0]["score_terms"] for slot in left["slots"] for group in slot["candidate_groups"])


def test_library_proposal_materializes_seals_binds_and_renders(tmp_path: Path) -> None:
    midi_path = tmp_path / "performance.mid"
    _write_performance(midi_path)
    ledger = midi_read(midi_path)
    demand = rack_compile_demands(ledger)
    proposal = rack_propose_from_atoms(demand, _atoms(tmp_path), taste_profile="test", top_k=4)
    build = rack_materialize_library_proposal(
        ledger,
        proposal,
        tmp_path / "rack-build",
        sample_rate=8_000,
    )
    assert build["ok"] is True
    assert build["complete"] is True
    assert len(build["materializations"]) == 2
    assert len(build["rack_revisions"]) == 2
    assert build["binding"]["complete"] is True
    assert build["binding"]["selected_event_count"] == build["binding"]["bound_event_count"] == 2
    assert all(Path(row["path"]).is_file() for row in build["materializations"])
    assert all(Path(row["rack_path"]).is_file() for row in build["racks"])
    assert all(row["sfz"] and Path(row["sfz"]["path"]).is_file() for row in build["racks"])

    render = rack_render_ledger(
        ledger,
        build["binding"],
        build["rack_revisions"],
        tmp_path / "substituted.wav",
        stems_dir=tmp_path / "substituted-stems",
        sample_rate=8_000,
    )
    assert render["complete_execution"] is True
    assert render["selected_event_count"] == render["executed_event_count"] == 2
    assert render["truncated_event_count"] == render["refused_event_count"] == 0
    master, rate = sf.read(render["output_path"], always_2d=True)
    stem_sum = np.zeros_like(master)
    for row in render["stems"]:
        stem, stem_rate = sf.read(row["path"], always_2d=True)
        assert stem_rate == rate
        stem_sum += stem
    assert float(np.max(np.abs(master - stem_sum))) < 1e-6
