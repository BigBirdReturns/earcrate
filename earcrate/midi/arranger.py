from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.anatomy import midi_arrangement_anatomy, midi_validate_arrangement_anatomy
from earcrate.midi.codec import midi_write
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
from earcrate.rack.demand import rack_compile_demands

ARRANGER_SCHEMA_VERSION = 1
ARRANGER_KIND = "earcrate_midi_pattern_arrangement"
PATTERN_BANK_SCHEMA_VERSION = 1
PATTERN_BANK_KIND = "earcrate_midi_pattern_bank"

_FLOOR = {"drums", "kick", "snare", "hats", "percussion", "percussive", "chromatic_percussion"}
_BASS = {"bass"}
_HARMONY = {"piano", "organ", "guitar", "strings", "ensemble", "brass", "reed", "pipe", "pad", "synth_pad"}
_FOREGROUND = {"vocal", "choir", "lead", "synth_lead"}
_FX = {"synth_fx", "sound_fx", "other", "ethnic"}


class ArrangementError(ValueError):
    """Raised when a deterministic MIDI arrangement cannot be compiled."""


def _arranger_category(role: str) -> str:
    value = str(role)
    if value in _FLOOR:
        return "floor"
    if value in _BASS:
        return "bass"
    if value in _HARMONY:
        return "harmony"
    if value in _FOREGROUND:
        return "foreground"
    if value in _FX:
        return "fx"
    return "other"


def _arranger_jitter(seed: int, *parts: Any) -> float:
    digest = midi_sha256_json({"seed": int(seed), "parts": [str(part) for part in parts]})
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _arranger_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite arrangement plan: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return {"path": str(destination), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _arranger_control_key(message: Mapping[str, Any]) -> tuple[Any, ...] | None:
    typ = str(message.get("type") or "")
    if typ == "control_change":
        return (typ, int(message.get("control") or 0))
    if typ == "pitchwheel":
        return (typ,)
    return None


def _arranger_source_controls(
    ledger: Mapping[str, Any],
    slot: Mapping[str, Any],
    bar: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Capture controller state at the bar edge plus all in-bar changes."""
    track_index = int(slot["track_index"])
    channel = int(slot["channel"])
    start_tick = int(bar["start_tick"])
    end_tick = int(bar["end_tick"])
    duration = max(1, end_tick - start_tick)
    wanted_controls = {int(value) for value in (slot.get("controls") or {}).get("controllers") or []}
    want_pitch = bool((slot.get("controls") or {}).get("pitch_bend_used"))
    state: dict[tuple[Any, ...], tuple[int, int, Mapping[str, Any]]] = {}
    local: list[tuple[int, int, Mapping[str, Any]]] = []
    for event in ledger["tracks"][track_index]["events"]:
        if bool(event["is_meta"]):
            continue
        message = event["message"]
        if int(message.get("channel", -1)) != channel:
            continue
        key = _arranger_control_key(message)
        if key is None:
            continue
        if key[0] == "control_change" and int(key[1]) not in wanted_controls:
            continue
        if key[0] == "pitchwheel" and not want_pitch:
            continue
        tick = int(event["tick"])
        row = (tick, int(event["order"]), message)
        if tick < start_tick:
            state[key] = row
        elif start_tick <= tick < end_tick:
            local.append(row)
    local_at_start = {_arranger_control_key(message) for tick, _order, message in local if tick == start_tick}
    out = []
    for key in sorted(state, key=lambda value: tuple(str(item) for item in value)):
        if key in local_at_start:
            continue
        source_tick, source_order, message = state[key]
        source_id = "source_control_" + midi_sha256_json(
            {
                "semantic_sha256": ledger["semantic_sha256"],
                "track_index": track_index,
                "order": source_order,
                "tick": source_tick,
                "message": message,
                "snapshot_at_tick": start_tick,
            }
        )[:24]
        out.append(
            {
                "source_control_id": source_id,
                "offset_ratio": 0.0,
                "snapshot": True,
                "message": deepcopy(dict(message)),
            }
        )
    for tick, source_order, message in sorted(local, key=lambda row: (row[0], row[1], midi_sha256_json(row[2]))):
        source_id = "source_control_" + midi_sha256_json(
            {
                "semantic_sha256": ledger["semantic_sha256"],
                "track_index": track_index,
                "order": source_order,
                "tick": tick,
                "message": message,
            }
        )[:24]
        out.append(
            {
                "source_control_id": source_id,
                "offset_ratio": round((tick - start_tick) / duration, 12),
                "snapshot": False,
                "message": deepcopy(dict(message)),
            }
        )
    return out


def midi_pattern_bank(
    ledger: Mapping[str, Any],
    anatomy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one-bar, harmony-coherent patterns from an exact MIDI performance."""
    midi_validate_ledger(ledger)
    measured = deepcopy(dict(anatomy)) if anatomy is not None else midi_arrangement_anatomy(ledger)
    midi_validate_arrangement_anatomy(measured)
    if str(measured["semantic_sha256"]) != str(ledger["semantic_sha256"]):
        raise ArrangementError("arrangement anatomy belongs to another MIDI performance")
    meters = {(int(bar["numerator"]), int(bar["denominator"])) for bar in measured["bars"] if not bool(bar.get("partial"))}
    if len(meters) != 1 or any(bool(bar.get("partial")) for bar in measured["bars"]):
        raise ArrangementError("pattern arrangement currently requires one constant meter and no partial bars")
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
    for bar in measured["bars"]:
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
        raise ArrangementError("source performance contains no playable bar patterns")
    patterns.sort(key=lambda row: (int(row["source_bar_index"]), str(row["pattern_id"])))
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


def midi_validate_pattern_bank(bank: Mapping[str, Any]) -> None:
    if int(bank.get("schema_version") or 0) != PATTERN_BANK_SCHEMA_VERSION:
        raise ArrangementError(f"unsupported pattern bank schema: {bank.get('schema_version')}")
    if str(bank.get("kind") or "") != PATTERN_BANK_KIND:
        raise ArrangementError(f"unsupported pattern bank kind: {bank.get('kind')}")
    patterns = bank.get("patterns")
    slots = bank.get("slots")
    if not isinstance(patterns, list) or not patterns:
        raise ArrangementError("pattern bank requires playable patterns")
    if not isinstance(slots, list) or not slots:
        raise ArrangementError("pattern bank requires slots")
    slot_ids = [str(row.get("slot_id") or "") for row in slots]
    if not all(slot_ids) or len(slot_ids) != len(set(slot_ids)):
        raise ArrangementError("pattern bank slots must be unique and nonempty")
    pattern_ids = [str(row.get("pattern_id") or "") for row in patterns]
    if not all(pattern_ids) or len(pattern_ids) != len(set(pattern_ids)):
        raise ArrangementError("pattern bank patterns must be unique and nonempty")
    known_slots = set(slot_ids)
    for pattern in patterns:
        rows = pattern.get("slots")
        if not isinstance(rows, list) or not rows:
            raise ArrangementError(f"pattern {pattern.get('pattern_id')} has no slots")
        for row in rows:
            if str(row.get("slot_id") or "") not in known_slots:
                raise ArrangementError(f"pattern {pattern.get('pattern_id')} references an unknown slot")
            events = row.get("events")
            if not isinstance(events, list) or not events:
                raise ArrangementError(f"pattern {pattern.get('pattern_id')} contains an empty slot pattern")
            for event in events:
                if not 0.0 <= float(event.get("offset_ratio", -1.0)) <= 1.0:
                    raise ArrangementError("pattern event offset_ratio must be in [0,1]")
                if float(event.get("duration_ratio", 0.0)) <= 0.0:
                    raise ArrangementError("pattern event duration_ratio must be positive")
            controls = row.get("controls") or []
            if not isinstance(controls, list):
                raise ArrangementError("pattern slot controls must be a list")
            control_ids = [str(control.get("source_control_id") or "") for control in controls]
            if not all(control_ids) or len(control_ids) != len(set(control_ids)):
                raise ArrangementError("pattern control observations must be unique and nonempty")
            for control in controls:
                if not 0.0 <= float(control.get("offset_ratio", -1.0)) <= 1.0:
                    raise ArrangementError("pattern control offset_ratio must be in [0,1]")
                if _arranger_control_key(control.get("message") or {}) is None:
                    raise ArrangementError("pattern control contains an unsupported MIDI message")
    expected = midi_sha256_json({key: value for key, value in bank.items() if key != "pattern_bank_sha256"})
    if str(bank.get("pattern_bank_sha256") or "") != expected:
        raise ArrangementError("pattern_bank_sha256 does not match pattern contents")


def _arranger_form(target_bars: int, variant: str) -> list[dict[str, Any]]:
    if target_bars < 16:
        raise ArrangementError("target_bars must be at least 16")
    if variant not in {"classic", "double_drop", "long_build"}:
        raise ArrangementError(f"unsupported form variant: {variant}")
    unit = 4 if target_bars >= 28 else 2
    labels = ["intro", "groove", "build", "drop", "breakdown", "drop", "outro"]
    bars = [unit] * len(labels)
    remaining = target_bars - sum(bars)
    allocation = [3, 5, 2, 3, 5, 1] if variant == "double_drop" else ([2, 3, 3, 5, 1] if variant == "long_build" else [3, 5, 3, 5, 1])
    cursor = 0
    while remaining > 0:
        index = allocation[cursor % len(allocation)]
        quantum = min(unit, remaining)
        bars[index] += quantum
        remaining -= quantum
        cursor += 1
    out = []
    start = 0
    for section_index, (label, length) in enumerate(zip(labels, bars)):
        section = {
            "section_index": section_index,
            "label": label,
            "start_bar_index": start,
            "end_bar_index": start + length,
            "bar_count": length,
        }
        section["section_id"] = "generated_section_" + midi_sha256_json(section)[:20]
        out.append(section)
        start += length
    return out


def _arranger_energy(label: str, position: int, length: int) -> float:
    progress = position / max(1, length - 1)
    if label == "intro":
        return 0.18 + 0.16 * progress
    if label == "groove":
        return 0.45 + 0.05 * (1.0 if position % 4 == 3 else 0.0)
    if label == "build":
        return 0.48 + 0.32 * progress
    if label == "drop":
        return 0.88 + 0.08 * (1.0 if position == 0 else 0.0)
    if label == "breakdown":
        return 0.30 + 0.08 * math.sin(progress * math.pi)
    if label == "outro":
        return 0.30 - 0.18 * progress
    return 0.50


def _arranger_policy(label: str, position: int, length: int) -> tuple[list[str], int]:
    if label == "intro":
        return ["harmony", "foreground", "bass", "fx"], 1 if position < max(1, length // 2) else 2
    if label == "groove":
        return ["floor", "bass", "harmony", "foreground"], 3
    if label == "build":
        layers = 2 + int(round(2 * position / max(1, length - 1)))
        return ["floor", "bass", "harmony", "foreground", "fx"], layers
    if label == "drop":
        return ["floor", "bass", "harmony", "foreground", "fx"], 5
    if label == "breakdown":
        return ["harmony", "foreground", "bass", "fx"], 2 if position < length - 1 else 3
    if label == "outro":
        return ["harmony", "bass", "foreground", "fx"], 2 if position < max(1, length // 2) else 1
    return ["floor", "bass", "harmony", "foreground", "fx"], 3


def _arranger_select_slots(pattern: Mapping[str, Any], categories: Sequence[str], maximum_layers: int) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in pattern["slots"]:
        by_category[str(slot["category"])].append(deepcopy(dict(slot)))
    selected = []
    for category in categories:
        rows = sorted(by_category.get(category, []), key=lambda row: (str(row["role"]), str(row["slot_id"])))
        if rows and len(selected) < maximum_layers:
            selected.append(rows[0])
    if len(selected) < maximum_layers:
        already = {str(row["slot_id"]) for row in selected}
        remaining = sorted(
            [deepcopy(dict(row)) for row in pattern["slots"] if str(row["slot_id"]) not in already],
            key=lambda row: (str(row["category"]), str(row["role"]), str(row["slot_id"])),
        )
        selected.extend(remaining[: maximum_layers - len(selected)])
    return selected


def _arranger_pattern_candidate(
    pattern: Mapping[str, Any],
    *,
    label: str,
    target_energy: float,
    categories: Sequence[str],
    maximum_layers: int,
    previous_pattern_id: str | None,
    uses: Mapping[str, int],
    seed: int,
    bar_index: int,
) -> dict[str, Any]:
    selected = _arranger_select_slots(pattern, categories, maximum_layers)
    if not selected:
        return {"compatible": False, "pattern_id": pattern["pattern_id"], "score": None, "selected_slots": []}
    selected_categories = {str(row["category"]) for row in selected}
    desired = set(categories[:maximum_layers])
    role_coverage = len(selected_categories & desired) / max(1, len(desired))
    energy_fit = max(0.0, 1.0 - abs(float(pattern["source_energy"]) - float(target_energy)))
    layer_fit = max(0.0, 1.0 - abs(len(selected) - maximum_layers) / max(1, maximum_layers))
    label_fit = 1.0 if str(pattern["source_section_label"]) == label else 0.55
    continuity = 1.0 if previous_pattern_id == str(pattern["pattern_id"]) else 0.0
    reuse_penalty = min(1.0, int(uses.get(str(pattern["pattern_id"]), 0)) / 8.0)
    jitter = _arranger_jitter(seed, bar_index, pattern["pattern_id"])
    score = (
        0.34 * role_coverage
        + 0.25 * energy_fit
        + 0.15 * layer_fit
        + 0.10 * label_fit
        + 0.07 * continuity
        + 0.04 * jitter
        - 0.05 * reuse_penalty
    )
    return {
        "compatible": True,
        "pattern_id": str(pattern["pattern_id"]),
        "source_bar_index": int(pattern["source_bar_index"]),
        "source_section_label": str(pattern["source_section_label"]),
        "score": round(score, 9),
        "selected_slots": selected,
        "score_terms": {
            "role_coverage": round(role_coverage, 9),
            "energy_fit": round(energy_fit, 9),
            "layer_fit": round(layer_fit, 9),
            "label_fit": round(label_fit, 9),
            "continuity": round(continuity, 9),
            "reuse_penalty": round(reuse_penalty, 9),
            "seed_jitter": round(jitter, 9),
        },
    }


def _arranger_allocate_channels(
    used_slots: Sequence[str],
    slot_specs: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    """Allocate portable MIDI channels without cross-track program conflicts."""
    pitched = [slot_id for slot_id in used_slots if str(slot_specs[slot_id].get("mode") or "") != "trigger"]
    channels = [value for value in range(16) if value != 9]
    if len(pitched) > len(channels):
        raise ArrangementError(
            f"generated performance needs {len(pitched)} pitched MIDI channels; Standard MIDI exposes {len(channels)} after reserving channel 10 for drums"
        )
    ordered = sorted(
        pitched,
        key=lambda slot_id: (
            str(slot_specs[slot_id].get("category") or ""),
            str(slot_specs[slot_id].get("role") or ""),
            str(slot_specs[slot_id].get("track_name") or ""),
            int(slot_specs[slot_id].get("program") or 0),
            slot_id,
        ),
    )
    mapping = {slot_id: channels[index] for index, slot_id in enumerate(ordered)}
    for slot_id in used_slots:
        if str(slot_specs[slot_id].get("mode") or "") == "trigger":
            mapping[slot_id] = 9
    return mapping


def _arranger_message_priority(message: Mapping[str, Any], is_meta: bool) -> int:
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


def _arranger_track_events(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        [deepcopy(dict(row)) for row in raw],
        key=lambda row: (
            int(row["tick"]),
            _arranger_message_priority(row["message"], bool(row["is_meta"])),
            midi_sha256_json(row["message"]),
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
        for marker in ("_generated_note_id", "_generated_note_off_id", "_generated_control_id"):
            if row.get(marker):
                event[marker] = str(row[marker])
        events.append(event)
    return events


def midi_generate_pattern_arrangement(
    source_ledger: Mapping[str, Any],
    *,
    anatomy: Mapping[str, Any] | None = None,
    target_bars: int = 32,
    seed: int = 1,
    form_variant: str = "classic",
    target_bpm: float = 0.0,
    density: float = 1.0,
    maximum_layers: int = 6,
) -> dict[str, Any]:
    """Rearrange harmony-coherent source bars with explicit role and layer decisions."""
    midi_validate_ledger(source_ledger)
    if density <= 0.0 or density > 2.0:
        raise ArrangementError("density must be in (0,2]")
    if maximum_layers <= 0 or maximum_layers > 16:
        raise ArrangementError("maximum_layers must be in [1,16]")
    if target_bpm < 0.0:
        raise ArrangementError("target_bpm cannot be negative")
    measured = deepcopy(dict(anatomy)) if anatomy is not None else midi_arrangement_anatomy(source_ledger)
    midi_validate_arrangement_anatomy(measured)
    bank = midi_pattern_bank(source_ledger, measured)
    form = _arranger_form(int(target_bars), str(form_variant))
    ppq = int(source_ledger["ticks_per_beat"])
    numerator = int(bank["meter"]["numerator"])
    denominator = int(bank["meter"]["denominator"])
    bar_ticks = max(1, int(round(ppq * numerator * 4.0 / denominator)))
    total_ticks = int(target_bars) * bar_ticks
    source_tempo = int(midi_tempo_map(source_ledger)[0]["tempo_us_per_beat"])
    tempo = int(round(60_000_000.0 / float(target_bpm))) if target_bpm > 0.0 else source_tempo
    slot_specs = {str(row["slot_id"]): deepcopy(dict(row)) for row in bank["slots"]}
    pattern_by_id = {str(row["pattern_id"]): row for row in bank["patterns"]}
    uses: Counter[str] = Counter()
    previous_pattern_id = None
    bar_decisions = []
    generated_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    provenance = []
    control_provenance = []

    section_for_bar = {}
    for section in form:
        for bar_index in range(int(section["start_bar_index"]), int(section["end_bar_index"])):
            section_for_bar[bar_index] = section

    for bar_index in range(int(target_bars)):
        section = section_for_bar[bar_index]
        position = bar_index - int(section["start_bar_index"])
        length = int(section["bar_count"])
        label = str(section["label"])
        target_energy = _arranger_energy(label, position, length)
        categories, policy_layers = _arranger_policy(label, position, length)
        target_layers = max(1, min(int(maximum_layers), int(round(policy_layers * float(density)))))
        candidates = [
            _arranger_pattern_candidate(
                pattern,
                label=label,
                target_energy=target_energy,
                categories=categories,
                maximum_layers=target_layers,
                previous_pattern_id=previous_pattern_id,
                uses=uses,
                seed=int(seed),
                bar_index=bar_index,
            )
            for pattern in bank["patterns"]
        ]
        compatible = [row for row in candidates if row["compatible"]]
        compatible.sort(key=lambda row: (-float(row["score"]), int(row["source_bar_index"]), str(row["pattern_id"])))
        if not compatible:
            raise ArrangementError(f"no source pattern can satisfy target bar {bar_index + 1}")
        chosen = compatible[0]
        pattern = pattern_by_id[str(chosen["pattern_id"])]
        uses[str(chosen["pattern_id"])] += 1
        previous_pattern_id = str(chosen["pattern_id"])
        target_start = bar_index * bar_ticks
        generated_ids = []
        bar_control_ids = []
        for slot_pattern in chosen["selected_slots"]:
            slot_id = str(slot_pattern["slot_id"])
            source_slot = next(row for row in pattern["slots"] if str(row["slot_id"]) == slot_id)
            source_energy = max(0.05, float(pattern["source_energy"]))
            velocity_factor = max(0.55, min(1.25, 0.82 + 0.45 * (target_energy - source_energy)))
            for source_control in source_slot.get("controls") or []:
                control_tick = target_start + int(round(float(source_control["offset_ratio"]) * bar_ticks))
                if control_tick >= total_ticks:
                    continue
                generated_control_id = "generated_control_" + midi_sha256_json(
                    {
                        "source_control_id": source_control["source_control_id"],
                        "target_bar_index": bar_index,
                        "slot_id": slot_id,
                        "seed": int(seed),
                    }
                )[:24]
                message = deepcopy(dict(source_control["message"]))
                message["channel"] = int(slot_specs[slot_id]["channel"])
                generated_events[slot_id].append(
                    {
                        "tick": control_tick,
                        "is_meta": False,
                        "message": message,
                        "_generated_control_id": generated_control_id,
                    }
                )
                bar_control_ids.append(generated_control_id)
                control_provenance.append(
                    {
                        "generated_control_id": generated_control_id,
                        "source_control_id": str(source_control["source_control_id"]),
                        "source_pattern_id": str(pattern["pattern_id"]),
                        "source_bar_index": int(pattern["source_bar_index"]),
                        "target_bar_index": bar_index,
                        "section_index": int(section["section_index"]),
                        "slot_id": slot_id,
                        "role": str(slot_specs[slot_id]["role"]),
                        "source_channel": int(slot_specs[slot_id]["channel"]),
                        "tick": control_tick,
                        "snapshot": bool(source_control.get("snapshot")),
                        "message": deepcopy(message),
                    }
                )
            for source_event in source_slot["events"]:
                start_tick = target_start + int(round(float(source_event["offset_ratio"]) * bar_ticks))
                duration = max(1, int(round(float(source_event["duration_ratio"]) * bar_ticks)))
                end_tick = min(total_ticks, start_tick + duration)
                if end_tick <= start_tick or start_tick >= total_ticks:
                    continue
                velocity = max(1, min(127, int(round(int(source_event["velocity"]) * velocity_factor))))
                generated_id = "generated_note_" + midi_sha256_json(
                    {
                        "source_event_id": source_event["source_event_id"],
                        "target_bar_index": bar_index,
                        "slot_id": slot_id,
                        "seed": int(seed),
                    }
                )[:24]
                generated_ids.append(generated_id)
                generated_events[slot_id].extend(
                    [
                        {
                            "tick": start_tick,
                            "is_meta": False,
                            "_generated_note_id": generated_id,
                            "message": {
                                "type": "note_on",
                                "channel": int(slot_specs[slot_id]["channel"]),
                                "note": int(source_event["note"]),
                                "velocity": velocity,
                            },
                        },
                        {
                            "tick": end_tick,
                            "is_meta": False,
                            "_generated_note_off_id": generated_id,
                            "message": {
                                "type": "note_off",
                                "channel": int(slot_specs[slot_id]["channel"]),
                                "note": int(source_event["note"]),
                                "velocity": 0,
                            },
                        },
                    ]
                )
                provenance.append(
                    {
                        "generated_note_id": generated_id,
                        "source_event_id": str(source_event["source_event_id"]),
                        "source_pattern_id": str(pattern["pattern_id"]),
                        "source_bar_index": int(pattern["source_bar_index"]),
                        "target_bar_index": bar_index,
                        "section_index": int(section["section_index"]),
                        "slot_id": slot_id,
                        "role": str(slot_specs[slot_id]["role"]),
                        "source_channel": int(slot_specs[slot_id]["channel"]),
                        "start_tick": start_tick,
                        "message_end_tick": end_tick,
                        "note": int(source_event["note"]),
                        "velocity": velocity,
                    }
                )
        bar_decisions.append(
            {
                "bar_index": bar_index,
                "section_index": int(section["section_index"]),
                "section_label": label,
                "target_energy": round(target_energy, 9),
                "maximum_layers": target_layers,
                "policy_layers": policy_layers,
                "density": float(density),
                "requested_categories": list(categories),
                "selected_pattern_id": str(chosen["pattern_id"]),
                "source_bar_index": int(chosen["source_bar_index"]),
                "selected_slot_ids": [str(row["slot_id"]) for row in chosen["selected_slots"]],
                "selected_roles": [str(row["role"]) for row in chosen["selected_slots"]],
                "generated_note_ids": generated_ids,
                "generated_control_ids": bar_control_ids,
                "score": float(chosen["score"]),
                "score_terms": deepcopy(chosen["score_terms"]),
                "alternatives": [
                    {
                        "pattern_id": str(row["pattern_id"]),
                        "source_bar_index": int(row["source_bar_index"]),
                        "score": float(row["score"]),
                        "selected_roles": [str(slot["role"]) for slot in row["selected_slots"]],
                    }
                    for row in compatible[:5]
                ],
            }
        )

    used_slots = sorted(
        generated_events,
        key=lambda slot_id: (
            str(slot_specs[slot_id]["category"]),
            str(slot_specs[slot_id]["role"]),
            str(slot_specs[slot_id]["track_name"]),
            slot_id,
        ),
    )
    channel_map = _arranger_allocate_channels(used_slots, slot_specs)
    for slot_id in used_slots:
        for event in generated_events[slot_id]:
            event["message"]["channel"] = int(channel_map[slot_id])
    for row in provenance:
        row["output_channel"] = int(channel_map[str(row["slot_id"])])
    for row in control_provenance:
        output_channel = int(channel_map[str(row["slot_id"])])
        row["output_channel"] = output_channel
        row["message"]["channel"] = output_channel

    conductor_raw = [
        {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": "EarCrate Conductor"}},
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
    conductor_events = _arranger_track_events(conductor_raw)
    tracks = [{"track_index": 0, "name": "EarCrate Conductor", "events": conductor_events}]
    note_locators: dict[str, dict[str, Any]] = {}
    note_off_locators: dict[str, dict[str, Any]] = {}
    control_locators: dict[str, dict[str, Any]] = {}
    for slot_id in used_slots:
        spec = slot_specs[slot_id]
        raw = [
            {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": str(spec["track_name"])}},
            {
                "tick": 0,
                "is_meta": False,
                "message": {"type": "program_change", "channel": int(channel_map[slot_id]), "program": int(spec["program"])},
            },
            *generated_events[slot_id],
            {"tick": total_ticks, "is_meta": True, "message": {"type": "end_of_track"}},
        ]
        track_index = len(tracks)
        track_events = _arranger_track_events(raw)
        for event in track_events:
            note_id = event.pop("_generated_note_id", None)
            note_off_id = event.pop("_generated_note_off_id", None)
            control_id = event.pop("_generated_control_id", None)
            if note_id:
                note_locators[str(note_id)] = {
                    "track_index": track_index,
                    "order": int(event["order"]),
                    "tick": int(event["tick"]),
                    "message": deepcopy(dict(event["message"])),
                }
            if note_off_id:
                note_off_locators[str(note_off_id)] = {
                    "track_index": track_index,
                    "order": int(event["order"]),
                    "tick": int(event["tick"]),
                    "message": deepcopy(dict(event["message"])),
                }
            if control_id:
                control_locators[str(control_id)] = {
                    "track_index": track_index,
                    "order": int(event["order"]),
                    "tick": int(event["tick"]),
                    "message": deepcopy(dict(event["message"])),
                }
        tracks.append({"track_index": track_index, "name": str(spec["track_name"]), "events": track_events})
    output = midi_seal_ledger(
        {
            "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
            "kind": MIDI_LEDGER_KIND,
            "midi_type": 1,
            "ticks_per_beat": ppq,
            "tracks": tracks,
            "generated_from": {
                "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
                "anatomy_sha256": str(measured["anatomy_sha256"]),
                "pattern_bank_sha256": str(bank["pattern_bank_sha256"]),
            },
        }
    )
    for row in provenance:
        locator = note_locators.get(str(row["generated_note_id"]))
        if locator is None:
            raise ArrangementError(f"generated note has no output event locator: {row['generated_note_id']}")
        message = locator["message"]
        digest = hashlib.sha256(
            f"{output['semantic_sha256']}:{locator['track_index']}:{locator['order']}:{locator['tick']}:{message['channel']}:{message['note']}".encode("utf-8")
        ).hexdigest()[:24]
        row["output_event_id"] = "mnote_" + digest
        row["output_track_index"] = int(locator["track_index"])
        row["output_event_order"] = int(locator["order"])
        note_off = note_off_locators.get(str(row["generated_note_id"]))
        if note_off is None:
            raise ArrangementError(f"generated note has no output note-off locator: {row['generated_note_id']}")
        row["output_note_off_event_id"] = "mnoteoff_" + midi_sha256_json(
            {
                "semantic_sha256": output["semantic_sha256"],
                "track_index": note_off["track_index"],
                "order": note_off["order"],
                "tick": note_off["tick"],
                "message": note_off["message"],
            }
        )[:24]
        row["output_note_off_order"] = int(note_off["order"])
        if int(note_off["tick"]) != int(row["message_end_tick"]):
            raise ArrangementError(f"generated note-off tick disagrees with provenance: {row['generated_note_id']}")
    for row in control_provenance:
        locator = control_locators.get(str(row["generated_control_id"]))
        if locator is None:
            raise ArrangementError(f"generated control has no output event locator: {row['generated_control_id']}")
        message = locator["message"]
        row["output_event_id"] = "mcontrol_" + midi_sha256_json(
            {
                "semantic_sha256": output["semantic_sha256"],
                "track_index": locator["track_index"],
                "order": locator["order"],
                "tick": locator["tick"],
                "message": message,
            }
        )[:24]
        row["output_track_index"] = int(locator["track_index"])
        row["output_event_order"] = int(locator["order"])
    compiled_output = midi_compile_note_spans(output)
    compiled_ids = {str(span["event_id"]) for span in compiled_output["note_spans"]}
    provenance_ids = {str(row["output_event_id"]) for row in provenance}
    if compiled_ids != provenance_ids:
        raise ArrangementError("generated note provenance does not match the exact output note-span ledger")
    spans_by_id = {str(span["event_id"]): span for span in compiled_output["note_spans"]}
    for row in provenance:
        span = spans_by_id[str(row["output_event_id"])]
        row["sounding_end_tick"] = int(span["end_tick"])
        row["end_reason"] = str(span.get("end_reason") or "")
        if (
            int(span["track_index"]) != int(row["output_track_index"])
            or int(span["channel"]) != int(row["output_channel"])
            or int(span["note"]) != int(row["note"])
            or int(span["velocity"]) != int(row["velocity"])
            or int(span["start_tick"]) != int(row["start_tick"])
        ):
            raise ArrangementError(f"output note span disagrees with generated provenance: {row['generated_note_id']}")
        if row["end_reason"] == "note_off" and int(span["end_tick"]) != int(row["message_end_tick"]):
            raise ArrangementError(f"output note-off pairing disagrees with generated provenance: {row['generated_note_id']}")
    stats = midi_statistics(output)
    if int(stats["note_on_count"]) != len(provenance):
        raise ArrangementError("generated MIDI note count disagrees with provenance ledger")
    plan = {
        "schema_version": ARRANGER_SCHEMA_VERSION,
        "kind": ARRANGER_KIND,
        "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
        "source_anatomy_sha256": str(measured["anatomy_sha256"]),
        "pattern_bank_sha256": str(bank["pattern_bank_sha256"]),
        "seed": int(seed),
        "form_variant": str(form_variant),
        "target_bars": int(target_bars),
        "ticks_per_beat": ppq,
        "meter": deepcopy(bank["meter"]),
        "tempo_us_per_beat": tempo,
        "target_bpm": round(60_000_000.0 / tempo, 9),
        "density": float(density),
        "maximum_layers": int(maximum_layers),
        "channel_assignments": [
            {
                "slot_id": slot_id,
                "track_name": str(slot_specs[slot_id]["track_name"]),
                "role": str(slot_specs[slot_id]["role"]),
                "mode": str(slot_specs[slot_id]["mode"]),
                "program": int(slot_specs[slot_id]["program"]),
                "source_channel": int(slot_specs[slot_id]["channel"]),
                "output_channel": int(channel_map[slot_id]),
            }
            for slot_id in used_slots
        ],
        "form": form,
        "bar_decisions": bar_decisions,
        "generated_note_count": len(provenance),
        "generated_control_count": len(control_provenance),
        "controller_policy": "copy_bar_edge_state_and_in_bar_changes",
        "event_provenance": sorted(provenance, key=lambda row: (int(row["start_tick"]), str(row["slot_id"]), str(row["generated_note_id"]))),
        "control_provenance": sorted(control_provenance, key=lambda row: (int(row["tick"]), str(row["slot_id"]), str(row["generated_control_id"]))),
        "output_semantic_sha256": str(output["semantic_sha256"]),
        "output_statistics": stats,
    }
    plan["plan_sha256"] = midi_sha256_json(plan)
    midi_validate_pattern_arrangement(plan, output)
    return {"ok": True, "ledger": output, "plan": plan, "pattern_bank": bank}


def midi_validate_pattern_arrangement(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any] | None = None,
) -> None:
    if int(plan.get("schema_version") or 0) != ARRANGER_SCHEMA_VERSION:
        raise ArrangementError(f"unsupported arrangement plan schema: {plan.get('schema_version')}")
    if str(plan.get("kind") or "") != ARRANGER_KIND:
        raise ArrangementError(f"unsupported arrangement plan kind: {plan.get('kind')}")
    if float(plan.get("target_bpm") or 0.0) <= 0.0:
        raise ArrangementError("arrangement target_bpm must be positive")
    if not 0.0 < float(plan.get("density") or 0.0) <= 2.0:
        raise ArrangementError("arrangement density is outside its contract")
    if not 1 <= int(plan.get("maximum_layers") or 0) <= 16:
        raise ArrangementError("arrangement maximum_layers is outside its contract")
    form = plan.get("form")
    decisions = plan.get("bar_decisions")
    provenance = plan.get("event_provenance")
    if not isinstance(form, list) or not form:
        raise ArrangementError("arrangement plan requires a form")
    expected_bar = 0
    for index, section in enumerate(form):
        if int(section.get("section_index", -1)) != index:
            raise ArrangementError("arrangement sections are not indexed contiguously")
        if int(section.get("start_bar_index", -1)) != expected_bar:
            raise ArrangementError("arrangement sections are not contiguous")
        if int(section.get("end_bar_index", 0)) <= expected_bar:
            raise ArrangementError("arrangement section has no bars")
        expected_bar = int(section["end_bar_index"])
    if expected_bar != int(plan.get("target_bars") or 0):
        raise ArrangementError("arrangement form does not cover target_bars")
    if not isinstance(decisions, list) or len(decisions) != int(plan.get("target_bars") or 0):
        raise ArrangementError("arrangement requires one decision per target bar")
    for index, decision in enumerate(decisions):
        if int(decision.get("bar_index", -1)) != index:
            raise ArrangementError("arrangement bar decisions are not indexed contiguously")
        if not str(decision.get("selected_pattern_id") or ""):
            raise ArrangementError("arrangement decision has no selected pattern")
        if not isinstance(decision.get("alternatives"), list) or not decision["alternatives"]:
            raise ArrangementError("arrangement decision has no alternatives receipt")
    if not isinstance(provenance, list):
        raise ArrangementError("arrangement event_provenance must be a list")
    note_ids = [str(row.get("generated_note_id") or "") for row in provenance]
    if not all(note_ids) or len(note_ids) != len(set(note_ids)):
        raise ArrangementError("generated note provenance IDs must be unique and nonempty")
    if len(provenance) != int(plan.get("generated_note_count") or 0):
        raise ArrangementError("generated_note_count disagrees with provenance")
    output_note_ids = [str(row.get("output_event_id") or "") for row in provenance]
    output_note_off_ids = [str(row.get("output_note_off_event_id") or "") for row in provenance]
    if not all(output_note_ids) or len(output_note_ids) != len(set(output_note_ids)):
        raise ArrangementError("output note event IDs must be unique and nonempty")
    if not all(output_note_off_ids) or len(output_note_off_ids) != len(set(output_note_off_ids)):
        raise ArrangementError("output note-off event IDs must be unique and nonempty")
    if any(int(row.get("message_end_tick", 0)) <= int(row.get("start_tick", -1)) for row in provenance):
        raise ArrangementError("generated note messages must have positive duration")
    if any(int(row.get("sounding_end_tick", 0)) < int(row.get("message_end_tick", 0)) for row in provenance):
        raise ArrangementError("generated sounding duration cannot end before its note-off message")
    controls = plan.get("control_provenance")
    if not isinstance(controls, list):
        raise ArrangementError("arrangement control_provenance must be a list")
    control_ids = [str(row.get("generated_control_id") or "") for row in controls]
    output_control_ids = [str(row.get("output_event_id") or "") for row in controls]
    if len(controls) != int(plan.get("generated_control_count") or 0):
        raise ArrangementError("generated_control_count disagrees with control provenance")
    if len(control_ids) != len(set(control_ids)) or not all(control_ids):
        raise ArrangementError("generated control IDs must be unique and nonempty")
    if len(output_control_ids) != len(set(output_control_ids)) or not all(output_control_ids):
        raise ArrangementError("output control event IDs must be unique and nonempty")
    assignments = plan.get("channel_assignments")
    if not isinstance(assignments, list) or not assignments:
        raise ArrangementError("arrangement requires MIDI channel assignments")
    pitched_channels = [int(row["output_channel"]) for row in assignments if str(row.get("mode") or "") != "trigger"]
    if 9 in pitched_channels or len(pitched_channels) != len(set(pitched_channels)):
        raise ArrangementError("pitched output channels must be unique and must not use the drum channel")
    if any(int(row["output_channel"]) != 9 for row in assignments if str(row.get("mode") or "") == "trigger"):
        raise ArrangementError("trigger slots must use MIDI channel 10")
    expected = midi_sha256_json({key: value for key, value in plan.items() if key != "plan_sha256"})
    if str(plan.get("plan_sha256") or "") != expected:
        raise ArrangementError("plan_sha256 does not match arrangement contents")
    if ledger is not None:
        midi_validate_ledger(ledger)
        if str(ledger["semantic_sha256"]) != str(plan.get("output_semantic_sha256") or ""):
            raise ArrangementError("arrangement plan references another output MIDI ledger")
        stats = midi_statistics(ledger)
        if int(stats["note_on_count"]) != len(provenance):
            raise ArrangementError("output MIDI note count disagrees with arrangement provenance")
        if int(stats["control_change_count"]) + int(stats["pitchwheel_count"]) != len(controls):
            raise ArrangementError("output MIDI controller count disagrees with arrangement provenance")
        compiled = midi_compile_note_spans(ledger)
        spans_by_id = {str(span["event_id"]): span for span in compiled["note_spans"]}
        if set(spans_by_id) != set(output_note_ids):
            raise ArrangementError("output MIDI note-span IDs disagree with arrangement provenance")
        for row in provenance:
            span = spans_by_id[str(row["output_event_id"])]
            if (
                int(span["track_index"]) != int(row["output_track_index"])
                or int(span["channel"]) != int(row["output_channel"])
                or int(span["note"]) != int(row["note"])
                or int(span["velocity"]) != int(row["velocity"])
                or int(span["start_tick"]) != int(row["start_tick"])
                or int(span["end_tick"]) != int(row["sounding_end_tick"])
                or str(span.get("end_reason") or "") != str(row.get("end_reason") or "")
            ):
                raise ArrangementError("output MIDI span values disagree with arrangement provenance")
            if str(row.get("end_reason") or "") == "note_off" and int(row["sounding_end_tick"]) != int(row["message_end_tick"]):
                raise ArrangementError("output MIDI note-off pairing disagrees with arrangement provenance")


def midi_write_pattern_arrangement(
    source_ledger: Mapping[str, Any],
    output_midi: str | Path,
    output_plan: str | Path,
    output_bank: str | Path | None = None,
    *,
    overwrite: bool = False,
    **configuration: Any,
) -> dict[str, Any]:
    result = midi_generate_pattern_arrangement(source_ledger, **configuration)
    midi_path = Path(output_midi).expanduser().resolve()
    plan_path = Path(output_plan).expanduser().resolve()
    bank_path = Path(output_bank).expanduser().resolve() if output_bank is not None else Path(str(plan_path) + ".patterns.json")
    paths = [midi_path, plan_path, bank_path]
    if len({str(path) for path in paths}) != len(paths):
        raise ArrangementError("MIDI, plan, and pattern-bank output paths must be distinct")
    if not overwrite:
        conflicts = [str(path) for path in paths if path.exists()]
        if conflicts:
            raise FileExistsError("refusing partial arrangement write because output path(s) already exist: " + ", ".join(conflicts))
    midi_receipt = midi_write(result["ledger"], midi_path, overwrite=overwrite)
    plan_receipt = _arranger_atomic_json(plan_path, result["plan"], overwrite=overwrite)
    bank_receipt = _arranger_atomic_json(bank_path, result["pattern_bank"], overwrite=overwrite)
    return {
        "ok": True,
        "midi": midi_receipt,
        "plan_path": plan_receipt["path"],
        "plan_file_sha256": plan_receipt["sha256"],
        "plan_sha256": result["plan"]["plan_sha256"],
        "pattern_bank_path": bank_receipt["path"],
        "pattern_bank_file_sha256": bank_receipt["sha256"],
        "pattern_bank_sha256": result["pattern_bank"]["pattern_bank_sha256"],
        "source_semantic_sha256": result["plan"]["source_semantic_sha256"],
        "output_semantic_sha256": result["plan"]["output_semantic_sha256"],
        "target_bars": result["plan"]["target_bars"],
        "section_count": len(result["plan"]["form"]),
        "generated_note_count": result["plan"]["generated_note_count"],
        "generated_control_count": result["plan"]["generated_control_count"],
    }
