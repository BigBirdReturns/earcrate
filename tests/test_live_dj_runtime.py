from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import mido

from earcrate.live.planner import live_atlas_from_midi, live_plan_session
from earcrate.live.runtime import (
    live_build_session,
    live_execute_cpu_program,
)
from earcrate.midi.codec import midi_read
from earcrate.midi.model import midi_sha256_json
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
        (0, mido.Message("control_change", channel=0, control=7, value=104, time=0)),
    ]
    for bar in range(16):
        root = 36 + (bar % 4) * 2
        for step, interval in enumerate((0, 0, 3, 7)):
            _note(bass, bar * BAR + step * PPQ, 96, root + interval, 78 + (bar % 3) * 4, 0)
    bass.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Fingered Bass", bass))

    drums: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    for bar in range(4, 16):
        for offset, note, velocity in ((0, 36, 112), (192, 42, 76), (384, 38, 106), (576, 42, 82)):
            _note(drums, bar * BAR + offset, 48, note, velocity, 9)
    drums.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Drums", drums))

    lead: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=1, program=80, time=0)),
        (0, mido.Message("control_change", channel=1, control=1, value=28, time=0)),
    ]
    for bar in range(8, 16):
        start = bar * BAR
        lead.append((start, mido.Message("pitchwheel", channel=1, pitch=768, time=0)))
        lead.append((start + 96, mido.Message("pitchwheel", channel=1, pitch=0, time=0)))
        for offset, note in ((0, 72), (192, 74), (384, 79), (576, 76)):
            _note(lead, start + offset, 144, note + (bar % 2), 104 + (bar % 2) * 5, 1)
    lead.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Synth Lead", lead))

    pad: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=2, program=88, time=0)),
        (0, mido.Message("control_change", channel=2, control=11, value=76, time=0)),
    ]
    for bar in [*range(0, 4), *range(12, 16)]:
        start = bar * BAR
        pad.append((start, mido.Message("control_change", channel=2, control=11, value=72 + (bar % 4) * 6, time=0)))
        _note(pad, start, BAR - 24, 60 + (bar % 2) * 5, 62, 2)
    pad.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Synth Pad", pad))

    fx: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.Message("program_change", channel=3, program=96, time=0)),
    ]
    for bar in (7, 11, 15):
        _note(fx, bar * BAR + 3 * PPQ, PPQ, 84, 94, 3)
    fx.append((16 * BAR, mido.MetaMessage("end_of_track", time=0)))
    midi.tracks.append(_track("Synth FX", fx))
    midi.save(path)


def test_live_session_switches_personas_techniques_and_executes_exactly(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    controls = [
        {"at_bar": 4, "command": "set_persona", "value": "girl_talk"},
        {"at_bar": 8, "command": "disable_technique", "value": "loop_extend"},
        {"at_bar": 12, "command": "set_persona", "value": "pretty_lights"},
        {"at_bar": 16, "command": "force_technique", "value": "hard_cut"},
    ]
    build = live_build_session(
        ledger,
        target_bars=24,
        persona="club",
        seed=73,
        controls=controls,
        beam_width=20,
        candidate_limit=10,
    )
    decisions = build["session"]["decisions"]
    assert len(decisions) == 24
    assert all(row["persona"] == "club" for row in decisions[:4])
    assert all(row["persona"] == "girl_talk" for row in decisions[4:12])
    assert all(row["persona"] == "pretty_lights" for row in decisions[12:])
    assert all(row["operator"] != "loop_extend" for row in decisions[8:])
    assert all(row["operator"] == "hard_cut" for row in decisions[16:20])
    assert all(row["alternatives"] for row in decisions)
    assert build["midi_lowering"]["output_statistics"]["note_on_count"] == len(build["midi_lowering"]["event_provenance"])
    assert build["midi_lowering"]["command_outcomes"]
    assert all(row["status"] == "executed" for row in build["midi_lowering"]["command_outcomes"])
    assert build["cpu_execution"]["complete"] is True
    assert build["cpu_execution"]["materials_scanned_during_execution"] == 0
    render = midi_render_ledger(build["midi_ledger"], tmp_path / "live.wav", sample_rate=8_000)
    assert render["complete_execution"] is True
    assert render["selected_event_count"] == len(build["midi_lowering"]["event_provenance"])


def test_live_hold_forces_loop_extension_until_release(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    planned = live_plan_session(
        midi_read(source),
        target_bars=12,
        persona="pretty_lights",
        seed=11,
        controls=[
            {"at_bar": 4, "command": "hold", "value": True},
            {"at_bar": 8, "command": "release_hold", "value": True},
        ],
        beam_width=16,
        candidate_limit=8,
    )
    decisions = planned["session"]["decisions"]
    assert all(row["operator"] == "loop_extend" for row in decisions[4:8])
    assert planned["final_state"]["hold_active"] is False
    assert planned["session"]["applied_control_count"] == 2


def test_live_planning_is_deterministic_and_persona_changes_the_performance(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    first = live_build_session(ledger, target_bars=16, persona="club", seed=9, beam_width=16, candidate_limit=8)
    second = live_build_session(ledger, target_bars=16, persona="club", seed=9, beam_width=16, candidate_limit=8)
    lights = live_build_session(ledger, target_bars=16, persona="pretty_lights", seed=9, beam_width=16, candidate_limit=8)
    assert first["atlas"]["atlas_sha256"] == second["atlas"]["atlas_sha256"]
    assert first["session"]["session_sha256"] == second["session"]["session_sha256"]
    assert first["midi_ledger"]["semantic_sha256"] == second["midi_ledger"]["semantic_sha256"]
    assert first["session"]["session_sha256"] != lights["session"]["session_sha256"]
    assert first["midi_ledger"]["semantic_sha256"] != lights["midi_ledger"]["semantic_sha256"]


def test_sparse_cpu_execution_cost_tracks_commands_not_declared_library_size(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    build = live_build_session(midi_read(source), target_bars=16, persona="club", seed=5, beam_width=12, candidate_limit=6)
    program = deepcopy(build["cpu_program"])
    original_operations = int(program["command_count"])
    program["declared_material_count"] = 1_000_000
    program["declared_pattern_count"] = 100_000
    program["program_sha256"] = midi_sha256_json({key: value for key, value in program.items() if key != "program_sha256"})
    execution = live_execute_cpu_program(program)
    assert execution["complete"] is True
    assert execution["runtime_operation_count"] == original_operations
    assert execution["declared_material_count"] == 1_000_000
    assert execution["materials_scanned_during_execution"] == 0
    assert execution["patterns_scanned_during_execution"] == 0


def test_single_file_package_executes_live_session_command(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    source = tmp_path / "source.mid"
    _write_source(source)
    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    output = tmp_path / "live-build"
    run = subprocess.run(
        [
            sys.executable,
            str(root / "dist" / "earcrate.py"),
            "live",
            "session",
            str(source),
            str(output),
            "--bars",
            "16",
            "--persona",
            "pretty_lights",
            "--beam-width",
            "12",
            "--candidate-limit",
            "6",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ok"] is True and payload["complete"] is True
    assert payload["materials_scanned_during_execution"] == 0
    assert (output / "session.mid").is_file()
    assert (output / "cpu.execution.json").is_file()
