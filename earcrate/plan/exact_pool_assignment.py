"""Deterministic complete assignment repair for exact-pool island composition.

``source_rotation`` repairs an exact-pool arrangement greedily. It walks the
missing sources in seed order, commits each replacement immediately, and will
only steal a slot whose current donor already holds two or more events. Jam
Season 001 showed both rules wrong on the dominant fixture shape: island 7 holds
24 mandatory sources across 33 slots and a feasible assignment exists, yet 22 of
the 24 sources are singletons in every successful plan, so the donor guard hides
almost every slot; and a flexible source visited early can consume the only slot
a later bass-only source could have used.

This module runs only after that greedy path has already refused. It rebuilds
the problem as one constrained assignment over the existing arrangement and
solves it atomically with deterministic augmenting paths, so a singleton donor
may be relocated and an earlier placement revised without ever passing through a
state where a mandatory source is uncovered. A genuine capacity deficit refuses
with a Hall witness rather than blaming whichever source happened to be visited
last.

Nothing here creates slots, changes a slot's musical role, broadens
compatibility, relaxes a transform, raises the reuse cap, or drops a mandatory
source. The successful greedy path never reaches this module, and its frozen
``exact_pool_rotation`` ledger is returned untouched.

Helpers are read off the ``source_rotation`` module object at call time on
purpose. ``key_identity.install_key_identity`` rebinds
``source_rotation._transform_for_slot`` so exact-deck slots obey the strict key
rule; importing that function by value would silently restore the weaker
pre-key-identity transform law.
"""
from __future__ import annotations

from collections import Counter, deque
import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import earcrate.plan.source_rotation as _rotation

EXACT_POOL_ASSIGNMENT_VERSION = "exact_pool_assignment_v1"

#: Input-order independence is a measured property, not a declared one. The
#: frozen ``exact_pool_rotation`` block asserts it as a literal; new repair-path
#: provenance names the witness that measures it, the permutations that witness
#: exercises, and what it observed.
INPUT_ORDER_INDEPENDENCE_EVIDENCE = {
    "witness": "tests/test_exact_pool_rotation.py::test_repair_assignment_is_input_order_independent",
    "permutations_exercised": [
        "pool_source_order_reversed",
        "per_source_atom_order_reversed",
        "section_declaration_order_reversed",
        "pool_mapping_insertion_order_reversed",
    ],
    "observed_outcome": "identical_slot_assignment_and_identical_repair_ledger",
}

PATH_INDEPENDENCE_WITNESS = (
    "tests/test_exact_pool_rotation.py::test_repair_assignment_ignores_local_filesystem_path"
)


class ExactPoolAssignmentError(_rotation.ExactPoolRotationError):
    """No complete assignment exists over the existing role slots.

    ``deficiency`` carries the structured mathematical evidence. It is a
    refusal object, not arrangement identity, so it never forces an already
    successful authority to re-seal.
    """

    def __init__(self, message: str, deficiency: Mapping[str, Any]):
        super().__init__(message)
        self.deficiency = dict(deficiency)


def _stable_source_identity(item: Mapping[str, Any]) -> Optional[str]:
    """Stable source identity only. No artist/title guess, no path hash."""
    explicit = item.get("source_track_key") or item.get("source_id")
    if explicit in (None, ""):
        return None
    return str(explicit)


def _stable_atom_identity(item: Mapping[str, Any]) -> Optional[str]:
    for key in ("atom_id", "id", "loop_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _slot_table(arrangement: Mapping[str, Any]) -> List[Tuple[Tuple[int, int], int, int, Dict[str, Any]]]:
    """Slot identity from musical position, not from declaration order.

    ``bar_start`` is where the section actually sits in the form, so reversing
    the section list cannot change which slot is which. Sections without unique
    bar starts carry no positional identity, so those fall back to declaration
    order rather than collide.
    """
    sections = list(arrangement.get("sections") or [])
    bar_starts: List[int] = []
    for index, section in enumerate(sections):
        value = section.get("bar_start")
        bar_starts.append(int(value) if value is not None else index)
    if len(set(bar_starts)) != len(bar_starts):
        bar_starts = list(range(len(sections)))
    table: List[Tuple[Tuple[int, int], int, int, Dict[str, Any]]] = []
    for section_index, section in enumerate(sections):
        for layer_index, layer in enumerate(section.get("layers") or []):
            table.append(((bar_starts[section_index], layer_index), section_index, layer_index, layer))
    return table


def _weak_identity_report(pool: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    weak: List[Dict[str, Any]] = []
    for index, item in enumerate(pool):
        source_id = _stable_source_identity(item)
        atom_id = _stable_atom_identity(item)
        if source_id is not None and atom_id is not None:
            continue
        weak.append({
            "pool_index": index,
            "has_stable_source_identity": source_id is not None,
            "has_stable_atom_or_loop_identity": atom_id is not None,
            "source_identity": source_id,
        })
    return weak


def _build_edges(
    core: Any,
    frozen_sections: Sequence[Mapping[str, Any]],
    slots: Sequence[Tuple[Tuple[int, int], int, int, Dict[str, Any]]],
    ordered_sources: Sequence[str],
    pool_by_source: Mapping[str, Sequence[Dict[str, Any]]],
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    seed: int,
) -> Tuple[Dict[Tuple[int, int], Dict[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]], Dict[str, Counter]]:
    """Compatibility graph over the *unmodified* arrangement.

    Scoring reads counterparts from a frozen snapshot so an edge never depends
    on how many repairs have already been applied. Edge existence is the whole
    of the compatibility law: role family, transform safety at the exact deck,
    and score admissibility.
    """
    all_items = [item for source_id in ordered_sources for item in pool_by_source[source_id]]
    by_atom = {_rotation._atom_identity(item): item for item in all_items if _rotation._atom_identity(item)}
    by_loop = {_rotation._loop_identity(item): item for item in all_items if _rotation._loop_identity(item)}

    reach: Dict[str, Counter] = {source_id: Counter() for source_id in ordered_sources}
    transform_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    edges: Dict[Tuple[int, int], Dict[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]] = {}

    for slot_key, section_index, layer_index, layer in slots:
        slot_role = str(layer.get("role") or "full")
        section = frozen_sections[section_index]
        best_by_source: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]] = {}
        for source_id in ordered_sources:
            best: Optional[Tuple[tuple, Dict[str, Any], Dict[str, Any]]] = None
            for candidate in pool_by_source[source_id]:
                if not _rotation._role_compatible(slot_role, candidate):
                    continue
                reach[source_id]["role_compatible"] += 1
                cache_key = (str(_stable_atom_identity(candidate)), slot_role)
                if cache_key in transform_cache:
                    transform = transform_cache[cache_key]
                else:
                    transform = _rotation._transform_for_slot(candidate, slot_role, render_bpm, target_key, params)
                    transform_cache[cache_key] = transform
                if transform is None:
                    continue
                reach[source_id]["transform_safe"] += 1
                score = _rotation._candidate_score(
                    core,
                    candidate,
                    section,
                    layer_index,
                    slot_role,
                    transform,
                    render_bpm,
                    target_key,
                    params,
                    by_atom,
                    by_loop,
                    seed,
                )
                if score is None:
                    continue
                reach[source_id]["score_admissible"] += 1
                rank = (
                    *score,
                    _rotation._stable_rank(seed, "atom", slot_key[0], slot_key[1], source_id, _stable_atom_identity(candidate)),
                )
                if best is None or rank > best[0]:
                    best = (rank, candidate, dict(transform))
            if best is not None:
                best_by_source[source_id] = (best[1], best[2], best[0])
                reach[source_id]["slots"] += 1
        edges[slot_key] = best_by_source
    return edges, reach


def _slot_preferences(
    edges: Mapping[Tuple[int, int], Mapping[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]],
    ordered_sources: Sequence[str],
    seed: int,
) -> Dict[str, List[Tuple[int, int]]]:
    preferences: Dict[str, List[Tuple[int, int]]] = {}
    for source_id in ordered_sources:
        rows = [(slot_key, row[source_id][2]) for slot_key, row in edges.items() if source_id in row]
        rows.sort(
            key=lambda entry: (
                entry[1],
                _rotation._stable_rank(seed, "prefer", source_id, entry[0][0], entry[0][1]),
            ),
            reverse=True,
        )
        preferences[source_id] = [entry[0] for entry in rows]
    return preferences


def _match_every_source(
    ordered_sources: Sequence[str],
    preferences: Mapping[str, Sequence[Tuple[int, int]]],
    current: Mapping[Tuple[int, int], str],
) -> Tuple[Dict[str, Tuple[int, int]], Dict[Tuple[int, int], str]]:
    """Anchor every mandatory source to one distinct slot.

    Seeded from where each source already sits so a plan is revised only where
    it must be, then completed by augmenting paths. The augmenting step is what
    the old ``count <= 1`` guard made impossible: displacing a singleton donor is
    fine here because the donor is re-anchored inside the same search, and
    nothing is written until the whole matching exists.
    """
    match_source: Dict[str, Tuple[int, int]] = {}
    match_slot: Dict[Tuple[int, int], str] = {}

    for source_id in ordered_sources:
        held = [
            slot_key
            for slot_key in preferences[source_id]
            if current.get(slot_key) == source_id and slot_key not in match_slot
        ]
        if held:
            match_source[source_id] = held[0]
            match_slot[held[0]] = source_id

    def augment(source_id: str, visited: set) -> bool:
        for slot_key in preferences[source_id]:
            if slot_key in visited:
                continue
            visited.add(slot_key)
            owner = match_slot.get(slot_key)
            if owner is None or augment(owner, visited):
                match_slot[slot_key] = source_id
                match_source[source_id] = slot_key
                return True
        return False

    for source_id in ordered_sources:
        if source_id not in match_source:
            augment(source_id, set())
    return match_source, match_slot


def _hall_witness(
    unmatched: Sequence[str],
    preferences: Mapping[str, Sequence[Tuple[int, int]]],
    match_slot: Mapping[Tuple[int, int], str],
) -> Tuple[List[str], List[Tuple[int, int]]]:
    """König-reachable deficient source set and its exact slot neighbourhood."""
    reached_sources = list(unmatched)
    seen_sources = set(unmatched)
    seen_slots: set = set()
    queue = deque(unmatched)
    while queue:
        source_id = queue.popleft()
        for slot_key in preferences[source_id]:
            if slot_key in seen_slots:
                continue
            seen_slots.add(slot_key)
            owner = match_slot.get(slot_key)
            if owner is not None and owner not in seen_sources:
                seen_sources.add(owner)
                reached_sources.append(owner)
                queue.append(owner)
    return sorted(reached_sources), sorted(seen_slots)


def _cap_relief_path(
    donor: str,
    assign: Mapping[Tuple[int, int], str],
    anchors: Mapping[str, Tuple[int, int]],
    counts: Mapping[str, int],
    edges: Mapping[Tuple[int, int], Mapping[str, Any]],
    max_events: int,
    seed: int,
) -> Optional[List[Tuple[str, Tuple[int, int], str]]]:
    """Shortest deterministic chain that moves one event off ``donor``.

    The chain may traverse a receiver that is itself already at the cap: that
    receiver takes the incoming slot and hands one of its own onward, so its own
    count is unchanged. Only the donor loses an event and only the final
    under-cap receiver gains one. A source's anchor slot is never movable, so no
    hop can strand a mandatory source.
    """
    previous: Dict[str, Optional[Tuple[str, Tuple[int, int]]]] = {donor: None}
    queue = deque([donor])
    target: Optional[str] = None
    while queue and target is None:
        holder = queue.popleft()
        movable = sorted(
            (slot_key for slot_key, owner in assign.items() if owner == holder and anchors.get(holder) != slot_key),
            key=lambda slot_key: (_rotation._stable_rank(seed, "release", holder, slot_key[0], slot_key[1]), slot_key),
        )
        for slot_key in movable:
            receivers = sorted(
                (receiver for receiver in edges[slot_key] if receiver != holder and receiver not in previous),
                key=lambda receiver: (
                    counts.get(receiver, 0),
                    _rotation._stable_rank(seed, "receive", receiver, slot_key[0], slot_key[1]),
                    receiver,
                ),
            )
            for receiver in receivers:
                previous[receiver] = (holder, slot_key)
                if counts.get(receiver, 0) < max_events:
                    target = receiver
                    break
                queue.append(receiver)
            if target is not None:
                break
    if target is None:
        return None
    chain: List[Tuple[str, Tuple[int, int], str]] = []
    node = target
    while previous[node] is not None:
        holder, slot_key = previous[node]
        chain.append((holder, slot_key, node))
        node = holder
    chain.reverse()
    return chain


def repair_exact_pool_assignment(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
    legacy_error: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """Solve the exact-pool assignment atomically, or refuse with a witness."""
    out: Dict[str, Any] = copy.deepcopy(dict(arrangement))
    seed = int(seed)
    render_bpm = float(out.get("bpm") or params.get("exact_target_bpm") or params.get("bpm") or 0.0)
    target_key = int(out.get("target_key") if out.get("target_key") is not None else params.get("exact_target_key") or 0) % 12
    max_events = int(params.get("exact_pool_max_source_events") or _rotation.DEFAULT_MAX_SOURCE_EVENTS)

    weak = _weak_identity_report([dict(item) for item in pool])
    if weak:
        raise ExactPoolAssignmentError(
            f"exact pool assignment refuses: {len(weak)} pool item(s) carry no stable source and atom identity",
            {
                "failure_class": "stable_identity_absent",
                "reason": "matching requires a stable source key plus a stable atom or loop id; a local path may not decide assignment",
                "unstable_pool_items": weak[:32],
                "unstable_pool_item_count": len(weak),
            },
        )

    pool_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in (dict(item) for item in pool):
        pool_by_source.setdefault(str(_stable_source_identity(item)), []).append(item)
    for source_id in pool_by_source:
        pool_by_source[source_id].sort(key=lambda item: str(_stable_atom_identity(item)))
    ordered_sources = _rotation._stable_source_order(pool_by_source, seed)

    slots = _slot_table(out)
    frozen_sections = copy.deepcopy(list(out.get("sections") or []))
    edges, reach = _build_edges(
        core, frozen_sections, slots, ordered_sources, pool_by_source, render_bpm, target_key, params, seed
    )
    role_of_slot = {slot_key: str(layer.get("role") or "full") for slot_key, _si, _li, layer in slots}
    current = {slot_key: _rotation._layer_source(layer) for slot_key, _si, _li, layer in slots}
    preferences = _slot_preferences(edges, ordered_sources, seed)

    match_source, match_slot = _match_every_source(ordered_sources, preferences, current)
    unmatched = [source_id for source_id in ordered_sources if source_id not in match_source]
    if unmatched:
        raise _coverage_refusal(unmatched, ordered_sources, pool_by_source, preferences, match_slot, reach, role_of_slot, slots)

    before = Counter(source_id for source_id in current.values() if source_id)
    assign: Dict[Tuple[int, int], str] = dict(current)
    for source_id, slot_key in match_source.items():
        assign[slot_key] = source_id
    counts = Counter(source_id for source_id in assign.values() if source_id)

    cap_paths: List[Dict[str, Any]] = []
    guard = 0
    guard_limit = len(slots) * max(1, len(ordered_sources)) + len(slots) + 8
    while True:
        over_cap = sorted(
            (source_id for source_id, count in counts.items() if count > max_events),
            key=lambda source_id: (-counts[source_id], _rotation._stable_rank(seed, "overcap", source_id), source_id),
        )
        if not over_cap:
            break
        guard += 1
        if guard > guard_limit:
            raise ExactPoolAssignmentError(
                "exact pool assignment cap relief failed to converge",
                {
                    "failure_class": "cap_constraint",
                    "reason": "cap relief exceeded its deterministic step bound",
                    "declared_max_source_events": max_events,
                    "over_cap_sources": {source_id: int(counts[source_id]) for source_id in over_cap},
                },
            )
        donor = over_cap[0]
        chain = _cap_relief_path(donor, assign, match_source, counts, edges, max_events, seed)
        if chain is None:
            raise _cap_refusal(donor, counts, assign, match_source, edges, role_of_slot, max_events)
        for holder, slot_key, receiver in chain:
            assign[slot_key] = receiver
            counts[holder] -= 1
            counts[receiver] += 1
        cap_paths.append({
            "donor": donor,
            "hops": [
                {"from_source": holder, "to_source": receiver, "bar_start": slot_key[0], "layer_index": slot_key[1]}
                for holder, slot_key, receiver in chain
            ],
            "receiver": chain[-1][2],
            "traversed_full_receiver": len(chain) > 1,
        })

    replacements: List[Dict[str, Any]] = []
    singleton_relocations = 0
    for slot_key, section_index, layer_index, layer in slots:
        target_source = assign[slot_key]
        donor_source = current[slot_key]
        if target_source == donor_source:
            continue
        candidate, transform, _rank = edges[slot_key][target_source]
        slot_role = role_of_slot[slot_key]
        _rotation._apply_candidate(layer, candidate, slot_role, transform)
        if before.get(donor_source, 0) == 1:
            singleton_relocations += 1
        replacements.append({
            "reason": "reuse_cap" if match_source.get(target_source) != slot_key else "missing_source",
            "section_index": section_index,
            "layer_index": layer_index,
            "role": slot_role,
            "from_source": donor_source,
            "to_source": target_source,
            "to_atom": _rotation._atom_identity(candidate),
        })

    used = {source_id for source_id, count in counts.items() if count > 0}
    missing_after = sorted(set(ordered_sources) - used)
    if missing_after:
        raise ExactPoolAssignmentError(
            f"exact pool assignment left source unused: {missing_after[0]}",
            {"failure_class": "role_capacity", "reason": "post-assignment coverage check failed", "unmatched_sources": missing_after},
        )
    if counts and max(counts.values()) > max_events:
        offender = max(sorted(counts), key=lambda source_id: counts[source_id])
        raise ExactPoolAssignmentError(
            f"exact pool assignment left {offender!r} above cap",
            {"failure_class": "cap_constraint", "reason": "post-assignment cap check failed", "declared_max_source_events": max_events},
        )

    ledger = out.setdefault("taste_ledger", {})
    ledger["exact_pool_rotation"] = {
        "version": _rotation.EXACT_POOL_ROTATION_VERSION,
        "target_source_count": len(ordered_sources),
        "used_source_count": len(used & set(ordered_sources)),
        "max_source_events": max_events,
        "observed_max_source_events": max(counts.values()) if counts else 0,
        "replacement_count": len(replacements),
        "source_event_counts": {source_id: int(counts.get(source_id, 0)) for source_id in sorted(ordered_sources)},
        "replacements": replacements,
    }
    ledger["exact_pool_assignment"] = {
        "version": EXACT_POOL_ASSIGNMENT_VERSION,
        "invoked_because": "greedy_exact_pool_path_refused",
        "greedy_refusal": str(legacy_error) if legacy_error is not None else "",
        "method": "deterministic_augmenting_path_complete_assignment",
        "mandatory_source_count": len(ordered_sources),
        "slot_count": len(slots),
        "anchor_relocation_count": sum(
            1 for source_id, slot_key in match_source.items() if current.get(slot_key) != source_id
        ),
        "singleton_donor_relocation_count": singleton_relocations,
        "cap_relief_paths": cap_paths,
        "identity_basis": "stable_source_key_plus_stable_atom_or_loop_id",
        "assignment_identity_fields": ["source_track_key|source_id", "atom_id|id|loop_id", "role", "bar_start", "layer_index", "seed"],
        "path_independence_witness": PATH_INDEPENDENCE_WITNESS,
        "input_order_independence_evidence": copy.deepcopy(INPUT_ORDER_INDEPENDENCE_EVIDENCE),
    }
    return out


def _coverage_refusal(
    unmatched: Sequence[str],
    ordered_sources: Sequence[str],
    pool_by_source: Mapping[str, Sequence[Dict[str, Any]]],
    preferences: Mapping[str, Sequence[Tuple[int, int]]],
    match_slot: Mapping[Tuple[int, int], str],
    reach: Mapping[str, Counter],
    role_of_slot: Mapping[Tuple[int, int], str],
    slots: Sequence[Tuple[Tuple[int, int], int, int, Dict[str, Any]]],
) -> ExactPoolAssignmentError:
    deficient_sources, neighbourhood = _hall_witness(list(unmatched), preferences, match_slot)
    role_capabilities = {
        source_id: sorted({_rotation._natural_role(item) for item in pool_by_source[source_id]})
        for source_id in unmatched
    }
    family_counts: Counter = Counter(_rotation._role_family(role) for role in role_of_slot.values())

    isolated = [source_id for source_id in unmatched if not preferences[source_id]]
    failure_class = "role_capacity"
    reason = (
        "the compatible slot neighbourhood of a deficient source subset is smaller than the subset itself, "
        "so no complete assignment exists at any visit order"
    )
    if isolated:
        probe = reach[isolated[0]]
        if not probe["role_compatible"]:
            failure_class = "role_capacity"
            reason = "a mandatory source has no role-compatible slot in the existing arrangement"
        elif not probe["transform_safe"]:
            failure_class = "transform_safety"
            reason = "a mandatory source is role-compatible but transform-unsafe at the exact deck in every compatible slot"
        else:
            failure_class = "score_admissibility"
            reason = "a mandatory source is role-compatible and transform-safe but score-inadmissible in every compatible slot"

    roles_named = sorted({role for roles in role_capabilities.values() for role in roles})
    message = (
        f"exact pool has no complete assignment: {len(unmatched)} source(s) unreachable in existing slots; "
        f"roles={roles_named}; deficient subset {len(deficient_sources)} source(s) reach only "
        f"{len(neighbourhood)} compatible slot(s)"
    )
    return ExactPoolAssignmentError(message, {
        "failure_class": failure_class,
        "reason": reason,
        "unmatched_source_count": len(unmatched),
        "unmatched_sources": sorted(unmatched),
        "unmatched_role_capabilities": role_capabilities,
        "compatible_slot_count_by_role_family": {family: int(count) for family, count in sorted(family_counts.items())},
        "candidate_slots_considered": len(slots),
        "compatible_slot_count_per_unmatched_source": {
            source_id: len(preferences[source_id]) for source_id in sorted(unmatched)
        },
        "hall_witness": {
            "deficient_source_subset": deficient_sources,
            "deficient_source_count": len(deficient_sources),
            "compatible_slot_neighbourhood": [list(slot_key) for slot_key in neighbourhood],
            "neighbourhood_slot_count": len(neighbourhood),
            "deficiency": len(deficient_sources) - len(neighbourhood),
            "minimality": "koenig_reachable_deficient_set_not_proven_cardinality_minimal",
        },
        "mandatory_source_count": len(ordered_sources),
    })


def _cap_refusal(
    donor: str,
    counts: Mapping[str, int],
    assign: Mapping[Tuple[int, int], str],
    anchors: Mapping[str, Tuple[int, int]],
    edges: Mapping[Tuple[int, int], Mapping[str, Any]],
    role_of_slot: Mapping[Tuple[int, int], str],
    max_events: int,
) -> ExactPoolAssignmentError:
    held = sorted(slot_key for slot_key, owner in assign.items() if owner == donor)
    movable = [slot_key for slot_key in held if anchors.get(donor) != slot_key]
    under_cap = sorted(source_id for source_id, count in counts.items() if count < max_events)
    return ExactPoolAssignmentError(
        f"source {donor!r} remains at {counts.get(donor, 0)} events; no augmenting path reaches an under-cap source",
        {
            "failure_class": "cap_constraint",
            "reason": "every releasable slot of the over-cap source reaches only sources that are themselves at the cap with nothing left to release",
            "declared_max_source_events": max_events,
            "over_cap_source": donor,
            "over_cap_event_count": int(counts.get(donor, 0)),
            "releasable_slot_count": len(movable),
            "releasable_slot_roles": sorted({role_of_slot[slot_key] for slot_key in movable}),
            "reachable_receiver_count": len({receiver for slot_key in movable for receiver in edges[slot_key] if receiver != donor}),
            "under_cap_source_count": len(under_cap),
            "under_cap_sources": under_cap,
        },
    )


__all__ = [
    "EXACT_POOL_ASSIGNMENT_VERSION",
    "INPUT_ORDER_INDEPENDENCE_EVIDENCE",
    "ExactPoolAssignmentError",
    "repair_exact_pool_assignment",
]
