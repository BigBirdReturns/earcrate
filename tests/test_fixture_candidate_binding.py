from __future__ import annotations

import hashlib
import json

from earcrate.plan import plan_island_set
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from test_fixture_slot_qualification import _matrix, _repair_fixture
from test_island_set import _fixture


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_direct_fixture_binding_is_returned_without_moving_arrangement_bytes():
    control_core, control_params = _fixture()
    control = plan_island_set(control_core, control_params)

    bound_core, bound_params = _fixture()
    bound_params["fixture_sha256"] = "fixture-authority-abc"
    bound_params["slot_qualification"] = {
        "version": "earcrate_fixture_slot_qualification_v1",
        "parent_fixture_sha256": "parent-authority",
    }
    result = plan_island_set(bound_core, bound_params)

    assert result["candidate_fixture_sha256"] == "fixture-authority-abc"
    assert result["candidate_source_pool_sha256"] == bound_params[
        "source_pool_sha256"
    ]
    assert result["slot_qualification_sha256"] == _digest(
        bound_params["slot_qualification"]
    )
    assert result["arrangement"] == control["arrangement"]
    assert result["arrangement_sha256"] == control["arrangement_sha256"]
    assert "candidate_fixture_sha256" not in result["arrangement"]


def test_legacy_plan_result_has_no_fixture_binding_fields():
    core, params = _fixture()
    result = plan_island_set(core, params)
    assert "candidate_fixture_sha256" not in result
    assert "candidate_source_pool_sha256" not in result
    assert "slot_qualification_sha256" not in result


def test_cli_candidate_file_metadata_is_not_slot_identity():
    decks, candidate, census = _repair_fixture()
    census["candidate_file"] = {
        "path": "S:/operational/location/candidate.json",
        "file_sha256": "f" * 64,
    }
    result = qualify_fixture_candidate(
        _matrix(decks), candidate, census, max_source_events=3
    )
    assert result["complete"] is True
    assert result["ignored_operational_census_fields"] == ["candidate_file"]
