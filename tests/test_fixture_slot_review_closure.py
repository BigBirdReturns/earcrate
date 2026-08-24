from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import (
    PAIR_CONSTRAINT_HALT,
    install_fixture_slot_census,
    qualify_fixture_candidate,
)
from test_fixture_slot_qualification import (
    _candidate,
    _census_campaign,
)


def _campaign_with_parent_pair_constraint():
    campaign = copy.deepcopy(_census_campaign())
    campaign["parent_exact_pool_refusal"] = {
        "failure_class": "section_pair_compatibility",
        "impossibility_claimed": True,
        "learned_pair_constraint_count": 1,
        "forbidden_final_pairs": [
            {
                "bar_start": 0,
                "layer_index": 0,
                "atom": "atom-left",
                "counterpart_layer_index": 1,
                "counterpart_atom": "atom-right",
            }
        ],
        "parent_refusal_sha256": "parent-proof",
    }
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def test_parent_atom_pair_constraints_stop_source_only_qualification():
    result = qualify_fixture_candidate(
        _candidate(), _campaign_with_parent_pair_constraint()
    )
    assert result["complete"] is False
    assert result["impossibility_claimed"] is False
    assert result["failure_class"] == "parent_pair_constraints_not_encoded"
    assert result["private_acceptance"] == PAIR_CONSTRAINT_HALT
    assert result["parent_exact_pool_refusal"][
        "learned_pair_constraint_count"
    ] == 1


def test_request_cap_reaches_the_exact_island_composer_and_is_restored():
    class Refusal(RuntimeError):
        def __init__(self):
            super().__init__("refused")
            self.deficiency = {
                "failure_class": "cap_constraint",
                "impossibility_claimed": True,
            }

    class Core:
        def __init__(self):
            self.caps = []

        def compose_taste_arrangement(self, _pool, params, _seed):
            self.caps.append(params.get("exact_pool_max_source_events"))
            return {"params": dict(params)}

        def propose_island_set(self, params):
            self.compose_taste_arrangement([], {}, 1)
            if params.get("refuse"):
                raise Refusal()
            return {"ok": True}

    original_builder = slot_binding.build_fixture_slot_census_campaign
    original_publisher = slot_binding._publish_census_run
    slot_binding.build_fixture_slot_census_campaign = lambda _self, params: {
        "kind": "earcrate_fixture_slot_census_campaign",
        "candidate_fixture_sha256": "candidate",
        "policy_identity": "policy",
        "source_pool_sha256": "pool",
        "islands": [],
        "campaign_sha256": "campaign",
        "max_source_events_seen": params.get(
            "exact_pool_max_source_events"
        ),
    }
    slot_binding._publish_census_run = lambda *_args, **_kwargs: None
    try:
        install_fixture_slot_census(Core)
        core = Core()
        assert core.propose_island_set(
            {"exact_pool_max_source_events": 2}
        ) == {"ok": True}
        assert core.caps == [2]
        assert not hasattr(
            core, "_fixture_slot_exact_pool_max_source_events"
        )
    finally:
        slot_binding.build_fixture_slot_census_campaign = original_builder
        slot_binding._publish_census_run = original_publisher


def test_parent_refusal_is_bound_into_the_census_campaign():
    class Refusal(RuntimeError):
        def __init__(self):
            super().__init__("refused")
            self.deficiency = {
                "failure_class": "section_pair_compatibility",
                "impossibility_claimed": True,
                "forbidden_final_pairs": [
                    {
                        "bar_start": 4,
                        "layer_index": 0,
                        "atom": "a",
                        "counterpart_layer_index": 1,
                        "counterpart_atom": "b",
                    }
                ],
            }

    class Core:
        def compose_taste_arrangement(self, _pool, _params, _seed):
            return {}

        def propose_island_set(self, _params):
            error = Refusal()
            error.deficiency["fixture_slot_census_campaign"] = {
                "kind": "earcrate_fixture_slot_census_campaign",
                "candidate_fixture_sha256": "candidate",
                "policy_identity": "policy",
                "source_pool_sha256": "pool",
                "islands": [],
            }
            raise error

    original_installer = slot_binding.install_fixture_slot_census
    original_publisher = slot_binding._publish_census_run
    slot_binding.install_fixture_slot_census = lambda core_class: core_class
    slot_binding._publish_census_run = lambda *_args, **_kwargs: None
    try:
        install_fixture_slot_census(Core)
        try:
            Core().propose_island_set({})
        except Refusal as exc:
            campaign = exc.deficiency["fixture_slot_census_campaign"]
            parent = campaign["parent_exact_pool_refusal"]
            assert parent["failure_class"] == "section_pair_compatibility"
            assert parent["learned_pair_constraint_count"] == 1
            assert campaign["campaign_sha256"] == (
                slot_binding._campaign_identity(campaign)
            )
        else:
            raise AssertionError("the original refusal was not preserved")
    finally:
        slot_binding.install_fixture_slot_census = original_installer
        slot_binding._publish_census_run = original_publisher


def test_cli_receipt_publish_failure_rolls_back_candidate(tmp_path):
    script = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "earcrate_slot_qualify.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_earcrate_slot_pair_publish_gate", script
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    output_path = tmp_path / "qualified.json"
    receipt_path = tmp_path / "receipt.json"
    candidate_path.write_text(
        slot_binding.canonical_json(_candidate()), encoding="utf-8"
    )
    census_path.write_text(
        slot_binding.canonical_json(_census_campaign()), encoding="utf-8"
    )

    real_replace = module._REPLACE
    calls = 0

    def fail_receipt_replace(source, destination):
        nonlocal calls
        calls += 1
        if Path(destination).resolve() == receipt_path.resolve():
            raise OSError("injected receipt publish failure")
        return real_replace(source, destination)

    module._REPLACE = fail_receipt_replace
    try:
        assert module.main(
            [
                str(candidate_path),
                str(census_path),
                "--out-candidate",
                str(output_path),
                "--receipt",
                str(receipt_path),
            ]
        ) == 2
    finally:
        module._REPLACE = real_replace
    assert calls >= 2
    assert not output_path.exists()
    assert not receipt_path.exists()
