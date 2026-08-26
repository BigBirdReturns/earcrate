from __future__ import annotations

import earcrate.plan.fixture_slot_binding as slot_binding
import earcrate.plan.fixture_source_universe as source_module
from earcrate.plan.fixture_slot_qualification import (
    FixtureSlotQualificationError,
    SOURCE_UNIVERSE_PAIR_CONSTRAINT_HALT,
    select_planable_source_universe,
)
import test_fixture_source_universe as helpers


def _reseal(campaign):
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def _solver_must_not_run(**_kwargs):
    raise AssertionError("source-universe MILP ran past the final contract")


def test_pair_state_precedes_proof_bearing_status():
    cases = []

    positive = helpers._campaign()
    positive["parent_exact_pool_refusal"]["impossibility_claimed"] = False
    positive["parent_exact_pool_refusal"]["forbidden_final_pairs"] = [
        {"left": "a", "right": "b"}
    ]
    positive["parent_exact_pool_refusal"]["learned_pair_constraint_count"] = 1
    cases.append((_reseal(positive), "parent_pair_constraints_not_encoded"))

    missing = helpers._campaign()
    missing["parent_exact_pool_refusal"]["impossibility_claimed"] = False
    missing["parent_exact_pool_refusal"].pop("forbidden_final_pairs")
    cases.append(
        (_reseal(missing), "parent_pair_constraint_receipt_inconsistent")
    )

    malformed = helpers._campaign()
    malformed["parent_exact_pool_refusal"]["impossibility_claimed"] = False
    malformed["parent_exact_pool_refusal"][
        "learned_pair_constraint_count"
    ] = 0.5
    cases.append(
        (_reseal(malformed), "parent_pair_constraint_receipt_inconsistent")
    )

    section_pair = helpers._campaign()
    section_pair["parent_exact_pool_refusal"]["impossibility_claimed"] = False
    section_pair["parent_exact_pool_refusal"][
        "failure_class"
    ] = "section_pair_compatibility"
    cases.append(
        (_reseal(section_pair), "parent_pair_constraints_not_encoded")
    )

    for campaign, expected in cases:
        result = select_planable_source_universe(
            helpers._candidate(),
            campaign,
            _solver=_solver_must_not_run,
        )
        assert result["complete"] is False
        assert result["failure_class"] == expected
        assert result["impossibility_claimed"] is False
        assert result["private_acceptance"] == (
            SOURCE_UNIVERSE_PAIR_CONSTRAINT_HALT
        )


def test_exact_source_count_requires_a_json_integer_before_solver():
    for invalid in (True, False, 10.9, "10"):
        try:
            select_planable_source_universe(
                helpers._candidate(),
                helpers._campaign(),
                target_source_count=invalid,
                _solver=_solver_must_not_run,
            )
        except FixtureSlotQualificationError as error:
            assert "JSON integer" in str(error)
        else:
            raise AssertionError(
                f"invalid exact source count reached the solver: {invalid!r}"
            )


def test_public_and_direct_imports_share_the_final_selector():
    assert source_module.select_planable_source_universe is (
        select_planable_source_universe
    )
