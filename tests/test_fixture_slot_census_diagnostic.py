"""Diagnostic census composition and exact turnover-refusal verification.

Stage 2C stopped because the census tried to compose the same deficient
candidate-restricted pool it was commissioned to repair.  The observer may use
the campaign-universe deck only after the ordinary restricted attempt produces
the exact distinct-source turnover refusal.  Both counts in that refusal are
verified independently: surviving sources come from the measured restricted
deck, and required sources come from the active TasteSpec, persona override, and
allocated island duration.
"""
from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from earcrate.plan.islands import (
    allocate_phrase_aligned_islands,
    source_pool_identity,
)

NEED_SOURCES = 5


def _atom(source, atom, role, bpm, key):
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
        "ear_role": ear,
        "render_role": render,
        "role": render,
        "bpm": bpm,
        "key_root": key,
        "bars": 4,
        "score": 0.8,
        "hook_score": 0.7,
        "high_share": 0.1,
        "path": f"X:/private/{source}.wav",
        "artist": "private",
        "title": source,
    }


def _pool():
    """Two decks; island a's allowlist survives 4/5 at its exact deck.

    Source s5 belongs to island a but survives only at deck b.  Source s9
    belongs to island b and survives both decks, with a floor atom at deck a
    and a foreground atom at deck b.  The diagnostic census therefore exposes
    the lawful s5/s9 exchange without changing the ten-source universe.
    """
    return [
        _atom("s1", "a1", "bass", 130.0, 0),
        _atom("s2", "a2", "floor", 130.0, 0),
        _atom("s3", "a3", "floor", 130.0, 0),
        _atom("s4", "a4", "bass", 130.0, 0),
        _atom("s5", "a5", "foreground", 120.0, 5),
        _atom("s6", "a6", "bass", 120.0, 5),
        _atom("s7", "a7", "floor", 120.0, 5),
        _atom("s8", "a8", "bass", 120.0, 5),
        _atom("s9", "a9", "floor", 130.0, 0),
        _atom("s9", "a10", "foreground", 120.0, 5),
        _atom("s10", "a11", "floor", 120.0, 5),
    ]


def _turnover_message(surviving, required, bpm, key):
    return (
        f"TasteSpec deck infeasible: best deck ({bpm} BPM, key {key}) "
        f"keeps {surviving}/{required} distinct playable sources; the crate "
        "needs more sources that survive transform at a common tempo"
    )


def _census_core_class():
    class Core:
        calls = []
        turnover_error = None

        def __init__(self, pool):
            self.pool = list(pool)

        def approved_atom_pool(self, _profile):
            return list(self.pool)

        def taste_feasible_pool(self, pool, bpm, key, _params):
            rows = [
                dict(item)
                for item in pool
                if abs(float(item.get("bpm") or 0.0) - float(bpm)) < 1e-9
                and int(item.get("key_root") or 0) % 12 == int(key) % 12
            ]
            return rows, {
                "pool_size": len(rows),
                "have": {
                    "sources": len(
                        {row["source_track_key"] for row in rows}
                    )
                },
            }

        def atom_edge_score(self, _candidate, _counterpart, _relation, *_a):
            return 0.8, {"reason": "fixture"}

        def _ordinary_compose_taste_arrangement(self, pool, params, seed):
            distinct = sorted({item["source_track_key"] for item in pool})
            Core.calls.append(
                (str(params.get("island_id") or ""), tuple(distinct))
            )
            if len(distinct) < NEED_SOURCES:
                error = Core.turnover_error
                if error is not None:
                    raise error
                raise RuntimeError(
                    _turnover_message(
                        len(distinct),
                        NEED_SOURCES,
                        params["exact_target_bpm"],
                        params["exact_target_key"],
                    )
                )
            layers = [
                {
                    "loop_id": item["id"],
                    "atom_id": item["atom_id"],
                    "ear_role": item["ear_role"],
                    "role": item["render_role"],
                    "source_track_key": item["source_track_key"],
                    "bar_offset": 0,
                    "bar_len": 4,
                    "gain_db": -8.0,
                }
                for item in pool
            ]
            return {
                "bpm": float(params["exact_target_bpm"]),
                "target_key": int(params["exact_target_key"]),
                "seed": seed,
                "params": dict(params),
                "dj_compiler": {"version": "fixture"},
                "sections": [
                    {
                        "bar_start": 0,
                        "bars": 4,
                        "type": "sustain",
                        "target_key": int(params["exact_target_key"]),
                        "layers": layers,
                    }
                ],
            }

    return Core


def _candidate(pool):
    candidate = {
        "kind": "earcrate_fixture_candidate",
        "fixture_id": "pending",
        "fixture_sha256": "pending",
        "profile": "girl_talk_v1",
        "persona": "remix_prettylights_v1",
        "phrase_playback_law": "proof001_phrase_law",
        "source_pool_sha256": source_pool_identity(pool, ()),
        "transform_policy": {
            "unchanged": True,
            "stretch_budget": 8.0,
            "pitch_shift_budget": 2,
        },
        "turnover_policy": {"unchanged": True},
        "transition": {
            "technique": "equal_power",
            "phrase_boundary_required": True,
        },
        "seed": 7,
        "duration_s": 80.0,
        "islands": [
            {
                "island_id": "a",
                "deck_id": "deck-a",
                "target_bpm": 130.0,
                "target_key": 0,
                "capacity_s": 60.0,
                "source_include_ids": ["s1", "s2", "s3", "s4", "s5"],
                "required_roles": [],
                "min_sources": 1,
                "max_sources": 10,
            },
            {
                "island_id": "b",
                "deck_id": "deck-b",
                "target_bpm": 120.0,
                "target_key": 5,
                "capacity_s": 32.0,
                "source_include_ids": ["s6", "s7", "s8", "s9", "s10"],
                "required_roles": [],
                "min_sources": 1,
                "max_sources": 10,
            },
        ],
        "transitions": [],
    }
    allocated, _transitions, _net = allocate_phrase_aligned_islands(
        candidate["islands"], float(candidate["duration_s"])
    )
    by_id = {str(row["island_id"]): row for row in allocated}
    for island in candidate["islands"]:
        island["allocated_duration_s"] = float(
            by_id[str(island["island_id"])]["allocated_duration_s"]
        )
    from earcrate.plan.fixture_diversity import fixture_projection

    identity = str(fixture_projection(candidate)["fixture_identity"])
    candidate["fixture_id"] = f"fixture-{identity[:12]}"
    candidate["fixture_sha256"] = identity
    return candidate


def _request(candidate):
    request = {
        key: copy.deepcopy(candidate[key])
        for key in (
            "profile",
            "persona",
            "phrase_playback_law",
            "seed",
            "duration_s",
            "source_pool_sha256",
            "transform_policy",
            "turnover_policy",
            "transition",
            "islands",
        )
    }
    request["fixture_sha256"] = candidate["fixture_sha256"]
    return request


def test_turnover_refusal_opens_diagnostic_composition_with_full_evidence():
    pool = _pool()
    Core = _census_core_class()
    candidate = _candidate(pool)

    from earcrate.core.deps import TASTE_PROFILES
    from earcrate.plan.math import sources_needed

    allocated = float(candidate["islands"][0]["allocated_duration_s"])
    assert sources_needed(
        allocated,
        float(TASTE_PROFILES["remix_prettylights_v1"]["source_seconds"]),
    ) == NEED_SOURCES
    assert sources_needed(
        allocated,
        float(TASTE_PROFILES["girl_talk_v1"]["source_seconds"]),
    ) == NEED_SOURCES + 1

    campaign = slot_binding.build_fixture_slot_census_campaign(
        Core(pool), _request(candidate)
    )

    assert Core.calls[0] == ("a", ("s1", "s2", "s3", "s4"))
    assert Core.calls[1] == ("a", ("s1", "s2", "s3", "s4", "s9"))
    assert Core.calls[2] == ("b", ("s10", "s6", "s7", "s8", "s9"))
    assert len(Core.calls) == 3

    assert campaign["diagnostic_island_ids"] == ["a"]
    by_island = {row["island_id"]: row for row in campaign["islands"]}
    assert "diagnostic_composition" not in by_island["b"]
    diagnostic = by_island["a"]["diagnostic_composition"]
    assert diagnostic["bypassed_precondition"] == (
        "tastespec_distinct_source_turnover"
    )
    assert diagnostic["restricted_surviving_source_count"] == 4
    assert diagnostic["required_turnover_source_count"] == NEED_SOURCES
    assert diagnostic["campaign_universe_surviving_source_count"] == 5
    assert diagnostic["deck_id"] == "deck-a"
    assert diagnostic["render_bpm"] == 130.0
    assert diagnostic["target_key"] == 0
    assert diagnostic["allocated_duration_s"] == allocated
    assert diagnostic["composer"]["entrypoint"] == (
        "compose_taste_arrangement"
    )
    assert diagnostic["composer"]["engine_version"]
    assert diagnostic["disposition"] == "diagnostic_only_no_publication"
    assert diagnostic["refusal_message"].startswith(
        "TasteSpec deck infeasible:"
    )

    assert by_island["a"]["slot_census_sha256"] == (
        slot_binding._census_identity(by_island["a"])
    )
    assert campaign["campaign_sha256"] == (
        slot_binding._campaign_identity(campaign)
    )
    stripped = copy.deepcopy(by_island["a"])
    del stripped["diagnostic_composition"]
    assert slot_binding._census_identity(stripped) != (
        by_island["a"]["slot_census_sha256"]
    )


def test_other_composer_exceptions_remain_indeterminate_census_failures():
    pool = _pool()

    operational = _census_core_class()
    operational.turnover_error = RuntimeError("compose backend unavailable")
    try:
        slot_binding.build_fixture_slot_census_campaign(
            operational(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert str(exc) == "compose backend unavailable"
    else:
        raise AssertionError("operational composer failure was bypassed")

    class Refusal(RuntimeError):
        pass

    subclassed = _census_core_class()
    subclassed.turnover_error = Refusal(
        _turnover_message(4, NEED_SOURCES, 130.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            subclassed(pool), _request(_candidate(pool))
        )
    except Refusal:
        pass
    else:
        raise AssertionError("a RuntimeError subclass opened the bypass")


def test_bypass_requires_both_counts_to_match_independent_authorities():
    pool = _pool()

    lying_survival = _census_core_class()
    lying_survival.turnover_error = RuntimeError(
        _turnover_message(3, NEED_SOURCES, 130.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            lying_survival(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert "keeps 3/5" in str(exc)
    else:
        raise AssertionError("a surviving-count lie opened the bypass")

    lying_requirement = _census_core_class()
    lying_requirement.turnover_error = RuntimeError(
        _turnover_message(4, 999, 130.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            lying_requirement(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert "keeps 4/999" in str(exc)
    else:
        raise AssertionError("an unverified required count opened the bypass")

    satisfied = _census_core_class()
    satisfied.turnover_error = RuntimeError(
        _turnover_message(4, 4, 130.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            satisfied(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert "keeps 4/4" in str(exc)
    else:
        raise AssertionError("a satisfied turnover message opened the bypass")


def test_diagnostic_census_supports_one_ordinary_qualification_round():
    pool = _pool()
    Core = _census_core_class()
    candidate = _candidate(pool)
    campaign = slot_binding.build_fixture_slot_census_campaign(
        Core(pool), _request(candidate)
    )
    assert campaign["diagnostic_island_ids"] == ["a"]

    result = qualify_fixture_candidate(candidate, campaign)
    assert result["complete"] is True
    partition = {
        row["island_id"]: sorted(row["source_include_ids"])
        for row in result["candidate"]["islands"]
    }
    assert partition == {
        "a": ["s1", "s2", "s3", "s4", "s9"],
        "b": ["s10", "s5", "s6", "s7", "s8"],
    }
    assert result["moved_source_count"] == 2
    assert result["candidate"]["fixture_sha256"] != candidate["fixture_sha256"]
    qualification = result["candidate"]["fixture_slot_qualification"]
    assert qualification["scope"] == "one_observed_skeleton_round_replan_required"
    assert qualification["impossibility_claimed"] is False
