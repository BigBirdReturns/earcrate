"""Fail-closed public contract for observational dynamic-arc evidence."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from earcrate.judge import arc_core as _core

DynamicArcError = _core.DynamicArcError
FRAME_SECONDS = _core.FRAME_SECONDS
EPS = _core.EPS


def _validate_arrangement(arrangement: Mapping[str, Any]) -> None:
    sections = list(arrangement.get("sections") or [])
    if not sections:
        raise DynamicArcError("dynamic-arc evidence requires at least one whole-set section")
    if not all(isinstance(section, Mapping) for section in sections):
        raise DynamicArcError("sections must be mappings")

    islands = list(arrangement.get("islands") or [])
    if not islands:
        return
    if not all(isinstance(island, Mapping) for island in islands):
        raise DynamicArcError("islands must be mappings")
    complete = [
        island.get("island_id") not in (None, "")
        and island.get("start_s") is not None
        and island.get("end_s") is not None
        for island in islands
    ]
    if not all(complete):
        raise DynamicArcError(
            "island spans are partial; provide start_s and end_s for every island or omit the island table"
        )


def _mono_signal(y: Any) -> np.ndarray:
    signal = np.asarray(y, dtype=np.float32)
    if signal.ndim != 1:
        raise DynamicArcError(
            f"dynamic-arc evidence requires a one-dimensional mono signal, got shape {signal.shape}"
        )
    return signal


def _rms_db(values: np.ndarray) -> float:
    if values.size == 0:
        return float(20.0 * math.log10(EPS))
    rms = float(np.sqrt(np.mean(np.square(values.astype(np.float64)))))
    return float(20.0 * math.log10(rms + EPS))


def _role_union(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    return sorted({
        str(role)
        for row in rows
        for role in row.get("roles") or []
        if str(role)
    })


def _role_boundary_events(
    signal: np.ndarray,
    sr: int,
    section_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Measure role changes from active-set boundaries, not declaration adjacency."""
    if sr <= 0:
        raise DynamicArcError("sample rate must be positive")
    boundaries = sorted({
        float(value)
        for row in section_rows
        for value in (row.get("start_s"), row.get("end_s"))
        if value is not None
    })
    events: List[Dict[str, Any]] = []
    for boundary_index, boundary_s in enumerate(boundaries[1:-1], 1):
        active_before = [
            row
            for row in section_rows
            if float(row["start_s"]) < boundary_s <= float(row["end_s"])
        ]
        active_after = [
            row
            for row in section_rows
            if float(row["start_s"]) <= boundary_s < float(row["end_s"])
        ]
        if not active_before or not active_after:
            continue
        before_roles = _role_union(active_before)
        after_roles = _role_union(active_after)
        entered = sorted(set(after_roles) - set(before_roles))
        exited = sorted(set(before_roles) - set(after_roles))
        if not entered and not exited:
            continue

        previous_boundary = boundaries[boundary_index - 1]
        next_boundary = boundaries[boundary_index + 1]
        before_start_s = max(previous_boundary, boundary_s - FRAME_SECONDS)
        after_end_s = min(next_boundary, boundary_s + FRAME_SECONDS)
        boundary_frame = max(
            0, min(signal.size, int(round(boundary_s * sr)))
        )
        before_start = max(
            0, min(boundary_frame, int(round(before_start_s * sr)))
        )
        after_end = max(
            boundary_frame, min(signal.size, int(round(after_end_s * sr)))
        )
        before_rms_db = _rms_db(signal[before_start:boundary_frame])
        after_rms_db = _rms_db(signal[boundary_frame:after_end])
        before_indices = sorted(
            int(row["section_index"]) for row in active_before
        )
        after_indices = sorted(
            int(row["section_index"]) for row in active_after
        )
        events.append({
            "at_s": float(boundary_s),
            "from_section_index": before_indices[0] if len(before_indices) == 1 else None,
            "to_section_index": after_indices[0] if len(after_indices) == 1 else None,
            "from_section_indices": before_indices,
            "to_section_indices": after_indices,
            "active_roles_before": before_roles,
            "active_roles_after": after_roles,
            "entered_roles": entered,
            "exited_roles": exited,
            "before_window_s": (boundary_frame - before_start) / float(sr),
            "after_window_s": (after_end - boundary_frame) / float(sr),
            "before_rms_db": before_rms_db,
            "after_rms_db": after_rms_db,
            "rms_delta_db": after_rms_db - before_rms_db,
        })
    return events


def gate_frame_rms_db(y: Any, sr: int) -> np.ndarray:
    return _core.gate_frame_rms_db(_mono_signal(y), sr)


def measure_dynamic_arc(y: Any, sr: int, arrangement: Mapping[str, Any]):
    _validate_arrangement(arrangement)
    signal = _mono_signal(y)
    result = dict(_core.measure_dynamic_arc(signal, sr, arrangement))
    result["role_entries_and_exits"] = _role_boundary_events(
        signal, int(sr), list(result.get("sections") or [])
    )
    result["role_transition_policy"] = (
        "active_role_union_at_actual_section_boundaries_with_adjacent_master_windows"
    )
    return result


__all__ = [
    "DynamicArcError",
    "FRAME_SECONDS",
    "gate_frame_rms_db",
    "measure_dynamic_arc",
]
