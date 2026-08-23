"""One deterministic exact-pool assignment authority.

``source_rotation`` owns the exact-pool arrangement. Its original depth-1 walk is
kept as an *identity-preserving fast path*: when that walk produces an assignment
that satisfies the laws below, the authority accepts its bytes unchanged and this
module builds nothing. It is a proposal, not a second authority — the acceptance
test lives here (:func:`accept_fast_path_proposal`), so a proposal is verified
rather than trusted, and nothing is "rescued" after a failure.

When no acceptable proposal exists, :func:`solve_exact_pool_assignment` solves the
whole problem instead of patching one:

*   **Coverage.** Every allowlisted source must occupy at least one slot.
*   **Capacity.** No source may exceed ``exact_pool_max_source_events``.
*   **Occupancy.** Every existing slot keeps a source; slots are never created,
    dropped, re-roled, or emptied.
*   **Compatibility.** A source may take a slot only through an atom that is role
    compatible, transform safe at the exact deck, and score admissible *against
    the section state that is actually published* — not against a snapshot.

The solver is three phases, and the decomposition is the completeness argument:

1.  A source-saturating matching over the compatibility graph. If none exists,
    coverage is impossible at any visit order and the refusal carries a
    König-reachable deficient source subset with its exact slot neighbourhood.
2.  Fill every slot under the cap alone, seeded from the arrangement as it stands
    and completed by deterministic augmenting exchange. A slot whose incumbent is
    already at the cap is simply not seeded, so relief and placement are the same
    search: any occurrence of any source may move, including the occurrence that
    phase 1 matched. By the standard alternating-path argument this fills the
    maximum number of slots, so a slot it cannot fill cannot be filled at all.
3.  Give every still-uncovered source an occurrence by residual exchange along a
    chain that ends at a source holding two or more. Each hop hands one slot on
    and takes one back, so no source is ever emptied and no cap is ever raised.
    If phase 1 succeeded this phase cannot fail: a failed search would exhibit a
    source set reaching strictly fewer slots than itself, which is exactly the
    Hall violation phase 1 ruled out.

Jam Season 001 is why phases 2 and 3 are separate from phase 1 rather than layered
over it. Pinning each source's matched occurrence and then relieving the cap around
those pins does *not* solve the two constraints together: a bounded assignment can
require moving the matched occurrence itself, taking the incoming slot as the
replacement occurrence in the same atomic chain. Nothing here is pinned.

The published pairing is then validated as a whole. Scores are ranked against the
unmodified snapshot — that keeps ranking independent of how much repair has already
happened — but admissibility is decided against the finished sections, so two layers
that are each admissible against their old counterparts and inadmissible together
are caught before publication rather than after. A violated pair becomes a forbidden
co-occurrence and the search runs again honouring it. Forbidding the pair rather than
withdrawing an edge matters: an edge withdrawal both discards a lawful placement and
lets the same pair reappear in a different section. The constraints are exact; the
search over them is monotone rather than exhaustive, so it can refuse where a
deeper search would not — see the ``completeness`` block of the emitted ledger.

Nothing here creates slots, changes a slot's musical role, broadens compatibility,
relaxes a transform, raises the reuse cap, or drops a mandatory source.

Helpers are read off the ``source_rotation`` module object at call time on purpose.
``key_identity.install_key_identity`` rebinds ``source_rotation._transform_for_slot``
so exact-deck slots obey the strict key rule; importing that function by value would
silently restore the weaker pre-key-identity transform law.
"""
from __future__ import annotations

from collections import Counter, deque
import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import earcrate.plan.source_rotation as _rotation

EXACT_POOL_ASSIGNMENT_VERSION = "exact_pool_assignment_v2"

#: Slot identity is musical position — ``(bar_start, layer_index)``.
SlotKey = Tuple[int, int]

#: Provenance names a witness only when that witness measures the named property.
#: ``test_provenance_witnesses_resolve_to_discovered_tests`` proves every string
#: below still resolves to a real discovered test.
PATH_INDEPENDENCE_WITNESS = (
    "tests/test_exact_pool_rotation.py::"
    "test_repair_requires_stable_identity_and_ignores_local_filesystem_path"
)

INPUT_ORDER_INDEPENDENCE_EVIDENCE = {
    "witness": (
        "tests/test_exact_pool_rotation.py::"
        "test_repair_is_identical_under_equivalent_input_permutations"
    ),
    "permutations_exercised": [
        "pool_source_order_reversed",
        "per_source_atom_order_reversed",
        "section_declaration_order_reversed",
        "pool_mapping_insertion_order_reversed",
    ],
    "compared_by_the_witness": [
        "every_layer_body_at_equal_musical_position",
        "exact_pool_rotation_ledger_including_every_replacement_record",
        "exact_pool_assignment_ledger",
    ],
    "observed_outcome": "identical_at_every_compared_field",
}

FINAL_PAIR_VALIDATION_WITNESS = (
    "tests/test_exact_pool_rotation.py::test_repair_validates_the_section_pair_it_publishes"
)

COVERAGE_CAP_EXCHANGE_WITNESS = (
    "tests/test_exact_pool_rotation.py::test_repair_moves_a_matched_occurrence_to_hold_the_cap"
)

PROVENANCE_WITNESSES = (
    PATH_INDEPENDENCE_WITNESS,
    INPUT_ORDER_INDEPENDENCE_EVIDENCE["witness"],
    FINAL_PAIR_VALIDATION_WITNESS,
    COVERAGE_CAP_EXCHANGE_WITNESS,
)

COMPLETENESS_STATEMENT = {
    "coverage_and_capacity": "complete_over_the_constructed_compatibility_graph",
    "argument": (
        "phase one refuses with a Hall witness unless a source-saturating matching exists; "
        "phase two fills the maximum number of slots under the cap by alternating exchange, "
        "so an unfillable slot is genuinely unfillable; phase three then covers every "
        "remaining source by residual exchange, and its failure would exhibit the very Hall "
        "violation phase one ruled out. No occurrence is pinned during either phase."
    ),
    "section_pair_constraints": "exact_constraints_under_a_monotone_non_exhaustive_search",
    "section_pair_note": (
        "every published pair is validated against the finished sections, and a violated pair "
        "becomes a forbidden co-occurrence the search honours from then on. The constraint is "
        "exact — it forbids the pair, never the placements — but the search over those "
        "constraints is monotone rather than exhaustive, so it refuses when it cannot avoid a "
        "co-occurrence it has already been told to avoid. Every constraint it learned is listed "
        "in forbidden_final_pairs."
    ),
}


class ExactPoolAssignmentError(_rotation.ExactPoolRotationError):
    """No complete assignment exists over the existing role slots.

    ``deficiency`` carries the structured mathematical evidence. It is a refusal
    object, not arrangement identity, so it never forces an already successful
    authority to re-seal.
    """

    def __init__(self, message: str, deficiency: Mapping[str, Any]):
        super().__init__(message)
        self.deficiency = dict(deficiency)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


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


def _slot_table(
    arrangement: Mapping[str, Any]
) -> List[Tuple[SlotKey, int, int, Dict[str, Any]]]:
    """Slot identity from musical position, not from declaration order.

    ``bar_start`` is where the section actually sits in the form, so reversing the
    section list cannot change which slot is which, and a receipt keyed on position
    is the same receipt under any equivalent input permutation. Sections without
    unique bar starts carry no positional identity, so those fall back to
    declaration order rather than collide.
    """
    sections = list(arrangement.get("sections") or [])
    bar_starts: List[int] = []
    for index, section in enumerate(sections):
        value = section.get("bar_start")
        bar_starts.append(int(value) if value is not None else index)
    if len(set(bar_starts)) != len(bar_starts):
        bar_starts = list(range(len(sections)))
    table: List[Tuple[SlotKey, int, int, Dict[str, Any]]] = []
    for section_index, section in enumerate(sections):
        for layer_index, layer in enumerate(section.get("layers") or []):
            table.append(((bar_starts[section_index], layer_index), section_index, layer_index, layer))
    table.sort(key=lambda row: row[0])
    return table


def _pool_maps(items: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Mapping[str, Any]], Dict[str, Mapping[str, Any]]]:
    by_atom = {_rotation._atom_identity(item): item for item in items if _rotation._atom_identity(item)}
    by_loop = {_rotation._loop_identity(item): item for item in items if _rotation._loop_identity(item)}
    return by_atom, by_loop


# ---------------------------------------------------------------------------
# Compatibility graph
# ---------------------------------------------------------------------------


def _build_edges(
    core: Any,
    frozen_sections: Sequence[Mapping[str, Any]],
    slots: Sequence[Tuple[SlotKey, int, int, Dict[str, Any]]],
    ordered_sources: Sequence[str],
    pool_by_source: Mapping[str, Sequence[Dict[str, Any]]],
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    seed: int,
) -> Tuple[Dict[SlotKey, Dict[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]], Dict[str, Counter]]:
    """Compatibility graph over the unmodified arrangement.

    Edge existence is role family, transform safety at the exact deck, and score
    admissibility. Scoring reads counterparts from a frozen snapshot so an edge's
    *rank* never depends on how many repairs have already been applied; whether the
    published pairing is admissible is decided later, against the finished sections,
    by :func:`_final_pair_violations`.
    """
    all_items = [item for source_id in ordered_sources for item in pool_by_source[source_id]]
    by_atom, by_loop = _pool_maps(all_items)

    reach: Dict[str, Counter] = {source_id: Counter() for source_id in ordered_sources}
    transform_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    edges: Dict[SlotKey, Dict[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]] = {}

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


def _pair_index(
    pairs: Sequence[Mapping[str, Any]]
) -> Dict[Tuple[SlotKey, str], List[Tuple[SlotKey, str]]]:
    """Forbidden co-occurrences, looked up from either end.

    A validated violation says *these two placements may not be published together*.
    It does not say either placement is bad, so the constraint is stored as the pair
    it is rather than withdrawn as an edge — withdrawing an edge would both lose a
    lawful placement and let the same pair reappear in another section.
    """
    index: Dict[Tuple[SlotKey, str], List[Tuple[SlotKey, str]]] = {}
    for pair in pairs:
        left = ((int(pair["bar_start"]), int(pair["layer_index"])), str(pair["source"]))
        right = ((int(pair["bar_start"]), int(pair["counterpart_layer_index"])), str(pair["counterpart_source"]))
        index.setdefault(left, []).append(right)
        index.setdefault(right, []).append(left)
    return index


def _pair_forbidden(
    slot_key: SlotKey,
    source_id: str,
    assign: Mapping[SlotKey, str],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> bool:
    for partner_slot, partner_source in forbidden.get((slot_key, source_id), ()):  # already validated as inadmissible
        if assign.get(partner_slot) == partner_source:
            return True
    return False


def _slot_preferences(
    edges: Mapping[SlotKey, Mapping[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]],
    ordered_sources: Sequence[str],
    seed: int,
) -> Dict[str, List[SlotKey]]:
    """For each source, every slot it can take, best first."""
    preferences: Dict[str, List[SlotKey]] = {}
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


def _slot_choices(
    edges: Mapping[SlotKey, Mapping[str, Tuple[Dict[str, Any], Dict[str, Any], tuple]]],
) -> Dict[SlotKey, List[str]]:
    """For each slot, every source that can take it, best first."""
    choices: Dict[SlotKey, List[str]] = {}
    for slot_key, row in edges.items():
        choices[slot_key] = [
            source_id
            for source_id, _edge in sorted(row.items(), key=lambda entry: (entry[1][2], entry[0]), reverse=True)
        ]
    return choices


# ---------------------------------------------------------------------------
# Phase 1 — coverage feasibility
# ---------------------------------------------------------------------------


def _match_every_source(
    ordered_sources: Sequence[str],
    preferences: Mapping[str, Sequence[SlotKey]],
    current: Mapping[SlotKey, str],
) -> Tuple[Dict[str, SlotKey], Dict[SlotKey, str]]:
    """One distinct slot per mandatory source, or the deficiency that forbids it.

    Seeded from where each source already sits so the witness describes the plan in
    front of us, then completed by augmenting paths. This matching is a *feasibility
    proof*, not a set of pins: phases 2 and 3 are free to move every occurrence it
    names. Its only job is to decide whether coverage is possible at all, and to
    produce an honest deficiency when it is not.
    """
    match_source: Dict[str, SlotKey] = {}
    match_slot: Dict[SlotKey, str] = {}

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
    preferences: Mapping[str, Sequence[SlotKey]],
    match_slot: Mapping[SlotKey, str],
) -> Tuple[List[str], List[SlotKey]]:
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


# ---------------------------------------------------------------------------
# Phase 2 — fill every slot under the cap
# ---------------------------------------------------------------------------


def _capacity_chain(
    start_slot: SlotKey,
    assign: Mapping[SlotKey, str],
    counts: Mapping[str, int],
    choices: Mapping[SlotKey, Sequence[str]],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
    max_events: int,
    seed: int,
) -> Tuple[Optional[List[Tuple[SlotKey, str]]], Dict[str, int]]:
    """Shortest deterministic exchange that gives ``start_slot`` a lawful source.

    Breadth first over sources. A source that is already at the cap is not a dead
    end: it may take the incoming slot and hand one of its own onward, leaving its
    own count unchanged, so only the final under-cap source gains an event. Every
    occurrence is movable — including one that phase 1's matching happened to name —
    because coverage is restored afterwards by phase 3 rather than pinned here.

    Returns the chain and, when there is none, the saturated set it explored: those
    sources hold ``max_events`` each and own every slot the search could reach, which
    is the capacity deficiency itself.
    """
    previous: Dict[str, Tuple[SlotKey, Optional[str]]] = {}
    queue: deque = deque()

    def offer(slot_key: SlotKey, holder: Optional[str]) -> Optional[str]:
        for source_id in choices.get(slot_key, ()):  # best first
            if source_id == holder or source_id in previous:
                continue
            if _pair_forbidden(slot_key, source_id, assign, forbidden):
                continue
            previous[source_id] = (slot_key, holder)
            if counts.get(source_id, 0) < max_events:
                return source_id
            queue.append(source_id)
        return None

    terminal = offer(start_slot, None)
    while terminal is None and queue:
        holder = queue.popleft()
        held = sorted(
            (slot_key for slot_key, owner in assign.items() if owner == holder),
            key=lambda slot_key: (_rotation._stable_rank(seed, "release", holder, slot_key[0], slot_key[1]), slot_key),
        )
        for slot_key in held:
            terminal = offer(slot_key, holder)
            if terminal is not None:
                break
    if terminal is None:
        return None, {source_id: int(counts.get(source_id, 0)) for source_id in sorted(previous)}

    chain: List[Tuple[SlotKey, str]] = []
    node: Optional[str] = terminal
    while node is not None:
        slot_key, parent = previous[node]
        chain.append((slot_key, node))
        node = parent
    chain.reverse()
    return chain, {}


def _fill_every_slot(
    slot_order: Sequence[SlotKey],
    choices: Mapping[SlotKey, Sequence[str]],
    edges: Mapping[SlotKey, Mapping[str, Any]],
    current: Mapping[SlotKey, str],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
    max_events: int,
    seed: int,
) -> Tuple[Optional[Dict[SlotKey, str]], Optional[Counter], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Every slot gets a lawful source, bounded by the reuse cap alone.

    The arrangement in front of us is the seed, so a plan is revised only where it
    must be. A slot whose incumbent would break the cap is simply left unseeded and
    filled by exchange, which is why cap relief needs no separate pass.
    """
    assign: Dict[SlotKey, str] = {}
    counts: Counter = Counter()
    for slot_key in slot_order:
        holder = current.get(slot_key) or ""
        if holder not in edges.get(slot_key, {}) or counts[holder] >= max_events:
            continue
        if _pair_forbidden(slot_key, holder, assign, forbidden):
            continue
        assign[slot_key] = holder
        counts[holder] += 1

    paths: List[Dict[str, Any]] = []
    for slot_key in slot_order:
        if slot_key in assign:
            continue
        chain, saturated = _capacity_chain(slot_key, assign, counts, choices, forbidden, max_events, seed)
        if chain is None:
            return None, None, paths, {"slot": slot_key, "saturated": saturated}
        hops: List[Dict[str, Any]] = []
        for hop_slot, receiver in chain:
            donor = assign.get(hop_slot, current.get(hop_slot) or "")
            if hop_slot in assign:
                counts[assign[hop_slot]] -= 1
            assign[hop_slot] = receiver
            counts[receiver] += 1
            hops.append({
                "bar_start": hop_slot[0],
                "layer_index": hop_slot[1],
                "from_source": donor,
                "to_source": receiver,
                "kept_by_incumbent": donor == receiver,
            })
        paths.append({
            "over_cap_slot": {"bar_start": slot_key[0], "layer_index": slot_key[1]},
            "incumbent": current.get(slot_key) or "",
            "hops": hops,
            "receiver": chain[-1][1],
            "traversed_full_receiver": len(chain) > 1,
        })
    return assign, counts, paths, None


# ---------------------------------------------------------------------------
# Phase 3 — cover every source
# ---------------------------------------------------------------------------


def _coverage_chain(
    source_id: str,
    preferences: Mapping[str, Sequence[SlotKey]],
    assign: Mapping[SlotKey, str],
    counts: Mapping[str, int],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> Optional[List[Tuple[SlotKey, str]]]:
    """Shortest residual exchange that gives ``source_id`` an occurrence.

    Each hop hands one slot to the source that needs it and obliges the donor to
    take one back, so only the last donor — which holds two or more — actually loses
    an event. Nothing exceeds the cap because only the uncovered source gains.
    """
    previous: Dict[str, Tuple[SlotKey, str]] = {}
    seen = {source_id}
    queue = deque([source_id])
    terminal: Optional[str] = None
    while queue and terminal is None:
        needy = queue.popleft()
        for slot_key in preferences[needy]:
            owner = assign.get(slot_key)
            if owner is None or owner == needy or owner in seen:
                continue
            if _pair_forbidden(slot_key, needy, assign, forbidden):
                continue
            seen.add(owner)
            previous[owner] = (slot_key, needy)
            if counts.get(owner, 0) > 1:
                terminal = owner
                break
            queue.append(owner)
    if terminal is None:
        return None
    chain: List[Tuple[SlotKey, str]] = []
    node = terminal
    while node != source_id:
        slot_key, receiver = previous[node]
        chain.append((slot_key, receiver))
        node = receiver
    chain.reverse()
    return chain


def _cover_every_source(
    ordered_sources: Sequence[str],
    preferences: Mapping[str, Sequence[SlotKey]],
    assign: Dict[SlotKey, str],
    counts: Counter,
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    paths: List[Dict[str, Any]] = []
    for source_id in ordered_sources:
        if counts.get(source_id, 0) > 0:
            continue
        chain = _coverage_chain(source_id, preferences, assign, counts, forbidden)
        if chain is None:
            return paths, source_id
        hops: List[Dict[str, Any]] = []
        for slot_key, receiver in chain:
            donor = assign[slot_key]
            counts[donor] -= 1
            assign[slot_key] = receiver
            counts[receiver] += 1
            hops.append({
                "bar_start": slot_key[0],
                "layer_index": slot_key[1],
                "from_source": donor,
                "to_source": receiver,
            })
        paths.append({
            "uncovered_source": source_id,
            "hops": hops,
            "released_by": hops[-1]["from_source"],
            "traversed_singleton_holder": len(hops) > 1,
        })
    return paths, None


# ---------------------------------------------------------------------------
# Published-pair validation
# ---------------------------------------------------------------------------


def _final_pair_violations(
    core: Any,
    sections: Sequence[Mapping[str, Any]],
    changed: Set[Tuple[int, int]],
    items: Sequence[Mapping[str, Any]],
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    seed: int,
) -> List[Dict[str, Any]]:
    """Re-judge the pairs this authority actually published.

    Two candidates can each be admissible against the counterpart they replaced and
    inadmissible against each other. So every layer this authority placed, and every
    layer whose counterpart it replaced, is scored again against the *finished*
    section. Layers it never touched, whose counterpart it never touched, are the
    ordinary composer's product and are left alone.
    """
    by_atom, by_loop = _pool_maps(items)
    violations: List[Dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        layers = list(section.get("layers") or [])
        moved = {layer_index for layer_index in range(len(layers)) if (section_index, layer_index) in changed}
        if not moved:
            continue
        moved_atoms = {str(layers[layer_index].get("atom_id") or "") for layer_index in moved}
        bar_start = int(section.get("bar_start") if section.get("bar_start") is not None else section_index)
        for layer_index, layer in enumerate(layers):
            counterpart = _rotation._counterpart_item(section, layer_index, by_atom, by_loop)
            counterpart_atom = _rotation._atom_identity(counterpart) if counterpart is not None else ""
            if layer_index not in moved and counterpart_atom not in moved_atoms:
                continue
            candidate = _rotation._current_item(layer, by_atom, by_loop)
            if candidate is None:
                continue
            slot_role = str(layer.get("role") or "full")
            transform = _rotation._transform_for_slot(candidate, slot_role, render_bpm, target_key, params)
            score = None
            if transform is not None:
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
            if score is not None:
                continue
            counterpart_layer_index = next(
                (
                    other_index
                    for other_index, other in enumerate(layers)
                    if other_index != layer_index and str(other.get("atom_id") or "") == counterpart_atom
                ),
                -1,
            )
            violations.append({
                "section_index": section_index,
                "bar_start": bar_start,
                "layer_index": layer_index,
                "role": slot_role,
                "source": _rotation._layer_source(layer),
                "atom": _rotation._atom_identity(candidate),
                "counterpart_layer_index": counterpart_layer_index,
                "counterpart_atom": counterpart_atom,
                "counterpart_source": (
                    _rotation._layer_source(layers[counterpart_layer_index])
                    if counterpart_layer_index >= 0
                    else ""
                ),
                "layer_moved": layer_index in moved,
                "moved_layer_indexes": sorted(moved),
            })
    return violations


def _recorded_pairs(violations: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Turn validated violations into forbidden co-occurrences, one per pair.

    The record is the pair, canonically ordered by layer index so the two ends of a
    mutual violation collapse to a single constraint, and keyed by musical position
    so it means the same thing under any equivalent input permutation.
    """
    recorded: Dict[Tuple[int, int, str, int, str], Dict[str, Any]] = {}
    for violation in violations:
        counterpart_layer_index = int(violation["counterpart_layer_index"])
        if counterpart_layer_index < 0 or not violation["counterpart_source"]:
            continue
        left = (int(violation["layer_index"]), str(violation["source"]))
        right = (counterpart_layer_index, str(violation["counterpart_source"]))
        if right < left:
            left, right = right, left
        key = (int(violation["bar_start"]), left[0], left[1], right[0], right[1])
        recorded.setdefault(key, {
            "bar_start": int(violation["bar_start"]),
            "layer_index": left[0],
            "source": left[1],
            "counterpart_layer_index": right[0],
            "counterpart_source": right[1],
            "forbidden_because": "inadmissible_against_each_other_in_the_published_section",
        })
    return [recorded[key] for key in sorted(recorded)]


# ---------------------------------------------------------------------------
# Fast-path acceptance
# ---------------------------------------------------------------------------


def accept_fast_path_proposal(
    core: Any,
    proposal: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
) -> Optional[Dict[str, Any]]:
    """Judge the depth-one proposal against the same laws the solver obeys.

    Returns ``None`` when the proposal is accepted — the authority then publishes it
    byte for byte — or the reason it is not. Coverage and the cap are re-derived from
    the proposed layers rather than read back from the proposal's own ledger, and the
    pairs it published are validated exactly as the solver's are. Layers it did not
    touch are not re-litigated here; they are the ordinary composer's product.
    """
    max_events = int(params.get("exact_pool_max_source_events") or _rotation.DEFAULT_MAX_SOURCE_EVENTS)
    render_bpm = float(proposal.get("bpm") or params.get("exact_target_bpm") or params.get("bpm") or 0.0)
    target_key = int(
        proposal.get("target_key") if proposal.get("target_key") is not None else params.get("exact_target_key") or 0
    ) % 12

    items = [dict(item) for item in pool]
    pool_sources = {
        _rotation._source_identity(item)
        for item in items
        if _rotation._source_identity(item)
    }
    sections = list(proposal.get("sections") or [])
    counts: Counter = Counter()
    for section in sections:
        for layer in section.get("layers") or []:
            counts[_rotation._layer_source(layer)] += 1

    uncovered = sorted(pool_sources - {source_id for source_id, count in counts.items() if count > 0})
    if uncovered:
        return {
            "disposition": "rejected_incomplete_coverage",
            "detail": f"the proposal leaves {len(uncovered)} mandatory source(s) unused: {uncovered[0]}",
        }
    over_cap = sorted(source_id for source_id in pool_sources if counts[source_id] > max_events)
    if over_cap:
        return {
            "disposition": "rejected_reuse_cap",
            "detail": f"the proposal leaves {over_cap[0]!r} at {counts[over_cap[0]]} events above a cap of {max_events}",
        }

    ledger = (proposal.get("taste_ledger") or {}).get("exact_pool_rotation") or {}
    changed = {
        (int(record.get("section_index", -1)), int(record.get("layer_index", -1)))
        for record in ledger.get("replacements") or []
    }
    changed = {row for row in changed if 0 <= row[0] < len(sections)}
    violations = _final_pair_violations(
        core, sections, changed, items, render_bpm, target_key, params, int(seed)
    )
    if violations:
        first = violations[0]
        return {
            "disposition": "rejected_published_pair",
            "detail": (
                f"bar {first['bar_start']} layer {first['layer_index']} is inadmissible against the "
                f"counterpart the proposal published"
            ),
            "violations": violations,
        }
    return None


# ---------------------------------------------------------------------------
# The solver
# ---------------------------------------------------------------------------


def solve_exact_pool_assignment(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
    fast_path: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Solve the exact-pool assignment atomically, or refuse with a witness."""
    original: Dict[str, Any] = copy.deepcopy(dict(arrangement))
    seed = int(seed)
    render_bpm = float(original.get("bpm") or params.get("exact_target_bpm") or params.get("bpm") or 0.0)
    target_key = int(
        original.get("target_key") if original.get("target_key") is not None else params.get("exact_target_key") or 0
    ) % 12
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
    items = [item for source_id in ordered_sources for item in pool_by_source[source_id]]

    slots = _slot_table(original)
    slot_order = [row[0] for row in slots]
    position_of = {row[0]: (row[1], row[2]) for row in slots}
    role_of_slot = {row[0]: str(row[3].get("role") or "full") for row in slots}
    current = {row[0]: _rotation._layer_source(row[3]) for row in slots}
    before = Counter(source_id for source_id in current.values() if source_id)
    frozen_sections = copy.deepcopy(list(original.get("sections") or []))

    edges, reach = _build_edges(
        core, frozen_sections, slots, ordered_sources, pool_by_source, render_bpm, target_key, params, seed
    )

    preferences = _slot_preferences(edges, ordered_sources, seed)
    choices = _slot_choices(edges)
    match_source, match_slot = _match_every_source(ordered_sources, preferences, current)
    unmatched = [source_id for source_id in ordered_sources if source_id not in match_source]
    if unmatched:
        raise _coverage_refusal(
            unmatched, ordered_sources, pool_by_source, preferences, match_slot, reach,
            role_of_slot, slots, [],
        )

    forbidden_pairs: List[Dict[str, Any]] = []
    forbidden: Dict[Tuple[SlotKey, str], List[Tuple[SlotKey, str]]] = {}
    round_limit = len(slots) * 4 + 8

    for _round in range(round_limit):
        assign, counts, capacity_paths, blocked = _fill_every_slot(
            slot_order, choices, edges, current, forbidden, max_events, seed
        )
        if assign is None or counts is None:
            raise _capacity_refusal(blocked or {}, role_of_slot, edges, max_events, forbidden_pairs)

        coverage_paths, uncovered = _cover_every_source(
            ordered_sources, preferences, assign, counts, forbidden
        )
        if uncovered is not None:
            raise ExactPoolAssignmentError(
                f"exact pool assignment cannot cover {uncovered!r} without emptying another source",
                {
                    "failure_class": "role_capacity",
                    "reason": (
                        "no residual exchange reaches a source holding two or more events; the reachable "
                        "set occupies fewer slots than it has members"
                    ),
                    "uncovered_source": uncovered,
                    "forbidden_final_pairs": list(forbidden_pairs),
                },
            )

        trial = copy.deepcopy(original)
        trial_sections = list(trial.get("sections") or [])
        replacements: List[Dict[str, Any]] = []
        changed: Set[Tuple[int, int]] = set()
        singleton_relocations = 0
        coverage_slots = {
            (hop["bar_start"], hop["layer_index"])
            for path in coverage_paths
            for hop in path["hops"]
        }
        for slot_key in slot_order:
            target_source = assign[slot_key]
            donor_source = current[slot_key]
            if target_source == donor_source:
                continue
            section_index, layer_index = position_of[slot_key]
            layer = trial_sections[section_index]["layers"][layer_index]
            candidate, transform, _rank = edges[slot_key][target_source]
            slot_role = role_of_slot[slot_key]
            _rotation._apply_candidate(layer, candidate, slot_role, transform)
            changed.add((section_index, layer_index))
            if before.get(donor_source, 0) == 1:
                singleton_relocations += 1
            replacements.append({
                "reason": "missing_source" if slot_key in coverage_slots else "reuse_cap",
                "bar_start": slot_key[0],
                "layer_index": layer_index,
                "role": slot_role,
                "from_source": donor_source,
                "to_source": target_source,
                "to_atom": _rotation._atom_identity(candidate),
            })

        violations = _final_pair_violations(
            core, trial_sections, changed, items, render_bpm, target_key, params, seed
        )
        if violations:
            known = {tuple(sorted(pair.items())) for pair in forbidden_pairs}
            fresh = [pair for pair in _recorded_pairs(violations) if tuple(sorted(pair.items())) not in known]
            if not fresh:
                # Nothing new to forbid: either the violation has no counterpart a
                # pair constraint could describe, or the search cannot avoid a
                # co-occurrence it has already been told to avoid. Another round would
                # reproduce this assignment, so refuse rather than spin.
                unpairable = [
                    violation for violation in violations if int(violation["counterpart_layer_index"]) < 0
                ]
                raise ExactPoolAssignmentError(
                    "exact pool assignment cannot publish a section pairing that passes the compatibility law",
                    {
                        "failure_class": "section_pair_compatibility",
                        "reason": (
                            "a published layer is inadmissible against no identifiable counterpart"
                            if unpairable
                            else "the search cannot avoid a co-occurrence it has already been told to avoid"
                        ),
                        "violations": violations[:16],
                        "forbidden_final_pairs": list(forbidden_pairs),
                    },
                )
            forbidden_pairs.extend(fresh)
            forbidden = _pair_index(forbidden_pairs)
            continue

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

        replacements.sort(key=lambda record: (record["bar_start"], record["layer_index"]))
        ledger = trial.setdefault("taste_ledger", {})
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
            "authority": "exact_pool_assignment_solver",
            "method": "deterministic_augmenting_assignment_with_published_pair_validation",
            "fast_path": dict(fast_path or {"disposition": "not_offered", "detail": ""}),
            "mandatory_source_count": len(ordered_sources),
            "slot_count": len(slots),
            "matched_occurrence_relocation_count": sum(
                1 for source_id, slot_key in match_source.items() if assign.get(slot_key) != source_id
            ),
            "singleton_donor_relocation_count": singleton_relocations,
            "cap_relief_paths": capacity_paths,
            "coverage_repair_paths": coverage_paths,
            "final_pair_revisions": len(forbidden_pairs),
            "forbidden_final_pairs": list(forbidden_pairs),
            "identity_basis": "stable_source_key_plus_stable_atom_or_loop_id",
            "assignment_identity_fields": ["source_track_key|source_id", "atom_id|id|loop_id", "role", "bar_start", "layer_index", "seed"],
            "receipt_position_basis": "section_bar_start_plus_layer_index",
            "path_independence_witness": PATH_INDEPENDENCE_WITNESS,
            "input_order_independence_evidence": copy.deepcopy(INPUT_ORDER_INDEPENDENCE_EVIDENCE),
            "final_pair_validation_witness": FINAL_PAIR_VALIDATION_WITNESS,
            "coverage_cap_exchange_witness": COVERAGE_CAP_EXCHANGE_WITNESS,
            "completeness": copy.deepcopy(COMPLETENESS_STATEMENT),
        }
        return trial

    raise ExactPoolAssignmentError(
        "exact pool assignment could not settle a publishable section pairing",
        {
            "failure_class": "section_pair_compatibility",
            "reason": "published-pair validation forbade a new co-occurrence on every round within the deterministic bound",
            "round_limit": round_limit,
            "forbidden_final_pairs": list(forbidden_pairs),
        },
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _coverage_refusal(
    unmatched: Sequence[str],
    ordered_sources: Sequence[str],
    pool_by_source: Mapping[str, Sequence[Dict[str, Any]]],
    preferences: Mapping[str, Sequence[SlotKey]],
    match_slot: Mapping[SlotKey, str],
    reach: Mapping[str, Counter],
    role_of_slot: Mapping[SlotKey, str],
    slots: Sequence[Tuple[SlotKey, int, int, Dict[str, Any]]],
    forbidden_pairs: Sequence[Mapping[str, Any]],
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
        "forbidden_final_pairs": list(forbidden_pairs),
    })


def _capacity_refusal(
    blocked: Mapping[str, Any],
    role_of_slot: Mapping[SlotKey, str],
    edges: Mapping[SlotKey, Mapping[str, Any]],
    max_events: int,
    forbidden_pairs: Sequence[Mapping[str, Any]],
) -> ExactPoolAssignmentError:
    """Saturation witness: everything the slot can reach already holds the maximum."""
    slot_key: SlotKey = tuple(blocked.get("slot") or (0, 0))  # type: ignore[assignment]
    saturated: Mapping[str, int] = blocked.get("saturated") or {}
    return ExactPoolAssignmentError(
        (
            f"exact pool assignment cannot fill the {role_of_slot.get(slot_key, 'full')} slot at bar "
            f"{slot_key[0]} layer {slot_key[1]}: every reachable source already holds {max_events} event(s)"
        ),
        {
            "failure_class": "cap_constraint",
            "reason": (
                "the slot and every slot reachable from it by exchange are owned by sources that are all at "
                "the declared maximum, so no bounded assignment can occupy it"
            ),
            "declared_max_source_events": max_events,
            "unfilled_slot": [slot_key[0], slot_key[1]],
            "unfilled_slot_role": role_of_slot.get(slot_key, "full"),
            "compatible_source_count": len(edges.get(slot_key, {})),
            "saturated_reachable_sources": {source_id: int(count) for source_id, count in sorted(saturated.items())},
            "saturated_reachable_source_count": len(saturated),
            "forbidden_final_pairs": list(forbidden_pairs),
        },
    )


__all__ = [
    "COMPLETENESS_STATEMENT",
    "COVERAGE_CAP_EXCHANGE_WITNESS",
    "EXACT_POOL_ASSIGNMENT_VERSION",
    "FINAL_PAIR_VALIDATION_WITNESS",
    "INPUT_ORDER_INDEPENDENCE_EVIDENCE",
    "PATH_INDEPENDENCE_WITNESS",
    "PROVENANCE_WITNESSES",
    "ExactPoolAssignmentError",
    "accept_fast_path_proposal",
    "solve_exact_pool_assignment",
]
