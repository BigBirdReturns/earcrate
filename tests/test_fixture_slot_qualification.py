from __future__ import annotations

import copy
import hashlib
import json

from earcrate.plan.fixture_diversity import fixture_projection
from earcrate.plan.fixture_slot_qualification import (
    INDETERMINATE_ACTION,
    attach_slot_census_to_error,
    qualify_fixture_candidate,
    slot_census_from_arrangement,
)


def _candidate(parts):
    islands, cursor = [], 0.0
    for index, (deck, sources) in enumerate(parts):
        islands.append({
            "island_id": f"island-{index}", "deck_id": deck,
            "target_bpm": 100.0 + index, "target_key": index,
            "capacity_s": 100.0, "allocated_duration_s": 100.0,
            "start_s": cursor, "end_s": cursor + 100.0,
            "source_include_ids": list(sources),
            "required_roles": ["foreground", "floor", "bass"],
            "min_sources": 1, "max_sources": 99,
        })
        cursor += 100.0
    value = {
        "kind": "earcrate_fixture_candidate", "schema_version": 1,
        "profile": "girl_talk_v1", "persona": "remix_prettylights_v1",
        "phrase_playback_law": "proof001_phrase_law",
        "source_pool_sha256": "pool-fixture", "source_exclude_ids": [],
        "transform_policy": {"identity": "tf", "unchanged": True, "stretch_budget": 8.0, "pitch_shift_budget": 2},
        "turnover_policy": {"identity": "turn", "unchanged": True},
        "transition": {"technique": "equal_power", "phrase_boundary_required": True},
        "duration_s": cursor, "phrase_bars": 4, "seed": 17,
        "islands": islands, "transitions": [],
    }
    value["fixture_sha256"] = fixture_projection(value)["fixture_identity"]
    value["fixture_id"] = "fixture-" + value["fixture_sha256"][:12]
    return value


def _matrix(decks):
    return {
        "duration_s": 200.0, "island_count": len(decks), "phrase_bars": 4,
        "candidate_count": 3, "base_seed": 11, "max_attempts": 64,
        "required_roles": ["foreground", "floor", "bass"],
        "request_template": {
            "profile": "girl_talk_v1", "source_pool_sha256": "pool-fixture",
            "persona": "remix_prettylights_v1", "phrase_playback_law": "proof001_phrase_law",
            "transform_policy": {"identity": "tf", "unchanged": True, "stretch_budget": 8.0, "pitch_shift_budget": 2},
            "turnover_policy": {"identity": "turn", "unchanged": True},
            "transition": {"technique": "equal_power", "phrase_boundary_required": True},
            "source_exclude_ids": [],
        },
        "decks": [
            {
                "deck_id": deck, "target_bpm": 100.0 + index, "target_key": index,
                "capacity_s": 200.0,
                "sources": [{"source_id": source, "roles": sorted(roles)} for source, roles in sources.items()],
                "required_roles": ["foreground", "floor", "bass"],
                "min_sources": 1, "max_sources": 99,
            }
            for index, (deck, sources) in enumerate(decks.items())
        ],
    }


def _census(candidate, families):
    rows = []
    for index, values in enumerate(families):
        rows.append({
            "island_id": f"island-{index}",
            "slots": [
                {"slot_key": f"{slot}:0", "bar_start": slot, "layer_index": 0, "role_family": family}
                for slot, family in enumerate(values)
            ],
        })
    value = {"candidate_fixture_sha256": candidate["fixture_sha256"], "islands": rows}
    value["slot_census_family_sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def _repair_fixture():
    decks = {
        "deck-a": {
            "bass-only": {"bass"}, "vocal": {"foreground"},
            "floor-a": {"floor"}, "flex": {"floor", "foreground"},
        },
        "deck-b": {
            "bass-only": {"bass"}, "vocal": {"foreground"},
            "floor-a": {"floor"}, "flex": {"floor", "foreground"},
        },
    }
    candidate = _candidate([
        ("deck-a", ["bass-only", "vocal"]),
        ("deck-b", ["floor-a", "flex"]),
    ])
    census = _census(candidate, [
        ["foreground", "floor", "floor"],
        ["bass", "floor", "foreground"],
    ])
    return decks, candidate, census


def test_slot_qualification_moves_a_bass_only_source_to_a_bass_slot():
    decks, candidate, census = _repair_fixture()
    receipt = qualify_fixture_candidate(
        _matrix(decks), candidate, census,
        max_source_events=3, max_anchor_rounds=64,
    )
    assert receipt["complete"] is True
    first, second = receipt["qualified_candidate"]["islands"]
    assert "bass-only" not in first["source_include_ids"]
    assert "bass-only" in second["source_include_ids"]
    assert receipt["source_universe_preserved"] is True
    assert receipt["deck_sequence_preserved"] is True
    assert receipt["duration_program_preserved"] is True


def test_slot_qualification_moves_floor_capacity_under_the_existing_cap():
    decks = {
        "deck-a": {"floor-1": {"floor"}, "floor-2": {"floor"}, "floor-3": {"floor"}, "vocal": {"foreground"}},
        "deck-b": {"floor-1": {"floor"}, "floor-2": {"floor"}, "floor-3": {"floor"}, "vocal": {"foreground"}},
    }
    candidate = _candidate([
        ("deck-a", ["floor-1", "vocal"]),
        ("deck-b", ["floor-2", "floor-3"]),
    ])
    receipt = qualify_fixture_candidate(
        _matrix(decks), candidate,
        _census(candidate, [["floor"] * 5, ["foreground", "floor"]]),
        max_source_events=3,
    )
    assert receipt["complete"] is True
    first, second = receipt["qualified_candidate"]["islands"]
    assert all(source.startswith("floor-") for source in first["source_include_ids"])
    assert "vocal" in second["source_include_ids"]


def test_true_role_deficit_carries_a_max_flow_min_cut_proof():
    decks = {
        "deck-a": {"bass-only": {"bass"}, "floor": {"floor"}},
        "deck-b": {"bass-only": {"bass"}, "floor": {"floor"}},
    }
    candidate = _candidate([("deck-a", ["bass-only"]), ("deck-b", ["floor"])])
    receipt = qualify_fixture_candidate(
        _matrix(decks), candidate, _census(candidate, [["floor"], ["floor"]])
    )
    assert receipt["complete"] is False
    assert receipt["impossibility_claimed"] is True
    assert receipt["evidence_class"] == "max_flow_min_cut"
    assert receipt["deficiency"] == 1
    assert "bass-only" in receipt["reachable_sources"]


def test_anchor_round_budget_is_a_bound_not_an_impossibility_claim():
    decks, candidate, census = _repair_fixture()
    receipt = qualify_fixture_candidate(
        _matrix(decks), candidate, census, max_anchor_rounds=0
    )
    assert receipt["complete"] is False
    assert receipt["impossibility_claimed"] is False
    assert receipt["private_acceptance"] == INDETERMINATE_ACTION
    assert receipt["evidence_class"] == "anchor_matching_round_bound"


def test_slot_qualification_is_independent_of_matrix_and_slot_order():
    decks, candidate, census = _repair_fixture()
    first = qualify_fixture_candidate(_matrix(decks), candidate, census, max_source_events=3)
    matrix = _matrix(decks)
    matrix["decks"].reverse()
    for deck in matrix["decks"]:
        deck["sources"].reverse()
    other_census = copy.deepcopy(census)
    for island in other_census["islands"]:
        island["slots"].reverse()
    second = qualify_fixture_candidate(matrix, candidate, other_census, max_source_events=3)
    assert first["qualified_fixture_sha256"] == second["qualified_fixture_sha256"]
    assert first["qualified_candidate"]["islands"] == second["qualified_candidate"]["islands"]


def test_slot_census_excludes_source_and_atom_identity():
    arrangement = {
        "bpm": 120.0, "target_key": 0,
        "sections": [{
            "bar_start": 4, "type": "drop",
            "layers": [
                {"role": "bass", "source_track_key": "private-source"},
                {"role": "texture", "atom_id": "private-atom"},
            ],
        }],
    }
    census = slot_census_from_arrangement(arrangement, island_id="island-x")
    text = json.dumps(census, sort_keys=True)
    assert census["role_family_counts"] == {"bass": 1, "spark": 1}
    assert "private-source" not in text and "private-atom" not in text


def test_exact_pool_refusal_is_enriched_without_reclassifying_it():
    class Refusal(RuntimeError):
        def __init__(self):
            super().__init__("no assignment")
            self.deficiency = {"impossibility_claimed": True}
    refusal = Refusal()
    returned = attach_slot_census_to_error(
        refusal,
        {"bpm": 120.0, "target_key": 0, "sections": [{"bar_start": 0, "layers": [{"role": "vocal"}]}]},
        {"island_id": "island-x", "source_pool_sha256": "pool"},
    )
    assert returned is refusal
    assert refusal.deficiency["impossibility_claimed"] is True
    assert refusal.deficiency["slot_census"]["role_family_counts"] == {"foreground": 1}
