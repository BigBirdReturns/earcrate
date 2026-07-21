from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import mido

from earcrate.midi.arranger import (
    midi_generate_pattern_arrangement,
    midi_pattern_bank,
    midi_write_pattern_arrangement,
)
from earcrate.midi.codec import midi_read
from earcrate.midi.render import midi_render_ledger

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
    conductor = [
        (0, mido.MetaMessage("set_tempo", tempo=500_000, time=0)),
        (0, mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)),
        (16 * BAR, mido.MetaMessage("end_of_track", time=0)),
    ]
    midi.tracks.append(_track("Conductor", conductor))

    bass: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=0, program=33, time=0)),
        (0, mido.Message("control_change", channel=0, control=7, value=102, time=0)),
    ]
    bass_notes = (36, 36, 39, 43)
    for bar in range(16):
        for step, note in enumerate(bass_notes):
            _note(bass, bar * BAR + step * PPQ, 96, note + (bar % 2) * 2, 78 + (bar % 4) * 3, 0)
    bass.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Fingered Bass", bass))

    drums: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    for bar in range(4, 12):
        for offset, note, velocity in ((0, 36, 108), (192, 42, 75), (384, 38, 104), (576, 42, 79)):
            _note(drums, bar * BAR + offset, 48, note, velocity, 9)
    drums.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Drums", drums))

    lead: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=1, program=80, time=0)),
        (0, mido.Message("control_change", channel=1, control=1, value=32, time=0)),
    ]
    for bar in range(8, 12):
        start = bar * BAR
        lead.append((start, mido.Message("pitchwheel", channel=1, pitch=1024, time=0)))
        lead.append((start + 96, mido.Message("pitchwheel", channel=1, pitch=0, time=0)))
        for offset, note in ((0, 72), (192, 74), (384, 79), (576, 76)):
            _note(lead, start + offset, 144, note + (bar % 2), 108 + (bar % 2) * 4, 1)
    lead.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Synth Lead", lead))

    pad: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=2, program=88, time=0)),
        (0, mido.Message("control_change", channel=2, control=11, value=72, time=0)),
    ]
    for bar in range(12, 16):
        start = bar * BAR
        pad.append((start, mido.Message("control_change", channel=2, control=11, value=72 + 6 * (bar - 12), time=0)))
        _note(pad, start, BAR - 24, 60 + (bar % 2) * 5, 58, 2)
    pad.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Synth Pad", pad))
    midi.save(path)


def test_arranger_is_deterministic_event_complete_and_neutral_renderable(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    first = midi_generate_pattern_arrangement(
        ledger,
        target_bars=32,
        seed=73,
        form_variant="classic",
        target_bpm=108.0,
        density=1.0,
        maximum_layers=6,
    )
    second = midi_generate_pattern_arrangement(
        ledger,
        target_bars=32,
        seed=73,
        form_variant="classic",
        target_bpm=108.0,
        density=1.0,
        maximum_layers=6,
    )

    assert first["ledger"]["semantic_sha256"] == second["ledger"]["semantic_sha256"]
    assert first["plan"]["plan_sha256"] == second["plan"]["plan_sha256"]
    assert first["pattern_bank"]["pattern_bank_sha256"] == second["pattern_bank"]["pattern_bank_sha256"]
    plan = first["plan"]
    assert plan["target_bars"] == len(plan["bar_decisions"]) == 32
    assert [section["label"] for section in plan["form"]] == ["intro", "groove", "build", "drop", "breakdown", "drop", "outro"]
    assert all(decision["alternatives"] for decision in plan["bar_decisions"])
    assert plan["generated_note_count"] == plan["output_statistics"]["note_on_count"]
    assert plan["generated_control_count"] > 0
    assert plan["generated_control_count"] == plan["output_statistics"]["control_change_count"] + plan["output_statistics"]["pitchwheel_count"]
    assert len({row["output_event_id"] for row in plan["event_provenance"]}) == plan["generated_note_count"]
    assert all(row["source_event_id"] and row["source_pattern_id"] for row in plan["event_provenance"])
    assert all(row["output_note_off_event_id"] for row in plan["event_provenance"])
    pitched_channels = [row["output_channel"] for row in plan["channel_assignments"] if row["mode"] == "pitched"]
    assert 9 not in pitched_channels and len(pitched_channels) == len(set(pitched_channels))
    assert all(row["output_channel"] == 9 for row in plan["channel_assignments"] if row["mode"] == "trigger")

    render = midi_render_ledger(first["ledger"], tmp_path / "generated.wav", sample_rate=8_000)
    assert render["ok"] is True
    assert render["complete_execution"] is True
    assert render["selected_event_count"] == plan["generated_note_count"]
    assert render["partially_executed_event_count"] == render["refused_event_count"] == 0


def test_density_changes_layer_decisions_without_changing_the_source_corpus(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    sparse = midi_generate_pattern_arrangement(ledger, target_bars=24, seed=9, density=0.55, maximum_layers=6)
    dense = midi_generate_pattern_arrangement(ledger, target_bars=24, seed=9, density=1.5, maximum_layers=6)
    sparse_layers = sum(len(row["selected_slot_ids"]) for row in sparse["plan"]["bar_decisions"])
    dense_layers = sum(len(row["selected_slot_ids"]) for row in dense["plan"]["bar_decisions"])
    assert dense_layers > sparse_layers
    assert sparse["plan"]["pattern_bank_sha256"] == dense["plan"]["pattern_bank_sha256"]
    assert sparse["ledger"]["semantic_sha256"] != dense["ledger"]["semantic_sha256"]


def test_pattern_bank_preserves_note_and_controller_sources(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    bank = midi_pattern_bank(midi_read(source))
    assert bank["pattern_count"] == 15
    assert bank["excluded_partial_bar_count"] == 1
    assert bank["excluded_partial_bars"][0]["bar_index"] == 15
    assert any(slot["controls"]["pitch_bend_used"] for slot in bank["slots"])
    source_events = [event for pattern in bank["patterns"] for slot in pattern["slots"] for event in slot["events"]]
    source_controls = [event for pattern in bank["patterns"] for slot in pattern["slots"] for event in slot["controls"]]
    assert source_events and all(row["source_event_id"] for row in source_events)
    assert source_controls and all(row["source_control_id"] for row in source_controls)
    assert any(row["snapshot"] for row in source_controls)
    assert any(row["message"]["type"] == "pitchwheel" for row in source_controls)


def test_arrangement_writer_preflights_all_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    output = tmp_path / "generated.mid"
    plan = tmp_path / "generated.plan.json"
    bank = tmp_path / "generated.patterns.json"
    receipt = midi_write_pattern_arrangement(
        ledger,
        output,
        plan,
        bank,
        target_bars=16,
        seed=11,
    )
    assert output.is_file() and plan.is_file() and bank.is_file()
    assert midi_read(output)["semantic_sha256"] == receipt["output_semantic_sha256"]
    stored = json.loads(plan.read_text(encoding="utf-8"))
    assert stored["plan_sha256"] == receipt["plan_sha256"]

    conflict = tmp_path / "already-there.plan.json"
    conflict.write_text("occupied", encoding="utf-8")
    refused_midi = tmp_path / "must-not-exist.mid"
    try:
        midi_write_pattern_arrangement(
            ledger,
            refused_midi,
            conflict,
            tmp_path / "must-not-exist.patterns.json",
            target_bars=16,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("arrangement writer ignored a preflight conflict")
    assert not refused_midi.exists()


def test_single_file_namespace_executes_the_arranger(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    source = tmp_path / "source.mid"
    _write_source(source)
    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    namespace = runpy.run_path(str(root / "dist" / "earcrate.py"), run_name="earcrate_arranger_singlefile_gate")
    result = namespace["midi_generate_pattern_arrangement"](midi_read(source), target_bars=16, seed=5)
    assert result["ok"] is True
    assert result["plan"]["generated_note_count"] == result["plan"]["output_statistics"]["note_on_count"]
