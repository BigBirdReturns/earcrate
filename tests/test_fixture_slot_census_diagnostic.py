"""Diagnostic census composition: the 23-of-32 witness family.

Stage 2C stopped indeterminate because the census probe composed the ordinary
skeleton from the same deficient restricted pool the census was invoked to
repair: when the restricted deck's surviving sources cannot meet the
composer's distinct-source turnover requirement, the observer died before
measuring any slots, even though it had already computed the wider
campaign-universe deck. These gates witness the narrow correction:

  1. the census first attempts the restricted ordinary composition;
  2. only the specific TasteSpec distinct-source turnover refusal, with its
     counts confirmed against the independently measured restricted deck,
     opens the diagnostic path, which composes from the already computed
     campaign-universe exact-deck pool;
  3. the diagnostic census records the mandated evidence fields and is
     marked diagnostic_only_no_publication;
  4. every other composer exception remains an indeterminate census failure;
  5. qualification over a diagnostic census stays ordinary and strict.
"""
from __future__ import annotations

import copy

import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate
from earcrate.plan.islands import (
    allocate_phrase_aligned_islands,
    source_pool_identity,
)

NEED_SOURCES = 3


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
    """Two decks; island a's allowlist survives 2/3 at its own deck."""
    return [
        _atom("s1", "a1", "bass", 120.0, 0),
        _atom("s2", "a2", "floor", 120.0, 0),
        # s3 is on island a's allowlist but survives only at deck b.
        _atom("s3", "a3", "foreground", 130.0, 5),
        # s4 is on island b's allowlist and also survives at deck a.
        _atom("s4", "a4", "foreground", 120.0, 0),
        _atom("s4", "a5", "bass", 130.0, 5),
        _atom("s5", "a6", "floor", 130.0, 5),
        _atom("s5", "a8", "bass", 130.0, 5),
        _atom("s6", "a7", "foreground", 130.0, 5),
        _atom("s6", "a9", "foreground", 130.0, 5),
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
        "duration_s": 20.0,
        "islands": [
            {
                "island_id": "a",
                "deck_id": "deck-a",
                "target_bpm": 120.0,
                "target_key": 0,
                "capacity_s": 20.0,
                "source_include_ids": ["s1", "s2", "s3"],
                "required_roles": [],
                "min_sources": 1,
                "max_sources": 6,
            },
            {
                "island_id": "b",
                "deck_id": "deck-b",
                "target_bpm": 130.0,
                "target_key": 5,
                "capacity_s": 20.0,
                "source_include_ids": ["s4", "s5", "s6"],
                "required_roles": [],
                "min_sources": 1,
                "max_sources": 6,
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
    campaign = slot_binding.build_fixture_slot_census_campaign(
        Core(pool), _request(_candidate(pool))
    )

    # The restricted composition ran first and refused; only then did the
    # diagnostic path compose island a from the campaign-universe deck.
    assert Core.calls[0] == ("a", ("s1", "s2"))
    assert Core.calls[1] == ("a", ("s1", "s2", "s4"))
    assert ("b", ("s4", "s5", "s6")) in Core.calls
    assert len(Core.calls) == 3

    assert campaign["diagnostic_island_ids"] == ["a"]
    by_island = {row["island_id"]: row for row in campaign["islands"]}
    assert "diagnostic_composition" not in by_island["b"]
    diagnostic = by_island["a"]["diagnostic_composition"]
    assert diagnostic["bypassed_precondition"] == (
        "tastespec_distinct_source_turnover"
    )
    assert diagnostic["restricted_surviving_source_count"] == 2
    assert diagnostic["required_turnover_source_count"] == NEED_SOURCES
    assert diagnostic["campaign_universe_surviving_source_count"] == 3
    assert diagnostic["deck_id"] == "deck-a"
    assert diagnostic["render_bpm"] == 120.0
    assert diagnostic["target_key"] == 0
    assert diagnostic["allocated_duration_s"] == float(
        by_island["a"]["allocated_duration_s"]
    )
    assert diagnostic["composer"]["entrypoint"] == (
        "compose_taste_arrangement"
    )
    assert diagnostic["composer"]["engine_version"]
    assert diagnostic["disposition"] == "diagnostic_only_no_publication"
    assert diagnostic["refusal_message"].startswith(
        "TasteSpec deck infeasible:"
    )

    # The diagnostic evidence is bound into the sealed identities.
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
        _turnover_message(2, NEED_SOURCES, 120.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            subclassed(pool), _request(_candidate(pool))
        )
    except Refusal:
        pass
    else:
        raise AssertionError("a RuntimeError subclass opened the bypass")


def test_bypass_requires_arithmetic_agreement_with_measured_deck():
    pool = _pool()
    lying = _census_core_class()
    # The message claims one surviving source; the measured restricted deck
    # for island a has two, so the bypass must stay closed.
    lying.turnover_error = RuntimeError(
        _turnover_message(1, NEED_SOURCES, 120.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            lying(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert "keeps 1/3" in str(exc)
    else:
        raise AssertionError("a count-mismatched refusal opened the bypass")

    satisfied = _census_core_class()
    # A turnover-shaped message where surviving >= required is not the
    # turnover refusal at all; the bypass must stay closed.
    satisfied.turnover_error = RuntimeError(
        _turnover_message(NEED_SOURCES, NEED_SOURCES, 120.0, 0)
    )
    try:
        slot_binding.build_fixture_slot_census_campaign(
            satisfied(pool), _request(_candidate(pool))
        )
    except RuntimeError as exc:
        assert f"keeps {NEED_SOURCES}/{NEED_SOURCES}" in str(exc)
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
    # The repartition the diagnostic census makes visible: s3 can only be
    # represented at deck b, s4 is the only source that can hold island a's
    # observed foreground slot.
    assert partition == {"a": ["s1", "s2", "s4"], "b": ["s3", "s5", "s6"]}
    assert result["moved_source_count"] == 2
    assert result["candidate"]["fixture_sha256"] != candidate["fixture_sha256"]
    qualification = result["candidate"]["fixture_slot_qualification"]
    assert qualification["scope"] == "one_observed_skeleton_round_replan_required"
    assert qualification["impossibility_claimed"] is False
