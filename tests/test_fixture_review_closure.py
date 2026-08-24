from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.judge.arc import DynamicArcError, gate_frame_rms_db, measure_dynamic_arc
from earcrate.plan.fixture_diversity import (
    fixture_distance,
    fixture_projection,
    select_max_min,
)


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "earcrate_fixture_audit.py"
    spec = importlib.util.spec_from_file_location("_earcrate_fixture_review_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _direct_candidate(fixture_id: str, left_source: str = "left", right_source: str = "right"):
    return {
        "kind": "earcrate_fixture_candidate",
        "fixture_id": fixture_id,
        "islands": [
            {
                "island_id": "left-label",
                "start_s": 0.0,
                "end_s": 60.0,
                "target_bpm": 120.0,
                "target_key": 0,
                "source_include_ids": [left_source],
                "allocated_duration_s": 60.0,
            },
            {
                "island_id": "right-label",
                "start_s": 0.0,
                "end_s": 60.0,
                "target_bpm": 90.0,
                "target_key": 5,
                "source_include_ids": [right_source],
                "allocated_duration_s": 60.0,
            },
        ],
        "sections": [
            {
                "island_id": "left-label",
                "type": "INTRO",
                "start_s": 0.0,
                "end_s": 30.0,
                "layers": [{"role": "vocal"}],
            },
            {
                "island_id": "right-label",
                "type": "PAYOFF",
                "start_s": 30.0,
                "end_s": 60.0,
                "layers": [{"role": "bass"}],
            },
        ],
        "transitions": [{"technique": "equal_power", "curve": "equal_power"}],
    }


def test_coextensive_island_labels_and_declaration_order_do_not_move_identity():
    first = _direct_candidate("first")
    second = copy.deepcopy(first)
    second["fixture_id"] = "second"
    second["islands"].reverse()
    second["sections"].reverse()
    rename = {"left-label": "zzz", "right-label": "aaa"}
    for island in second["islands"]:
        island["island_id"] = rename[island["island_id"]]
    for section in second["sections"]:
        section["island_id"] = rename[section["island_id"]]

    left_projection = fixture_projection(first)
    right_projection = fixture_projection(second)
    report = fixture_distance(first, second)
    assert left_projection["fixture_identity"] == right_projection["fixture_identity"]
    assert report["total"] == 0.0
    assert all(value == 0.0 for value in report["axes"].values())


def test_duplicate_semantic_fixture_identities_cannot_enter_max_min():
    first = _direct_candidate("first")
    alias = copy.deepcopy(first)
    alias["fixture_id"] = "alias"
    second = _direct_candidate("second", left_source="other-left")
    third = _direct_candidate("third", right_source="other-right")

    report = select_max_min([first, alias, second, third], limit=3)
    reversed_report = select_max_min([third, second, alias, first], limit=3)
    assert report["status"] == "discriminating"
    assert report["selection_status"] == "not_run_duplicate_semantic_fixture_identities"
    assert report["selected_fixture_ids"] == []
    assert len(report["duplicate_semantic_fixture_identities"]) == 1
    assert reversed_report["selection_status"] == report["selection_status"]
    assert reversed_report["duplicate_semantic_fixture_identities"] == report[
        "duplicate_semantic_fixture_identities"
    ]


def test_public_arc_api_refuses_multidimensional_audio():
    arrangement = {
        "sections": [
            {
                "island_id": "only",
                "type": "PAYOFF",
                "start_s": 0.0,
                "end_s": 1.0,
                "layers": [{"role": "vocal"}],
            }
        ]
    }
    stereo = np.zeros((1000, 2), dtype=np.float32)
    for call in (
        lambda: gate_frame_rms_db(stereo, 1000),
        lambda: measure_dynamic_arc(stereo, 1000, arrangement),
    ):
        try:
            call()
        except DynamicArcError as exc:
            assert "mono" in str(exc)
        else:
            raise AssertionError("multidimensional audio was accepted as canonical arc evidence")


def test_role_events_follow_actual_overlap_boundaries_and_not_declaration_order():
    sample_rate = 1000
    signal = np.concatenate(
        [
            np.full(sample_rate * 15, 0.10, dtype=np.float32),
            np.full(sample_rate * 5, 0.20, dtype=np.float32),
            np.full(sample_rate * 10, 0.30, dtype=np.float32),
        ]
    )
    arrangement = {
        "sections": [
            {
                "island_id": "left",
                "type": "BUILD",
                "start_s": 0.0,
                "end_s": 20.0,
                "layers": [{"role": "vocal"}],
            },
            {
                "island_id": "right",
                "type": "PAYOFF",
                "start_s": 15.0,
                "end_s": 30.0,
                "layers": [{"role": "bass"}],
            },
        ]
    }
    first = measure_dynamic_arc(signal, sample_rate, arrangement)
    reversed_arrangement = copy.deepcopy(arrangement)
    reversed_arrangement["sections"].reverse()
    second = measure_dynamic_arc(signal, sample_rate, reversed_arrangement)

    def semantic(events):
        return [
            {
                key: row[key]
                for key in (
                    "at_s",
                    "active_roles_before",
                    "active_roles_after",
                    "entered_roles",
                    "exited_roles",
                    "before_window_s",
                    "after_window_s",
                    "before_rms_db",
                    "after_rms_db",
                    "rms_delta_db",
                )
            }
            for row in events
        ]

    events = first["role_entries_and_exits"]
    assert semantic(events) == semantic(second["role_entries_and_exits"])
    assert [(row["at_s"], row["entered_roles"], row["exited_roles"]) for row in events] == [
        (15.0, ["bass"], []),
        (20.0, [], ["vocal"]),
    ]
    assert first["role_transition_policy"].startswith("active_role_union")


def test_cli_output_aliases_are_rejected_without_touching_inputs(tmp_path):
    cli = _load_cli()
    sample_rate = 1000
    arrangement = {
        "sections": [
            {
                "island_id": "only",
                "type": "PAYOFF",
                "start_s": 0.0,
                "end_s": 1.0,
                "layers": [{"role": "vocal"}],
            }
        ]
    }
    arrangement_path = tmp_path / "arrangement.json"
    master_path = tmp_path / "master.wav"
    arrangement_path.write_text(json.dumps(arrangement), encoding="utf-8")
    sf.write(str(master_path), np.zeros(sample_rate, dtype=np.float32), sample_rate, subtype="FLOAT")
    master_before = master_path.read_bytes()
    assert cli.main(
        ["arc", str(arrangement_path), str(master_path), "--out", str(master_path)]
    ) == 2
    assert master_path.read_bytes() == master_before

    candidates = [
        _direct_candidate("a"),
        _direct_candidate("b", left_source="b"),
        _direct_candidate("c", right_source="c"),
    ]
    paths = []
    for index, candidate in enumerate(candidates):
        path = tmp_path / f"candidate-{index}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        paths.append(path)
    candidate_before = paths[0].read_bytes()
    assert cli.main(
        ["diversity", *map(str, paths), "--out", str(paths[0])]
    ) == 2
    assert paths[0].read_bytes() == candidate_before


def test_candidate_receipt_hashes_the_exact_bytes_that_were_classified(tmp_path):
    cli = _load_cli()
    candidate_path = tmp_path / "candidate.json"
    replacement_path = tmp_path / "replacement.json"
    old_candidate = _direct_candidate("old")
    new_candidate = _direct_candidate("new", left_source="replacement")
    candidate_path.write_text(json.dumps(old_candidate), encoding="utf-8")
    replacement_path.write_text(json.dumps(new_candidate), encoding="utf-8")
    old_bytes = candidate_path.read_bytes()
    new_bytes = replacement_path.read_bytes()

    real_projection = cli.fixture_projection
    replaced = False

    def replacing_projection(candidate):
        nonlocal replaced
        if not replaced:
            replacement_path.replace(candidate_path)
            replaced = True
        return real_projection(candidate)

    cli.fixture_projection = replacing_projection
    try:
        rows = cli._candidate_rows([candidate_path])
    finally:
        cli.fixture_projection = real_projection

    assert rows[0][1]["file_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert candidate_path.read_bytes() == new_bytes
    assert rows[0][0]["fixture_id"] == "old"


def test_coextensive_realization_form_moves_observation_not_fixture_authority():
    first_body = _direct_candidate("body")
    first_body.pop("kind", None)
    first_body.pop("fixture_id", None)
    first = {"fixture_id": "first", "arrangement": first_body}
    second = copy.deepcopy(first)
    second["fixture_id"] = "second"
    second["arrangement"]["sections"][0]["type"] = "BUILD"
    second["arrangement"]["sections"][0]["layers"].append({"role": "texture"})
    second["arrangement"]["islands"].reverse()
    second["arrangement"]["sections"].reverse()
    rename = {"left-label": "zzz", "right-label": "aaa"}
    for island in second["arrangement"]["islands"]:
        island["island_id"] = rename[island["island_id"]]
    for section in second["arrangement"]["sections"]:
        section["island_id"] = rename[section["island_id"]]

    report = fixture_distance(first, second)
    assert report["total"] == 0.0
    assert report["observed_total"] > 0.0
    assert report["left_semantic_fixture_identity"] == report[
        "right_semantic_fixture_identity"
    ]
    assert report["left_realization_identity"] != report["right_realization_identity"]
