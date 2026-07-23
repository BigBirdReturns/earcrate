from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.midi.model import midi_sha256_json

LIVE_STATE_SCHEMA_VERSION = 1
LIVE_STATE_KIND = "earcrate_live_set_state"
LIVE_CONTROL_SCHEMA_VERSION = 1


class LiveError(ValueError):
    """Raised when the deterministic live-performance contract is invalid."""


_LIVE_PERSONAS: dict[str, dict[str, Any]] = {
    "club": {
        "name": "club",
        "phrase_bars": 4,
        "horizon_bars": 16,
        "default_energy": 0.62,
        "default_density": 0.90,
        "default_risk": 0.24,
        "maximum_layers": 4,
        "turnover_preference": 0.28,
        "category_priority": ["floor", "bass", "harmony", "foreground", "fx"],
        "technique_weights": {
            "blend": 1.00,
            "hard_cut": 0.40,
            "loop_extend": 0.88,
            "drop_to_floor": 0.66,
            "foreground_swap": 0.72,
            "tease": 0.58,
            "build_layers": 0.74,
            "breakdown": 0.68,
            "echo_out": 0.62,
            "drum_rebuild": 0.70,
            "sample_chop": 0.28,
            "layer_accumulation": 0.54,
        },
        "score_weights": {
            "technique": 0.15,
            "energy_fit": 0.24,
            "density_fit": 0.16,
            "role_coverage": 0.17,
            "continuity_fit": 0.14,
            "novelty": 0.05,
            "risk_fit": 0.07,
            "source_diversity": 0.02,
        },
    },
    "girl_talk": {
        "name": "girl_talk",
        "phrase_bars": 4,
        "horizon_bars": 12,
        "default_energy": 0.80,
        "default_density": 1.35,
        "default_risk": 0.78,
        "maximum_layers": 6,
        "turnover_preference": 0.88,
        "category_priority": ["floor", "foreground", "bass", "harmony", "fx"],
        "technique_weights": {
            "blend": 0.78,
            "hard_cut": 1.00,
            "loop_extend": 0.30,
            "drop_to_floor": 0.76,
            "foreground_swap": 1.00,
            "tease": 0.90,
            "build_layers": 0.70,
            "breakdown": 0.44,
            "echo_out": 0.56,
            "drum_rebuild": 0.78,
            "sample_chop": 1.00,
            "layer_accumulation": 0.72,
        },
        "score_weights": {
            "technique": 0.18,
            "energy_fit": 0.17,
            "density_fit": 0.14,
            "role_coverage": 0.17,
            "continuity_fit": 0.06,
            "novelty": 0.13,
            "risk_fit": 0.09,
            "source_diversity": 0.06,
        },
    },
    "pretty_lights": {
        "name": "pretty_lights",
        "phrase_bars": 4,
        "horizon_bars": 16,
        "default_energy": 0.70,
        "default_density": 1.12,
        "default_risk": 0.46,
        "maximum_layers": 6,
        "turnover_preference": 0.38,
        "category_priority": ["floor", "bass", "harmony", "foreground", "fx"],
        "technique_weights": {
            "blend": 0.88,
            "hard_cut": 0.32,
            "loop_extend": 0.96,
            "drop_to_floor": 0.68,
            "foreground_swap": 0.64,
            "tease": 0.72,
            "build_layers": 1.00,
            "breakdown": 0.78,
            "echo_out": 0.76,
            "drum_rebuild": 0.94,
            "sample_chop": 0.86,
            "layer_accumulation": 1.00,
        },
        "score_weights": {
            "technique": 0.17,
            "energy_fit": 0.20,
            "density_fit": 0.14,
            "role_coverage": 0.16,
            "continuity_fit": 0.15,
            "novelty": 0.06,
            "risk_fit": 0.08,
            "source_diversity": 0.04,
        },
    },
    "minimal": {
        "name": "minimal",
        "phrase_bars": 4,
        "horizon_bars": 16,
        "default_energy": 0.46,
        "default_density": 0.58,
        "default_risk": 0.16,
        "maximum_layers": 3,
        "turnover_preference": 0.18,
        "category_priority": ["floor", "bass", "harmony", "foreground", "fx"],
        "technique_weights": {
            "blend": 0.80,
            "hard_cut": 0.18,
            "loop_extend": 1.00,
            "drop_to_floor": 0.84,
            "foreground_swap": 0.48,
            "tease": 0.52,
            "build_layers": 0.36,
            "breakdown": 0.90,
            "echo_out": 0.72,
            "drum_rebuild": 0.42,
            "sample_chop": 0.16,
            "layer_accumulation": 0.28,
        },
        "score_weights": {
            "technique": 0.15,
            "energy_fit": 0.25,
            "density_fit": 0.19,
            "role_coverage": 0.15,
            "continuity_fit": 0.17,
            "novelty": 0.03,
            "risk_fit": 0.05,
            "source_diversity": 0.01,
        },
    },
}


def live_persona_names() -> list[str]:
    return sorted(_LIVE_PERSONAS)


def live_persona_policy(name: str) -> dict[str, Any]:
    key = str(name or "").strip().lower()
    if key not in _LIVE_PERSONAS:
        raise LiveError(f"unknown live persona {name!r}; choose one of {live_persona_names()}")
    policy = deepcopy(_LIVE_PERSONAS[key])
    live_validate_persona_policy(policy)
    return policy


def live_validate_persona_policy(policy: Mapping[str, Any]) -> None:
    name = str(policy.get("name") or "")
    if not name:
        raise LiveError("persona policy requires a name")
    phrase_bars = int(policy.get("phrase_bars") or 0)
    horizon_bars = int(policy.get("horizon_bars") or 0)
    if phrase_bars <= 0 or horizon_bars < phrase_bars:
        raise LiveError(f"persona {name} has invalid phrase or horizon bars")
    if horizon_bars % phrase_bars:
        raise LiveError(f"persona {name} horizon must be a multiple of phrase bars")
    for field in ("default_energy", "default_risk", "turnover_preference"):
        value = float(policy.get(field, -1.0))
        if not 0.0 <= value <= 1.0:
            raise LiveError(f"persona {name} {field} must be in [0,1]")
    density = float(policy.get("default_density") or 0.0)
    if not 0.0 < density <= 2.0:
        raise LiveError(f"persona {name} default_density must be in (0,2]")
    maximum_layers = int(policy.get("maximum_layers") or 0)
    if not 1 <= maximum_layers <= 16:
        raise LiveError(f"persona {name} maximum_layers must be in [1,16]")
    techniques = policy.get("technique_weights")
    weights = policy.get("score_weights")
    priorities = policy.get("category_priority")
    if not isinstance(techniques, Mapping) or not techniques:
        raise LiveError(f"persona {name} requires technique weights")
    if not isinstance(weights, Mapping) or not weights:
        raise LiveError(f"persona {name} requires score weights")
    if not isinstance(priorities, list) or not priorities:
        raise LiveError(f"persona {name} requires category priorities")
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
        raise LiveError(f"persona {name} score weights must sum to 1")


def live_state_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(state))
    payload.pop("state_sha256", None)
    return payload


def live_compute_state_sha256(state: Mapping[str, Any]) -> str:
    return midi_sha256_json(live_state_payload(state))


def live_seal_state(state: Mapping[str, Any]) -> dict[str, Any]:
    out = live_state_payload(state)
    out["state_sha256"] = live_compute_state_sha256(out)
    live_validate_state(out)
    return out


def live_validate_state(state: Mapping[str, Any]) -> None:
    if int(state.get("schema_version") or 0) != LIVE_STATE_SCHEMA_VERSION:
        raise LiveError(f"unsupported live state schema: {state.get('schema_version')}")
    if str(state.get("kind") or "") != LIVE_STATE_KIND:
        raise LiveError(f"unsupported live state kind: {state.get('kind')}")
    if not str(state.get("atlas_sha256") or ""):
        raise LiveError("live state requires atlas_sha256")
    if int(state.get("current_bar_index", -1)) < 0:
        raise LiveError("live state current_bar_index cannot be negative")
    if int(state.get("state_revision", -1)) < 0:
        raise LiveError("live state revision cannot be negative")
    policy = live_persona_policy(str(state.get("current_persona") or ""))
    pending = state.get("pending_persona")
    if pending not in {None, ""}:
        live_persona_policy(str(pending))
    if not isinstance(state.get("enabled_techniques"), list):
        raise LiveError("live state enabled_techniques must be a list")
    if len(state["enabled_techniques"]) != len(set(str(value) for value in state["enabled_techniques"])):
        raise LiveError("live state enabled techniques must be unique")
    for field in ("target_energy", "risk"):
        value = float(state.get(field, -1.0))
        if not 0.0 <= value <= 1.0:
            raise LiveError(f"live state {field} must be in [0,1]")
    density = float(state.get("density") or 0.0)
    if not 0.0 < density <= 2.0:
        raise LiveError("live state density must be in (0,2]")
    maximum_layers = int(state.get("maximum_layers") or 0)
    if not 1 <= maximum_layers <= 16:
        raise LiveError("live state maximum_layers must be in [1,16]")
    phrase_bars = int(state.get("phrase_bars") or policy["phrase_bars"])
    horizon_bars = int(state.get("horizon_bars") or policy["horizon_bars"])
    if phrase_bars <= 0 or horizon_bars < phrase_bars:
        raise LiveError("live state phrase and horizon bars are invalid")
    if not isinstance(state.get("active_layers"), list):
        raise LiveError("live state active_layers must be a list")
    layer_ids = [str(row.get("layer_id") or "") for row in state["active_layers"]]
    if not all(layer_ids) or len(layer_ids) != len(set(layer_ids)):
        raise LiveError("live state active layers must have unique nonempty layer IDs")
    expected = live_compute_state_sha256(state)
    if str(state.get("state_sha256") or "") != expected:
        raise LiveError("state_sha256 does not match live state contents")


def live_new_state(
    atlas: Mapping[str, Any],
    *,
    persona: str = "club",
    seed: int = 1,
    target_energy: float | None = None,
    density: float | None = None,
    risk: float | None = None,
    maximum_layers: int | None = None,
    enabled_techniques: Sequence[str] | None = None,
    phrase_bars: int = 0,
    horizon_bars: int = 0,
) -> dict[str, Any]:
    policy = live_persona_policy(persona)
    techniques = sorted(
        str(value)
        for value in (
            enabled_techniques
            if enabled_techniques is not None
            else [name for name, weight in policy["technique_weights"].items() if float(weight) > 0.0]
        )
    )
    state = {
        "schema_version": LIVE_STATE_SCHEMA_VERSION,
        "kind": LIVE_STATE_KIND,
        "atlas_sha256": str(atlas.get("atlas_sha256") or ""),
        "state_revision": 0,
        "seed": int(seed),
        "current_bar_index": 0,
        "current_persona": str(policy["name"]),
        "pending_persona": None,
        "enabled_techniques": techniques,
        "target_energy": float(policy["default_energy"] if target_energy is None else target_energy),
        "density": float(policy["default_density"] if density is None else density),
        "risk": float(policy["default_risk"] if risk is None else risk),
        "maximum_layers": int(policy["maximum_layers"] if maximum_layers is None else maximum_layers),
        "phrase_bars": int(phrase_bars or policy["phrase_bars"]),
        "horizon_bars": int(horizon_bars or policy["horizon_bars"]),
        "active_layers": [],
        "current_operator": None,
        "hold_active": False,
        "forced_technique": None,
        "skipped_pattern_ids": [],
        "recent_pattern_ids": [],
        "recent_operator_names": [],
        "control_receipts": [],
        "last_committed_plan_sha256": None,
    }
    return live_seal_state(state)


def _live_lcm(left: int, right: int) -> int:
    return abs(int(left) * int(right)) // max(1, math.gcd(int(left), int(right)))


def live_apply_pending_persona(state: Mapping[str, Any]) -> dict[str, Any]:
    live_validate_state(state)
    out = live_state_payload(state)
    pending = out.get("pending_persona")
    if pending in {None, ""}:
        return live_seal_state(out)
    current_policy = live_persona_policy(str(out["current_persona"]))
    next_policy = live_persona_policy(str(pending))
    boundary = _live_lcm(int(current_policy["phrase_bars"]), int(next_policy["phrase_bars"]))
    if int(out["current_bar_index"]) % boundary:
        return live_seal_state(out)
    out["current_persona"] = str(next_policy["name"])
    out["pending_persona"] = None
    out["phrase_bars"] = int(next_policy["phrase_bars"])
    out["horizon_bars"] = int(next_policy["horizon_bars"])
    out["maximum_layers"] = min(int(out["maximum_layers"]), int(next_policy["maximum_layers"]))
    out["state_revision"] = int(out["state_revision"]) + 1
    return live_seal_state(out)


def live_apply_control(
    state: Mapping[str, Any],
    control: Mapping[str, Any],
    *,
    known_techniques: Sequence[str],
) -> dict[str, Any]:
    live_validate_state(state)
    out = live_state_payload(state)
    command = str(control.get("command") or "").strip().lower()
    value = control.get("value")
    known = set(str(name) for name in known_techniques)
    if command == "set_persona":
        out["pending_persona"] = str(live_persona_policy(str(value))["name"])
    elif command in {"enable_technique", "disable_technique", "force_technique"}:
        technique = str(value or "")
        if technique not in known:
            raise LiveError(f"unknown live technique: {technique}")
        enabled = set(str(name) for name in out["enabled_techniques"])
        if command == "enable_technique":
            enabled.add(technique)
            out["enabled_techniques"] = sorted(enabled)
        elif command == "disable_technique":
            enabled.discard(technique)
            if not enabled:
                raise LiveError("at least one live technique must remain enabled")
            out["enabled_techniques"] = sorted(enabled)
            if out.get("forced_technique") == technique:
                out["forced_technique"] = None
        else:
            if technique not in enabled:
                raise LiveError("forced technique must also be enabled")
            out["forced_technique"] = technique
    elif command == "clear_force":
        out["forced_technique"] = None
    elif command == "set_energy":
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise LiveError("energy must be in [0,1]")
        out["target_energy"] = number
    elif command == "set_density":
        number = float(value)
        if not 0.0 < number <= 2.0:
            raise LiveError("density must be in (0,2]")
        out["density"] = number
    elif command == "set_risk":
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise LiveError("risk must be in [0,1]")
        out["risk"] = number
    elif command == "set_maximum_layers":
        number = int(value)
        if not 1 <= number <= 16:
            raise LiveError("maximum layers must be in [1,16]")
        out["maximum_layers"] = number
    elif command == "hold":
        out["hold_active"] = True
    elif command == "release_hold":
        out["hold_active"] = False
    elif command == "skip_pattern":
        pattern_id = str(value or "")
        if not pattern_id:
            raise LiveError("skip_pattern requires a pattern ID")
        out["skipped_pattern_ids"] = sorted(set([*out["skipped_pattern_ids"], pattern_id]))
    elif command == "clear_skips":
        out["skipped_pattern_ids"] = []
    else:
        raise LiveError(f"unsupported live control command: {command}")
    receipt = {
        "schema_version": LIVE_CONTROL_SCHEMA_VERSION,
        "control_id": "live_control_" + midi_sha256_json(
            {
                "atlas_sha256": out["atlas_sha256"],
                "state_revision": out["state_revision"],
                "bar_index": out["current_bar_index"],
                "command": command,
                "value": value,
                "ordinal": len(out["control_receipts"]),
            }
        )[:24],
        "requested_bar_index": int(control.get("at_bar", out["current_bar_index"])),
        "applied_bar_index": int(out["current_bar_index"]),
        "command": command,
        "value": value,
    }
    out["control_receipts"] = [*out["control_receipts"], receipt]
    out["state_revision"] = int(out["state_revision"]) + 1
    return live_seal_state(out)


def live_advance_state(
    state: Mapping[str, Any],
    committed_decisions: Sequence[Mapping[str, Any]],
    *,
    plan_sha256: str,
) -> dict[str, Any]:
    live_validate_state(state)
    rows = [deepcopy(dict(row)) for row in committed_decisions]
    if not rows:
        raise LiveError("cannot advance a live state without committed decisions")
    expected = int(state["current_bar_index"])
    for row in rows:
        if int(row.get("bar_index", -1)) != expected:
            raise LiveError("committed live decisions are not contiguous with the state")
        expected += 1
    out = live_state_payload(state)
    final = rows[-1]
    out["current_bar_index"] = expected
    out["active_layers"] = deepcopy(list(final.get("layers") or []))
    out["current_operator"] = str(final.get("operator") or "") or None
    recent_patterns = [*out["recent_pattern_ids"]]
    recent_operators = [*out["recent_operator_names"]]
    for row in rows:
        recent_patterns.extend(str(value) for value in row.get("pattern_ids") or [])
        recent_operators.append(str(row.get("operator") or ""))
    out["recent_pattern_ids"] = recent_patterns[-64:]
    out["recent_operator_names"] = [value for value in recent_operators if value][-64:]
    out["forced_technique"] = None
    out["last_committed_plan_sha256"] = str(plan_sha256)
    out["state_revision"] = int(out["state_revision"]) + 1
    return live_seal_state(out)
