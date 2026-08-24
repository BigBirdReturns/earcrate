from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from test_fixture_slot_qualification import (
    _candidate,
    _census_campaign,
    _seal_census,
)


def test_required_role_needs_an_observed_slot_family():
    candidate = _candidate()
    candidate["islands"][0]["required_roles"] = ["bass"]
    campaign = copy.deepcopy(_census_campaign())
    campaign["islands"][0]["candidate_required_roles"] = ["bass"]
    for index, slot in enumerate(campaign["islands"][0]["slots"]):
        slot["role_family"] = "floor" if index == 0 else "foreground"
    _seal_census(campaign["islands"][0])
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)

    result = qualify_fixture_candidate(candidate, campaign)
    assert result["complete"] is False
    assert result["impossibility_claimed"] is True
    assert result["failure_class"] == "required_role_slot_capacity"
    assert result["proof"]["required_role"] == "bass"


def test_required_role_slot_guard_is_order_independent():
    candidate = _candidate()
    candidate["islands"][0]["required_roles"] = ["bass"]
    campaign = copy.deepcopy(_census_campaign())
    campaign["islands"][0]["candidate_required_roles"] = ["bass"]
    for index, slot in enumerate(campaign["islands"][0]["slots"]):
        slot["role_family"] = "floor" if index == 0 else "foreground"
    campaign["islands"].reverse()
    for census in campaign["islands"]:
        census["slots"].reverse()
        _seal_census(census)
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)

    result = qualify_fixture_candidate(candidate, campaign)
    assert result["failure_class"] == "required_role_slot_capacity"
    assert result["proof"]["observed_role_families"] == [
        "floor",
        "foreground",
    ]
