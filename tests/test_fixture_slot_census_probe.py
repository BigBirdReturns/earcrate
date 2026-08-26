from __future__ import annotations

import copy

from earcrate.app import EarcrateCore
from earcrate.plan import fixture_slot_binding as slot_binding
from earcrate.plan import fixture_slot_qualification_core as slot_core
from earcrate.plan.fixture_slot_census_probe import PROBE_VERSION
from earcrate.plan.islands import ExactDeckProxy


_BPM = 117.45
_KEY = 1


def _atom(index: int, role: str):
    ear_role, render_role = {
        "foreground": ("VOX_HOOK", "vocal"),
        "floor": ("DRUM_BREAK", "drum_anchor"),
        "bass": ("BASS_RIFF", "bass"),
        "spark": ("DROP_HIT", "texture"),
    }[role]
    source = f"source-{index:02d}"
    atom = f"atom-{index:02d}"
    return {
        "id": atom,
        "atom_id": atom,
        "source_track_key": source,
        "artist": "fixture",
        "title": source,
        "ear_role": ear_role,
        "render_role": render_role,
        "role": render_role,
        "bpm": _BPM,
        "key_root": _KEY,
        "bars": 4,
        "start_s": 0.0,
        "end_s": 8.0,
        "score": 0.8,
        "hook_score": 0.8,
        "high_share": 0.1,
        "low_share": 0.1 if role != "bass" else 0.5,
        "source_audio_sha256": f"pcm-{index:02d}",
    }


def _pool():
    roles = (
        ["foreground"] * 8
        + ["floor"] * 8
        + ["bass"] * 5
        + ["spark"] * 2
    )
    return [_atom(index, role) for index, role in enumerate(roles)]


class _TurnoverCore:
    _ordinary_compose_taste_arrangement = (
        EarcrateCore._ordinary_compose_taste_arrangement
    )

    def taste_feasible_pool(self, pool, bpm, key, _params):
        rows = [
            dict(item)
            for item in pool
            if abs(float(item.get("bpm") or 0.0) - float(bpm)) < 1e-9
            and int(item.get("key_root") or 0) % 12 == int(key) % 12
        ]
        return rows, {
            "pool_size": len(rows),
            "render_bpm": float(bpm),
            "target_key": int(key) % 12,
            "have": {
                "sources": len(
                    {str(item["source_track_key"]) for item in rows}
                )
            },
        }

    def conn(self):
        raise RuntimeError("synthetic core has no judgment database")

    def atom_edge_score(
        self,
        _candidate,
        _counterpart,
        _relation,
        *_args,
    ):
        return 0.8, {"reason": "fixture"}

    def plan_transition(
        self,
        prev_sec,
        _sec_type,
        _prev_key,
        _next_key,
        _bar_start,
        _bars,
        _layers,
        _chaos,
        _drama,
        _rng,
    ):
        return {
            "type": "start" if prev_sec is None else "beatmatch_blend",
            "xfade_beats": 0 if prev_sec is None else 4,
        }

    def choose_target_key_for_pool(self, _pool):
        return _KEY


def _base_params():
    return {
        "taste_profile": "girl_talk_v1",
        "persona": "fixture-persona",
        "phrase_playback_law": "proof001_phrase_law",
        "stretch_budget": 8.0,
        "pitch_shift_budget": 2,
        "quality_mode": "stable_deck",
        "post_render_gate": True,
        "mix_mode": "tastespec_graph",
        "reuse_policy_override": {
            "source_seconds": 10.0,
        },
    }


def _row():
    return {
        "island_id": "deck2-117.45-k1",
        "target_bpm": _BPM,
        "target_key": _KEY,
        "allocated_duration_s": 320.0,
    }


def _ordinary_compose(core, pool, params):
    proxy = ExactDeckProxy(core, _BPM, _KEY)
    original = core._ordinary_compose_taste_arrangement
    compose = getattr(original, "__func__", original)
    return compose(proxy, copy.deepcopy(pool), copy.deepcopy(params), 7)


def test_slot_census_probe_observes_a_23_of_32_deck_without_weakening_product():
    core = _TurnoverCore()
    pool = _pool()
    base = _base_params()
    product_params = {
        **base,
        "target_seconds": 320.0,
        "bpm": _BPM,
        "exact_target_bpm": _BPM,
        "exact_target_key": _KEY,
        "island_id": _row()["island_id"],
    }
    try:
        _ordinary_compose(core, pool, product_params)
    except RuntimeError as exc:
        text = str(exc)
        assert "keeps 23/32 distinct playable sources" in text
    else:
        raise AssertionError(
            "ordinary product composition bypassed its turnover admission gate"
        )

    arrangement, compose_params = slot_core._raw_island_arrangement(
        core,
        pool,
        _row(),
        base,
        7,
    )
    observation = arrangement["slot_census_probe"]
    assert observation == {
        "version": PROBE_VERSION,
        "disposition": "diagnostic_only_no_publication",
        "bypassed_precondition": "taste_deck_distinct_playable_sources",
        "actual_distinct_playable_sources": 23,
        "required_distinct_playable_sources": 32,
        "exact_target_bpm": _BPM,
        "exact_target_key": _KEY,
        "pool_item_count": 23,
        "real_feasible_pool_unchanged": True,
        "ordinary_product_path_unchanged": True,
    }
    assert arrangement["sections"]

    census = slot_binding.build_exact_pool_slot_census(
        core,
        arrangement,
        pool,
        compose_params,
        7,
        island_id=_row()["island_id"],
    )
    assert census["diagnostic_skeleton_probe"] == observation
    assert census["slot_census_sha256"] == slot_binding._census_identity(
        census
    )
    assert census["slot_count"] > 0

    # The probe is private to census construction. Running the same ordinary
    # product composer again must still refuse at the original gate.
    try:
        _ordinary_compose(core, pool, product_params)
    except RuntimeError as exc:
        assert "keeps 23/32 distinct playable sources" in str(exc)
    else:
        raise AssertionError(
            "slot-census probing mutated ordinary product composition"
        )


def test_slot_census_probe_does_not_hide_an_empty_exact_deck():
    core = _TurnoverCore()
    pool = _pool()
    for item in pool:
        item["bpm"] = 90.0
    try:
        slot_core._raw_island_arrangement(
            core,
            pool,
            _row(),
            _base_params(),
            7,
        )
    except slot_core.FixtureSlotQualificationError as exc:
        assert "retains no material for slot census" in str(exc)
    else:
        raise AssertionError(
            "diagnostic probe invented a skeleton for an empty exact deck"
        )
