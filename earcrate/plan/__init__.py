"""earcrate.plan — pure composition arithmetic (§5.3 / Lesson #1).

ONE source of the composition math: no I/O, no DB, no core state. Every
function here is pure and deterministic so the numbers a mashup is planned
against cannot drift between the readiness audit and the composer. app.py
delegates to these functions instead of re-inlining the formulas.
"""
from earcrate.plan.math import (
    readiness_scale,
    sources_needed,
    readiness_need,
    bars_exact,
    target_bars,
)

__all__ = [
    "readiness_scale",
    "sources_needed",
    "readiness_need",
    "bars_exact",
    "target_bars",
]
