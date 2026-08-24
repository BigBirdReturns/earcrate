from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from earcrate.plan.fixture_derivation import (
    FixtureDerivationError,
    INDETERMINATE_ACTION,
    derive_fixture_candidates,
)
from earcrate.plan.fixture_diversity import classify_candidate_family
from earcrate.plan.islands import allocate_phrase_aligned_islands, validate_request


def _source(source_id, *roles):
    return {"source_id": source_id, "roles": list(roles)}


def _matrix():
    return {
        "kind": "earcrate_fixture_survival_matrix",
        "schema_version": 1,
        "duration_s": 300.0,
        "island_count": 3,
        "phrase_bars": 4,
        "candidate_count": 3,
        "base_seed": 700,
        "max_attempts": 64,
        "target_source_count": 9,
        "arrangement_seed": 679200,
        "required_roles": ["foreground", "bass", "floor"],
        "request_template": {
            "profile": "P004-PROM",
            "source_pool_sha256": "pool-authority",
            "persona": "girl-talk",
            "phrase_playback_law": "proof001_phrase_law",
            "transform_policy": {"unchanged": True},
            "turnover_policy": {"unchanged": True},
            "transition": {
                "technique": "equal_power",
                "phrase_boundary_required": True,
            },
            "source_exclude_ids": [],
        },
        "decks": [
            {
                "deck_id": "deck-120-k0",
                "target_bpm": 120.0,
                "target_key": 0,
                "capacity_s": 160.0,
                "max_sources": 4,
                "sources": [
                    _source("s01", "foreground"),
                    _source("s02", "bass"),
                    _source("s03", "floor"),
                    _source("s04", "foreground", "floor"),
                    _source("s05", "bass", "spark"),
                ],
            },
            {
                "deck_id": "deck-100-k5",
                "target_bpm": 100.0,
                "target_key": 5,
                "capacity_s": 180.0,
                "max_sources": 4,
                "sources": [
                    _source("s04", "foreground", "floor"),
                    _source("s06", "bass"),
                    _source("s07", "floor"),
                    _source("s08", "foreground"),
                    _source("s09", "bass", "spark"),
                ],
            },
            {
                "deck_id": "deck-90-k8",
                "target_bpm": 90.0,
                "target_key": 8,
                "capacity_s": 200.0,
                "max_sources": 4,
                "sources": [
                    _source("s01", "foreground"),
                    _source("s10", "bass"),
                    _source("s11", "floor"),
                    _source("s12", "foreground"),
                    _source("s13", "bass", "floor"),
                ],
            },
            {
                "deck_id": "deck-130-k2",
                "target_bpm": 130.0,
                "target_key": 2,
                "capacity_s": 150.0,
                "max_sources": 4,
                "sources": [
                    _source("s03", "floor"),
                    _source("s06", "bass"),
                    _source("s08", "foreground"),
                    _source("s14", "floor"),
                    _source("s15", "foreground", "bass"),
                ],
            },
            {
                "deck_id": "deck-110-k10",
                "target_bpm": 110.0,
                "target_key": 10,
                "capacity_s": 170.0,
                "max_sources": 4,
                "sources": [
                    _source("s02", "bass"),
                    _source("s07", "floor"),
                    _source("s12", "foreground"),
                    _source("s16", "floor", "spark"),
                    _source("s17", "foreground", "bass"),
                ],
            },
        ],
    }


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _roles_by_deck(matrix):
    return {
        deck["deck_id"]: {
            row["source_id"]: set(row["roles"])
            for row in deck["sources"]
        }
        for deck in matrix["decks"]
    }


def test_derivation_builds_three_distinct_direct_planner_requests():
    matrix = _matrix()
    receipt = derive_fixture_candidates(matrix)
    assert receipt["complete"] is True
    assert receipt["derived_candidate_count"] == 3
    assert receipt["impossibility_claimed"] is False
    assert receipt["private_acceptance"] is None

    candidates = receipt["candidates"]
    semantic_ids = {candidate["fixture_sha256"] for candidate in candidates}
    assert len(semantic_ids) == 3
    assert classify_candidate_family(candidates)["status"] == "discriminating"

    roles_by_deck = _roles_by_deck(matrix)
    for candidate in candidates:
        validate_request(candidate)
        assert candidate["seed"] == matrix["arrangement_seed"]
        assert len(candidate["islands"]) == matrix["island_count"]
        assert candidate["fixture_derivation"]["assigned_source_count"] == matrix["target_source_count"]

        used = []
        for island in candidate["islands"]:
            sources = list(island["source_include_ids"])
            assert len(sources) <= int(island["max_sources"])
            assert len(sources) >= int(island["min_sources"])
            used.extend(sources)
            roles = set().union(*(roles_by_deck[island["deck_id"]][source] for source in sources))
            assert set(island["required_roles"]).issubset(roles)
            assert float(island["capacity_s"]) <= float(island["survival_capacity_s"])
        assert len(used) == len(set(used)) == matrix["target_source_count"]

        allocated, _transitions, net_duration = allocate_phrase_aligned_islands(
            candidate["islands"], candidate["duration_s"], candidate["phrase_bars"]
        )
        assert len(allocated) == len(candidate["islands"])
        assert abs(net_duration - candidate["fixture_derivation"]["net_duration_s"]) < 1e-9
        assert [row["island_id"] for row in allocated] == [
            row["island_id"] for row in candidate["islands"]
        ]
        assert [row["allocated_duration_s"] for row in allocated] == [
            row["allocated_duration_s"] for row in candidate["islands"]
        ]


def test_derivation_is_independent_of_deck_source_and_dictionary_order():
    matrix = _matrix()
    baseline = derive_fixture_candidates(matrix)
    variant = copy.deepcopy(matrix)
    variant["decks"].reverse()
    for deck in variant["decks"]:
        deck["sources"].reverse()
        deck = {key: deck[key] for key in reversed(list(deck))}
    variant["request_template"] = {
        key: variant["request_template"][key]
        for key in reversed(list(variant["request_template"]))
    }
    again = derive_fixture_candidates(variant)
    assert _canonical(again) == _canonical(baseline)


def test_declared_source_target_is_never_silently_lowered():
    matrix = _matrix()
    matrix["target_source_count"] = 999
    matrix["max_attempts"] = 5
    receipt = derive_fixture_candidates(matrix)
    assert receipt["complete"] is False
    assert receipt["derived_candidate_count"] == 0
    assert receipt["impossibility_claimed"] is False
    assert receipt["private_acceptance"] == INDETERMINATE_ACTION
    assert {
        row["failure_class"] for row in receipt["attempts"]
    } == {"selected_decks_cannot_reach_declared_target_source_count"}
    assert all(row["target_source_count"] == 999 for row in receipt["attempts"])


def test_attempt_budget_is_a_bound_not_an_impossibility_claim():
    matrix = _matrix()
    matrix["duration_s"] = 60.0
    matrix["island_count"] = 1
    matrix["candidate_count"] = 2
    matrix["max_attempts"] = 4
    matrix["target_source_count"] = 3
    matrix["decks"] = [matrix["decks"][0]]
    receipt = derive_fixture_candidates(matrix)
    assert receipt["derived_candidate_count"] == 1
    assert receipt["complete"] is False
    assert receipt["impossibility_claimed"] is False
    assert receipt["private_acceptance"] == INDETERMINATE_ACTION
    assert any(row.get("disposition") == "duplicate_semantic_fixture" for row in receipt["attempts"])


def test_derivation_excludes_declared_sources_before_partitioning():
    matrix = _matrix()
    excluded = "discard-me"
    for deck in matrix["decks"]:
        deck["sources"].append(_source(excluded, "spark"))
    matrix["request_template"]["source_exclude_ids"] = [excluded]
    receipt = derive_fixture_candidates(matrix)
    assert receipt["complete"] is True
    for candidate in receipt["candidates"]:
        assert excluded not in {
            source
            for island in candidate["islands"]
            for source in island["source_include_ids"]
        }


def test_survival_matrix_rejects_human_and_filesystem_identity_fields():
    for forbidden in ("path", "artist", "title", "filename"):
        matrix = _matrix()
        matrix["decks"][0]["sources"][0][forbidden] = "private-value"
        try:
            derive_fixture_candidates(matrix)
        except FixtureDerivationError as exc:
            assert forbidden in str(exc)
        else:
            raise AssertionError(f"matrix with {forbidden} must fail closed")


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "earcrate_fixture_audit.py"
    spec = importlib.util.spec_from_file_location("_earcrate_fixture_derive_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fixture_audit_cli_derives_atomic_candidate_files_and_redacted_receipt(tmp_path):
    cli = _load_cli()
    matrix = _matrix()
    matrix_path = tmp_path / "survival-matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    output_dir = tmp_path / "candidates"
    receipt_path = tmp_path / "derivation.json"

    assert cli.main([
        "derive", str(matrix_path),
        "--count", "3",
        "--out-dir", str(output_dir),
        "--receipt", str(receipt_path),
    ]) == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["complete"] is True
    assert receipt["derived_candidate_count"] == 3
    assert "candidates" not in receipt
    assert len(receipt["candidate_files"]) == 3
    assert all(Path(row["path"]).is_file() for row in receipt["candidate_files"])
    assert all("source_include_ids" not in _canonical(row) for row in receipt["candidate_files"])

    first = receipt_path.read_bytes()
    assert cli.main([
        "derive", str(matrix_path),
        "--count", "3",
        "--out-dir", str(output_dir),
        "--receipt", str(receipt_path),
    ]) == 0
    assert receipt_path.read_bytes() == first
