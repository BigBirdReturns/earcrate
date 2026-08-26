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


def _write_inputs(tmp_path):
    helpers = _helpers()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True), encoding="utf-8"
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True), encoding="utf-8"
    )
    return candidate_path, census_path


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
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)

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
    assert maximum["publication"]["authority"] == "receipt"
    assert maximum["publication"]["candidate_role"] == "materialized_cache"
    assert maximum["selected_candidate"]["fixture_sha256"] == (
        maximum["selected_fixture_identity"]
    )
    assert maximum_candidate.is_file()
    assert maximum_candidate.read_bytes() == cli._json_bytes(
        maximum["selected_candidate"]
    )

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
    assert maximum["selected_candidate"] == exact["selected_candidate"]
    assert maximum_candidate.read_bytes() == exact_candidate.read_bytes()

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
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    receipt_path = tmp_path / "receipt.json"
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
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    candidate_alias = tmp_path / "candidate-hardlink.json"
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


def test_receipt_publish_failure_leaves_prior_candidate_untouched(tmp_path):
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    output_path = tmp_path / "selected.json"
    receipt_path = tmp_path / "receipt.json"
    prior_output = b"prior selected candidate\n"
    output_path.write_bytes(prior_output)

    original_replace = cli._REPLACE
    calls = {"count": 0}

    def fail_receipt_commit(source, destination):
        calls["count"] += 1
        raise OSError("injected receipt commit failure")

    cli._REPLACE = fail_receipt_commit
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

    assert calls["count"] == 1
    assert output_path.read_bytes() == prior_output
    assert not receipt_path.exists()


def test_committed_receipt_recovers_candidate_after_process_style_interrupt(
    tmp_path,
):
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    output_path = tmp_path / "selected.json"
    receipt_path = tmp_path / "receipt.json"
    prior_output = b"prior selected candidate\n"
    output_path.write_bytes(prior_output)

    original_materialize = cli._MATERIALIZE_CANDIDATE

    def interrupt_after_receipt_commit(_path, _body):
        raise KeyboardInterrupt("simulated process termination")

    cli._MATERIALIZE_CANDIDATE = interrupt_after_receipt_commit
    try:
        try:
            cli.main(
                [
                    str(candidate_path),
                    str(census_path),
                    "--out-candidate",
                    str(output_path),
                    "--receipt",
                    str(receipt_path),
                ]
            )
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("process-style interruption did not escape")
    finally:
        cli._MATERIALIZE_CANDIDATE = original_materialize

    committed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert committed["publication"]["authority"] == "receipt"
    assert committed["complete"] is True
    assert output_path.read_bytes() == prior_output

    original_selector = cli.select_planable_source_universe

    def solver_must_not_run(*_args, **_kwargs):
        raise AssertionError("recovery reran source-universe selection")

    cli.select_planable_source_universe = solver_must_not_run
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
        ) == 0
    finally:
        cli.select_planable_source_universe = original_selector

    expected = cli._json_bytes(committed["selected_candidate"])
    assert output_path.read_bytes() == expected
    assert (
        committed["selected_candidate_file"]["file_sha256"]
        == __import__("hashlib").sha256(expected).hexdigest()
    )


def test_malformed_or_mismatched_receipt_never_authorizes_recovery(tmp_path):
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    output_path = tmp_path / "selected.json"
    receipt_path = tmp_path / "receipt.json"

    receipt_path.write_text("{not-json", encoding="utf-8")
    calls = {"count": 0}
    original_selector = cli.select_planable_source_universe

    def counted_selector(*args, **kwargs):
        calls["count"] += 1
        return original_selector(*args, **kwargs)

    cli.select_planable_source_universe = counted_selector
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
        ) == 0
    finally:
        cli.select_planable_source_universe = original_selector
    assert calls["count"] == 1

    committed = json.loads(receipt_path.read_text(encoding="utf-8"))
    committed["request"]["time_limit_s"] = 999.0
    receipt_path.write_bytes(cli._json_bytes(committed))
    output_path.unlink()

    calls["count"] = 0
    cli.select_planable_source_universe = counted_selector
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
        ) == 0
    finally:
        cli.select_planable_source_universe = original_selector
    assert calls["count"] == 1
    assert output_path.is_file()
