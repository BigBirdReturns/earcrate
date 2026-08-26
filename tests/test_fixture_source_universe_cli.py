from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _helpers():
    return _load_module(
        Path(__file__).with_name("test_fixture_source_universe.py"),
        "_earcrate_source_universe_test_helpers",
    )


def _cli():
    return _load_module(
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "earcrate_source_universe.py",
        "_earcrate_source_universe_cli_gate",
    )


def test_stage2d_census_uses_expanded_policy_schema_v3():
    import earcrate.plan.fixture_slot_binding as slot_binding
    import earcrate.plan.fixture_slot_qualification as public

    helpers = _helpers()
    assert slot_binding.SLOT_CENSUS_VERSION == (
        "earcrate_exact_pool_slot_census_v3"
    )
    assert public.SLOT_CENSUS_VERSION == slot_binding.SLOT_CENSUS_VERSION
    assert helpers._campaign()["version"] == slot_binding.SLOT_CENSUS_VERSION
    assert {
        row["version"] for row in helpers._campaign()["islands"]
    } == {slot_binding.SLOT_CENSUS_VERSION}


def test_source_universe_cli_runs_maximum_and_exact_common_count(tmp_path):
    helpers = _helpers()
    cli = _cli()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True), encoding="utf-8"
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True), encoding="utf-8"
    )

    maximum_candidate = tmp_path / "maximum.json"
    maximum_receipt = tmp_path / "maximum-receipt.json"
    assert cli.main(
        [
            str(candidate_path),
            str(census_path),
            "--out-candidate",
            str(maximum_candidate),
            "--receipt",
            str(maximum_receipt),
        ]
    ) == 0
    maximum = json.loads(maximum_receipt.read_text(encoding="utf-8"))
    assert maximum["complete"] is True
    assert maximum["maximum_planable_source_count"] == 10
    assert maximum["selected_source_count"] == 10
    assert maximum["request"]["target_source_count"] is None
    assert maximum["candidate_input"]["capture_policy"].startswith(
        "single_byte"
    )
    assert maximum["census_input"]["capture_policy"].startswith(
        "single_byte"
    )
    assert maximum_candidate.is_file()

    exact_candidate = tmp_path / "exact.json"
    exact_receipt = tmp_path / "exact-receipt.json"
    assert cli.main(
        [
            str(candidate_path),
            str(census_path),
            "--target-source-count",
            "10",
            "--out-candidate",
            str(exact_candidate),
            "--receipt",
            str(exact_receipt),
        ]
    ) == 0
    exact = json.loads(exact_receipt.read_text(encoding="utf-8"))
    assert exact["complete"] is True
    assert exact["selected_source_count"] == 10
    assert exact["request"]["target_source_count"] == 10
    assert (
        json.loads(maximum_candidate.read_text(encoding="utf-8"))
        == json.loads(exact_candidate.read_text(encoding="utf-8"))
    )

    bound_receipt = tmp_path / "bound-receipt.json"
    assert cli.main(
        [
            str(candidate_path),
            str(census_path),
            "--target-source-count",
            "11",
            "--receipt",
            str(bound_receipt),
        ]
    ) == 3
    bound = json.loads(bound_receipt.read_text(encoding="utf-8"))
    assert bound["complete"] is False
    assert bound["impossibility_claimed"] is False
    assert bound["failure_class"] == "target_exceeds_solver_certified_maximum"


def test_source_universe_cli_refuses_input_alias_without_mutation(tmp_path):
    helpers = _helpers()
    cli = _cli()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    receipt_path = tmp_path / "receipt.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True), encoding="utf-8"
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True), encoding="utf-8"
    )
    before_candidate = candidate_path.read_bytes()
    before_census = census_path.read_bytes()

    assert cli.main(
        [
            str(candidate_path),
            str(census_path),
            "--out-candidate",
            str(candidate_path),
            "--receipt",
            str(receipt_path),
        ]
    ) == 2
    assert candidate_path.read_bytes() == before_candidate
    assert census_path.read_bytes() == before_census
    assert not receipt_path.exists()


def test_source_universe_cli_refuses_hardlink_alias_without_mutation(tmp_path):
    helpers = _helpers()
    cli = _cli()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    candidate_alias = tmp_path / "candidate-hardlink.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True), encoding="utf-8"
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True), encoding="utf-8"
    )
    os.link(candidate_path, candidate_alias)
    before_candidate = candidate_path.read_bytes()
    before_census = census_path.read_bytes()

    assert cli.main(
        [
            str(candidate_path),
            str(census_path),
            "--receipt",
            str(candidate_alias),
        ]
    ) == 2
    assert candidate_path.read_bytes() == before_candidate
    assert candidate_alias.read_bytes() == before_candidate
    assert census_path.read_bytes() == before_census


def test_source_universe_cli_rolls_back_candidate_when_receipt_publish_fails(
    tmp_path,
):
    helpers = _helpers()
    cli = _cli()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    output_path = tmp_path / "selected.json"
    receipt_path = tmp_path / "receipt.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True), encoding="utf-8"
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True), encoding="utf-8"
    )
    prior_output = b"prior selected candidate\n"
    output_path.write_bytes(prior_output)

    original_replace = cli._REPLACE
    calls = {"count": 0}

    def fail_receipt_publish(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected receipt publish failure")
        return original_replace(source, destination)

    cli._REPLACE = fail_receipt_publish
    try:
        assert cli.main(
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
        cli._REPLACE = original_replace

    assert calls["count"] == 3
    assert output_path.read_bytes() == prior_output
    assert not receipt_path.exists()
