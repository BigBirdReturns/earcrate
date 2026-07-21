from __future__ import annotations

import math
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.midi.model import MidiTempoClock, midi_duration_ticks, midi_sha256_json, midi_validate_ledger

DEFAULT_ROLE_ORDER = (
    "drums", "kick", "snare", "hats", "percussion", "bass", "piano", "organ",
    "guitar", "strings", "ensemble", "choir", "vocal", "brass", "reed", "pipe",
    "lead", "synth_lead", "pad", "synth_pad", "synth_fx", "sound_fx", "other",
)


class AnatomyError(ValueError):
    """Raised when arrangement anatomy cannot be compiled or verified."""


def _anatomy_round(value: float, digits: int = 9) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise AnatomyError("arrangement anatomy cannot contain non-finite values")
    return round(number, digits)


def midi_time_signature_map(ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the effective meter map with deterministic same-tick precedence."""
    midi_validate_ledger(ledger)
    candidates: list[tuple[int, int, int, int, int, int, int]] = []
    for track in ledger["tracks"]:
        track_index = int(track["track_index"])
        for event in track["events"]:
            message = event["message"]
            if not event["is_meta"] or str(message.get("type") or "") != "time_signature":
                continue
            numerator = int(message.get("numerator") or 0)
            denominator = int(message.get("denominator") or 0)
            clocks = int(message.get("clocks_per_click") or 24)
            notated = int(message.get("notated_32nd_notes_per_beat") or 8)
            if numerator <= 0 or denominator <= 0 or denominator & (denominator - 1):
                raise AnatomyError("time signatures require a positive numerator and power-of-two denominator")
            candidates.append(
                (
                    int(event["tick"]),
                    track_index,
                    int(event["order"]),
                    numerator,
                    denominator,
                    clocks,
                    notated,
                )
            )
    candidates.sort()
    effective: dict[int, tuple[int, int, int, int]] = {0: (4, 4, 24, 8)}
    for tick, _track, _order, numerator, denominator, clocks, notated in candidates:
        effective[tick] = (numerator, denominator, clocks, notated)
    return [
        {
            "tick": tick,
            "numerator": effective[tick][0],
            "denominator": effective[tick][1],
            "clocks_per_click": effective[tick][2],
            "notated_32nd_notes_per_beat": effective[tick][3],
        }
        for tick in sorted(effective)
    ]


def _anatomy_bar_grid(
    ledger: Mapping[str, Any],
    spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    clock = MidiTempoClock(ledger)
    signatures = midi_time_signature_map(ledger)
    end_tick = max(
        midi_duration_ticks(ledger),
        max((int(span["end_tick"]) for span in spans), default=0),
    )
    if end_tick <= 0:
        end_tick = int(ledger["ticks_per_beat"]) * 4
    ticks_per_quarter = int(ledger["ticks_per_beat"])
    signature_index = 0
    tick = 0
    bars = []
    while tick < end_tick:
        while signature_index + 1 < len(signatures) and int(signatures[signature_index + 1]["tick"]) <= tick:
            signature_index += 1
        signature = signatures[signature_index]
        numerator = int(signature["numerator"])
        denominator = int(signature["denominator"])
        nominal = ticks_per_quarter * numerator * 4.0 / denominator
        nominal_ticks = max(1, int(round(nominal)))
        next_change = (
            int(signatures[signature_index + 1]["tick"])
            if signature_index + 1 < len(signatures)
            else None
        )
        proposed_end = tick + nominal_ticks
        partial = False
        if next_change is not None and tick < next_change < proposed_end:
            bar_end = next_change
            partial = True
        else:
            bar_end = min(proposed_end, end_tick)
            partial = bar_end - tick != nominal_ticks
        if bar_end <= tick:
            raise AnatomyError("meter map produced a non-positive bar")
        bars.append(
            {
                "bar_index": len(bars),
                "bar_number": len(bars) + 1,
                "start_tick": tick,
                "end_tick": bar_end,
                "duration_ticks": bar_end - tick,
                "duration_beats": _anatomy_round((bar_end - tick) / ticks_per_quarter),
                "start_seconds": _anatomy_round(clock.tick_to_seconds(tick)),
                "end_seconds": _anatomy_round(clock.tick_to_seconds(bar_end)),
                "numerator": numerator,
                "denominator": denominator,
                "partial": partial,
            }
        )
        tick = bar_end
    return bars


def _anatomy_event_slot_maps(demand: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    slots = {}
    events = {}
    for slot in demand["slots"]:
        slot_id = str(slot["slot_id"])
        summary = {
            "slot_id": slot_id,
            "track_index": int(slot["track_index"]),
            "track_name": str(slot["track_name"]),
            "channel": int(slot["channel"]),
            "program": int(slot["program"]),
            "mode": str(slot["mode"]),
            "role": str(slot.get("role_hint") or slot.get("gm_family") or "other"),
            "gm_family": str(slot.get("gm_family") or "other"),
        }
        slots[slot_id] = summary
        for event in slot["events"]:
            event_id = str(event["event_id"])
            events[event_id] = {**summary, **deepcopy(dict(event))}
    return slots, events


def _anatomy_union_ticks(intervals: Sequence[tuple[int, int]]) -> int:
    merged = 0
    current_start = None
    current_end = None
    for start, end in sorted((int(start), int(end)) for start, end in intervals if int(end) > int(start)):
        if current_start is None:
            current_start, current_end = start, end
        elif start > int(current_end):
            merged += int(current_end) - int(current_start)
            current_start, current_end = start, end
        else:
            current_end = max(int(current_end), end)
    if current_start is not None:
        merged += int(current_end) - int(current_start)
    return merged


def _anatomy_max_polyphony(spans: Sequence[Mapping[str, Any]], start_tick: int, end_tick: int) -> int:
    boundaries = []
    for span in spans:
        start = max(start_tick, int(span["start_tick"]))
        end = min(end_tick, int(span["end_tick"]))
        if end <= start:
            continue
        boundaries.append((start, 1, str(span["event_id"])))
        boundaries.append((end, 0, str(span["event_id"])))
    active = 0
    maximum = 0
    for _tick, kind, _event_id in sorted(boundaries):
        active += 1 if kind else -1
        active = max(0, active)
        maximum = max(maximum, active)
    return maximum


def _anatomy_register_bucket(note: int) -> str:
    if note < 48:
        return "bass"
    if note < 60:
        return "low_mid"
    if note < 72:
        return "mid"
    if note < 84:
        return "upper_mid"
    return "high"


def _anatomy_bar_cells(
    ledger: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    spans: Sequence[Mapping[str, Any]],
    event_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cells = []
    onset_to_bar: dict[str, int] = {}
    previous_slots: set[str] = set()
    for bar in bars:
        start = int(bar["start_tick"])
        end = int(bar["end_tick"])
        active = [span for span in spans if int(span["start_tick"]) < end and int(span["end_tick"]) > start]
        onsets = [span for span in spans if start <= int(span["start_tick"]) < end]
        for span in onsets:
            event_id = str(span["event_id"])
            if event_id in onset_to_bar:
                raise AnatomyError(f"event mapped to two onset bars: {event_id}")
            onset_to_bar[event_id] = int(bar["bar_index"])
        active_slots = sorted({str(event_map[str(span["event_id"])]["slot_id"]) for span in active})
        active_slot_set = set(active_slots)
        active_roles = sorted({str(event_map[str(span["event_id"])]["role"]) for span in active})
        entering_slots = sorted(active_slot_set - previous_slots)
        exiting_slots = sorted(previous_slots - active_slot_set)
        role_onsets: Counter[str] = Counter()
        register: Counter[str] = Counter()
        notes = []
        velocities = []
        for span in onsets:
            meta = event_map[str(span["event_id"])]
            role_onsets[str(meta["role"])] += 1
            note = int(span["note"])
            notes.append(note)
            velocities.append(int(span["velocity"]))
            register[_anatomy_register_bucket(note)] += 1
        slot_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
        role_intervals: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
        for span in active:
            meta = event_map[str(span["event_id"])]
            slot_id = str(meta["slot_id"])
            role = str(meta["role"])
            interval = (max(start, int(span["start_tick"])), min(end, int(span["end_tick"])))
            slot_intervals[slot_id].append(interval)
            role_intervals[role][slot_id].append(interval)
        bar_ticks = max(1, end - start)
        slot_occupancy = {
            slot_id: _anatomy_round(_anatomy_union_ticks(intervals) / bar_ticks)
            for slot_id, intervals in sorted(slot_intervals.items())
        }
        role_layer_occupancy = {
            role: _anatomy_round(sum(_anatomy_union_ticks(intervals) for intervals in by_slot.values()) / bar_ticks)
            for role, by_slot in sorted(role_intervals.items())
        }
        mean_layers = sum(slot_occupancy.values())
        density = len(onsets) / max(1e-9, float(bar["duration_beats"]))
        mean_velocity = sum(velocities) / len(velocities) if velocities else 0.0
        maximum_polyphony = _anatomy_max_polyphony(active, start, end)
        energy = (
            0.35 * min(1.0, density / 8.0)
            + 0.30 * min(1.0, mean_layers / 6.0)
            + 0.20 * (mean_velocity / 127.0)
            + 0.15 * min(1.0, maximum_polyphony / 16.0)
        )
        cell = {
            **deepcopy(dict(bar)),
            "active_slot_ids": active_slots,
            "active_roles": active_roles,
            "entering_slot_ids": entering_slots,
            "exiting_slot_ids": exiting_slots,
            "onset_event_ids": [str(span["event_id"]) for span in sorted(onsets, key=lambda row: (int(row["start_tick"]), int(row["track_index"]), int(row["note"]), str(row["event_id"])))],
            "active_event_ids": [str(span["event_id"]) for span in sorted(active, key=lambda row: (int(row["start_tick"]), int(row["track_index"]), int(row["note"]), str(row["event_id"])))],
            "onset_count": len(onsets),
            "active_note_count": len(active),
            "active_slot_count": len(active_slots),
            "mean_active_layers": _anatomy_round(mean_layers),
            "maximum_polyphony": maximum_polyphony,
            "onsets_per_beat": _anatomy_round(density),
            "mean_velocity": _anatomy_round(mean_velocity),
            "minimum_onset_note": min(notes) if notes else None,
            "maximum_onset_note": max(notes) if notes else None,
            "mean_onset_note": _anatomy_round(sum(notes) / len(notes)) if notes else None,
            "register_onsets": {name: int(register.get(name, 0)) for name in ("bass", "low_mid", "mid", "upper_mid", "high")},
            "role_onsets": dict(sorted(role_onsets.items())),
            "slot_occupancy": slot_occupancy,
            "role_layer_occupancy": role_layer_occupancy,
            "energy": _anatomy_round(energy),
        }
        cell["state_sha256"] = midi_sha256_json({key: value for key, value in cell.items() if key != "state_sha256"})
        cells.append(cell)
        previous_slots = active_slot_set
    if cells:
        cells[-1]["exiting_slot_ids"] = sorted(set(cells[-1]["active_slot_ids"]))
        cells[-1]["state_sha256"] = midi_sha256_json({key: value for key, value in cells[-1].items() if key != "state_sha256"})
    return cells, onset_to_bar


def _anatomy_role_order(cells: Sequence[Mapping[str, Any]]) -> list[str]:
    present = {str(role) for cell in cells for role in cell.get("active_roles") or []}
    ordered = [role for role in DEFAULT_ROLE_ORDER if role in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _anatomy_feature_vectors(cells: Sequence[Mapping[str, Any]], roles: Sequence[str]) -> list[list[float]]:
    vectors = []
    for cell in cells:
        vector = [
            float(cell["energy"]),
            min(1.0, float(cell["mean_active_layers"]) / 6.0),
            min(1.0, float(cell["onsets_per_beat"]) / 8.0),
            min(1.0, float(cell["maximum_polyphony"]) / 16.0),
            (float(cell["mean_onset_note"]) / 127.0) if cell["mean_onset_note"] is not None else 0.0,
            min(1.0, len(cell["entering_slot_ids"]) / 4.0),
            min(1.0, len(cell["exiting_slot_ids"]) / 4.0),
        ]
        vector.extend(1.0 if role in set(cell["active_roles"]) else 0.0 for role in roles)
        vectors.append(vector)
    return vectors


def _anatomy_novelty(vectors: Sequence[Sequence[float]], cells: Sequence[Mapping[str, Any]]) -> list[float]:
    values = [0.0]
    for index in range(1, len(vectors)):
        left = vectors[index - 1]
        right = vectors[index]
        distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))
        event_change = 0.10 * (len(cells[index]["entering_slot_ids"]) + len(cells[index]["exiting_slot_ids"]))
        values.append(_anatomy_round(distance + event_change))
    return values
