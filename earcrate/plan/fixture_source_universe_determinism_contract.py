"""Canonicalize Stage 2D slot assignment with exact deterministic semantics.

The Stage 2D MILP owns source-universe cardinality and minimum partition
movement. Its slot variables are feasibility witnesses, not identity authority:
equal-cost source swaps can otherwise vary across HiGHS versions or platforms.
This closure replaces that incidental witness with the lexicographically
smallest feasible source vector over sorted stable slot identities.

Canonicalization uses exact integer lower-bound flow feasibility. It preserves
the selected partition, every observed slot, source coverage, and the existing
per-source event cap. Solver bounds remain non-evidentiary stops.
"""
from __future__ import annotations

from collections import Counter, deque
import copy
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_source_universe as _source
from earcrate.plan import fixture_slot_qualification_core as _core

CANONICAL_SLOT_ASSIGNMENT_VERSION = (
    "earcrate_source_universe_canonical_slot_assignment_v1"
)
Slot = Tuple[str, int, int]


class _CanonicalAssignmentBound(RuntimeError):
    pass


class _CanonicalAssignmentInvariant(RuntimeError):
    pass


class _FlowEdge:
    __slots__ = ("to", "rev", "cap")

    def __init__(self, to: int, rev: int, cap: int) -> None:
        self.to = int(to)
        self.rev = int(rev)
        self.cap = int(cap)


def _add_flow_edge(
    graph: List[List[_FlowEdge]],
    left: int,
    right: int,
    capacity: int,
) -> None:
    if capacity < 0:
        raise _CanonicalAssignmentInvariant("negative flow capacity")
    forward = _FlowEdge(right, len(graph[right]), capacity)
    reverse = _FlowEdge(left, len(graph[left]), 0)
    graph[left].append(forward)
    graph[right].append(reverse)


def _max_flow(
    graph: List[List[_FlowEdge]],
    source: int,
    sink: int,
    *,
    deadline: float,
) -> int:
    total = 0
    node_count = len(graph)
    while True:
        if time.monotonic() > deadline:
            raise _CanonicalAssignmentBound(
                "canonical slot assignment exceeded its time bound"
            )
        level = [-1] * node_count
        level[source] = 0
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for edge in graph[node]:
                if edge.cap > 0 and level[edge.to] < 0:
                    level[edge.to] = level[node] + 1
                    queue.append(edge.to)
        if level[sink] < 0:
            return total

        cursor = [0] * node_count

        def send(node: int, available: int) -> int:
            if node == sink:
                return available
            while cursor[node] < len(graph[node]):
                edge = graph[node][cursor[node]]
                if edge.cap > 0 and level[edge.to] == level[node] + 1:
                    pushed = send(edge.to, min(available, edge.cap))
                    if pushed:
                        edge.cap -= pushed
                        graph[edge.to][edge.rev].cap += pushed
                        return pushed
                cursor[node] += 1
            return 0

        while True:
            if time.monotonic() > deadline:
                raise _CanonicalAssignmentBound(
                    "canonical slot assignment exceeded its time bound"
                )
            pushed = send(source, 1 << 60)
            if not pushed:
                break
            total += pushed


def _remaining_assignment_feasible(
    remaining_slots: Sequence[Slot],
    compatible_by_slot: Mapping[Slot, Sequence[str]],
    selected_sources: Sequence[str],
    assigned_counts: Mapping[str, int],
    max_source_events: int,
    *,
    deadline: float,
) -> bool:
    """Check exact slot fill, source coverage, and cap feasibility."""
    if time.monotonic() > deadline:
        raise _CanonicalAssignmentBound(
            "canonical slot assignment exceeded its time bound"
        )

    slots = list(remaining_slots)
    sources = list(selected_sources)
    lower_by_source: Dict[str, int] = {}
    upper_by_source: Dict[str, int] = {}
    for source_id in sources:
        count = int(assigned_counts.get(source_id, 0))
        if count < 0 or count > max_source_events:
            return False
        lower_by_source[source_id] = 0 if count > 0 else 1
        upper_by_source[source_id] = max_source_events - count
        if upper_by_source[source_id] < lower_by_source[source_id]:
            return False

    if sum(lower_by_source.values()) > len(slots):
        return False
    if sum(upper_by_source.values()) < len(slots):
        return False

    source_index = {source_id: index for index, source_id in enumerate(sources)}
    source_node = 0
    first_slot_node = 1
    first_source_node = first_slot_node + len(slots)
    sink_node = first_source_node + len(sources)
    super_source = sink_node + 1
    super_sink = sink_node + 2
    graph: List[List[_FlowEdge]] = [
        [] for _ in range(super_sink + 1)
    ]
    balance = [0] * (sink_node + 1)

    def add_lower_bound_edge(
        left: int,
        right: int,
        lower: int,
        upper: int,
    ) -> None:
        if lower < 0 or upper < lower:
            raise _CanonicalAssignmentInvariant(
                "invalid lower-bound flow edge"
            )
        _add_flow_edge(graph, left, right, upper - lower)
        balance[left] -= lower
        balance[right] += lower

    for slot_pos, slot in enumerate(slots):
        slot_node = first_slot_node + slot_pos
        add_lower_bound_edge(source_node, slot_node, 1, 1)
        candidates = [
            source_id
            for source_id in sorted(set(compatible_by_slot.get(slot) or []))
            if source_id in source_index
            and upper_by_source[source_id] > 0
        ]
        if not candidates:
            return False
        for source_id in candidates:
            add_lower_bound_edge(
                slot_node,
                first_source_node + source_index[source_id],
                0,
                1,
            )

    for source_id in sources:
        add_lower_bound_edge(
            first_source_node + source_index[source_id],
            sink_node,
            lower_by_source[source_id],
            upper_by_source[source_id],
        )

    infinity = max(
        1,
        len(slots) + sum(upper_by_source.values()) + 1,
    )
    add_lower_bound_edge(sink_node, source_node, 0, infinity)

    required = 0
    for node, value in enumerate(balance):
        if value > 0:
            _add_flow_edge(graph, super_source, node, value)
            required += value
        elif value < 0:
            _add_flow_edge(graph, node, super_sink, -value)

    return (
        _max_flow(
            graph,
            super_source,
            super_sink,
            deadline=deadline,
        )
        == required
    )


def _canonical_slot_assignment(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    max_source_events: int,
    time_limit_s: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected_by_island: Dict[str, List[str]] = {}
    for raw_island in candidate.get("islands") or []:
        island = dict(raw_island)
        island_id = str(island.get("island_id") or "")
        if not island_id or island_id in selected_by_island:
            raise _CanonicalAssignmentInvariant(
                "selected candidate has malformed island identities"
            )
        selected_by_island[island_id] = sorted(
            {
                str(value)
                for value in island.get("source_include_ids") or []
            }
        )

    census_by_island = {
        str(row.get("island_id") or ""): row
        for row in census_campaign.get("islands") or []
        if isinstance(row, Mapping)
    }
    if set(census_by_island) != set(selected_by_island):
        raise _CanonicalAssignmentInvariant(
            "selected candidate and census island families disagree"
        )

    deadline = time.monotonic() + max(0.01, float(time_limit_s))
    assignment: List[Dict[str, Any]] = []
    feasibility_checks = 0

    for island_id in sorted(selected_by_island):
        selected_sources = selected_by_island[island_id]
        if not selected_sources:
            raise _CanonicalAssignmentInvariant(
                f"island {island_id!r} selected no source"
            )
        slots: List[Slot] = []
        compatible_by_slot: Dict[Slot, List[str]] = {}
        for raw_slot in census_by_island[island_id].get("slots") or []:
            key = list(raw_slot.get("slot_key") or [])
            if len(key) != 2:
                raise _CanonicalAssignmentInvariant(
                    f"island {island_id!r} has a malformed slot key"
                )
            slot = (island_id, int(key[0]), int(key[1]))
            if slot in compatible_by_slot:
                raise _CanonicalAssignmentInvariant(
                    f"duplicate canonical slot identity: {slot!r}"
                )
            candidates = sorted(
                set(
                    str(value)
                    for value in raw_slot.get("compatible_sources") or []
                ).intersection(selected_sources)
            )
            if not candidates:
                raise _CanonicalAssignmentInvariant(
                    f"selected partition leaves slot {slot!r} unreachable"
                )
            slots.append(slot)
            compatible_by_slot[slot] = candidates
        slots.sort()
        counts: Counter[str] = Counter()

        feasibility_checks += 1
        if not _remaining_assignment_feasible(
            slots,
            compatible_by_slot,
            selected_sources,
            counts,
            max_source_events,
            deadline=deadline,
        ):
            raise _CanonicalAssignmentInvariant(
                f"selected partition for island {island_id!r} is not assignable"
            )

        for slot_pos, slot in enumerate(slots):
            chosen: Optional[str] = None
            for source_id in compatible_by_slot[slot]:
                if counts[source_id] >= max_source_events:
                    continue
                counts[source_id] += 1
                feasibility_checks += 1
                if _remaining_assignment_feasible(
                    slots[slot_pos + 1 :],
                    compatible_by_slot,
                    selected_sources,
                    counts,
                    max_source_events,
                    deadline=deadline,
                ):
                    chosen = source_id
                    break
                counts[source_id] -= 1
            if chosen is None:
                raise _CanonicalAssignmentInvariant(
                    f"no canonical source can commit to slot {slot!r}"
                )
            assignment.append(
                {
                    "island_id": island_id,
                    "bar_start": slot[1],
                    "layer_index": slot[2],
                    "source_id": chosen,
                }
            )

        if set(source_id for source_id, count in counts.items() if count > 0) != set(
            selected_sources
        ):
            raise _CanonicalAssignmentInvariant(
                f"canonical assignment failed source coverage in {island_id!r}"
            )
        if any(count > max_source_events for count in counts.values()):
            raise _CanonicalAssignmentInvariant(
                f"canonical assignment exceeded the cap in {island_id!r}"
            )

    assignment.sort(
        key=lambda row: (
            str(row["island_id"]),
            int(row["bar_start"]),
            int(row["layer_index"]),
        )
    )
    receipt = {
        "version": CANONICAL_SLOT_ASSIGNMENT_VERSION,
        "method": (
            "lexicographically_smallest_source_vector_by_sorted_slot_"
            "with_exact_lower_bound_flow_feasibility"
        ),
        "slot_order": "island_id_then_bar_start_then_layer_index",
        "source_order": "stable_source_identity_lexical",
        "numeric_semantics": "exact_integer_flow_no_floating_tie_break",
        "slot_count": len(assignment),
        "selected_source_count": sum(
            len(values) for values in selected_by_island.values()
        ),
        "island_count": len(selected_by_island),
        "feasibility_check_count": feasibility_checks,
    }
    return assignment, receipt


def install_fixture_source_universe_determinism_contract() -> None:
    """Install canonical slot-assignment authority once."""
    if getattr(
        _source,
        "_fixture_source_universe_determinism_contract_installed",
        False,
    ):
        return

    original_select = _source.select_planable_source_universe

    def canonical_select(
        candidate: Mapping[str, Any],
        census_campaign: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        result = original_select(candidate, census_campaign, **kwargs)
        if not bool(result.get("complete")):
            return result
        selected = result.get("candidate")
        if not isinstance(selected, Mapping):
            return _source._failure(
                "canonical_slot_assignment_invariant",
                "complete source-universe selection omitted its candidate",
                solver=result.get("solver") or {},
                parent_fixture_identity=str(
                    result.get("parent_fixture_identity") or ""
                ),
                parent_source_count=int(result.get("parent_source_count") or 0),
            )

        body = copy.deepcopy(dict(selected))
        selection = body.get("fixture_source_universe_selection")
        if not isinstance(selection, Mapping):
            return _source._failure(
                "canonical_slot_assignment_invariant",
                "selected candidate omitted its source-universe ledger",
                solver=result.get("solver") or {},
                parent_fixture_identity=str(
                    result.get("parent_fixture_identity") or ""
                ),
                parent_source_count=int(result.get("parent_source_count") or 0),
            )
        selection = copy.deepcopy(dict(selection))
        max_events = int(selection.get("max_source_events") or 0)
        if max_events <= 0:
            return _source._failure(
                "canonical_slot_assignment_invariant",
                "selected candidate carries no positive event cap",
                solver=result.get("solver") or {},
                parent_fixture_identity=str(
                    result.get("parent_fixture_identity") or ""
                ),
                parent_source_count=int(result.get("parent_source_count") or 0),
            )

        try:
            assignment, canonical_receipt = _canonical_slot_assignment(
                body,
                census_campaign,
                max_source_events=max_events,
                time_limit_s=float(kwargs.get("time_limit_s", 30.0)),
            )
        except _CanonicalAssignmentBound as error:
            solver = copy.deepcopy(dict(result.get("solver") or {}))
            solver["slot_assignment_canonicalization"] = {
                "version": CANONICAL_SLOT_ASSIGNMENT_VERSION,
                "complete": False,
                "status": "bound",
                "reason": str(error),
            }
            return _source._failure(
                "canonical_slot_assignment_bound",
                str(error),
                solver=solver,
                parent_fixture_identity=str(
                    result.get("parent_fixture_identity") or ""
                ),
                parent_source_count=int(result.get("parent_source_count") or 0),
            )
        except _CanonicalAssignmentInvariant as error:
            solver = copy.deepcopy(dict(result.get("solver") or {}))
            solver["slot_assignment_canonicalization"] = {
                "version": CANONICAL_SLOT_ASSIGNMENT_VERSION,
                "complete": False,
                "status": "invariant_failure",
                "reason": str(error),
            }
            return _source._failure(
                "canonical_slot_assignment_invariant",
                str(error),
                solver=solver,
                parent_fixture_identity=str(
                    result.get("parent_fixture_identity") or ""
                ),
                parent_source_count=int(result.get("parent_source_count") or 0),
            )

        solver = copy.deepcopy(dict(selection.get("solver") or {}))
        solver["slot_assignment_canonicalization"] = canonical_receipt
        selection["solver"] = solver
        selection["slot_assignment_sha256"] = _core.semantic_sha256(
            assignment
        )
        selection["slot_assignment_canonicalization"] = canonical_receipt
        body["fixture_source_universe_selection"] = selection
        body.pop("fixture_id", None)
        body.pop("fixture_sha256", None)

        from earcrate.plan.fixture_diversity import fixture_projection

        projection = fixture_projection(body)
        new_identity = str(projection["fixture_identity"])
        body["fixture_id"] = f"season002-universe-{new_identity[:12]}"
        body["fixture_sha256"] = new_identity

        completed = copy.deepcopy(dict(result))
        completed["candidate"] = body
        completed["selected_fixture_identity"] = new_identity
        completed["slot_assignment"] = assignment
        completed["solver"] = solver
        return completed

    canonical_select.__name__ = original_select.__name__
    canonical_select.__doc__ = original_select.__doc__
    canonical_select.__wrapped__ = original_select
    _source.select_planable_source_universe = canonical_select

    public = sys.modules.get("earcrate.plan.fixture_slot_qualification")
    if public is not None:
        public.select_planable_source_universe = canonical_select

    _source._fixture_source_universe_determinism_contract_installed = True


__all__ = [
    "CANONICAL_SLOT_ASSIGNMENT_VERSION",
    "install_fixture_source_universe_determinism_contract",
]
