"""Regression witnesses for pitch-class zero across the long-form planner.

The private Proof-005 fixture exposed a Python truthiness defect: ``key_root=0``
(C) was treated as missing and replaced by the exact deck key. These witnesses
keep feasibility, exact-slot transforms, pair scoring, composition adaptation,
and source-pool identity on one rule. The repository runner invokes tests
directly, so every witness is argument-free.
"""
from earcrate.app import EarcrateCore
from earcrate.plan.key_identity import (
    KEY_IDENTITY_POLICY,
    KeyIdentityError,
    canonical_key_root,
    corrected_source_pool_identity,
    corrected_source_pool_projection,
    normalize_key_pool_for_legacy,
)
from earcrate.plan import source_rotation


def _atom(source, key_root, role="vocal", ear_role="VOX_HOOK"):
    return {
        "id": f"loop-{source}",
        "loop_id": f"loop-{source}",
        "atom_id": f"atom-{source}",
        "source_track_key": source,
        "artist": source,
        "title": source,
        "ear_role": ear_role,
        "render_role": role,
        "role": role,
        "bpm": 120.0,
        "key_root": key_root,
        "bars": 4,
        "start_s": 0.0,
        "end_s": 8.0,
        "score": 0.9,
        "hook_score": 0.8,
        "bed_score": 0.8,
        "floor_score": 0.8,
        "bass_score": 0.8,
        "spark_score": 0.6,
        "intelligibility": 0.8,
        "source_audio_sha256": f"pcm-{source}",
    }


def test_key_zero_is_C_and_only_null_inherits_fallback():
    assert canonical_key_root(0, 6) == 0
    assert canonical_key_root({"key_root": 0}, 6) == 0
    assert canonical_key_root(None, 6) == 6
    assert canonical_key_root({"key_root": None}, 6) == 6
    try:
        canonical_key_root("", 6)
    except KeyIdentityError as exc:
        assert "only NULL" in str(exc)
    else:
        raise AssertionError("empty key identity must not inherit a deck key")


def test_legacy_compose_adapter_preserves_numeric_C_without_mutating_input():
    original = [{"source_track_key": "c", "key_root": 0}, {"source_track_key": "unknown", "key_root": None}]
    normalized = normalize_key_pool_for_legacy(original)
    assert int(normalized[0]["key_root"]) == 0
    assert bool(normalized[0]["key_root"]) is True, "C must survive legacy `value or fallback` expressions"
    assert normalized[1]["key_root"] is None
    assert original[0]["key_root"] == 0 and original[1]["key_root"] is None


def test_feasibility_does_not_admit_key_C_as_the_exact_deck_key():
    core = EarcrateCore.__new__(EarcrateCore)
    params = {"stretch_budget": 8.0, "pitch_shift_budget": 1}
    key_c = _atom("key-c", 0)
    unknown = _atom("unknown", None)

    c_pool, c_diag = core.taste_feasible_pool([key_c], 120.0, 6, params)
    unknown_pool, unknown_diag = core.taste_feasible_pool([unknown], 120.0, 6, params)

    assert c_pool == [], "C vocal must not masquerade as the target tritone deck"
    assert len(unknown_pool) == 1, "only NULL may inherit the exact deck key"
    assert c_diag["key_identity_policy"] == KEY_IDENTITY_POLICY
    assert unknown_diag["key_identity_policy"] == KEY_IDENTITY_POLICY


def test_exact_slot_transform_and_pair_score_read_key_zero_identically():
    core = EarcrateCore.__new__(EarcrateCore)
    params = {"stretch_budget": 8.0, "pitch_shift_budget": 1}
    key_c = _atom("key-c", 0)
    unknown = _atom("unknown", None)
    bed = _atom("bed", 6, role="harmony", ear_role="BED_CHORD")

    assert source_rotation._transform_for_slot(key_c, "vocal", 120.0, 6, params) is None
    assert source_rotation._transform_for_slot(unknown, "vocal", 120.0, 6, params) is not None

    c_score, c_reasons = core.atom_edge_score(key_c, bed, "vocal_over_bed", 120.0, 6, 8.0, 1)
    unknown_score, _unknown_reasons = core.atom_edge_score(unknown, bed, "vocal_over_bed", 120.0, 6, 8.0, 1)
    assert c_score == 0.0 and c_reasons.get("reason") == "transform_violation"
    assert unknown_score > 0.0


def test_source_pool_identity_distinguishes_C_from_unknown_key():
    key_c = _atom("same-source", 0)
    unknown = _atom("same-source", None)
    c_projection = corrected_source_pool_projection([key_c])
    unknown_projection = corrected_source_pool_projection([unknown])

    assert c_projection[0]["key_root"] == 0
    assert unknown_projection[0]["key_root"] is None
    assert corrected_source_pool_identity([key_c]) != corrected_source_pool_identity([unknown])
    assert corrected_source_pool_identity([key_c, unknown]) == corrected_source_pool_identity([unknown, key_c])


def test_key_identity_policy_is_installed_on_engine():
    assert EarcrateCore._key_identity_policy == KEY_IDENTITY_POLICY
    assert EarcrateCore._key_identity_installed is True
