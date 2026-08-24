"""Deterministic derivation of governed multi-island fixture candidates.

The input is a public-safe survival matrix containing only opaque source IDs,
stable deck IDs, exact BPM/key identities, role capabilities, and capacities.
The derivation is deliberately bounded and non-evidentiary: failing to produce
the requested number of candidates within the attempt budget is a search bound,
not a claim that the matrix has no lawful fixture.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from earcrate.plan.fixture_diversity import fixture_projection

DERIVATION_VERSION = "earcrate_fixture_derivation_v1"
BEATS_PER_BAR = 4
EPS = 1e-9
INDETERMINATE_ACTION = "halt_candidate_campaign_this_is_not_an_impossibility_proof"
FORBIDDEN_SOURCE_FIELDS = {"path", "filepath", "filename", "artist", "title", "album"}


class FixtureDerivationError(ValueError):
    """The survival matrix or requested derivation contract is malformed."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def semantic_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_rank(seed: int, *parts: Any) -> int:
    body = "|".join([str(int(seed)), *[str(part) for part in parts]])
    return int(hashlib.sha256(body.encode("utf-8")).hexdigest(), 16)


def phrase_seconds(bpm: float, phrase_bars: int) -> float:
    if not math.isfinite(float(bpm)) or float(bpm) <= 0.0:
        raise FixtureDerivationError(f"invalid deck BPM: {bpm!r}")
    if int(phrase_bars) <= 0:
        raise FixtureDerivationError("phrase_bars must be positive")
    return int(phrase_bars) * BEATS_PER_BAR * 60.0 / float(bpm)


def transition_seconds(left_bpm: float, right_bpm: float) -> float:
    return min(BEATS_PER_BAR * 60.0 / float(left_bpm), BEATS_PER_BAR * 60.0 / float(right_bpm))


def _request_template(matrix: Mapping[str, Any]) -> Dict[str, Any]:
    template = copy.deepcopy(dict(matrix.get("request_template") or {}))
    for key in (
        "profile",
        "source_pool_sha256",
        "persona",
        "phrase_playback_law",
        "transform_policy",
        "turnover_policy",
        "transition",
        "source_exclude_ids",
    ):
        if key not in template and key in matrix:
            template[key] = copy.deepcopy(matrix[key])
    for key in ("profile", "source_pool_sha256", "persona", "phrase_playback_law"):
        if not str(template.get(key) or ""):
            raise FixtureDerivationError(f"request_template is missing {key}")
    if not bool((template.get("transform_policy") or {}).get("unchanged")):
        raise FixtureDerivationError("transform_policy must be explicitly unchanged")
    if not bool((template.get("turnover_policy") or {}).get("unchanged")):
        raise FixtureDerivationError("turnover_policy must be explicitly unchanged")
    transition = dict(template.get("transition") or {})
    if transition.get("technique") != "equal_power" or not bool(transition.get("phrase_boundary_required")):
        raise FixtureDerivationError("transition must require phrase-boundary equal_power joins")
    template["source_exclude_ids"] = sorted({str(value) for value in template.get("source_exclude_ids") or []})
    return template


def _source_rows(raw: Mapping[str, Any], global_roles: Mapping[str, Set[str]]) -> Dict[str, Set[str]]:
    rows = raw.get("sources")
    if rows is None:
        rows = [
            {"source_id": value, "roles": sorted(global_roles.get(str(value), set()))}
            for value in raw.get("source_ids") or []
        ]
    out: Dict[str, Set[str]] = {}
    for index, item in enumerate(rows or []):
        if isinstance(item, str):
            source_id = str(item)
            roles = set(global_roles.get(source_id, set()))
        elif isinstance(item, Mapping):
            forbidden = FORBIDDEN_SOURCE_FIELDS.intersection(str(key).lower() for key in item)
            if forbidden:
                raise FixtureDerivationError(
                    f"deck {raw.get('deck_id')!r} source {index} carries forbidden identity field {sorted(forbidden)[0]!r}"
                )
            source_id = str(item.get("source_id") or "")
            roles = {str(value) for value in item.get("roles") or global_roles.get(source_id, set())}
        else:
            raise FixtureDerivationError(f"deck {raw.get('deck_id')!r} has a non-object source row")
        if not source_id:
            raise FixtureDerivationError(f"deck {raw.get('deck_id')!r} source {index} has no source_id")
        if not roles:
            raise FixtureDerivationError(f"source {source_id!r} has no role capability")
        out.setdefault(source_id, set()).update(roles)
    if not out:
        raise FixtureDerivationError(f"deck {raw.get('deck_id')!r} has no surviving sources")
    return out


def normalize_matrix(matrix: Mapping[str, Any]) -> Dict[str, Any]:
    duration_s = float(matrix.get("duration_s") or 0.0)
    island_count = int(matrix.get("island_count") or 0)
    phrase_bars = int(matrix.get("phrase_bars") or 4)
    candidate_count = int(matrix.get("candidate_count") or 3)
    base_seed = int(matrix.get("base_seed") or 0)
    max_attempts = int(matrix.get("max_attempts") or max(64, candidate_count * 64))
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise FixtureDerivationError("duration_s must be positive")
    if island_count <= 0:
        raise FixtureDerivationError("island_count must be positive")
    if phrase_bars <= 0:
        raise FixtureDerivationError("phrase_bars must be positive")
    if candidate_count <= 0:
        raise FixtureDerivationError("candidate_count must be positive")
    if max_attempts <= 0:
        raise FixtureDerivationError("max_attempts must be positive")

    required_roles = [str(value) for value in matrix.get("required_roles") or ("foreground", "bass", "floor")]
    if not required_roles:
        raise FixtureDerivationError("required_roles cannot be empty")
    global_roles = {
        str(source_id): {str(role) for role in roles or []}
        for source_id, roles in dict(matrix.get("source_roles") or {}).items()
    }

    decks: List[Dict[str, Any]] = []
    seen_decks: Set[str] = set()
    for index, raw in enumerate(matrix.get("decks") or []):
        if not isinstance(raw, Mapping):
            raise FixtureDerivationError(f"deck {index} is not an object")
        deck_id = str(raw.get("deck_id") or "")
        if not deck_id:
            raise FixtureDerivationError(f"deck {index} has no deck_id")
        if deck_id in seen_decks:
            raise FixtureDerivationError(f"duplicate deck_id: {deck_id}")
        seen_decks.add(deck_id)
        bpm = float(raw.get("target_bpm") or 0.0)
        key = int(raw.get("target_key") if raw.get("target_key") is not None else -1)
        capacity_s = float(raw.get("capacity_s") or 0.0)
        if key < 0:
            raise FixtureDerivationError(f"deck {deck_id!r} has no target_key")
        if not math.isfinite(capacity_s) or capacity_s <= 0.0:
            raise FixtureDerivationError(f"deck {deck_id!r} has invalid capacity_s")
        one_phrase = phrase_seconds(bpm, phrase_bars)
        max_phrases = int(math.floor((capacity_s + EPS) / one_phrase))
        sources = _source_rows(raw, global_roles)
        deck_required = [str(value) for value in raw.get("required_roles") or required_roles]
        min_sources = int(raw.get("min_sources") or max(1, len(deck_required)))
        max_sources = int(raw.get("max_sources") or len(sources))
        if min_sources <= 0 or max_sources < min_sources:
            raise FixtureDerivationError(f"deck {deck_id!r} has invalid source bounds")
        if max_sources > len(sources):
            max_sources = len(sources)
        decks.append({
            "deck_id": deck_id,
            "target_bpm": bpm,
            "target_key": key % 12,
            "capacity_s": capacity_s,
            "phrase_seconds": one_phrase,
            "max_phrases": max_phrases,
            "sources": sources,
            "required_roles": deck_required,
            "min_sources": min_sources,
            "max_sources": max_sources,
        })
    if len(decks) < island_count:
        raise FixtureDerivationError(
            f"matrix has {len(decks)} deck(s), fewer than island_count {island_count}"
        )
    template = _request_template(matrix)
    excluded = set(template.get("source_exclude_ids") or [])
    for deck in decks:
        deck["sources"] = {
            source_id: roles
            for source_id, roles in deck["sources"].items()
            if source_id not in excluded
        }
        if not deck["sources"]:
            raise FixtureDerivationError(
                f"deck {deck['deck_id']!r} has no sources after declared exclusions"
            )
        deck["max_sources"] = min(int(deck["max_sources"]), len(deck["sources"]))
        if int(deck["min_sources"]) > int(deck["max_sources"]):
            raise FixtureDerivationError(
                f"deck {deck['deck_id']!r} cannot meet min_sources after declared exclusions"
            )
    normalized = {
        "kind": "earcrate_fixture_survival_matrix",
        "schema_version": 1,
        "duration_s": duration_s,
        "island_count": island_count,
        "phrase_bars": phrase_bars,
        "candidate_count": candidate_count,
        "base_seed": base_seed,
        "max_attempts": max_attempts,
        "target_source_count": (
            int(matrix["target_source_count"])
            if matrix.get("target_source_count") is not None
            else None
        ),
        "arrangement_seed": int(matrix.get("arrangement_seed") or 0),
        "required_roles": required_roles,
        "request_template": template,
        "decks": decks,
    }
    normalized["matrix_semantic_sha256"] = semantic_sha256({
        **normalized,
        "decks": [
            {
                **{key: value for key, value in deck.items() if key != "sources"},
                "sources": {
                    source_id: sorted(roles)
                    for source_id, roles in sorted(deck["sources"].items())
                },
            }
            for deck in sorted(decks, key=lambda row: row["deck_id"])
        ],
    })
    return normalized


def _role_complete(deck: Mapping[str, Any]) -> bool:
    roles = set().union(*(set(value) for value in deck["sources"].values()))
    return int(deck["max_phrases"]) > 0 and set(deck["required_roles"]).issubset(roles)


def _assign_required_sources(
    selected: Sequence[Mapping[str, Any]], seed: int
) -> Optional[Tuple[Dict[str, Set[str]], Dict[str, str]]]:
    deck_sources: Dict[str, Set[str]] = {str(deck["deck_id"]): set() for deck in selected}
    assigned: Dict[str, str] = {}

    def missing_requirements() -> List[Tuple[Mapping[str, Any], str]]:
        missing: List[Tuple[Mapping[str, Any], str]] = []
        for deck in selected:
            deck_id = str(deck["deck_id"])
            covered: Set[str] = set()
            for source_id in deck_sources[deck_id]:
                covered.update(deck["sources"][source_id])
            for role in deck["required_roles"]:
                if role not in covered:
                    missing.append((deck, str(role)))
        return missing

    def cover_roles() -> bool:
        missing = missing_requirements()
        if not missing:
            return True
        ranked: List[Tuple[int, int, str, str, Mapping[str, Any], List[str]]] = []
        for deck, role in missing:
            deck_id = str(deck["deck_id"])
            candidates = [
                source_id
                for source_id, roles in deck["sources"].items()
                if role in roles
                and assigned.get(source_id) in (None, deck_id)
                and (
                    assigned.get(source_id) == deck_id
                    or len(deck_sources[deck_id]) < int(deck["max_sources"])
                )
            ]
            ranked.append((
                len(candidates),
                stable_rank(seed, "requirement", deck_id, role),
                deck_id,
                role,
                deck,
                candidates,
            ))
        _count, _rank, deck_id, role, deck, candidates = min(
            ranked, key=lambda row: (row[0], row[1], row[2], row[3])
        )
        candidates.sort(key=lambda source_id: (
            assigned.get(source_id) is None,
            stable_rank(seed, "role-source", deck_id, role, source_id),
            source_id,
        ))
        for source_id in candidates:
            new_assignment = assigned.get(source_id) is None
            if new_assignment:
                assigned[source_id] = deck_id
                deck_sources[deck_id].add(source_id)
            if cover_roles():
                return True
            if new_assignment:
                deck_sources[deck_id].remove(source_id)
                del assigned[source_id]
        return False

    if not cover_roles():
        return None

    def source_deficits() -> List[Tuple[Mapping[str, Any], int]]:
        return [
            (deck, int(deck["min_sources"]) - len(deck_sources[str(deck["deck_id"])]))
            for deck in selected
            if len(deck_sources[str(deck["deck_id"])]) < int(deck["min_sources"])
        ]

    def fill_minima() -> bool:
        deficits = source_deficits()
        if not deficits:
            return True
        ranked = []
        for deck, deficit in deficits:
            deck_id = str(deck["deck_id"])
            candidates = [
                source_id
                for source_id in deck["sources"]
                if source_id not in assigned
                and len(deck_sources[deck_id]) < int(deck["max_sources"])
            ]
            ranked.append((
                len(candidates),
                -deficit,
                stable_rank(seed, "minimum", deck_id),
                deck_id,
                deck,
                candidates,
            ))
        _count, _deficit, _rank, deck_id, deck, candidates = min(
            ranked, key=lambda row: (row[0], row[1], row[2], row[3])
        )
        candidates.sort(key=lambda source_id: (
            sum(source_id in candidate["sources"] for candidate in selected),
            stable_rank(seed, "minimum-source", deck_id, source_id),
            source_id,
        ))
        for source_id in candidates:
            assigned[source_id] = deck_id
            deck_sources[deck_id].add(source_id)
            if fill_minima():
                return True
            deck_sources[deck_id].remove(source_id)
            del assigned[source_id]
        return False

    if not fill_minima():
        return None
    return deck_sources, assigned


def _fill_extra_sources(
    selected: Sequence[Mapping[str, Any]],
    deck_sources: Dict[str, Set[str]],
    assigned: Dict[str, str],
    target_count: int,
    seed: int,
) -> bool:
    if target_count <= len(assigned):
        return True
    deck_by_id = {str(deck["deck_id"]): deck for deck in selected}
    slots: List[Tuple[str, int]] = []
    for deck in selected:
        deck_id = str(deck["deck_id"])
        remaining = int(deck["max_sources"]) - len(deck_sources[deck_id])
        slots.extend((deck_id, index) for index in range(max(0, remaining)))

    all_sources = set().union(*(set(deck["sources"]) for deck in selected))
    unassigned = sorted(
        all_sources - set(assigned),
        key=lambda source_id: (
            sum(source_id in deck["sources"] for deck in selected),
            stable_rank(seed, "extra-source", source_id),
            source_id,
        ),
    )
    source_slots = {
        source_id: [
            slot for slot in slots
            if source_id in deck_by_id[slot[0]]["sources"]
        ]
        for source_id in unassigned
    }
    match_slot: Dict[Tuple[str, int], str] = {}
    match_source: Dict[str, Tuple[str, int]] = {}

    def augment(source_id: str, visited: Set[Tuple[str, int]]) -> bool:
        preferences = sorted(
            source_slots[source_id],
            key=lambda slot: (
                stable_rank(seed, "extra-slot", source_id, slot[0], slot[1]),
                slot,
            ),
        )
        for slot in preferences:
            if slot in visited:
                continue
            visited.add(slot)
            owner = match_slot.get(slot)
            if owner is None or augment(owner, visited):
                match_slot[slot] = source_id
                match_source[source_id] = slot
                return True
        return False

    for source_id in unassigned:
        augment(source_id, set())
    needed = target_count - len(assigned)
    if len(match_source) < needed:
        return False
    chosen = sorted(
        match_source,
        key=lambda source_id: (
            stable_rank(seed, "chosen-extra", source_id),
            source_id,
        ),
    )[:needed]
    for source_id in chosen:
        deck_id = match_source[source_id][0]
        assigned[source_id] = deck_id
        deck_sources[deck_id].add(source_id)
    return True


def _allocate_phrases(
    selected: Sequence[Mapping[str, Any]],
    duration_s: float,
    phrase_bars: int,
    seed: int,
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]]:
    last_choices = sorted(
        selected,
        key=lambda deck: (
            stable_rank(seed, "last-deck", deck["deck_id"]),
            str(deck["deck_id"]),
        ),
    )
    for last in last_choices:
        others = [deck for deck in selected if deck is not last]
        others.sort(key=lambda deck: (
            stable_rank(seed, "deck-order", deck["deck_id"]),
            str(deck["deck_id"]),
        ))
        order = others + [last]
        if any(int(deck["max_phrases"]) <= 0 for deck in order):
            continue
        used = {str(deck["deck_id"]): 1 for deck in order}

        def gross(deck: Mapping[str, Any]) -> float:
            return used[str(deck["deck_id"])] * float(deck["phrase_seconds"])

        def net_duration() -> float:
            return (
                sum(gross(deck) for deck in order)
                - sum(
                    transition_seconds(float(left["target_bpm"]), float(right["target_bpm"]))
                    for left, right in zip(order, order[1:])
                )
            )

        while net_duration() + EPS < duration_s:
            available = [
                deck
                for deck in order
                if used[str(deck["deck_id"])] < int(deck["max_phrases"])
            ]
            if not available:
                break
            available.sort(key=lambda deck: (
                0 if deck is last else 1,
                used[str(deck["deck_id"])] / int(deck["max_phrases"]),
                stable_rank(seed, "phrase", used[str(deck["deck_id"])], deck["deck_id"]),
                str(deck["deck_id"]),
            ))
            used[str(available[0]["deck_id"])] += 1
        if net_duration() + EPS < duration_s:
            continue
        if sum(gross(deck) for deck in order[:-1]) + EPS >= duration_s:
            continue

        rows: List[Dict[str, Any]] = []
        transitions: List[Dict[str, Any]] = []
        cursor = 0.0
        for index, deck in enumerate(order):
            if index:
                overlap = transition_seconds(
                    float(order[index - 1]["target_bpm"]),
                    float(deck["target_bpm"]),
                )
                cursor -= overlap
                transitions.append({
                    "from_deck_id": str(order[index - 1]["deck_id"]),
                    "to_deck_id": str(deck["deck_id"]),
                    "at_phrase_boundary": True,
                    "technique": "equal_power",
                    "curve": "equal_power",
                    "duration_s": overlap,
                    "start_s": cursor,
                    "end_s": cursor + overlap,
                })
            start = cursor
            allocation = gross(deck)
            cursor += allocation
            rows.append({
                "deck": deck,
                "used_phrases": used[str(deck["deck_id"])],
                "allocated_duration_s": allocation,
                "start_s": start,
                "end_s": cursor,
            })
        return rows, transitions, cursor
    return None


def _replay_phrase_allocation(
    capacity_rows: Sequence[Mapping[str, Any]],
    duration_s: float,
    phrase_bars: int,
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]]:
    """Replay the planner's existing allocation law over conservative ceilings."""
    rows: List[Dict[str, Any]] = []
    remaining = float(duration_s)
    for raw in capacity_rows:
        deck = raw["deck"]
        one_phrase = phrase_seconds(float(deck["target_bpm"]), phrase_bars)
        max_phrases = int(math.floor((float(raw["allocated_duration_s"]) + EPS) / one_phrase))
        needed = max(1, int(math.ceil(max(0.0, remaining) / one_phrase - EPS)))
        used = min(max_phrases, needed)
        rows.append({
            "deck": deck,
            "capacity_s": float(raw["allocated_duration_s"]),
            "phrase_seconds": one_phrase,
            "max_phrases": max_phrases,
            "used_phrases": used,
            "allocated_duration_s": used * one_phrase,
        })
        remaining -= used * one_phrase
        if remaining <= EPS:
            break
    if remaining > EPS or len(rows) != len(capacity_rows):
        return None

    def place() -> Tuple[List[Dict[str, Any]], float]:
        transitions: List[Dict[str, Any]] = []
        cursor = 0.0
        for index, row in enumerate(rows):
            if index:
                overlap = transition_seconds(
                    float(rows[index - 1]["deck"]["target_bpm"]),
                    float(row["deck"]["target_bpm"]),
                )
                cursor -= overlap
                transitions.append({
                    "from_deck_id": str(rows[index - 1]["deck"]["deck_id"]),
                    "to_deck_id": str(row["deck"]["deck_id"]),
                    "at_phrase_boundary": True,
                    "technique": "equal_power",
                    "curve": "equal_power",
                    "duration_s": overlap,
                    "start_s": cursor,
                    "end_s": cursor + overlap,
                })
            row["start_s"] = cursor
            cursor += float(row["allocated_duration_s"])
            row["end_s"] = cursor
        return transitions, cursor

    transitions, net_duration = place()
    while net_duration + EPS < duration_s:
        for row in reversed(rows):
            if int(row["used_phrases"]) < int(row["max_phrases"]):
                row["used_phrases"] = int(row["used_phrases"]) + 1
                row["allocated_duration_s"] = (
                    int(row["used_phrases"]) * float(row["phrase_seconds"])
                )
                transitions, net_duration = place()
                break
        else:
            return None
    return rows, transitions, net_duration


def _attempt_candidate(
    normalized: Mapping[str, Any],
    seed: int,
    attempt_index: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    eligible = [deck for deck in normalized["decks"] if _role_complete(deck)]
    ranked = sorted(
        eligible,
        key=lambda deck: (
            stable_rank(seed, "deck", deck["deck_id"]),
            str(deck["deck_id"]),
        ),
    )
    selected = ranked[: int(normalized["island_count"])]
    if len(selected) < int(normalized["island_count"]):
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "failure_class": "insufficient_role_complete_decks",
            "impossibility_claimed": False,
        }

    allocation = _allocate_phrases(
        selected,
        float(normalized["duration_s"]),
        int(normalized["phrase_bars"]),
        seed,
    )
    if allocation is None:
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in selected],
            "failure_class": "selected_decks_do_not_meet_phrase_aligned_duration",
            "impossibility_claimed": False,
        }
    replayed = _replay_phrase_allocation(
        allocation[0],
        float(normalized["duration_s"]),
        int(normalized["phrase_bars"]),
    )
    if replayed is None:
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in selected],
            "failure_class": "planner_allocation_replay_did_not_use_every_selected_deck",
            "impossibility_claimed": False,
        }
    allocated, raw_transitions, net_duration = replayed
    ordered_decks = [row["deck"] for row in allocated]

    required = _assign_required_sources(ordered_decks, seed)
    if required is None:
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in ordered_decks],
            "failure_class": "selected_decks_have_no_distinct_role_complete_partition",
            "impossibility_claimed": False,
        }
    deck_sources, assigned = required
    all_sources = set().union(*(set(deck["sources"]) for deck in ordered_decks))
    capacity = sum(int(deck["max_sources"]) for deck in ordered_decks)
    requested_count = normalized.get("target_source_count")
    target_count = (
        int(requested_count)
        if requested_count is not None
        else min(len(all_sources), capacity)
    )
    if target_count > len(all_sources) or target_count > capacity:
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in ordered_decks],
            "failure_class": "selected_decks_cannot_reach_declared_target_source_count",
            "target_source_count": target_count,
            "available_unique_source_count": len(all_sources),
            "partition_capacity": capacity,
            "impossibility_claimed": False,
        }
    if target_count < len(assigned):
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in ordered_decks],
            "failure_class": "declared_target_source_count_below_required_partition",
            "target_source_count": target_count,
            "required_partition_source_count": len(assigned),
            "impossibility_claimed": False,
        }
    if not _fill_extra_sources(
        ordered_decks, deck_sources, assigned, target_count, seed
    ):
        return None, {
            "attempt_index": attempt_index,
            "derivation_seed": seed,
            "selected_deck_ids": [str(deck["deck_id"]) for deck in ordered_decks],
            "failure_class": "selected_decks_cannot_reach_target_source_count",
            "target_source_count": target_count,
            "impossibility_claimed": False,
        }

    template = copy.deepcopy(dict(normalized["request_template"]))
    candidate: Dict[str, Any] = {
        **template,
        "kind": "earcrate_fixture_candidate",
        "schema_version": 1,
        "duration_s": float(normalized["duration_s"]),
        "seed": int(normalized["arrangement_seed"]),
        "fixture_derivation_seed": int(seed),
        "phrase_bars": int(normalized["phrase_bars"]),
        "islands": [],
        "transitions": [],
    }
    island_id_by_deck: Dict[str, str] = {}
    for index, row in enumerate(allocated):
        deck = row["deck"]
        deck_id = str(deck["deck_id"])
        island_id = f"isl-{index:02d}-{hashlib.sha256(deck_id.encode('utf-8')).hexdigest()[:8]}"
        island_id_by_deck[deck_id] = island_id
        candidate["islands"].append({
            "island_id": island_id,
            "deck_id": deck_id,
            "target_bpm": float(deck["target_bpm"]),
            "target_key": int(deck["target_key"]),
            "capacity_s": float(row["capacity_s"]),
            "survival_capacity_s": float(deck["capacity_s"]),
            "allocated_duration_s": float(row["allocated_duration_s"]),
            "start_s": float(row["start_s"]),
            "end_s": float(row["end_s"]),
            "source_include_ids": sorted(deck_sources[deck_id]),
            "required_roles": list(deck["required_roles"]),
            "min_sources": int(deck["min_sources"]),
            "max_sources": int(deck["max_sources"]),
        })
    for transition in raw_transitions:
        candidate["transitions"].append({
            **transition,
            "from_island": island_id_by_deck[transition["from_deck_id"]],
            "to_island": island_id_by_deck[transition["to_deck_id"]],
        })

    projection = fixture_projection(candidate)
    semantic_identity = str(projection["fixture_identity"])
    candidate["fixture_id"] = f"season002-{semantic_identity[:12]}"
    candidate["fixture_sha256"] = semantic_identity
    candidate["fixture_derivation"] = {
        "version": DERIVATION_VERSION,
        "matrix_semantic_sha256": str(normalized["matrix_semantic_sha256"]),
        "attempt_index": int(attempt_index),
        "derivation_seed": int(seed),
        "arrangement_seed": int(normalized["arrangement_seed"]),
        "selected_deck_ids": [str(row["deck"]["deck_id"]) for row in allocated],
        "assigned_source_count": len(assigned),
        "target_source_count": target_count,
        "net_duration_s": float(net_duration),
        "transform_policy": "unchanged",
        "turnover_policy": "unchanged",
        "transition_policy": "phrase_boundary_equal_power",
    }
    return candidate, {
        "attempt_index": attempt_index,
        "derivation_seed": seed,
        "fixture_id": candidate["fixture_id"],
        "semantic_fixture_identity": semantic_identity,
        "selected_deck_ids": candidate["fixture_derivation"]["selected_deck_ids"],
        "assigned_source_count": len(assigned),
        "net_duration_s": float(net_duration),
        "disposition": "candidate_derived",
        "impossibility_claimed": False,
    }


def derive_fixture_candidates(
    matrix: Mapping[str, Any],
    candidate_count: Optional[int] = None,
    base_seed: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = normalize_matrix(matrix)
    requested = int(candidate_count if candidate_count is not None else normalized["candidate_count"])
    start_seed = int(base_seed if base_seed is not None else normalized["base_seed"])
    budget = int(max_attempts if max_attempts is not None else normalized["max_attempts"])
    if requested <= 0 or budget <= 0:
        raise FixtureDerivationError("candidate_count and max_attempts must be positive")

    candidates: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for attempt_index in range(budget):
        seed = start_seed + attempt_index
        candidate, receipt = _attempt_candidate(normalized, seed, attempt_index)
        if candidate is None:
            attempts.append(receipt)
            continue
        semantic_identity = str(candidate["fixture_sha256"])
        if semantic_identity in seen:
            attempts.append({
                **receipt,
                "disposition": "duplicate_semantic_fixture",
            })
            continue
        seen.add(semantic_identity)
        candidates.append(candidate)
        attempts.append(receipt)
        if len(candidates) >= requested:
            break

    complete = len(candidates) >= requested
    return {
        "kind": "earcrate_fixture_derivation_receipt",
        "version": DERIVATION_VERSION,
        "matrix_semantic_sha256": str(normalized["matrix_semantic_sha256"]),
        "requested_candidate_count": requested,
        "derived_candidate_count": len(candidates),
        "attempt_budget": budget,
        "attempts_executed": len(attempts),
        "complete": complete,
        "impossibility_claimed": False,
        "private_acceptance": (
            None if complete else INDETERMINATE_ACTION
        ),
        "candidates": candidates,
        "attempts": attempts,
    }


__all__ = [
    "DERIVATION_VERSION",
    "FixtureDerivationError",
    "INDETERMINATE_ACTION",
    "derive_fixture_candidates",
    "normalize_matrix",
    "phrase_seconds",
    "transition_seconds",
]
