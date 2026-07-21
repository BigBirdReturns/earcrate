from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

_live_planner_module = None
import earcrate.live.planner as _live_planner_module
from earcrate.midi.model import midi_sha256_json

_original_live_score_candidate = (
    _live_planner_module._live_score_candidate
    if _live_planner_module is not None
    else _live_score_candidate
)
_original_live_plan_next = (
    _live_planner_module.live_plan_next
    if _live_planner_module is not None
    else live_plan_next
)
_live_runtime_requested_risk = 0.5


def _live_score_candidate_with_requested_risk(*args: Any, **kwargs: Any):
    score, terms = _original_live_score_candidate(*args, **kwargs)
    result: Mapping[str, Any] = kwargs["result"]
    policy: Mapping[str, Any] = kwargs["policy"]
    previous = float(terms["risk_fit"])
    current = max(0.0, 1.0 - abs(float(result["risk"]) - float(_live_runtime_requested_risk)))
    weight = float(policy["score_weights"]["risk_fit"])
    adjusted = float(score) + weight * (current - previous)
    terms = dict(terms)
    terms["risk_fit"] = round(current, 9)
    return round(adjusted, 9), terms


def live_plan_next(*args: Any, **kwargs: Any) -> dict[str, Any]:
    global _live_runtime_requested_risk
    state = args[1] if len(args) > 1 else kwargs["state"]
    previous_risk = _live_runtime_requested_risk
    _live_runtime_requested_risk = float(state["risk"])
    try:
        result = _original_live_plan_next(*args, **kwargs)
    finally:
        _live_runtime_requested_risk = previous_risk
    out = deepcopy(result)
    plan = out["plan"]
    for decision in plan["decisions"]:
        contextual = []
        for ordinal, command in enumerate(decision.get("commands") or []):
            row = deepcopy(dict(command))
            template_id = str(row.pop("command_id", ""))
            row["template_command_id"] = template_id
            row["bar_index"] = int(decision["bar_index"])
            row["ordinal"] = ordinal
            row["command_id"] = "live_command_" + midi_sha256_json(
                {
                    "atlas_sha256": plan["atlas_sha256"],
                    "state_before_sha256": plan["state_before_sha256"],
                    "bar_index": decision["bar_index"],
                    "operator": decision["operator"],
                    "candidate_pattern_id": decision["candidate_pattern_id"],
                    "ordinal": ordinal,
                    "template": row,
                }
            )[:24]
            contextual.append(row)
        decision["commands"] = contextual
    plan["committed_decisions"] = deepcopy(plan["decisions"][: int(plan["commit_bars"])])
    plan["plan_sha256"] = midi_sha256_json({key: value for key, value in plan.items() if key != "plan_sha256"})
    if _live_planner_module is not None:
        _live_planner_module.live_validate_horizon_plan(plan)
    else:
        live_validate_horizon_plan(plan)
    return out


# In the package build, patch the module. In the concatenated single-file build,
# these assignments overwrite the already-defined global functions directly.
if _live_planner_module is not None:
    _live_planner_module._live_score_candidate = _live_score_candidate_with_requested_risk
    _live_planner_module.live_plan_next = live_plan_next
else:
    _live_score_candidate = _live_score_candidate_with_requested_risk
