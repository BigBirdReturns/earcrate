"""Executable witnesses for the deterministic multi-island planner.

The repository runner invokes test functions directly, so every gate is argument
free. Private source IDs never enter these fixtures.
"""
import copy
from pathlib import Path

import numpy as np

from earcrate.plan.islands import (
    IslandPlanError,
    allocate_phrase_aligned_islands,
    install_island_set,
    plan_island_set,
    source_pool_identity,
)
from earcrate.plan.island_render import combine_island_audio


def _atom(source, atom, role, bpm, key, score=0.8):
    ear = {
        "foreground": "VOX_HOOK",
        "floor": "DRUM_BREAK",
        "bass": "BASS_RIFF",
        "spark": "DROP_HIT",
    }[role]
    render = {
        "foreground": "vocal",
        "floor": "drum_anchor",
        "bass": "bass",
        "spark": "texture",
    }[role]
    return {
        "id": atom,
        "atom_id": atom,
        "source_track_key": source,
        "artist": source,
        "title": source,
        "ear_role": ear,
        "render_role": render,
        "role": render,
        "bpm": bpm,
        "key_root": key,
        "bars": 4,
        "start_s": 0.0,
        "end_s": 10.0,
        "score": score,
        "hook_score": score,
        "source_audio_sha256": source + "-pcm",
    }


class _FakeCore:
    def __init__(self, pool):
        self.pool = list(pool)
        self.ordinary_deck_calls = 0

    def approved_atom_pool(self, profile):
        return list(self.pool)

    def taste_feasible_pool(self, pool, bpm, key, params):
        out = [
            dict(item, feasible_transform={"violation": None})
            for item in pool
            if abs(float(item["bpm"]) - float(bpm)) < 1e-9
            and int(item["key_root"]) % 12 == int(key) % 12
        ]
        counts = {name: 0 for name in ("VOX_HOOK", "DRUM_BREAK", "BASS_RIFF", "DROP_HIT")}
        for item in out:
            counts[item["ear_role"]] += 1
        have = {
            "foreground": counts["VOX_HOOK"],
            "floor": counts["DRUM_BREAK"],
            "bass": counts["BASS_RIFF"],
            "spark": counts["DROP_HIT"],
            "sources": len({item["source_track_key"] for item in out}),
        }
        return out, {
            "have": have,
            "render_bpm": bpm,
            "target_key": key,
            "pool_size": len(out),
        }

    def choose_taste_deck(self, pool, params):
        self.ordinary_deck_calls += 1
        return {
            "pool": list(pool),
            "render_bpm": 999.0,
            "target_key": 11,
            "diagnostics": {},
        }

    def compose_taste_arrangement(self, pool, params, seed):
        deck = self.choose_taste_deck(pool, params)
        bpm = deck["render_bpm"]
        key = deck["target_key"]
        sections = []
        for index, item in enumerate(deck["pool"]):
            sections.append({
                "bar_start": index * 4,
                "bars": 4,
                "type": "sustain",
                "target_key": key,
                "transition_in": {"type": "start" if index == 0 else "beatmatch_blend"},
                "layers": [{
                    "loop_id": item["id"],
                    "atom_id": item["atom_id"],
                    "ear_role": item["ear_role"],
                    "role": item["render_role"],
                    "bar_offset": 0,
                    "bar_len": 4,
                    "source_track_key": item["source_track_key"],
                }],
            })
        return {
            "bpm": bpm,
            "target_key": key,
            "seed": seed,
            "params": dict(params),
            "sections": sections,
        }

    def arrangement_preflight_gate(self, arrangement):
        return {"passed": True, "failures": []}

    def taste_arrangement_gate(self, arrangement):
        return {"passed": True, "failures": []}

    def render_mashup(self, mashup_id, destination):
        return {"type": "render_mashup", "path": str(destination), "presented": True}


def _fixture():
    pool = []
    for island, bpm, key in ((0, 120.0, 0), (1, 130.0, 5)):
        for role in ("foreground", "floor", "bass", "spark"):
            pool.append(_atom(f"s{island}-{role}", f"a{island}-{role}", role, bpm, key))
    core = _FakeCore(pool)
    params = {
        "profile": "girl_talk_v1",
        "seed": 7,
        "duration_s": 55.0,
        "source_pool_sha256": source_pool_identity(pool),
        "source_exclude_ids": [],
        "transform_policy": {
            "identity": "transform-policy-fixture",
            "unchanged": True,
            "stretch_budget": 8.0,
            "pitch_shift_budget": 2,
        },
        "turnover_policy": {
            "identity": "turnover-policy-fixture",
            "unchanged": True,
        },
        "persona": "remix_prettylights_v1",
        "phrase_playback_law": "proof001_phrase_law",
        "transition": {
            "technique": "equal_power",
            "phrase_boundary_required": True,
            "duration_policy": "existing_anchor_derived",
        },
        "persist": False,
        "islands": [
            {
                "island_id": "island-000",
                "target_bpm": 120.0,
                "target_key": 0,
                "capacity_s": 40.0,
                "source_include_ids": [f"s0-{role}" for role in ("foreground", "floor", "bass", "spark")],
                "required_roles": ["foreground", "floor", "bass", "spark"],
            },
            {
                "island_id": "island-001",
                "target_bpm": 130.0,
                "target_key": 5,
                "capacity_s": 40.0,
                "source_include_ids": [f"s1-{role}" for role in ("foreground", "floor", "bass", "spark")],
                "required_roles": ["foreground", "floor", "bass", "spark"],
            },
        ],
    }
    return core, params


def test_island_set_is_deterministic_under_allowlist_reordering():
    core, params = _fixture()
    first = plan_island_set(core, params)
    reordered = copy.deepcopy(params)
    for island in reordered["islands"]:
        island["source_include_ids"].reverse()
    second = plan_island_set(core, reordered)
    assert first["arrangement_sha256"] == second["arrangement_sha256"]


def test_island_set_respects_exact_decks_and_source_allowlists():
    core, params = _fixture()
    result = plan_island_set(core, params)
    assert [row["target_bpm"] for row in result["islands"]] == [120.0, 130.0]
    assert [row["target_key"] for row in result["islands"]] == [0, 5]
    for row in result["islands"]:
        assert set(row["source_ids"]) <= set(row["source_allowlist"])
    assert result["arrangement"]["accounting"]["source_reuse"] == 0
    assert core.ordinary_deck_calls == 0


def test_island_set_rejects_duplicate_source_across_islands():
    core, params = _fixture()
    params["islands"][1]["source_include_ids"][0] = params["islands"][0]["source_include_ids"][0]
    try:
        plan_island_set(core, params)
    except IslandPlanError as exc:
        assert "multiple islands" in str(exc)
    else:
        raise AssertionError("duplicate source should refuse")


def test_island_set_rejects_stale_source_pool_identity():
    core, params = _fixture()
    params["source_pool_sha256"] = "0" * 64
    try:
        plan_island_set(core, params)
    except IslandPlanError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("stale pool identity should refuse")


def test_island_set_rejects_role_incomplete_exact_deck():
    core, params = _fixture()
    params["islands"][0]["source_include_ids"].remove("s0-bass")
    try:
        plan_island_set(core, params)
    except IslandPlanError as exc:
        assert "role-complete" in str(exc)
    else:
        raise AssertionError("role-incomplete island should refuse")


def test_island_set_capacity_is_phrase_aligned_and_overlap_aware():
    core, params = _fixture()
    result = plan_island_set(core, params)
    assert result["duration_s"] >= params["duration_s"]
    for row in result["islands"]:
        phrase = 16 * 60.0 / row["target_bpm"]
        quotient = row["allocated_duration_s"] / phrase
        assert abs(quotient - round(quotient)) < 1e-8
    assert all(
        transition["at_phrase_boundary"]
        and transition["technique"] == "equal_power"
        for transition in result["transitions"]
    )


def test_island_set_insufficient_union_capacity_refuses_before_composition():
    core, params = _fixture()
    params["duration_s"] = 500.0
    try:
        plan_island_set(core, params)
    except IslandPlanError as exc:
        assert "insufficient phrase-aligned union capacity" in str(exc)
    else:
        raise AssertionError("insufficient capacity should refuse")
    assert core.ordinary_deck_calls == 0


def test_island_render_stems_reconcile_to_master():
    left = np.linspace(-0.4, 0.4, 8000, dtype=np.float32)
    right = np.linspace(0.3, -0.3, 9000, dtype=np.float32)
    stems = [
        {"voice": left * 0.6, "rhythm": left * 0.4},
        {"voice": right * 0.55, "rhythm": right * 0.45},
    ]
    master, combined = combine_island_audio(
        [left, right],
        stems,
        [{"duration_s": 0.05, "technique": "equal_power"}],
        48000,
    )
    summed = np.zeros_like(master)
    for audio in combined.values():
        summed += audio
    assert np.max(np.abs(master - summed)) <= 1e-7


def test_installation_preserves_ordinary_single_deck_method():
    class Core(_FakeCore):
        pass

    install_island_set(Core)
    core, _ = _fixture()
    core.__class__ = Core
    assert hasattr(Core, "_single_deck_render_mashup")
    result = Core._single_deck_render_mashup(core, "ordinary", Path("ordinary.wav"))
    assert result["presented"] is True
