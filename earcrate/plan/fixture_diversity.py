"""Structural diversity accounting for governed multi-island fixture candidates.

The seed is deliberately absent from every distance axis. A fixture comparison
asks whether the source universe, exact decks, island allocation, form, role
occupancy, or transition vocabulary changed. Arrangement hashes, audio hashes,
and display labels remain provenance; they are not evidence that the music's
governing fixture moved.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DEFAULT_WEIGHTS: Dict[str, float] = {
    # Equal weights are the neutral repository default. A campaign may supply an
    # explicit public contract, but private observations never silently tune this
    # selector.
    "source_set": 1.0,
    "source_partition": 1.0,
    "deck_sequence": 1.0,
    "island_duration": 1.0,
    "form_sequence": 1.0,
    "role_occupancy": 1.0,
    "transition_histogram": 1.0,
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

    indexed = list(enumerate(rows))
    if rows and all(row.get("start_s") is not None for row in rows):
        indexed.sort(
            key=lambda item: (
                float(item[1]["start_s"]),
                float(item[1].get("end_s") or item[1]["start_s"]),
                str(item[1].get("island_id") or ""),
            )
        )
    elif rows and all(row.get("island_id") not in (None, "") for row in rows):
        indexed.sort(key=lambda item: str(item[1]["island_id"]))
    return [row for _index, row in indexed]


def _sections(candidate: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    arrangement = candidate.get("arrangement")
    body = arrangement if isinstance(arrangement, Mapping) else candidate
    rows = list(body.get("sections") or [])
    if not all(isinstance(row, Mapping) for row in rows):
        raise FixtureDiversityError("sections must be mappings")

    indexed = list(enumerate(rows))
    if rows and all(row.get("start_s") is not None for row in rows):
        indexed.sort(
            key=lambda item: (
                float(item[1]["start_s"]),
                float(item[1].get("end_s") or item[1]["start_s"]),
                item[0],
            )
        )
    elif rows and all(row.get("bar_start") is not None for row in rows):
        indexed.sort(
            key=lambda item: (
                float(item[1].get("bar_start") or 0.0),
                item[0],
            )
        )
    return [row for _index, row in indexed]


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


def _section_span_token(section: Mapping[str, Any]) -> Tuple[str, Any]:
    if section.get("bars") is not None:
        return ("bars", int(section["bars"]))
    if section.get("start_s") is not None and section.get("end_s") is not None:
        return ("seconds_hex", _float_identity(float(section["end_s"]) - float(section["start_s"])))
    return ("unspecified", "")


def _form_token(
    section: Mapping[str, Any],
    island_position: Mapping[str, int],
) -> Tuple[int, str, Tuple[str, ...], Tuple[str, Any], str]:
    label = str(section.get("island_id") or "")
    position = island_position.get(label, -1)
    transition = section.get("transition_in")
    transition_type = (
        str(transition.get("technique") or transition.get("type") or "")
        if isinstance(transition, Mapping)
        else ""
    )
    return (
        position,
        str(section.get("type") or section.get("section_type") or ""),
        _role_set(section),
        _section_span_token(section),
        transition_type,
    )


def _normalized_histogram(values: Iterable[Any]) -> Dict[str, float]:
    counts = Counter(canonical_json(value) for value in values)
    total = float(sum(counts.values()))
    if total <= 0.0:
        return {}
    return {key: counts[key] / total for key in sorted(counts)}


def fixture_projection(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the fixture-level semantic projection used by every comparison.

    Island labels are used only to join sections to their musical island position.
    They are absent from the projection. Renaming ``island-001`` to ``deck-b`` or
    respelling an arrangement hash therefore cannot manufacture diversity.
    """
    islands = _islands(candidate)
    sections = _sections(candidate)
    transitions = _transitions(candidate)

    deck_sequence: List[Dict[str, Any]] = []
    duration_sequence: List[str] = []
    source_partition: List[List[str]] = []
    island_position: Dict[str, int] = {}
    for index, row in enumerate(islands):
        island_id = _island_id(row, index)
        if island_id in island_position:
            raise FixtureDiversityError(f"duplicate island_id: {island_id}")
        island_position[island_id] = index
        bpm = row.get("target_bpm", row.get("island_bpm"))
        key = row.get("target_key", row.get("island_key"))
        if bpm is None or key is None:
            raise FixtureDiversityError(f"island {island_id!r} has no exact deck identity")
        deck_sequence.append({
            "target_bpm_hex": _float_identity(bpm),
            "target_key": int(key) % 12,
        })
        duration_sequence.append(_float_identity(_duration(row)))
        partition_values: set[str] = set()
        for field in ("source_allowlist", "source_include_ids", "source_ids"):
            partition_values.update(str(value) for value in row.get(field) or [] if str(value))
        source_partition.append(sorted(partition_values))

    transition_histogram = _normalized_histogram(
        (
            str(row.get("technique") or row.get("type") or ""),
            str(row.get("curve") or ""),
        )
        for row in transitions
    )
    role_histogram = _normalized_histogram(_role_set(section) for section in sections)
    form_sequence = [_form_token(section, island_position) for section in sections]

    projection: Dict[str, Any] = {
        "source_ids": _source_ids(candidate, islands),
        "source_partition": source_partition,
        "deck_sequence": deck_sequence,
        "duration_sequence": duration_sequence,
        "form_sequence": form_sequence,
        "role_occupancy_histogram": role_histogram,
        "transition_histogram": transition_histogram,
    }
    projection["fixture_identity"] = hashlib.sha256(
        canonical_json(projection).encode("utf-8")
    ).hexdigest()
    return projection


def fixture_id(candidate: Mapping[str, Any], projection: Optional[Mapping[str, Any]] = None) -> str:
    explicit = candidate.get("fixture_id") or candidate.get("fixture_sha256")
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


def _duration_distance(left: Sequence[str], right: Sequence[str]) -> float:
    a = [float.fromhex(value) for value in left]
    b = [float.fromhex(value) for value in right]
    width = max(len(a), len(b))
    if width == 0:
        return 0.0
    a += [0.0] * (width - len(a))
    b += [0.0] * (width - len(b))
    sum_a = sum(a)
    sum_b = sum(b)
    if sum_a <= EPS and sum_b <= EPS:
        return 0.0
    if sum_a <= EPS or sum_b <= EPS:
        return 1.0
    return 0.5 * sum(abs(x / sum_a - y / sum_b) for x, y in zip(a, b))


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
        "source_partition": _sequence_distance(a["source_partition"], b["source_partition"]),
        "deck_sequence": _sequence_distance(a["deck_sequence"], b["deck_sequence"]),
        "island_duration": _duration_distance(a["duration_sequence"], b["duration_sequence"]),
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
