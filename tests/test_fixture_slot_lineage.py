from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from test_fixture_slot_qualification import _candidate, _census_campaign


def test_unchanged_qualified_identity_halts_instead_of_replanning_same_request():
    first = qualify_fixture_candidate(_candidate(), _census_campaign())
    assert first["complete"] is True
    candidate = first["candidate"]
    campaign = copy.deepcopy(_census_campaign())
    campaign["candidate_fixture_sha256"] = candidate["fixture_sha256"]
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)

    result = qualify_fixture_candidate(candidate, campaign)
    assert result["complete"] is False
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == slot_binding.INDETERMINATE_ACTION
    assert result["failure_class"] == "qualification_no_structural_change"
    assert result["parent_fixture_identity"] == result[
        "qualified_fixture_identity"
    ]


def test_qualification_refuses_a_semantic_identity_cycle():
    candidate = _candidate()
    probe = qualify_fixture_candidate(candidate, _census_campaign())
    assert probe["complete"] is True
    revisited = str(probe["qualified_fixture_identity"])
    cycled = copy.deepcopy(candidate)
    cycled["fixture_slot_qualification"] = {
        "lineage_fixture_identities": [revisited],
        "parent_fixture_identity": "older-parent",
    }

    result = qualify_fixture_candidate(cycled, _census_campaign())
    assert result["complete"] is False
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == slot_binding.INDETERMINATE_ACTION
    assert result["failure_class"] == "qualification_identity_cycle"
    assert result["qualified_fixture_identity"] == revisited


def test_successful_round_carries_its_semantic_lineage_without_changing_identity():
    candidate = _candidate()
    result = qualify_fixture_candidate(candidate, _census_campaign())
    assert result["complete"] is True
    output = result["candidate"]
    ledger = output["fixture_slot_qualification"]
    assert ledger["lineage_fixture_identities"] == [
        candidate["fixture_sha256"]
    ]
    assert ledger["qualification_round"] == 1
    assert output["fixture_sha256"] == result[
        "qualified_fixture_identity"
    ]
