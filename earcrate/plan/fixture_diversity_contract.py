"""Fail-closed public contract for fixture-level diversity evidence.

The implementation core computes the structural axes. This layer normalizes
semantically equivalent inputs and makes every selection tie depend on the
semantic projection rather than on user-supplied labels or file ordering.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_diversity_core as _core

DEFAULT_WEIGHTS = _core.DEFAULT_WEIGHTS
EPS = _core.EPS
FixtureDiversityError = _core.FixtureDiversityError
canonical_json = _core.canonical_json
jaccard_distance = _core.jaccard_distance


def _arrangement_body(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    arrangement = candidate.get("arrangement")
    return arrangement if isinstance(arrangement, Mapping) else candidate


def _stable_island_positions(candidate: Mapping[str, Any]) -> Dict[str, int]:
    rows = list(_arrangement_body(candidate).get("islands") or candidate.get("islands") or [])
    if not all(isinstance(row, Mapping) for row in rows):
        raise FixtureDiversityError("islands must be mappings")
    indexed = list(enumerate(rows))
    if rows and all(row.get("start_s") is not None for row in rows):
        indexed.sort(key=lambda item: (
            float(item[1]["start_s"]),
            float(item[1].get("end_s") or item[1]["start_s"]),
            _island_semantic_token(item[1]),
            item[0],
        ))
    positions: Dict[str, int] = {}
    for position, (_index, row) in enumerate(indexed):
        island_id = str(row.get("island_id") or "")
        if not island_id:
            raise FixtureDiversityError(f"island {position} has no stable island_id")
        if island_id in positions:
            raise FixtureDiversityError(f"duplicate island_id: {island_id}")
        positions[island_id] = position
    return positions


def _island_semantic_token(row: Mapping[str, Any]) -> str:
    values = {
        "bpm": float(row.get("target_bpm", row.get("island_bpm")) or 0.0).hex(),
        "key": int(row.get("target_key", row.get("island_key")) or 0) % 12,
        "sources": sorted({
            str(value)
            for field in ("source_allowlist", "source_include_ids", "source_ids")
            for value in row.get(field) or []
            if str(value)
        }),
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _section_semantic_token(section: Mapping[str, Any], island_positions: Mapping[str, int]) -> str:
    roles = sorted({
        str(layer.get("role") or "full")
        for layer in section.get("layers") or []
        if isinstance(layer, Mapping)
    })
    if section.get("bars") is not None:
        span: Tuple[str, Any] = ("bars", int(section["bars"]))
    elif section.get("start_s") is not None and section.get("end_s") is not None:
        span = ("seconds_hex", (float(section["end_s"]) - float(section["start_s"])).hex())
    else:
        span = ("unspecified", "")
    transition = section.get("transition_in")
    transition_type = (
        str(transition.get("technique") or transition.get("type") or "")
        if isinstance(transition, Mapping)
        else ""
    )
    body = {
        "island_position": island_positions.get(str(section.get("island_id") or ""), -1),
        "type": str(section.get("type") or section.get("section_type") or ""),
        "roles": roles,
        "span": span,
        "transition_in": transition_type,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _normalize_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(dict(candidate))
    body = normalized.get("arrangement")
    target = body if isinstance(body, dict) else normalized
    sections = list(target.get("sections") or [])
    if not all(isinstance(section, Mapping) for section in sections):
        raise FixtureDiversityError("sections must be mappings")
    positions = _stable_island_positions(normalized)

    def order(item: Tuple[int, Mapping[str, Any]]) -> Tuple[Any, ...]:
        index, section = item
        if section.get("start_s") is not None:
            temporal = (0, float(section["start_s"]))
        else:
            temporal = (
                1,
                positions.get(str(section.get("island_id") or ""), -1),
                float(section.get("bar_start") or 0.0),
            )
        end = float(section.get("end_s") or section.get("start_s") or 0.0)
        token = _section_semantic_token(section, positions)
        return (*temporal, end, token, index)

    target["sections"] = [
        section for _index, section in sorted(enumerate(sections), key=order)
    ]
    return normalized


def fixture_projection(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return _core.fixture_projection(_normalize_candidate(candidate))


def fixture_id(candidate: Mapping[str, Any], projection: Optional[Mapping[str, Any]] = None) -> str:
    explicit = candidate.get("fixture_id") or candidate.get("fixture_sha256")
    if explicit not in (None, ""):
        return str(explicit)
    body = projection or fixture_projection(candidate)
    return str(body["fixture_identity"])


def fixture_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    left_normalized = _normalize_candidate(left)
    right_normalized = _normalize_candidate(right)
    result = dict(_core.fixture_distance(left_normalized, right_normalized, weights))
    left_projection = _core.fixture_projection(left_normalized)
    right_projection = _core.fixture_projection(right_normalized)
    result.update({
        "left_fixture_id": fixture_id(left, left_projection),
        "right_fixture_id": fixture_id(right, right_projection),
        "left_semantic_fixture_identity": left_projection["fixture_identity"],
        "right_semantic_fixture_identity": right_projection["fixture_identity"],
    })
    return result


def distance_matrix(
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for left_index in range(len(candidates)):
        for right_index in range(left_index + 1, len(candidates)):
            row = fixture_distance(candidates[left_index], candidates[right_index], weights)
            row["left_index"] = left_index
            row["right_index"] = right_index
            rows.append(row)
    return rows


def classify_candidate_family(
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    matrix = distance_matrix(candidates, weights)
    axes = tuple(DEFAULT_WEIGHTS)
    discriminating = [
        row for row in matrix
        if any(float(row["axes"][axis]) > EPS for axis in axes)
    ]
    if len(candidates) < 2:
        status = "insufficient_candidates"
    elif not discriminating:
        status = "non_discriminating"
    else:
        status = "discriminating"
    projections = [fixture_projection(candidate) for candidate in candidates]
    return {
        "status": status,
        "candidate_count": len(candidates),
        "fixture_ids": [
            fixture_id(candidate, projection)
            for candidate, projection in zip(candidates, projections)
        ],
        "semantic_fixture_identities": [projection["fixture_identity"] for projection in projections],
        "structural_axes": list(axes),
        "distance_matrix": matrix,
        "discriminating_pair_count": len(discriminating),
    }


def select_max_min(
    candidates: Sequence[Mapping[str, Any]],
    limit: int = 3,
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    family = classify_candidate_family(candidates, weights)
    matrix = family["distance_matrix"]
    totals = [float(row["total"]) for row in matrix]
    empty = {
        "selected_fixture_ids": [],
        "selected_semantic_fixture_identities": [],
        "raw_total_distances": totals,
    }
    if len(candidates) < 3:
        return {**family, **empty, "selection_status": "not_run_fewer_than_three_candidates"}
    if not totals or max(totals) - min(totals) <= EPS:
        return {**family, **empty, "selection_status": "not_run_degenerate_distance_range"}
    if family["status"] != "discriminating":
        return {**family, **empty, "selection_status": "not_run_non_discriminating_family"}

    display_ids = list(family["fixture_ids"])
    semantic_ids = list(family["semantic_fixture_identities"])
    pair_distance: Dict[Tuple[int, int], float] = {
        tuple(sorted((int(row["left_index"]), int(row["right_index"])))): float(row["total"])
        for row in matrix
    }
    means: Dict[int, float] = {}
    for index in range(len(candidates)):
        values = [
            pair_distance[tuple(sorted((index, other)))]
            for other in range(len(candidates))
            if other != index
        ]
        means[index] = sum(values) / len(values)

    first = min(
        range(len(candidates)),
        key=lambda index: (-means[index], semantic_ids[index], index),
    )
    selected = [first]
    target = min(max(1, int(limit)), len(candidates))
    while len(selected) < target:
        remaining = [index for index in range(len(candidates)) if index not in selected]
        selected.append(min(
            remaining,
            key=lambda index: (
                -min(pair_distance[tuple(sorted((index, chosen)))] for chosen in selected),
                -means[index],
                semantic_ids[index],
                index,
            ),
        ))
    return {
        **family,
        "selection_status": "selected_max_min",
        "selected_fixture_ids": [display_ids[index] for index in selected],
        "selected_semantic_fixture_identities": [semantic_ids[index] for index in selected],
        "selected_indexes": selected,
        "raw_total_distances": totals,
    }


__all__ = [
    "DEFAULT_WEIGHTS",
    "FixtureDiversityError",
    "canonical_json",
    "classify_candidate_family",
    "distance_matrix",
    "fixture_distance",
    "fixture_id",
    "fixture_projection",
    "jaccard_distance",
    "select_max_min",
]
