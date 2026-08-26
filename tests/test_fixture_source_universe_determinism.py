from __future__ import annotations

import copy

import numpy as np
from scipy.optimize import milp

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import (
    select_planable_source_universe,
)
from earcrate.plan.fixture_source_universe import PAIR_CONSTRAINT_HALT
import test_fixture_source_universe as helpers


def _solver_must_not_run(**_kwargs):
    raise AssertionError("source-universe MILP ran past an invalid proof schema")


def _reseal_campaign(campaign):
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def test_unrecognized_nonempty_proof_mappings_halt_before_solver():
    invalid_proofs = [
        {"unrelated": True},
        {"kind": ""},
        {"kind": "unknown", "payload": {"value": 1}},
        {"kind": "mapping_proof", "payload": {}},
        {"kind": "hall_witness", "witness": {}},
        {"kind": "producer_proof_statement", "statement": "   "},
    ]
    for proof in invalid_proofs:
        campaign = helpers._campaign()
        campaign["parent_exact_pool_refusal"]["proof"] = proof
        _reseal_campaign(campaign)
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == (
            "parent_exact_pool_refusal_proof_missing_or_malformed"
        )
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def _symmetric_campaign():
    candidate = helpers._candidate()
    campaign = helpers._campaign(candidate)
    island_a = next(
        row for row in campaign["islands"] if row["island_id"] == "a"
    )
    island_a["slots"] = [
        {"slot_key": [0, 0], "compatible_sources": ["s1", "s2"]},
        {"slot_key": [4, 0], "compatible_sources": ["s1", "s2"]},
        {"slot_key": [8, 0], "compatible_sources": ["s3"]},
        {"slot_key": [12, 0], "compatible_sources": ["s4"]},
        {"slot_key": [16, 0], "compatible_sources": ["s5"]},
    ]
    helpers._seal_census(island_a)
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return candidate, campaign


def _variable_indices(candidate, campaign):
    sources = sorted(
        {
            source_id
            for island in candidate["islands"]
            for source_id in island["source_include_ids"]
        }
    )
    candidate_ids = [str(row["island_id"]) for row in candidate["islands"]]
    slots = {}
    source_slots = {source_id: [] for source_id in sources}
    for census in campaign["islands"]:
        island_id = str(census["island_id"])
        for raw_slot in census["slots"]:
            slot = (
                island_id,
                int(raw_slot["slot_key"][0]),
                int(raw_slot["slot_key"][1]),
            )
            compatible = sorted(
                set(raw_slot["compatible_sources"]).intersection(sources)
            )
            slots[slot] = compatible
            for source_id in compatible:
                source_slots[source_id].append(slot)

    column = 0
    for source_id in sources:
        reachable = {slot[0] for slot in source_slots[source_id]}
        for island_id in candidate_ids:
            if island_id in reachable:
                column += 1
    y_index = {}
    for slot in sorted(slots):
        for source_id in slots[slot]:
            y_index[(slot, source_id)] = column
            column += 1
    return y_index


def test_equal_cost_slot_swaps_cannot_move_assignment_or_fixture_identity():
    candidate, campaign = _symmetric_campaign()
    baseline = select_planable_source_universe(candidate, campaign)
    assert baseline["complete"] is True

    y_index = _variable_indices(candidate, campaign)
    slot_zero = ("a", 0, 0)
    slot_four = ("a", 4, 0)
    swap_columns = [
        y_index[(slot_zero, "s1")],
        y_index[(slot_zero, "s2")],
        y_index[(slot_four, "s1")],
        y_index[(slot_four, "s2")],
    ]
    calls = {"count": 0}

    def solver_with_alternate_equal_cost_assignment(**kwargs):
        result = milp(**kwargs)
        calls["count"] += 1
        if calls["count"] == 2 and result.x is not None:
            values = np.asarray(result.x, dtype=np.float64).copy()
            a, b, c, d = swap_columns
            values[a], values[b], values[c], values[d] = (
                values[b],
                values[a],
                values[d],
                values[c],
            )
            result.x = values
        return result

    alternate = select_planable_source_universe(
        candidate,
        campaign,
        _solver=solver_with_alternate_equal_cost_assignment,
    )
    assert calls["count"] == 2
    assert alternate["complete"] is True
    assert alternate["slot_assignment"] == baseline["slot_assignment"]
    assert alternate["candidate"] == baseline["candidate"]
    assert alternate["selected_fixture_identity"] == (
        baseline["selected_fixture_identity"]
    )

    first_two = [
        row
        for row in baseline["slot_assignment"]
        if row["island_id"] == "a" and row["bar_start"] in (0, 4)
    ]
    assert first_two == [
        {
            "island_id": "a",
            "bar_start": 0,
            "layer_index": 0,
            "source_id": "s1",
        },
        {
            "island_id": "a",
            "bar_start": 4,
            "layer_index": 0,
            "source_id": "s2",
        },
    ]
    selection = baseline["candidate"]["fixture_source_universe_selection"]
    canonical = selection["slot_assignment_canonicalization"]
    assert canonical["numeric_semantics"] == (
        "exact_integer_flow_no_floating_tie_break"
    )
    assert canonical["method"].startswith("lexicographically_smallest")
