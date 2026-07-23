from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.live.model import (
    LiveError,
    live_advance_state,
    live_apply_control,
    live_apply_pending_persona,
    live_new_state,
    live_validate_state,
)
from earcrate.live.operators import LIVE_TECHNIQUE_NAMES
from earcrate.live.planner import live_plan_next, live_validate_atlas, live_validate_horizon_plan
from earcrate.midi.model import midi_sha256_json

LIVE_ENGINE_STEP_SCHEMA_VERSION = 1
LIVE_ENGINE_STEP_KIND = "earcrate_live_engine_step"


def live_engine_new(
    atlas: Mapping[str, Any],
    *,
    persona: str = "club",
    seed: int = 1,
    target_energy: float | None = None,
    density: float | None = None,
    risk: float | None = None,
    maximum_layers: int | None = None,
    phrase_bars: int = 0,
    horizon_bars: int = 0,
) -> dict[str, Any]:
    live_validate_atlas(atlas)
    return live_new_state(
        atlas,
        persona=persona,
        seed=seed,
        target_energy=target_energy,
        density=density,
        risk=risk,
        maximum_layers=maximum_layers,
        phrase_bars=phrase_bars,
        horizon_bars=horizon_bars,
    )


def live_validate_engine_step(step: Mapping[str, Any]) -> None:
    if int(step.get("schema_version") or 0) != LIVE_ENGINE_STEP_SCHEMA_VERSION:
        raise LiveError(f"unsupported live engine-step schema: {step.get('schema_version')}")
    if str(step.get("kind") or "") != LIVE_ENGINE_STEP_KIND:
        raise LiveError(f"unsupported live engine-step kind: {step.get('kind')}")
    before = step.get("state_before")
    after = step.get("state_after")
    plan = step.get("plan")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise LiveError("live engine step requires before and after states")
    live_validate_state(before)
    live_validate_state(after)
    if not isinstance(plan, Mapping):
        raise LiveError("live engine step requires a horizon plan")
    live_validate_horizon_plan(plan)
    committed = plan["committed_decisions"]
    expected_advance = len(committed)
    if int(after["current_bar_index"]) - int(before["current_bar_index"]) != expected_advance:
        raise LiveError("live engine state did not advance by the committed bar count")
    if str(after.get("last_committed_plan_sha256") or "") != str(plan["plan_sha256"]):
        raise LiveError("live engine state does not identify its committed plan")
    expected = midi_sha256_json({key: value for key, value in step.items() if key != "step_sha256"})
    if str(step.get("step_sha256") or "") != expected:
        raise LiveError("step_sha256 does not match live engine step contents")


def live_engine_step(
    atlas: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    controls: Sequence[Mapping[str, Any]] | None = None,
    horizon_bars: int = 0,
    commit_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
) -> dict[str, Any]:
    """Apply controls to the current state, commit one legal prefix, and return the next state."""
    live_validate_atlas(atlas)
    live_validate_state(state)
    if str(state["atlas_sha256"]) != str(atlas["atlas_sha256"]):
        raise LiveError("live engine state belongs to another atlas")
    controlled = deepcopy(dict(state))
    applied_controls = []
    for control in controls or []:
        before_hash = str(controlled["state_sha256"])
        controlled = live_apply_control(controlled, control, known_techniques=LIVE_TECHNIQUE_NAMES)
        applied_controls.append(
            {
                "state_before_sha256": before_hash,
                "state_after_sha256": str(controlled["state_sha256"]),
                "command": str(control.get("command") or ""),
                "value": deepcopy(control.get("value")),
            }
        )
    controlled = live_apply_pending_persona(controlled)
    planned = live_plan_next(
        atlas,
        controlled,
        horizon_bars=horizon_bars,
        commit_bars=commit_bars,
        beam_width=beam_width,
        candidate_limit=candidate_limit,
    )
    resolved = planned["resolved_state"]
    plan = planned["plan"]
    after = live_advance_state(
        resolved,
        plan["committed_decisions"],
        plan_sha256=str(plan["plan_sha256"]),
    )
    step = {
        "schema_version": LIVE_ENGINE_STEP_SCHEMA_VERSION,
        "kind": LIVE_ENGINE_STEP_KIND,
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "state_before": deepcopy(dict(state)),
        "controlled_state_sha256": str(controlled["state_sha256"]),
        "applied_controls": applied_controls,
        "plan": deepcopy(dict(plan)),
        "state_after": after,
    }
    step["step_sha256"] = midi_sha256_json(step)
    live_validate_engine_step(step)
    return step
