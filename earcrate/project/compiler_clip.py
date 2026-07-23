from __future__ import annotations

import itertools
import math
from typing import Any, Mapping

from . import buffalo
from .compiler_source_common import _jsonable
from .model import make_clip_id
from .policy import assert_value_in_policy_range, policy_range
from .util import ValidationError, clamp, sha256_json, stable_id

def _clip_from_candidate(
    candidate: Mapping[str, Any],
    *,
    section_index: int,
    start_beat: float,
    duration_beats: float,
    energy: float,
    render_bpm: float,
    target_key: int,
    policy: Mapping[str, Any],
    source: Mapping[str, Any],
    ordinal: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rail = str(candidate["rail"])
    role = str(candidate["role"])
    role_budget = ((policy.get("transform_budgets") or {}).get("roles") or {}).get(role) or {}
    stretch_budget = float(role_budget.get("varispeed_pct") or (policy.get("transform_budgets") or {}).get("default_stretch_pct") or 8.0)
    pitch_budget = float(role_budget.get("residual_pitch") or (policy.get("transform_budgets") or {}).get("default_pitch_semitones") or 2.0)
    source_bpm = float(candidate["analysis"].get("bpm") or render_bpm)
    source_key = int(candidate["analysis"].get("key_root") or target_key) % 12
    plan, transform_receipt = buffalo.transform_plan(role, source_bpm, render_bpm, source_key, target_key, stretch_budget, pitch_budget)
    if plan.get("violation"):
        raise ValidationError(str(plan["violation"]))
    source_duration_beats = float(candidate["duration_s"]) * render_bpm / 60.0
    loopable = bool(source.get("capabilities", {}).get("loopable")) and float(candidate["metrics"].get("loopability") or 0.0) >= 0.35 and role != "vocal"
    active_beats = duration_beats if loopable else min(duration_beats, source_duration_beats)
    if active_beats <= 0.25:
        raise ValidationError("candidate cannot cover a playable beat window")
    gain_range = policy_range(policy, "bass" if role == "bass" else rail)
    energy_trim = (float(energy) - 0.72) * (5.0 if rail == "foreground" else 7.0)
    role_rms = float((((policy.get("mix") or {}).get("normalization") or {}).get("target_role_rms") or {}).get("bass" if role == "bass" else rail) or 0.09)
    measured_rms = max(1e-6, float(candidate["metrics"].get("rms") or role_rms))
    max_norm = float(((policy.get("mix") or {}).get("normalization") or {}).get("max_correction_db") or 12.0)
    norm_db = clamp(20.0 * math.log10(role_rms / measured_rms), -max_norm, max_norm)
    gain_db = clamp(gain_range["target"] + energy_trim + norm_db, gain_range["min"], gain_range["max"])
    assert_value_in_policy_range(policy, "bass" if role == "bass" else rail, gain_db, f"clip[{candidate['candidate_id']}].gain_db")
    fade_ms = float(((policy.get("mix") or {}).get("fade_ms") or {}).get("target") or 18.0)
    fade_beats = fade_ms / 1000.0 * render_bpm / 60.0
    pan_seed = int(sha256_json({"candidate": candidate["candidate_id"], "section": section_index})[:8], 16)
    pan = 0.0 if rail in {"floor", "foreground"} else ((pan_seed % 1601) / 1000.0 - 0.8)
    pan = clamp(pan, -0.8, 0.8)
    clip_id = make_clip_id(str(candidate["source_id"]), start_beat, role, ordinal)
    locked_fields = ["source_id", "source_range"] if candidate.get("locked") or source.get("locked") else []
    clip = {
        "clip_id": clip_id,
        "source_id": candidate["source_id"],
        "candidate_id": candidate["candidate_id"],
        "stem": candidate.get("stem") or "mix",
        "role": role,
        "ear_role": candidate["ear_role"],
        "timeline_start_beat": round(float(start_beat), 9),
        "timeline_duration_beats": round(float(active_beats), 9),
        "source_start_sample": int(candidate["source_start_sample"]),
        "source_end_sample": int(candidate["source_end_sample"]),
        "loop": {"enabled": loopable and active_beats > source_duration_beats + 1e-6, "crossfade_samples": 512},
        "gain_db": round(float(gain_db), 6),
        "normalization_gain_db": round(float(norm_db), 6),
        "pan": round(float(pan), 6),
        "fades": {"in_beats": round(fade_beats, 9), "out_beats": round(fade_beats, 9), "curve": "equal_power"},
        "transform": {
            "rate": float(plan.get("speed_ratio") or 1.0),
            "pitch_semitones": float(plan.get("residual_pitch_shift", plan.get("synthetic_pitch_shift") or 0.0) or 0.0),
            "mode": str(plan.get("transform_mode") or plan.get("mode") or "identity"),
            "artifact_risk": float(plan.get("artifact_risk") or 0.0),
            "receipt": _jsonable(plan),
        },
        "muted": False,
        "solo": False,
        "locked_fields": locked_fields,
        "decision_id": stable_id("decision", {"clip": clip_id, "candidate": candidate["candidate_id"]}),
        "source_context": {
            "available_head_samples": int(candidate["source_start_sample"]),
            "available_tail_samples": int(source["duration_samples"]) - int(candidate["source_end_sample"]),
        },
    }
    decision = {
        "decision_id": clip["decision_id"],
        "kind": "clip_selection",
        "section_index": section_index,
        "rail": rail,
        "selected": candidate["candidate_id"],
        "selected_score": round(float(candidate["score"]), 6),
        "chosen_parameters": {"gain_db": clip["gain_db"], "normalization_gain_db": clip["normalization_gain_db"], "pan": clip["pan"], "timeline_duration_beats": clip["timeline_duration_beats"]},
        "evidence": {"candidate_metrics": candidate["metrics"], "transform": transform_receipt, "source_analysis": {"bpm": source_bpm, "key_root": source_key}},
        "policy_ranges": {"gain_db": gain_range},
        "human_lock": bool(locked_fields),
    }
    return clip, decision


def _candidate_options(candidates: list[dict[str, Any]], rail: str, limit: int) -> list[dict[str, Any]]:
    items = [candidate for candidate in candidates if candidate["rail"] == rail]
    items.sort(key=lambda candidate: (-float(candidate["score"]), str(candidate["candidate_id"])))
    return items[:limit]


def _section_options(
    candidates: list[dict[str, Any]],
    state: Mapping[str, Any],
    *,
    energy: float,
    section_type: str,
    policy: Mapping[str, Any],
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]]:
    floors = _candidate_options(candidates, "floor", 7)
    foregrounds = _candidate_options(candidates, "foreground", 7)
    sparks = _candidate_options(candidates, "spark", 5)
    previous_floor = state.get("last_floor")
    previous_fg = state.get("last_foreground")
    if previous_floor:
        floors = [previous_floor] + [item for item in floors if item["candidate_id"] != previous_floor["candidate_id"]]
    if previous_fg:
        foregrounds = [previous_fg] + [item for item in foregrounds if item["candidate_id"] != previous_fg["candidate_id"]]
    if not floors:
        return []
    allow_no_fg = section_type in {"intro", "breakdown", "outro"}
    fg_options: list[dict[str, Any] | None] = foregrounds[:5] + ([None] if allow_no_fg else [])
    spark_due = section_type in {"build", "drop"} or energy >= 0.72
    spark_options: list[dict[str, Any] | None] = ([None] + sparks[:3]) if spark_due else [None]
    return list(itertools.product(floors[:5], fg_options, spark_options))


def _source_reuse_penalty(source_use: Mapping[str, int], candidate: Mapping[str, Any] | None, policy: Mapping[str, Any]) -> float:
    if candidate is None:
        return 0.0
    count = int(source_use.get(str(candidate["source_id"]), 0))
    max_share = float((policy.get("turnover") or {}).get("foreground_max_share") or 0.5)
    return count * (0.10 + 0.35 * (1.0 - max_share))

