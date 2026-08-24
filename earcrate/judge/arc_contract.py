"""Fail-closed public contract for observational dynamic-arc evidence."""
from __future__ import annotations

from typing import Any, Mapping

from earcrate.judge import arc_core as _core

DynamicArcError = _core.DynamicArcError
FRAME_SECONDS = _core.FRAME_SECONDS
gate_frame_rms_db = _core.gate_frame_rms_db


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


def measure_dynamic_arc(y: Any, sr: int, arrangement: Mapping[str, Any]):
    _validate_arrangement(arrangement)
    return _core.measure_dynamic_arc(y, sr, arrangement)


__all__ = [
    "DynamicArcError",
    "FRAME_SECONDS",
    "gate_frame_rms_db",
    "measure_dynamic_arc",
]
