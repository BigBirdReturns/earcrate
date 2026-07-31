from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import mido
import numpy as np
import soundfile as sf

from earcrate.midi.codec import midi_read
from earcrate.music.director import music_render_director_score
from earcrate.music.model import music_sha256_json
from earcrate.music.source_phrase import MusicSourcePhraseError, music_build_source_phrase, sp_sha256_file
from earcrate.project import Gate8ProjectStore
from earcrate.project.continuation import project_extend_causal_score
from earcrate.project.custody import (
    project_adopt_causal_semantics,
    project_import_causal_score,
    project_verify_custody,
    project_verify_semantic_adoption,
)

PPQ = 480


def _write_midi(path: Path, notes: list[tuple[int, int, int]], total_ticks: int) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=total_ticks))
    midi.tracks.append(conductor)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Foreground", time=0))
    track.append(mido.Message("program_change", channel=0, program=80, time=0))
    absolute = []
    for start, duration, pitch in notes:
        absolute.append((start, 1, mido.Message("note_on", channel=0, note=pitch, velocity=90, time=0)))
        absolute.append((start + duration, 0, mido.Message("note_off", channel=0, note=pitch, velocity=0, time=0)))
    absolute.sort(key=lambda row: (row[0], row[1], str(row[2])))
    previous = 0
    for tick, _priority, message in absolute:
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, total_ticks - previous)))
    midi.tracks.append(track)
    midi.save(path)


def _score(notes: list[tuple[int, int, int]], total_ticks: int) -> dict:
    duration = total_ticks / PPQ * 0.5
    events = []
    for index, (start, length, pitch) in enumerate(notes):
        events.append(
            {
                "event_id": f"note_{index:03d}",
                "kind": "note",
                "track": "Foreground",
                "channel": 0,
                "start_tick": start,
                "duration_tick": length,
                "pitch": pitch,
                "velocity": 90,
                "role": "vocal",
                "rail": "foreground",
                "section_id": "section_a" if start < 960 else "section_b",
                "source_event_ids": [f"observation_{index:03d}"],
                "metadata": {"motif_id": "motif_a", "literal_identity": True},
            }
        )
    sections = [
        {
            "section_id": "section_a",
            "function": "statement",
            "start_tick": 0,
            "end_tick": min(960, total_ticks),
            "start_seconds": 0.0,
            "end_seconds": min(960, total_ticks) / PPQ * 0.5,
            "foreground_owner": "source_a",
            "floor_owner": "rack_floor",
            "low_end_owner": "rack_floor",
            "rails": ["floor", "foreground"],
            "operator_stack": ["foreground_swap"],
            "mix_actions": [],
        }
    ]
    if total_ticks > 960:
        sections.append(
            {
                "section_id": "section_b",
                "function": "continuation",
                "start_tick": 960,
                "end_tick": total_ticks,
                "start_seconds": 1.0,
                "end_seconds": duration,
                "foreground_owner": "source_a",
                "floor_owner": "rack_floor",
                "low_end_owner": "rack_floor",
                "rails": ["floor", "foreground"],
                "operator_stack": ["blend"],
                "mix_actions": [],
            }
        )
    score = {
        "schema_version": 1,
        "stage_id": "gate8_test",
        "title": "Gate 8 integration fixture",
        "duration_seconds": duration,
        "tempo_bpm": 120.0,
        "ticks_per_beat": PPQ,
        "total_ticks": total_ticks,
        "sections": sections,
        "events": events,
        "source_dialogue": [
            {
                "dialogue_id": "dialogue_a",
                "from_section_id": "section_a",
                "to_section_id": sections[-1]["section_id"],
                "relationship": "continuation",
            }
        ],
        "phrase_obligations": [
            {"obligation_id": "obligation_a", "kind": "answer", "status": "resolved"}
        ],
        "metrics": {"sounding_track_count": 1},
    }
    score["score_sha256"] = music_sha256_json(score)
    return score


def _fixture(root: Path, notes: list[tuple[int, int, int]], total_ticks: int) -> tuple[Path, Path, Path, Path]:
    midi_path = root / "score.mid"
    score_path = root / "score.json"
    neutral_path = root / "neutral.wav"
    verdict_path = root / "verdict.json"
    _write_midi(midi_path, notes, total_ticks)
    score = _score(notes, total_ticks)
    score_path.write_text(json.dumps(score, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    music_render_director_score(score, neutral_path, sample_rate=8_000, overwrite=True)
    verdict_path.write_text(json.dumps({"verdict": "conditional", "publication_ok": False}) + "\n", encoding="utf-8")
    return midi_path, score_path, neutral_path, verdict_path


def test_project_artifact_import_fsyncs_a_writable_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"gate8-windows-fsync")
    store = Gate8ProjectStore(tmp_path / "store")
    seen = []
    original = os.fsync

    def checked(fd: int) -> None:
        if os.name != "nt":
            import fcntl

            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            assert flags & os.O_ACCMODE != os.O_RDONLY
        seen.append(fd)
        original(fd)

    os.fsync = checked
    try:
        artifact = store.import_artifact("project", source, label="fixture")
    finally:
        os.fsync = original
    assert seen
    assert store.resolve_artifact("project", artifact).read_bytes() == source.read_bytes()


def test_gate8_custody_adoption_and_continuation_preserve_the_note_ledger(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    midi_path, score_path, neutral_path, verdict_path = _fixture(first, [(0, 480, 66)], 960)
    store = Gate8ProjectStore(tmp_path / "projects")
    imported = project_import_causal_score(
        store,
        name="Gate 8 fixture",
        family_id="gate8_fixture_v1",
        midi_path=midi_path,
        score_path=score_path,
        evidence_path=None,
        plan_path=None,
        historical_neutral_render=neutral_path,
        producer_verdict_path=verdict_path,
        profile="remix_prettylights_v1",
        project_id="gate8-fixture",
    )
    assert imported["ok"] is True
    custody = project_verify_custody(store, "gate8-fixture")
    assert custody["custody_ok"] is True
    custody_head = imported["revision"]["revision_sha"]
    adoption = project_adopt_causal_semantics(store, "gate8-fixture", expected_head=custody_head)
    assert adoption["ok"] is True
    assert project_verify_semantic_adoption(store, "gate8-fixture")["adoption_ok"] is True
    adopted_ledger = adoption["revision"]["performance"]["note_ledger_sha256"]
    assert adopted_ledger == imported["revision"]["performance"]["note_ledger_sha256"]

    second = tmp_path / "second"
    second.mkdir()
    midi2, score2, _neutral2, _verdict2 = _fixture(second, [(0, 480, 66), (960, 480, 68)], 1920)
    extended = project_extend_causal_score(
        store,
        "gate8-fixture",
        score_path=score2,
        midi_path=midi2,
        expected_head=adoption["revision"]["revision_sha"],
    )
    assert extended["ok"] is True
    assert extended["verification"]["prefix"]["prefix_unchanged"] is True
    assert extended["verification"]["prefix"]["added_note_count"] == 1
    assert extended["revision"]["performance"]["note_ledger_sha256"] != adopted_ledger


def test_gate8_continuation_refuses_backfill(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    midi_path, score_path, neutral_path, verdict_path = _fixture(first, [(0, 480, 66)], 960)
    store = Gate8ProjectStore(tmp_path / "projects")
    imported = project_import_causal_score(
        store,
        name="Backfill fixture",
        family_id="gate8_backfill_v1",
        midi_path=midi_path,
        score_path=score_path,
        evidence_path=None,
        plan_path=None,
        historical_neutral_render=neutral_path,
        producer_verdict_path=verdict_path,
        profile="remix_prettylights_v1",
        project_id="gate8-backfill",
    )
    adopted = project_adopt_causal_semantics(store, "gate8-backfill", expected_head=imported["revision"]["revision_sha"])
    bad = tmp_path / "bad"
    bad.mkdir()
    midi2, score2, _neutral2, _verdict2 = _fixture(bad, [(0, 480, 66), (240, 240, 68)], 960)
    try:
        project_extend_causal_score(
            store,
            "gate8-backfill",
            score_path=score2,
            midi_path=midi2,
            expected_head=adopted["revision"]["revision_sha"],
        )
    except Exception as exc:
        assert "backfill" in str(exc).lower() or "rewrote accepted event" in str(exc).lower()
    else:
        raise AssertionError("continuation accepted an event inside the sealed parent interval")


def test_source_phrase_cannot_execute_the_comparison_target_as_eligible_source(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    samples = np.zeros((44_100, 2), dtype=np.float32)
    samples[:, 0] = 0.1 * np.sin(2 * np.pi * 220 * np.arange(44_100) / 44_100)
    samples[:, 1] = samples[:, 0]
    sf.write(source, samples, 44_100, subtype="PCM_16")
    digest = sp_sha256_file(source)
    try:
        music_build_source_phrase(
            source,
            identity_label="target-negative-control",
            origin_kind="owned_source_recording",
            publication_eligible=True,
            source_role="foreground_vocal",
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            destination_start_seconds=0.0,
            destination_end_seconds=1.0,
            comparison_reference_sha256=digest,
        )
    except MusicSourcePhraseError as exc:
        assert "comparison target" in str(exc)
    else:
        raise AssertionError("comparison target was accepted as an eligible source")


def test_project_capabilities_are_side_effect_free_and_standalone_equal(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    package_store = tmp_path / "package-store"
    package = subprocess.run(
        [sys.executable, "-m", "earcrate", "project", "--root", str(package_store), "capabilities"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    assert not package_store.exists()
    package_json = json.loads(package.stdout)
    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    standalone_store = tmp_path / "standalone-store"
    standalone = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "project", "--root", str(standalone_store), "capabilities"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert standalone.returncode == 0, standalone.stdout + standalone.stderr
    assert not standalone_store.exists()
    assert json.loads(standalone.stdout) == package_json


def test_gate_runner_classifies_every_root_test_module() -> None:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    import run_gates

    discovered = {path.stem for path in root.glob("test_*.py")}
    classified = set(run_gates.MODULES) | set(getattr(run_gates, "EXCLUDED_MODULES", {}))
    assert discovered == classified, {
        "unclassified": sorted(discovered - classified),
        "missing_files": sorted(classified - discovered),
    }
