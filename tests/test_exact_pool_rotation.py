"""Regression witnesses for Proof-005 exact-pool source rotation.

The private failure was structural: a restricted exact-deck pool could contain
role-valid sources that never entered the arrangement, while one selected source
continued past the existing 12-event veto. These fixtures reproduce that shape
without private identities or media.
"""
from collections import Counter
import copy

from earcrate.plan.source_rotation import (
    ExactPoolRotationError,
    install_exact_pool_rotation,
    rebalance_exact_pool_sources,
)


def _atom(source, role, index, score=0.8):
    ear = {
        "vocal": "VOX_HOOK",
        "bass": "BASS_RIFF",
        "drum_anchor": "DRUM_BREAK",
        "harmony": "BED_CHORD",
        "texture": "TEXTURE",
    }[role]
    return {
        "id": f"loop-{source}-{index}",
        "atom_id": f"atom-{source}-{index}",
        "source_track_key": source,
        "artist": source,
        "title": source,
        "ear_role": ear,
        "render_role": role,
        "role": role,
        "bpm": 120.0,
        "key_root": 0,
        "bars": 4,
        "start_s": 0.0,
        "end_s": 8.0,
        "score": score,
        "hook_score": score,
        "high_share": 0.25,
    }


def _layer(item, role):
    return {
        "loop_id": item["id"],
        "atom_id": item["atom_id"],
        "ear_role": item["ear_role"],
        "role": role,
        "bar_offset": 0,
        "bar_len": 4,
        "gain_db": -8.0,
        "world": "taste",
        "source_track_key": item["source_track_key"],
    }


def _fixture():
    vocals = [_atom(f"vocal-{index}", "vocal", index) for index in range(4)]
    floors = [_atom(f"floor-{index}", "drum_anchor", index) for index in range(4)]
    pool = vocals + floors
    sections = []
    for index in range(8):
        sections.append({
            "bar_start": index * 4,
            "bars": 4,
            "type": "sustain",
            "target_key": 0,
            "transition_in": {"type": "start" if index == 0 else "beatmatch_blend"},
            "layers": [
                _layer(vocals[-1], "vocal"),
                _layer(floors[-1], "drum_anchor"),
            ],
        })
    arrangement = {
        "bpm": 120.0,
        "target_key": 0,
        "seed": 413676,
        "params": {},
        "sections": sections,
        "taste_ledger": {},
    }
    params = {
        "island_id": "island-000",
        "exact_target_bpm": 120.0,
        "exact_target_key": 0,
        "stretch_budget": 8.0,
        "pitch_shift_budget": 2,
        "exact_pool_max_source_events": 3,
    }
    return pool, arrangement, params


class _Core:
    def atom_edge_score(self, left, right, relation, render_bpm, target_key, stretch_budget, pitch_budget):
        # All fixture pairs are admitted. The rotation algorithm still has to
        # preserve role families and deterministic transform legality.
        return 0.75, {"fixture": True, "relation": relation}


def _source_sequence(arrangement):
    return [
        layer["source_track_key"]
        for section in arrangement["sections"]
        for layer in section["layers"]
    ]


def test_exact_pool_rotation_reaches_every_source_and_obeys_cap():
    pool, arrangement, params = _fixture()
    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, 413676)
    counts = Counter(_source_sequence(result))
    assert set(counts) == {item["source_track_key"] for item in pool}
    assert max(counts.values()) <= 3
    authority = result["taste_ledger"]["exact_pool_rotation"]
    assert authority["target_source_count"] == 8
    assert authority["used_source_count"] == 8
    assert authority["observed_max_source_events"] <= 3
    assert authority["replacement_count"] > 0


def test_exact_pool_rotation_is_independent_of_allowlist_order():
    pool, arrangement, params = _fixture()
    first = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, 413676)
    second = rebalance_exact_pool_sources(_Core(), arrangement, list(reversed(pool)), params, 413676)
    assert _source_sequence(first) == _source_sequence(second)
    assert first["taste_ledger"]["exact_pool_rotation"] == second["taste_ledger"]["exact_pool_rotation"]


def test_exact_pool_rotation_refuses_unreachable_role_instead_of_faking_it():
    pool, arrangement, params = _fixture()
    pool.append(_atom("bass-only", "bass", 99))
    try:
        rebalance_exact_pool_sources(_Core(), arrangement, pool, params, 413676)
    except ExactPoolRotationError as exc:
        assert "unreachable" in str(exc) and "bass" in str(exc)
    else:
        raise AssertionError("a source with no compatible arrangement slot must refuse")


def test_exact_pool_rotation_does_not_change_ordinary_composition():
    class Core(_Core):
        def compose_taste_arrangement(self, pool, params, seed):
            return {"ordinary": True, "seed": seed, "pool": [item["id"] for item in pool]}

    install_exact_pool_rotation(Core)
    core = Core()
    pool, _arrangement, _params = _fixture()
    result = core.compose_taste_arrangement(pool, {"target_seconds": 120}, 7)
    assert result == {"ordinary": True, "seed": 7, "pool": [item["id"] for item in pool]}
    assert Core.compose_taste_arrangement.__wrapped__ is Core._ordinary_compose_taste_arrangement


def test_exact_pool_wrapper_repairs_only_exact_island_calls():
    pool, arrangement, params = _fixture()

    class Core(_Core):
        def compose_taste_arrangement(self, _pool, _params, _seed):
            return copy.deepcopy(arrangement)

    install_exact_pool_rotation(Core)
    result = Core().compose_taste_arrangement(pool, params, 413676)
    counts = Counter(_source_sequence(result))
    assert set(counts) == {item["source_track_key"] for item in pool}
    assert max(counts.values()) <= 3
