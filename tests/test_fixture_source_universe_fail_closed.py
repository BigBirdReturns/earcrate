from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

import earcrate.plan.fixture_slot_binding as slot_binding
import earcrate.plan.fixture_slot_review_closure as slot_review
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


def test_parent_refusal_requires_nonempty_mapping_proof_before_solver():
    variants = []

    missing = helpers._campaign()
    missing["parent_exact_pool_refusal"].pop("proof")
    variants.append(_reseal_campaign(missing))

    for value in (None, {}, "not-proof", [], [1]):
        malformed = helpers._campaign()
        malformed["parent_exact_pool_refusal"]["proof"] = value
        variants.append(_reseal_campaign(malformed))

    for campaign in variants:
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


class _ProductRefusal(RuntimeError):
    def __init__(self, deficiency: Mapping[str, Any]):
        super().__init__("synthetic product refusal")
        self.deficiency = copy.deepcopy(dict(deficiency))


def _project_through_real_refusal_binding(
    deficiency: Mapping[str, Any],
) -> Dict[str, Any]:
    class ProductCore:
        def propose_island_set(self, _params):
            raise _ProductRefusal(deficiency)

    slot_review.install_fixture_slot_review_closure(ProductCore)
    try:
        ProductCore().propose_island_set(
            {"exact_pool_max_source_events": 12}
        )
    except _ProductRefusal as error:
        campaign = error.deficiency["fixture_slot_census_campaign"]
        return dict(campaign["parent_exact_pool_refusal"])
    raise AssertionError("product refusal was not raised")


def _producer_deficiency(**evidence: Any) -> Dict[str, Any]:
    return {
        "failure_class": evidence.pop("failure_class", "role_capacity"),
        "impossibility_claimed": True,
        "forbidden_final_pairs": [],
        "learned_pair_constraint_count": 0,
        "fixture_slot_census_campaign": {
            "kind": "earcrate_fixture_slot_census_campaign",
            "version": slot_binding.SLOT_CENSUS_VERSION,
            "candidate_fixture_sha256": "fixture",
            "source_pool_sha256": "pool",
            "source_universe_sha256": "universe",
            "source_count": 1,
            "policy_identity": "policy",
            "islands": [],
            "impossibility_claimed": False,
        },
        **evidence,
    }


def test_real_refusal_binding_normalizes_hall_and_scalar_proofs():
    hall = {
        "deficient_sources": ["bass-a", "bass-b"],
        "reachable_slot_count": 1,
        "deficiency": 1,
    }
    cases = [
        (
            _producer_deficiency(hall_witness=hall),
            {
                "kind": "hall_witness",
                "witness": hall,
            },
        ),
        (
            _producer_deficiency(
                failure_class="cap_constraint",
                proof="exhausted alternating exchange under the event cap",
            ),
            {
                "kind": "producer_proof_statement",
                "statement": (
                    "exhausted alternating exchange under the event cap"
                ),
            },
        ),
        (
            _producer_deficiency(
                failure_class="coverage_counting_deficiency",
                proof="total source capacity is below the observed slot count",
            ),
            {
                "kind": "producer_proof_statement",
                "statement": (
                    "total source capacity is below the observed slot count"
                ),
            },
        ),
        (
            _producer_deficiency(
                failure_class="space_exhausted",
                proof="complete assignment space exhausted",
            ),
            {
                "kind": "producer_proof_statement",
                "statement": "complete assignment space exhausted",
            },
        ),
    ]

    for deficiency, expected in cases:
        parent = _project_through_real_refusal_binding(deficiency)
        assert parent["proof"] == expected
        assert parent["impossibility_claimed"] is True
        assert parent["learned_pair_constraint_count"] == 0
        assert parent["forbidden_final_pairs"] == []
        assert parent["parent_refusal_sha256"] == slot_binding.semantic_sha256(
            {
                key: value
                for key, value in parent.items()
                if key != "parent_refusal_sha256"
            }
        )


def test_pair_receipt_classification_precedes_missing_proof():
    positive = helpers._campaign()
    positive_parent = positive["parent_exact_pool_refusal"]
    positive_parent.pop("proof")
    positive_parent["forbidden_final_pairs"] = [
        {
            "left_atom_id": "a",
            "right_atom_id": "b",
        }
    ]
    positive_parent["learned_pair_constraint_count"] = 1
    _reseal_campaign(positive)

    inconsistent = helpers._campaign()
    inconsistent_parent = inconsistent["parent_exact_pool_refusal"]
    inconsistent_parent.pop("proof")
    inconsistent_parent["forbidden_final_pairs"] = []
    inconsistent_parent["learned_pair_constraint_count"] = 1
    _reseal_campaign(inconsistent)

    section_pair = helpers._campaign()
    section_parent = section_pair["parent_exact_pool_refusal"]
    section_parent.pop("proof")
    section_parent["failure_class"] = "section_pair_compatibility"
    section_parent["forbidden_final_pairs"] = []
    section_parent["learned_pair_constraint_count"] = 0
    _reseal_campaign(section_pair)

    cases = [
        (positive, "parent_pair_constraints_not_encoded"),
        (inconsistent, "parent_pair_constraint_receipt_inconsistent"),
        (section_pair, "parent_pair_constraints_not_encoded"),
    ]
    for campaign, expected_class in cases:
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == expected_class
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT
