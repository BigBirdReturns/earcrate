from __future__ import annotations

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan import fixture_slot_solver as direct_solver
from earcrate.plan.fixture_slot_qualification import (
    PAIR_CONSTRAINT_HALT,
    install_fixture_slot_census,
    qualify_fixture_candidate,
)
from test_fixture_slot_qualification import _candidate
from test_fixture_slot_review_closure import (
    _campaign_with_parent_pair_constraint,
)


def test_direct_solver_import_has_no_pair_constraint_bypass():
    assert direct_solver.qualify_fixture_candidate is qualify_fixture_candidate
    result = direct_solver.qualify_fixture_candidate(
        _candidate(), _campaign_with_parent_pair_constraint()
    )
    assert result["complete"] is False
    assert result["impossibility_claimed"] is False
    assert result["failure_class"] == "parent_pair_constraints_not_encoded"
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT


def test_nested_uncapped_proposal_shadows_outer_cap_context():
    class Core:
        def __init__(self):
            self.caps = []

        def compose_taste_arrangement(self, _pool, params, _seed):
            self.caps.append(params.get("exact_pool_max_source_events"))
            return {"params": dict(params)}

        def propose_island_set(self, params):
            if params.get("inner"):
                self.compose_taste_arrangement([], {}, 2)
                return {"ok": True, "inner": True}
            inner = self.propose_island_set({"inner": True})
            self.compose_taste_arrangement([], {}, 1)
            return {"ok": True, "inner_result": inner}

    original_installer = slot_binding.install_fixture_slot_census
    slot_binding.install_fixture_slot_census = lambda core_class: core_class
    try:
        install_fixture_slot_census(Core)
        core = Core()
        result = core.propose_island_set(
            {"exact_pool_max_source_events": 2}
        )
        assert result["ok"] is True
        assert result["inner_result"]["inner"] is True
        assert core.caps == [None, 2]
    finally:
        slot_binding.install_fixture_slot_census = original_installer
