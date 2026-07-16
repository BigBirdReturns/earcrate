from earcrate.core.deps import *
from earcrate.core.util import json_dumps, sha256_text
from earcrate.tastespec.profiles import load_tastespec, tastespec_hash
from earcrate.judge.audio import GT_SPECTRAL_PROFILE
from earcrate.project.model import ProjectValidationError


_REQUIRED_PROFILE_FIELDS = {
    "id", "version", "provenance", "permitted_roles", "role_salience",
    "hard_constraints", "objective_weights", "compatibility_relations",
    "transition_grammar", "transform_budgets", "coverage_obligations",
    "source_turnover", "foreground_limits", "low_end_ownership",
    "masking_intelligibility", "acceptance_corpus_metrics",
}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = _copy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = _copy(value)
    return out


def _derive_mix_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    density = raw.get("density_model") or {}
    masking = raw.get("masking_intelligibility") or {}
    low = raw.get("low_end_ownership") or {}
    max_layers = max(1, int(density.get("max_layers") or 4))
    min_layers = max(1, int(density.get("min_layers") or 2))
    mode = str(raw.get("mode") or "collage")
    sparse = max_layers <= 2
    foreground_target = -4.5 if sparse else -5.8
    floor_target = -7.2 if sparse else -8.5
    spark_target = -16.0 if sparse else -14.0
    if mode == "remix":
        foreground_target += 0.5
    duck_required = bool(low.get("bass_ducking_required", True))
    intelligibility = float(masking.get("min_vocal_intelligibility") or 0.5)
    duck_target = 0.0 if not duck_required else min(4.0, 1.5 + 2.0 * intelligibility)
    return {
        "role_gain_db": {
            "floor": {"min": -24.0, "target": floor_target, "max": 4.0},
            "foreground": {"min": -18.0, "target": foreground_target, "max": 12.0},
            "spark": {"min": -32.0, "target": spark_target, "max": 2.0},
        },
        "vocal_bed_duck_db": {"min": 0.0, "target": duck_target, "max": 4.0},
        "fade_ms": {"min": 8.0, "target": 18.0, "max": 120.0},
        "energy_trim_span_db": 8.0 if max_layers >= 3 else 6.0,
        "pan_max_abs": 0.35 if max_layers >= 3 else 0.20,
        "sidechain_lowpass_hz": 3000.0,
        "sidechain_attack_ms": 10.0,
        "sidechain_release_ms": 180.0,
        "derivation": {
            "density_min_layers": min_layers,
            "density_max_layers": max_layers,
            "mode": mode,
            "min_vocal_intelligibility": intelligibility,
            "bass_ducking_required": duck_required,
        },
    }


def _derive_transition_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    grammar = raw.get("transition_grammar") or {}
    allowed = [str(x) for x in (grammar.get("allowed") or [])]
    # Keep every authored name. Add the canonical robust-cut spelling used by the
    # score editor without deleting the legacy renderer's two hard-cut gestures.
    if "hard_cut" not in allowed:
        allowed.append("hard_cut")
    defaults = {
        "start": (0.0, "none", []),
        "hard_cut": (0.0, "none", []),
        "hard_cut_pickup": (0.0, "none", []),
        "hard_cut_to_air": (0.0, "none", []),
        "impact_drop": (0.0, "none", []),
        "bed_ride": (0.0, "none", []),
        "echo_out": (4.0, "s_curve", ["outgoing_audio"]),
        "beatmatch_blend": (8.0, "equal_power", ["outgoing_tail"]),
        "hook_blend_over_bed": (4.0, "equal_power", ["outgoing_tail", "preserved_bed"]),
        "acapella_bridge": (4.0, "s_curve", ["vocal_material"]),
        "bass_swap": (8.0, "equal_power", ["outgoing_tail", "one_low_owner"]),
    }
    techniques: Dict[str, Any] = {}
    for name in allowed:
        duration, curve, required = defaults.get(name, (4.0, "equal_power", ["outgoing_tail"]))
        techniques[name] = {
            "duration_beats": {"min": 0.0 if duration == 0 else max(1.0, duration / 2.0),
                               "target": duration, "max": 0.0 if duration == 0 else duration * 2.0},
            "curve": curve,
            "required_capabilities": list(required),
        }
    return {
        "allowed": allowed,
        "techniques": techniques,
        "authored": _copy(grammar),
    }


def _spectral_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    merged = {k: dict(v) for k, v in GT_SPECTRAL_PROFILE.items()}
    for band, values in (raw.get("spectral_target") or {}).items():
        if isinstance(values, dict):
            merged.setdefault(str(band), {}).update(values)
    # Low correction needs an explicit target. Existing profiles authored a
    # warning/ceiling contract, so the default target remains the measured GT mean.
    merged.setdefault("low200_share", {}).setdefault("target", 0.20)
    return merged


def _derive_mastering_policy(raw: Dict[str, Any], spectral: Dict[str, Any]) -> Dict[str, Any]:
    presence = spectral["high3000_share"]
    return {
        "integrated_lufs": -14.0,
        "peak_ceiling": 0.891,
        "low_shelf": {
            "allowed": True,
            "target_share": float(spectral["low200_share"].get("target", 0.20)),
            "ceiling_warn": float(spectral["low200_share"].get("ceiling_warn", 0.34)),
            "max_cut_db": 14.0,
        },
        "presence_shelf": {
            "allowed": True,
            "lower_knee_hz": 3000.0,
            "upper_knee_hz": 4000.0,
            "floor_warn": float(presence.get("floor_warn", 0.15)),
            "floor_fail": float(presence.get("floor_fail", float(presence.get("floor_warn", 0.15)) * 0.6)),
            "target_share": min(1.0, float(presence.get("floor_warn", 0.15)) + 0.01),
            "system_ceiling_db": 6.0,
            "derive_persona_cap_from_spectral_target": True,
        },
        "max_machine_revisions": 1,
    }


def compile_taste_policy(profile_id: str) -> Dict[str, Any]:
    raw = load_tastespec(str(profile_id))
    missing = sorted(_REQUIRED_PROFILE_FIELDS - set(raw))
    if missing:
        raise ProjectValidationError(
            f"TasteSpec {profile_id!r} missing required fields: {', '.join(missing)}"
        )
    source_hash = tastespec_hash(raw)
    spectral = _spectral_profile(raw)
    derived_paths: List[str] = []
    mix_policy = raw.get("mix_policy")
    if not isinstance(mix_policy, dict):
        mix_policy = _derive_mix_policy(raw)
        derived_paths.append("mix_policy")
    mastering_policy = raw.get("mastering_policy")
    if not isinstance(mastering_policy, dict):
        mastering_policy = _derive_mastering_policy(raw, spectral)
        derived_paths.append("mastering_policy")
    transition_policy = _derive_transition_policy(raw)
    if "tempo_target" not in raw:
        derived_paths.append("tempo_target")
    tempo = _copy(raw.get("tempo_target") or {
        "bpm_low": 70.0, "bpm_high": 180.0, "preferred_bpm": None,
        "derivation": "unconstrained deck; choose_taste_deck remains authoritative",
    })
    if "groove_target" not in raw:
        derived_paths.append("groove_target")
    groove = _copy(raw.get("groove_target") or {
        "feel": "source_native", "derivation": "preserve measured source groove",
    })
    if "spectral_target" not in raw:
        derived_paths.append("spectral_target")

    consumers = {
        "id": "project identity and output naming",
        "version": "immutable TasteSpec receipt",
        "provenance": "human-facing evidence ledger",
        "permitted_roles": "EarAtom and clip-role validation",
        "role_salience": "EarAtom curation and source ranking",
        "hard_constraints": "pre-render and publication vetoes",
        "objective_weights": "crate ranking and compatibility scoring",
        "compatibility_relations": "typed compatibility graph",
        "transition_grammar": "transition candidate and render-capability validation",
        "transform_budgets": "deck feasibility and exact transform validation",
        "coverage_obligations": "TasteSpec arrangement gate",
        "source_turnover": "composer source rotation and gate",
        "density_model": "layer budget and candidate-search breadth",
        "foreground_limits": "foreground reuse and duration gates",
        "low_end_ownership": "bass ownership and ducking program",
        "masking_intelligibility": "pair scoring and sidechain depth",
        "acceptance_corpus_metrics": "determinism and acceptance receipts",
        "endless_contract": "resident/readiness no-repeat contract",
        "min_edge_score": "compatibility graph threshold",
        "name": "resident and project display identity",
        "contract": "human-facing project contract",
        "mode": "compile request mode",
        "tempo_target": "deck search envelope",
        "groove_target": "score-sheet groove intent",
        "spectral_target": "mastering and verification profile",
        "mix_policy": "clip gains, fades, pan and sidechain automation",
        "mastering_policy": "explicit mastering action resolver",
    }
    unaccounted = sorted(set(raw) - set(consumers) - {"hash"})
    if unaccounted:
        raise ProjectValidationError(
            "TasteSpec contains unaccounted top-level fields: " + ", ".join(unaccounted)
        )
    compiled = {
        "profile_id": str(raw["id"]),
        "version": str(raw["version"]),
        "source_profile_hash": source_hash,
        "name": str(raw.get("name") or raw["id"]),
        "contract": str(raw.get("contract") or ""),
        "mode": str(raw.get("mode") or "taste_compiler"),
        "permitted_roles": list(raw["permitted_roles"]),
        "role_salience": _copy(raw["role_salience"]),
        "hard_constraints": _copy(raw["hard_constraints"]),
        "objective_weights": _copy(raw["objective_weights"]),
        "compatibility_relations": _copy(raw["compatibility_relations"]),
        "transition_policy": transition_policy,
        "transform_budgets": _copy(raw["transform_budgets"]),
        "coverage_obligations": _copy(raw["coverage_obligations"]),
        "source_turnover": _copy(raw["source_turnover"]),
        "density_model": _copy(raw.get("density_model") or {}),
        "foreground_limits": _copy(raw["foreground_limits"]),
        "low_end_ownership": _copy(raw["low_end_ownership"]),
        "masking_intelligibility": _copy(raw["masking_intelligibility"]),
        "acceptance_corpus_metrics": _copy(raw["acceptance_corpus_metrics"]),
        "endless_contract": _copy(raw.get("endless_contract") or {}),
        "min_edge_score": float(raw.get("min_edge_score") or 0.54),
        "tempo_target": tempo,
        "groove_target": groove,
        "spectral_profile": spectral,
        "mix_policy": _copy(mix_policy),
        "mastering_policy": _copy(mastering_policy),
        "raw_profile": _copy({k: v for k, v in raw.items() if k != "hash"}),
        "consumers": consumers,
        "derivation_receipt": {
            "derived_paths": derived_paths,
            "source_profile_hash": source_hash,
            "rule": "existing persona data stays authoritative; missing actuator envelopes are derived and receipted, never silently invented",
        },
    }
    compiled["compiled_policy_sha"] = sha256_text(json_dumps(compiled))
    return compiled


def policy_gain_bounds(policy: Dict[str, Any], rail: str) -> Tuple[float, float, float]:
    row = ((policy.get("mix_policy") or {}).get("role_gain_db") or {}).get(str(rail))
    if not isinstance(row, dict):
        raise ProjectValidationError(f"compiled policy has no gain range for rail {rail!r}")
    lo, target, hi = float(row["min"]), float(row["target"]), float(row["max"])
    if not lo <= target <= hi:
        raise ProjectValidationError(f"invalid gain envelope for rail {rail!r}")
    return lo, target, hi
