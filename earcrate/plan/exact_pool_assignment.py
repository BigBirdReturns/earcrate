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

Coverage and the cap are solved first by a three-phase construction, and the
decomposition is that construction's completeness argument:

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

Those three phases are a *fast constructor*, not the decision procedure. They place
one slot at a time and never revise a placement they have already made. That is
enough for coverage and the cap, and it keeps a revision minimal, but it is not
enough for the pair constraints below: honouring a forbidden co-occurrence can
require moving the layer already standing at the *other* end of it. So whenever the
fast constructor cannot finish, the authority escalates to
:func:`_search_complete_assignment` — a deterministic backtracking search over
``(source, atom)`` values that carries coverage, the cap and every learned pair
constraint in one search state. Only that search may declare a pool impossible, and
its refusal says plainly whether it exhausted the space or stopped at its node
budget.

The published pairing is then validated as a whole. Scores are ranked against the
unmodified snapshot — that keeps ranking independent of how much repair has already
happened — but admissibility is decided against the finished sections, so two layers
that are each admissible against their old counterparts and inadmissible together
are caught before publication rather than after. A violated pair becomes a forbidden
co-occurrence and the search runs again honouring it. Forbidding the pair rather than
withdrawing an edge matters: an edge withdrawal both discards a lawful placement and
lets the same pair reappear in a different section.

The constraint is recorded against the two *atoms* that were actually judged, at
their musical positions. Admissibility is a property of the atom pair, so a
source-keyed constraint would retire every other atom that source could have played
in that role — including one that is admissible against the very counterpart that
refused the first. For the same reason the compatibility graph keeps every
admissible atom per source and slot, ranked best first, instead of collapsing to one
before the pairing is known; the fast constructor still only ever offers the best,
while the complete search is free to reach past it.

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
import sys
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
    "escalated_path_witness": (
        "tests/test_exact_pool_rotation.py::"
        "test_the_complete_search_is_identical_under_equivalent_input_permutations"
    ),
    "compared_by_the_witness": [
        "every_layer_body_at_equal_musical_position",
        "exact_pool_rotation_ledger_including_every_replacement_record",
        "exact_pool_assignment_ledger",
        "complete_search_node_count_when_the_search_produced_the_arrangement",
    ],
    "observed_outcome": "identical_at_every_compared_field",
}

FINAL_PAIR_VALIDATION_WITNESS = (
    "tests/test_exact_pool_rotation.py::test_repair_validates_the_section_pair_it_publishes"
)

COVERAGE_CAP_EXCHANGE_WITNESS = (
    "tests/test_exact_pool_rotation.py::test_repair_moves_a_matched_occurrence_to_hold_the_cap"
)

COMPLETE_PAIR_SEARCH_WITNESS = (
    "tests/test_exact_pool_rotation.py::"
    "test_repair_moves_an_earlier_placement_to_honour_a_learned_pair"
)

ATOM_LEVEL_CONSTRAINT_WITNESS = (
    "tests/test_exact_pool_rotation.py::"
    "test_a_learned_pair_leaves_another_atom_of_the_same_source_available"
)

SUCCESSFUL_PATH_PRESERVATION_WITNESS = (
    "tests/test_exact_pool_rotation.py::"
    "test_a_historically_successful_proposal_is_published_unchanged"
)

PROVENANCE_WITNESSES = (
    PATH_INDEPENDENCE_WITNESS,
    INPUT_ORDER_INDEPENDENCE_EVIDENCE["witness"],
    FINAL_PAIR_VALIDATION_WITNESS,
    COVERAGE_CAP_EXCHANGE_WITNESS,
    COMPLETE_PAIR_SEARCH_WITNESS,
    ATOM_LEVEL_CONSTRAINT_WITNESS,
    SUCCESSFUL_PATH_PRESERVATION_WITNESS,
)

#: The search is exhaustive, so its cost is bounded by a node budget rather than by
#: the shape of the pool. The budget scales with the slot count and nothing else, so
#: it is identical under every equivalent input permutation. A feasible assignment is
#: normally reached in about one node per slot; the budget exists for the adverse case,
#: and reaching it is reported as a bound rather than as an impossibility.
SEARCH_NODE_BUDGET_PER_SLOT = 128
SEARCH_NODE_BUDGET_FLOOR = 4096
SEARCH_NODE_BUDGET_CEILING = 49152

#: The search descends one frame per slot, so an arrangement with more slots than the
#: interpreter has stack for is declined as a bound rather than allowed to raise.
SEARCH_DEPTH_HEADROOM = 256

#: Every refusal states, in one field, whether it is a claim about the pool or a
#: report about the search. ``impossibility_claimed`` is true only where the
#: deficiency beside it is a proof — a Hall witness, a counting argument, an
#: exhausted alternating exchange, or an exhausted assignment space. A
#: ``search_bound`` refusal sets it false and carries ``private_acceptance``: the
#: search ran out of nodes, stack, or rounds and therefore learned nothing about
#: this pool. It is neither a capacity diagnosis nor a deficiency witness, it may
#: never be reported as "no compatible assignment exists", and a run that is
#: deciding acceptance has to stop on it rather than count it as an honest refusal.
INDETERMINATE_REFUSAL_ACTION = "halt_run_this_is_not_a_deficiency_witness"

COMPLETENESS_STATEMENT = {
    "coverage_and_capacity": "complete_over_the_constructed_compatibility_graph",
    "argument": (
        "phase one refuses with a Hall witness unless a source-saturating matching exists; "
        "phase two fills the maximum number of slots under the cap by alternating exchange, "
        "so an unfillable slot is genuinely unfillable; phase three then covers every "
        "remaining source by residual exchange, and its failure would exhibit the very Hall "
        "violation phase one ruled out. No occurrence is pinned during either phase."
    ),
    "section_pair_constraints": "complete_over_the_learned_atom_pair_constraints",
    "section_pair_note": (
        "every published pair is validated against the finished sections, and a violated pair "
        "becomes a forbidden co-occurrence the search honours from then on. The constraint is "
        "exact twice over: it forbids the pair rather than either placement, and it names the "
        "two atoms that were actually judged, so another atom of the same source stays "
        "available in the same role slot. The three-phase construction is only a fast "
        "constructor and does not carry those constraints in its search state; once any "
        "constraint has been learned and that construction cannot finish, a deterministic "
        "backtracking search over (source, atom) values enumerates the whole space under "
        "coverage, the cap and every learned constraint at once. A refusal therefore reports "
        "an exhausted space, not a traversal that could not revise itself. Every constraint "
        "learned is listed in forbidden_final_pairs."
    ),
    "refusal_regimes": (
        "before anything is learned the three phases are already complete over coverage and "
        "the cap, so their failure is reported as the structural proof it is; after a "
        "constraint is learned only the exhaustive search may refuse. A pool whose total "
        "capacity under the cap is smaller than its slot count is settled earlier still, by "
        "counting"
    ),
    "refusal_evidence": (
        "every deficiency carries impossibility_claimed. It is true only where the evidence "
        "beside it is a proof — a Hall witness, a counting argument, an exhausted alternating "
        "exchange, or an exhausted assignment space, which search.space_exhausted marks. A "
        "search_bound refusal sets it false and carries private_acceptance: the search ran out "
        "of nodes, stack, or rounds, so it is neither a capacity diagnosis nor a deficiency "
        "witness, may never be read as 'no compatible assignment exists', and must stop a run "
        "that is deciding acceptance rather than count as an honest refusal"
    ),
    "constraint_identity": "stable_atom_or_loop_id_at_a_musical_position",
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
) -> Tuple[Dict[SlotKey, Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]], Dict[str, Counter]]:
    """Compatibility graph over the unmodified arrangement.

    Edge existence is role family, transform safety at the exact deck, and score
    admissibility. Scoring reads counterparts from a frozen snapshot so an edge's
    *rank* never depends on how many repairs have already been applied; whether the
    published pairing is admissible is decided later, against the finished sections,
    by :func:`_final_pair_violations`.

    Every admissible atom is kept, ranked best first, not just the best one. Final-pair
    admissibility is a property of the two atoms, so collapsing a source's atoms to one
    before the pairing is known would let a single refused atom retire alternatives that
    are admissible against the very counterpart that refused it. The fast constructor
    still offers only ``row[0]``; the complete search may reach past it.
    """
    all_items = [item for source_id in ordered_sources for item in pool_by_source[source_id]]
    by_atom, by_loop = _pool_maps(all_items)

    reach: Dict[str, Counter] = {source_id: Counter() for source_id in ordered_sources}
    transform_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    edges: Dict[SlotKey, Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]] = {}

    for slot_key, section_index, layer_index, layer in slots:
        slot_role = str(layer.get("role") or "full")
        section = frozen_sections[section_index]
        best_by_source: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], tuple]]] = {}
        for source_id in ordered_sources:
            admissible: List[Tuple[tuple, str, Dict[str, Any], Dict[str, Any]]] = []
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
                atom_id = str(_stable_atom_identity(candidate))
                rank = (
                    *score,
                    _rotation._stable_rank(seed, "atom", slot_key[0], slot_key[1], source_id, atom_id),
                )
                admissible.append((rank, atom_id, candidate, dict(transform)))
            if admissible:
                admissible.sort(key=lambda row: (row[0], row[1]), reverse=True)
                best_by_source[source_id] = [(row[2], row[3], row[0]) for row in admissible]
                reach[source_id]["slots"] += 1
        edges[slot_key] = best_by_source
    return edges, reach


def _edge_row(
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    slot_key: SlotKey,
    source_id: str,
) -> Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]:
    return edges.get(slot_key, {}).get(source_id) or ()


def _best_edge(
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    slot_key: SlotKey,
    source_id: str,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], tuple]]:
    row = _edge_row(edges, slot_key, source_id)
    return row[0] if row else None


def _edge_for_atom(
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    slot_key: SlotKey,
    source_id: str,
    atom_id: str,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], tuple]]:
    for edge in _edge_row(edges, slot_key, source_id):
        if str(_stable_atom_identity(edge[0])) == atom_id:
            return edge
    return None


def _pair_index(
    pairs: Sequence[Mapping[str, Any]]
) -> Dict[Tuple[SlotKey, str], List[Tuple[SlotKey, str]]]:
    """Forbidden co-occurrences, looked up from either end, keyed by atom.

    A validated violation says *these two atoms may not be published together, here*.
    It does not say either placement is bad, so the constraint is stored as the pair
    it is rather than withdrawn as an edge — withdrawing an edge would both lose a
    lawful placement and let the same pair reappear in another section. Nor is it
    stored against the two sources: the core judged two atoms, and another atom of the
    same source may well be admissible against the same counterpart, so a source-keyed
    constraint would forbid placements no one has measured.
    """
    index: Dict[Tuple[SlotKey, str], List[Tuple[SlotKey, str]]] = {}
    for pair in pairs:
        left = ((int(pair["bar_start"]), int(pair["layer_index"])), str(pair["atom"]))
        right = ((int(pair["bar_start"]), int(pair["counterpart_layer_index"])), str(pair["counterpart_atom"]))
        index.setdefault(left, []).append(right)
        index.setdefault(right, []).append(left)
    return index


def _pair_forbidden(
    slot_key: SlotKey,
    atom_id: str,
    atoms: Mapping[SlotKey, str],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> bool:
    for partner_slot, partner_atom in forbidden.get((slot_key, atom_id), ()):  # already validated as inadmissible
        if atoms.get(partner_slot) == partner_atom:
            return True
    return False


def _slot_preferences(
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    ordered_sources: Sequence[str],
    seed: int,
) -> Dict[str, List[SlotKey]]:
    """For each source, every slot it can take, best first."""
    preferences: Dict[str, List[SlotKey]] = {}
    for source_id in ordered_sources:
        rows = [(slot_key, row[source_id][0][2]) for slot_key, row in edges.items() if source_id in row]
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
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
) -> Dict[SlotKey, List[str]]:
    """For each slot, every source that can take it, best first."""
    choices: Dict[SlotKey, List[str]] = {}
    for slot_key, row in edges.items():
        choices[slot_key] = [
            source_id
            for source_id, _edge in sorted(row.items(), key=lambda entry: (entry[1][0][2], entry[0]), reverse=True)
        ]
    return choices


def _monotone_atom(
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    current: Mapping[SlotKey, str],
    current_atom: Mapping[SlotKey, str],
    slot_key: SlotKey,
    source_id: str,
) -> str:
    """The atom the fast constructor would publish for ``source_id`` at ``slot_key``.

    A slot whose source does not change is not rewritten at all, so its published atom
    is the one already standing there — not the graph's best. Any other slot takes the
    best-ranked atom. This is the whole atom vocabulary the fast constructor has; the
    complete search is what reaches the alternatives.
    """
    if current.get(slot_key) == source_id:
        return str(current_atom.get(slot_key) or "")
    best = _best_edge(edges, slot_key, source_id)
    return str(_stable_atom_identity(best[0])) if best else ""


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
    atoms: Mapping[SlotKey, str],
    counts: Mapping[str, int],
    choices: Mapping[SlotKey, Sequence[str]],
    atom_of: Any,
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

    Pair constraints are read against the assignment as it already stands, so an empty
    return is a capacity deficiency *given those placements* — never a proof. When a
    learned co-occurrence is what closed the search off, the answer may be to move the
    other end of it, and only :func:`_search_complete_assignment` can do that.
    """
    previous: Dict[str, Tuple[SlotKey, Optional[str]]] = {}
    queue: deque = deque()

    def offer(slot_key: SlotKey, holder: Optional[str]) -> Optional[str]:
        for source_id in choices.get(slot_key, ()):  # best first
            if source_id == holder or source_id in previous:
                continue
            if _pair_forbidden(slot_key, atom_of(slot_key, source_id), atoms, forbidden):
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
    current_atom: Mapping[SlotKey, str],
    atom_of: Any,
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
    max_events: int,
    seed: int,
) -> Tuple[
    Optional[Dict[SlotKey, str]],
    Optional[Dict[SlotKey, str]],
    Optional[Counter],
    List[Dict[str, Any]],
    Optional[Dict[str, Any]],
]:
    """Every slot gets a lawful source, bounded by the reuse cap alone.

    The arrangement in front of us is the seed, so a plan is revised only where it
    must be. A slot whose incumbent would break the cap is simply left unseeded and
    filled by exchange, which is why cap relief needs no separate pass.
    """
    assign: Dict[SlotKey, str] = {}
    atoms: Dict[SlotKey, str] = {}
    counts: Counter = Counter()
    for slot_key in slot_order:
        holder = current.get(slot_key) or ""
        if holder not in edges.get(slot_key, {}) or counts[holder] >= max_events:
            continue
        if _pair_forbidden(slot_key, str(current_atom.get(slot_key) or ""), atoms, forbidden):
            continue
        assign[slot_key] = holder
        atoms[slot_key] = str(current_atom.get(slot_key) or "")
        counts[holder] += 1

    paths: List[Dict[str, Any]] = []
    for slot_key in slot_order:
        if slot_key in assign:
            continue
        chain, saturated = _capacity_chain(
            slot_key, assign, atoms, counts, choices, atom_of, forbidden, max_events, seed
        )
        if chain is None:
            return None, None, None, paths, {"slot": slot_key, "saturated": saturated}
        hops: List[Dict[str, Any]] = []
        for hop_slot, receiver in chain:
            donor = assign.get(hop_slot, current.get(hop_slot) or "")
            if hop_slot in assign:
                counts[assign[hop_slot]] -= 1
            assign[hop_slot] = receiver
            atoms[hop_slot] = atom_of(hop_slot, receiver)
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
    return assign, atoms, counts, paths, None


# ---------------------------------------------------------------------------
# Phase 3 — cover every source
# ---------------------------------------------------------------------------


def _coverage_chain(
    source_id: str,
    preferences: Mapping[str, Sequence[SlotKey]],
    assign: Mapping[SlotKey, str],
    atoms: Mapping[SlotKey, str],
    counts: Mapping[str, int],
    atom_of: Any,
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
            if _pair_forbidden(slot_key, atom_of(slot_key, needy), atoms, forbidden):
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
    atoms: Dict[SlotKey, str],
    counts: Counter,
    atom_of: Any,
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    paths: List[Dict[str, Any]] = []
    for source_id in ordered_sources:
        if counts.get(source_id, 0) > 0:
            continue
        chain = _coverage_chain(source_id, preferences, assign, atoms, counts, atom_of, forbidden)
        if chain is None:
            return paths, source_id
        hops: List[Dict[str, Any]] = []
        for slot_key, receiver in chain:
            donor = assign[slot_key]
            counts[donor] -= 1
            assign[slot_key] = receiver
            atoms[slot_key] = atom_of(slot_key, receiver)
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
# The decision procedure — every constraint in one search state
# ---------------------------------------------------------------------------


#: A search value is what a slot may actually publish: a source, the atom it plays
#: there, and the graph edge that carries the transform. The incumbent value carries
#: ``None`` because keeping a slot rewrites nothing.
SearchValue = Tuple[str, str, Optional[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]


def _search_domains(
    slot_order: Sequence[SlotKey],
    edges: Mapping[SlotKey, Mapping[str, Sequence[Tuple[Dict[str, Any], Dict[str, Any], tuple]]]],
    current: Mapping[SlotKey, str],
    current_atom: Mapping[SlotKey, str],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
) -> Dict[SlotKey, List[SearchValue]]:
    """Everything each slot may lawfully publish, least-disruptive first.

    The incumbent pairing leads, so the search reaches for an unchanged slot before a
    rewritten one and a revision stays as small as the constraints allow. After it come
    the admissible ``(source, atom)`` values the graph holds, best rank first.

    A source's atoms are *not* all enumerated, and the reduction is exact rather than a
    sampling. At a given slot the constraints split a source's atoms into the ones some
    learned pair names there and the ones it does not. Every unnamed atom is
    indistinguishable to every law in the search — it satisfies the same compatibility
    edge, counts once against the same source's cap, and appears in no constraint — so
    any assignment using one is still an assignment using the best-ranked one. Each
    source therefore contributes its best atom, its best *unnamed* atom, every atom
    named at this slot, and the incumbent. A pool with a thousand loops per source
    searches no wider than one with three.
    """
    named_here: Dict[SlotKey, Set[str]] = {}
    for slot_key, atom_id in forbidden:
        named_here.setdefault(slot_key, set()).add(atom_id)

    domains: Dict[SlotKey, List[SearchValue]] = {}
    for slot_key in slot_order:
        row = edges.get(slot_key, {})
        holder = str(current.get(slot_key) or "")
        holder_atom = str(current_atom.get(slot_key) or "")
        named = named_here.get(slot_key, frozenset())
        head: List[SearchValue] = [(holder, holder_atom, None)] if holder in row else []
        seen: Set[Tuple[str, str]] = {(holder, holder_atom)} if head else set()
        tail: List[SearchValue] = []
        for source_id, candidates in row.items():
            atom_ids = [str(_stable_atom_identity(edge[0])) for edge in candidates]  # ranked best first
            wanted = {atom_id for atom_id in atom_ids if atom_id in named}
            if atom_ids:
                wanted.add(atom_ids[0])
                wanted.add(next((atom_id for atom_id in atom_ids if atom_id not in named), atom_ids[0]))
            for atom_id, edge in zip(atom_ids, candidates):
                if atom_id not in wanted or (source_id, atom_id) in seen:
                    continue
                seen.add((source_id, atom_id))
                tail.append((source_id, atom_id, edge))
        tail.sort(key=lambda value: (value[2][2], value[0], value[1]), reverse=True)
        domains[slot_key] = head + tail
    return domains


def _search_complete_assignment(
    slot_order: Sequence[SlotKey],
    domains: Mapping[SlotKey, Sequence[SearchValue]],
    ordered_sources: Sequence[str],
    forbidden: Mapping[Tuple[SlotKey, str], Sequence[Tuple[SlotKey, str]]],
    max_events: int,
) -> Dict[str, Any]:
    """Deterministic backtracking over ``(source, atom)`` values under every law at once.

    Coverage, the reuse cap and every learned co-occurrence live in the same search
    state, so honouring a constraint may move the placement standing at its other end —
    which is exactly what the fast constructor cannot do. Slot order is dynamic (fewest
    surviving values first, musical position to break the tie) and value order is fixed
    by :func:`_search_domains`, so the whole procedure is a pure function of the graph,
    the constraints and the seed already baked into the ranks.

    Feasibility is maintained rather than recomputed. Each value carries a block count,
    raised when its source reaches the cap or when a learned partner stands beside it
    and lowered on the way back out, so a node costs one pass over the unassigned slots
    instead of a pass over every value of every one of them. That is the difference
    between an exhaustive search being a real decision procedure at island scale and
    being a theoretical one.

    The two prunes are necessary conditions, not a full Hall test: an uncovered source
    with no reachable slot left, and more uncovered sources than unassigned slots.
    Completeness does not rest on them — they only decide how fast the space is
    exhausted, never which assignments are considered.

    A ``None`` assignment with ``space_exhausted`` set is a proof: no assignment over
    this graph satisfies the laws. Without it the search stopped at its node budget and
    the caller must say so rather than claim impossibility.
    """
    node_budget = min(
        SEARCH_NODE_BUDGET_CEILING,
        max(SEARCH_NODE_BUDGET_FLOOR, len(slot_order) * SEARCH_NODE_BUDGET_PER_SLOT),
    )
    receipt = {
        "method": "deterministic_backtracking_over_source_atom_values",
        "value_basis": "stable_source_key_plus_stable_atom_or_loop_id",
        "constraints_in_search_state": ["coverage", "reuse_cap", "learned_final_pairs"],
        "node_budget": node_budget,
    }
    if len(slot_order) > sys.getrecursionlimit() - SEARCH_DEPTH_HEADROOM:
        # One frame per slot. An arrangement deeper than the interpreter's stack is a
        # bound like any other, and saying so beats an interpreter error mid-render.
        return {
            **receipt,
            "assignment": None,
            "atoms": None,
            "counts": None,
            "nodes_explored": 0,
            "space_exhausted": False,
            "depth_limited": True,
        }

    values: Dict[SlotKey, List[SearchValue]] = {slot_key: list(domains[slot_key]) for slot_key in slot_order}
    index_by_source: Dict[SlotKey, Dict[str, List[int]]] = {}
    index_by_atom: Dict[SlotKey, Dict[str, List[int]]] = {}
    slots_of_source: Dict[str, List[SlotKey]] = {}
    for slot_key in slot_order:
        by_source: Dict[str, List[int]] = {}
        by_atom: Dict[str, List[int]] = {}
        for position, (source_id, atom_id, _edge) in enumerate(values[slot_key]):
            by_source.setdefault(source_id, []).append(position)
            by_atom.setdefault(atom_id, []).append(position)
        index_by_source[slot_key] = by_source
        index_by_atom[slot_key] = by_atom
        for source_id in by_source:
            slots_of_source.setdefault(source_id, []).append(slot_key)

    blocked: Dict[SlotKey, List[int]] = {slot_key: [0] * len(values[slot_key]) for slot_key in slot_order}
    alive: Dict[SlotKey, int] = {slot_key: len(values[slot_key]) for slot_key in slot_order}
    reach: Counter = Counter({source_id: len(slots) for source_id, slots in slots_of_source.items()})
    assign: Dict[SlotKey, str] = {}
    atoms: Dict[SlotKey, str] = {}
    counts: Counter = Counter()
    explored = 0
    budget_reached = False

    def shift(slot_key: SlotKey, position: int, blocking: bool) -> None:
        if blocking:
            if blocked[slot_key][position] == 0:
                alive[slot_key] -= 1
            blocked[slot_key][position] += 1
            return
        blocked[slot_key][position] -= 1
        if blocked[slot_key][position] == 0:
            alive[slot_key] += 1

    def hold(slot_key: SlotKey, position: int, taking: bool) -> None:
        source_id, atom_id, _edge = values[slot_key][position]
        if taking:
            assign[slot_key] = source_id
            atoms[slot_key] = atom_id
            counts[source_id] += 1
        for partner_slot, partner_atom in forbidden.get((slot_key, atom_id), ()):
            for other in index_by_atom.get(partner_slot, {}).get(partner_atom, ()):
                shift(partner_slot, other, taking)
        if counts[source_id] == max_events:
            for other_slot in slots_of_source[source_id]:
                for other in index_by_source[other_slot][source_id]:
                    shift(other_slot, other, taking)
        for other_source in index_by_source[slot_key]:
            reach[other_source] += -1 if taking else 1
        if not taking:
            counts[source_id] -= 1
            del assign[slot_key]
            del atoms[slot_key]

    def descend(remaining: List[SlotKey]) -> bool:
        nonlocal explored, budget_reached
        if not remaining:
            return all(counts[source_id] > 0 for source_id in ordered_sources)
        if explored >= node_budget:
            budget_reached = True
            return False
        uncovered = 0
        for source_id in ordered_sources:
            if counts[source_id] == 0:
                if reach[source_id] == 0:
                    return False
                uncovered += 1
        if uncovered > len(remaining):
            return False
        slot_key = min(remaining, key=lambda key: (alive[key], key))
        if alive[slot_key] == 0:
            return False
        rest = [key for key in remaining if key != slot_key]
        for position in range(len(values[slot_key])):
            if blocked[slot_key][position]:
                continue
            explored += 1
            hold(slot_key, position, True)
            if descend(rest):
                return True
            hold(slot_key, position, False)
            if budget_reached:
                return False
        return False

    found = descend(list(slot_order))
    return {
        **receipt,
        "assignment": dict(assign) if found else None,
        "atoms": dict(atoms) if found else None,
        "counts": Counter(counts) if found else None,
        "nodes_explored": explored,
        "space_exhausted": bool(not found and not budget_reached),
        "depth_limited": False,
    }


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

    The two atoms are part of the constraint, not decoration on it. The core judged
    *those* two loops in those two roles; another atom of either source is a placement
    nothing has measured yet, and forbidding it here would refuse music on evidence
    that was never gathered. The source names ride along because a receipt has to be
    readable, but :func:`_pair_index` keys on the atoms.
    """
    recorded: Dict[Tuple[int, int, str, int, str], Dict[str, Any]] = {}
    for violation in violations:
        counterpart_layer_index = int(violation["counterpart_layer_index"])
        if counterpart_layer_index < 0 or not violation["counterpart_atom"]:
            continue
        left = (int(violation["layer_index"]), str(violation["atom"]), str(violation["source"]))
        right = (counterpart_layer_index, str(violation["counterpart_atom"]), str(violation["counterpart_source"]))
        if right < left:
            left, right = right, left
        key = (int(violation["bar_start"]), left[0], left[1], right[0], right[1])
        recorded.setdefault(key, {
            "bar_start": int(violation["bar_start"]),
            "layer_index": left[0],
            "atom": left[1],
            "source": left[2],
            "counterpart_layer_index": right[0],
            "counterpart_atom": right[1],
            "counterpart_source": right[2],
            "forbidden_because": "inadmissible_against_each_other_in_the_published_section",
            "constraint_identity": "stable_atom_or_loop_id_at_a_musical_position",
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
    """Judge the depth-one proposal against the success predicate it has always had.

    Returns ``None`` when the proposal is accepted — the authority then publishes it
    byte for byte — or the reason it is not. Coverage and the cap are re-derived from
    the proposed layers rather than read back from the proposal's own ledger, so the
    proposal is verified rather than trusted, but the predicate itself is the one that
    was already in force: every allowlisted source used, no source past the cap.

    Final-pair validation deliberately does *not* run here. This repair is an
    adverse-path repair, and its preservation boundary is explicit: an arrangement the
    depth-one walk could already produce must come back with the same bytes. Adding a
    criterion the old path never had would reject plans that have been shipping, and
    the replacement the solver built instead would be a different arrangement — a
    behaviour change to successful renders, smuggled in under a refusal fix. Published
    pairs are therefore validated where they are newly constructed, in
    :func:`solve_exact_pool_assignment`. A latent pair defect in a legacy plan is a
    real question, and it belongs to its own issue with its own preservation decision.
    """
    max_events = int(params.get("exact_pool_max_source_events") or _rotation.DEFAULT_MAX_SOURCE_EVENTS)
    pool_sources = {
        _rotation._source_identity(dict(item))
        for item in pool
        if _rotation._source_identity(dict(item))
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
                "impossibility_claimed": True,
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
    current_atom = {row[0]: str(_rotation._atom_identity(row[3])) for row in slots}
    before = Counter(source_id for source_id in current.values() if source_id)
    frozen_sections = copy.deepcopy(list(original.get("sections") or []))

    edges, reach = _build_edges(
        core, frozen_sections, slots, ordered_sources, pool_by_source, render_bpm, target_key, params, seed
    )

    if len(slots) > len(ordered_sources) * max_events:
        # A counting proof, and the cheapest one available: every slot must hold a
        # source and no source may hold more than the cap, so the arrangement cannot
        # be filled at all. Settling this here keeps the assignment search off a
        # question arithmetic already answers.
        raise ExactPoolAssignmentError(
            (
                f"exact pool cannot fill {len(slots)} slot(s): {len(ordered_sources)} source(s) "
                f"capped at {max_events} event(s) each hold at most "
                f"{len(ordered_sources) * max_events}"
            ),
            {
                "failure_class": "cap_constraint",
                "impossibility_claimed": True,
                "reason": (
                    "the total capacity of the allowlist under the declared reuse cap is smaller "
                    "than the number of slots that must be occupied, so no assignment exists"
                ),
                "declared_max_source_events": max_events,
                "slot_count": len(slots),
                "mandatory_source_count": len(ordered_sources),
                "total_capacity": len(ordered_sources) * max_events,
                "proof": "counting",
            },
        )

    preferences = _slot_preferences(edges, ordered_sources, seed)
    choices = _slot_choices(edges)

    def atom_of(slot_key: SlotKey, source_id: str) -> str:
        return _monotone_atom(edges, current, current_atom, slot_key, source_id)

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
    escalated = False

    for _round in range(round_limit):
        assign: Optional[Dict[SlotKey, str]] = None
        atoms: Optional[Dict[SlotKey, str]] = None
        counts: Optional[Counter] = None
        capacity_paths: List[Dict[str, Any]] = []
        coverage_paths: List[Dict[str, Any]] = []
        blocked: Optional[Dict[str, Any]] = None
        construction = "monotone_exchange"
        search_receipt: Optional[Dict[str, Any]] = None

        if not escalated:
            assign, atoms, counts, capacity_paths, blocked = _fill_every_slot(
                slot_order, choices, edges, current, current_atom, atom_of, forbidden, max_events, seed
            )
            if assign is not None and atoms is not None and counts is not None:
                coverage_paths, uncovered = _cover_every_source(
                    ordered_sources, preferences, assign, atoms, counts, atom_of, forbidden
                )
                if uncovered is not None:
                    assign = atoms = counts = None
                    blocked = {"stage": "coverage_exchange", "uncovered_source": uncovered}
            else:
                blocked = dict(blocked or {}, stage="capacity_exchange")

        if (assign is None or atoms is None or counts is None) and not forbidden_pairs and not escalated:
            # Nothing has been learned yet, so the three phases are the complete
            # argument they claim to be: with no co-occurrence to honour they run
            # exactly as their proof describes, and a slot they cannot fill under the
            # cap cannot be filled at any visit order. A wider search would enumerate
            # the same space to reach the same answer, so this refusal is already a
            # proof and is reported as one.
            raise _construction_refusal(blocked, role_of_slot, edges, max_events)

        if assign is None or atoms is None or counts is None:
            # A learned constraint is in play, and the fast constructor reads those
            # against placements it has already made rather than carrying them in its
            # search state. Its failure is a statement about a traversal, not about the
            # pool, so nothing is refused until the complete search has enumerated the
            # space under every constraint at once. Domains are rebuilt each round
            # because which atoms a constraint names is exactly what decides how wide a
            # source's choice at a slot has to be.
            search = _search_complete_assignment(
                slot_order,
                _search_domains(slot_order, edges, current, current_atom, forbidden),
                ordered_sources,
                forbidden,
                max_events,
            )
            if search["assignment"] is None:
                raise _search_refusal(search, blocked, role_of_slot, edges, max_events, forbidden_pairs)
            assign = dict(search["assignment"])
            atoms = dict(search["atoms"])
            counts = Counter(search["counts"])
            construction = "complete_pair_aware_search"
            capacity_paths, coverage_paths = [], []
            search_receipt = {
                key: search[key]
                for key in ("method", "value_basis", "constraints_in_search_state", "nodes_explored", "node_budget")
            }
        escalated = False

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
            target_atom = atoms[slot_key]
            donor_source = current[slot_key]
            donor_atom = current_atom[slot_key]
            if target_source == donor_source and target_atom == donor_atom:
                continue
            section_index, layer_index = position_of[slot_key]
            layer = trial_sections[section_index]["layers"][layer_index]
            edge = _edge_for_atom(edges, slot_key, target_source, target_atom)
            if edge is None:  # unreachable: every search value comes from the graph
                raise ExactPoolAssignmentError(
                    f"exact pool assignment selected {target_atom!r} with no compatibility edge",
                    {
                        "failure_class": "internal_consistency",
                        "impossibility_claimed": True,
                        "reason": "an assignment value must name an atom the compatibility graph admits at that slot",
                        "slot": [slot_key[0], slot_key[1]],
                        "source": target_source,
                        "atom": target_atom,
                    },
                )
            candidate, transform, _rank = edge
            slot_role = role_of_slot[slot_key]
            _rotation._apply_candidate(layer, candidate, slot_role, transform)
            changed.add((section_index, layer_index))
            if target_source != donor_source and before.get(donor_source, 0) == 1:
                singleton_relocations += 1
            if construction != "monotone_exchange":
                reason = "complete_assignment"
            elif slot_key in coverage_slots:
                reason = "missing_source"
            else:
                reason = "reuse_cap"
            replacements.append({
                "reason": reason,
                "bar_start": slot_key[0],
                "layer_index": layer_index,
                "role": slot_role,
                "from_source": donor_source,
                "from_atom": donor_atom,
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
                if construction == "monotone_exchange":
                    # The fast constructor walked back into a co-occurrence it was
                    # already told to avoid — its chains only read the constraints
                    # against placements already made. Nothing new has been learned, so
                    # hand the same constraint set to the search that carries it.
                    escalated = True
                    continue
                # The complete search honoured every learned constraint and the
                # finished sections still refuse. The only violations it can still
                # produce are ones no pair constraint describes.
                unpairable = [
                    violation for violation in violations if int(violation["counterpart_layer_index"]) < 0
                ]
                raise ExactPoolAssignmentError(
                    "exact pool assignment cannot publish a section pairing that passes the compatibility law",
                    {
                        "failure_class": "section_pair_compatibility",
                        "impossibility_claimed": True,
                        "reason": (
                            "a published layer is inadmissible against no identifiable counterpart, so no "
                            "co-occurrence constraint can describe it and no reassignment can avoid it"
                            if unpairable
                            else "a complete search satisfied every learned constraint and the published "
                                 "sections still refuse the pairing it produced"
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
                {
                    "failure_class": "role_capacity",
                    "impossibility_claimed": True,
                    "reason": "post-assignment coverage check failed",
                    "unmatched_sources": missing_after,
                },
            )
        if counts and max(counts.values()) > max_events:
            offender = max(sorted(counts), key=lambda source_id: counts[source_id])
            raise ExactPoolAssignmentError(
                f"exact pool assignment left {offender!r} above cap",
                {
                    "failure_class": "cap_constraint",
                    "impossibility_claimed": True,
                    "reason": "post-assignment cap check failed",
                    "declared_max_source_events": max_events,
                },
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
            "construction": construction,
            "complete_search": copy.deepcopy(search_receipt) if search_receipt else None,
            "fast_path": dict(fast_path or {"disposition": "not_offered", "detail": ""}),
            "fast_path_acceptance_predicate": "every_mandatory_source_used_and_no_source_past_the_reuse_cap",
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
            "learned_pair_identity_basis": "stable_atom_or_loop_id_at_a_musical_position",
            "identity_basis": "stable_source_key_plus_stable_atom_or_loop_id",
            "assignment_identity_fields": ["source_track_key|source_id", "atom_id|id|loop_id", "role", "bar_start", "layer_index", "seed"],
            "receipt_position_basis": "section_bar_start_plus_layer_index",
            "path_independence_witness": PATH_INDEPENDENCE_WITNESS,
            "input_order_independence_evidence": copy.deepcopy(INPUT_ORDER_INDEPENDENCE_EVIDENCE),
            "final_pair_validation_witness": FINAL_PAIR_VALIDATION_WITNESS,
            "coverage_cap_exchange_witness": COVERAGE_CAP_EXCHANGE_WITNESS,
            "complete_pair_search_witness": COMPLETE_PAIR_SEARCH_WITNESS,
            "atom_level_constraint_witness": ATOM_LEVEL_CONSTRAINT_WITNESS,
            "successful_path_preservation_witness": SUCCESSFUL_PATH_PRESERVATION_WITNESS,
            "completeness": copy.deepcopy(COMPLETENESS_STATEMENT),
        }
        return trial

    raise ExactPoolAssignmentError(
        "exact pool assignment could not settle a publishable section pairing",
        {
            "failure_class": "search_bound",
            "impossibility_claimed": False,
            "private_acceptance": INDETERMINATE_REFUSAL_ACTION,
            "reason": (
                "published-pair validation learned a new co-occurrence on every round within the "
                "deterministic round bound; this is a bound on the learning loop, not a proof that "
                "no publishable pairing exists"
            ),
            "round_limit": round_limit,
            "learned_pair_constraint_count": len(forbidden_pairs),
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
        "impossibility_claimed": True,
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


def _fast_constructor_block(
    blocked: Optional[Mapping[str, Any]],
    role_of_slot: Mapping[SlotKey, str],
    edges: Mapping[SlotKey, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Where the fast constructor stopped — context for the refusal, never its ground.

    Kept because it is the most legible description of the shape of the problem: the
    slot it could not fill and the saturated sources it reached trying. It proves
    nothing on its own, which is why the complete search runs before any of this is
    reported.
    """
    if not blocked:
        return {"stage": "not_run"}
    detail: Dict[str, Any] = {"stage": str(blocked.get("stage") or "unknown")}
    if blocked.get("uncovered_source"):
        detail["uncovered_source"] = str(blocked["uncovered_source"])
    if blocked.get("slot"):
        slot_key: SlotKey = tuple(blocked["slot"])  # type: ignore[assignment]
        saturated: Mapping[str, int] = blocked.get("saturated") or {}
        detail.update({
            "unfilled_slot": [slot_key[0], slot_key[1]],
            "unfilled_slot_role": role_of_slot.get(slot_key, "full"),
            "compatible_source_count": len(edges.get(slot_key, {})),
            "saturated_reachable_sources": {source_id: int(count) for source_id, count in sorted(saturated.items())},
            "saturated_reachable_source_count": len(saturated),
        })
    return detail


def _construction_refusal(
    blocked: Optional[Mapping[str, Any]],
    role_of_slot: Mapping[SlotKey, str],
    edges: Mapping[SlotKey, Mapping[str, Any]],
    max_events: int,
) -> ExactPoolAssignmentError:
    """The three-phase construction failed with nothing learned, which is a proof.

    Its completeness argument covers exactly coverage and the cap, and with no
    co-occurrence constraint to honour that is the whole problem. Phase two fills the
    maximum number of slots by alternating exchange, so an unfilled slot is unfillable;
    phase three's failure would exhibit the Hall violation phase one already ruled out.
    Both witnesses are structural, so they are reported as they stand.
    """
    detail = _fast_constructor_block(blocked, role_of_slot, edges)
    if detail.get("uncovered_source"):
        return ExactPoolAssignmentError(
            f"exact pool assignment cannot cover {detail['uncovered_source']!r} without emptying another source",
            {
                "failure_class": "role_capacity",
                "impossibility_claimed": True,
                "reason": (
                    "no residual exchange reaches a source holding two or more events; the reachable "
                    "set occupies fewer slots than it has members"
                ),
                "uncovered_source": detail["uncovered_source"],
                "proof": "residual_exchange_exhausted_under_no_learned_pair_constraint",
                "fast_constructor_block": detail,
                "forbidden_final_pairs": [],
            },
        )
    slot_key: SlotKey = tuple(detail.get("unfilled_slot") or (0, 0))  # type: ignore[assignment]
    return ExactPoolAssignmentError(
        (
            f"exact pool assignment cannot fill the {detail.get('unfilled_slot_role', 'full')} slot at bar "
            f"{slot_key[0]} layer {slot_key[1]}: every reachable source already holds {max_events} event(s)"
        ),
        {
            "failure_class": "cap_constraint",
            "impossibility_claimed": True,
            "reason": (
                "the slot and every slot reachable from it by exchange are owned by sources that are all at "
                "the declared maximum, so no bounded assignment can occupy it"
            ),
            "declared_max_source_events": max_events,
            "unfilled_slot": detail.get("unfilled_slot", [slot_key[0], slot_key[1]]),
            "unfilled_slot_role": detail.get("unfilled_slot_role", "full"),
            "compatible_source_count": detail.get("compatible_source_count", 0),
            "saturated_reachable_sources": detail.get("saturated_reachable_sources", {}),
            "saturated_reachable_source_count": detail.get("saturated_reachable_source_count", 0),
            "proof": "alternating_exchange_exhausted_under_no_learned_pair_constraint",
            "forbidden_final_pairs": [],
        },
    )


def _search_refusal(
    search: Mapping[str, Any],
    blocked: Optional[Mapping[str, Any]],
    role_of_slot: Mapping[SlotKey, str],
    edges: Mapping[SlotKey, Mapping[str, Any]],
    max_events: int,
    forbidden_pairs: Sequence[Mapping[str, Any]],
) -> ExactPoolAssignmentError:
    """The only refusal that may follow a feasible coverage matching.

    Two different statements share this path and they are not interchangeable. An
    exhausted space is a proof: every assignment over the compatibility graph was
    considered and none satisfies the laws. A budget stop is a bound: the search ran
    out of nodes and knows nothing about the assignments it never reached. The refusal
    says which one happened, in its class, its message and ``search.space_exhausted``,
    so nobody downstream can read a bound as an impossibility.
    """
    receipt = {
        "method": search.get("method"),
        "value_basis": search.get("value_basis"),
        "constraints_in_search_state": list(search.get("constraints_in_search_state") or []),
        "nodes_explored": int(search.get("nodes_explored") or 0),
        "node_budget": int(search.get("node_budget") or 0),
        "space_exhausted": bool(search.get("space_exhausted")),
        "depth_limited": bool(search.get("depth_limited")),
    }
    deficiency: Dict[str, Any] = {
        "declared_max_source_events": max_events,
        "search": receipt,
        "fast_constructor_block": _fast_constructor_block(blocked, role_of_slot, edges),
        "learned_pair_constraint_count": len(forbidden_pairs),
        "forbidden_final_pairs": list(forbidden_pairs),
    }
    if not receipt["space_exhausted"]:
        deficiency.update({
            "failure_class": "search_bound",
            "impossibility_claimed": False,
            "private_acceptance": INDETERMINATE_REFUSAL_ACTION,
            "reason": (
                "the arrangement has more slots than the assignment search has stack depth for, so "
                "it was never run and nothing about this pool has been decided"
                if receipt["depth_limited"] else
                "the assignment search reached its deterministic node budget before enumerating the "
                "whole space, so this refusal is a bound on the search and not a proof that the pool "
                "is impossible"
            ),
        })
        return ExactPoolAssignmentError(
            (
                f"exact pool assignment stopped at its deterministic search bound after "
                f"{receipt['nodes_explored']} node(s); no impossibility is claimed"
            ),
            deficiency,
        )
    deficiency.update({
        "failure_class": "section_pair_compatibility" if forbidden_pairs else "cap_constraint",
        "impossibility_claimed": True,
        "reason": (
            "a deterministic backtracking search enumerated every (source, atom) assignment the "
            "compatibility graph admits and found none that covers every mandatory source, holds the "
            "reuse cap, and avoids every co-occurrence final-pair validation has refused"
        ),
    })
    return ExactPoolAssignmentError(
        (
            f"exact pool assignment is impossible over the existing slots: {receipt['nodes_explored']} "
            f"node(s) exhausted the assignment space under a cap of {max_events} event(s) and "
            f"{len(forbidden_pairs)} learned pair constraint(s)"
        ),
        deficiency,
    )


__all__ = [
    "ATOM_LEVEL_CONSTRAINT_WITNESS",
    "COMPLETENESS_STATEMENT",
    "COMPLETE_PAIR_SEARCH_WITNESS",
    "COVERAGE_CAP_EXCHANGE_WITNESS",
    "EXACT_POOL_ASSIGNMENT_VERSION",
    "FINAL_PAIR_VALIDATION_WITNESS",
    "INPUT_ORDER_INDEPENDENCE_EVIDENCE",
    "PATH_INDEPENDENCE_WITNESS",
    "PROVENANCE_WITNESSES",
    "SUCCESSFUL_PATH_PRESERVATION_WITNESS",
    "ExactPoolAssignmentError",
    "accept_fast_path_proposal",
    "solve_exact_pool_assignment",
]
