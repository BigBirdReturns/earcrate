"""Observational dynamic-arc diagnostics for governed renders.

This module measures the master that was actually rendered. It does not change
gains, normalize sections, compress the program, or assign causal blame.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np


FRAME_SECONDS = 5.0
EPS = 1e-9


class DynamicArcError(ValueError):
    """The render and arrangement cannot be aligned honestly."""


def _rms(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values.astype(np.float64)))))


def _db(value: float) -> float:
    return float(20.0 * math.log10(float(value) + EPS))


def gate_frame_rms_db(y: np.ndarray, sr: int) -> np.ndarray:
    """The same non-overlapping five-second frame law used by drydeck_metrics."""
    signal = np.nan_to_num(np.asarray(y, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if sr <= 0:
        raise DynamicArcError("sample rate must be positive")
    if signal.size == 0:
        return np.zeros(0, dtype=np.float64)
    frame = max(512, int(FRAME_SECONDS * sr))
    values = [
        _rms(signal[index:index + frame])
        for index in range(0, max(1, signal.size - frame + 1), frame)
    ]
    return 20.0 * np.log10(np.asarray(values, dtype=np.float64) + EPS)


def _section_bounds(section: Mapping[str, Any], sr: int, signal_size: int) -> Tuple[int, int]:
    if section.get("start_s") is None or section.get("end_s") is None:
        raise DynamicArcError("every whole-set section needs start_s and end_s")
    start = max(0, min(signal_size, int(round(float(section["start_s"]) * sr))))
    end = max(start, min(signal_size, int(round(float(section["end_s"]) * sr))))
    return start, end


def _roles(section: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(sorted({
        str(layer.get("role") or "full")
        for layer in section.get("layers") or []
        if isinstance(layer, Mapping)
    }))


def _weighted_mean(rows: Sequence[Tuple[float, float]]) -> float:
    total = sum(weight for _value, weight in rows)
    if total <= 0.0:
        return 0.0
    return sum(value * weight for value, weight in rows) / total


def _island_spans(
    arrangement: Mapping[str, Any],
    section_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    raw_islands = list(arrangement.get("islands") or [])
    for index, row in enumerate(raw_islands):
        if not isinstance(row, Mapping):
            raise DynamicArcError(f"island {index} is not a mapping")
        island_id = str(row.get("island_id") or "")
        if not island_id:
            raise DynamicArcError(f"island {index} has no island_id")
        if row.get("start_s") is None or row.get("end_s") is None:
            continue
        start = float(row["start_s"])
        end = float(row["end_s"])
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise DynamicArcError(f"invalid island span for {island_id!r}")
        spans.append({"island_id": island_id, "start_s": start, "end_s": end})

    if not spans:
        grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for row in section_rows:
            grouped[str(row.get("island_id") or "")].append(row)
        for island_id, rows in grouped.items():
            if not island_id:
                continue
            spans.append({
                "island_id": island_id,
                "start_s": min(float(row["start_s"]) for row in rows),
                "end_s": max(float(row["end_s"]) for row in rows),
            })

    spans.sort(key=lambda row: (row["start_s"], row["end_s"], row["island_id"]))
    return spans


def _frame_region(center_s: float, spans: Sequence[Mapping[str, Any]]) -> Tuple[str, List[str], str]:
    memberships = [
        str(row["island_id"])
        for row in spans
        if float(row["start_s"]) <= center_s < float(row["end_s"])
    ]
    if not memberships:
        return "unassigned", [], "unassigned"
    if len(memberships) == 1:
        return memberships[0], memberships, "island"
    return "overlap:" + "|".join(memberships), memberships, "transition_overlap"


def measure_dynamic_arc(
    y: np.ndarray,
    sr: int,
    arrangement: Mapping[str, Any],
) -> Dict[str, Any]:
    """Measure section and island energy on the published master timeline.

    Frames that fall inside a phrase-boundary overlap are assigned to an explicit
    composite region rather than whichever island or section happened to be listed
    first. The within/between decomposition is therefore declaration-order stable.
    """
    signal = np.nan_to_num(np.asarray(y, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    frame_db = gate_frame_rms_db(signal, sr)
    frame_seconds = max(512, int(FRAME_SECONDS * sr)) / float(sr)
    frame_centers = [
        (index * frame_seconds) + frame_seconds / 2.0
        for index in range(len(frame_db))
    ]

    indexed_sections = list(enumerate(arrangement.get("sections") or []))
    if not all(isinstance(section, Mapping) for _index, section in indexed_sections):
        raise DynamicArcError("sections must be mappings")
    indexed_sections.sort(
        key=lambda item: (
            float(item[1].get("start_s") or 0.0),
            float(item[1].get("end_s") or item[1].get("start_s") or 0.0),
            item[0],
        )
    )

    section_rows: List[Dict[str, Any]] = []
    for original_index, section in indexed_sections:
        start, end = _section_bounds(section, sr, signal.size)
        roles = _roles(section)
        gains = [
            float(layer.get("gain_db") or 0.0)
            for layer in section.get("layers") or []
            if isinstance(layer, Mapping)
        ]
        section_rows.append({
            "section_index": original_index,
            "musical_order_index": len(section_rows),
            "island_id": str(section.get("island_id") or ""),
            "section_type": str(section.get("type") or section.get("section_type") or ""),
            "start_s": start / float(sr),
            "end_s": end / float(sr),
            "duration_s": (end - start) / float(sr),
            "rms_db": _db(_rms(signal[start:end])),
            "roles": list(roles),
            "role_count": len(roles),
            "layer_count": len(list(section.get("layers") or [])),
            "declared_gain_db_mean": sum(gains) / len(gains) if gains else 0.0,
            "declared_gain_db_min": min(gains) if gains else 0.0,
            "declared_gain_db_max": max(gains) if gains else 0.0,
        })

    spans = _island_spans(arrangement, section_rows)
    region_for_frame: List[str] = []
    region_memberships: Dict[str, Dict[str, Any]] = {}
    for center in frame_centers:
        region_id, memberships, kind = _frame_region(center, spans)
        region_for_frame.append(region_id)
        region_memberships.setdefault(region_id, {
            "region_id": region_id,
            "kind": kind,
            "island_ids": memberships,
        })

    grouped_frames: Dict[str, List[float]] = defaultdict(list)
    for region_id, value in zip(region_for_frame, frame_db):
        grouped_frames[region_id].append(float(value))

    total_mean = float(np.mean(frame_db)) if frame_db.size else 0.0
    total_variance = float(np.var(frame_db)) if frame_db.size else 0.0
    within_variance = 0.0
    between_variance = 0.0
    frame_count = max(1, len(frame_db))
    region_rows: List[Dict[str, Any]] = []
    for region_id in sorted(grouped_frames):
        values = np.asarray(grouped_frames[region_id], dtype=np.float64)
        mean = float(np.mean(values)) if values.size else 0.0
        variance = float(np.var(values)) if values.size else 0.0
        weight = values.size / frame_count
        within_variance += weight * variance
        between_variance += weight * (mean - total_mean) ** 2
        region_rows.append({
            **region_memberships[region_id],
            "frame_count": int(values.size),
            "mean_rms_db": mean,
            "variance_db2": variance,
        })

    pure_island_rows = [row for row in region_rows if row["kind"] == "island"]
    pure_frame_count = sum(int(row["frame_count"]) for row in pure_island_rows)
    pure_mean = (
        sum(float(row["mean_rms_db"]) * int(row["frame_count"]) for row in pure_island_rows)
        / pure_frame_count
        if pure_frame_count
        else 0.0
    )
    within_island_variance = (
        sum(float(row["variance_db2"]) * int(row["frame_count"]) for row in pure_island_rows)
        / pure_frame_count
        if pure_frame_count
        else 0.0
    )
    between_island_variance = (
        sum(
            (float(row["mean_rms_db"]) - pure_mean) ** 2 * int(row["frame_count"])
            for row in pure_island_rows
        )
        / pure_frame_count
        if pure_frame_count
        else 0.0
    )

    type_groups: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    role_groups: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for row in section_rows:
        weight = max(0.0, float(row["duration_s"]))
        type_groups[str(row["section_type"])].append((float(row["rms_db"]), weight))
        role_groups["|".join(row["roles"])].append((float(row["rms_db"]), weight))

    section_type_means = {
        key: _weighted_mean(rows)
        for key, rows in sorted(type_groups.items())
    }
    role_occupancy_means = {
        key: _weighted_mean(rows)
        for key, rows in sorted(role_groups.items())
    }

    role_transitions: List[Dict[str, Any]] = []
    for previous, current in zip(section_rows, section_rows[1:]):
        before = set(previous["roles"])
        after = set(current["roles"])
        role_transitions.append({
            "from_section_index": previous["section_index"],
            "to_section_index": current["section_index"],
            "entered_roles": sorted(after - before),
            "exited_roles": sorted(before - after),
            "rms_delta_db": float(current["rms_db"]) - float(previous["rms_db"]),
        })

    return {
        "measurement": "published_master_dynamic_arc_v1",
        "frame_seconds": FRAME_SECONDS,
        "frame_count": int(frame_db.size),
        "rms_std_db": float(np.std(frame_db)) if frame_db.size else 0.0,
        "rms_mean_db": total_mean,
        "total_variance_db2": total_variance,
        "within_island_variance_db2": within_island_variance,
        "between_island_variance_db2": between_island_variance,
        "pure_island_frame_count": pure_frame_count,
        "within_region_variance_db2": within_variance,
        "between_region_variance_db2": between_variance,
        "variance_decomposition_residual_db2": total_variance - within_variance - between_variance,
        "regions": region_rows,
        "island_spans": spans,
        "sections": section_rows,
        "section_type_mean_rms_db": section_type_means,
        "role_occupancy_mean_rms_db": role_occupancy_means,
        "role_entries_and_exits": role_transitions,
        "overlap_policy": "transition_frames_form_explicit_composite_regions",
        "causal_disposition": "unassigned_measurement_only",
    }


__all__ = [
    "DynamicArcError",
    "FRAME_SECONDS",
    "gate_frame_rms_db",
    "measure_dynamic_arc",
]
