from __future__ import annotations

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_review_closure import (
    install_fixture_slot_census,
)


def test_refined_census_uses_the_real_four_argument_publisher_contract():
    class Refusal(RuntimeError):
        def __init__(self):
            super().__init__("refused")
            self.deficiency = {
                "failure_class": "cap_constraint",
                "impossibility_claimed": True,
                "fixture_slot_census_run_id": "old-census-run",
                "fixture_slot_census_campaign": {
                    "kind": "earcrate_fixture_slot_census_campaign",
                    "candidate_fixture_sha256": "candidate",
                    "policy_identity": "policy",
                    "source_pool_sha256": "pool",
                    "islands": [],
                    "campaign_sha256": "unbound",
                },
            }

    class Core:
        def propose_island_set(self, _params):
            raise Refusal()

    published = []
    original_installer = slot_binding.install_fixture_slot_census
    original_publisher = slot_binding._publish_census_run
    slot_binding.install_fixture_slot_census = lambda core_class: core_class

    def publish(core, request, deficiency, census):
        published.append((core, dict(request), dict(census)))
        deficiency["fixture_slot_census_run_id"] = "new-census-run"

    slot_binding._publish_census_run = publish
    try:
        install_fixture_slot_census(Core)
        try:
            Core().propose_island_set({"fixture_sha256": "candidate"})
        except Refusal as exc:
            assert len(published) == 1
            assert exc.deficiency["fixture_slot_census_run_id"] == (
                "new-census-run"
            )
            assert exc.deficiency[
                "fixture_slot_census_supersedes_run_id"
            ] == "old-census-run"
            assert "fixture_slot_census_receipt_failure" not in (
                exc.deficiency
            )
            parent = exc.deficiency[
                "fixture_slot_census_campaign"
            ]["parent_exact_pool_refusal"]
            assert parent["failure_class"] == "cap_constraint"
            assert parent["impossibility_claimed"] is True
        else:
            raise AssertionError("the exact-pool refusal was not preserved")
    finally:
        slot_binding.install_fixture_slot_census = original_installer
        slot_binding._publish_census_run = original_publisher
