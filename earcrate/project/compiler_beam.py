from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Mapping

from .compiler_clip import _candidate_options, _clip_from_candidate, _section_options, _source_reuse_penalty
from .compiler_deck import _form_energy, _pair_score
from .util import ValidationError, clamp, deep_copy_json, sha256_json, stable_id

def _compile_beam(
    *,
    sources: Mapping[str, Any],
    candidates: list[dict[str, Any]],
    policy: Mapping[str, Any],
    deck: Mapping[str, Any],
    target_seconds: float,
    seed: int,
    form_variant: str,
    beam_width: int = 12,
) -> dict[str, Any]:
    bpm = float(deck["bpm"])
    key = int(deck["key_root"])
    total_bars = max(4, int(round(target_seconds * bpm / 240.0 / 4.0)) * 4)
    section_bars = 4
    n_sections = max(1, math.ceil(total_bars / section_bars))
    form = _form_energy(n_sections, form_variant, policy.get("density") or {})
    feasible_ids = set(deck.get("feasible_candidate_ids") or [])
    feasible = [candidate for candidate in candidates if candidate["candidate_id"] in feasible_ids]
    if not any(candidate["rail"] == "floor" for candidate in feasible):
        raise ValidationError("deck has no feasible floor material")
    min_fg_coverage = float((policy.get("coverage") or {}).get("foreground_coverage") or 0.0)
    if min_fg_coverage > 0 and not any(candidate["rail"] == "foreground" for candidate in feasible):
        raise ValidationError("deck has no feasible foreground material")

    rng = random.Random(int(seed) ^ int(sha256_json({"deck": deck, "form": form_variant})[:16], 16))
    states: list[dict[str, Any]] = [{
        "score": 0.0,
        "sections": [],
        "source_use": {},
        "last_floor": None,
        "last_foreground": None,
        "decisions": [],
        "jitter": 0.0,
    }]
    weights = policy.get("objective_weights") or {}
    recognizability_w = float(weights.get("recognizability") or 0.2)
    role_clarity_w = float(weights.get("role_clarity") or 0.2)
    dance_w = float(weights.get("danceability") or 0.2)
    contrast_w = float(weights.get("contrast") or 0.2)

    for section_index, (section_type, energy) in enumerate(form):
        start_bar = section_index * section_bars
        bars = min(section_bars, total_bars - start_bar)
        start_beat = float(start_bar * 4)
        duration_beats = float(bars * 4)
        next_states: list[dict[str, Any]] = []
        for state in states:
            for floor, foreground, spark in _section_options(feasible, state, energy=energy, section_type=section_type, policy=policy):
                if floor is None:
                    continue
                pair_fg, pair_fg_receipt = _pair_score(foreground, floor, "vocal_over_bed", policy)
                if foreground is not None and not pair_fg_receipt.get("passed", True):
                    continue
                pair_spark, pair_spark_receipt = _pair_score(spark, floor, "spark_into_phrase", policy)
                if spark is not None and not pair_spark_receipt.get("passed", True):
                    continue
                chosen = [item for item in (floor, foreground, spark) if item is not None]
                if len(chosen) > int(((policy.get("mix") or {}).get("layer_budget") or {}).get("max") or 4):
                    continue
                new_state = {
                    "score": float(state["score"]),
                    "sections": deep_copy_json(state["sections"]),
                    "source_use": dict(state["source_use"]),
                    "last_floor": floor,
                    "last_foreground": foreground or state.get("last_foreground"),
                    "decisions": deep_copy_json(state["decisions"]),
                    "jitter": state["jitter"] + rng.random() * 1e-7,
                }
                clips: list[dict[str, Any]] = []
                for ordinal, candidate in enumerate(chosen):
                    try:
                        clip, decision = _clip_from_candidate(
                            candidate,
                            section_index=section_index,
                            start_beat=start_beat,
                            duration_beats=duration_beats,
                            energy=energy,
                            render_bpm=bpm,
                            target_key=key,
                            policy=policy,
                            source=sources[candidate["source_id"]],
                            ordinal=ordinal,
                        )
                    except ValidationError:
                        clips = []
                        break
                    clips.append(clip)
                    decision["alternatives"] = []
                    new_state["decisions"].append(decision)
                    source_id = str(candidate["source_id"])
                    new_state["source_use"][source_id] = int(new_state["source_use"].get(source_id, 0)) + 1
                if not clips:
                    continue
                source_penalty = sum(_source_reuse_penalty(state["source_use"], candidate, policy) for candidate in chosen)
                source_contrast = len({candidate["source_id"] for candidate in chosen}) / len(chosen)
                selection = sum(float(candidate["score"]) for candidate in chosen) / len(chosen)
                role_clarity = 1.0 if floor and (foreground or min_fg_coverage == 0.0) else 0.4
                danceability = float(floor["metrics"].get("transient_density") or 0.0)
                dynamic_fit = 1.0 - abs(energy - (0.45 + 0.55 * max(float(candidate["score"]) for candidate in chosen)))
                section_score = (
                    recognizability_w * selection
                    + role_clarity_w * role_clarity
                    + dance_w * danceability
                    + contrast_w * source_contrast
                    + 0.20 * dynamic_fit
                    + 0.14 * pair_fg
                    + 0.08 * pair_spark
                    - source_penalty
                )
                new_state["score"] += section_score
                new_state["sections"].append({
                    "section_id": stable_id("section", {"index": section_index, "start_beat": start_beat, "variant": form_variant}),
                    "index": section_index,
                    "bar_start": start_bar,
                    "bars": bars,
                    "start_beat": start_beat,
                    "duration_beats": duration_beats,
                    "type": section_type,
                    "energy": energy,
                    "clips": clips,
                    "pair_receipts": {"foreground": pair_fg_receipt, "spark": pair_spark_receipt},
                })
                next_states.append(new_state)
        if not next_states:
            raise ValidationError(f"beam search exhausted at section {section_index}")
        next_states.sort(key=lambda state: (-float(state["score"] + state["jitter"]), sha256_json(state["sections"])))
        states = next_states[:beam_width]
    winner = states[0]
    winner.update({"bpm": bpm, "key_root": key, "total_bars": total_bars, "form_variant": form_variant, "beam_finalists": len(states)})
    return winner

