from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.judge.arc import DynamicArcError, measure_dynamic_arc
from earcrate.plan.fixture_diversity import fixture_projection, select_max_min


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "earcrate_fixture_audit.py"
    spec = importlib.util.spec_from_file_location("_earcrate_fixture_audit_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _candidate(fixture_id, source, bpm, key):
    return {
        "fixture_id": fixture_id,
        "arrangement": {
            "islands": [{
                "island_id": "island-0",
                "start_s": 0.0,
                "end_s": 30.0,
                "target_bpm": bpm,
                "target_key": key,
                "source_allowlist": [source],
                "allocated_duration_s": 30.0,
            }],
            "sections": [{
                "island_id": "island-0",
                "type": "PAYOFF",
                "start_s": 0.0,
                "end_s": 30.0,
                "layers": [{"role": "vocal"}, {"role": "drum_anchor"}],
            }],
            "transitions": [],
        },
    }


def test_fixture_audit_cli_emits_input_order_independent_diversity_receipt(tmp_path):
    cli = _load_cli()
    candidates = [
        _candidate("a", "a", 120.0, 0),
        _candidate("b", "b", 100.0, 5),
        {
            "fixture_id": "c",
            "arrangement": {
                "islands": [
                    {
                        "island_id": "island-0", "start_s": 0.0, "end_s": 15.0,
                        "target_bpm": 90.0, "target_key": 2,
                        "source_allowlist": ["c"], "allocated_duration_s": 15.0,
                    },
                    {
                        "island_id": "island-1", "start_s": 15.0, "end_s": 30.0,
                        "target_bpm": 130.0, "target_key": 8,
                        "source_allowlist": ["d"], "allocated_duration_s": 15.0,
                    },
                ],
                "sections": [
                    {
                        "island_id": "island-0", "type": "INTRO",
                        "start_s": 0.0, "end_s": 15.0,
                        "layers": [{"role": "vocal"}],
                    },
                    {
                        "island_id": "island-1", "type": "OUTRO",
                        "start_s": 15.0, "end_s": 30.0,
                        "layers": [{"role": "texture"}],
                    },
                ],
                "transitions": [{"technique": "cut", "curve": "cut"}],
            },
        },
    ]
    paths = []
    for index, candidate in enumerate(candidates):
        path = tmp_path / f"candidate-{index}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        paths.append(path)

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert cli.main(["diversity", *map(str, paths), "--limit", "2", "--out", str(first)]) == 0
    assert cli.main(["diversity", *map(str, reversed(paths)), "--limit", "2", "--out", str(second)]) == 0
    left = json.loads(first.read_text(encoding="utf-8"))
    right = json.loads(second.read_text(encoding="utf-8"))
    assert left == right
    assert left["kind"] == "earcrate_fixture_diversity_receipt"
    assert left["selection"]["selection_status"] == "selected_max_min"
    assert left["path_semantics"] == "operational_only_not_fixture_identity"


def test_fixture_audit_cli_measures_master_without_mutating_inputs(tmp_path):
    cli = _load_cli()
    sample_rate = 1000
    signal = np.concatenate([
        np.full(sample_rate * 10, amplitude, dtype=np.float32)
        for amplitude in (0.05, 0.20, 0.10, 0.40)
    ])
    arrangement = {
        "islands": [
            {"island_id": "left", "start_s": 0.0, "end_s": 20.0},
            {"island_id": "right", "start_s": 20.0, "end_s": 40.0},
        ],
        "sections": [
            {
                "island_id": "left", "type": "INTRO", "start_s": 0.0, "end_s": 20.0,
                "layers": [{"role": "vocal", "gain_db": -8.0}],
            },
            {
                "island_id": "right", "type": "PAYOFF", "start_s": 20.0, "end_s": 40.0,
                "layers": [{"role": "vocal", "gain_db": -8.0}, {"role": "bass", "gain_db": -7.0}],
            },
        ],
    }
    arrangement_path = tmp_path / "arrangement.json"
    master_path = tmp_path / "master.wav"
    output_path = tmp_path / "arc.json"
    arrangement_path.write_text(json.dumps(arrangement), encoding="utf-8")
    sf.write(str(master_path), signal, sample_rate, subtype="FLOAT")
    before_arrangement = arrangement_path.read_bytes()
    before_master = master_path.read_bytes()

    assert cli.main(["arc", str(arrangement_path), str(master_path), "--out", str(output_path)]) == 0
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["kind"] == "earcrate_dynamic_arc_receipt"
    assert receipt["measurement"]["rms_std_db"] > 0.0
    assert receipt["master_file"]["channel_count"] == 1
    assert receipt["path_semantics"] == "operational_only_not_arrangement_or_pcm_identity"
    assert arrangement_path.read_bytes() == before_arrangement
    assert master_path.read_bytes() == before_master


def test_fixture_audit_cli_refuses_stereo_as_noncanonical_arc_evidence(tmp_path):
    cli = _load_cli()
    sample_rate = 1000
    signal = np.column_stack([
        np.full(sample_rate * 10, 0.1, dtype=np.float32),
        np.full(sample_rate * 10, 0.2, dtype=np.float32),
    ])
    arrangement = {
        "islands": [{"island_id": "only", "start_s": 0.0, "end_s": 10.0}],
        "sections": [{
            "island_id": "only", "type": "PAYOFF", "start_s": 0.0, "end_s": 10.0,
            "layers": [{"role": "vocal"}],
        }],
    }
    arrangement_path = tmp_path / "arrangement.json"
    master_path = tmp_path / "stereo.wav"
    output_path = tmp_path / "should-not-exist.json"
    arrangement_path.write_text(json.dumps(arrangement), encoding="utf-8")
    sf.write(str(master_path), signal, sample_rate, subtype="FLOAT")
    assert cli.main(["arc", str(arrangement_path), str(master_path), "--out", str(output_path)]) == 2
    assert not output_path.exists()


def test_display_fixture_ids_do_not_decide_max_min_ties():
    candidates = [
        _candidate("z-label", "a", 120.0, 0),
        _candidate("a-label", "b", 120.0, 0),
        _candidate("m-label", "c", 90.0, 8),
    ]
    baseline = select_max_min(candidates, limit=2)
    relabelled = copy.deepcopy(candidates)
    for index, candidate in enumerate(relabelled):
        candidate["fixture_id"] = f"label-{2 - index}"
    variant = select_max_min(relabelled, limit=2)
    assert baseline["selection_status"] == "selected_max_min"
    assert baseline["selected_semantic_fixture_identities"] == variant["selected_semantic_fixture_identities"]
    assert baseline["selected_indexes"] == variant["selected_indexes"]


def test_overlapping_section_declaration_and_island_labels_do_not_change_form_identity():
    candidate = _candidate(None, "a", 120.0, 0)
    candidate["arrangement"]["islands"][0]["island_id"] = "old-label"
    candidate["arrangement"]["sections"] = [
        {
            "island_id": "old-label", "type": "PAYOFF", "start_s": 0.0, "end_s": 20.0,
            "layers": [{"role": "bass"}],
        },
        {
            "island_id": "old-label", "type": "BUILD", "start_s": 0.0, "end_s": 20.0,
            "layers": [{"role": "vocal"}],
        },
    ]
    variant = copy.deepcopy(candidate)
    variant["arrangement"]["islands"][0]["island_id"] = "renamed"
    variant["arrangement"]["sections"].reverse()
    for section in variant["arrangement"]["sections"]:
        section["island_id"] = "renamed"
    assert fixture_projection(candidate) == fixture_projection(variant)


def test_dynamic_arc_refuses_partial_island_spans():
    signal = np.ones(2000, dtype=np.float32)
    arrangement = {
        "islands": [
            {"island_id": "complete", "start_s": 0.0, "end_s": 1.0},
            {"island_id": "partial", "start_s": 1.0},
        ],
        "sections": [
            {"island_id": "complete", "start_s": 0.0, "end_s": 1.0, "layers": []},
            {"island_id": "partial", "start_s": 1.0, "end_s": 2.0, "layers": []},
        ],
    }
    try:
        measure_dynamic_arc(signal, 1000, arrangement)
    except DynamicArcError as exc:
        assert "partial" in str(exc)
    else:
        raise AssertionError("partial island spans must fail closed")
