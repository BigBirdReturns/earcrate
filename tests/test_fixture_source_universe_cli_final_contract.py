from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
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
        "_earcrate_source_universe_final_helpers",
    )


def _cli():
    return _load_module(
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "earcrate_source_universe.py",
        "_earcrate_source_universe_cli_final_gate",
    )


def _write_inputs(tmp_path):
    helpers = _helpers()
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    candidate_path.write_text(
        json.dumps(helpers._candidate(), sort_keys=True),
        encoding="utf-8",
    )
    census_path.write_text(
        json.dumps(helpers._campaign(), sort_keys=True),
        encoding="utf-8",
    )
    return candidate_path, census_path


def _canonical_copies(receipt):
    selection = receipt["selected_candidate"][
        "fixture_source_universe_selection"
    ]
    return (
        selection["slot_assignment_canonicalization"],
        selection["solver"]["slot_assignment_canonicalization"],
        receipt["solver"]["slot_assignment_canonicalization"],
    )


def _reseal(cli, receipt):
    selected_bytes = cli._json_bytes(receipt["selected_candidate"])
    receipt["selected_candidate_file"]["file_sha256"] = hashlib.sha256(
        selected_bytes
    ).hexdigest()
    receipt["selected_candidate_file"]["byte_count"] = len(selected_bytes)
    receipt["publication"]["recovery_evidence_sha256"] = cli.semantic_sha256(
        cli._recovery_evidence_projection(receipt)
    )


def test_solver_free_recovery_reconciles_the_canonicalization_receipt(tmp_path):
    cli = _cli()
    candidate_path, census_path = _write_inputs(tmp_path)
    output_path = tmp_path / "selected.json"
    receipt_path = tmp_path / "receipt.json"

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
    baseline = json.loads(receipt_path.read_text(encoding="utf-8"))
    original_selector = cli.select_planable_source_universe

    def solver_must_not_run(*_args, **_kwargs):
        raise AssertionError("canonicalization contradiction reran the solver")

    cases = {
        "version": lambda row: row.__setitem__("version", "bogus"),
        "method": lambda row: row.__setitem__("method", "bogus"),
        "slot count": lambda row: row.__setitem__(
            "slot_count", int(row["slot_count"]) + 1
        ),
        "selected count": lambda row: row.__setitem__(
            "selected_source_count",
            int(row["selected_source_count"]) + 1,
        ),
        "island count": lambda row: row.__setitem__(
            "island_count", int(row["island_count"]) + 1
        ),
        "feasibility count": lambda row: row.__setitem__(
            "feasibility_check_count", 0
        ),
    }

    cli.select_planable_source_universe = solver_must_not_run
    try:
        for label, mutate in cases.items():
            tampered = copy.deepcopy(baseline)
            for canonical in _canonical_copies(tampered):
                mutate(canonical)
            _reseal(cli, tampered)
            receipt_path.write_bytes(cli._json_bytes(tampered))
            prior_cache = f"prior-cache:{label}\n".encode("utf-8")
            output_path.write_bytes(prior_cache)
            assert cli.main(
                [
                    str(candidate_path),
                    str(census_path),
                    "--out-candidate",
                    str(output_path),
                    "--receipt",
                    str(receipt_path),
                ]
            ) == 2, label
            assert output_path.read_bytes() == prior_cache, label
    finally:
        cli.select_planable_source_universe = original_selector
