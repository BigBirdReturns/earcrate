"""Structural diversity accounting for governed multi-island fixture candidates.

The seed is deliberately absent from every distance axis. A fixture comparison
asks whether the source universe, exact decks, island allocation, form, role
occupancy, or transition vocabulary changed. Arrangement hashes and audio hashes
remain provenance; they are not evidence that the music's governing fixture moved.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DEFAULT_WEIGHTS: Dict[str, float] = {
    "source_set": 0.30,
    "deck_sequence": 0.20,
    "island_duration": 0.15,
    "form_sequence": 0.20,
    "role_occupancy": 0.10,
    "transition_histogram": 0.05,
}
EPS = 1e-12


class FixtureDiversityError(ValueError):
    """A fixture candidate cannot be compared without inventing identity."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _float_identity(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise FixtureDiversityError(f"non-finite numeric identity: {value!r}")
    return number.hex()


def _islands(candidate: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    arrangement = candidate.get("arrangement")
    body = arrangement if isinstance(arrangement, Mapping) else candidate
    rows = list(body.get("islands") or candidate.get("islands") or [])
    if not all(isinstance(row, Mapping) for row in rows):
        raise FixtureDiversityError("islands must be mappings")

    def start_key(item: Tuple[int, Mapping[str, Any]]) -> Tuple[int, float, int]:
        index, row = item
        if row.get("start_s") is None:
            return (1, 0.0, index)
        return (0, float(row["start_s"]), index)

    return [row for _index, row in sorted(enumerate(rows), key=start_key)]


def _sections(candidate: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    arrangement = candidate.get("arrangement")
    body = arrangement if isinstance(arrangement, Mapping) else candidate
    rows = list(body.get("sections") or [])
    if not all(isinstance(row, Mapping) for row in rows):
        raise FixtureDiversityError("sections must be mappings")

    def position(item: Tuple[int, Mapping[str, Any]]) -> Tuple[float, float, int]:
        index, row = item
        if row.get("start_s") is not None:
            return (float(row["start_s"]), 0.0, index)
        return (
            float(row.get("island_index") or 0),
            float(row.get("bar_start") or 0),
            index,
        )

    return [row for _index, row in sorted(enumerate(rows), key=position)]


def _transitions(candidate: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    arrangement = candidate.get("arrangement")
    body = arrangement if isinstance(arrangement, Mapping) else candidate
    rows = list(body.get("transitions") or candidate.get("transitions") or [])
    if not all(isinstance(row, Mapping) for row in rows):
        raise FixtureDiversityError("transitions must be mappings")
    return rows


def _source_ids(candidate: Mapping[str, Any], islands: Sequence[Mapping[str, Any]]) -> List[str]:
    values: set[str] = set()
    for row in islands:
        for key in ("source_allowlist", "source_include_ids", "source_ids"):
            for value in row.get(key) or []:
                if str(value):
                    values.add(str(value))
    arrangement = candidate.get("arrangement")
    body = arrangement if isinstance(arrangement, Mapping) else candidate
    for row in body.get("global_source_ledger") or candidate.get("global_source_ledger") or []:
        if isinstance(row, Mapping) and str(row.get("source_id") or ""):
            values.add(str(row["source_id"]))
    return sorted(values)


def _island_id(row: Mapping[str, Any], index: int) -> str:
    value = row.get("island_id")
    if value in (None, ""):
        raise FixtureDiversityError(f"island {index} has no stable island_id")
    return str(value)


def _duration(row: Mapping[str, Any]) -> float:
    if row.get("allocated_duration_s") is not None:
        value = float(row["allocated_duration_s"])
    elif row.get("start_s") is not None and row.get("end_s") is not None:
        value = float(row["end_s"]) - float(row["start_s"])
    elif row.get("duration_s") is not None:
        value = float(row["duration_s"])
    elif row.get("capacity_s") is not None:
        value = float(row["capacity_s"])
    else:
        raise FixtureDiversityError(f"island {row.get('island_id')!r} has no duration identity")
    if not math.isfinite(value) or value < 0.0:
        raise FixtureDiversityError(f"invalid island duration: {value!r}")
    return value


def _role_set(section: Mapping[str, Any]) -> Tuple[str, ...]:
    roles = {
        str(layer.get("role") or "full")
        for layer in section.get("layers") or []
        if isinstance(layer, Mapping)
    }
    return tuple(sorted(roles))


def _form_token(section: Mapping[str, Any]) -> Tuple[str, str, Tuple[str, ...]]:
    return (
        str(section.get("island_id") or ""),
        str(section.get("type") or section.get("section_type") or ""),
        _role_set(section),
    )


def _normalized_histogram(values: Iterable[Any]) -> Dict[str, float]:
    counts = Counter(canonical_json(value) for value in values)
    total = float(sum(counts.values()))
    if total <= 0.0:
        return {}
    return {key: counts[key] / total for key in sorted(counts)}


def fixture_projection(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the fixture-level semantic projection used by every comparison."""
    islands = _islands(candidate)
    sections = _sections(candidate)
    transitions = _transitions(candidate)

    deck_sequence: List[Dict[str, Any]] = []
    duration_by_island: Dict[str, float] = {}
    seen_islands: set[str] = set()
    for index, row in enumerate(islands):
        island_id = _island_id(row, index)
        if island_id in seen_islands:
            raise FixtureDiversityError(f"duplicate island_id: {island_id}")
        seen_islands.add(island_id)
        bpm = row.get("target_bpm", row.get("island_bpm"))
        key = row.get("target_key", row.get("island_key"))
        if bpm is None or key is None:
            raise FixtureDiversityError(f"island {island_id!r} has no exact deck identity")
        deck_sequence.append({
            "island_id": island_id,
            "target_bpm_hex": _float_identity(bpm),
            "target_key": int(key) % 12,
        })
        duration_by_island[island_id] = _duration(row)

    transition_histogram = _normalized_histogram(
        (
            str(row.get("technique") or row.get("type") or ""),
            str(row.get("curve") or ""),
        )
        for row in transitions
    )
    role_histogram = _normalized_histogram(_role_set(section) for section in sections)
    form_sequence = [_form_token(section) for section in sections]

    projection = {
        "source_ids": _source_ids(candidate, islands),
        "deck_sequence": deck_sequence,
        "duration_by_island": {
            island_id: _float_identity(duration_by_island[island_id])
            for island_id in sorted(duration_by_island)
        },
        "form_sequence": form_sequence,
        "role_occupancy_histogram": role_histogram,
        "transition_histogram": transition_histogram,
    }
    projection["fixture_identity"] = hashlib.sha256(
        canonical_json(projection).encode("utf-8")
    ).hexdigest()
    return projection


def fixture_id(candidate: Mapping[str, Any], projection: Optional[Mapping[str, Any]] = None) -> str:
    explicit = (
        candidate.get("fixture_id")
        or candidate.get("fixture_sha256")
        or candidate.get("arrangement_sha256")
    )
    if explicit not in (None, ""):
        return str(explicit)
    body = projection or fixture_projection(candidate)
    return str(body["fixture_identity"])


def jaccard_distance(left: Iterable[str], right: Iterable[str]) -> float:
    a = {str(value) for value in left}
    b = {str(value) for value in right}
    union = a | b
    if not union:
        return 0.0
    return 1.0 - len(a & b) / len(union)


def _sequence_distance(left: Sequence[Any], right: Sequence[Any]) -> float:
    """Normalized Levenshtein distance over canonical semantic tokens."""
    a = [canonical_json(value) for value in left]
    b = [canonical_json(value) for value in right]
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, left_value in enumerate(a, 1):
        current = [i]
        for j, right_value in enumerate(b, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_value != right_value),
            ))
        previous = current
    return previous[-1] / max(len(a), len(b))


def _duration_distance(left: Mapping[str, str], right: Mapping[str, str]) -> float:
    keys = sorted(set(left) | set(right))
    a = {key: float.fromhex(left[key]) if key in left else 0.0 for key in keys}
    b = {key: float.fromhex(right[key]) if key in right else 0.0 for key in keys}
    sum_a = sum(a.values())
    sum_b = sum(b.values())
    if sum_a <= EPS and sum_b <= EPS:
        return 0.0
    if sum_a <= EPS or sum_b <= EPS:
        return 1.0
    return 0.5 * sum(abs(a[key] / sum_a - b[key] / sum_b) for key in keys)


def _histogram_distance(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    keys = set(left) | set(right)
    return 0.5 * sum(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) for key in keys)


def fixture_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    a = fixture_projection(left)
    b = fixture_projection(right)
    axes = {
        "source_set": jaccard_distance(a["source_ids"], b["source_ids"]),
        "deck_sequence": _sequence_distance(a["deck_sequence"], b["deck_sequence"]),
        "island_duration": _duration_distance(a["duration_by_island"], b["duration_by_island"]),
        "form_sequence": _sequence_distance(a["form_sequence"], b["form_sequence"]),
        "role_occupancy": _histogram_distance(
            a["role_occupancy_histogram"], b["role_occupancy_histogram"]
        ),
        "transition_histogram": _histogram_distance(
            a["transition_histogram"], b["transition_histogram"]
        ),
    }
    chosen = dict(DEFAULT_WEIGHTS)
    if weights is not None:
        chosen.update({str(key): float(value) for key, value in weights.items()})
    unknown = set(chosen) - set(axes)
    if unknown:
        raise FixtureDiversityError(f"unknown distance weight: {sorted(unknown)[0]}")
    if any(value < 0.0 or not math.isfinite(value) for value in chosen.values()):
        raise FixtureDiversityError("distance weights must be finite and non-negative")
    total_weight = sum(chosen.values())
    if total_weight <= EPS:
        raise FixtureDiversityError("at least one distance weight must be positive")
    total = sum(axes[key] * chosen[key] for key in axes) / total_weight
    return {
        "left_fixture_id": fixture_id(left, a),
        "right_fixture_id": fixture_id(right, b),
        "axes": axes,
        "weights": {key: chosen[key] for key in sorted(chosen)},
        "total": total,
    }


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
    structural_axes = tuple(DEFAULT_WEIGHTS)
    discriminating_pairs = [
        row for row in matrix
        if any(float(row["axes"][axis]) > EPS for axis in structural_axes)
    ]
    if len(candidates) < 2:
        status = "insufficient_candidates"
    elif not discriminating_pairs:
        status = "non_discriminating"
    else:
        status = "discriminating"
    return {
        "status": status,
        "candidate_count": len(candidates),
        "fixture_ids": [fixture_id(candidate) for candidate in candidates],
        "structural_axes": list(structural_axes),
        "distance_matrix": matrix,
        "discriminating_pair_count": len(discriminating_pairs),
    }


def select_max_min(
    candidates: Sequence[Mapping[str, Any]],
    limit: int = 3,
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Greedy max-min shelf selection, only when the evidence can discriminate."""
    family = classify_candidate_family(candidates, weights)
    matrix = family["distance_matrix"]
    totals = [float(row["total"]) for row in matrix]
    if len(candidates) < 3:
        return {
            **family,
            "selection_status": "not_run_fewer_than_three_candidates",
            "selected_fixture_ids": [],
            "raw_total_distances": totals,
        }
    if not totals or max(totals) - min(totals) <= EPS:
        return {
            **family,
            "selection_status": "not_run_degenerate_distance_range",
            "selected_fixture_ids": [],
            "raw_total_distances": totals,
        }
    if family["status"] != "discriminating":
        return {
            **family,
            "selection_status": "not_run_non_discriminating_family",
            "selected_fixture_ids": [],
            "raw_total_distances": totals,
        }

    identifiers = [fixture_id(candidate) for candidate in candidates]
    pair_distance: Dict[Tuple[int, int], float] = {}
    for row in matrix:
        key = tuple(sorted((int(row["left_index"]), int(row["right_index"]))))
        pair_distance[key] = float(row["total"])

    mean_distance: Dict[int, float] = {}
    for index in range(len(candidates)):
        values = [
            pair_distance[tuple(sorted((index, other)))]
            for other in range(len(candidates))
            if other != index
        ]
        mean_distance[index] = sum(values) / len(values)

    first = min(
        range(len(candidates)),
        key=lambda index: (-mean_distance[index], identifiers[index], index),
    )
    selected = [first]
    target = min(max(1, int(limit)), len(candidates))
    while len(selected) < target:
        remaining = [index for index in range(len(candidates)) if index not in selected]
        best = min(
            remaining,
            key=lambda index: (
                -min(pair_distance[tuple(sorted((index, chosen)))] for chosen in selected),
                -mean_distance[index],
                identifiers[index],
                index,
            ),
        )
        selected.append(best)

    return {
        **family,
        "selection_status": "selected_max_min",
        "selected_fixture_ids": [identifiers[index] for index in selected],
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
