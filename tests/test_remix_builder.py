"""Gate: build_remix_persona turns a compact style spec into a persona that
satisfies every load-bearing TasteSpec constraint the persona gate enforces --
so the producer-roster fan-out can supply only style numbers and never emit an
invalid persona.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.tastespec.remix_builder import build_remix_persona, CANONICAL_TRANSFORM_BUDGETS
from earcrate.tastespec.profiles import flat_profile, tastespec_hash


def _spec():
    return {
        "id": "remix_test_v1", "name": "Remix Test", "contract": "a test bed",
        "bpm_low": 88, "bpm_high": 96, "source_seconds": 10, "max_layers": 5, "min_layers": 3,
        "high3000_target": 0.12, "high3000_floor_fail": 0.03, "low200_ceiling_fail": 0.50,
        "objective_weights": {"recognizability": 3, "role_clarity": 2, "danceability": 1,
                              "deck_feasibility": 1, "contrast": 1},  # unnormalized on purpose
    }


def test_builder_output_projects_and_hashes():
    p = build_remix_persona(_spec())
    p["hash"] = tastespec_hash(p)
    fp = flat_profile(p)                       # must not raise
    assert fp["min_layers"] == 3 and fp["max_layers"] == 5
    assert fp["source_seconds"] == 10.0
    assert len(p["hash"]) == 64


def test_builder_weights_sum_to_one_even_from_unnormalized_input():
    p = build_remix_persona(_spec())
    w = p["objective_weights"]
    assert set(w) == {"recognizability", "role_clarity", "danceability", "deck_feasibility", "contrast"}
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_builder_keeps_canonical_transform_budgets():
    p = build_remix_persona(_spec())
    assert p["transform_budgets"]["roles"] == CANONICAL_TRANSFORM_BUDGETS["roles"]
    assert p["transform_budgets"]["default_pitch_semitones"] == 2


def test_builder_compat_relations_match_min_edge_score():
    p = build_remix_persona(dict(_spec(), min_edge_score=0.55))
    for rel, cfg in p["compatibility_relations"].items():
        assert abs(cfg["min_score"] - p["min_edge_score"]) < 1e-9, rel
    assert abs(p["min_edge_score"] - 0.55) < 1e-9


def test_builder_spectral_target_is_well_formed_and_persona_shaped():
    p = build_remix_persona(_spec())
    st = p["spectral_target"]
    assert set(st) == {"rms_std_db", "low200_share", "high3000_share"}
    assert set(st["rms_std_db"]) == {"target", "floor"}
    assert set(st["low200_share"]) == {"ceiling_fail", "ceiling_warn", "floor_warn"}
    assert set(st["high3000_share"]) == {"target", "floor_warn", "floor_fail"}
    assert p["mode"] == "remix" and st["high3000_share"]["floor_fail"] == 0.03


def test_builder_requires_an_id():
    try:
        build_remix_persona({"name": "no id"})
        raise AssertionError("expected ValueError for missing id")
    except ValueError as e:
        assert "id" in str(e)
