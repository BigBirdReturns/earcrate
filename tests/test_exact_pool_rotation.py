"""Regression witnesses for Proof-005 exact-pool source rotation.

The private failure was structural: a restricted exact-deck pool could contain
role-valid sources that never entered the arrangement, while one selected source
continued past the existing 12-event veto. These fixtures reproduce that shape
without private identities or media.
"""
from collections import Counter
from collections.abc import Mapping
import copy
import importlib.util
import json
from pathlib import Path

from earcrate.plan import source_rotation
from earcrate.plan.exact_pool_assignment import ExactPoolAssignmentError
from earcrate.plan.source_rotation import (
    ExactPoolRotationError,
    install_exact_pool_rotation,
    rebalance_exact_pool_sources,
)


def _atom(source, role, index, score=0.8, key_root=0):
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
        "key_root": key_root,
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


# ---------------------------------------------------------------------------
# Issue #121 witnesses: seed-order exact-pool failures must become deterministic
# complete assignments. Every fixture below is built here; none of them touches
# the sealed Season-001 evidence or names a private source.
# ---------------------------------------------------------------------------

SEED = 679133


def _scene(rows, pool, cap, target_key=0):
    """Build an exact-island arrangement from ``[(role, occupying_atom), ...]`` rows."""
    sections = []
    for index, row in enumerate(rows):
        sections.append({
            "bar_start": index * 4,
            "bars": 4,
            "type": "sustain",
            "target_key": target_key,
            "transition_in": {"type": "start" if index == 0 else "beatmatch_blend"},
            "layers": [_layer(item, role) for role, item in row],
        })
    arrangement = {
        "bpm": 120.0,
        "target_key": target_key,
        "seed": SEED,
        "params": {},
        "sections": sections,
        "taste_ledger": {},
    }
    params = {
        "island_id": "island-007",
        "exact_target_bpm": 120.0,
        "exact_target_key": target_key,
        "stretch_budget": 8.0,
        "pitch_shift_budget": 2,
        "exact_pool_max_source_events": cap,
    }
    return list(pool), arrangement, params


def _slot_map(arrangement):
    """Slot identity is musical position, so it survives section reordering."""
    return {
        (int(section["bar_start"]), layer_index): layer["source_track_key"]
        for section in arrangement["sections"]
        for layer_index, layer in enumerate(section["layers"])
    }


def _singleton_block_scene(rider_role="drum_anchor"):
    """The Season-001 shape: the only bass slot is held by a singleton donor.

    ``flex`` sits alone in the bass slot, so the greedy ``count <= 1`` guard makes
    that slot invisible and the bass-only source can never be reached — even
    though ``flex`` has a floor atom and a floor slot is free to take it.
    """
    flex_bass = _atom("flex", "bass", 0)
    flex_floor = _atom("flex", "drum_anchor", 1)
    hold = [_atom("hold", "drum_anchor", index) for index in range(3)]
    bass_only = _atom("bass-only", "bass", 0)
    rider = _atom("rider", rider_role, 0)
    # A 'bass' rider is the flexible one: it can play the contested bass slot or
    # step aside onto the floor. A 'drum_anchor' rider has no bass atom at all.
    extra = [_atom("rider", "drum_anchor", 1)] if rider_role == "bass" else []
    pool = [flex_bass, flex_floor, bass_only, rider, *extra, *hold]
    rows = [
        [("bass", flex_bass), ("drum_anchor", hold[0])],
        [("drum_anchor", hold[1]), ("drum_anchor", hold[2])],
    ]
    return _scene(rows, pool, cap=3)


def _greedy_refusal(pool, arrangement, params):
    """Prove the first authority really does refuse this fixture."""
    try:
        source_rotation._depth_one_fast_path(_Core(), arrangement, pool, params, SEED)
    except ExactPoolRotationError as exc:
        return exc
    raise AssertionError("fixture does not reproduce a greedy exact-pool refusal")


def test_repair_reaches_a_bass_only_source_past_a_singleton_donor():
    """Witness 1 — Season-001 failure shape."""
    pool, arrangement, params = _singleton_block_scene()
    _greedy_refusal(pool, arrangement, params)

    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    slots = _slot_map(result)
    assert slots[(0, 0)] == "bass-only"
    assert set(slots.values()) == {"flex", "hold", "bass-only", "rider"}
    assert result["taste_ledger"]["exact_pool_assignment"]["singleton_donor_relocation_count"] >= 1


def test_repair_does_not_let_a_flexible_source_eat_a_constrained_source_slot():
    """Witness 2 — multiple missing sources, order-independent completion."""
    pool, arrangement, params = _singleton_block_scene(rider_role="bass")
    _greedy_refusal(pool, arrangement, params)

    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    slots = _slot_map(result)
    # 'rider' and 'flex' can both play bass; 'bass-only' cannot play anything
    # else, so the single bass slot must be spent on it.
    assert slots[(0, 0)] == "bass-only"
    assert set(slots.values()) == {"flex", "hold", "bass-only", "rider"}

    reversed_pool = list(reversed(pool))
    again = rebalance_exact_pool_sources(_Core(), arrangement, reversed_pool, params, SEED)
    assert _slot_map(again) == slots


def _permuted(pool, arrangement):
    """Four equivalent inputs at once: source order, atom order, section order, key order."""
    shuffled_pool = [
        {key: item[key] for key in reversed(list(item))}
        for item in reversed(pool)
    ]
    reordered = copy.deepcopy(arrangement)
    reordered["sections"] = list(reversed(reordered["sections"]))
    return shuffled_pool, reordered


def _canonical_form(result):
    """The whole repaired object, keyed by musical position rather than declaration order."""
    body = {key: value for key, value in result.items() if key != "sections"}
    body["sections_by_bar"] = {
        str(int(section["bar_start"])): section for section in result["sections"]
    }
    return json.dumps(body, sort_keys=True, default=str)


def test_repair_is_identical_under_equivalent_input_permutations():
    """Witness 3 — the receipt is a measurement, so compare all of it.

    Slot identity alone is too weak a claim to seal into provenance: a receipt keyed
    on declaration order can differ while the music is identical. This compares every
    layer body at equal musical position, the repair ledger including every
    replacement record, and the assignment ledger.
    """
    for scene in (_singleton_block_scene, _matched_occurrence_cap_scene, _cap_chain_scene):
        pool, arrangement, params = scene()
        baseline = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
        shuffled_pool, reordered = _permuted(pool, arrangement)
        variant = rebalance_exact_pool_sources(_Core(), reordered, shuffled_pool, params, SEED)

        assert _slot_map(variant) == _slot_map(baseline), scene.__name__
        assert variant["taste_ledger"] == baseline["taste_ledger"], scene.__name__
        assert _canonical_form(variant) == _canonical_form(baseline), scene.__name__


def test_repair_is_not_invoked_when_the_greedy_path_already_succeeds():
    """Witness 4 — successful-path byte identity and a frozen legacy ledger."""
    pool, arrangement, params = _fixture()
    greedy = source_rotation._depth_one_fast_path(_Core(), arrangement, pool, params, 413676)
    through_wrapper = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, 413676)

    assert json.dumps(through_wrapper, sort_keys=False) == json.dumps(greedy, sort_keys=False)
    assert "exact_pool_assignment" not in through_wrapper["taste_ledger"]
    ledger = through_wrapper["taste_ledger"]["exact_pool_rotation"]
    assert ledger["version"] == source_rotation.EXACT_POOL_ROTATION_VERSION
    assert ledger["input_order_independent"] is True


def _cap_chain_scene():
    """An over-cap donor whose only route to spare capacity runs through a full receiver."""
    donor = [_atom("donor", "bass", index) for index in range(3)]
    middle_bass = _atom("middle", "bass", 0)
    middle_floor = [_atom("middle", "drum_anchor", index) for index in (1, 2)]
    tail = _atom("tail", "drum_anchor", 0)
    pool = [*donor, middle_bass, *middle_floor, tail]
    rows = [
        [("bass", donor[0]), ("bass", donor[1])],
        [("bass", donor[2]), ("drum_anchor", middle_floor[0])],
        [("drum_anchor", middle_floor[1]), ("drum_anchor", tail)],
    ]
    return _scene(rows, pool, cap=2)


def test_repair_holds_the_reuse_cap():
    """Witness 5 — cap preservation."""
    pool, arrangement, params = _cap_chain_scene()
    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    counts = Counter(_source_sequence(result))
    assert max(counts.values()) <= 2
    assert result["taste_ledger"]["exact_pool_rotation"]["observed_max_source_events"] <= 2


def test_cap_repair_traverses_a_receiver_that_is_itself_full():
    """Witness 6 — the cap path is atomic, not depth-1."""
    pool, arrangement, params = _cap_chain_scene()
    refusal = _greedy_refusal(pool, arrangement, params)
    assert "no compatible under-cap source" in str(refusal)

    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    counts = Counter(_source_sequence(result))
    assert set(counts) == {"donor", "middle", "tail"}
    assert max(counts.values()) <= 2
    paths = result["taste_ledger"]["exact_pool_assignment"]["cap_relief_paths"]
    assert paths and any(path["traversed_full_receiver"] for path in paths)
    # 'middle' was already at the cap and still passed an event along, so its own
    # count is unchanged while the singleton 'tail' is the source that receives.
    assert counts["middle"] == 2
    assert counts["tail"] == 2


def test_repair_never_assigns_a_source_outside_its_role():
    """Witness 7 — role honesty."""
    pool, arrangement, params = _singleton_block_scene()
    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    by_atom = {item["atom_id"]: item for item in pool}
    for section in result["sections"]:
        for layer in section["layers"]:
            chosen = by_atom[layer["atom_id"]]
            assert chosen["source_track_key"] == layer["source_track_key"]
            assert source_rotation._role_compatible(layer["role"], chosen)


def test_repair_refuses_a_source_that_is_transform_unsafe_at_the_exact_deck():
    """Witness 8 — transform honesty; an unsafe source never buys an edge."""
    flex_bass = _atom("flex", "bass", 0, key_root=6)
    flex_floor = _atom("flex", "drum_anchor", 1, key_root=6)
    hold = [_atom("hold", "drum_anchor", index, key_root=6) for index in range(3)]
    # Bass is pitched, so it carries the key budget: six semitones away with a
    # two-semitone budget, and no varispeed to absorb it because the fixture is
    # already at the render tempo. The bass slot is role-compatible and stays
    # transform-unsafe, which is exactly the edge the solver must not invent.
    off_key = _atom("off-key", "bass", 0, key_root=0)
    pool = [flex_bass, flex_floor, off_key, *hold]
    rows = [
        [("bass", flex_bass), ("drum_anchor", hold[0])],
        [("drum_anchor", hold[1]), ("drum_anchor", hold[2])],
    ]
    pool, arrangement, params = _scene(rows, pool, cap=3, target_key=6)
    _greedy_refusal(pool, arrangement, params)

    try:
        rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    except ExactPoolAssignmentError as exc:
        assert exc.deficiency["failure_class"] == "transform_safety"
        assert "off-key" in exc.deficiency["unmatched_sources"]
    else:
        raise AssertionError("a transform-unsafe source must refuse, not acquire an edge")


def test_repair_requires_stable_identity_and_ignores_local_filesystem_path():
    """Witness 9 — identity is stable keys; a local path decides nothing."""
    pool, arrangement, params = _singleton_block_scene()
    baseline = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)

    repathed = []
    for index, item in enumerate(pool):
        moved = dict(item)
        moved["path"] = f"S:/relocated/{len(pool) - index:04d}.wav"
        repathed.append(moved)
    assert _slot_map(rebalance_exact_pool_sources(_Core(), arrangement, repathed, params, SEED)) == _slot_map(baseline)

    unstable = []
    for item in pool:
        stripped = dict(item)
        stripped.pop("source_track_key", None)
        stripped.pop("source_id", None)
        unstable.append(stripped)
    try:
        rebalance_exact_pool_sources(_Core(), arrangement, unstable, params, SEED)
    except ExactPoolRotationError as exc:
        assert getattr(exc, "deficiency", {}).get("failure_class") == "stable_identity_absent"
    else:
        raise AssertionError("assignment without a stable source identity must fail closed")


def test_repair_refuses_a_true_capacity_deficit_with_a_hall_witness():
    """Witness 10 — real impossibility, proved rather than blamed on visit order."""
    flex_bass = _atom("flex", "bass", 0)
    flex_floor = _atom("flex", "drum_anchor", 1)
    hold = [_atom("hold", "drum_anchor", index) for index in range(3)]
    first = _atom("bass-only-a", "bass", 0)
    second = _atom("bass-only-b", "bass", 0)
    pool = [flex_bass, flex_floor, first, second, *hold]
    rows = [
        [("bass", flex_bass), ("drum_anchor", hold[0])],
        [("drum_anchor", hold[1]), ("drum_anchor", hold[2])],
    ]
    pool, arrangement, params = _scene(rows, pool, cap=3)

    try:
        rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    except ExactPoolAssignmentError as exc:
        witness = exc.deficiency["hall_witness"]
        assert exc.deficiency["failure_class"] == "role_capacity"
        assert witness["deficiency"] >= 1
        assert witness["deficient_source_count"] > witness["neighbourhood_slot_count"]
        assert exc.deficiency["unmatched_source_count"] >= 1
        assert "unreachable" in str(exc)
    else:
        raise AssertionError("two bass-only sources cannot share one bass slot")


def test_repair_leaves_non_exact_island_composition_untouched():
    """Witness 11 — ordinary-path stability."""

    class Core(_Core):
        def compose_taste_arrangement(self, pool, params, seed):
            return {"ordinary": True, "seed": seed, "pool": [item["id"] for item in pool]}

    install_exact_pool_rotation(Core)
    core = Core()
    pool, _arrangement, params = _singleton_block_scene()
    single_deck = core.compose_taste_arrangement(pool, {"target_seconds": 120}, 11)
    assert single_deck == {"ordinary": True, "seed": 11, "pool": [item["id"] for item in pool]}

    # An island without an exact deck is not an exact-pool call either.
    partial = dict(params)
    partial.pop("exact_target_key")
    assert core.compose_taste_arrangement(pool, partial, 11)["ordinary"] is True


# ---------------------------------------------------------------------------
# Review corrections: coupled cap-and-occurrence choice, jointly modified section
# compatibility, whole-receipt permutation equality, and provenance resolution.
# ---------------------------------------------------------------------------


def _matched_occurrence_cap_scene():
    """A bounded assignment that requires moving the occurrence coverage matched.

    Two role families that never meet. The bass/floor half reproduces the Season-001
    refusal so the fast path declines. The vocal/spark half is the coupled case:
    ``lead`` is the only vocal source, so both vocal slots are its and its cap of two
    is already spent there, while its third occurrence is a spark atom that outranks
    its own vocal atoms. Any decomposition that matches each source to its best-ranked
    held slot and then relieves the cap *around* those matches therefore pins exactly
    the occurrence that has to move: the two vocal slots reach no other source, so
    relief has nowhere to go and refuses ``cap_constraint`` while this assignment
    exists. Coverage and the cap have to be solved together, in one search.
    """
    flex_bass = _atom("flex", "bass", 0)
    flex_floor = _atom("flex", "drum_anchor", 1)
    hold = [_atom("hold", "drum_anchor", index) for index in range(2)]
    bass_only = _atom("bass-only", "bass", 0)
    lead = [_atom("lead", "vocal", index) for index in range(2)]
    lead_spark = _atom("lead", "texture", 2, score=0.95)
    spark = _atom("spark", "texture", 0)
    pool = [flex_bass, flex_floor, *hold, bass_only, *lead, lead_spark, spark]
    rows = [
        [("bass", flex_bass), ("drum_anchor", hold[0])],
        [("drum_anchor", hold[1])],
        [("vocal", lead[0]), ("texture", lead_spark)],
        [("vocal", lead[1]), ("texture", spark)],
    ]
    return _scene(rows, pool, cap=2)


def test_repair_moves_a_matched_occurrence_to_hold_the_cap():
    """Witness 12 — cap relief may move the occurrence coverage matched."""
    pool, arrangement, params = _matched_occurrence_cap_scene()
    _greedy_refusal(pool, arrangement, params)

    result = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    slots = _slot_map(result)
    counts = Counter(_source_sequence(result))

    assert set(counts) == {"flex", "hold", "bass-only", "lead", "spark"}
    assert max(counts.values()) <= 2
    # Both vocal slots can only be 'lead', so its floor occurrence is the one that
    # must give way — and it is the occurrence the coverage matching selected.
    assert slots[(8, 0)] == "lead" and slots[(12, 0)] == "lead"
    assert slots[(8, 1)] != "lead"
    assert slots[(0, 0)] == "bass-only"

    assignment = result["taste_ledger"]["exact_pool_assignment"]
    assert assignment["matched_occurrence_relocation_count"] >= 1
    released = [
        hop
        for path in assignment["cap_relief_paths"]
        for hop in path["hops"]
        if (hop["bar_start"], hop["layer_index"]) == (8, 1)
    ]
    assert released and released[0]["from_source"] == "lead"


class _VetoCore(_Core):
    """A core that refuses one pairing however it is reached.

    Compatibility is the core's to decide, and it decides on the pair actually
    published — so a fixture can make two candidates each admissible against the layer
    they replace and inadmissible against each other. The preference bump is what
    makes the pair *attractive*: both vetoed sources score highest against the bar-8
    counterparts, so any preference-following assignment reaches for that section
    first.
    """

    VETOED_PAIR = {"solo", "echo"}
    CONTESTED_COUNTERPARTS = {"atom-twin-1", "atom-twin-4"}

    def atom_edge_score(self, left, right, relation, render_bpm, target_key, stretch_budget, pitch_budget):
        pair = {left.get("source_track_key"), right.get("source_track_key")}
        if pair == self.VETOED_PAIR:
            return 0.0, {"vetoed": True, "relation": relation}
        if right.get("atom_id") in self.CONTESTED_COUNTERPARTS:
            # Only the two vetoed sources want the contested section; everyone else
            # would rather be anywhere else. Preference, not luck, sends them there.
            if left.get("source_track_key") in self.VETOED_PAIR:
                return 0.9, {"fixture": True, "relation": relation, "contested": True}
            return 0.6, {"fixture": True, "relation": relation, "contested": True}
        return 0.75, {"fixture": True, "relation": relation}


def _jointly_modified_section_scene():
    """A section whose two layers are both replaced in one assignment.

    ``twin`` holds all six vocal and floor slots against a cap of two, so four of them
    change hands at once. ``solo`` and ``echo`` both score highest against the bar-12
    layers, so both reach for that one section. Scored against the frozen snapshot each
    is admissible against the layer it replaces; together they are not. Only the
    finished section shows that, and there is room for them apart — ``duet`` and
    ``sheen`` are the alternatives a search that keeps going will find.

    The bass/spark half is disjoint in role family and only exists to make the
    depth-one fast path decline, so the solver is the thing under test.
    """
    flex_bass = _atom("flex", "bass", 0)
    flex_spark = _atom("flex", "texture", 1)
    rest = [_atom("rest", "texture", index) for index in range(2)]
    bass_only = _atom("bass-only", "bass", 0)
    twin_vocal = [_atom("twin", "vocal", index) for index in range(3)]
    twin_floor = [_atom("twin", "drum_anchor", index) for index in range(3, 6)]
    solo = _atom("solo", "vocal", 0)
    duet = _atom("duet", "vocal", 0)
    echo = _atom("echo", "drum_anchor", 0)
    sheen = _atom("sheen", "drum_anchor", 0)
    pool = [flex_bass, flex_spark, *rest, bass_only, *twin_vocal, *twin_floor, solo, duet, echo, sheen]
    rows = [
        [("bass", flex_bass), ("texture", rest[0])],
        [("texture", rest[1])],
        [("vocal", twin_vocal[0]), ("drum_anchor", twin_floor[0])],
        [("vocal", twin_vocal[1]), ("drum_anchor", twin_floor[1])],
        [("vocal", twin_vocal[2]), ("drum_anchor", twin_floor[2])],
    ]
    return _scene(rows, pool, cap=2)


def _published_pair_scores(core, result, pool):
    """Re-judge every published layer against the counterpart it actually sits with."""
    by_atom = {item["atom_id"]: item for item in pool}
    by_loop = {item["id"]: item for item in pool}
    judged = []
    for section in result["sections"]:
        for layer_index, layer in enumerate(section["layers"]):
            candidate = by_atom[layer["atom_id"]]
            role = str(layer["role"])
            transform = source_rotation._transform_for_slot(candidate, role, 120.0, 0, {"stretch_budget": 8.0, "pitch_shift_budget": 2})
            score = None
            if transform is not None:
                score = source_rotation._candidate_score(
                    core, candidate, section, layer_index, role, transform,
                    120.0, 0, {"stretch_budget": 8.0, "pitch_shift_budget": 2},
                    by_atom, by_loop, SEED,
                )
            judged.append(((int(section["bar_start"]), layer_index), score))
    return judged


def test_repair_validates_the_section_pair_it_publishes():
    """Witness 13 — two layers admissible apart, inadmissible together."""
    pool, arrangement, params = _jointly_modified_section_scene()
    core = _VetoCore()
    _greedy_refusal(pool, arrangement, params)

    result = rebalance_exact_pool_sources(core, arrangement, pool, params, SEED)
    slots = _slot_map(result)
    counts = Counter(_source_sequence(result))
    assert set(counts) == {"flex", "rest", "bass-only", "twin", "solo", "duet", "echo", "sheen"}
    assert max(counts.values()) <= 2

    # The vetoed pair never reaches a section, and every published layer is
    # admissible against the counterpart it was actually published with.
    for section in result["sections"]:
        sources = {layer["source_track_key"] for layer in section["layers"]}
        assert sources != _VetoCore.VETOED_PAIR
    for slot_key, score in _published_pair_scores(core, result, pool):
        assert score is not None, slot_key

    # The first assignment did pair them: the authority found that out before
    # publishing and kept searching rather than committing.
    assignment = result["taste_ledger"]["exact_pool_assignment"]
    assert assignment["final_pair_revisions"] >= 1
    forbidden = assignment["forbidden_final_pairs"]
    assert forbidden and forbidden[0]["source"] in _VetoCore.VETOED_PAIR
    assert forbidden[0]["counterpart_source"] in _VetoCore.VETOED_PAIR


def _witness_references(node):
    """Every ``tests/...::name`` string anywhere inside a provenance structure."""
    found = set()
    if isinstance(node, str):
        if node.startswith("tests/") and "::" in node:
            found.add(node)
    elif isinstance(node, Mapping):
        for value in node.values():
            found |= _witness_references(value)
    elif isinstance(node, (list, tuple, set)):
        for value in node:
            found |= _witness_references(value)
    return found


def test_provenance_witnesses_resolve_to_discovered_tests():
    """Witness 14 — a receipt may not name a witness that does not exist.

    Provenance is only worth what it points at. Every witness reference the module
    publishes, and every one it seals into an emitted receipt, has to resolve to a
    real test the gate runner would discover.
    """
    from earcrate.plan import exact_pool_assignment

    pool, arrangement, params = _singleton_block_scene()
    receipt = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
    references = _witness_references(vars(exact_pool_assignment))
    references |= _witness_references(receipt["taste_ledger"]["exact_pool_assignment"])
    assert len(references) >= len(exact_pool_assignment.PROVENANCE_WITNESSES)
    assert references >= set(exact_pool_assignment.PROVENANCE_WITNESSES)

    here = Path(__file__).resolve()
    root = here.parent.parent
    for reference in sorted(references):
        relative, _, name = reference.partition("::")
        path = (root / relative).resolve()
        assert path.is_file(), f"{reference} names a file that does not exist"
        if path == here:
            discovered = globals()
        else:
            spec = importlib.util.spec_from_file_location(f"_witness_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            discovered = vars(module)
        assert name.startswith("test_"), f"{reference} is not a discoverable gate name"
        assert callable(discovered.get(name)), f"{reference} names a test that does not exist"
