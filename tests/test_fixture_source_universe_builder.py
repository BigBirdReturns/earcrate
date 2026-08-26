from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_source_universe_review_closure import (
    SOURCE_UNIVERSE_POLICY_FIELD,
    SOURCE_UNIVERSE_SLOT_CENSUS_VERSION,
    source_universe_policy_identity,
)
import test_fixture_slot_census_diagnostic as diagnostic


def test_fresh_builder_emits_v3_stage2d_custody_without_redefining_legacy_policy():
    pool = diagnostic._pool()
    Core = diagnostic._census_core_class()
    Core.calls = []
    Core.turnover_error = None
    candidate = diagnostic._candidate(pool)
    request = diagnostic._request(candidate)

    legacy_policy = slot_binding._core._policy_identity(request)
    stage2d_policy = source_universe_policy_identity(request)
    drifted = copy.deepcopy(request)
    drifted["seed"] = int(request["seed"]) + 1

    # PR #127 keeps its historical policy identity. Stage 2D binds the wider
    # composition request separately rather than changing inherited semantics.
    assert slot_binding._core._policy_identity(drifted) == legacy_policy
    assert source_universe_policy_identity(drifted) != stage2d_policy

    campaign = slot_binding.build_fixture_slot_census_campaign(
        Core(pool), request
    )
    assert campaign["version"] == SOURCE_UNIVERSE_SLOT_CENSUS_VERSION
    assert campaign["policy_identity"] == legacy_policy
    assert campaign[SOURCE_UNIVERSE_POLICY_FIELD] == stage2d_policy
    assert campaign["campaign_sha256"] == slot_binding._campaign_identity(
        campaign
    )
    assert campaign["islands"]
    for island in campaign["islands"]:
        assert island["version"] == SOURCE_UNIVERSE_SLOT_CENSUS_VERSION
        assert island[SOURCE_UNIVERSE_POLICY_FIELD] == stage2d_policy
        assert island["slot_census_sha256"] == slot_binding._census_identity(
            island
        )
