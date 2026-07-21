"""Compatibility import for the folded live planner implementation.

The planner now owns risk scoring and contextual command IDs directly. This
module intentionally performs no mutation and is excluded from the package
build; downstream imports should move to ``earcrate.live.planner``.
"""

from earcrate.live.planner import live_plan_next

__all__ = ["live_plan_next"]
