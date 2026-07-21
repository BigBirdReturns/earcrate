from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.live.instrumentation import (
    LiveActivityRecorder,
    live_activity_delta,
    live_activity_scope,
    live_record_activity,
)
from earcrate.live.model import LiveError
from earcrate.live.planner import (
    live_plan_session,
    live_validate_atlas,
    live_validate_session_plan,
)
from earcrate.midi.arranger import _arranger_allocate_channels
from earcrate.midi.model import (
    MIDI_LEDGER_KIND,
    MIDI_LEDGER_SCHEMA_VERSION,
    midi_seal_ledger,
    midi_sha256_json,
    midi_statistics,
    midi_tempo_map,
    midi_validate_ledger,
)
from earcrate.midi.render import midi_compile_note_spans

LIVE_MIDI_LOWERING_SCHEMA_VERSION = 1
LIVE_MIDI_LOWERING_KIND = "earcrate_live_midi_lowering"
LIVE_CPU_PROGRAM_SCHEMA_VERSION = 1
LIVE_CPU_PROGRAM_KIND = "earcrate_live_cpu_program"
LIVE_CPU_EXECUTION_SCHEMA_VERSION = 1
LIVE_CPU_EXECUTION_KIND = "earcrate_live_cpu_execution"

_EVENT_MARKERS = (
    "_generated_note_id",
    "_generated_note_off_id",
    "_generated_control_id",
    "_live_note_id",
    "_live_note_off_id",
    "_live_control_id",
)


def _live_message_priority(message: Mapping[str, Any], is_meta: bool) -> int:
    typ = str(message.get("type") or "")
    if is_meta and typ == "track_name":
        return 0
    if typ == "program_change":
        return 1
    if typ in {"control_change", "pitchwheel"}:
        return 2
    if typ == "note_off" or (typ == "note_on" and int(message.get("velocity") or 0) == 0):
        return 3
    if typ == "note_on":
        return 4
    if is_meta and typ == "end_of_track":
        return 9
    return 5


def _marker_identity(row: Mapping[str, Any]) -> str:
    return next((str(row[marker]) for marker in _EVENT_MARKERS if row.get(marker)), "")


def _live_track_events(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order MIDI events while retaining arranger and live provenance markers."""
    ordered = sorted(
        [deepcopy(dict(row)) for row in raw],
        key=lambda row: (
            int(row["tick"]),
            _live_message_priority(row["message"], bool(row["is_meta"])),
            midi_sha256_json(row["message"]),
            _marker_identity(row),
        ),
    )
    events = []
    for index, row in enumerate(ordered):
        event = {
            "tick": int(row["tick"]),
            "order": index,
            "is_meta": bool(row["is_meta"]),
            "message": deepcopy(dict(row["message"])),
        }
        for marker in _EVENT_MARKERS:
            if row.get(marker):
                event[marker] = str(row[marker])
        events.append(event)
    return events


def _live_find_slot(pattern: Mapping[str, Any], slot_id: str) -> dict[str, Any]:
    for row in pattern.get("slots") or []:
        if str(row.get("slot_id") or "") == str(slot_id):
            return deepcopy(dict(row))
    raise LiveError(f"pattern {pattern.get('pattern_id')} does not contain slot {slot_id}")


def _live_fragments(
    *,
    technique: str,
    source_start: int,
    source_duration: int,
    bar_ticks: int,
    category: str,
    is_candidate_layer: bool,
    ppq: int,
) -> list[tuple[int, int, int]]:
    duration = max(1, int(source_duration))
    if technique == "tease" and is_candidate_layer:
        return [(int(source_start), max(1, int(round(duration * 0.5))), 0)]
    if technique == "sample_chop" and is_candidate_layer and category in {"foreground", "fx", "harmony"}:
        subdivision = max(1, int(round(bar_ticks / 4.0)))
        count = max(1, min(4, int((duration + subdivision - 1) // subdivision)))
        rows = []
        for fragment in range(count):
            start = int(source_start) + fragment * subdivision
            if start >= int(source_start) + duration:
                break
            fragment_duration = max(1, min(subdivision // 2, int(source_start) + duration - start))
            rows.append((start, fragment_duration, fragment))
        return rows or [(int(source_start), duration, 0)]
    if technique == "echo_out":
        return [(int(source_start), duration + int(ppq), 0)]
    return [(int(source_start), duration, 0)]


def _live_control_event(
    *,
    tick: int,
    channel: int,
    control: int,
    value: int,
    control_id: str,
) -> dict[str, Any]:
    return {
        "tick": int(tick),
        "is_meta": False,
        "_live_control_id": str(control_id),
        "message": {
            "type": "control_change",
            "channel": int(channel),
            "control": int(control),
            "value": max(0, min(127, int(value))),
        },
    }


def live_validate_midi_lowering(lowering: Mapping[str, Any]) -> None:
    if int(lowering.get("schema_version") or 0) != LIVE_MIDI_LOWERING_SCHEMA_VERSION:
        raise LiveError(f"unsupported live MIDI lowering schema: {lowering.get('schema_version')}")
    if str(lowering.get("kind") or "") != LIVE_MIDI_LOWERING_KIND:
        raise LiveError(f"unsupported live MIDI lowering kind: {lowering.get('kind')}")
    ledger = lowering.get("ledger")
    if not isinstance(ledger, Mapping):
        raise LiveError("live MIDI lowering requires an output ledger")
    midi_validate_ledger(ledger)
    provenance = lowering.get("event_provenance")
    controls = lowering.get("control_provenance")
    outcomes = lowering.get("command_outcomes")
    if not isinstance(provenance, list) or not provenance:
        raise LiveError("live MIDI lowering requires note provenance")
    if not isinstance(controls, list):
        raise LiveError("live MIDI lowering control provenance must be a list")
    if not isinstance(outcomes, list):
        raise LiveError("live MIDI lowering command outcomes must be a list")
    note_ids = [str(row.get("generated_note_id") or "") for row in provenance]
    if not all(note_ids) or len(note_ids) != len(set(note_ids)):
        raise LiveError("live generated note IDs must be unique")
    command_ids = [str(row.get("command_id") or "") for row in outcomes]
    if not all(command_ids) or len(command_ids) != len(set(command_ids)):
        raise LiveError("live command outcomes must be unique")
    if any(str(row.get("status") or "") != "executed" for row in outcomes):
        raise LiveError("live MIDI lowering contains an unexecuted technique command")
    spans = midi_compile_note_spans(ledger)
    if len(spans["note_spans"]) != len(provenance):
        raise LiveError("live MIDI output note count disagrees with provenance")
    expected = midi_sha256_json({key: value for key, value in lowering.items() if key != "lowering_sha256"})
    if str(lowering.get("lowering_sha256") or "") != expected:
        raise LiveError("lowering_sha256 does not match live MIDI lowering")


def live_lower_session_to_midi(
    source_ledger: Mapping[str, Any],
    atlas: Mapping[str, Any],
    session: Mapping[str, Any],
    *,
    target_bpm: float = 0.0,
) -> dict[str, Any]:
    midi_validate_ledger(source_ledger)
    live_validate_atlas(atlas)
    live_validate_session_plan(session)
    if str(source_ledger["semantic_sha256"]) != str(session["source_semantic_sha256"]):
        raise LiveError("live session was planned from another MIDI performance")
    if str(atlas["atlas_sha256"]) != str(session["atlas_sha256"]):
        raise LiveError("live session and material atlas identities disagree")
    if target_bpm < 0.0:
        raise LiveError("live target BPM cannot be negative")

    bank = atlas["pattern_bank"]
    pattern_by_id = {str(row["pattern_id"]): row for row in bank["patterns"]}
    slot_specs = {str(row["slot_id"]): deepcopy(dict(row)) for row in bank["slots"]}
    decisions = [deepcopy(dict(row)) for row in session["decisions"]]
    ppq = int(source_ledger["ticks_per_beat"])
    numerator = int(atlas["meter"]["numerator"])
    denominator = int(atlas["meter"]["denominator"])
    bar_ticks = max(1, int(round(ppq * numerator * 4.0 / denominator)))
    total_ticks = len(decisions) * bar_ticks
    source_tempo = int(midi_tempo_map(source_ledger)[0]["tempo_us_per_beat"])
    tempo = int(round(60_000_000.0 / float(target_bpm))) if target_bpm > 0.0 else source_tempo

    used_slots = sorted(
        {str(layer["slot_id"]) for decision in decisions for layer in decision["layers"]},
        key=lambda slot_id: (
            str(slot_specs[slot_id].get("category") or ""),
            str(slot_specs[slot_id].get("role") or ""),
            str(slot_specs[slot_id].get("track_name") or ""),
            slot_id,
        ),
    )
    channel_map = _arranger_allocate_channels(used_slots, slot_specs)
    generated_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_provenance: list[dict[str, Any]] = []
    control_provenance: list[dict[str, Any]] = []
    command_effects: dict[str, list[str]] = {}
    command_meta: dict[str, dict[str, Any]] = {}

    for decision in decisions:
        bar_index = int(decision["bar_index"])
        target_start = bar_index * bar_ticks
        target_end = min(total_ticks, target_start + bar_ticks)
        technique = str(decision["operator"])
        candidate_pattern_id = str(decision.get("candidate_pattern_id") or "")
        velocity_scale = float(decision.get("velocity_scale") or 1.0)
        for command in decision.get("commands") or []:
            command_id = str(command["command_id"])
            command_effects[command_id] = []
            command_meta[command_id] = deepcopy(dict(command))

        for layer in decision["layers"]:
            pattern_id = str(layer["pattern_id"])
            slot_id = str(layer["slot_id"])
            pattern = pattern_by_id.get(pattern_id)
            if pattern is None:
                raise LiveError(f"live decision references unknown pattern {pattern_id}")
            source_slot = _live_find_slot(pattern, slot_id)
            output_channel = int(channel_map[slot_id])
            source_energy = max(0.05, float(pattern.get("source_energy") or 0.5))
            target_energy = float(decision.get("target_energy") or source_energy)
            energy_velocity = max(0.58, min(1.24, 0.84 + 0.42 * (target_energy - source_energy)))

            for source_control in source_slot.get("controls") or []:
                tick = target_start + int(round(float(source_control["offset_ratio"]) * bar_ticks))
                if tick >= total_ticks:
                    continue
                control_id = "live_control_event_" + midi_sha256_json(
                    {
                        "source_control_id": source_control["source_control_id"],
                        "bar_index": bar_index,
                        "slot_id": slot_id,
                        "session_sha256": session["session_sha256"],
                    }
                )[:24]
                message = deepcopy(dict(source_control["message"]))
                message["channel"] = output_channel
                generated_events[slot_id].append(
                    {
                        "tick": tick,
                        "is_meta": False,
                        "_live_control_id": control_id,
                        "message": message,
                    }
                )
                control_provenance.append(
                    {
                        "generated_control_id": control_id,
                        "source_control_id": str(source_control["source_control_id"]),
                        "bar_index": bar_index,
                        "operator": technique,
                        "pattern_id": pattern_id,
                        "slot_id": slot_id,
                        "tick": tick,
                        "message": deepcopy(message),
                        "snapshot": bool(source_control.get("snapshot")),
                    }
                )

            expression_commands = [
                command
                for command in decision.get("commands") or []
                if str(command.get("kind") or "") == "expression_ramp"
            ]
            for command in expression_commands:
                command_id = str(command["command_id"])
                start_value = int(round(127.0 * float(command.get("start", 1.0))))
                end_value = int(round(127.0 * float(command.get("end", 1.0))))
                start_id = "live_control_event_" + midi_sha256_json(
                    {"command_id": command_id, "slot_id": slot_id, "edge": "start"}
                )[:24]
                end_id = "live_control_event_" + midi_sha256_json(
                    {"command_id": command_id, "slot_id": slot_id, "edge": "end"}
                )[:24]
                generated_events[slot_id].append(
                    _live_control_event(
                        tick=target_start,
                        channel=output_channel,
                        control=11,
                        value=start_value,
                        control_id=start_id,
                    )
                )
                generated_events[slot_id].append(
                    _live_control_event(
                        tick=max(target_start, target_end - 1),
                        channel=output_channel,
                        control=11,
                        value=end_value,
                        control_id=end_id,
                    )
                )
                command_effects[command_id].extend([start_id, end_id])
                for generated_id, tick, value in (
                    (start_id, target_start, start_value),
                    (end_id, max(target_start, target_end - 1), end_value),
                ):
                    control_provenance.append(
                        {
                            "generated_control_id": generated_id,
                            "source_control_id": None,
                            "bar_index": bar_index,
                            "operator": technique,
                            "pattern_id": pattern_id,
                            "slot_id": slot_id,
                            "tick": tick,
                            "message": {
                                "type": "control_change",
                                "channel": output_channel,
                                "control": 11,
                                "value": value,
                            },
                            "snapshot": False,
                            "command_id": command_id,
                        }
                    )

            for source_event in source_slot["events"]:
                source_start = int(round(float(source_event["offset_ratio"]) * bar_ticks))
                source_duration = max(1, int(round(float(source_event["duration_ratio"]) * bar_ticks)))
                fragments = _live_fragments(
                    technique=technique,
                    source_start=source_start,
                    source_duration=source_duration,
                    bar_ticks=bar_ticks,
                    category=str(layer["category"]),
                    is_candidate_layer=pattern_id == candidate_pattern_id,
                    ppq=ppq,
                )
                for local_start, fragment_duration, fragment_index in fragments:
                    start_tick = target_start + int(local_start)
                    end_tick = min(total_ticks, start_tick + max(1, int(fragment_duration)))
                    if start_tick >= total_ticks or end_tick <= start_tick:
                        continue
                    velocity = max(
                        1,
                        min(
                            127,
                            int(
                                round(
                                    int(source_event["velocity"])
                                    * velocity_scale
                                    * energy_velocity
                                )
                            ),
                        ),
                    )
                    generated_id = "live_note_" + midi_sha256_json(
                        {
                            "source_event_id": source_event["source_event_id"],
                            "bar_index": bar_index,
                            "slot_id": slot_id,
                            "pattern_id": pattern_id,
                            "operator": technique,
                            "fragment_index": fragment_index,
                            "session_sha256": session["session_sha256"],
                        }
                    )[:24]
                    generated_events[slot_id].extend(
                        [
                            {
                                "tick": start_tick,
                                "is_meta": False,
                                "_live_note_id": generated_id,
                                "message": {
                                    "type": "note_on",
                                    "channel": output_channel,
                                    "note": int(source_event["note"]),
                                    "velocity": velocity,
                                },
                            },
                            {
                                "tick": end_tick,
                                "is_meta": False,
                                "_live_note_off_id": generated_id,
                                "message": {
                                    "type": "note_off",
                                    "channel": output_channel,
                                    "note": int(source_event["note"]),
                                    "velocity": 0,
                                },
                            },
                        ]
                    )
                    event_provenance.append(
                        {
                            "generated_note_id": generated_id,
                            "source_event_id": str(source_event["source_event_id"]),
                            "source_pattern_id": pattern_id,
                            "source_bar_index": int(pattern["source_bar_index"]),
                            "target_bar_index": bar_index,
                            "operator": technique,
                            "persona": str(decision["persona"]),
                            "slot_id": slot_id,
                            "role": str(layer["role"]),
                            "category": str(layer["category"]),
                            "fragment_index": int(fragment_index),
                            "start_tick": start_tick,
                            "message_end_tick": end_tick,
                            "note": int(source_event["note"]),
                            "velocity": velocity,
                            "output_channel": output_channel,
                        }
                    )
                    for command in decision.get("commands") or []:
                        kind = str(command.get("kind") or "")
                        command_id = str(command["command_id"])
                        if kind in {"gate_new_layers", "retrigger_subdivision", "tail_hold"}:
                            command_effects[command_id].append(generated_id)

        layer_ids = [str(layer["layer_id"]) for layer in decision["layers"]]
        for command in decision.get("commands") or []:
            command_id = str(command["command_id"])
            kind = str(command.get("kind") or "")
            if kind in {
                "replace_all_layers",
                "repeat_active_layers",
                "crossfade_layers",
                "mute_categories",
                "replace_category",
                "activate_missing_categories",
            }:
                command_effects[command_id].extend(layer_ids)

    conductor_raw = [
        {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": "EarCrate Live Conductor"}},
        {"tick": 0, "is_meta": True, "message": {"type": "set_tempo", "tempo": tempo}},
        {
            "tick": 0,
            "is_meta": True,
            "message": {
                "type": "time_signature",
                "numerator": numerator,
                "denominator": denominator,
                "clocks_per_click": 24,
                "notated_32nd_notes_per_beat": 8,
            },
        },
        {"tick": total_ticks, "is_meta": True, "message": {"type": "end_of_track"}},
    ]
    tracks = [
        {
            "track_index": 0,
            "name": "EarCrate Live Conductor",
            "events": _live_track_events(conductor_raw),
        }
    ]
    note_on_locators: dict[str, dict[str, Any]] = {}
    note_off_locators: dict[str, dict[str, Any]] = {}
    control_locators: dict[str, dict[str, Any]] = {}
    for slot_id in used_slots:
        spec = slot_specs[slot_id]
        channel = int(channel_map[slot_id])
        raw = [
            {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": str(spec["track_name"])}},
            {"tick": 0, "is_meta": False, "message": {"type": "program_change", "channel": channel, "program": int(spec["program"])}},
            *generated_events[slot_id],
            {"tick": total_ticks, "is_meta": True, "message": {"type": "end_of_track"}},
        ]
        track_index = len(tracks)
        events = _live_track_events(raw)
        for event in events:
            note_id = event.pop("_live_note_id", None)
            note_off_id = event.pop("_live_note_off_id", None)
            control_id = event.pop("_live_control_id", None)
            locator = {
                "track_index": track_index,
                "order": int(event["order"]),
                "tick": int(event["tick"]),
                "message": deepcopy(dict(event["message"])),
            }
            if note_id:
                note_on_locators[str(note_id)] = locator
            if note_off_id:
                note_off_locators[str(note_off_id)] = locator
            if control_id:
                control_locators[str(control_id)] = locator
        tracks.append({"track_index": track_index, "name": str(spec["track_name"]), "events": events})

    output = midi_seal_ledger(
        {
            "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
            "kind": MIDI_LEDGER_KIND,
            "midi_type": 1,
            "ticks_per_beat": ppq,
            "tracks": tracks,
            "generated_from": {
                "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
                "atlas_sha256": str(atlas["atlas_sha256"]),
                "session_sha256": str(session["session_sha256"]),
            },
        }
    )
    compiled = midi_compile_note_spans(output)
    span_queues: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for span in compiled["note_spans"]:
        span_queues[
            (
                int(span["track_index"]),
                int(span["channel"]),
                int(span["note"]),
                int(span["start_tick"]),
            )
        ].append(span)
    for rows in span_queues.values():
        rows.sort(key=lambda row: (int(row["end_tick"]), str(row["event_id"])))

    for row in event_provenance:
        note_locator = note_on_locators.get(str(row["generated_note_id"]))
        off_locator = note_off_locators.get(str(row["generated_note_id"]))
        if note_locator is None or off_locator is None:
            raise LiveError(f"generated live note {row['generated_note_id']} is missing an output locator")
        key = (
            int(note_locator["track_index"]),
            int(note_locator["message"]["channel"]),
            int(note_locator["message"]["note"]),
            int(note_locator["tick"]),
        )
        if not span_queues[key]:
            raise LiveError(f"generated live note {row['generated_note_id']} has no compiled sounding span")
        span = span_queues[key].pop(0)
        row["output_note_on"] = note_locator
        row["output_note_off"] = off_locator
        row["output_span_event_id"] = str(span["event_id"])
        row["sounding_end_tick"] = int(span["end_tick"])
        row["sounding_end_reason"] = str(span["end_reason"])
    for row in control_provenance:
        locator = control_locators.get(str(row["generated_control_id"]))
        if locator is None:
            raise LiveError(f"generated live control {row['generated_control_id']} is missing an output locator")
        row["output_event"] = locator

    command_outcomes = []
    for command_id in sorted(command_meta):
        effects = sorted(set(str(value) for value in command_effects.get(command_id) or []))
        if not effects:
            raise LiveError(f"live technique command {command_id} lowered to no executable effect")
        command_outcomes.append(
            {
                "command_id": command_id,
                "kind": str(command_meta[command_id].get("kind") or ""),
                "technique": str(command_meta[command_id].get("technique") or ""),
                "status": "executed",
                "effect_ids": effects,
            }
        )

    lowering = {
        "schema_version": LIVE_MIDI_LOWERING_SCHEMA_VERSION,
        "kind": LIVE_MIDI_LOWERING_KIND,
        "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "session_sha256": str(session["session_sha256"]),
        "target_bpm": round(60_000_000.0 / tempo, 9),
        "bar_ticks": bar_ticks,
        "target_bars": len(decisions),
        "channel_assignments": [
            {
                "slot_id": slot_id,
                "track_name": str(slot_specs[slot_id]["track_name"]),
                "role": str(slot_specs[slot_id]["role"]),
                "mode": str(slot_specs[slot_id]["mode"]),
                "output_channel": int(channel_map[slot_id]),
            }
            for slot_id in used_slots
        ],
        "event_provenance": event_provenance,
        "control_provenance": control_provenance,
        "command_outcomes": command_outcomes,
        "output_statistics": midi_statistics(output),
        "ledger": output,
    }
    lowering["lowering_sha256"] = midi_sha256_json(lowering)
    live_validate_midi_lowering(lowering)
    return lowering


def _live_cpu_command(tick: int, order: int, kind: str, **payload: Any) -> dict[str, Any]:
    row = {"tick": int(tick), "order": int(order), "kind": str(kind), **deepcopy(payload)}
    row["program_command_id"] = "live_cpu_command_" + midi_sha256_json(row)[:24]
    return row


def live_validate_cpu_program(program: Mapping[str, Any]) -> None:
    if int(program.get("schema_version") or 0) != LIVE_CPU_PROGRAM_SCHEMA_VERSION:
        raise LiveError(f"unsupported live CPU program schema: {program.get('schema_version')}")
    if str(program.get("kind") or "") != LIVE_CPU_PROGRAM_KIND:
        raise LiveError(f"unsupported live CPU program kind: {program.get('kind')}")
    commands = program.get("commands")
    if not isinstance(commands, list) or not commands:
        raise LiveError("live CPU program requires commands")
    ids = [str(row.get("program_command_id") or "") for row in commands]
    if not all(ids) or len(ids) != len(set(ids)):
        raise LiveError("live CPU program command IDs must be unique")
    previous = (-1, -1)
    for row in commands:
        current = (int(row.get("tick", -1)), int(row.get("order", -1)))
        if current < previous:
            raise LiveError("live CPU commands are not ordered")
        previous = current
    expected = midi_sha256_json({key: value for key, value in program.items() if key != "program_sha256"})
    if str(program.get("program_sha256") or "") != expected:
        raise LiveError("program_sha256 does not match live CPU program")


def live_compile_cpu_program(atlas: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    live_validate_atlas(atlas)
    live_validate_session_plan(session)
    if str(atlas["atlas_sha256"]) != str(session["atlas_sha256"]):
        raise LiveError("cannot compile a CPU program from mismatched atlas and session")
    ppq = int(atlas["ticks_per_beat"])
    numerator = int(atlas["meter"]["numerator"])
    denominator = int(atlas["meter"]["denominator"])
    bar_ticks = max(1, int(round(ppq * numerator * 4.0 / denominator)))
    commands = []
    active: dict[str, dict[str, Any]] = {}
    order = 0
    for decision in session["decisions"]:
        tick = int(decision["bar_index"]) * bar_ticks
        selected = {str(row["layer_id"]): deepcopy(dict(row)) for row in decision["layers"]}
        for layer_id in sorted(set(active) - set(selected)):
            commands.append(_live_cpu_command(tick, order, "deactivate_layer", layer_id=layer_id, layer=active[layer_id]))
            order += 1
        for layer_id in sorted(set(selected) - set(active)):
            commands.append(_live_cpu_command(tick, order, "activate_layer", layer_id=layer_id, layer=selected[layer_id]))
            order += 1
        for technique_command in decision.get("commands") or []:
            commands.append(
                _live_cpu_command(
                    tick,
                    order,
                    "technique_command",
                    technique=str(decision["operator"]),
                    command=deepcopy(dict(technique_command)),
                    bar_index=int(decision["bar_index"]),
                )
            )
            order += 1
        commands.append(
            _live_cpu_command(
                tick,
                order,
                "bar_commit",
                bar_index=int(decision["bar_index"]),
                persona=str(decision["persona"]),
                operator=str(decision["operator"]),
                expected_layer_ids=sorted(selected),
            )
        )
        order += 1
        active = selected
    end_tick = len(session["decisions"]) * bar_ticks
    for layer_id in sorted(active):
        commands.append(_live_cpu_command(end_tick, order, "deactivate_layer", layer_id=layer_id, layer=active[layer_id]))
        order += 1
    commands.sort(key=lambda row: (int(row["tick"]), int(row["order"]), str(row["program_command_id"])))
    for index, row in enumerate(commands):
        row["order"] = index
        row["program_command_id"] = "live_cpu_command_" + midi_sha256_json(
            {key: value for key, value in row.items() if key != "program_command_id"}
        )[:24]
    program = {
        "schema_version": LIVE_CPU_PROGRAM_SCHEMA_VERSION,
        "kind": LIVE_CPU_PROGRAM_KIND,
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "session_sha256": str(session["session_sha256"]),
        "ticks_per_beat": ppq,
        "bar_ticks": bar_ticks,
        "declared_pattern_count": int(atlas["declared_pattern_count"]),
        "declared_material_count": int(atlas["declared_material_count"]),
        "command_count": len(commands),
        "commands": commands,
    }
    program["program_sha256"] = midi_sha256_json(program)
    live_validate_cpu_program(program)
    return program


def live_validate_cpu_execution(execution: Mapping[str, Any]) -> None:
    if int(execution.get("schema_version") or 0) != LIVE_CPU_EXECUTION_SCHEMA_VERSION:
        raise LiveError(f"unsupported live CPU execution schema: {execution.get('schema_version')}")
    if str(execution.get("kind") or "") != LIVE_CPU_EXECUTION_KIND:
        raise LiveError(f"unsupported live CPU execution kind: {execution.get('kind')}")
    outcomes = execution.get("outcomes")
    if not isinstance(outcomes, list):
        raise LiveError("live CPU execution outcomes must be a list")
    if len(outcomes) != int(execution.get("selected_command_count") or 0):
        raise LiveError("live CPU execution does not account for every command")
    if bool(execution.get("complete")) and any(str(row.get("status") or "") != "executed" for row in outcomes):
        raise LiveError("complete live CPU execution contains a refused command")
    activity = execution.get("activity_delta")
    if not isinstance(activity, Mapping):
        raise LiveError("live CPU execution requires a measured activity delta")
    expected = midi_sha256_json({key: value for key, value in execution.items() if key != "execution_sha256"})
    if str(execution.get("execution_sha256") or "") != expected:
        raise LiveError("execution_sha256 does not match live CPU execution")


def live_execute_cpu_program(
    program: Mapping[str, Any],
    *,
    activity_recorder: LiveActivityRecorder | None = None,
) -> dict[str, Any]:
    live_validate_cpu_program(program)
    recorder = activity_recorder or LiveActivityRecorder()
    before = recorder.snapshot()
    active: dict[str, dict[str, Any]] = {}
    outcomes = []
    peak_active = 0
    refused = 0
    with live_activity_scope(recorder, "cpu_execution"):
        for command in program["commands"]:
            live_record_activity(
                "cpu_command",
                detail={
                    "program_command_id": command["program_command_id"],
                    "kind": command["kind"],
                },
            )
            kind = str(command["kind"])
            status = "executed"
            reason = ""
            if kind == "activate_layer":
                layer_id = str(command["layer_id"])
                if layer_id in active:
                    status = "refused"
                    reason = "layer_already_active"
                else:
                    active[layer_id] = deepcopy(dict(command["layer"]))
            elif kind == "deactivate_layer":
                layer_id = str(command["layer_id"])
                if layer_id not in active:
                    status = "refused"
                    reason = "layer_not_active"
                else:
                    active.pop(layer_id)
            elif kind == "bar_commit":
                expected_layers = sorted(str(value) for value in command["expected_layer_ids"])
                if sorted(active) != expected_layers:
                    status = "refused"
                    reason = "active_layer_state_mismatch"
            elif kind == "technique_command":
                technique_command = command.get("command") or {}
                if not str(technique_command.get("command_id") or ""):
                    status = "refused"
                    reason = "technique_command_missing_identity"
            else:
                status = "refused"
                reason = "unknown_program_command"
            if status != "executed":
                refused += 1
            peak_active = max(peak_active, len(active))
            outcomes.append(
                {
                    "program_command_id": str(command["program_command_id"]),
                    "tick": int(command["tick"]),
                    "kind": kind,
                    "status": status,
                    "reason": reason,
                    "active_layer_count_after": len(active),
                }
            )
    after = recorder.snapshot()
    delta = live_activity_delta(before, after)
    complete = refused == 0 and not active
    execution = {
        "schema_version": LIVE_CPU_EXECUTION_SCHEMA_VERSION,
        "kind": LIVE_CPU_EXECUTION_KIND,
        "program_sha256": str(program["program_sha256"]),
        "complete": complete,
        "selected_command_count": len(program["commands"]),
        "executed_command_count": len(program["commands"]) - refused,
        "refused_command_count": refused,
        "peak_active_layer_count": peak_active,
        "declared_pattern_count": int(program["declared_pattern_count"]),
        "declared_material_count": int(program["declared_material_count"]),
        "patterns_scanned_during_execution": int(delta["domains"]["cpu_execution"]["pattern_scan"]),
        "materials_scanned_during_execution": int(delta["domains"]["cpu_execution"]["material_scan"]),
        "runtime_operation_count": int(delta["domains"]["cpu_execution"]["cpu_command"]),
        "active_layers_after_execution": sorted(active),
        "activity_delta": delta,
        "outcomes": outcomes,
    }
    execution["execution_sha256"] = midi_sha256_json(execution)
    live_validate_cpu_execution(execution)
    if not complete:
        raise LiveError("live CPU execution refused one or more selected commands")
    return execution


def live_build_session(
    source_ledger: Mapping[str, Any],
    *,
    target_bars: int = 64,
    persona: str = "club",
    seed: int = 1,
    controls: Sequence[Mapping[str, Any]] | None = None,
    target_energy: float | None = None,
    density: float | None = None,
    risk: float | None = None,
    maximum_layers: int | None = None,
    horizon_bars: int = 0,
    phrase_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
    target_bpm: float = 0.0,
    activity_recorder: LiveActivityRecorder | None = None,
) -> dict[str, Any]:
    recorder = activity_recorder or LiveActivityRecorder()
    before = recorder.snapshot()
    with live_activity_scope(recorder, "control"):
        planned = live_plan_session(
            source_ledger,
            target_bars=target_bars,
            persona=persona,
            seed=seed,
            controls=controls,
            target_energy=target_energy,
            density=density,
            risk=risk,
            maximum_layers=maximum_layers,
            horizon_bars=horizon_bars,
            phrase_bars=phrase_bars,
            beam_width=beam_width,
            candidate_limit=candidate_limit,
        )
    lowering = live_lower_session_to_midi(
        source_ledger,
        planned["atlas"],
        planned["session"],
        target_bpm=target_bpm,
    )
    program = live_compile_cpu_program(planned["atlas"], planned["session"])
    execution = live_execute_cpu_program(program, activity_recorder=recorder)
    activity = live_activity_delta(before, recorder.snapshot())
    return {
        "ok": True,
        "complete": True,
        "atlas": planned["atlas"],
        "session": planned["session"],
        "final_state": planned["final_state"],
        "midi_lowering": lowering,
        "midi_ledger": lowering["ledger"],
        "cpu_program": program,
        "cpu_execution": execution,
        "activity_delta": activity,
        "activity_receipt": recorder.snapshot(),
    }
