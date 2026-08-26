"""Select a planable source universe against one observed slot family.

Stage 2C proved that repartitioning an immutable source universe can still be
mathematically impossible. This authority changes exactly that layer: it may
drop sources from the parent fixture while preserving the exact decks, island
durations, policies, source custody, role requirements, per-source event cap,
and every observed slot.

The solver runs in two deterministic phases. Phase one maximizes the number of
selected sources that can each be represented at least once while filling every
observed slot. Phase two fixes that optimum and minimizes movement from the
parent partition with stable semantic tie-breaking. A successful result is a
new direct fixture candidate that must be replanned through the ordinary strict
product path. Solver bounds and solver-only infeasibility remain indeterminate.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan.fixture_slot_turnover_contract import (
    _effective_turnover_requirement,
)

SOURCE_UNIVERSE_SELECTION_VERSION = (
    "earcrate_fixture_source_universe_selection_v1"
)
INDETERMINATE_ACTION = (
    "halt_source_universe_selection_this_is_not_an_impossibility_proof"
)
PAIR_CONSTRAINT_HALT = (
    "halt_source_universe_selection_parent_pair_constraints_are_not_encoded"
)
EPS = _core.EPS


def _failure(
    failure_class: str,
    reason: str,
    *,
    proof: Optional[Mapping[str, Any]] = None,
    solver: Optional[Mapping[str, Any]] = None,
    parent_fixture_identity: str = "",
    parent_source_count: int = 0,
) -> Dict[str, Any]:
    proved = proof is not None
    body: Dict[str, Any] = {
        "kind": "earcrate_fixture_source_universe_selection_receipt",
        "version": SOURCE_UNIVERSE_SELECTION_VERSION,
        "complete": False,
        "failure_class": str(failure_class),
        "reason": str(reason),
        "impossibility_claimed": bool(proved),
        "proof": dict(proof or {}),
        "solver": dict(solver or {}),
        "parent_fixture_identity": str(parent_fixture_identity),
        "parent_source_count": int(parent_source_count),
        "candidate": None,
    }
    if not proved:
        body["private_acceptance"] = INDETERMINATE_ACTION
    return body


def _stable_fraction(*parts: str) -> float:
    digest = hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _solver_receipt(
    result: Any,
    *,
    phase: str,
    variable_count: int,
    constraint_count: int,
    time_limit_s: float,
    selected_source_count: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "method": "scipy.optimize.milp_highs",
        "phase": str(phase),
        "status": int(getattr(result, "status", -1)),
        "success": bool(getattr(result, "success", False)),
        "message": str(getattr(result, "message", "")),
        "objective": (
            None
            if getattr(result, "fun", None) is None
            else float(result.fun)
        ),
        "variable_count": int(variable_count),
        "constraint_count": int(constraint_count),
        "time_limit_s": float(time_limit_s),
        "selected_source_count": (
            None
            if selected_source_count is None
            else int(selected_source_count)
        ),
        "deterministic_variable_order": (
            "source_then_island_then_slot_then_source"
        ),
    }


def _run_milp(
    *,
    objective: Sequence[float],
    rows_i: Sequence[int],
    cols_i: Sequence[int],
    data: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    variable_count: int,
    time_limit_s: float,
    solver: Any,
) -> Any:
    matrix = coo_matrix(
        (
            np.asarray(data, dtype=np.float64),
            (
                np.asarray(rows_i, dtype=np.int64),
                np.asarray(cols_i, dtype=np.int64),
            ),
        ),
        shape=(len(lower), int(variable_count)),
    ).tocsr()
    return solver(
        c=np.asarray(objective, dtype=np.float64),
        integrality=np.ones(int(variable_count), dtype=np.int8),
        bounds=Bounds(
            np.zeros(int(variable_count), dtype=np.float64),
            np.ones(int(variable_count), dtype=np.float64),
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


def select_planable_source_universe(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    target_source_count: Optional[int] = None,
    max_source_events: Optional[int] = None,
    time_limit_s: float = 30.0,
    _solver: Any = milp,
) -> Dict[str, Any]:
    """Select the largest planable subset of one candidate source universe.

    ``target_source_count`` may request an exact count no larger than the
    phase-one optimum. This supports a later campaign choosing one common
    cardinality across several fixture authorities without changing any other
    law. A target above the certified optimum stops as indeterminate rather
    than converting solver optimality into a portable impossibility witness.
    """
    if float(time_limit_s) <= 0.0:
        raise _core.FixtureSlotQualificationError(
            "time_limit_s must be positive"
        )

    body = _core._candidate_body(candidate)
    islands, by_census_id = _binding._verified_census_campaign(
        body, census_campaign
    )
    parent_identity = str(
        body.get("fixture_sha256") or body.get("fixture_id") or ""
    )

    parent_refusal = census_campaign.get("parent_exact_pool_refusal")
    if isinstance(parent_refusal, Mapping):
        forbidden = list(parent_refusal.get("forbidden_final_pairs") or [])
        parent_class = str(parent_refusal.get("failure_class") or "")
        if forbidden or parent_class == "section_pair_compatibility":
            result = _failure(
                "parent_pair_constraints_not_encoded",
                (
                    "the parent exact-pool refusal contains atom-pair "
                    "co-occurrence constraints, but source-universe selection "
                    "carries source identities only"
                ),
                solver={
                    "method": "not_run",
                    "parent_failure_class": parent_class,
                    "learned_pair_constraint_count": len(forbidden),
                },
                parent_fixture_identity=parent_identity,
            )
            result["private_acceptance"] = PAIR_CONSTRAINT_HALT
            result["parent_exact_pool_refusal"] = dict(parent_refusal)
            return result

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
                raise _core.FixtureSlotQualificationError(
                    f"source {source_id!r} appears in multiple candidate islands"
                )
            original_partition[source_id] = island_id
    sources = sorted(original_partition)
    if not sources:
        raise _core.FixtureSlotQualificationError(
            "fixture candidate has an empty source universe"
        )
    parent_count = len(sources)

    expected_universe = _core.semantic_sha256(sources)
    if str(census_campaign.get("source_universe_sha256") or "") != expected_universe:
        raise _core.FixtureSlotQualificationError(
            "slot census source universe does not match the candidate"
        )
    if int(census_campaign.get("source_count") or 0) != parent_count:
        raise _core.FixtureSlotQualificationError(
            "slot census source count does not match the candidate"
        )

    cap_values = {
        int(row.get("max_source_events") or _core.DEFAULT_MAX_SOURCE_EVENTS)
        for row in by_census_id.values()
    }
    if max_source_events is None:
        if len(cap_values) != 1:
            raise _core.FixtureSlotQualificationError(
                "slot censuses disagree on max_source_events"
            )
        max_events = next(iter(cap_values))
    else:
        max_events = int(max_source_events)
        if any(value != max_events for value in cap_values):
            raise _core.FixtureSlotQualificationError(
                "requested max_source_events disagrees with a slot census"
            )
    if max_events <= 0:
        raise _core.FixtureSlotQualificationError(
            "max_source_events must be positive"
        )

    candidate_ids = [str(row["island_id"]) for row in islands]
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
                raise _core.FixtureSlotQualificationError(
                    f"island {island_id!r} has a malformed slot key"
                )
            slot = (
                island_id,
                int(key_values[0]),
                int(key_values[1]),
            )
            if slot in slot_rows:
                raise _core.FixtureSlotQualificationError(
                    f"duplicate slot identity: {slot}"
                )
            compatible = sorted(
                set(
                    str(value)
                    for value in raw_slot.get("compatible_sources") or []
                ).intersection(sources)
            )
            if not compatible:
                return _failure(
                    "slot_unreachable",
                    (
                        f"observed slot {slot!r} has no compatible source "
                        "in the parent universe"
                    ),
                    proof={
                        "slot": list(slot),
                        "compatible_source_count": 0,
                    },
                    parent_fixture_identity=parent_identity,
                    parent_source_count=parent_count,
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
    if total_slots <= 0:
        return _failure(
            "empty_slot_family",
            "the observed census contains no assignable slots",
            proof={"slot_count": 0},
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )
    globally_reachable = {
        source_id
        for slot in slot_rows.values()
        for source_id in slot["compatible_sources"]
    }
    if total_slots > len(globally_reachable) * max_events:
        return _failure(
            "global_cap_counting_deficiency",
            (
                "the observed slots exceed the complete reachable source "
                "capacity under the event cap"
            ),
            proof={
                "slot_count": total_slots,
                "reachable_source_count": len(globally_reachable),
                "max_source_events": max_events,
                "capacity": len(globally_reachable) * max_events,
                "deficiency": (
                    total_slots - len(globally_reachable) * max_events
                ),
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )

    x_index: Dict[Tuple[str, str], int] = {}
    y_index: Dict[Tuple[Tuple[str, int, int], str], int] = {}
    variable_names: List[str] = []
    for source_id in sources:
        reachable_islands = sorted(
            {slot[0] for slot in source_slots[source_id]}
        )
        for island_id in candidate_ids:
            if island_id not in reachable_islands:
                continue
            x_index[(source_id, island_id)] = len(variable_names)
            variable_names.append(f"x:{source_id}:{island_id}")
    for slot in sorted(slot_rows):
        for source_id in slot_rows[slot]["compatible_sources"]:
            y_index[(slot, source_id)] = len(variable_names)
            variable_names.append(
                f"y:{slot[0]}:{slot[1]}:{slot[2]}:{source_id}"
            )

    rows_i: List[int] = []
    cols_i: List[int] = []
    data: List[float] = []
    lower: List[float] = []
    upper: List[float] = []

    def add_constraint(
        coefficients: Mapping[int, float],
        lo: float,
        hi: float,
    ) -> None:
        row_index = len(lower)
        for column, value in sorted(coefficients.items()):
            if abs(float(value)) <= EPS:
                continue
            rows_i.append(row_index)
            cols_i.append(int(column))
            data.append(float(value))
        lower.append(float(lo))
        upper.append(float(hi))

    selected_columns: List[int] = []
    for source_id in sources:
        columns = {
            index: 1.0
            for (source, _island), index in x_index.items()
            if source == source_id
        }
        if columns:
            selected_columns.extend(columns)
            add_constraint(columns, 0.0, 1.0)

    for slot in sorted(slot_rows):
        columns = {
            index: 1.0
            for (candidate_slot, _source), index in y_index.items()
            if candidate_slot == slot
        }
        add_constraint(columns, 1.0, 1.0)

    for (slot, source_id), y_col in sorted(y_index.items()):
        x_col = x_index.get((source_id, slot[0]))
        if x_col is None:
            raise _core.FixtureSlotQualificationError(
                "slot edge has no source-island selection variable"
            )
        add_constraint({y_col: 1.0, x_col: -1.0}, -math.inf, 0.0)

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
    base_params = _core._base_params(body)
    effective_minimums: Dict[str, int] = {}
    for island_id in candidate_ids:
        island = by_candidate_id[island_id]
        island_x = {
            index: 1.0
            for (source, candidate_island), index in x_index.items()
            if candidate_island == island_id
        }
        candidate_minimum = int(island.get("min_sources") or 1)
        turnover_minimum = int(
            _effective_turnover_requirement(island, base_params)
        )
        minimum = max(candidate_minimum, turnover_minimum)
        maximum = int(island.get("max_sources") or parent_count)
        effective_minimums[island_id] = minimum
        if minimum <= 0 or maximum < minimum:
            return _failure(
                "island_source_bound_deficiency",
                (
                    f"island {island_id!r} cannot satisfy its effective "
                    "source-count bounds"
                ),
                proof={
                    "island_id": island_id,
                    "candidate_min_sources": candidate_minimum,
                    "turnover_required_sources": turnover_minimum,
                    "effective_min_sources": minimum,
                    "candidate_max_sources": maximum,
                    "deficiency": max(0, minimum - maximum),
                },
                parent_fixture_identity=parent_identity,
                parent_source_count=parent_count,
            )
        if len(island_x) < minimum:
            return _failure(
                "island_reachable_source_count_deficiency",
                (
                    f"island {island_id!r} reaches fewer sources than its "
                    "effective turnover minimum"
                ),
                proof={
                    "island_id": island_id,
                    "reachable_source_count": len(island_x),
                    "effective_min_sources": minimum,
                    "deficiency": minimum - len(island_x),
                },
                parent_fixture_identity=parent_identity,
                parent_source_count=parent_count,
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
                    (
                        f"island {island_id!r} has no reachable source "
                        f"capable of required role {role!r}"
                    ),
                    proof={
                        "island_id": island_id,
                        "required_role": role,
                        "reachable_capable_source_count": 0,
                    },
                    parent_fixture_identity=parent_identity,
                    parent_source_count=parent_count,
                )
            add_constraint(capable, 1.0, math.inf)

    phase_one_objective = np.zeros(len(variable_names), dtype=np.float64)
    for column in selected_columns:
        phase_one_objective[column] = -1.0

    phase_one = _run_milp(
        objective=phase_one_objective,
        rows_i=rows_i,
        cols_i=cols_i,
        data=data,
        lower=lower,
        upper=upper,
        variable_count=len(variable_names),
        time_limit_s=float(time_limit_s),
        solver=_solver,
    )
    phase_one_receipt = _solver_receipt(
        phase_one,
        phase="maximize_selected_source_count",
        variable_count=len(variable_names),
        constraint_count=len(lower),
        time_limit_s=float(time_limit_s),
    )
    if (
        not phase_one_receipt["success"]
        or phase_one_receipt["status"] != 0
        or getattr(phase_one, "x", None) is None
    ):
        return _failure(
            "solver_bound_or_failure",
            (
                "source-universe maximization did not reach a certified "
                "optimum; no impossibility is claimed"
            ),
            solver={"phase_one": phase_one_receipt},
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )

    first_values = np.asarray(phase_one.x, dtype=np.float64)
    maximum_count = int(
        round(sum(first_values[column] for column in selected_columns))
    )
    if maximum_count <= 0:
        return _failure(
            "solver_result_invariant",
            "the certified optimum selected no source despite filled slots",
            solver={"phase_one": phase_one_receipt},
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )

    if target_source_count is None:
        selected_count = maximum_count
    else:
        selected_count = int(target_source_count)
        if selected_count <= 0:
            raise _core.FixtureSlotQualificationError(
                "target_source_count must be positive"
            )
        if selected_count > parent_count:
            raise _core.FixtureSlotQualificationError(
                "target_source_count exceeds the parent source count"
            )
        if selected_count > maximum_count:
            return _failure(
                "target_exceeds_solver_certified_maximum",
                (
                    "the requested common source count exceeds the current "
                    "solver-certified optimum; this is not emitted as a "
                    "portable impossibility proof"
                ),
                solver={
                    "phase_one": {
                        **phase_one_receipt,
                        "selected_source_count": maximum_count,
                    },
                    "target_source_count": selected_count,
                },
                parent_fixture_identity=parent_identity,
                parent_source_count=parent_count,
            )

    exact_count = {
        column: 1.0 for column in sorted(set(selected_columns))
    }
    add_constraint(exact_count, float(selected_count), float(selected_count))

    phase_two_objective = np.zeros(len(variable_names), dtype=np.float64)
    for source_pos, source_id in enumerate(sources):
        for island_pos, island_id in enumerate(candidate_ids):
            column = x_index.get((source_id, island_id))
            if column is None:
                continue
            moved = (
                0.0
                if original_partition[source_id] == island_id
                else 1_000_000.0
            )
            semantic_tie = _stable_fraction(
                parent_identity, source_id, island_id
            )
            lexical_tie = (
                source_pos * max(1, len(candidate_ids))
                + island_pos
                + 1
            ) * 1e-6
            phase_two_objective[column] = (
                moved + semantic_tie * 1e-3 + lexical_tie
            )
    for slot_pos, slot in enumerate(sorted(slot_rows)):
        for source_pos, source_id in enumerate(
            slot_rows[slot]["compatible_sources"]
        ):
            column = y_index[(slot, source_id)]
            phase_two_objective[column] = (
                (slot_pos + 1) * 1e-9 + (source_pos + 1) * 1e-12
            )

    phase_two = _run_milp(
        objective=phase_two_objective,
        rows_i=rows_i,
        cols_i=cols_i,
        data=data,
        lower=lower,
        upper=upper,
        variable_count=len(variable_names),
        time_limit_s=float(time_limit_s),
        solver=_solver,
    )
    phase_two_receipt = _solver_receipt(
        phase_two,
        phase="fix_count_then_minimize_partition_movement",
        variable_count=len(variable_names),
        constraint_count=len(lower),
        time_limit_s=float(time_limit_s),
        selected_source_count=selected_count,
    )
    if (
        not phase_two_receipt["success"]
        or phase_two_receipt["status"] != 0
        or getattr(phase_two, "x", None) is None
    ):
        return _failure(
            "solver_bound_or_failure",
            (
                "source-universe selection did not reach a certified "
                "deterministic assignment at the selected count"
            ),
            solver={
                "phase_one": {
                    **phase_one_receipt,
                    "selected_source_count": maximum_count,
                },
                "phase_two": phase_two_receipt,
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )

    values = np.asarray(phase_two.x, dtype=np.float64)
    partition: Dict[str, List[str]] = {
        island_id: [] for island_id in candidate_ids
    }
    for (source_id, island_id), column in sorted(x_index.items()):
        if values[column] > 0.5:
            partition[island_id].append(source_id)
    for island_id in partition:
        partition[island_id].sort()
    selected_sources = sorted(
        source_id
        for island_sources in partition.values()
        for source_id in island_sources
    )
    if len(selected_sources) != selected_count:
        return _failure(
            "solver_result_invariant",
            "solver output selected the wrong number of sources",
            solver={
                "phase_one": phase_one_receipt,
                "phase_two": phase_two_receipt,
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )
    if len(set(selected_sources)) != len(selected_sources):
        return _failure(
            "solver_result_invariant",
            "solver output assigned one source to multiple islands",
            solver={
                "phase_one": phase_one_receipt,
                "phase_two": phase_two_receipt,
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
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
            solver={
                "phase_one": phase_one_receipt,
                "phase_two": phase_two_receipt,
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )
    if any(count > max_events for count in event_counts.values()):
        return _failure(
            "solver_result_invariant",
            "solver output exceeded the per-source event cap",
            solver={
                "phase_one": phase_one_receipt,
                "phase_two": phase_two_receipt,
            },
            parent_fixture_identity=parent_identity,
            parent_source_count=parent_count,
        )

    for island_id in candidate_ids:
        assigned = partition[island_id]
        island = by_candidate_id[island_id]
        minimum = effective_minimums[island_id]
        maximum = int(island.get("max_sources") or parent_count)
        if not minimum <= len(assigned) <= maximum:
            return _failure(
                "solver_result_invariant",
                "solver output violated effective island source bounds",
                solver={
                    "phase_one": phase_one_receipt,
                    "phase_two": phase_two_receipt,
                },
                parent_fixture_identity=parent_identity,
                parent_source_count=parent_count,
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
                    "solver output violated required role completeness",
                    solver={
                        "phase_one": phase_one_receipt,
                        "phase_two": phase_two_receipt,
                    },
                    parent_fixture_identity=parent_identity,
                    parent_source_count=parent_count,
                )

    dropped_sources = sorted(set(sources) - set(selected_sources))
    for island in islands:
        island["source_include_ids"] = partition[str(island["island_id"])]
    body["islands"] = islands
    body.pop("fixture_id", None)
    body.pop("fixture_sha256", None)

    selection = {
        "version": SOURCE_UNIVERSE_SELECTION_VERSION,
        "parent_fixture_identity": parent_identity,
        "census_campaign_sha256": str(
            census_campaign.get("campaign_sha256") or ""
        ),
        "parent_source_universe_sha256": _core.semantic_sha256(sources),
        "parent_source_count": parent_count,
        "maximum_planable_source_count": maximum_count,
        "selected_source_universe_sha256": _core.semantic_sha256(
            selected_sources
        ),
        "selected_source_count": selected_count,
        "dropped_source_count": len(dropped_sources),
        "dropped_source_ids": dropped_sources,
        "max_source_events": max_events,
        "effective_min_sources_by_island": {
            key: int(value)
            for key, value in sorted(effective_minimums.items())
        },
        "slot_assignment_sha256": _core.semantic_sha256(slot_assignment),
        "solver": {
            "phase_one": {
                **phase_one_receipt,
                "selected_source_count": maximum_count,
                "optimality_disposition": (
                    "solver_certified_optimum_not_portable_impossibility_proof"
                ),
            },
            "phase_two": phase_two_receipt,
        },
        "scope": (
            "maximum_planable_source_universe_under_one_observed_"
            "skeleton_round_replan_required"
        ),
        "impossibility_claimed": False,
    }
    body["fixture_source_universe_selection"] = selection

    from earcrate.plan.fixture_diversity import fixture_projection

    projection = fixture_projection(body)
    new_identity = str(projection["fixture_identity"])
    body["fixture_id"] = f"season002-universe-{new_identity[:12]}"
    body["fixture_sha256"] = new_identity

    return {
        "kind": "earcrate_fixture_source_universe_selection_receipt",
        "version": SOURCE_UNIVERSE_SELECTION_VERSION,
        "complete": True,
        "impossibility_claimed": False,
        "parent_fixture_identity": parent_identity,
        "selected_fixture_identity": new_identity,
        "parent_source_count": parent_count,
        "maximum_planable_source_count": maximum_count,
        "selected_source_count": selected_count,
        "dropped_source_count": len(dropped_sources),
        "dropped_source_ids": dropped_sources,
        "solver": selection["solver"],
        "candidate": body,
        "slot_assignment": slot_assignment,
    }


__all__ = [
    "INDETERMINATE_ACTION",
    "PAIR_CONSTRAINT_HALT",
    "SOURCE_UNIVERSE_SELECTION_VERSION",
    "select_planable_source_universe",
]
