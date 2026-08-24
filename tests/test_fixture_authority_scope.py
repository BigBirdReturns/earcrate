from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from earcrate.plan.fixture_diversity import (
    ARRANGEMENT_REALIZATION_SCOPE,
    FIXTURE_CANDIDATE_SCOPE,
    FixtureDiversityError,
    classify_candidate_family,
    fixture_distance,
    select_max_min,
)


def _realization(index: int):
    islands = [
        {
            "island_id": "left",
            "start_s": 0.0,
            "end_s": 50.0,
            "target_bpm": 120.0,
            "target_key": 0,
            "source_allowlist": ["s1", "s2"],
            "allocated_duration_s": 50.0,
        },
        {
            "island_id": "right",
            "start_s": 50.0,
            "end_s": 100.0,
            "target_bpm": 100.0,
            "target_key": 5,
            "source_allowlist": ["s3", "s4"],
            "allocated_duration_s": 50.0,
        },
    ]
    sections = []
    for section_index in range(5):
        roles = ["vocal", "drum_anchor"]
        if section_index < index:
            roles.append("bass")
        sections.append(
            {
                "island_id": "left" if section_index < 3 else "right",
                "type": ("INTRO", "BUILD", "HOLD", "PAYOFF", "OUTRO")[section_index],
                "start_s": section_index * 20.0,
                "end_s": (section_index + 1) * 20.0,
                "layers": [{"role": role} for role in roles],
            }
        )
    return {
        "kind": "earcrate_island_set_proposal",
        "seed": 679129 + index,
        "arrangement_sha256": f"arr-{index}",
        "arrangement": {
            "kind": "earcrate_island_set",
            "islands": islands,
            "sections": sections,
            "transitions": [{"technique": "equal_power", "curve": "equal_power"}],
        },
    }


def _fixture_candidate(label: str, source: str, bpm: float):
    return {
        "kind": "earcrate_fixture_candidate",
        "fixture_id": label,
        "duration_s": 100.0,
        "islands": [
            {
                "island_id": "only",
                "start_s": 0.0,
                "end_s": 100.0,
                "target_bpm": bpm,
                "target_key": 0,
                "source_include_ids": [source],
                "allocated_duration_s": 100.0,
            }
        ],
        "transitions": [],
    }


def test_reseeded_arrangements_do_not_manufacture_fixture_diversity():
    candidates = [_realization(index) for index in range(5)]
    report = classify_candidate_family(candidates)
    assert report["evidence_scope"] == ARRANGEMENT_REALIZATION_SCOPE
    assert report["status"] == "non_discriminating"
    assert report["discriminating_pair_count"] == 0
    assert report["realization_variation_pair_count"] == 10
    assert len(set(report["semantic_fixture_identities"])) == 1
    assert len(set(report["semantic_realization_identities"])) == 5
    assert all(row["total"] == 0.0 for row in report["distance_matrix"])
    assert all(row["observed_total"] > 0.0 for row in report["distance_matrix"])
    assert all(
        row["axes"]["form_sequence"] > 0.0
        or row["axes"]["role_occupancy"] > 0.0
        for row in report["distance_matrix"]
    )

    selection = select_max_min(candidates, limit=3)
    assert selection["selection_status"] == "not_run_non_discriminating_family"
    assert selection["selected_fixture_ids"] == []


def test_direct_fixture_candidates_still_authorize_structural_selection():
    candidates = [
        _fixture_candidate("a", "s1", 120.0),
        _fixture_candidate("b", "s2", 100.0),
        _fixture_candidate("c", "s3", 90.0),
    ]
    report = classify_candidate_family(candidates)
    assert report["evidence_scope"] == FIXTURE_CANDIDATE_SCOPE
    assert report["status"] == "discriminating"
    assert report["discriminating_pair_count"] == 3
    selection = select_max_min(candidates, limit=2)
    assert selection["selection_status"] == "selected_max_min"
    assert len(selection["selected_fixture_ids"]) == 2


def test_fixture_and_realization_evidence_cannot_be_mixed():
    try:
        classify_candidate_family(
            [_fixture_candidate("a", "s1", 120.0), _realization(1)]
        )
    except FixtureDiversityError as exc:
        assert "cannot mix" in str(exc)
    else:
        raise AssertionError("mixed evidence scopes were accepted")


def test_realization_only_weights_cannot_authorize_fixture_classification():
    weights = {
        "source_set": 0.0,
        "source_partition": 0.0,
        "deck_sequence": 0.0,
        "island_duration": 0.0,
        "form_sequence": 1.0,
        "role_occupancy": 1.0,
        "transition_histogram": 0.0,
    }
    try:
        fixture_distance(_realization(1), _realization(2), weights)
    except FixtureDiversityError as exc:
        assert "zero weight" in str(exc)
    else:
        raise AssertionError("realization-only weights authorized fixture evidence")


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "earcrate_fixture_audit.py"
    spec = importlib.util.spec_from_file_location("_fixture_scope_cli_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_preserves_realization_movement_without_building_a_fixture_shelf(tmp_path):
    cli = _load_cli()
    paths = []
    for index in range(5):
        path = tmp_path / f"arrangement-{index}.json"
        path.write_text(json.dumps(_realization(index)), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "diversity.json"
    assert cli.main(
        ["diversity", *map(str, paths), "--limit", "3", "--out", str(output)]
    ) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    selection = receipt["selection"]
    assert selection["evidence_scope"] == ARRANGEMENT_REALIZATION_SCOPE
    assert selection["status"] == "non_discriminating"
    assert selection["selection_status"] == "not_run_non_discriminating_family"
    assert selection["realization_variation_pair_count"] == 10
    assert len(set(selection["semantic_fixture_identities"])) == 1
