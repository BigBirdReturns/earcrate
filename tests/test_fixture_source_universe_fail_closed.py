from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import (
    FixtureSlotQualificationError,
    select_planable_source_universe,
)
from earcrate.plan.fixture_source_universe import PAIR_CONSTRAINT_HALT
import test_fixture_source_universe as helpers


def _solver_must_not_run(**_kwargs):
    raise AssertionError("source-only MILP ran past a fail-closed boundary")


def _reseal_campaign(campaign):
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def _assert_policy_drift_refused(candidate, campaign, drifted):
    from earcrate.plan.fixture_diversity import fixture_projection

    assert (
        fixture_projection(drifted)["fixture_identity"]
        == candidate["fixture_sha256"]
    )
    try:
        select_planable_source_universe(
            drifted,
            campaign,
            _solver=_solver_must_not_run,
        )
    except FixtureSlotQualificationError as exc:
        assert "policy identity" in str(exc)
    else:
        raise AssertionError("a census-composition input drift reused stale evidence")


def test_seed_duration_exclusions_and_capacity_are_bound_to_census_policy():
    candidate = helpers._candidate()
    campaign = helpers._campaign(candidate)

    changed_seed = copy.deepcopy(candidate)
    changed_seed["seed"] = 999

    changed_duration = copy.deepcopy(candidate)
    changed_duration["duration_s"] = 21.0

    changed_exclusions = copy.deepcopy(candidate)
    changed_exclusions["source_exclude_ids"] = ["opaque-excluded-source"]

    changed_capacity = copy.deepcopy(candidate)
    changed_capacity["islands"][0]["capacity_s"] = 40.0

    for drifted in (
        changed_seed,
        changed_duration,
        changed_exclusions,
        changed_capacity,
    ):
        _assert_policy_drift_refused(candidate, campaign, drifted)


def test_semantically_equivalent_island_reordering_cannot_reuse_a_census():
    candidate = helpers._candidate()
    candidate["islands"][0].update({"start_s": 0.0, "end_s": 10.0})
    candidate["islands"][1].update({"start_s": 10.0, "end_s": 20.0})
    helpers._seal_candidate(candidate)
    campaign = helpers._campaign(candidate)

    reordered = copy.deepcopy(candidate)
    reordered["islands"].reverse()
    _assert_policy_drift_refused(candidate, campaign, reordered)


def test_fractional_pair_count_is_rejected_before_solver():
    campaign = helpers._campaign()
    campaign["parent_exact_pool_refusal"][
        "learned_pair_constraint_count"
    ] = 0.5
    _reseal_campaign(campaign)

    result = select_planable_source_universe(
        helpers._candidate(),
        campaign,
        _solver=_solver_must_not_run,
    )
    assert result["complete"] is False
    assert result["failure_class"] == (
        "parent_pair_constraint_receipt_inconsistent"
    )
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_missing_and_nonobject_parent_refusals_halt_before_solver():
    variants = []
    missing = helpers._campaign()
    missing.pop("parent_exact_pool_refusal")
    variants.append(_reseal_campaign(missing))

    nonobject = helpers._campaign()
    nonobject["parent_exact_pool_refusal"] = "not-a-refusal-object"
    variants.append(_reseal_campaign(nonobject))

    for campaign in variants:
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == (
            "parent_exact_pool_refusal_missing_or_malformed"
        )
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_parent_refusal_must_be_complete_and_proof_bearing():
    variants = []
    not_proof = helpers._campaign()
    not_proof["parent_exact_pool_refusal"]["impossibility_claimed"] = False
    variants.append(_reseal_campaign(not_proof))

    unnamed = helpers._campaign()
    unnamed["parent_exact_pool_refusal"]["failure_class"] = ""
    variants.append(_reseal_campaign(unnamed))

    for campaign in variants:
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == (
            "parent_exact_pool_refusal_not_proof_bearing"
        )
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_parent_pair_fields_may_not_be_omitted():
    variants = []
    missing_pairs = helpers._campaign()
    missing_pairs["parent_exact_pool_refusal"].pop(
        "forbidden_final_pairs"
    )
    variants.append(_reseal_campaign(missing_pairs))

    missing_count = helpers._campaign()
    missing_count["parent_exact_pool_refusal"].pop(
        "learned_pair_constraint_count"
    )
    variants.append(_reseal_campaign(missing_count))

    for campaign in variants:
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == (
            "parent_pair_constraint_receipt_inconsistent"
        )
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT
