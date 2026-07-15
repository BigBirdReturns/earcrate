from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Mapping

from .util import ValidationError, clamp, merge_dict, read_json, sha256_json

REQUIRED_PROFILE_FIELDS = (
    "id",
    "version",
    "provenance",
    "permitted_roles",
    "role_salience",
    "hard_constraints",
    "objective_weights",
    "compatibility_relations",
    "transition_grammar",
    "transform_budgets",
    "coverage_obligations",
    "source_turnover",
    "foreground_limits",
    "low_end_ownership",
    "masking_intelligibility",
    "acceptance_corpus_metrics",
)

FIELD_CONSUMERS = {
    "id": "revision identity and CLI selection",
    "version": "revision identity and deterministic migration",
    "provenance": "compiler receipt and project sheet",
    "permitted_roles": "candidate admissibility",
    "role_salience": "role-specific candidate score and minimums",
    "hard_constraints": "static gate and lowering vetoes",
    "objective_weights": "candidate and beam-search objective",
    "compatibility_relations": "typed pair scoring",
    "transition_grammar": "transition candidate admissibility",
    "transform_budgets": "pre-compose transform feasibility",
    "coverage_obligations": "form and rail coverage gates",
    "source_turnover": "hold time, reuse penalty, and gate",
    "foreground_limits": "foreground reuse and duration gates",
    "low_end_ownership": "bass selection and transition policy",
    "masking_intelligibility": "vocal-over-bed compatibility",
    "acceptance_corpus_metrics": "verification expectations",
    "density_model": "section density, layer budget, and event cadence",
    "tempo_target": "deck candidate scoring",
    "groove_target": "compiler receipt and transition preference",
    "spectral_target": "material provenance, mastering limits, and post-render gate",
    "endless_contract": "source recycle diagnostics",
    "mix_policy": "clip gain, ducking, fade, and pan envelopes",
    "mastering_policy": "explicit master-action bounds",
}


def _profile_dirs(extra: Path | None = None) -> list[Path]:
    out: list[Path] = []
    env = str(os.environ.get("EARCRATE_PROFILE_DIR") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    if extra is not None:
        out.append(extra)
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        out.append(parent / "profiles")
    out.append(Path.cwd() / "profiles")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in out:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_profile(profile: str | Path | Mapping[str, Any], profile_dir: Path | None = None) -> dict[str, Any]:
    if isinstance(profile, Mapping):
        data = dict(profile)
    else:
        raw = Path(str(profile)).expanduser()
        if raw.exists():
            data = read_json(raw)
        else:
            found = None
            name = str(profile)
            filename = name if name.endswith(".json") else f"{name}.json"
            for directory in _profile_dirs(profile_dir):
                candidate = directory / filename
                if candidate.exists():
                    found = candidate
                    break
            if found is None:
                # Use the existing buffalo loader when package mode has embedded profiles.
                try:
                    from earcrate.tastespec import load_tastespec  # type: ignore

                    data = dict(load_tastespec(name))
                except Exception as exc:
                    raise ValidationError(f"TasteSpec profile not found: {profile}") from exc
            else:
                data = read_json(found)
    missing = [field for field in REQUIRED_PROFILE_FIELDS if field not in data]
    if missing:
        raise ValidationError(f"TasteSpec profile missing required fields: {', '.join(missing)}")
    clean = {key: value for key, value in data.items() if key != "hash"}
    data["hash"] = sha256_json(clean)
    return data


def _normalise_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    parsed = {str(key): max(0.0, float(value)) for key, value in weights.items()}
    total = sum(parsed.values())
    if total <= 0:
        raise ValidationError("objective_weights must contain at least one positive value")
    return {key: value / total for key, value in sorted(parsed.items())}


def _log_odds_gain_db(target: float, floor_fail: float) -> float:
    def odds(value: float) -> float:
        value = clamp(float(value), 1e-9, 1.0 - 1e-9)
        return value / (1.0 - value)

    return 10.0 * math.log10(odds(target) / odds(floor_fail))


def _derive_mix_policy(profile: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    density = profile.get("density_model") or {}
    role_salience = profile.get("role_salience") or {}
    masking = profile.get("masking_intelligibility") or {}
    low_end = profile.get("low_end_ownership") or {}
    max_layers = max(2, int(density.get("max_layers") or 4))
    min_layers = max(1, int(density.get("min_layers") or 2))
    density_span = clamp((max_layers - 2) / 4.0, 0.0, 1.0)
    vocal_floor = float(masking.get("min_vocal_intelligibility") or 0.5)

    floor_target = -8.0 - 1.4 * density_span
    foreground_target = -6.7 + 1.8 * clamp((vocal_floor - 0.45) / 0.35, 0.0, 1.0)
    spark_target = -16.5 + 3.5 * density_span
    bass_target = floor_target + 0.5
    duck_required = bool(low_end.get("bass_ducking_required"))
    duck_target = 2.5 if duck_required else 1.25
    fade_target_ms = 18 + int(round(12 * density_span))

    policy = {
        "role_gain_db": {
            "floor": {"min": floor_target - 8.0, "target": floor_target, "max": floor_target + 5.0},
            "foreground": {"min": foreground_target - 7.0, "target": foreground_target, "max": min(2.0, foreground_target + 7.0)},
            "spark": {"min": spark_target - 8.0, "target": spark_target, "max": spark_target + 7.0},
            "bass": {"min": bass_target - 6.0, "target": bass_target, "max": bass_target + 4.0},
            "aux": {"min": -24.0, "target": -14.0, "max": -6.0},
        },
        "vocal_bed_duck_db": {"min": 0.0, "target": duck_target, "max": 4.0 if duck_required else 2.5},
        "fade_ms": {"min": 8, "target": fade_target_ms, "max": 120},
        "pan": {"min": -1.0, "target": 0.0, "max": 1.0},
        "normalization": {
            "target_role_rms": {
                "floor": 0.11,
                "foreground": 0.105,
                "spark": 0.08,
                "bass": 0.10,
                "aux": 0.075,
            },
            "max_correction_db": 12.0,
        },
        "layer_budget": {"min": min_layers, "max": max_layers},
        "role_salience": role_salience,
    }
    derivations = [
        {
            "field": "mix_policy.role_gain_db",
            "formula": "density_model.max_layers controls floor attenuation and spark audibility; masking_intelligibility.min_vocal_intelligibility controls foreground target",
            "inputs": {"max_layers": max_layers, "min_layers": min_layers, "min_vocal_intelligibility": vocal_floor},
            "result": policy["role_gain_db"],
        },
        {
            "field": "mix_policy.vocal_bed_duck_db",
            "formula": "low_end_ownership.bass_ducking_required selects a bounded 2.5 dB target, otherwise 1.25 dB",
            "inputs": {"bass_ducking_required": duck_required},
            "result": policy["vocal_bed_duck_db"],
        },
    ]
    return policy, derivations


def _derive_mastering_policy(profile: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spectral = profile.get("spectral_target") or {}
    high = spectral.get("high3000_share") or {"target": 0.30, "floor_warn": 0.15, "floor_fail": 0.09}
    low = spectral.get("low200_share") or {"ceiling_warn": 0.34, "ceiling_fail": 0.45, "floor_warn": 0.05}
    high_floor = float(high.get("floor_warn") or high.get("target") or 0.15)
    high_target = min(1.0 - 1e-6, high_floor + 0.01)
    high_fail = float(high.get("floor_fail") or max(0.01, high_floor * 0.6))
    persona_reach = max(0.0, _log_odds_gain_db(high_target, high_fail))
    system_ceiling = 6.0
    policy = {
        "integrated_lufs": -14.0,
        "true_peak": 0.94,
        "low_shelf": {
            "allowed": True,
            "boundary_hz": 200.0,
            "target_share": min(0.20, float(low.get("ceiling_warn") or 0.34)),
            "trigger_share": float(low.get("ceiling_warn") or 0.34),
            "max_cut_db": 14.0,
        },
        "presence_shelf": {
            "allowed": True,
            "measurement_boundary_hz": 3000.0,
            "lower_knee_hz": 3000.0,
            "upper_knee_hz": 4000.0,
            "target_share": high_target,
            "trigger_share": high_floor,
            "floor_fail": high_fail,
            "persona_reach_db": persona_reach,
            "system_ceiling_db": system_ceiling,
            "max_boost_db": min(system_ceiling, persona_reach),
            "unreachable_action": "refuse",
        },
    }
    derivations = [
        {
            "field": "mastering_policy.presence_shelf.max_boost_db",
            "formula": "min(6 dB system ceiling, 10*log10(odds(floor_warn+0.01)/odds(floor_fail)))",
            "inputs": {"floor_warn": high_floor, "floor_fail": high_fail, "target_share": high_target},
            "result": policy["presence_shelf"]["max_boost_db"],
        }
    ]
    return policy, derivations


def compile_policy(profile: str | Path | Mapping[str, Any], profile_dir: Path | None = None) -> dict[str, Any]:
    raw = load_profile(profile, profile_dir=profile_dir)
    mix_derived, mix_derivations = _derive_mix_policy(raw)
    master_derived, master_derivations = _derive_mastering_policy(raw)
    mix_policy = merge_dict(mix_derived, raw.get("mix_policy") or {})
    mastering_policy = merge_dict(master_derived, raw.get("mastering_policy") or {})

    transitions = raw.get("transition_grammar") or {}
    allowed = [str(item) for item in transitions.get("allowed") or ["hard_cut"]]
    if not allowed:
        raise ValidationError("transition_grammar.allowed cannot be empty")

    density = raw.get("density_model") or {}
    if not density:
        # Older profiles are valid but this absence is explicit in the receipt.
        density = {"seconds_per_event": 11.0, "sources_per_minute": 5.5, "min_layers": 2, "max_layers": 4}
    spectral = raw.get("spectral_target") or {
        "rms_std_db": {"target": 5.0, "floor": 3.5},
        "low200_share": {"ceiling_fail": 0.45, "ceiling_warn": 0.34, "floor_warn": 0.05},
        "high3000_share": {"target": 0.30, "floor_warn": 0.15, "floor_fail": 0.09},
    }

    compiled = {
        "schema_version": 1,
        "profile": {"id": raw["id"], "version": raw["version"], "hash": raw["hash"]},
        "provenance": raw.get("provenance") or {},
        "permitted_roles": sorted(str(role) for role in raw.get("permitted_roles") or []),
        "role_salience": raw.get("role_salience") or {},
        "hard_constraints": raw.get("hard_constraints") or {},
        "objective_weights": _normalise_weights(raw.get("objective_weights") or {}),
        "compatibility_relations": raw.get("compatibility_relations") or {},
        "transition_policy": {
            "allowed": allowed,
            "preferred": [str(x) for x in transitions.get("preferred") or allowed],
            "duration_beats": merge_dict(
                {
                    "hard_cut": 0.0,
                    "hard_cut_pickup": 0.0,
                    "hard_cut_to_air": 0.0,
                    "impact_drop": 0.0,
                    "beatmatch_blend": 4.0,
                    "hook_blend_over_bed": 4.0,
                    "long_blend": 16.0,
                    "echo_out": 4.0,
                    "acapella_bridge": 2.0,
                    "bass_swap": 8.0,
                    "double_drop": 0.0,
                },
                transitions.get("duration_beats") or {},
            ),
            "fallback_forbidden": True,
        },
        "transform_budgets": raw.get("transform_budgets") or {},
        "coverage": raw.get("coverage_obligations") or {},
        "turnover": raw.get("source_turnover") or {},
        "foreground_limits": raw.get("foreground_limits") or {},
        "low_end": raw.get("low_end_ownership") or {},
        "masking": raw.get("masking_intelligibility") or {},
        "acceptance": raw.get("acceptance_corpus_metrics") or {},
        "density": density,
        "tempo": raw.get("tempo_target") or {},
        "groove": raw.get("groove_target") or {},
        "spectral": spectral,
        "endless": raw.get("endless_contract") or {},
        "mix": mix_policy,
        "mastering": mastering_policy,
    }
    compiled_sha = sha256_json(compiled)
    consumed = []
    for field in sorted(raw):
        if field == "hash":
            continue
        consumer = FIELD_CONSUMERS.get(field)
        consumed.append({
            "field": field,
            "consumer": consumer or "preserved in profile provenance; no actuator consumer declared",
            "status": "consumed" if consumer else "preserved_unconsumed",
        })
    unconsumed_required = [entry["field"] for entry in consumed if entry["status"] != "consumed" and entry["field"] in REQUIRED_PROFILE_FIELDS]
    if unconsumed_required:
        raise ValidationError(f"required TasteSpec field(s) have no consumer: {', '.join(unconsumed_required)}")
    return {
        "raw_profile": raw,
        "compiled_policy": compiled,
        "compiled_policy_sha": compiled_sha,
        "receipt": {
            "profile_id": raw["id"],
            "profile_version": raw["version"],
            "profile_hash": raw["hash"],
            "compiled_policy_sha": compiled_sha,
            "consumed_fields": consumed,
            "derivations": mix_derivations + master_derivations,
            "all_required_fields_consumed": True,
        },
    }


def policy_range(policy: Mapping[str, Any], rail: str) -> dict[str, float]:
    gains = ((policy.get("mix") or {}).get("role_gain_db") or {})
    if rail not in gains:
        rail = "aux"
    row = gains.get(rail) or {"min": -24.0, "target": -12.0, "max": 0.0}
    return {key: float(row[key]) for key in ("min", "target", "max")}


def assert_value_in_policy_range(policy: Mapping[str, Any], rail: str, value: float, field: str) -> None:
    row = policy_range(policy, rail)
    if value < row["min"] - 1e-9 or value > row["max"] + 1e-9:
        raise ValidationError(f"{field}={value:.3f} dB is outside {rail} policy range [{row['min']:.3f}, {row['max']:.3f}]")
