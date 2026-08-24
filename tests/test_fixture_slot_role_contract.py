from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from test_fixture_slot_qualification import (
    _candidate,
    _census_campaign,
    _seal_census,
)


def _capability_without_matching_slot_family():
    candidate = _candidate()
    candidate["islands"][0]["required_roles"] = ["bass"]
    campaign = copy.deepcopy(_census_campaign())
    campaign["islands"][0]["candidate_required_roles"] = ["bass"]
    for index, slot in enumerate(campaign["islands"][0]["slots"]):
        slot["role_family"] = "floor" if index == 0 else "foreground"
    for source in campaign["islands"][0]["sources"]:
        if source["source_id"] == "floor":
            source["planner_role_capabilities"] = ["bass", "floor"]
    _seal_census(campaign["islands"][0])
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return candidate, campaign


def test_required_role_is_a_pool_capability_not_a_slot_label():
    candidate, campaign = _capability_without_matching_slot_family()
    result = qualify_fixture_candidate(candidate, campaign)
    assert result["complete"] is True
    assert {
        str(row.get("role_family") or "")
        for row in campaign["islands"][0]["slots"]
    } == {"floor", "foreground"}
    first_island = next(
        row
        for row in result["candidate"]["islands"]
        if row["island_id"] == "a"
    )
    assert "floor" in first_island["source_include_ids"]


def test_required_role_capability_semantics_are_order_independent():
    candidate, campaign = _capability_without_matching_slot_family()
    first = qualify_fixture_candidate(candidate, campaign)
    permuted = copy.deepcopy(campaign)
    permuted["islands"].reverse()
    for census in permuted["islands"]:
        census["slots"].reverse()
        census["sources"].reverse()
        for slot in census["slots"]:
            slot["compatible_sources"].reverse()
        _seal_census(census)
    permuted["campaign_sha256"] = slot_binding._campaign_identity(permuted)
    second = qualify_fixture_candidate(candidate, permuted)
    assert first["candidate"] == second["candidate"]
    assert first["slot_assignment"] == second["slot_assignment"]
