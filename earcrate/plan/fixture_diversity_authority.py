"""Separate fixture-authority evidence from arrangement-realization movement.

A governed arrangement can vary in section role occupancy under a fixed fixture.
Those observed differences remain useful measurements, but they cannot manufacture
a new fixture identity or authorize max-min fixture selection. Direct
``earcrate_fixture_candidate`` objects retain the complete structural ruler.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_diversity_contract as _legacy


DEFAULT_WEIGHTS = _legacy.DEFAULT_WEIGHTS
EPS = _legacy.EPS
FixtureDiversityError = _legacy.FixtureDiversityError
canonical_json = _legacy.canonical_json
jaccard_distance = _legacy.jaccard_distance

FIXTURE_CANDIDATE_SCOPE = "fixture_candidates"
ARRANGEMENT_REALIZATION_SCOPE = "arrangement_realizations"

ALL_AXES: Tuple[str, ...] = tuple(DEFAULT_WEIGHTS)
FIXTURE_AUTHORITY_AXES: Tuple[str, ...] = (
    "source_set",
    "source_partition",
    "deck_sequence",
    "island_duration",
    "transition_histogram",
)
REALIZATION_ONLY_AXES: Tuple[str, ...] = (
    "form_sequence",
    "role_occupancy",
)

_FULL_PROJECTION_FIELDS: Tuple[str, ...] = (
    "source_ids",
    "source_partition",
    "deck_sequence",
    "duration_sequence",
    "form_sequence",
    "role_occupancy_histogram",
    "transition_histogram",
)
_AUTHORITY_PROJECTION_FIELDS: Tuple[str, ...] = (
    "source_ids",
    "source_partition",
    "deck_sequence",
    "duration_sequence",
    "transition_histogram",
)


def _candidate_scope(candidate: Mapping[str, Any]) -> str:
    """Classify the evidence object without trusting a display label."""
    if not isinstance(candidate, Mapping):
        raise FixtureDiversityError("fixture evidence candidates must be mappings")
    kind = str(candidate.get("kind") or "")
    if isinstance(candidate.get("arrangement"), Mapping):
        return ARRANGEMENT_REALIZATION_SCOPE
    if kind == "earcrate_fixture_candidate" or isinstance(
        candidate.get("fixture_derivation"), Mapping
    ):
        return FIXTURE_CANDIDATE_SCOPE
    if kind in {"earcrate_island_set", "earcrate_island_set_proposal"}:
        return ARRANGEMENT_REALIZATION_SCOPE
    if candidate.get("sections") is not None:
        return ARRANGEMENT_REALIZATION_SCOPE
    return FIXTURE_CANDIDATE_SCOPE


def _family_scope(candidates: Sequence[Mapping[str, Any]]) -> str:
    scopes = {_candidate_scope(candidate) for candidate in candidates}
    if len(scopes) > 1:
        raise FixtureDiversityError(
            "cannot mix fixture candidates with arrangement realizations in one family"
        )
    return next(iter(scopes), FIXTURE_CANDIDATE_SCOPE)


def _classification_axes(scope: str) -> Tuple[str, ...]:
    return ALL_AXES if scope == FIXTURE_CANDIDATE_SCOPE else FIXTURE_AUTHORITY_AXES


def _observational_axes(scope: str) -> Tuple[str, ...]:
    return () if scope == FIXTURE_CANDIDATE_SCOPE else REALIZATION_ONLY_AXES


def _semantic_projection(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    scope = _candidate_scope(candidate)
    full = dict(_legacy.fixture_projection(candidate))
    full_identity = str(full["fixture_identity"])
    fields = (
        _FULL_PROJECTION_FIELDS
        if scope == FIXTURE_CANDIDATE_SCOPE
        else _AUTHORITY_PROJECTION_FIELDS
    )
    authority_body = {field: full[field] for field in fields}
    authority_identity = hashlib.sha256(
        canonical_json(authority_body).encode("utf-8")
    ).hexdigest()
    full.update(
        {
            "evidence_scope": scope,
            "fixture_authority_fields": list(fields),
            "realization_only_fields": list(_observational_axes(scope)),
            "realization_identity": full_identity,
            "fixture_identity": authority_identity,
        }
    )
    return full


def fixture_projection(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return _semantic_projection(candidate)


def fixture_id(
    candidate: Mapping[str, Any],
    projection: Optional[Mapping[str, Any]] = None,
) -> str:
    explicit = candidate.get("fixture_id") or candidate.get("fixture_sha256")
    if explicit not in (None, ""):
        return str(explicit)
    body = projection or fixture_projection(candidate)
    return str(body["fixture_identity"])


def _weighted_total(
    axes: Mapping[str, float],
    weights: Mapping[str, float],
    names: Sequence[str],
    *,
    scope: str,
) -> float:
    total_weight = sum(float(weights.get(name, 0.0)) for name in names)
    if total_weight <= EPS:
        raise FixtureDiversityError(
            "fixture-authority classification has zero weight; "
            f"{scope} may not be classified from realization-only axes"
        )
    return sum(
        float(axes[name]) * float(weights.get(name, 0.0))
        for name in names
    ) / total_weight


def fixture_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    left_scope = _candidate_scope(left)
    right_scope = _candidate_scope(right)
    if left_scope != right_scope:
        raise FixtureDiversityError(
            "cannot compare a fixture candidate with an arrangement realization"
        )

    raw = dict(_legacy.fixture_distance(left, right, weights))
    axes = {str(key): float(value) for key, value in raw["axes"].items()}
    chosen = {str(key): float(value) for key, value in raw["weights"].items()}
    classification_axes = _classification_axes(left_scope)
    observational_axes = _observational_axes(left_scope)
    left_projection = fixture_projection(left)
    right_projection = fixture_projection(right)

    authority_total = _weighted_total(
        axes, chosen, classification_axes, scope=left_scope
    )
    if observational_axes and sum(chosen.get(name, 0.0) for name in observational_axes) > EPS:
        realization_total = sum(
            axes[name] * chosen.get(name, 0.0) for name in observational_axes
        ) / sum(chosen.get(name, 0.0) for name in observational_axes)
    else:
        realization_total = 0.0

    return {
        "left_fixture_id": fixture_id(left, left_projection),
        "right_fixture_id": fixture_id(right, right_projection),
        "left_semantic_fixture_identity": left_projection["fixture_identity"],
        "right_semantic_fixture_identity": right_projection["fixture_identity"],
        "left_realization_identity": left_projection["realization_identity"],
        "right_realization_identity": right_projection["realization_identity"],
        "evidence_scope": left_scope,
        "classification_basis": "fixture_authority_axes_only",
        "classification_axes": list(classification_axes),
        "observational_axes": list(observational_axes),
        "axes": axes,
        "weights": chosen,
        "total": authority_total,
        "observed_total": float(raw["total"]),
        "realization_total": realization_total,
    }


def distance_matrix(
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    _family_scope(candidates)
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
    scope = _family_scope(candidates)
    matrix = distance_matrix(candidates, weights)
    classification_axes = _classification_axes(scope)
    observational_axes = _observational_axes(scope)
    discriminating = [
        row
        for row in matrix
        if any(float(row["axes"][axis]) > EPS for axis in classification_axes)
    ]
    realization_variation = [
        row
        for row in matrix
        if any(float(row["axes"][axis]) > EPS for axis in observational_axes)
    ]
    if len(candidates) < 2:
        status = "insufficient_candidates"
    elif not discriminating:
        status = "non_discriminating"
    else:
        status = "discriminating"

    projections = [fixture_projection(candidate) for candidate in candidates]
    disposition = (
        "direct_fixture_authority"
        if scope == FIXTURE_CANDIDATE_SCOPE
        else "arrangement_realizations_observed_fixture_authority_only"
    )
    return {
        "status": status,
        "candidate_count": len(candidates),
        "evidence_scope": scope,
        "classification_disposition": disposition,
        "fixture_ids": [
            fixture_id(candidate, projection)
            for candidate, projection in zip(candidates, projections)
        ],
        "semantic_fixture_identities": [
            projection["fixture_identity"] for projection in projections
        ],
        "semantic_realization_identities": [
            projection["realization_identity"] for projection in projections
        ],
        "structural_axes": list(ALL_AXES),
        "classification_axes": list(classification_axes),
        "observational_axes": list(observational_axes),
        "distance_matrix": matrix,
        "discriminating_pair_count": len(discriminating),
        "realization_variation_pair_count": len(realization_variation),
    }


def select_max_min(
    candidates: Sequence[Mapping[str, Any]],
    limit: int = 3,
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Select fixture authorities only when authority evidence discriminates."""
    family = classify_candidate_family(candidates, weights)
    matrix = family["distance_matrix"]
    totals = [float(row["total"]) for row in matrix]
    observed_totals = [float(row["observed_total"]) for row in matrix]
    empty = {
        "selected_fixture_ids": [],
        "selected_semantic_fixture_identities": [],
        "raw_total_distances": totals,
        "raw_observed_total_distances": observed_totals,
    }
    if len(candidates) < 3:
        return {
            **family,
            **empty,
            "selection_status": "not_run_fewer_than_three_candidates",
        }
    if family["status"] != "discriminating":
        return {
            **family,
            **empty,
            "selection_status": "not_run_non_discriminating_family",
        }
    if not totals or max(totals) - min(totals) <= EPS:
        return {
            **family,
            **empty,
            "selection_status": "not_run_degenerate_distance_range",
        }

    display_ids = list(family["fixture_ids"])
    semantic_ids = list(family["semantic_fixture_identities"])
    pair_distance: Dict[Tuple[int, int], float] = {
        tuple(sorted((int(row["left_index"]), int(row["right_index"])))): float(
            row["total"]
        )
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
        remaining = [
            index for index in range(len(candidates)) if index not in selected
        ]
        selected.append(
            min(
                remaining,
                key=lambda index: (
                    -min(
                        pair_distance[tuple(sorted((index, chosen)))]
                        for chosen in selected
                    ),
                    -means[index],
                    semantic_ids[index],
                    index,
                ),
            )
        )
    return {
        **family,
        "selection_status": "selected_max_min",
        "selected_fixture_ids": [display_ids[index] for index in selected],
        "selected_semantic_fixture_identities": [
            semantic_ids[index] for index in selected
        ],
        "selected_indexes": selected,
        "raw_total_distances": totals,
        "raw_observed_total_distances": observed_totals,
    }


__all__ = [
    "ALL_AXES",
    "ARRANGEMENT_REALIZATION_SCOPE",
    "DEFAULT_WEIGHTS",
    "FIXTURE_AUTHORITY_AXES",
    "FIXTURE_CANDIDATE_SCOPE",
    "FixtureDiversityError",
    "REALIZATION_ONLY_AXES",
    "canonical_json",
    "classify_candidate_family",
    "distance_matrix",
    "fixture_distance",
    "fixture_id",
    "fixture_projection",
    "jaccard_distance",
    "select_max_min",
]
