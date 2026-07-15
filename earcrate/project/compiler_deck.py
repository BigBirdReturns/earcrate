from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from typing import Any, Mapping

from . import buffalo
from .util import clamp

def _candidate_decks(candidates: list[dict[str, Any]], policy: Mapping[str, Any], constraints: Mapping[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    pinned_bpm = constraints.get("bpm") or constraints.get("pin_bpm")
    pinned_key = constraints.get("key_root") if constraints.get("key_root") is not None else constraints.get("pin_key")
    if pinned_bpm:
        bpms = [float(pinned_bpm)]
    else:
        weighted = sorted((float(candidate["analysis"].get("bpm") or 120.0), float(candidate["score"])) for candidate in candidates)
        values: list[float] = []
        if weighted:
            cumulative = 0.0
            total = sum(weight for _, weight in weighted) or len(weighted)
            for bpm, weight in weighted:
                cumulative += weight or 1.0
                if cumulative >= total * 0.5:
                    values.append(bpm)
                    break
        tempo = policy.get("tempo") or {}
        if tempo.get("bpm_low") is not None and tempo.get("bpm_high") is not None:
            values.append((float(tempo["bpm_low"]) + float(tempo["bpm_high"])) / 2.0)
        values.extend(bpm for bpm, _ in sorted(weighted, key=lambda row: -row[1])[:4])
        bpms = []
        for bpm in values or [120.0]:
            if all(abs(bpm - existing) > 1.0 for existing in bpms):
                bpms.append(bpm)
        bpms = bpms[:limit]
    if pinned_key is not None:
        keys = [int(pinned_key) % 12]
    else:
        counts: dict[int, float] = defaultdict(float)
        for candidate in candidates:
            counts[int(candidate["analysis"].get("key_root") or 0) % 12] += float(candidate["score"])
        keys = [key for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]] or [0]
    stretch_default = float((policy.get("transform_budgets") or {}).get("default_stretch_pct") or 8.0)
    pitch_default = float((policy.get("transform_budgets") or {}).get("default_pitch_semitones") or 2.0)
    decks: list[dict[str, Any]] = []
    for bpm, key in itertools.product(bpms, keys):
        feasible: list[str] = []
        rejected: list[dict[str, Any]] = []
        role_counts: Counter[str] = Counter()
        score = 0.0
        for candidate in candidates:
            source_bpm = float(candidate["analysis"].get("bpm") or bpm)
            source_key = int(candidate["analysis"].get("key_root") or key) % 12
            role_budget = ((policy.get("transform_budgets") or {}).get("roles") or {}).get(candidate["role"]) or {}
            stretch = float(role_budget.get("varispeed_pct") or stretch_default)
            pitch = float(role_budget.get("residual_pitch") or pitch_default)
            plan, receipt = buffalo.transform_plan(candidate["role"], source_bpm, bpm, source_key, key, stretch, pitch)
            if plan.get("violation"):
                rejected.append({"candidate_id": candidate["candidate_id"], "reason": plan.get("violation")})
                continue
            feasible.append(candidate["candidate_id"])
            role_counts[candidate["rail"]] += 1
            score += float(candidate["score"]) * (1.0 - 0.35 * float(plan.get("artifact_risk") or 0.0))
        score += 2.0 * min(1.0, role_counts["floor"] / 4.0) + 2.0 * min(1.0, role_counts["foreground"] / 3.0) + min(1.0, role_counts["spark"] / 3.0)
        decks.append({
            "bpm": round(float(bpm), 6),
            "key_root": int(key),
            "score": round(score, 6),
            "feasible_candidate_ids": feasible,
            "rejected": rejected,
            "role_counts": dict(role_counts),
            "pinned": bool(pinned_bpm or pinned_key is not None),
        })
    decks.sort(key=lambda row: (-float(row["score"]), float(row["bpm"]), int(row["key_root"])))
    return decks[:limit]


def _form_energy(n_sections: int, variant: str, density: Mapping[str, Any]) -> list[tuple[str, float]]:
    if n_sections <= 1:
        return [("sustain", 0.72)]
    result: list[tuple[str, float]] = []
    drop_stride = 3 if variant == "volatile" else 4 if variant == "balanced" else 5
    break_stride = 5 if variant == "volatile" else 6 if variant == "balanced" else 7
    for index in range(n_sections):
        if index == 0:
            result.append(("intro", 0.38))
        elif index == n_sections - 1:
            result.append(("outro", 0.52))
        elif index % drop_stride == 0:
            result.append(("drop", 1.0))
        elif (index + 1) % drop_stride == 0:
            result.append(("build", 0.70))
        elif index % break_stride == break_stride - 2:
            result.append(("breakdown", 0.44))
        else:
            result.append(("sustain", 0.76 if variant != "calm" else 0.68))
    return result


def _pair_score(left: dict[str, Any] | None, right: dict[str, Any] | None, relation: str, policy: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    if left is None or right is None:
        return 0.5, {"reason": "single_role"}
    lk = int(left["analysis"].get("key_root") or 0) % 12
    rk = int(right["analysis"].get("key_root") or 0) % 12
    interval = (rk - lk) % 12
    harmonic = {0: 1.0, 7: 0.86, 5: 0.86, 9: 0.72, 3: 0.72, 2: 0.58, 10: 0.58}.get(interval, 0.25 if interval == 6 else 0.44)
    low_conflict = min(float(left["metrics"].get("low_share") or 0.0), float(right["metrics"].get("low_share") or 0.0))
    mid_mask = min(float(left["metrics"].get("mid_share") or 0.0), float(right["metrics"].get("mid_share") or 0.0))
    same_source = left["source_id"] == right["source_id"]
    if relation == "vocal_over_bed":
        intelligible = max(float(left["metrics"].get("intelligibility") or 0.0), float(right["metrics"].get("intelligibility") or 0.0))
        score = 0.34 * harmonic + 0.28 * intelligible + 0.20 * (1.0 - min(1.0, mid_mask / max(0.01, float((policy.get("masking") or {}).get("max_mid_mask") or 0.55)))) + 0.18 * (1.0 - min(1.0, low_conflict / 0.32))
    elif relation == "bass_over_drums":
        score = 0.40 * harmonic + 0.35 * (1.0 - min(1.0, low_conflict / 0.38)) + 0.25 * max(float(left["score"]), float(right["score"]))
    else:
        score = 0.35 * harmonic + 0.30 * (1.0 - min(1.0, mid_mask / 0.9)) + 0.35 * max(float(left["score"]), float(right["score"]))
    if same_source:
        score -= 0.25
    minimum = float(((policy.get("compatibility_relations") or {}).get(relation) or {}).get("min_score") or 0.0)
    score = clamp(score, 0.0, 1.0)
    return score, {"harmonic": harmonic, "low_conflict": low_conflict, "mid_mask": mid_mask, "same_source": same_source, "minimum": minimum, "passed": score >= minimum}

