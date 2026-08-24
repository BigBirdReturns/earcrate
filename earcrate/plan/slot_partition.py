"""Deterministic source repartitioning against an observed island slot census."""
from __future__ import annotations

from collections import Counter, deque
import copy
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence

from earcrate.plan.slot_census import FixtureSlotQualificationError, VERSION, role_family

INDETERMINATE_ACTION = "halt_candidate_campaign_this_is_not_an_impossibility_proof"
DEFAULT_MAX_SOURCE_EVENTS = 12
DEFAULT_MAX_ANCHOR_ROUNDS = 128


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _rank(seed: Any, *parts: Any) -> int:
    body = "|".join([str(seed), *[str(part) for part in parts]])
    return int(hashlib.sha256(body.encode("utf-8")).hexdigest(), 16)


class _Edge:
    __slots__ = ("to", "rev", "cap", "original")

    def __init__(self, to: int, rev: int, cap: int):
        self.to = to
        self.rev = rev
        self.cap = cap
        self.original = cap


class _Flow:
    def __init__(self, size: int):
        self.g: list[list[_Edge]] = [[] for _ in range(size)]

    def add(self, left: int, right: int, cap: int) -> None:
        index = len(self.g[left])
        self.g[left].append(_Edge(right, len(self.g[right]), cap))
        self.g[right].append(_Edge(left, index, 0))

    def run(self, source: int, sink: int) -> int:
        total = 0
        size = len(self.g)
        while True:
            level = [-1] * size
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for edge in self.g[node]:
                    if edge.cap and level[edge.to] < 0:
                        level[edge.to] = level[node] + 1
                        queue.append(edge.to)
            if level[sink] < 0:
                return total
            work = [0] * size

            def send(node: int, amount: int) -> int:
                if node == sink:
                    return amount
                while work[node] < len(self.g[node]):
                    edge = self.g[node][work[node]]
                    if edge.cap and level[edge.to] == level[node] + 1:
                        pushed = send(edge.to, min(amount, edge.cap))
                        if pushed:
                            edge.cap -= pushed
                            self.g[edge.to][edge.rev].cap += pushed
                            return pushed
                    work[node] += 1
                return 0

            while True:
                pushed = send(source, 10**9)
                if not pushed:
                    break
                total += pushed

    def reached(self, source: int) -> set[int]:
        seen = {source}
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for edge in self.g[node]:
                if edge.cap and edge.to not in seen:
                    seen.add(edge.to)
                    queue.append(edge.to)
        return seen


def _matrix(matrix: Mapping[str, Any]):
    from earcrate.plan.fixture_derivation import normalize_matrix

    normalized = normalize_matrix(matrix)
    return (
        {str(row["deck_id"]): row for row in normalized["decks"]},
        str(normalized["matrix_semantic_sha256"]),
    )


def _candidate(candidate: Mapping[str, Any]):
    islands: list[Dict[str, Any]] = []
    universe: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(candidate.get("islands") or []):
        row = copy.deepcopy(dict(raw))
        island = str(row.get("island_id") or "")
        deck = str(row.get("deck_id") or "")
        if not island or not deck:
            raise FixtureSlotQualificationError(
                f"candidate island {index} needs island_id and deck_id"
            )
        sources = sorted({str(value) for value in row.get("source_include_ids") or []})
        if not sources:
            raise FixtureSlotQualificationError(
                f"candidate island {island} has no sources"
            )
        overlap = seen.intersection(sources)
        if overlap:
            raise FixtureSlotQualificationError(
                f"source appears in multiple islands: {sorted(overlap)[0]}"
            )
        seen.update(sources)
        universe.extend(sources)
        row["source_include_ids"] = sources
        row["qualification_source_count"] = len(sources)
        islands.append(row)
    if not islands:
        raise FixtureSlotQualificationError("candidate has no islands")
    return islands, sorted(universe)


def _verified_digest(value: Mapping[str, Any], field: str, label: str) -> str:
    body = copy.deepcopy(dict(value))
    provided = str(body.pop(field, "") or "")
    if not provided:
        raise FixtureSlotQualificationError(f"{label} has no {field}")
    observed = _sha(body)
    if provided != observed:
        raise FixtureSlotQualificationError(f"{label} digest mismatch")
    return provided


def _censuses(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
    islands: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, Dict[str, Any]], str]:
    family_sha = _verified_digest(
        receipt, "slot_census_family_sha256", "slot census family"
    )
    candidate_fixture = str(candidate.get("fixture_sha256") or "")
    bound_fixture = str(receipt.get("candidate_fixture_sha256") or "")
    if not bound_fixture or bound_fixture != candidate_fixture:
        raise FixtureSlotQualificationError(
            "slot census is not bound to this candidate fixture"
        )
    candidate_pool = str(candidate.get("source_pool_sha256") or "")
    receipt_pool = str(receipt.get("source_pool_sha256") or "")
    if not receipt_pool or receipt_pool != candidate_pool:
        raise FixtureSlotQualificationError(
            "slot census source-pool identity mismatch"
        )
    rows = list(receipt.get("islands") or [])
    if not rows and receipt.get("slot_census"):
        rows = [receipt["slot_census"]]
    candidate_by_island = {
        str(row["island_id"]): row for row in islands
    }
    out: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = dict(raw)
        island = str(row.get("island_id") or "")
        if not island or island in out or island not in candidate_by_island:
            raise FixtureSlotQualificationError(
                f"invalid slot census island at index {index}"
            )
        _verified_digest(row, "slot_census_sha256", f"slot census {island}")
        candidate_row = candidate_by_island[island]
        if str(row.get("candidate_fixture_sha256") or "") != candidate_fixture:
            raise FixtureSlotQualificationError(
                f"slot census {island} fixture binding mismatch"
            )
        if str(row.get("source_pool_sha256") or "") != candidate_pool:
            raise FixtureSlotQualificationError(
                f"slot census {island} source-pool binding mismatch"
            )
        declared_deck = str(candidate_row.get("deck_id") or "")
        census_deck = str(row.get("deck_id") or "")
        if census_deck and census_deck != declared_deck:
            raise FixtureSlotQualificationError(
                f"slot census {island} deck binding mismatch"
            )
        if abs(
            float(row.get("exact_target_bpm") or 0.0)
            - float(candidate_row.get("target_bpm") or 0.0)
        ) > 1e-9:
            raise FixtureSlotQualificationError(
                f"slot census {island} BPM binding mismatch"
            )
        if int(row.get("exact_target_key") or 0) % 12 != int(
            candidate_row.get("target_key") or 0
        ) % 12:
            raise FixtureSlotQualificationError(
                f"slot census {island} key binding mismatch"
            )
        if abs(
            float(row.get("allocated_duration_s") or 0.0)
            - float(candidate_row.get("allocated_duration_s") or 0.0)
        ) > 1e-9:
            raise FixtureSlotQualificationError(
                f"slot census {island} duration binding mismatch"
            )
        slots = [dict(slot) for slot in row.get("slots") or []]
        seen_slots: set[str] = set()
        for slot in slots:
            key = str(slot.get("slot_key") or "")
            if not key or key in seen_slots:
                raise FixtureSlotQualificationError(
                    f"invalid or duplicate slot in island {island}"
                )
            seen_slots.add(key)
            slot["role_family"] = role_family(
                str(slot.get("role_family") or slot.get("role") or "full")
            )
        row["slots"] = sorted(
            slots,
            key=lambda slot: (
                int(slot.get("bar_start") or 0),
                int(slot.get("layer_index") or 0),
                str(slot["slot_key"]),
            ),
        )
        out[island] = row
    return out, family_sha


def _anchor_flow(
    sources: Sequence[str],
    islands: Sequence[Mapping[str, Any]],
    decks: Mapping[str, Mapping[str, Any]],
    censuses: Mapping[str, Mapping[str, Any]],
    seed: Any,
    round_index: int,
):
    domains: Dict[str, list[tuple[str, str]]] = {}
    for source in sources:
        values: list[tuple[str, str]] = []
        for island in islands:
            island_id = str(island["island_id"])
            deck = str(island["deck_id"])
            roles = {
                role_family(role)
                for role in (decks[deck]["sources"].get(source) or set())
            }
            for slot in censuses[island_id]["slots"]:
                if slot["role_family"] in roles:
                    values.append((island_id, str(slot["slot_key"])))
        domains[source] = sorted(
            values,
            key=lambda value: (
                _rank(seed, round_index, source, *value),
                value,
            ),
        )
    order = sorted(
        sources,
        key=lambda source: (
            len(domains[source]),
            _rank(seed, round_index, source),
            source,
        ),
    )
    source_nodes: Dict[str, int] = {}
    slot_nodes: Dict[tuple[str, str], int] = {}
    island_nodes: Dict[str, int] = {}
    cursor = 1
    for source in order:
        source_nodes[source] = cursor
        cursor += 1
    for island in islands:
        island_id = str(island["island_id"])
        for slot in censuses[island_id]["slots"]:
            slot_nodes[(island_id, str(slot["slot_key"]))] = cursor
            cursor += 1
    for island in islands:
        island_nodes[str(island["island_id"])] = cursor
        cursor += 1
    sink = cursor
    root = 0
    flow = _Flow(sink + 1)
    for source in order:
        flow.add(root, source_nodes[source], 1)
        for value in domains[source]:
            flow.add(source_nodes[source], slot_nodes[value], 1)
    for island in islands:
        island_id = str(island["island_id"])
        for slot in censuses[island_id]["slots"]:
            flow.add(
                slot_nodes[(island_id, str(slot["slot_key"]))],
                island_nodes[island_id],
                1,
            )
        flow.add(
            island_nodes[island_id],
            sink,
            int(island["qualification_source_count"]),
        )
    return flow, root, sink, source_nodes, slot_nodes, island_nodes


def _anchors(
    flow: _Flow,
    sources: Sequence[str],
    source_nodes: Mapping[str, int],
    slot_nodes: Mapping[tuple[str, str], int],
) -> Dict[str, tuple[str, str]]:
    reverse = {node: key for key, node in slot_nodes.items()}
    out: Dict[str, tuple[str, str]] = {}
    for source in sources:
        for edge in flow.g[source_nodes[source]]:
            if edge.original == 1 and edge.cap == 0 and edge.to in reverse:
                out[source] = reverse[edge.to]
                break
    return out


def _fill(
    assignments: Mapping[str, str],
    anchors: Mapping[str, tuple[str, str]],
    islands: Sequence[Mapping[str, Any]],
    decks: Mapping[str, Mapping[str, Any]],
    censuses: Mapping[str, Mapping[str, Any]],
    cap: int,
    seed: Any,
):
    reports: Dict[str, Any] = {}
    for island in islands:
        island_id = str(island["island_id"])
        deck = str(island["deck_id"])
        sources = sorted(
            source for source, assigned in assignments.items() if assigned == island_id
        )
        anchor_slots = {
            slot
            for _source, (anchor_island, slot) in anchors.items()
            if anchor_island == island_id
        }
        remaining = [
            slot
            for slot in censuses[island_id]["slots"]
            if str(slot["slot_key"]) not in anchor_slots
        ]
        if not remaining:
            reports[island_id] = {
                "remaining_slot_count": 0,
                "filled_slot_count": 0,
                "source_count": len(sources),
            }
            continue
        source_nodes = {source: 1 + index for index, source in enumerate(sources)}
        offset = 1 + len(sources)
        slot_nodes = {
            str(slot["slot_key"]): offset + index
            for index, slot in enumerate(remaining)
        }
        sink = offset + len(remaining)
        root = 0
        flow = _Flow(sink + 1)
        for source in sources:
            flow.add(root, source_nodes[source], cap - 1)
            roles = {
                role_family(role)
                for role in (decks[deck]["sources"].get(source) or set())
            }
            for slot in sorted(
                remaining,
                key=lambda slot: (
                    _rank(seed, island_id, source, slot["slot_key"]),
                    slot["slot_key"],
                ),
            ):
                if slot["role_family"] in roles:
                    flow.add(
                        source_nodes[source],
                        slot_nodes[str(slot["slot_key"])],
                        1,
                    )
        for slot in remaining:
            flow.add(slot_nodes[str(slot["slot_key"])], sink, 1)
        filled = flow.run(root, sink)
        reports[island_id] = {
            "remaining_slot_count": len(remaining),
            "filled_slot_count": filled,
            "source_count": len(sources),
        }
        if filled != len(remaining):
            return False, {
                "failure_class": "remaining_slot_capacity",
                "failure_island_id": island_id,
                "islands": reports,
            }
    return True, {"islands": reports}


def qualify_fixture_candidate(
    matrix: Mapping[str, Any],
    candidate: Mapping[str, Any],
    slot_census_receipt: Mapping[str, Any],
    *,
    max_source_events: int = DEFAULT_MAX_SOURCE_EVENTS,
    max_anchor_rounds: int = DEFAULT_MAX_ANCHOR_ROUNDS,
) -> Dict[str, Any]:
    """Preserve the fixture program while choosing a slot-compatible partition."""
    from earcrate.plan.fixture_diversity import fixture_projection

    cap = int(max_source_events)
    rounds = int(max_anchor_rounds)
    if cap <= 0 or rounds < 0:
        raise FixtureSlotQualificationError("invalid qualification bound")
    projection = fixture_projection(candidate)
    parent = str(candidate.get("fixture_sha256") or projection["fixture_identity"])
    if parent != projection["fixture_identity"]:
        raise FixtureSlotQualificationError(
            "candidate fixture_sha256 does not match projection"
        )
    islands, sources = _candidate(candidate)
    decks, matrix_sha = _matrix(matrix)
    censuses, census_sha = _censuses(slot_census_receipt, candidate, islands)
    expected = {str(row["island_id"]) for row in islands}
    if set(censuses) != expected:
        raise FixtureSlotQualificationError("slot census island set mismatch")
    for row in islands:
        if str(row["deck_id"]) not in decks:
            raise FixtureSlotQualificationError(f"unknown deck {row['deck_id']}")
    total_slots = sum(len(censuses[island_id]["slots"]) for island_id in expected)
    if total_slots < len(sources):
        return {
            "kind": "earcrate_fixture_slot_qualification_receipt",
            "version": VERSION,
            "complete": False,
            "impossibility_claimed": True,
            "evidence_class": "counting",
            "source_count": len(sources),
            "slot_count": total_slots,
            "deficiency": len(sources) - total_slots,
            "parent_fixture_sha256": parent,
            "matrix_semantic_sha256": matrix_sha,
            "slot_census_family_sha256": census_sha,
            "qualified_candidate": None,
        }
    for row in islands:
        island_id = str(row["island_id"])
        count = int(row["qualification_source_count"])
        slots = len(censuses[island_id]["slots"])
        if count * cap < slots:
            return {
                "kind": "earcrate_fixture_slot_qualification_receipt",
                "version": VERSION,
                "complete": False,
                "impossibility_claimed": True,
                "evidence_class": "counting",
                "failure_island_id": island_id,
                "source_count": count,
                "slot_count": slots,
                "capacity": count * cap,
                "deficiency": slots - count * cap,
                "parent_fixture_sha256": parent,
                "matrix_semantic_sha256": matrix_sha,
                "slot_census_family_sha256": census_sha,
                "qualified_candidate": None,
            }
    seed = _sha(
        {
            "parent": parent,
            "matrix": matrix_sha,
            "census": census_sha,
            "cap": cap,
        }
    )
    last: Dict[str, Any] | None = None
    for round_index in range(rounds):
        flow, root, sink, source_nodes, slot_nodes, island_nodes = _anchor_flow(
            sources, islands, decks, censuses, seed, round_index
        )
        matched = flow.run(root, sink)
        if matched != len(sources):
            reached = flow.reached(root)
            return {
                "kind": "earcrate_fixture_slot_qualification_receipt",
                "version": VERSION,
                "complete": False,
                "impossibility_claimed": True,
                "evidence_class": "max_flow_min_cut",
                "network_contract": (
                    "source_once_to_compatible_observed_slot_to_exact_island_quota"
                ),
                "required_flow": len(sources),
                "cut_capacity": matched,
                "matched_source_count": matched,
                "source_count": len(sources),
                "deficiency": len(sources) - matched,
                "reachable_sources": sorted(
                    source
                    for source, node in source_nodes.items()
                    if node in reached
                ),
                "reachable_slots": sorted(
                    f"{island_id}:{slot}"
                    for (island_id, slot), node in slot_nodes.items()
                    if node in reached
                ),
                "reachable_island_quota_nodes": sorted(
                    island_id
                    for island_id, node in island_nodes.items()
                    if node in reached
                ),
                "parent_fixture_sha256": parent,
                "matrix_semantic_sha256": matrix_sha,
                "slot_census_family_sha256": census_sha,
                "qualified_candidate": None,
            }
        anchors = _anchors(flow, sources, source_nodes, slot_nodes)
        if len(anchors) != len(sources):
            raise FixtureSlotQualificationError(
                "maximum flow exposed no anchor for a source"
            )
        assignments = {source: value[0] for source, value in anchors.items()}
        complete, extra = _fill(
            assignments,
            anchors,
            islands,
            decks,
            censuses,
            cap,
            (seed, round_index),
        )
        if not complete:
            last = extra
            continue
        qualified = copy.deepcopy(dict(candidate))
        rows = [dict(row) for row in qualified.get("islands") or []]
        for row in rows:
            island_id = str(row["island_id"])
            row["source_include_ids"] = sorted(
                source
                for source, assigned in assignments.items()
                if assigned == island_id
            )
        qualified["islands"] = rows
        qualified.pop("fixture_id", None)
        qualified.pop("fixture_sha256", None)
        qualified["slot_qualification"] = {
            "version": VERSION,
            "parent_fixture_sha256": parent,
            "matrix_semantic_sha256": matrix_sha,
            "slot_census_family_sha256": census_sha,
            "source_pool_sha256": str(candidate.get("source_pool_sha256") or ""),
            "source_universe_sha256": _sha(sources),
            "source_count": len(sources),
            "source_count_policy": "preserve_exact_parent_count_per_island",
            "max_source_events": cap,
            "anchor_round": round_index,
            "anchor_matching": (
                "maximum_flow_over_observed_slots_with_exact_island_quotas"
            ),
            "remaining_slot_fill": "maximum_flow_under_per_source_event_cap",
            "island_source_counts": {
                island_id: sum(value == island_id for value in assignments.values())
                for island_id in sorted(expected)
            },
            "island_role_anchor_counts": {
                island_id: dict(
                    sorted(
                        Counter(
                            next(
                                slot["role_family"]
                                for slot in censuses[island_id]["slots"]
                                if str(slot["slot_key"]) == slot_key
                            )
                            for _source, (anchor_island, slot_key) in anchors.items()
                            if anchor_island == island_id
                        ).items()
                    )
                )
                for island_id in sorted(expected)
            },
        }
        identity = str(fixture_projection(qualified)["fixture_identity"])
        qualified["fixture_sha256"] = identity
        qualified["fixture_id"] = f"season002-slotq-{identity[:12]}"
        return {
            "kind": "earcrate_fixture_slot_qualification_receipt",
            "version": VERSION,
            "complete": True,
            "impossibility_claimed": False,
            "private_acceptance": None,
            "parent_fixture_sha256": parent,
            "qualified_fixture_sha256": identity,
            "matrix_semantic_sha256": matrix_sha,
            "slot_census_family_sha256": census_sha,
            "source_universe_preserved": sorted(
                source for row in rows for source in row["source_include_ids"]
            )
            == sources,
            "deck_sequence_preserved": [str(row["deck_id"]) for row in rows]
            == [str(row["deck_id"]) for row in islands],
            "duration_program_preserved": [
                float(row.get("allocated_duration_s") or 0.0) for row in rows
            ]
            == [float(row.get("allocated_duration_s") or 0.0) for row in islands],
            "anchor_round": round_index,
            "remaining_slot_fill": extra,
            "qualified_candidate": qualified,
        }
    return {
        "kind": "earcrate_fixture_slot_qualification_receipt",
        "version": VERSION,
        "complete": False,
        "impossibility_claimed": False,
        "private_acceptance": INDETERMINATE_ACTION,
        "evidence_class": "anchor_matching_round_bound",
        "rounds_executed": rounds,
        "max_anchor_rounds": rounds,
        "last_remaining_slot_failure": last,
        "parent_fixture_sha256": parent,
        "matrix_semantic_sha256": matrix_sha,
        "slot_census_family_sha256": census_sha,
        "qualified_candidate": None,
    }
