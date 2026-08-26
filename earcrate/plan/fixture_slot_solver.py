"""Mixed-integer authority for slot-qualified fixture repartition."""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan.fixture_slot_binding import (
    DEFAULT_MAX_SOURCE_EVENTS, EPS, FixtureSlotQualificationError,
    INDETERMINATE_ACTION, SLOT_QUALIFICATION_VERSION,
    _failure, _verified_census_campaign, semantic_sha256,
)

def qualify_fixture_candidate(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    max_source_events: Optional[int] = None,
    time_limit_s: float = 30.0,
    _solver: Any = milp,
) -> Dict[str, Any]:
    """Solve the immutable source partition against one observed slot family."""
    body = _core._candidate_body(candidate)
    islands, by_census_id = _verified_census_campaign(
        body, census_campaign
    )
    candidate_ids = [str(row["island_id"]) for row in islands]

    original_partition: Dict[str, str] = {}
    for island in islands:
        island_id = str(island["island_id"])
        for source_id in sorted(
            {
                str(value)
                for value in island.get("source_include_ids") or []
            }
        ):
            if source_id in original_partition:
                raise FixtureSlotQualificationError(
                    f"source {source_id!r} appears in multiple candidate islands"
                )
            original_partition[source_id] = island_id
    sources = sorted(original_partition)
    if not sources:
        raise FixtureSlotQualificationError(
            "fixture candidate has an empty source universe"
        )
    expected_universe = semantic_sha256(sources)
    if str(census_campaign.get("source_universe_sha256") or "") != expected_universe:
        raise FixtureSlotQualificationError(
            "slot census source universe does not match the candidate"
        )
    if int(census_campaign.get("source_count") or 0) != len(sources):
        raise FixtureSlotQualificationError(
            "slot census source count does not match the candidate"
        )

    cap_values = {
        int(row.get("max_source_events") or DEFAULT_MAX_SOURCE_EVENTS)
        for row in by_census_id.values()
    }
    if max_source_events is None:
        if len(cap_values) != 1:
            raise FixtureSlotQualificationError(
                "slot censuses disagree on max_source_events"
            )
        max_events = next(iter(cap_values))
    else:
        max_events = int(max_source_events)
        if any(value != max_events for value in cap_values):
            raise FixtureSlotQualificationError(
                "requested max_source_events disagrees with a slot census"
            )
    if max_events <= 0:
        raise FixtureSlotQualificationError(
            "max_source_events must be positive"
        )

    slot_rows: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    source_slots: Dict[str, List[Tuple[str, int, int]]] = {
        source_id: [] for source_id in sources
    }
    source_capabilities: Dict[Tuple[str, str], set[str]] = {}
    for island_id in candidate_ids:
        census = by_census_id[island_id]
        for source_row in census.get("sources") or []:
            source_id = str(source_row.get("source_id") or "")
            if source_id in original_partition:
                source_capabilities[(island_id, source_id)] = {
                    str(value)
                    for value in source_row.get(
                        "planner_role_capabilities"
                    )
                    or []
                }
        for raw_slot in census.get("slots") or []:
            key_values = list(raw_slot.get("slot_key") or [])
            if len(key_values) != 2:
                raise FixtureSlotQualificationError(
                    f"island {island_id!r} has a malformed slot key"
                )
            slot = (
                island_id,
                int(key_values[0]),
                int(key_values[1]),
            )
            if slot in slot_rows:
                raise FixtureSlotQualificationError(
                    f"duplicate slot identity: {slot}"
                )
            compatible = sorted(
                set(
                    str(value)
                    for value in raw_slot.get("compatible_sources") or []
                ).intersection(sources)
            )
            slot_rows[slot] = {
                **dict(raw_slot),
                "compatible_sources": compatible,
            }
            for source_id in compatible:
                source_slots[source_id].append(slot)
    for source_id in sources:
        source_slots[source_id].sort()

    total_slots = len(slot_rows)
    if total_slots < len(sources):
        return _failure(
            "coverage_counting_deficiency",
            "the observed section graphs contain fewer slots than mandatory sources",
            proof={
                "mandatory_source_count": len(sources),
                "slot_count": total_slots,
                "deficiency": len(sources) - total_slots,
            },
        )
    if total_slots > len(sources) * max_events:
        return _failure(
            "cap_counting_deficiency",
            "the observed section graphs contain more slots than the source universe can fill under the cap",
            proof={
                "mandatory_source_count": len(sources),
                "slot_count": total_slots,
                "max_source_events": max_events,
                "capacity": len(sources) * max_events,
                "deficiency": total_slots - len(sources) * max_events,
            },
        )
    hall = _core._global_hall_witness(sources, source_slots)
    if hall is not None:
        return _failure(
            "role_capacity",
            "the immutable source universe cannot cover the observed compatible slots",
            proof={"hall_witness": hall},
        )
    for island_id in candidate_ids:
        island_slots = [slot for slot in slot_rows if slot[0] == island_id]
        reachable = {
            source_id
            for slot in island_slots
            for source_id in slot_rows[slot]["compatible_sources"]
        }
        if len(island_slots) > len(reachable) * max_events:
            return _failure(
                "island_cap_counting_deficiency",
                f"island {island_id!r} has more slots than its reachable sources can fill under the cap",
                proof={
                    "island_id": island_id,
                    "slot_count": len(island_slots),
                    "reachable_source_count": len(reachable),
                    "max_source_events": max_events,
                    "capacity": len(reachable) * max_events,
                    "deficiency": len(island_slots)
                    - len(reachable) * max_events,
                },
            )

    x_index: Dict[Tuple[str, str], int] = {}
    y_index: Dict[Tuple[Tuple[str, int, int], str], int] = {}
    variable_names: List[str] = []
    objective: List[float] = []
    for source_pos, source_id in enumerate(sources):
        reachable_islands = sorted(
            {slot[0] for slot in source_slots[source_id]}
        )
        for island_pos, island_id in enumerate(candidate_ids):
            if island_id not in reachable_islands:
                continue
            x_index[(source_id, island_id)] = len(variable_names)
            variable_names.append(f"x:{source_id}:{island_id}")
            moved = (
                0.0
                if original_partition[source_id] == island_id
                else 1_000_000.0
            )
            tie = (
                source_pos * max(1, len(candidate_ids)) + island_pos + 1
            ) * 1e-3
            objective.append(moved + tie)
    for slot_pos, slot in enumerate(sorted(slot_rows)):
        for source_pos, source_id in enumerate(
            slot_rows[slot]["compatible_sources"]
        ):
            y_index[(slot, source_id)] = len(variable_names)
            variable_names.append(
                f"y:{slot[0]}:{slot[1]}:{slot[2]}:{source_id}"
            )
            objective.append(
                (slot_pos + 1) * 1e-7 + (source_pos + 1) * 1e-10
            )

    rows_i: List[int] = []
    cols_i: List[int] = []
    data: List[float] = []
    lower: List[float] = []
    upper: List[float] = []

    def add_constraint(
        coefficients: Mapping[int, float], lo: float, hi: float
    ) -> None:
        row_index = len(lower)
        for column, value in sorted(coefficients.items()):
            if abs(value) <= EPS:
                continue
            rows_i.append(row_index)
            cols_i.append(column)
            data.append(float(value))
        lower.append(float(lo))
        upper.append(float(hi))

    for source_id in sources:
        coefficients = {
            index: 1.0
            for (source, _island), index in x_index.items()
            if source == source_id
        }
        if not coefficients:
            return _failure(
                "role_capacity",
                f"source {source_id!r} reaches no observed island slot",
                proof={
                    "hall_witness": {
                        "deficient_source_subset": [source_id],
                        "deficient_source_count": 1,
                        "compatible_slot_neighbourhood": [],
                        "neighbourhood_slot_count": 0,
                        "deficiency": 1,
                    }
                },
            )
        add_constraint(coefficients, 1.0, 1.0)
    for slot in sorted(slot_rows):
        coefficients = {
            index: 1.0
            for (candidate_slot, _source), index in y_index.items()
            if candidate_slot == slot
        }
        if not coefficients:
            return _failure(
                "slot_unreachable",
                f"observed slot {slot!r} has no compatible source in the immutable universe",
                proof={"slot": list(slot), "compatible_source_count": 0},
            )
        add_constraint(coefficients, 1.0, 1.0)
    for (slot, source_id), y_col in y_index.items():
        add_constraint(
            {
                y_col: 1.0,
                x_index[(source_id, slot[0])]: -1.0,
            },
            -math.inf,
            0.0,
        )
    for (source_id, island_id), x_col in sorted(x_index.items()):
        ys = {
            index: 1.0
            for (slot_source, source), index in y_index.items()
            if source == source_id and slot_source[0] == island_id
        }
        coverage = dict(ys)
        coverage[x_col] = coverage.get(x_col, 0.0) - 1.0
        add_constraint(coverage, 0.0, math.inf)
        cap_row = dict(ys)
        cap_row[x_col] = cap_row.get(x_col, 0.0) - float(max_events)
        add_constraint(cap_row, -math.inf, 0.0)

    by_candidate_id = {
        str(row["island_id"]): row for row in islands
    }
    for island_id in candidate_ids:
        island = by_candidate_id[island_id]
        island_x = {
            index: 1.0
            for (source, candidate_island), index in x_index.items()
            if candidate_island == island_id
        }
        minimum = int(island.get("min_sources") or 1)
        maximum = int(island.get("max_sources") or len(sources))
        if minimum <= 0 or maximum < minimum:
            raise FixtureSlotQualificationError(
                f"candidate source bounds are invalid for {island_id!r}"
            )
        add_constraint(island_x, float(minimum), float(maximum))
        required_roles = sorted(
            {
                str(value)
                for value in island.get("required_roles") or []
            }
        )
        for role in required_roles:
            capable = {
                x_index[(source_id, island_id)]: 1.0
                for source_id in sources
                if (source_id, island_id) in x_index
                and role in source_capabilities.get(
                    (island_id, source_id), set()
                )
            }
            if not capable:
                return _failure(
                    "required_role_capacity",
                    f"island {island_id!r} has no reachable source capable of required role {role!r}",
                    proof={
                        "island_id": island_id,
                        "required_role": role,
                        "reachable_capable_source_count": 0,
                    },
                )
            add_constraint(capable, 1.0, math.inf)

    matrix = coo_matrix(
        (
            np.asarray(data, dtype=np.float64),
            (rows_i, cols_i),
        ),
        shape=(len(lower), len(variable_names)),
    ).tocsr()
    result = _solver(
        c=np.asarray(objective, dtype=np.float64),
        integrality=np.ones(len(variable_names), dtype=np.int8),
        bounds=Bounds(
            np.zeros(len(variable_names), dtype=np.float64),
            np.ones(len(variable_names), dtype=np.float64),
        ),
        constraints=LinearConstraint(
            matrix,
            np.asarray(lower, dtype=np.float64),
            np.asarray(upper, dtype=np.float64),
        ),
        options={
            "time_limit": max(0.01, float(time_limit_s)),
            "presolve": True,
            "mip_rel_gap": 0.0,
        },
    )
    solver_receipt = {
        "method": "scipy.optimize.milp_highs",
        "status": int(getattr(result, "status", -1)),
        "success": bool(getattr(result, "success", False)),
        "message": str(getattr(result, "message", "")),
        "objective": (
            None
            if getattr(result, "fun", None) is None
            else float(result.fun)
        ),
        "variable_count": len(variable_names),
        "constraint_count": len(lower),
        "time_limit_s": float(time_limit_s),
        "deterministic_variable_order": (
            "source_then_island_then_slot_then_source"
        ),
        "constraints": [
            "one_island_per_source",
            "one_source_per_slot",
            "source_coverage",
            "per_source_event_cap",
            "candidate_min_max_sources",
            "candidate_required_roles",
        ],
    }
    if not solver_receipt["success"] or getattr(result, "x", None) is None:
        failure_class = (
            "solver_infeasible_without_portable_witness"
            if solver_receipt["status"] == 2
            else "solver_bound_or_failure"
        )
        return _failure(
            failure_class,
            "slot qualification did not produce a complete assignment; no impossibility is claimed without an explicit witness",
            solver=solver_receipt,
        )

    values = np.asarray(result.x, dtype=np.float64)
    partition: Dict[str, List[str]] = {
        island_id: [] for island_id in candidate_ids
    }
    for (source_id, island_id), column in sorted(x_index.items()):
        if values[column] > 0.5:
            partition[island_id].append(source_id)
    for island_id in partition:
        partition[island_id].sort()
    if sorted(
        source_id for rows in partition.values() for source_id in rows
    ) != sources:
        return _failure(
            "solver_result_invariant",
            "solver output did not assign every source exactly once",
            solver=solver_receipt,
        )

    slot_assignment: List[Dict[str, Any]] = []
    event_counts: Counter[Tuple[str, str]] = Counter()
    for (slot, source_id), column in sorted(y_index.items()):
        if values[column] <= 0.5:
            continue
        slot_assignment.append(
            {
                "island_id": slot[0],
                "bar_start": slot[1],
                "layer_index": slot[2],
                "source_id": source_id,
            }
        )
        event_counts[(slot[0], source_id)] += 1
    if len(slot_assignment) != total_slots:
        return _failure(
            "solver_result_invariant",
            "solver output did not fill every observed slot",
            solver=solver_receipt,
        )
    if any(count > max_events for count in event_counts.values()):
        return _failure(
            "solver_result_invariant",
            "solver output exceeded the declared per-source event cap",
            solver=solver_receipt,
        )
    for island_id in candidate_ids:
        assigned = partition[island_id]
        island = by_candidate_id[island_id]
        minimum = int(island.get("min_sources") or 1)
        maximum = int(island.get("max_sources") or len(sources))
        if not minimum <= len(assigned) <= maximum:
            return _failure(
                "solver_result_invariant",
                "solver output violated candidate source bounds",
                solver=solver_receipt,
            )
        for role in island.get("required_roles") or []:
            if not any(
                str(role)
                in source_capabilities.get(
                    (island_id, source_id), set()
                )
                for source_id in assigned
            ):
                return _failure(
                    "solver_result_invariant",
                    "solver output violated candidate role completeness",
                    solver=solver_receipt,
                )

    parent_identity = str(
        body.get("fixture_sha256") or body.get("fixture_id") or ""
    )
    for island in islands:
        island["source_include_ids"] = partition[
            str(island["island_id"])
        ]
    body["islands"] = islands
    body.pop("fixture_id", None)
    body.pop("fixture_sha256", None)
    qualification = {
        "version": SLOT_QUALIFICATION_VERSION,
        "parent_fixture_identity": parent_identity,
        "census_campaign_sha256": str(
            census_campaign["campaign_sha256"]
        ),
        "census_identities": [
            str(by_census_id[island_id]["slot_census_sha256"])
            for island_id in candidate_ids
        ],
        "source_universe_sha256": semantic_sha256(sources),
        "source_count": len(sources),
        "slot_count": total_slots,
        "max_source_events": max_events,
        "moved_source_count": sum(
            1
            for source_id in sources
            if source_id
            not in partition[original_partition[source_id]]
        ),
        "slot_assignment_sha256": semantic_sha256(slot_assignment),
        "solver": solver_receipt,
        "scope": "one_observed_skeleton_round_replan_required",
        "impossibility_claimed": False,
    }
    body["fixture_slot_qualification"] = qualification

    from earcrate.plan.fixture_diversity import fixture_projection

    projection = fixture_projection(body)
    new_identity = str(projection["fixture_identity"])
    body["fixture_id"] = f"season002-slot-{new_identity[:12]}"
    body["fixture_sha256"] = new_identity
    return {
        "kind": "earcrate_fixture_slot_qualification_receipt",
        "version": SLOT_QUALIFICATION_VERSION,
        "complete": True,
        "impossibility_claimed": False,
        "parent_fixture_identity": parent_identity,
        "qualified_fixture_identity": new_identity,
        "moved_source_count": qualification["moved_source_count"],
        "solver": solver_receipt,
        "candidate": body,
        "slot_assignment": slot_assignment,
    }


__all__ = ["qualify_fixture_candidate"]
