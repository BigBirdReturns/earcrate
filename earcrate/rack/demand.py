from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from earcrate.midi.model import MidiTempoClock, midi_sha256_json, midi_validate_ledger
from earcrate.midi.render import midi_compile_note_spans
from earcrate.rack.model import RackError

DEMAND_SCHEMA_VERSION = 1
DEMAND_KIND = "earcrate_performance_demand"

_GM_FAMILIES = (
    "piano",
    "chromatic_percussion",
    "organ",
    "guitar",
    "bass",
    "strings",
    "ensemble",
    "brass",
    "reed",
    "pipe",
    "synth_lead",
    "synth_pad",
    "synth_fx",
    "ethnic",
    "percussive",
    "sound_fx",
)

_ROLE_KEYWORDS = (
    ("kick", "kick"),
    ("snare", "snare"),
    ("hat", "hats"),
    ("drum", "drums"),
    ("perc", "percussion"),
    ("bass", "bass"),
    ("piano", "piano"),
    ("keys", "piano"),
    ("guitar", "guitar"),
    ("string", "strings"),
    ("violin", "strings"),
    ("cello", "strings"),
    ("choir", "choir"),
    ("vocal", "vocal"),
    ("voice", "vocal"),
    ("horn", "brass"),
    ("brass", "brass"),
    ("sax", "reed"),
    ("lead", "lead"),
    ("pad", "pad"),
    ("fx", "sound_fx"),
)


def rack_gm_family(program: int) -> str:
    value = max(0, min(127, int(program)))
    return _GM_FAMILIES[value // 8]


def _role_hint(track_name: str, channel: int, program: int) -> str:
    if int(channel) == 9:
        return "drums"
    lowered = str(track_name).lower()
    for token, role in _ROLE_KEYWORDS:
        if token in lowered:
            return role
    return rack_gm_family(program)


def _slot_id(semantic_sha256: str, track_index: int, channel: int, program: int) -> str:
    digest = midi_sha256_json(
        {
            "semantic_sha256": semantic_sha256,
            "track_index": int(track_index),
            "channel": int(channel),
            "program": int(program),
        }
    )
    return "slot_" + digest[:24]


def _max_polyphony(events: list[Mapping[str, Any]]) -> int:
    boundaries: list[tuple[int, int, str]] = []
    for event in events:
        boundaries.append((int(event["start_tick"]), 1, str(event["event_id"])))
        boundaries.append((int(event["end_tick"]), 0, str(event["event_id"])))
    active = 0
    maximum = 0
    for _tick, kind, _event_id in sorted(boundaries):
        active += 1 if kind else -1
        active = max(0, active)
        maximum = max(maximum, active)
    return maximum


def _channel_controls(ledger: Mapping[str, Any], track_index: int, channel: int) -> dict[str, Any]:
    controls: Counter[int] = Counter()
    pitchwheel_values: list[int] = []
    track = ledger["tracks"][track_index]
    for event in track["events"]:
        if event["is_meta"]:
            continue
        message = event["message"]
        if int(message.get("channel", -1)) != int(channel):
            continue
        typ = str(message.get("type") or "")
        if typ == "control_change":
            controls[int(message.get("control") or 0)] += 1
        elif typ == "pitchwheel":
            pitchwheel_values.append(int(message.get("pitch") or 0))
    return {
        "controllers": sorted(controls),
        "controller_event_counts": {str(key): controls[key] for key in sorted(controls)},
        "sustain_used": 64 in controls,
        "pitch_bend_used": bool(pitchwheel_values),
        "maximum_absolute_pitchwheel": max((abs(value) for value in pitchwheel_values), default=0),
    }


def rack_compile_demands(
    ledger: Mapping[str, Any],
    *,
    pitch_bend_range_semitones: float = 2.0,
) -> dict[str, Any]:
    """Describe exactly what a future crate substitute must be able to perform."""
    midi_validate_ledger(ledger)
    bend_range = float(pitch_bend_range_semitones)
    if bend_range <= 0:
        raise RackError("pitch_bend_range_semitones must be positive")
    compiled = midi_compile_note_spans(ledger)
    clock = MidiTempoClock(ledger)
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for span in compiled["note_spans"]:
        start_tick = int(span["start_tick"])
        end_tick = int(span["end_tick"])
        grouped[(int(span["track_index"]), int(span["channel"]), int(span["program"]))].append(
            {
                "event_id": str(span["event_id"]),
                "note": int(span["note"]),
                "velocity": int(span["velocity"]),
                "start_tick": start_tick,
                "end_tick": end_tick,
                "duration_ticks": end_tick - start_tick,
                "duration_beats": round((end_tick - start_tick) / int(ledger["ticks_per_beat"]), 9),
                "duration_seconds": round(clock.tick_to_seconds(end_tick) - clock.tick_to_seconds(start_tick), 9),
                "end_reason": str(span.get("end_reason") or ""),
            }
        )

    slots: list[dict[str, Any]] = []
    for (track_index, channel, program), events in sorted(grouped.items()):
        events.sort(key=lambda event: (event["start_tick"], event["note"], event["event_id"]))
        track_name = str(ledger["tracks"][track_index].get("name") or f"Track {track_index + 1}")
        role = _role_hint(track_name, channel, program)
        mode = "trigger" if channel == 9 else "pitched"
        by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_note[int(event["note"])].append(event)
        requirements = []
        for note in sorted(by_note):
            rows = by_note[note]
            requirements.append(
                {
                    "note": note,
                    "event_count": len(rows),
                    "minimum_velocity": min(row["velocity"] for row in rows),
                    "maximum_velocity": max(row["velocity"] for row in rows),
                    "maximum_duration_seconds": max(row["duration_seconds"] for row in rows),
                    "maximum_duration_beats": max(row["duration_beats"] for row in rows),
                }
            )
        controls = _channel_controls(ledger, track_index, channel)
        durations = sorted(float(event["duration_seconds"]) for event in events)
        slot_id = _slot_id(str(ledger["semantic_sha256"]), track_index, channel, program)
        slots.append(
            {
                "slot_id": slot_id,
                "track_index": track_index,
                "track_name": track_name,
                "channel": channel,
                "program": program,
                "gm_family": "drums" if channel == 9 else rack_gm_family(program),
                "role_hint": role,
                "mode": mode,
                "event_count": len(events),
                "minimum_note": min(event["note"] for event in events),
                "maximum_note": max(event["note"] for event in events),
                "minimum_velocity": min(event["velocity"] for event in events),
                "maximum_velocity": max(event["velocity"] for event in events),
                "maximum_polyphony": _max_polyphony(events),
                "minimum_duration_seconds": durations[0],
                "median_duration_seconds": durations[len(durations) // 2],
                "maximum_duration_seconds": durations[-1],
                "note_requirements": requirements,
                "controls": controls,
                "events": events,
                "search_query": {
                    "mode": mode,
                    "role": role,
                    "gm_family": "drums" if channel == 9 else rack_gm_family(program),
                    "note_range": [
                        min(event["note"] for event in events),
                        max(event["note"] for event in events),
                    ],
                    "maximum_polyphony": _max_polyphony(events),
                    "maximum_duration_seconds": durations[-1],
                    "requires_loop_or_sustain": mode == "pitched" and durations[-1] >= 0.5,
                    "requires_pitch_bend": controls["pitch_bend_used"],
                },
            }
        )

    demand = {
        "schema_version": DEMAND_SCHEMA_VERSION,
        "kind": DEMAND_KIND,
        "semantic_sha256": ledger["semantic_sha256"],
        "ticks_per_beat": int(ledger["ticks_per_beat"]),
        "pitch_bend_range_semitones": bend_range,
        "selected_event_count": len(compiled["note_spans"]),
        "slot_count": len(slots),
        "slots": slots,
        "compile_diagnostics": compiled["diagnostics"],
    }
    demand["demand_sha256"] = midi_sha256_json(demand)
    rack_validate_demands(demand)
    return demand


def rack_validate_demands(demand: Mapping[str, Any]) -> None:
    if int(demand.get("schema_version") or 0) != DEMAND_SCHEMA_VERSION:
        raise RackError(f"unsupported demand schema: {demand.get('schema_version')}")
    if str(demand.get("kind") or "") != DEMAND_KIND:
        raise RackError(f"unsupported demand kind: {demand.get('kind')}")
    if not str(demand.get("semantic_sha256") or ""):
        raise RackError("demand semantic_sha256 is required")
    slots = demand.get("slots")
    if not isinstance(slots, list):
        raise RackError("demand slots must be a list")
    seen: set[str] = set()
    total_events = 0
    for slot in slots:
        slot_id = str(slot.get("slot_id") or "")
        if not slot_id or slot_id in seen:
            raise RackError(f"duplicate or empty slot_id: {slot_id}")
        seen.add(slot_id)
        if str(slot.get("mode") or "") not in {"pitched", "trigger"}:
            raise RackError(f"slot {slot_id} has unsupported mode")
        events = slot.get("events")
        if not isinstance(events, list) or not events:
            raise RackError(f"slot {slot_id} has no events")
        total_events += len(events)
    if total_events != int(demand.get("selected_event_count") or 0):
        raise RackError("demand selected_event_count does not match slot events")
    expected = midi_sha256_json({key: value for key, value in demand.items() if key != "demand_sha256"})
    if str(demand.get("demand_sha256") or "") != expected:
        raise RackError("demand_sha256 does not match demand contents")
