from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from earcrate.midi.codec import midi_read
from earcrate.rack.demand import rack_compile_demands
from earcrate.rack.multizone import (
    rack_materialize_library_proposal,
    rack_propose_from_atoms,
)
from earcrate.rack.render_fix import rack_render_ledger


def _write_sine(path: Path, frequency: float, seconds: float = 1.0, sample_rate: int = 8_000) -> None:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    audio = (0.30 * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _write_wide_lane(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=192)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    lead = mido.MidiTrack()
    lead.append(mido.MetaMessage("track_name", name="Wide Synth Lead", time=0))
    lead.append(mido.Message("program_change", channel=0, program=80, time=0))
    lead.append(mido.Message("note_on", channel=0, note=24, velocity=104, time=0))
    lead.append(mido.Message("note_off", channel=0, note=24, velocity=0, time=192))
    lead.append(mido.Message("note_on", channel=0, note=79, velocity=108, time=192))
    lead.append(mido.Message("note_off", channel=0, note=79, velocity=0, time=192))
    lead.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(lead)
    midi.save(path)


def _atoms(tmp_path: Path) -> list[dict]:
    low = tmp_path / "low-root.wav"
    high = tmp_path / "high-root.wav"
    rejected = tmp_path / "rejected.wav"
    _write_sine(low, 32.703)
    _write_sine(high, 783.991)
    _write_sine(rejected, 220.0)
    common = {
        "atom_status": "approved",
        "start_s": 0.0,
        "end_s": 1.0,
        "ear_role": "RIFF_ID",
        "render_role": "full",
        "score": 0.92,
        "hook_score": 0.86,
        "spark_score": 0.70,
        "mid_share": 0.58,
        "loopability": 0.82,
    }
    return [
        {
            **common,
            "atom_id": "wide-low-root",
            "path": str(low),
            "key_root": 0,
            "root_midi": 24,
            "root_pitch_confidence": 0.99,
            "source_audio_sha256": "a" * 64,
        },
        {
            **common,
            "atom_id": "wide-high-root",
            "path": str(high),
            "key_root": 7,
            "root_midi": 79,
            "root_pitch_confidence": 0.99,
            "source_audio_sha256": "b" * 64,
        },
        {
            **common,
            "atom_id": "rejected-perfect-decoy",
            "atom_status": "rejected",
            "path": str(rejected),
            "key_root": 4,
            "root_midi": 52,
            "score": 1.0,
            "source_audio_sha256": "c" * 64,
        },
    ]


def test_wide_lane_resolves_with_multiple_zones_under_eighteen_semitones(tmp_path: Path) -> None:
    midi_path = tmp_path / "wide.mid"
    _write_wide_lane(midi_path)
    demand = rack_compile_demands(midi_read(midi_path))
    atoms = _atoms(tmp_path)

    left = rack_propose_from_atoms(
        demand,
        atoms,
        taste_profile="test",
        maximum_transpose_semitones=18.0,
        max_zones_per_slot=4,
        top_k=4,
    )
    right = rack_propose_from_atoms(
        demand,
        list(reversed(atoms)),
        taste_profile="test",
        maximum_transpose_semitones=18.0,
        max_zones_per_slot=4,
        top_k=4,
    )

    assert left["proposal_sha256"] == right["proposal_sha256"]
    assert left["complete"] is True
    assert left["multi_zone_slot_count"] == 1
    assert left["zone_count"] == 2
    slot = left["slots"][0]
    assert slot["strategy"] == "multi_zone"
    assert slot["demanded_notes"] == slot["covered_notes"] == [24, 79]
    assert [choice["key_range"] for choice in slot["selected"]] == [[24, 24], [79, 79]]
    assert {choice["atom_id"] for choice in slot["selected"]} == {"wide-low-root", "wide-high-root"}
    assert all(choice["maximum_transpose_semitones"] <= 18.0 for choice in slot["selected"])
    assert "rejected-perfect-decoy" not in {choice["atom_id"] for choice in slot["selected"]}


def test_wide_lane_refuses_when_zone_budget_is_one(tmp_path: Path) -> None:
    midi_path = tmp_path / "wide.mid"
    _write_wide_lane(midi_path)
    demand = rack_compile_demands(midi_read(midi_path))
    proposal = rack_propose_from_atoms(
        demand,
        _atoms(tmp_path),
        maximum_transpose_semitones=18.0,
        max_zones_per_slot=1,
    )
    assert proposal["complete"] is False
    assert proposal["zone_count"] == 0
    assert proposal["unresolved"][0]["reason"] == "maximum_zone_count_exceeded"


def test_multi_zone_materialization_binding_and_render_preserve_every_event(tmp_path: Path) -> None:
    midi_path = tmp_path / "wide.mid"
    _write_wide_lane(midi_path)
    ledger = midi_read(midi_path)
    demand = rack_compile_demands(ledger)
    proposal = rack_propose_from_atoms(
        demand,
        _atoms(tmp_path),
        maximum_transpose_semitones=18.0,
        max_zones_per_slot=4,
        top_k=4,
    )
    build = rack_materialize_library_proposal(
        ledger,
        proposal,
        tmp_path / "wide-build",
        sample_rate=8_000,
    )

    assert build["ok"] is True
    assert build["complete"] is True
    assert build["zone_count"] == 2
    assert build["multi_zone_slot_count"] == 1
    assert len(build["rack_revisions"]) == 1
    assert len(build["rack_revisions"][0]["zones"]) == 2
    assert build["binding"]["selected_event_count"] == build["binding"]["bound_event_count"] == 2
    assert max(abs(float(row["transpose_semitones"])) for row in build["binding"]["event_bindings"]) <= 18.0

    render = rack_render_ledger(
        ledger,
        build["binding"],
        build["rack_revisions"],
        tmp_path / "wide-substituted.wav",
        stems_dir=tmp_path / "wide-stems",
        sample_rate=8_000,
    )
    assert render["complete_execution"] is True
    assert render["selected_event_count"] == render["executed_event_count"] == 2
    assert render["truncated_event_count"] == render["refused_event_count"] == 0


def test_measured_root_is_not_re_octaved_to_fake_coverage(tmp_path: Path) -> None:
    midi_path = tmp_path / "wide.mid"
    _write_wide_lane(midi_path)
    demand = rack_compile_demands(midi_read(midi_path))
    low_only = [_atoms(tmp_path)[0]]
    proposal = rack_propose_from_atoms(
        demand,
        low_only,
        maximum_transpose_semitones=18.0,
        max_zones_per_slot=4,
    )
    assert proposal["complete"] is False
    assert any(row["reason"] == "no_compatible_approved_atom" for row in proposal["unresolved"])
