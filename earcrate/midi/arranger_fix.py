from __future__ import annotations

import importlib
from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping

from earcrate.midi.anatomy import midi_arrangement_anatomy, midi_validate_arrangement_anatomy
from earcrate.midi.model import midi_sha256_json, midi_validate_ledger
from earcrate.rack.demand import rack_compile_demands
from earcrate.midi.arranger import (
    ArrangementError,
    PATTERN_BANK_KIND,
    PATTERN_BANK_SCHEMA_VERSION,
    _arranger_category,
    _arranger_source_controls,
    midi_validate_pattern_bank,
)


def midi_pattern_bank(
    ledger: Mapping[str, Any],
    anatomy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile complete source bars while retaining explicit receipts for exclusions."""
    midi_validate_ledger(ledger)
    measured = deepcopy(dict(anatomy)) if anatomy is not None else midi_arrangement_anatomy(ledger)
    midi_validate_arrangement_anatomy(measured)
    if str(measured["semantic_sha256"]) != str(ledger["semantic_sha256"]):
        raise ArrangementError("arrangement anatomy belongs to another MIDI performance")
    complete_bars = [bar for bar in measured["bars"] if not bool(bar.get("partial"))]
    excluded_bars = [bar for bar in measured["bars"] if bool(bar.get("partial"))]
    meters = {(int(bar["numerator"]), int(bar["denominator"])) for bar in complete_bars}
    if len(meters) != 1 or not complete_bars:
        raise ArrangementError("pattern arrangement requires at least one complete bar in one constant meter")
    if excluded_bars and any(int(bar["bar_index"]) < int(complete_bars[-1]["bar_index"]) for bar in excluded_bars):
        raise ArrangementError("pattern arrangement refuses partial bars inside the source form")

    demand = rack_compile_demands(ledger)
    slots = {str(slot["slot_id"]): deepcopy(dict(slot)) for slot in demand["slots"]}
    events = {
        str(event["event_id"]): {**deepcopy(dict(event)), "slot_id": str(slot["slot_id"])}
        for slot in demand["slots"]
        for event in slot["events"]
    }
    assignments_by_bar: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for assignment in measured["event_assignments"]:
        assignments_by_bar[int(assignment["bar_index"])].append(assignment)
    section_for_bar = {}
    for section in measured["sections"]:
        for bar_index in range(int(section["start_bar_index"]), int(section["end_bar_index"])):
            section_for_bar[bar_index] = str(section["label"])

    patterns = []
    for bar in complete_bars:
        bar_index = int(bar["bar_index"])
        bar_ticks = int(bar["duration_ticks"])
        by_slot: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for assignment in assignments_by_bar.get(bar_index, []):
            source = events[str(assignment["event_id"])]
            slot_id = str(source["slot_id"])
            by_slot[slot_id].append(
                {
                    "source_event_id": str(source["event_id"]),
                    "offset_ratio": round((int(source["start_tick"]) - int(bar["start_tick"])) / max(1, bar_ticks), 12),
                    "duration_ratio": round(int(source["duration_ticks"]) / max(1, bar_ticks), 12),
                    "note": int(source["note"]),
                    "velocity": int(source["velocity"]),
                }
            )
        slot_patterns = []
        for slot_id in sorted(by_slot):
            slot = slots[slot_id]
            pattern_events = sorted(
                by_slot[slot_id],
                key=lambda row: (float(row["offset_ratio"]), int(row["note"]), str(row["source_event_id"])),
            )
            slot_patterns.append(
                {
                    "slot_id": slot_id,
                    "track_name": str(slot["track_name"]),
                    "channel": int(slot["channel"]),
                    "program": int(slot["program"]),
                    "mode": str(slot["mode"]),
                    "role": str(slot["role_hint"]),
                    "category": _arranger_category(str(slot["role_hint"])),
                    "events": pattern_events,
                    "controls": _arranger_source_controls(ledger, slot, bar),
                }
            )
        if not slot_patterns:
            continue
        payload = {
            "source_bar_index": bar_index,
            "source_section_label": section_for_bar.get(bar_index, "groove"),
            "source_energy": float(bar["energy"]),
            "source_layers": float(bar["mean_active_layers"]),
            "source_onsets_per_beat": float(bar["onsets_per_beat"]),
            "roles": sorted({str(slot["role"]) for slot in slot_patterns}),
            "categories": sorted({str(slot["category"]) for slot in slot_patterns}),
            "slots": slot_patterns,
        }
        payload["pattern_id"] = "pattern_" + midi_sha256_json(payload)[:24]
        patterns.append(payload)
    if not patterns:
        raise ArrangementError("source performance contains no playable complete-bar patterns")
    patterns.sort(key=lambda row: (int(row["source_bar_index"]), str(row["pattern_id"])))
    excluded = [
        {
            "bar_index": int(bar["bar_index"]),
            "start_tick": int(bar["start_tick"]),
            "end_tick": int(bar["end_tick"]),
            "duration_ticks": int(bar["duration_ticks"]),
            "event_ids": [str(value) for value in bar.get("onset_event_ids") or []],
            "reason": "trailing_partial_bar_not_retimed_as_a_full_pattern",
        }
        for bar in excluded_bars
    ]
    bank = {
        "schema_version": PATTERN_BANK_SCHEMA_VERSION,
        "kind": PATTERN_BANK_KIND,
        "source_semantic_sha256": str(ledger["semantic_sha256"]),
        "anatomy_sha256": str(measured["anatomy_sha256"]),
        "structural_sha256": str(measured["structural_sha256"]),
        "ticks_per_beat": int(ledger["ticks_per_beat"]),
        "meter": {"numerator": next(iter(meters))[0], "denominator": next(iter(meters))[1]},
        "slot_count": len(slots),
        "pattern_count": len(patterns),
        "excluded_partial_bar_count": len(excluded),
        "excluded_partial_bars": excluded,
        "slots": [
            {
                "slot_id": slot_id,
                "track_name": str(slot["track_name"]),
                "channel": int(slot["channel"]),
                "program": int(slot["program"]),
                "mode": str(slot["mode"]),
                "role": str(slot["role_hint"]),
                "category": _arranger_category(str(slot["role_hint"])),
                "controls": deepcopy(dict(slot.get("controls") or {})),
            }
            for slot_id, slot in sorted(slots.items())
        ],
        "patterns": patterns,
    }
    bank["pattern_bank_sha256"] = midi_sha256_json(bank)
    midi_validate_pattern_bank(bank)
    return bank


try:
    _arranger_module = importlib.import_module("earcrate.midi.arranger")
except Exception:
    _arranger_module = None
if _arranger_module is not None:
    _arranger_module.midi_pattern_bank = midi_pattern_bank
