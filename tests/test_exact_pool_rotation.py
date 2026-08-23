"""Regression witnesses for Proof-005 exact-pool source rotation.

The private failure was structural: a restricted exact-deck pool could contain
role-valid sources that never entered the arrangement, while one selected source
continued past the existing 12-event veto. These fixtures reproduce that shape
without private identities or media.
"""
from collections import Counter
import copy
import json

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
        source_rotation._greedy_rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)
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


def test_repair_assignment_is_input_order_independent():
    """Witness 3 — reversing source, atom, section and mapping order changes nothing."""
    pool, arrangement, params = _singleton_block_scene()
    baseline = rebalance_exact_pool_sources(_Core(), arrangement, pool, params, SEED)

    shuffled_pool = [
        {key: item[key] for key in reversed(list(item))}
        for item in reversed(pool)
    ]
    reordered = copy.deepcopy(arrangement)
    reordered["sections"] = list(reversed(reordered["sections"]))
    variant = rebalance_exact_pool_sources(_Core(), reordered, shuffled_pool, params, SEED)

    assert _slot_map(variant) == _slot_map(baseline)
    assert (
        variant["taste_ledger"]["exact_pool_rotation"]["source_event_counts"]
        == baseline["taste_ledger"]["exact_pool_rotation"]["source_event_counts"]
    )


def test_repair_is_not_invoked_when_the_greedy_path_already_succeeds():
    """Witness 4 — successful-path byte identity and a frozen legacy ledger."""
    pool, arrangement, params = _fixture()
    greedy = source_rotation._greedy_rebalance_exact_pool_sources(_Core(), arrangement, pool, params, 413676)
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
