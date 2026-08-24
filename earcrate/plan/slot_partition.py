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


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _rank(seed, *parts):
    return int(hashlib.sha256("|".join([str(seed), *map(str, parts)]).encode()).hexdigest(), 16)


class _Edge:
    __slots__ = ("to", "rev", "cap", "original")
    def __init__(self, to, rev, cap):
        self.to, self.rev, self.cap, self.original = to, rev, cap, cap


class _Flow:
    def __init__(self, size):
        self.g = [[] for _ in range(size)]
    def add(self, left, right, cap):
        index = len(self.g[left])
        self.g[left].append(_Edge(right, len(self.g[right]), cap))
        self.g[right].append(_Edge(left, index, 0))
    def run(self, source, sink):
        total, size = 0, len(self.g)
        while True:
            level = [-1] * size
            level[source] = 0
            q = deque([source])
            while q:
                node = q.popleft()
                for edge in self.g[node]:
                    if edge.cap and level[edge.to] < 0:
                        level[edge.to] = level[node] + 1
                        q.append(edge.to)
            if level[sink] < 0:
                return total
            work = [0] * size
            def send(node, amount):
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
    def reached(self, source):
        seen, q = {source}, deque([source])
        while q:
            node = q.popleft()
            for edge in self.g[node]:
                if edge.cap and edge.to not in seen:
                    seen.add(edge.to); q.append(edge.to)
        return seen


def _matrix(matrix):
    from earcrate.plan.fixture_derivation import normalize_matrix
    normalized = normalize_matrix(matrix)
    return {str(row["deck_id"]): row for row in normalized["decks"]}, str(normalized["matrix_semantic_sha256"])


def _candidate(candidate):
    islands, universe, seen = [], [], set()
    for index, raw in enumerate(candidate.get("islands") or []):
        row = copy.deepcopy(dict(raw))
        island, deck = str(row.get("island_id") or ""), str(row.get("deck_id") or "")
        if not island or not deck:
            raise FixtureSlotQualificationError(f"candidate island {index} needs island_id and deck_id")
        sources = sorted({str(x) for x in row.get("source_include_ids") or []})
        if not sources:
            raise FixtureSlotQualificationError(f"candidate island {island} has no sources")
        overlap = seen.intersection(sources)
        if overlap:
            raise FixtureSlotQualificationError(f"source appears in multiple islands: {sorted(overlap)[0]}")
        seen.update(sources); universe.extend(sources)
        row["source_include_ids"] = sources
        row["qualification_source_count"] = len(sources)
        islands.append(row)
    if not islands:
        raise FixtureSlotQualificationError("candidate has no islands")
    return islands, sorted(universe)


def _censuses(receipt):
    rows = list(receipt.get("islands") or [])
    if not rows and receipt.get("slot_census"):
        rows = [receipt["slot_census"]]
    out = {}
    for index, raw in enumerate(rows):
        row = dict(raw)
        island = str(row.get("island_id") or "")
        if not island or island in out:
            raise FixtureSlotQualificationError(f"invalid slot census island at index {index}")
        slots = [dict(slot) for slot in row.get("slots") or []]
        for slot in slots:
            if not str(slot.get("slot_key") or ""):
                raise FixtureSlotQualificationError(f"unnamed slot in island {island}")
            slot["role_family"] = role_family(str(slot.get("role_family") or slot.get("role") or "full"))
        row["slots"] = sorted(slots, key=lambda slot: (
            int(slot.get("bar_start") or 0), int(slot.get("layer_index") or 0), str(slot["slot_key"])
        ))
        out[island] = row
    return out


def _anchor_flow(sources, islands, decks, censuses, seed, round_index):
    domains = {}
    for source in sources:
        values = []
        for island in islands:
            iid, deck = str(island["island_id"]), str(island["deck_id"])
            roles = {role_family(role) for role in (decks[deck]["sources"].get(source) or set())}
            for slot in censuses[iid]["slots"]:
                if slot["role_family"] in roles:
                    values.append((iid, str(slot["slot_key"])))
        domains[source] = sorted(values, key=lambda value: (_rank(seed, round_index, source, *value), value))
    order = sorted(sources, key=lambda source: (len(domains[source]), _rank(seed, round_index, source), source))
    source_nodes, slot_nodes, island_nodes, cursor = {}, {}, {}, 1
    for source in order:
        source_nodes[source] = cursor; cursor += 1
    for island in islands:
        iid = str(island["island_id"])
        for slot in censuses[iid]["slots"]:
            slot_nodes[(iid, str(slot["slot_key"]))] = cursor; cursor += 1
    for island in islands:
        island_nodes[str(island["island_id"])] = cursor; cursor += 1
    sink, root = cursor, 0
    flow = _Flow(sink + 1)
    for source in order:
        flow.add(root, source_nodes[source], 1)
        for value in domains[source]:
            flow.add(source_nodes[source], slot_nodes[value], 1)
    for island in islands:
        iid = str(island["island_id"])
        for slot in censuses[iid]["slots"]:
            flow.add(slot_nodes[(iid, str(slot["slot_key"]))], island_nodes[iid], 1)
        flow.add(island_nodes[iid], sink, int(island["qualification_source_count"]))
    return flow, root, sink, source_nodes, slot_nodes, island_nodes


def _anchors(flow, sources, source_nodes, slot_nodes):
    reverse = {node: key for key, node in slot_nodes.items()}
    out = {}
    for source in sources:
        for edge in flow.g[source_nodes[source]]:
            if edge.original == 1 and edge.cap == 0 and edge.to in reverse:
                out[source] = reverse[edge.to]; break
    return out


def _fill(assignments, anchors, islands, decks, censuses, cap, seed):
    reports = {}
    for island in islands:
        iid, deck = str(island["island_id"]), str(island["deck_id"])
        sources = sorted(source for source, assigned in assignments.items() if assigned == iid)
        anchor_slots = {slot for source, (anchor_island, slot) in anchors.items() if anchor_island == iid}
        remaining = [slot for slot in censuses[iid]["slots"] if str(slot["slot_key"]) not in anchor_slots]
        if not remaining:
            reports[iid] = {"remaining_slot_count": 0, "filled_slot_count": 0, "source_count": len(sources)}
            continue
        source_nodes = {source: 1 + i for i, source in enumerate(sources)}
        offset = 1 + len(sources)
        slot_nodes = {str(slot["slot_key"]): offset + i for i, slot in enumerate(remaining)}
        sink, root = offset + len(remaining), 0
        flow = _Flow(sink + 1)
        for source in sources:
            flow.add(root, source_nodes[source], cap - 1)
            roles = {role_family(role) for role in (decks[deck]["sources"].get(source) or set())}
            for slot in sorted(remaining, key=lambda slot: (_rank(seed, iid, source, slot["slot_key"]), slot["slot_key"])):
                if slot["role_family"] in roles:
                    flow.add(source_nodes[source], slot_nodes[str(slot["slot_key"])], 1)
        for slot in remaining:
            flow.add(slot_nodes[str(slot["slot_key"])], sink, 1)
        filled = flow.run(root, sink)
        reports[iid] = {"remaining_slot_count": len(remaining), "filled_slot_count": filled, "source_count": len(sources)}
        if filled != len(remaining):
            return False, {"failure_class": "remaining_slot_capacity", "failure_island_id": iid, "islands": reports}
    return True, {"islands": reports}


def qualify_fixture_candidate(matrix, candidate, slot_census_receipt, *, max_source_events=DEFAULT_MAX_SOURCE_EVENTS, max_anchor_rounds=DEFAULT_MAX_ANCHOR_ROUNDS):
    from earcrate.plan.fixture_diversity import fixture_projection
    cap, rounds = int(max_source_events), int(max_anchor_rounds)
    if cap <= 0 or rounds < 0:
        raise FixtureSlotQualificationError("invalid qualification bound")
    projection = fixture_projection(candidate)
    parent = str(candidate.get("fixture_sha256") or projection["fixture_identity"])
    if parent != projection["fixture_identity"]:
        raise FixtureSlotQualificationError("candidate fixture_sha256 does not match projection")
    bound_parent = str(slot_census_receipt.get("candidate_fixture_sha256") or "")
    if bound_parent and bound_parent != parent:
        raise FixtureSlotQualificationError("slot census is bound to a different fixture")
    censuses, (islands, sources), (decks, matrix_sha) = _censuses(slot_census_receipt), _candidate(candidate), _matrix(matrix)
    expected = {str(row["island_id"]) for row in islands}
    if set(censuses) != expected:
        raise FixtureSlotQualificationError("slot census island set mismatch")
    for row in islands:
        if str(row["deck_id"]) not in decks:
            raise FixtureSlotQualificationError(f"unknown deck {row['deck_id']}")
    total_slots = sum(len(censuses[iid]["slots"]) for iid in expected)
    if total_slots < len(sources):
        return {"kind": "earcrate_fixture_slot_qualification_receipt", "version": VERSION,
                "complete": False, "impossibility_claimed": True, "evidence_class": "counting",
                "source_count": len(sources), "slot_count": total_slots,
                "deficiency": len(sources) - total_slots, "parent_fixture_sha256": parent,
                "matrix_semantic_sha256": matrix_sha, "qualified_candidate": None}
    for row in islands:
        iid, count = str(row["island_id"]), int(row["qualification_source_count"])
        slots = len(censuses[iid]["slots"])
        if count * cap < slots:
            return {"kind": "earcrate_fixture_slot_qualification_receipt", "version": VERSION,
                    "complete": False, "impossibility_claimed": True, "evidence_class": "counting",
                    "failure_island_id": iid, "source_count": count, "slot_count": slots,
                    "capacity": count * cap, "deficiency": slots - count * cap,
                    "parent_fixture_sha256": parent, "matrix_semantic_sha256": matrix_sha,
                    "qualified_candidate": None}
    census_sha = str(slot_census_receipt.get("slot_census_family_sha256") or _sha(slot_census_receipt))
    seed = _sha({"parent": parent, "matrix": matrix_sha, "census": census_sha, "cap": cap})
    last = None
    for round_index in range(rounds):
        flow, root, sink, source_nodes, slot_nodes, island_nodes = _anchor_flow(
            sources, islands, decks, censuses, seed, round_index
        )
        matched = flow.run(root, sink)
        if matched != len(sources):
            reached = flow.reached(root)
            return {
                "kind": "earcrate_fixture_slot_qualification_receipt", "version": VERSION,
                "complete": False, "impossibility_claimed": True,
                "evidence_class": "max_flow_min_cut", "matched_source_count": matched,
                "source_count": len(sources), "deficiency": len(sources) - matched,
                "reachable_sources": sorted(source for source, node in source_nodes.items() if node in reached),
                "reachable_slots": sorted(f"{iid}:{slot}" for (iid, slot), node in slot_nodes.items() if node in reached),
                "reachable_island_quota_nodes": sorted(iid for iid, node in island_nodes.items() if node in reached),
                "parent_fixture_sha256": parent, "matrix_semantic_sha256": matrix_sha,
                "qualified_candidate": None,
            }
        anchors = _anchors(flow, sources, source_nodes, slot_nodes)
        if len(anchors) != len(sources):
            raise FixtureSlotQualificationError("maximum flow exposed no anchor for a source")
        assignments = {source: value[0] for source, value in anchors.items()}
        complete, extra = _fill(assignments, anchors, islands, decks, censuses, cap, (seed, round_index))
        if not complete:
            last = extra; continue
        qualified = copy.deepcopy(dict(candidate))
        rows = [dict(row) for row in qualified.get("islands") or []]
        for row in rows:
            iid = str(row["island_id"])
            row["source_include_ids"] = sorted(source for source, assigned in assignments.items() if assigned == iid)
        qualified["islands"] = rows
        qualified.pop("fixture_id", None); qualified.pop("fixture_sha256", None)
        qualified["slot_qualification"] = {
            "version": VERSION, "parent_fixture_sha256": parent,
            "matrix_semantic_sha256": matrix_sha, "slot_census_family_sha256": census_sha,
            "source_pool_sha256": str(candidate.get("source_pool_sha256") or ""),
            "source_universe_sha256": _sha(sources), "source_count": len(sources),
            "source_count_policy": "preserve_exact_parent_count_per_island",
            "max_source_events": cap, "anchor_round": round_index,
            "anchor_matching": "maximum_flow_over_observed_slots_with_exact_island_quotas",
            "remaining_slot_fill": "maximum_flow_under_per_source_event_cap",
            "island_source_counts": {iid: sum(value == iid for value in assignments.values()) for iid in sorted(expected)},
            "island_role_anchor_counts": {
                iid: dict(sorted(Counter(
                    next(slot["role_family"] for slot in censuses[iid]["slots"] if str(slot["slot_key"]) == slot_key)
                    for source, (anchor_island, slot_key) in anchors.items() if anchor_island == iid
                ).items())) for iid in sorted(expected)
            },
        }
        identity = str(fixture_projection(qualified)["fixture_identity"])
        qualified["fixture_sha256"] = identity
        qualified["fixture_id"] = f"season002-slotq-{identity[:12]}"
        return {
            "kind": "earcrate_fixture_slot_qualification_receipt", "version": VERSION,
            "complete": True, "impossibility_claimed": False, "private_acceptance": None,
            "parent_fixture_sha256": parent, "qualified_fixture_sha256": identity,
            "matrix_semantic_sha256": matrix_sha, "slot_census_family_sha256": census_sha,
            "source_universe_preserved": sorted(source for row in rows for source in row["source_include_ids"]) == sources,
            "deck_sequence_preserved": [str(row["deck_id"]) for row in rows] == [str(row["deck_id"]) for row in islands],
            "duration_program_preserved": [float(row.get("allocated_duration_s") or 0.0) for row in rows] == [float(row.get("allocated_duration_s") or 0.0) for row in islands],
            "anchor_round": round_index, "remaining_slot_fill": extra,
            "qualified_candidate": qualified,
        }
    return {
        "kind": "earcrate_fixture_slot_qualification_receipt", "version": VERSION,
        "complete": False, "impossibility_claimed": False,
        "private_acceptance": INDETERMINATE_ACTION,
        "evidence_class": "anchor_matching_round_bound", "rounds_executed": rounds,
        "max_anchor_rounds": rounds, "last_remaining_slot_failure": last,
        "parent_fixture_sha256": parent, "matrix_semantic_sha256": matrix_sha,
        "qualified_candidate": None,
    }
