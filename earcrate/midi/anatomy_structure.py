from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from earcrate.midi.model import midi_sha256_json
from earcrate.midi.anatomy_grid import AnatomyError, _anatomy_round


def _anatomy_segment_intervals(
    vectors: Sequence[Sequence[float]],
    novelty: Sequence[float],
    *,
    minimum_bars: int,
    maximum_bars: int,
    section_penalty: float,
    boundary_reward: float,
) -> list[tuple[int, int]]:
    count = len(vectors)
    if count == 0:
        return []
    minimum = max(1, int(minimum_bars))
    maximum = max(minimum, int(maximum_bars))
    dimensions = len(vectors[0])
    prefix = [[0.0] * (count + 1) for _ in range(dimensions)]
    prefix_sq = [[0.0] * (count + 1) for _ in range(dimensions)]
    for index, vector in enumerate(vectors):
        for dimension, value in enumerate(vector):
            prefix[dimension][index + 1] = prefix[dimension][index] + float(value)
            prefix_sq[dimension][index + 1] = prefix_sq[dimension][index] + float(value) ** 2

    def cost(start: int, end: int) -> float:
        length = end - start
        total = 0.0
        for dimension in range(dimensions):
            summed = prefix[dimension][end] - prefix[dimension][start]
            summed_sq = prefix_sq[dimension][end] - prefix_sq[dimension][start]
            total += max(0.0, summed_sq - summed * summed / max(1, length))
        return total / max(1, dimensions)

    best: list[tuple[tuple[Any, ...], list[tuple[int, int]]] | None] = [None] * (count + 1)
    best[0] = ((0.0, 0, ()), [])
    for end in range(1, count + 1):
        candidates = []
        for start in range(max(0, end - maximum), end):
            length = end - start
            if length < minimum and not (start == 0 and end == count):
                continue
            previous = best[start]
            if previous is None:
                continue
            objective = float(previous[0][0]) + cost(start, end) + float(section_penalty)
            if start > 0:
                objective -= float(boundary_reward) * float(novelty[start])
            intervals = [*previous[1], (start, end)]
            rank = (_anatomy_round(objective, 12), len(intervals), tuple(interval[1] for interval in intervals))
            candidates.append((rank, intervals))
        if candidates:
            best[end] = min(candidates, key=lambda item: item[0])
    if best[count] is None:
        return [(0, count)]
    return best[count][1]


def _anatomy_quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _anatomy_section_label(
    index: int,
    count: int,
    mean_energy: float,
    energy_start: float,
    energy_end: float,
    active_roles: set[str],
    low: float,
    high: float,
) -> str:
    trend = energy_end - energy_start
    if index == 0 and mean_energy <= high:
        return "intro"
    if index == count - 1 and mean_energy <= high:
        return "outro"
    if trend >= 0.14:
        return "build"
    if mean_energy >= high:
        return "drop"
    if mean_energy <= low:
        return "breakdown"
    if {"vocal", "choir", "lead", "synth_lead"} & active_roles:
        return "hook"
    return "groove"


def _anatomy_sections(
    cells: Sequence[Mapping[str, Any]],
    vectors: Sequence[Sequence[float]],
    novelty: Sequence[float],
    *,
    minimum_bars: int,
    maximum_bars: int,
    section_penalty: float,
    boundary_reward: float,
) -> list[dict[str, Any]]:
    intervals = _anatomy_segment_intervals(
        vectors,
        novelty,
        minimum_bars=minimum_bars,
        maximum_bars=maximum_bars,
        section_penalty=section_penalty,
        boundary_reward=boundary_reward,
    )
    means = [sum(float(cells[index]["energy"]) for index in range(start, end)) / max(1, end - start) for start, end in intervals]
    low = _anatomy_quantile(means, 0.30)
    high = _anatomy_quantile(means, 0.72)
    out = []
    for section_index, (start, end) in enumerate(intervals):
        subset = cells[start:end]
        active_roles = {str(role) for cell in subset for role in cell["active_roles"]}
        active_slots = {str(slot) for cell in subset for slot in cell["active_slot_ids"]}
        mean_energy = means[section_index]
        label = _anatomy_section_label(
            section_index,
            len(intervals),
            mean_energy,
            float(subset[0]["energy"]),
            float(subset[-1]["energy"]),
            active_roles,
            low,
            high,
        )
        section = {
            "section_index": section_index,
            "label": label,
            "start_bar_index": start,
            "end_bar_index": end,
            "bar_count": end - start,
            "start_tick": int(subset[0]["start_tick"]),
            "end_tick": int(subset[-1]["end_tick"]),
            "start_seconds": float(subset[0]["start_seconds"]),
            "end_seconds": float(subset[-1]["end_seconds"]),
            "mean_energy": _anatomy_round(mean_energy),
            "minimum_energy": _anatomy_round(min(float(cell["energy"]) for cell in subset)),
            "maximum_energy": _anatomy_round(max(float(cell["energy"]) for cell in subset)),
            "energy_trend": _anatomy_round(float(subset[-1]["energy"]) - float(subset[0]["energy"])),
            "mean_layers": _anatomy_round(sum(float(cell["mean_active_layers"]) for cell in subset) / len(subset)),
            "mean_onsets_per_beat": _anatomy_round(sum(float(cell["onsets_per_beat"]) for cell in subset) / len(subset)),
            "active_roles": sorted(active_roles),
            "active_slot_ids": sorted(active_slots),
            "transition_in": None,
        }
        if section_index > 0:
            boundary_cell = cells[start]
            section["transition_in"] = {
                "boundary_bar_index": start,
                "novelty": float(novelty[start]),
                "entering_slot_ids": list(boundary_cell["entering_slot_ids"]),
                "exiting_slot_ids": list(boundary_cell["exiting_slot_ids"]),
            }
        section["section_id"] = "section_" + midi_sha256_json(section)[:24]
        out.append(section)
    return out


def _anatomy_bar_index_for_tick(bars: Sequence[Mapping[str, Any]], tick: int) -> int:
    starts = [int(bar["start_tick"]) for bar in bars]
    index = bisect_right(starts, int(tick)) - 1
    return max(0, min(len(bars) - 1, index))


def _anatomy_motifs(
    demand: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    *,
    subdivisions: int,
) -> list[dict[str, Any]]:
    if subdivisions <= 0:
        raise AnatomyError("motif_subdivisions must be positive")
    motifs = []
    for slot in demand["slots"]:
        by_bar: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for event in slot["events"]:
            by_bar[_anatomy_bar_index_for_tick(bars, int(event["start_tick"]))].append(event)
        groups: dict[str, dict[str, Any]] = {}
        for bar_index, events in sorted(by_bar.items()):
            bar = bars[bar_index]
            length = max(1, int(bar["end_tick"]) - int(bar["start_tick"]))
            pitches = [int(event["note"]) for event in events]
            pitch_anchor = 0 if str(slot["mode"]) == "trigger" else min(pitches)
            pattern = []
            for event in sorted(events, key=lambda row: (int(row["start_tick"]), int(row["note"]), str(row["event_id"]))):
                step = int(round((int(event["start_tick"]) - int(bar["start_tick"])) / length * subdivisions))
                duration = max(1, int(round(int(event["duration_ticks"]) / length * subdivisions)))
                pitch_value = int(event["note"]) if str(slot["mode"]) == "trigger" else int(event["note"]) - pitch_anchor
                pattern.append(
                    {
                        "step": max(0, min(subdivisions, step)),
                        "duration_steps": duration,
                        "pitch": pitch_value,
                        "velocity_bucket": min(7, int(event["velocity"]) // 16),
                    }
                )
            signature = {
                "mode": str(slot["mode"]),
                "role": str(slot["role_hint"]),
                "subdivisions": subdivisions,
                "pattern": pattern,
            }
            signature_sha = midi_sha256_json(signature)
            group = groups.setdefault(
                signature_sha,
                {
                    "motif_id": "motif_" + midi_sha256_json({"slot_id": slot["slot_id"], "signature": signature})[:24],
                    "slot_id": str(slot["slot_id"]),
                    "track_index": int(slot["track_index"]),
                    "track_name": str(slot["track_name"]),
                    "role": str(slot["role_hint"]),
                    "mode": str(slot["mode"]),
                    "signature_sha256": signature_sha,
                    "signature": signature,
                    "occurrences": [],
                },
            )
            group["occurrences"].append(
                {
                    "bar_index": bar_index,
                    "bar_number": int(bar["bar_number"]),
                    "event_ids": [str(event["event_id"]) for event in events],
                    "pitch_anchor": pitch_anchor if str(slot["mode"]) == "pitched" else None,
                }
            )
        for group in groups.values():
            group["occurrence_count"] = len(group["occurrences"])
            group["recurring"] = len(group["occurrences"]) >= 2
            motifs.append(group)
    motifs.sort(key=lambda item: (int(item["track_index"]), str(item["slot_id"]), str(item["signature_sha256"])))
    return motifs


def _anatomy_event_assignments(
    event_map: Mapping[str, Mapping[str, Any]],
    onset_to_bar: Mapping[str, int],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    section_for_bar = {}
    for section in sections:
        for bar_index in range(int(section["start_bar_index"]), int(section["end_bar_index"])):
            section_for_bar[bar_index] = int(section["section_index"])
    assignments = []
    for event_id in sorted(event_map):
        if event_id not in onset_to_bar:
            raise AnatomyError(f"selected MIDI event is absent from anatomy bars: {event_id}")
        meta = event_map[event_id]
        bar_index = int(onset_to_bar[event_id])
        assignments.append(
            {
                "event_id": event_id,
                "slot_id": str(meta["slot_id"]),
                "track_index": int(meta["track_index"]),
                "role": str(meta["role"]),
                "bar_index": bar_index,
                "section_index": int(section_for_bar[bar_index]),
                "start_tick": int(meta["start_tick"]),
                "end_tick": int(meta["end_tick"]),
                "note": int(meta["note"]),
                "velocity": int(meta["velocity"]),
            }
        )
    return assignments


def _anatomy_fingerprint(
    cells: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
    motifs: Sequence[Mapping[str, Any]],
    roles: Sequence[str],
) -> dict[str, Any]:
    role_coverage = {
        role: _anatomy_round(sum(1 for cell in cells if role in set(cell["active_roles"])) / max(1, len(cells)))
        for role in roles
    }
    layer_histogram: Counter[int] = Counter(int(cell["active_slot_count"]) for cell in cells)
    recurring = sum(1 for motif in motifs if motif["recurring"])
    return {
        "section_form": [
            {
                "label": section["label"],
                "bars": section["bar_count"],
                "mean_energy": section["mean_energy"],
                "active_roles": section["active_roles"],
            }
            for section in sections
        ],
        "bar_energy_curve": [float(cell["energy"]) for cell in cells],
        "bar_layer_curve": [float(cell["mean_active_layers"]) for cell in cells],
        "bar_onset_density_curve": [float(cell["onsets_per_beat"]) for cell in cells],
        "role_bar_coverage": role_coverage,
        "layer_count_histogram": {str(key): value for key, value in sorted(layer_histogram.items())},
        "motif_count": len(motifs),
        "recurring_motif_count": recurring,
        "motif_recurrence_ratio": _anatomy_round(recurring / max(1, len(motifs))),
    }


def _anatomy_structural_payload(anatomy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bars": [
            {
                "duration_beats": cell["duration_beats"],
                "numerator": cell["numerator"],
                "denominator": cell["denominator"],
                "active_roles": cell["active_roles"],
                "onset_count": cell["onset_count"],
                "mean_active_layers": cell["mean_active_layers"],
                "maximum_polyphony": cell["maximum_polyphony"],
                "onsets_per_beat": cell["onsets_per_beat"],
                "mean_velocity": cell["mean_velocity"],
                "register_onsets": cell["register_onsets"],
                "energy": cell["energy"],
            }
            for cell in anatomy["bars"]
        ],
        "sections": [
            {
                "label": section["label"],
                "start_bar_index": section["start_bar_index"],
                "end_bar_index": section["end_bar_index"],
                "mean_energy": section["mean_energy"],
                "active_roles": section["active_roles"],
            }
            for section in anatomy["sections"]
        ],
        "motifs": sorted(
            [
                {
                    "role": motif["role"],
                    "signature_sha256": motif["signature_sha256"],
                    "occurrence_bars": [row["bar_index"] for row in motif["occurrences"]],
                }
                for motif in anatomy["motifs"]
            ],
            key=lambda row: (str(row["role"]), str(row["signature_sha256"]), tuple(row["occurrence_bars"])),
        ),
        "fingerprint": anatomy["fingerprint"],
    }
