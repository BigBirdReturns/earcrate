from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.live.model import (
    LiveError,
    live_advance_state,
    live_apply_control,
    live_apply_pending_persona,
    live_new_state,
    live_persona_policy,
    live_validate_state,
)
from earcrate.live.operators import (
    LIVE_TECHNIQUE_NAMES,
    live_apply_technique,
    live_operator_capabilities,
    live_technique_names,
)
from earcrate.midi.arranger_fix import midi_pattern_bank
from earcrate.midi.model import midi_sha256_json, midi_validate_ledger

LIVE_ATLAS_SCHEMA_VERSION = 1
LIVE_ATLAS_KIND = "earcrate_live_material_atlas"
LIVE_PLAN_SCHEMA_VERSION = 1
LIVE_PLAN_KIND = "earcrate_live_horizon_plan"
LIVE_SESSION_SCHEMA_VERSION = 1
LIVE_SESSION_KIND = "earcrate_live_session_plan"


def live_runtime_capability() -> dict[str, Any]:
    return {
        "ready": True,
        "planner_backend": "deterministic_python_cpu",
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "expensive_analysis_expected_offline": True,
        "techniques": live_technique_names(),
        "personas": ["club", "girl_talk", "minimal", "pretty_lights"],
    }


def live_atlas_payload(atlas: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(atlas))
    out.pop("atlas_sha256", None)
    return out


def live_compute_atlas_sha256(atlas: Mapping[str, Any]) -> str:
    return midi_sha256_json(live_atlas_payload(atlas))


def live_validate_atlas(atlas: Mapping[str, Any]) -> None:
    if int(atlas.get("schema_version") or 0) != LIVE_ATLAS_SCHEMA_VERSION:
        raise LiveError(f"unsupported live atlas schema: {atlas.get('schema_version')}")
    if str(atlas.get("kind") or "") != LIVE_ATLAS_KIND:
        raise LiveError(f"unsupported live atlas kind: {atlas.get('kind')}")
    if not str(atlas.get("source_semantic_sha256") or ""):
        raise LiveError("live atlas requires a source semantic identity")
    patterns = atlas.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise LiveError("live atlas requires playable patterns")
    pattern_ids = [str(row.get("pattern_id") or "") for row in patterns]
    if not all(pattern_ids) or len(pattern_ids) != len(set(pattern_ids)):
        raise LiveError("live atlas pattern IDs must be unique and nonempty")
    for pattern in patterns:
        slots = pattern.get("slots")
        if not isinstance(slots, list) or not slots:
            raise LiveError(f"live pattern {pattern.get('pattern_id')} has no slots")
        techniques = pattern.get("techniques")
        if not isinstance(techniques, list) or not techniques:
            raise LiveError(f"live pattern {pattern.get('pattern_id')} has no technique capability list")
        if any(str(name) not in LIVE_TECHNIQUE_NAMES for name in techniques):
            raise LiveError(f"live pattern {pattern.get('pattern_id')} contains an unknown technique")
    bank = atlas.get("pattern_bank")
    if not isinstance(bank, Mapping):
        raise LiveError("live atlas requires its exact pattern bank")
    if str(bank.get("pattern_bank_sha256") or "") != str(atlas.get("pattern_bank_sha256") or ""):
        raise LiveError("live atlas and pattern bank identities disagree")
    expected = live_compute_atlas_sha256(atlas)
    if str(atlas.get("atlas_sha256") or "") != expected:
        raise LiveError("atlas_sha256 does not match live atlas contents")


def live_atlas_from_midi(
    ledger: Mapping[str, Any],
    *,
    anatomy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    midi_validate_ledger(ledger)
    bank = midi_pattern_bank(ledger, anatomy)
    patterns = []
    for source in bank["patterns"]:
        pattern = deepcopy(dict(source))
        pattern["techniques"] = live_operator_capabilities(pattern)
        pattern["material_ids"] = [
            "live_material_" + midi_sha256_json(
                {
                    "pattern_id": pattern["pattern_id"],
                    "slot_id": slot["slot_id"],
                    "source_semantic_sha256": ledger["semantic_sha256"],
                }
            )[:24]
            for slot in pattern["slots"]
        ]
        patterns.append(pattern)
    atlas = {
        "schema_version": LIVE_ATLAS_SCHEMA_VERSION,
        "kind": LIVE_ATLAS_KIND,
        "source_semantic_sha256": str(ledger["semantic_sha256"]),
        "pattern_bank_sha256": str(bank["pattern_bank_sha256"]),
        "ticks_per_beat": int(bank["ticks_per_beat"]),
        "meter": deepcopy(dict(bank["meter"])),
        "declared_pattern_count": len(patterns),
        "declared_material_count": sum(len(pattern["slots"]) for pattern in patterns),
        "patterns": patterns,
        "pattern_bank": deepcopy(dict(bank)),
        "runtime_contract": live_runtime_capability(),
    }
    atlas["atlas_sha256"] = live_compute_atlas_sha256(atlas)
    live_validate_atlas(atlas)
    return atlas


def _live_jitter(seed: int, *parts: Any) -> float:
    digest = midi_sha256_json({"seed": int(seed), "parts": [str(part) for part in parts]})
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _live_target_energy(state: Mapping[str, Any], bar_index: int) -> float:
    base = float(state["target_energy"])
    phase = int(bar_index) % 16
    contour = (0.045 * math.sin(phase / 15.0 * math.pi * 2.0)) + (0.025 if phase in {7, 15} else 0.0)
    return max(0.0, min(1.0, base + contour))


def _live_target_layers(state: Mapping[str, Any], policy: Mapping[str, Any], target_energy: float) -> int:
    maximum = min(int(state["maximum_layers"]), int(policy["maximum_layers"]))
    raw = (1.0 + target_energy * max(0, maximum - 1)) * float(state["density"])
    return max(1, min(maximum, int(round(raw))))


def _live_layer_energy(layers: Sequence[Mapping[str, Any]], patterns: Mapping[str, Mapping[str, Any]]) -> float:
    pattern_ids = sorted({str(row.get("pattern_id") or "") for row in layers if str(row.get("pattern_id") or "")})
    if not pattern_ids:
        return 0.0
    return sum(float(patterns[pattern_id]["source_energy"]) for pattern_id in pattern_ids) / len(pattern_ids)


def _live_continuity(active: Sequence[Mapping[str, Any]], selected: Sequence[Mapping[str, Any]]) -> float:
    before = {str(row.get("layer_id") or "") for row in active}
    after = {str(row.get("layer_id") or "") for row in selected}
    if not before and not after:
        return 1.0
    if not before or not after:
        return 0.0
    return len(before & after) / max(1, len(before | after))


def _live_role_coverage(layers: Sequence[Mapping[str, Any]], priorities: Sequence[str], target_layers: int) -> float:
    desired = set(str(value) for value in list(priorities)[: max(1, int(target_layers))])
    present = {str(row.get("category") or "other") for row in layers}
    return len(desired & present) / max(1, len(desired))


def _live_pattern_rank(
    pattern: Mapping[str, Any],
    *,
    target_energy: float,
    priorities: Sequence[str],
    target_layers: int,
    recent_patterns: Sequence[str],
    seed: int,
    bar_index: int,
) -> tuple[float, int, str]:
    energy_fit = max(0.0, 1.0 - abs(float(pattern["source_energy"]) - float(target_energy)))
    categories = set(str(value) for value in pattern.get("categories") or [])
    desired = set(str(value) for value in list(priorities)[: max(1, int(target_layers))])
    coverage = len(categories & desired) / max(1, len(desired))
    reuse = min(1.0, list(recent_patterns).count(str(pattern["pattern_id"])) / 6.0)
    score = 0.58 * energy_fit + 0.34 * coverage + 0.08 * _live_jitter(seed, bar_index, pattern["pattern_id"]) - 0.12 * reuse
    return -score, int(pattern["source_bar_index"]), str(pattern["pattern_id"])


def _live_score_candidate(
    *,
    result: Mapping[str, Any],
    active_layers: Sequence[Mapping[str, Any]],
    target_energy: float,
    target_layers: int,
    policy: Mapping[str, Any],
    recent_patterns: Sequence[str],
    seed: int,
    bar_index: int,
    pattern_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[float, dict[str, float]]:
    technique = str(result["technique"])
    technique_values = [float(value) for value in policy["technique_weights"].values()]
    technique_score = float(policy["technique_weights"].get(technique, 0.0)) / max(1e-9, max(technique_values))
    estimated_energy = max(0.0, min(1.0, _live_layer_energy(result["layers"], pattern_by_id) + float(result["energy_delta"])))
    energy_fit = max(0.0, 1.0 - abs(estimated_energy - target_energy))
    density_fit = max(0.0, 1.0 - abs(len(result["layers"]) - target_layers) / max(1, target_layers))
    role_coverage = _live_role_coverage(result["layers"], policy["category_priority"], target_layers)
    continuity = _live_continuity(active_layers, result["layers"])
    desired_continuity = 1.0 - float(policy["turnover_preference"])
    continuity_fit = max(0.0, 1.0 - abs(continuity - desired_continuity))
    pattern_ids = [str(value) for value in result["pattern_ids"]]
    recent = Counter(str(value) for value in recent_patterns)
    novelty = 1.0 - min(1.0, sum(recent[pattern_id] for pattern_id in pattern_ids) / max(1, 8 * len(pattern_ids)))
    risk_fit = max(0.0, 1.0 - abs(float(result["risk"]) - float(policy.get("default_risk", 0.5))))
    source_diversity = len(set(pattern_ids)) / max(1, len(result["layers"]))
    terms = {
        "technique": technique_score,
        "energy_fit": energy_fit,
        "density_fit": density_fit,
        "role_coverage": role_coverage,
        "continuity_fit": continuity_fit,
        "novelty": novelty,
        "risk_fit": risk_fit,
        "source_diversity": source_diversity,
        "estimated_energy": estimated_energy,
        "continuity": continuity,
    }
    weighted = sum(float(policy["score_weights"][name]) * terms[name] for name in policy["score_weights"])
    weighted += 0.003 * _live_jitter(seed, bar_index, technique, *pattern_ids)
    return round(weighted, 9), {key: round(float(value), 9) for key, value in terms.items()}


def live_validate_horizon_plan(plan: Mapping[str, Any]) -> None:
    if int(plan.get("schema_version") or 0) != LIVE_PLAN_SCHEMA_VERSION:
        raise LiveError(f"unsupported live plan schema: {plan.get('schema_version')}")
    if str(plan.get("kind") or "") != LIVE_PLAN_KIND:
        raise LiveError(f"unsupported live plan kind: {plan.get('kind')}")
    decisions = plan.get("decisions")
    committed = plan.get("committed_decisions")
    if not isinstance(decisions, list) or not decisions:
        raise LiveError("live horizon plan requires decisions")
    if not isinstance(committed, list) or not committed:
        raise LiveError("live horizon plan requires committed decisions")
    start = int(plan.get("start_bar_index") or 0)
    for offset, row in enumerate(decisions):
        if int(row.get("bar_index", -1)) != start + offset:
            raise LiveError("live horizon decisions must be contiguous")
        if str(row.get("operator") or "") not in LIVE_TECHNIQUE_NAMES:
            raise LiveError("live horizon decision contains an unknown technique")
        layers = row.get("layers")
        if not isinstance(layers, list) or not layers:
            raise LiveError("live horizon decision requires layers")
        layer_ids = [str(layer.get("layer_id") or "") for layer in layers]
        if not all(layer_ids) or len(layer_ids) != len(set(layer_ids)):
            raise LiveError("live horizon decision layers must be unique")
    if committed != decisions[: len(committed)]:
        raise LiveError("committed live decisions must be the horizon prefix")
    expected = midi_sha256_json({key: value for key, value in plan.items() if key != "plan_sha256"})
    if str(plan.get("plan_sha256") or "") != expected:
        raise LiveError("plan_sha256 does not match live plan contents")


def live_plan_next(
    atlas: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    horizon_bars: int = 0,
    commit_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
) -> dict[str, Any]:
    live_validate_atlas(atlas)
    live_validate_state(state)
    if str(state["atlas_sha256"]) != str(atlas["atlas_sha256"]):
        raise LiveError("live state belongs to another material atlas")
    if beam_width <= 0 or candidate_limit <= 0:
        raise LiveError("live planner beam and candidate limits must be positive")
    resolved = live_apply_pending_persona(state)
    policy = live_persona_policy(str(resolved["current_persona"]))
    horizon = int(horizon_bars or resolved["horizon_bars"])
    commit = int(commit_bars or resolved["phrase_bars"])
    if horizon <= 0 or commit <= 0 or commit > horizon:
        raise LiveError("live planner requires 0 < commit_bars <= horizon_bars")
    patterns = [deepcopy(dict(row)) for row in atlas["patterns"] if str(row["pattern_id"]) not in set(resolved["skipped_pattern_ids"])]
    if not patterns:
        raise LiveError("all live patterns are skipped")
    pattern_by_id = {str(row["pattern_id"]): row for row in patterns}
    enabled = [str(value) for value in resolved["enabled_techniques"]]
    if resolved.get("forced_technique"):
        enabled = [str(resolved["forced_technique"])]
    if resolved.get("hold_active"):
        enabled = ["loop_extend"]
    candidate_evaluations = 0
    beam = [
        {
            "score": 0.0,
            "active_layers": deepcopy(list(resolved["active_layers"])),
            "recent_patterns": list(resolved["recent_pattern_ids"]),
            "decisions": [],
        }
    ]

    for step in range(horizon):
        bar_index = int(resolved["current_bar_index"]) + step
        target_energy = _live_target_energy(resolved, bar_index)
        target_layers = _live_target_layers(resolved, policy, target_energy)
        ranked_patterns = sorted(
            patterns,
            key=lambda pattern: _live_pattern_rank(
                pattern,
                target_energy=target_energy,
                priorities=policy["category_priority"],
                target_layers=target_layers,
                recent_patterns=resolved["recent_pattern_ids"],
                seed=int(resolved["seed"]),
                bar_index=bar_index,
            ),
        )[:candidate_limit]
        expansions = []
        for node in beam:
            for technique in enabled:
                operator_patterns = ranked_patterns[:1] if technique in {"loop_extend", "echo_out"} and node["active_layers"] else ranked_patterns
                for pattern in operator_patterns:
                    candidate_evaluations += 1
                    result = live_apply_technique(
                        technique,
                        active_layers=node["active_layers"],
                        candidate_pattern=pattern,
                        maximum_layers=target_layers,
                        category_priority=policy["category_priority"],
                    )
                    if not result["compatible"]:
                        continue
                    score, terms = _live_score_candidate(
                        result=result,
                        active_layers=node["active_layers"],
                        target_energy=target_energy,
                        target_layers=target_layers,
                        policy=policy,
                        recent_patterns=node["recent_patterns"],
                        seed=int(resolved["seed"]),
                        bar_index=bar_index,
                        pattern_by_id=pattern_by_id,
                    )
                    decision = {
                        "bar_index": bar_index,
                        "persona": str(resolved["current_persona"]),
                        "operator": technique,
                        "target_energy": round(target_energy, 9),
                        "estimated_energy": terms["estimated_energy"],
                        "target_layers": target_layers,
                        "candidate_pattern_id": str(pattern["pattern_id"]),
                        "pattern_ids": list(result["pattern_ids"]),
                        "layers": deepcopy(result["layers"]),
                        "commands": deepcopy(result["commands"]),
                        "velocity_scale": round(float(result["velocity_scale"]), 9),
                        "operator_risk": round(float(result["risk"]), 9),
                        "score": score,
                        "score_terms": terms,
                        "alternatives": [],
                    }
                    expansions.append(
                        {
                            "score": float(node["score"]) + score,
                            "active_layers": deepcopy(result["layers"]),
                            "recent_patterns": [*node["recent_patterns"], *result["pattern_ids"]][-64:],
                            "decisions": [*node["decisions"], decision],
                        }
                    )
        if not expansions:
            raise LiveError(f"no enabled live technique can satisfy bar {bar_index + 1}")
        expansions.sort(
            key=lambda node: (
                -float(node["score"]),
                tuple(str(row["operator"]) for row in node["decisions"]),
                tuple(tuple(str(value) for value in row["pattern_ids"]) for row in node["decisions"]),
            )
        )
        alternatives = [
            {
                "operator": str(node["decisions"][-1]["operator"]),
                "pattern_ids": list(node["decisions"][-1]["pattern_ids"]),
                "score": float(node["decisions"][-1]["score"]),
                "cumulative_score": round(float(node["score"]), 9),
                "roles": sorted({str(layer["role"]) for layer in node["decisions"][-1]["layers"]}),
            }
            for node in expansions[:5]
        ]
        for node in expansions[:beam_width]:
            node["decisions"][-1]["alternatives"] = deepcopy(alternatives)
        beam = expansions[:beam_width]

    best = beam[0]
    plan = {
        "schema_version": LIVE_PLAN_SCHEMA_VERSION,
        "kind": LIVE_PLAN_KIND,
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "state_before_sha256": str(resolved["state_sha256"]),
        "state_before_revision": int(resolved["state_revision"]),
        "start_bar_index": int(resolved["current_bar_index"]),
        "horizon_bars": horizon,
        "commit_bars": commit,
        "persona": str(resolved["current_persona"]),
        "pending_persona": resolved.get("pending_persona"),
        "enabled_techniques": list(enabled),
        "beam_width": int(beam_width),
        "candidate_limit": int(candidate_limit),
        "candidate_evaluations": candidate_evaluations,
        "cumulative_score": round(float(best["score"]), 9),
        "decisions": deepcopy(best["decisions"]),
        "committed_decisions": deepcopy(best["decisions"][:commit]),
    }
    plan["plan_sha256"] = midi_sha256_json(plan)
    live_validate_horizon_plan(plan)
    return {"plan": plan, "resolved_state": resolved}


def live_validate_session_plan(session: Mapping[str, Any]) -> None:
    if int(session.get("schema_version") or 0) != LIVE_SESSION_SCHEMA_VERSION:
        raise LiveError(f"unsupported live session schema: {session.get('schema_version')}")
    if str(session.get("kind") or "") != LIVE_SESSION_KIND:
        raise LiveError(f"unsupported live session kind: {session.get('kind')}")
    decisions = session.get("decisions")
    plans = session.get("horizon_plans")
    if not isinstance(decisions, list) or not decisions:
        raise LiveError("live session requires committed decisions")
    if not isinstance(plans, list) or not plans:
        raise LiveError("live session requires horizon plans")
    for index, row in enumerate(decisions):
        if int(row.get("bar_index", -1)) != index:
            raise LiveError("live session decisions must start at bar zero and remain contiguous")
    if len(decisions) != int(session.get("target_bars") or 0):
        raise LiveError("live session target_bars does not match decisions")
    expected = midi_sha256_json({key: value for key, value in session.items() if key != "session_sha256"})
    if str(session.get("session_sha256") or "") != expected:
        raise LiveError("session_sha256 does not match live session contents")


def live_plan_session(
    source_ledger: Mapping[str, Any],
    *,
    target_bars: int = 64,
    persona: str = "club",
    seed: int = 1,
    controls: Sequence[Mapping[str, Any]] | None = None,
    target_energy: float | None = None,
    density: float | None = None,
    risk: float | None = None,
    maximum_layers: int | None = None,
    horizon_bars: int = 0,
    phrase_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
) -> dict[str, Any]:
    midi_validate_ledger(source_ledger)
    if int(target_bars) <= 0:
        raise LiveError("live target_bars must be positive")
    atlas = live_atlas_from_midi(source_ledger)
    state = live_new_state(
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
    ordered_controls = [deepcopy(dict(row)) for row in (controls or [])]
    ordered_controls.sort(key=lambda row: (int(row.get("at_bar", 0)), str(row.get("command") or ""), midi_sha256_json(row)))
    applied: set[int] = set()
    decisions = []
    horizon_plans = []
    state_history = [deepcopy(state)]

    while int(state["current_bar_index"]) < int(target_bars):
        current = int(state["current_bar_index"])
        for ordinal, control in enumerate(ordered_controls):
            if ordinal in applied or int(control.get("at_bar", 0)) > current:
                continue
            state = live_apply_control(state, control, known_techniques=LIVE_TECHNIQUE_NAMES)
            applied.add(ordinal)
        state = live_apply_pending_persona(state)
        remaining = int(target_bars) - int(state["current_bar_index"])
        commit = min(int(state["phrase_bars"]), remaining)
        horizon = min(max(commit, int(state["horizon_bars"])), remaining)
        planned = live_plan_next(
            atlas,
            state,
            horizon_bars=horizon,
            commit_bars=commit,
            beam_width=beam_width,
            candidate_limit=candidate_limit,
        )
        plan = planned["plan"]
        state = live_advance_state(planned["resolved_state"], plan["committed_decisions"], plan_sha256=plan["plan_sha256"])
        decisions.extend(deepcopy(plan["committed_decisions"]))
        horizon_plans.append(
            {
                "plan_sha256": str(plan["plan_sha256"]),
                "start_bar_index": int(plan["start_bar_index"]),
                "horizon_bars": int(plan["horizon_bars"]),
                "commit_bars": int(plan["commit_bars"]),
                "persona": str(plan["persona"]),
                "candidate_evaluations": int(plan["candidate_evaluations"]),
                "cumulative_score": float(plan["cumulative_score"]),
            }
        )
        state_history.append(deepcopy(state))

    session = {
        "schema_version": LIVE_SESSION_SCHEMA_VERSION,
        "kind": LIVE_SESSION_KIND,
        "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "target_bars": int(target_bars),
        "seed": int(seed),
        "initial_persona": str(persona),
        "controls": ordered_controls,
        "applied_control_count": len(applied),
        "horizon_plans": horizon_plans,
        "decisions": decisions,
        "final_state": deepcopy(state),
        "state_history_sha256": midi_sha256_json(state_history),
        "runtime_capability": live_runtime_capability(),
    }
    session["session_sha256"] = midi_sha256_json(session)
    live_validate_session_plan(session)
    return {"atlas": atlas, "session": session, "final_state": state, "state_history": state_history}
