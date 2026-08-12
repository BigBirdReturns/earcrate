from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "configs" / "album_one" / "a1-07" / "gold-v7-iteration.v1.json"
CLI = ROOT / "scripts" / "earcrate_a1_07_gold_v7.py"


def _load() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _load_cli():
    spec = importlib.util.spec_from_file_location("earcrate_a1_07_gold_v7", CLI)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gold_v7_contract_is_sealed_and_binds_the_real_parent_review() -> None:
    cli = _load_cli()
    contract = cli.load_contract(CONTRACT)
    assert contract["contract_sha256"] == "858d39452319e90e054c5a4994f07f7ccfcd5d38f011a46ed6834e7a766f1048"
    assert contract["parent"]["owner_review_receipt_sha256"] == (
        "96aab3a610f0786047bfa076030531ea72da4d3c2b0000e35471ac5511ddc4b3"
    )
    assert contract["parent"]["human_acceptance"] is False
    assert contract["parent"]["protected_incumbent"] is True


def test_gold_v7_has_three_causally_distinct_bounded_children() -> None:
    contract = _load()
    assert [row["candidate_id"] for row in contract["children"]] == [
        "gold-v7-arc",
        "gold-v7-interplay",
        "gold-v7-production",
    ]
    assert {row["strategy"] for row in contract["children"]} == {
        "arc_extension",
        "bounded_cross_era_handoffs",
        "production_integration",
    }
    interplay = next(
        row for row in contract["children"]
        if row["candidate_id"] == "gold-v7-interplay"
    )
    assert interplay["mutation_budget"]["handoff_events_max"] == 2
    assert interplay["mutation_budget"]["outside_mask_must_match_incumbent"] is True


def test_owner_frontier_requires_two_qualified_children_and_preserves_incumbent() -> None:
    contract = _load()
    admission = contract["machine_admission"]
    frontier = contract["owner_frontier"]
    assert admission["minimum_qualified_children"] == 2
    assert admission["owner_audition_prohibited_when_below_minimum"] is True
    assert frontier["incumbent"] == "gold-v6"
    assert frontier["challenger_limit"] == 3
    assert frontier["acceptance_semantics"]["child_must_beat_gold_v6"] is True
    assert frontier["acceptance_semantics"][
        "relative_preference_does_not_equal_album_acceptance"
    ] is True


def test_cli_scaffolds_all_children_and_refuses_frontier_below_gate(tmp_path: Path) -> None:
    cli = _load_cli()
    contract = cli.load_contract(CONTRACT)
    workspace = tmp_path / "v7"
    result = cli.scaffold(contract, workspace)
    assert result["ok"] is True
    for child in ("gold-v7-arc", "gold-v7-interplay", "gold-v7-production"):
        assert (workspace / child / "strategy.json").is_file()

    ledger_path = workspace / "RETURN.private.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["exact_branch_head"] = "1" * 64
    ledger["parent_score_sha256"] = "2" * 64
    ledger["parent_pcm_sha256"] = "3" * 64
    ledger["machine_gate_result_by_candidate"]["gold-v7-arc"] = {
        "state": "qualified",
        "reason": "fixture",
    }
    ledger["child_score_sha256_by_candidate"]["gold-v7-arc"] = "4" * 64
    ledger["child_pcm_sha256_by_candidate"]["gold-v7-arc"] = "5" * 64
    ledger["reproduction_receipt_sha256_by_candidate"]["gold-v7-arc"] = "6" * 64
    ledger["machine_gate_result_by_candidate"]["gold-v7-interplay"] = {
        "state": "rejected",
        "reason": "fixture",
    }
    ledger["machine_gate_result_by_candidate"]["gold-v7-production"] = {
        "state": "failed",
        "reason": "fixture",
    }
    ledger["qualified_child_count"] = 1
    ledger["owner_frontier_created"] = False
    ledger["review_public_path_or_null"] = None
    ledger_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verified = cli.verify_return(contract, ledger_path)
    assert verified["qualified_child_count"] == 1
    assert verified["owner_frontier_created"] is False

    ledger["owner_frontier_created"] = True
    ledger["review_public_path_or_null"] = "should-not-exist"
    ledger_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        cli.verify_return(contract, ledger_path)
    except cli.ContractError as exc:
        assert "prohibited below" in str(exc)
    else:
        raise AssertionError("frontier below two-child gate was accepted")


def test_cli_command_surface_verifies_contract() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--contract",
            str(CONTRACT),
            "verify-contract",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["track_id"] == "A1-07"


def test_album_manifest_promotes_gold_v6_to_protected_incumbent() -> None:
    manifest_path = ROOT / "configs" / "album_one" / "manifest.v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    track = next(row for row in manifest["tracks"] if row["track_id"] == "A1-07")
    evidence = track["private_evidence_commitments"]
    assert track["status"]["evidence_state"] == (
        "gold_v6_preferred_incumbent_gold_v7_active"
    )
    assert track["status"]["human_acceptance"] is False
    assert evidence["protected_incumbent_id"] == "gold-v6"
    assert evidence["gold_v6_owner_review_receipt_sha256"] == (
        "96aab3a610f0786047bfa076030531ea72da4d3c2b0000e35471ac5511ddc4b3"
    )
    assert evidence["gold_v7_contract_sha256"] == (
        "858d39452319e90e054c5a4994f07f7ccfcd5d38f011a46ed6834e7a766f1048"
    )
    assert evidence["recovery_open"] is False


def test_gold_v7_assigns_existing_organs_to_explicit_duties() -> None:
    contract = _load()
    roles = contract["organ_roles"]
    assert set(roles) == {
        "work_identity",
        "material_census",
        "performance_clock",
        "pulse_witness",
        "event_map",
        "time_transform",
        "composition_validation",
        "taste_and_search",
        "score_and_render",
        "review",
    }
    assert roles["pulse_witness"]["organs"] == ["Beat This"]
    assert roles["event_map"]["organs"] == ["EarCrate onset baseline"]
    assert roles["time_transform"]["organs"] == ["Rubber Band"]
    assert "PlayerPiano obligation validation" in roles["composition_validation"]["organs"]
    assert roles["score_and_render"]["organs"] == [
        "PerformanceScore",
        "Reference Zero exact renderer",
    ]
