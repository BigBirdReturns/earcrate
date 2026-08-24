from __future__ import annotations

import json
import math

import numpy as np

from earcrate.plan.fixture_diversity import (
    classify_candidate_family,
    fixture_distance,
    fixture_id,
    jaccard_distance,
    select_max_min,
)
from earcrate.judge.arc import gate_frame_rms_db, measure_dynamic_arc


def _candidate(
    fixture_id_value,
    sources,
    decks,
    durations,
    forms=("INTRO", "BUILD", "PAYOFF"),
    role_sets=(("vocal",), ("vocal", "drum_anchor"), ("vocal", "drum_anchor", "bass")),
    transitions=("equal_power", "equal_power"),
    arrangement_sha=None,
    seed=1,
    island_prefix="isl",
):
    islands = []
    cursor = 0.0
    for index, ((bpm, key), duration) in enumerate(zip(decks, durations)):
        island_id = f"{island_prefix}-{index}"
        islands.append({
            "island_id": island_id,
            "start_s": cursor,
            "end_s": cursor + duration,
            "target_bpm": bpm,
            "target_key": key,
            "source_allowlist": list(
                sources[index] if isinstance(sources[0], (list, tuple, set)) else sources
            ),
            "allocated_duration_s": duration,
        })
        cursor += duration
    sections = []
    section_duration = cursor / max(1, len(forms))
    for index, form in enumerate(forms):
        sections.append({
            "island_id": islands[min(index, len(islands) - 1)]["island_id"],
            "type": form,
            "start_s": index * section_duration,
            "end_s": (index + 1) * section_duration,
            "layers": [{"role": role} for role in role_sets[index]],
        })
    return {
        "fixture_id": fixture_id_value,
        "seed": seed,
        "arrangement_sha256": arrangement_sha or f"arr-{fixture_id_value}",
        "arrangement": {
            "islands": islands,
            "sections": sections,
            "transitions": [
                {"technique": value, "curve": value}
                for value in transitions
            ],
        },
    }


def test_seed_only_hash_changes_are_non_discriminating():
    base = _candidate(
        "same-a", [["s1", "s2"], ["s3"]], [(120.0, 0), (100.0, 5)], [60.0, 40.0],
        arrangement_sha="aaa", seed=1,
    )
    other = _candidate(
        "same-b", [["s1", "s2"], ["s3"]], [(120.0, 0), (100.0, 5)], [60.0, 40.0],
        arrangement_sha="bbb", seed=999,
    )
    report = classify_candidate_family([base, other])
    assert report["status"] == "non_discriminating"
    assert report["distance_matrix"][0]["total"] == 0.0


def test_fixture_labels_and_arrangement_hashes_cannot_manufacture_identity():
    base = _candidate(
        None, [["s1", "s2"], ["s3"]], [(120.0, 0), (100.0, 5)], [60.0, 40.0],
        arrangement_sha="aaa", island_prefix="old",
    )
    variant = _candidate(
        None, [["s1", "s2"], ["s3"]], [(120.0, 0), (100.0, 5)], [60.0, 40.0],
        arrangement_sha="bbb", island_prefix="renamed",
    )
    report = fixture_distance(base, variant)
    assert report["total"] == 0.0
    assert fixture_id(base) == fixture_id(variant)


def test_source_set_distance_is_symmetric_bounded_and_order_independent():
    assert jaccard_distance(["a", "b", "c"], ["c", "d"]) == jaccard_distance(
        ["d", "c"], ["c", "b", "a"]
    )
    assert math.isclose(jaccard_distance(["a", "b", "c"], ["c", "d"]), 0.75)
    assert 0.0 <= jaccard_distance(["a"], ["b"]) <= 1.0


def test_deck_sequence_distinguishes_order_and_exact_identity():
    base = _candidate("a", [["a"], ["b"]], [(120.0, 0), (100.0, 5)], [50, 50])
    reordered = _candidate("b", [["a"], ["b"]], [(100.0, 5), (120.0, 0)], [50, 50])
    changed = _candidate("c", [["a"], ["b"]], [(120.0, 0), (100.0, 6)], [50, 50])
    first = fixture_distance(base, reordered)["axes"]["deck_sequence"]
    second = fixture_distance(base, changed)["axes"]["deck_sequence"]
    assert first > 0.0
    assert second > 0.0
    assert first != second


def test_duration_distance_is_independent_of_island_declaration_order():
    base = _candidate("a", [["a"], ["b"]], [(120.0, 0), (100.0, 5)], [70, 30])
    variant = json.loads(json.dumps(base))
    variant["fixture_id"] = "b"
    variant["arrangement"]["islands"].reverse()
    axes = fixture_distance(base, variant)["axes"]
    assert axes["island_duration"] == 0.0
    assert axes["deck_sequence"] == 0.0


def test_role_and_transition_histograms_ignore_dictionary_order():
    base = _candidate("a", [["a"]], [(120.0, 0)], [90])
    variant = json.loads(json.dumps(base))
    variant["fixture_id"] = "b"
    variant["arrangement"]["sections"] = [
        {key: row[key] for key in reversed(list(row))}
        for row in variant["arrangement"]["sections"]
    ]
    variant["arrangement"]["transitions"] = [
        {key: row[key] for key in reversed(list(row))}
        for row in variant["arrangement"]["transitions"]
    ]
    axes = fixture_distance(base, variant)["axes"]
    assert axes["role_occupancy"] == 0.0
    assert axes["transition_histogram"] == 0.0


def test_max_min_does_not_claim_discrimination_for_pair_or_zero_range():
    pair = [
        _candidate("a", [["a"]], [(120.0, 0)], [90]),
        _candidate("b", [["b"]], [(100.0, 5)], [90]),
    ]
    report = select_max_min(pair, limit=2)
    assert report["selection_status"] == "not_run_fewer_than_three_candidates"
    assert report["selected_fixture_ids"] == []

    equal = [
        _candidate("a", [["a"]], [(120.0, 0)], [90]),
        _candidate("b", [["b"]], [(120.0, 0)], [90]),
        _candidate("c", [["c"]], [(120.0, 0)], [90]),
    ]
    report = select_max_min(equal, limit=2, weights={
        "source_set": 1.0,
        "deck_sequence": 0.0,
        "island_duration": 0.0,
        "form_sequence": 0.0,
        "role_occupancy": 0.0,
        "transition_histogram": 0.0,
    })
    assert report["selection_status"] == "not_run_degenerate_distance_range"
    assert report["selected_fixture_ids"] == []


def test_max_min_selects_a_real_structural_frontier():
    first = _candidate("a", [["s1", "s2"]], [(120.0, 0)], [90])
    second = _candidate("b", [["s1", "s3"]], [(120.0, 0)], [90])
    third = _candidate(
        "c", [["x1"], ["x2"]], [(90.0, 7), (130.0, 2)], [40, 50],
        forms=("INTRO", "HOLD", "OUTRO"),
        role_sets=(("vocal",), ("vocal",), ("texture",)),
        transitions=("cut", "equal_power"),
    )
    report = select_max_min([first, second, third], limit=2)
    assert report["selection_status"] == "selected_max_min"
    assert "c" in report["selected_fixture_ids"]
    assert len(report["selected_fixture_ids"]) == 2


def test_fixture_receipts_name_only_structural_metrics():
    candidates = [
        _candidate("a", [["a"]], [(120.0, 0)], [90]),
        _candidate("b", [["b"]], [(100.0, 5)], [90]),
        _candidate("c", [["c"], ["d"]], [(90.0, 2), (130.0, 8)], [40, 50]),
    ]
    text = json.dumps(select_max_min(candidates, limit=2), sort_keys=True).lower()
    assert "mfcc" not in text
    for name in (
        "source_set", "deck_sequence", "island_duration",
        "form_sequence", "role_occupancy", "transition_histogram",
    ):
        assert name in text


def _arc_fixture():
    sr = 1000
    amplitudes = [0.05, 0.20, 0.10, 0.40]
    signal = np.concatenate([
        np.full(sr * 10, amplitude, dtype=np.float32)
        for amplitude in amplitudes
    ])
    sections = []
    for index, _amplitude in enumerate(amplitudes):
        sections.append({
            "island_id": "left" if index < 2 else "right",
            "type": ("INTRO", "PAYOFF", "HOLD", "PAYOFF")[index],
            "start_s": index * 10.0,
            "end_s": (index + 1) * 10.0,
            "layers": [
                {"role": "vocal", "gain_db": -8.0},
                *([{"role": "bass", "gain_db": -7.0}] if index % 2 else []),
            ],
        })
    return signal, sr, {
        "islands": [
            {"island_id": "left", "start_s": 0.0, "end_s": 20.0},
            {"island_id": "right", "start_s": 20.0, "end_s": 40.0},
        ],
        "sections": sections,
    }


def test_arc_measurement_reproduces_the_gate_frame_law():
    from earcrate.judge.audio import drydeck_metrics

    signal, sr, arrangement = _arc_fixture()
    report = measure_dynamic_arc(signal, sr, arrangement)
    gate = drydeck_metrics(signal, sr)
    assert math.isclose(
        report["rms_std_db"], gate["rms_std_db"], rel_tol=0.0, abs_tol=1e-12
    )
    assert np.array_equal(
        gate_frame_rms_db(signal, sr), gate_frame_rms_db(signal.copy(), sr)
    )


def test_arc_measurement_separates_variance_without_assigning_cause():
    signal, sr, arrangement = _arc_fixture()
    report = measure_dynamic_arc(signal, sr, arrangement)
    assert report["within_island_variance_db2"] > 0.0
    assert report["between_island_variance_db2"] > 0.0
    assert abs(report["variance_decomposition_residual_db2"]) < 1e-10
    assert report["causal_disposition"] == "unassigned_measurement_only"
    assert len(report["role_entries_and_exits"]) == 3
    assert all("cause" not in key for key in report if key != "causal_disposition")


def test_arc_overlap_attribution_is_explicit_and_declaration_order_stable():
    sr = 1000
    signal = np.concatenate([
        np.full(sr * 10, value, dtype=np.float32)
        for value in (0.05, 0.10, 0.20)
    ])
    arrangement = {
        "islands": [
            {"island_id": "left", "start_s": 0.0, "end_s": 20.0},
            {"island_id": "right", "start_s": 15.0, "end_s": 30.0},
        ],
        "sections": [
            {"island_id": "left", "type": "BUILD", "start_s": 0.0, "end_s": 20.0, "layers": []},
            {"island_id": "right", "type": "PAYOFF", "start_s": 15.0, "end_s": 30.0, "layers": []},
        ],
    }
    first = measure_dynamic_arc(signal, sr, arrangement)
    reversed_sections = json.loads(json.dumps(arrangement))
    reversed_sections["sections"].reverse()
    second = measure_dynamic_arc(signal, sr, reversed_sections)
    assert first["regions"] == second["regions"]
    assert first["within_region_variance_db2"] == second["within_region_variance_db2"]
    assert first["between_region_variance_db2"] == second["between_region_variance_db2"]
    assert any(row["kind"] == "transition_overlap" for row in first["regions"])
    assert first["overlap_policy"] == "transition_frames_form_explicit_composite_regions"


def test_fixture_diversity_import_does_not_mutate_exact_pool_authority():
    from earcrate.plan import source_rotation

    before = source_rotation.rebalance_exact_pool_sources
    import earcrate.plan.fixture_diversity as fixture_diversity
    assert fixture_diversity.fixture_projection
    assert source_rotation.rebalance_exact_pool_sources is before
