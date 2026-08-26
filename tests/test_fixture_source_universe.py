from __future__ import annotations

import copy
from types import SimpleNamespace

import earcrate.plan.fixture_slot_binding as slot_binding
import earcrate.plan.fixture_source_universe as source_module
from earcrate.plan.fixture_slot_qualification import (
    FixtureSlotQualificationError,
    select_planable_source_universe as public_selector,
)
from earcrate.plan.fixture_source_universe import (
    INDETERMINATE_ACTION,
    PAIR_CONSTRAINT_HALT,
    select_planable_source_universe,
)


A_SOURCES = ["s1", "s2", "s3", "s4", "s5"]
B_SOURCES = ["s6", "s7", "s8", "s9", "s10"]
EXCESS_SOURCE = "s11"
ALL_SOURCES = A_SOURCES + B_SOURCES + [EXCESS_SOURCE]


def _seal_candidate(candidate):
    from earcrate.plan.fixture_diversity import fixture_projection

    candidate["fixture_id"] = "pending"
    candidate["fixture_sha256"] = "pending"
    identity = str(fixture_projection(candidate)["fixture_identity"])
    candidate["fixture_id"] = f"fixture-{identity[:12]}"
    candidate["fixture_sha256"] = identity
    return candidate


def _candidate():
    return _seal_candidate(
        {
            "kind": "earcrate_fixture_candidate",
            "fixture_id": "pending",
            "fixture_sha256": "pending",
            "profile": "girl_talk_v1",
            "persona": "",
            "phrase_playback_law": "proof001_phrase_law",
            "source_pool_sha256": "pool",
            "transform_policy": {
                "unchanged": True,
                "stretch_budget": 8.0,
                "pitch_shift_budget": 2,
            },
            "turnover_policy": {"unchanged": True},
            "transition": {
                "technique": "equal_power",
                "phrase_boundary_required": True,
            },
            # The ordinary TasteSpec law still applies. Its hard minimum is five
            # sources per island, so this fixture deliberately places both
            # islands above that floor rather than weakening the product law.
            "reuse_policy_override": {"source_seconds": 100.0},
            "seed": 19,
            "duration_s": 20.0,
            "islands": [
                {
                    "island_id": "a",
                    "deck_id": "deck-a",
                    "target_bpm": 120.0,
                    "target_key": 0,
                    "capacity_s": 20.0,
                    "allocated_duration_s": 10.0,
                    "source_include_ids": A_SOURCES + [EXCESS_SOURCE],
                    "required_roles": [],
                    "min_sources": 5,
                    "max_sources": 6,
                },
                {
                    "island_id": "b",
                    "deck_id": "deck-b",
                    "target_bpm": 130.0,
                    "target_key": 5,
                    "capacity_s": 20.0,
                    "allocated_duration_s": 10.0,
                    "source_include_ids": B_SOURCES,
                    "required_roles": [],
                    "min_sources": 5,
                    "max_sources": 6,
                },
            ],
            "transitions": [],
        }
    )


def _source_rows():
    return [
        {
            "source_id": source_id,
            "planner_role_capabilities": ["foreground"],
        }
        for source_id in ALL_SOURCES
    ]


def _seal_census(census):
    census["slot_census_sha256"] = slot_binding._census_identity(census)
    return census


def _slot_rows(source_ids):
    return [
        {
            "slot_key": [index * 4, 0],
            "compatible_sources": [source_id],
        }
        for index, source_id in enumerate(source_ids)
    ]


def _campaign(candidate=None):
    candidate = copy.deepcopy(candidate or _candidate())
    rows = [
        _seal_census(
            {
                "island_id": "a",
                "deck_id": "deck-a",
                "render_bpm": 120.0,
                "target_key": 0,
                "allocated_duration_s": 10.0,
                "max_source_events": 2,
                "candidate_required_roles": [],
                "candidate_min_sources": 5,
                "candidate_max_sources": 6,
                "sources": copy.deepcopy(_source_rows()),
                "slots": _slot_rows(A_SOURCES),
            }
        ),
        _seal_census(
            {
                "island_id": "b",
                "deck_id": "deck-b",
                "render_bpm": 130.0,
                "target_key": 5,
                "allocated_duration_s": 10.0,
                "max_source_events": 2,
                "candidate_required_roles": [],
                "candidate_min_sources": 5,
                "candidate_max_sources": 6,
                "sources": copy.deepcopy(_source_rows()),
                "slots": _slot_rows(B_SOURCES),
            }
        ),
    ]
    sources = sorted(
        {
            source_id
            for island in candidate["islands"]
            for source_id in island["source_include_ids"]
        }
    )
    campaign = {
        "kind": "earcrate_fixture_slot_census_campaign",
        "version": slot_binding.SLOT_CENSUS_VERSION,
        "candidate_fixture_sha256": candidate["fixture_sha256"],
        "source_pool_sha256": "pool",
        "source_universe_sha256": slot_binding.semantic_sha256(sources),
        "source_count": len(sources),
        "policy_identity": slot_binding._core._policy_identity(candidate),
        "islands": rows,
        "parent_exact_pool_refusal": {
            "failure_class": "coverage_counting_deficiency",
            "impossibility_claimed": True,
            "proof": {
                "mandatory_source_count": 11,
                "slot_count": 10,
                "deficiency": 1,
            },
            "forbidden_final_pairs": [],
            "learned_pair_constraint_count": 0,
        },
    }
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def _selected_partition(result):
    return {
        row["island_id"]: list(row["source_include_ids"])
        for row in result["candidate"]["islands"]
    }


def test_public_and_direct_imports_share_the_reviewed_selector():
    assert source_module.select_planable_source_universe is public_selector
    assert select_planable_source_universe is public_selector


def test_maximum_source_universe_drops_only_the_unrepresentable_excess():
    result = select_planable_source_universe(_candidate(), _campaign())
    assert result["complete"] is True
    assert result["parent_source_count"] == 11
    assert result["maximum_planable_source_count"] == 10
    assert result["selected_source_count"] == 10
    assert result["dropped_source_count"] == 1
    assert _selected_partition(result) == {
        "a": A_SOURCES,
        "b": B_SOURCES,
    }
    assert result["dropped_source_ids"] == [EXCESS_SOURCE]
    assert len(result["slot_assignment"]) == 10
    assert result["candidate"]["fixture_sha256"] != _candidate()["fixture_sha256"]
    selection = result["candidate"]["fixture_source_universe_selection"]
    assert selection["scope"].endswith("replan_required")
    assert selection["impossibility_claimed"] is False
    assert selection["solver"]["phase_one"]["selected_source_count"] == 10
    assert selection["effective_min_sources_by_island"] == {"a": 5, "b": 5}


def test_selection_is_identical_under_equivalent_census_permutations():
    first = select_planable_source_universe(_candidate(), _campaign())
    permuted = copy.deepcopy(_campaign())
    permuted["islands"].reverse()
    for census in permuted["islands"]:
        census["slots"].reverse()
        census["sources"].reverse()
        for slot in census["slots"]:
            slot["compatible_sources"].reverse()
        _seal_census(census)
    permuted["campaign_sha256"] = slot_binding._campaign_identity(permuted)
    second = select_planable_source_universe(_candidate(), permuted)
    assert first["candidate"] == second["candidate"]
    assert first["slot_assignment"] == second["slot_assignment"]


def test_a_target_above_the_certified_maximum_is_not_an_impossibility_claim():
    result = select_planable_source_universe(
        _candidate(), _campaign(), target_source_count=11
    )
    assert result["complete"] is False
    assert result["failure_class"] == "target_exceeds_solver_certified_maximum"
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == INDETERMINATE_ACTION
    assert result["solver"]["phase_one"]["selected_source_count"] == 10


def test_parent_atom_pair_constraints_stop_source_only_selection():
    campaign = _campaign()
    campaign["parent_exact_pool_refusal"]["failure_class"] = (
        "section_pair_compatibility"
    )
    campaign["parent_exact_pool_refusal"]["forbidden_final_pairs"] = [
        {"left_atom": "a", "right_atom": "b"}
    ]
    campaign["parent_exact_pool_refusal"]["learned_pair_constraint_count"] = 1
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    result = select_planable_source_universe(_candidate(), campaign)
    assert result["complete"] is False
    assert result["failure_class"] == "parent_pair_constraints_not_encoded"
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_positive_pair_count_without_records_fails_closed():
    campaign = _campaign()
    campaign["parent_exact_pool_refusal"]["forbidden_final_pairs"] = []
    campaign["parent_exact_pool_refusal"]["learned_pair_constraint_count"] = 1
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    result = select_planable_source_universe(_candidate(), campaign)
    assert result["complete"] is False
    assert result["failure_class"] == "parent_pair_constraint_receipt_inconsistent"
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_pair_count_and_record_count_must_agree():
    campaign = _campaign()
    campaign["parent_exact_pool_refusal"]["forbidden_final_pairs"] = [
        {"left_atom": "a", "right_atom": "b"}
    ]
    campaign["parent_exact_pool_refusal"]["learned_pair_constraint_count"] = 2
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    result = select_planable_source_universe(_candidate(), campaign)
    assert result["complete"] is False
    assert result["failure_class"] == "parent_pair_constraint_receipt_inconsistent"
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_reuse_policy_drift_is_refused_before_selection():
    candidate = _candidate()
    campaign = _campaign(candidate)
    drifted = copy.deepcopy(candidate)
    drifted["reuse_policy_override"] = {"source_seconds": 50.0}

    from earcrate.plan.fixture_diversity import fixture_projection

    assert (
        fixture_projection(drifted)["fixture_identity"]
        == candidate["fixture_sha256"]
    )
    try:
        select_planable_source_universe(drifted, campaign)
    except FixtureSlotQualificationError as exc:
        assert "policy identity" in str(exc)
    else:
        raise AssertionError("turnover-policy drift reused an old census")


def test_required_role_and_effective_turnover_bounds_remain_solver_laws():
    candidate = _candidate()
    candidate["islands"][0]["required_roles"] = ["spark"]
    _seal_candidate(candidate)
    campaign = _campaign(candidate)
    campaign["islands"][0]["candidate_required_roles"] = ["spark"]
    for source in campaign["islands"][0]["sources"]:
        source["planner_role_capabilities"] = ["foreground"]
    _seal_census(campaign["islands"][0])
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    result = select_planable_source_universe(candidate, campaign)
    assert result["complete"] is False
    assert result["failure_class"] == "required_role_capacity"
    assert result["impossibility_claimed"] is True


def test_solver_bound_stops_without_claiming_the_parent_universe_is_impossible():
    def bounded_solver(**_kwargs):
        return SimpleNamespace(
            status=1,
            success=False,
            message="time limit",
            fun=None,
            x=None,
        )

    result = select_planable_source_universe(
        _candidate(), _campaign(), _solver=bounded_solver
    )
    assert result["complete"] is False
    assert result["failure_class"] == "solver_bound_or_failure"
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == INDETERMINATE_ACTION
