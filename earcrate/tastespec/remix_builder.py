"""Deterministic builder: a compact style fingerprint -> a schema-valid remix
TasteSpec. This is what makes the producer-roster fan-out SAFE: research agents
supply only STYLE NUMBERS (tempo, density, spectral targets, a contract sentence)
and this fills every load-bearing structural field from fixed, canonical values --
so no agent can emit a persona that drifts the enforced transform budgets, breaks
the weights-sum-to-1 rule, or malforms the spectral target.

The output matches the hand-authored remix_branchez_v1 / remix_prettylights_v1
shape and passes the same persona gates.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# The enforced per-role transform SAFETY budgets (single source of truth: they
# must equal the engine's deck/transform.py budgets, which the persona gate
# checks). A persona may NOT loosen these -- style pitching/stretching is a
# composition choice, not a safety-budget change.
CANONICAL_TRANSFORM_BUDGETS = {
    "default_stretch_pct": 8.0,
    "default_pitch_semitones": 2,
    "roles": {
        "drum_anchor": {"varispeed_pct": 8.0, "residual_pitch": 0.75},
        "bass": {"varispeed_pct": 8.5, "residual_pitch": 0.9},
        "vocal": {"varispeed_pct": 6.5, "residual_pitch": 1.15},
        "harmony": {"varispeed_pct": 8.5, "residual_pitch": 1.25},
        "texture": {"varispeed_pct": 8.5, "residual_pitch": 1.0},
        "fx": {"varispeed_pct": 8.5, "residual_pitch": 1.0},
        "full": {"varispeed_pct": 7.0, "residual_pitch": 0.9},
    },
}

_WEIGHT_KEYS = ("recognizability", "role_clarity", "danceability", "deck_feasibility", "contrast")
_DEFAULT_WEIGHTS = {"recognizability": 0.30, "role_clarity": 0.26, "danceability": 0.14,
                    "deck_feasibility": 0.16, "contrast": 0.14}
_PERMITTED_ROLES = ["VOX_HOOK", "VOX_VERSE", "RIFF_ID", "BED_CHORD", "DRUM_BREAK",
                    "BASS_RIFF", "TEXTURE", "PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL"]
_TRANSITION_ALLOWED = ["hook_blend_over_bed", "beatmatch_blend", "acapella_bridge",
                       "impact_drop", "hard_cut_pickup", "hard_cut_to_air"]


def _f(spec: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = spec.get(key)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _i(spec: Dict[str, Any], key: str, default: int) -> int:
    try:
        v = spec.get(key)
        return int(v) if v is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


def _norm_weights(raw: Optional[Dict[str, Any]]) -> Dict[str, float]:
    w = dict(_DEFAULT_WEIGHTS)
    if isinstance(raw, dict):
        for k in _WEIGHT_KEYS:
            if k in raw:
                try:
                    w[k] = max(0.0, float(raw[k]))
                except (TypeError, ValueError):
                    pass
    total = sum(w.values()) or 1.0
    w = {k: round(v / total, 6) for k, v in w.items()}
    # fix rounding drift so the sum is exactly 1.0 (the gate asserts this)
    drift = round(1.0 - sum(w.values()), 6)
    w["recognizability"] = round(w["recognizability"] + drift, 6)
    return w


def build_remix_persona(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build a full, schema-valid remix TasteSpec from a compact style spec.

    Required in ``spec``: ``id`` (e.g. "remix_dilla_v1"), ``name``, ``contract``.
    Everything else is optional and falls back to sensible remix defaults:
      bpm_low/bpm_high, half_time_feel, groove_feel/swing/syncopation,
      source_seconds, max_source_run_s, foreground_max_share, min_feasible_sources,
      seconds_per_event, sources_per_minute, min_layers, max_layers,
      floor_coverage, foreground_coverage, first_foreground_s, max_silent_gap_s,
      min_edge_score, objective_weights,
      low200_ceiling_fail/low200_ceiling_warn/low200_floor_warn,
      high3000_target/high3000_floor_warn/high3000_floor_fail,
      rms_target/rms_floor, provenance (dict).
    """
    pid = str(spec.get("id") or "").strip()
    if not pid:
        raise ValueError("build_remix_persona: spec.id is required")
    edge = round(_f(spec, "min_edge_score", 0.52), 6)
    persona: Dict[str, Any] = {
        "id": pid,
        "version": str(spec.get("version") or "1.0.0"),
        "name": str(spec.get("name") or pid),
        "contract": str(spec.get("contract") or "one foreground element over a rebuilt style-bed"),
        "mode": "remix",
        "provenance": spec.get("provenance") if isinstance(spec.get("provenance"), dict)
                      else {"basis": str(spec.get("provenance") or "")},
        "permitted_roles": list(_PERMITTED_ROLES),
        "role_salience": {
            "VOX_HOOK": {"min_score": 0.48, "min_intelligibility": 0.5},
            "RIFF_ID": {"min_score": 0.46},
            "BED_CHORD": {"min_bed_score": 0.46},
            "BASS_RIFF": {"min_bass_score": 0.48},
            "DRUM_BREAK": {"min_floor_score": 0.46},
        },
        "hard_constraints": {
            "rails": ["floor", "foreground", "spark"],
            "require_transform_feasible_before_compose": True,
            "forbid_silent_layer_drop": True,
            "forbid_wav_on_gate_failure": True,
        },
        "objective_weights": _norm_weights(spec.get("objective_weights")),
        "compatibility_relations": {
            rel: {"min_score": edge} for rel in
            ("vocal_over_bed", "bass_over_drums", "spark_into_phrase", "floor", "foreground")
        },
        "min_edge_score": edge,
        "transition_grammar": {"allowed": list(spec.get("transition_allowed") or _TRANSITION_ALLOWED)},
        "transform_budgets": {
            "default_stretch_pct": CANONICAL_TRANSFORM_BUDGETS["default_stretch_pct"],
            "default_pitch_semitones": CANONICAL_TRANSFORM_BUDGETS["default_pitch_semitones"],
            "roles": {r: dict(v) for r, v in CANONICAL_TRANSFORM_BUDGETS["roles"].items()},
        },
        "coverage_obligations": {
            "floor_coverage": round(_f(spec, "floor_coverage", 0.9), 6),
            "foreground_coverage": round(_f(spec, "foreground_coverage", 0.75), 6),
            "first_foreground_s": round(_f(spec, "first_foreground_s", 6.0), 6),
            "max_silent_gap_s": round(_f(spec, "max_silent_gap_s", 1.5), 6),
        },
        "source_turnover": {
            "source_seconds": round(_f(spec, "source_seconds", 12.0), 6),
            "max_source_run_s": round(_f(spec, "max_source_run_s", 28.0), 6),
            "foreground_max_share": round(_f(spec, "foreground_max_share", 0.5), 6),
            "min_feasible_sources": _i(spec, "min_feasible_sources", 7),
        },
        "density_model": {
            "seconds_per_event": round(_f(spec, "seconds_per_event", 9.0), 6),
            "sources_per_minute": round(_f(spec, "sources_per_minute", 3.5), 6),
            "min_layers": _i(spec, "min_layers", 2),
            "max_layers": _i(spec, "max_layers", 6),
        },
        "tempo_target": {
            "bpm_low": _i(spec, "bpm_low", 90),
            "bpm_high": _i(spec, "bpm_high", 110),
            "half_time_feel": bool(spec.get("half_time_feel", True)),
        },
        "groove_target": {
            "feel": str(spec.get("groove_feel") or "swung_hip_hop"),
            "swing": str(spec.get("groove_swing") or "moderate"),
            "syncopation": str(spec.get("groove_syncopation") or "medium"),
        },
        "spectral_target": {
            "rms_std_db": {"target": round(_f(spec, "rms_target", 4.5), 6),
                           "floor": round(_f(spec, "rms_floor", 3.0), 6)},
            "low200_share": {"ceiling_fail": round(_f(spec, "low200_ceiling_fail", 0.50), 6),
                             "ceiling_warn": round(_f(spec, "low200_ceiling_warn", 0.38), 6),
                             "floor_warn": round(_f(spec, "low200_floor_warn", 0.08), 6)},
            "high3000_share": {"target": round(_f(spec, "high3000_target", 0.14), 6),
                               "floor_warn": round(_f(spec, "high3000_floor_warn", 0.08), 6),
                               "floor_fail": round(_f(spec, "high3000_floor_fail", 0.04), 6)},
        },
        "endless_contract": {"min_recycle_gap_s": round(_f(spec, "min_recycle_gap_s", 600.0), 6),
                             "note": str(spec.get("endless_note") or "the foreground anchors; the bed rotates")},
        "foreground_limits": {"max_foreground_reuse": _i(spec, "max_foreground_reuse", 5),
                              "max_foreground_duration_s": round(_f(spec, "max_foreground_duration_s", 45.0), 6)},
        "low_end_ownership": {"max_simultaneous_bass_layers": 1,
                             "low_conflict_warn": round(_f(spec, "low_conflict_warn", 0.38), 6),
                             "bass_ducking_required": True},
        "masking_intelligibility": {"max_mid_mask": round(_f(spec, "max_mid_mask", 0.55), 6),
                                    "min_vocal_intelligibility": round(_f(spec, "min_vocal_intelligibility", 0.5), 6)},
        "acceptance_corpus_metrics": {"hook_top5_recall": 0.8, "pair_top10_min_approved_when_available": 3,
                                      "deterministic_plan_hash": True, "deterministic_render_hash": True},
    }
    return persona
