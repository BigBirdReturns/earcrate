"""Gates for the anchor-based transition foundation (earcrate/plan/transitions.py).

Deterministic, audio-free: every fixture is a plain analysis dict. These lock in
the Step-1 contract -- anchors carry functional meaning, duration is DERIVED by
the technique (not a fixed [8,4,2,1] ladder), stem-dependent techniques stay
honestly disabled until Step 2, and a low-confidence grid ABSTAINS from a fragile
blend down to a robust cut.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.plan.transitions import (
    Anchor, TECHNIQUES, track_anchors, derive_duration,
    generate_transition_candidates, best_transition,
)


def _track(bpm=120.0, bpm_conf=0.9, key_root=0, key_mode=1, energy=0.5, bars=32, tid="A"):
    bi = 60.0 / bpm
    n_beats = bars * 4
    beats = [round(i * bi, 4) for i in range(n_beats + 1)]
    downbeats = [beats[i] for i in range(0, n_beats, 4)]
    duration = beats[-1]
    sections = [
        {"start": 0.0, "end": downbeats[4], "label": "intro", "energy": 0.2},
        {"start": downbeats[4], "end": downbeats[12], "label": "verse", "energy": 0.4},
        {"start": downbeats[12], "end": downbeats[20], "label": "chorus", "energy": 0.8},
        {"start": downbeats[20], "end": duration, "label": "outro", "energy": 0.3},
    ]
    return {"id": tid, "bpm": bpm, "bpm_confidence": bpm_conf, "key_root": key_root,
            "key_mode": key_mode, "energy": energy, "beats": beats,
            "downbeats": downbeats, "sections": sections, "duration_s": duration}


def test_no_grid_yields_no_anchors_and_no_transition():
    t = {"id": "X", "bpm": 0, "bpm_confidence": 0, "beats": [], "downbeats": [], "sections": []}
    assert track_anchors(t) == {"entry": [], "exit": []}
    # An outgoing track with no grid cannot host a transition -> hold, don't force one.
    assert best_transition(t, _track()) is None


def test_anchors_carry_functional_kinds():
    an = track_anchors(_track())
    entry_kinds = {a.kind for a in an["entry"]}
    exit_kinds = {a.kind for a in an["exit"]}
    assert "clean_in" in entry_kinds and "section_in" in entry_kinds and "drop_in" in entry_kinds
    assert "clean_out" in exit_kinds and "outro_in" in exit_kinds
    # phrase alignment grades strength: a 16-bar boundary outranks a 4-bar one.
    clean_ins = [a for a in an["entry"] if a.kind == "clean_in"]
    s16 = next(a for a in clean_ins if a.beat_index == 0)
    s4 = next(a for a in clean_ins if a.beat_index == 4)  # 1 bar in, a 4-bar-grid downbeat
    assert s16.strength > s4.strength


def test_duration_is_derived_by_technique_not_a_fixed_ladder():
    techs = {t.name: t for t in TECHNIQUES}
    a_exit = Anchor("exit", "clean_out", 64, 32.0, 1.0, 0.9, 32.0, 32.0)
    b_entry = Anchor("entry", "clean_in", 0, 0.0, 1.0, 0.9, 32.0, 32.0)
    assert derive_duration(techs["hard_cut"], a_exit, b_entry, 0.9) == 0        # one downbeat
    assert derive_duration(techs["echo_out"], a_exit, b_entry, 0.9) == 4
    assert derive_duration(techs["echo_out"], a_exit, b_entry, 0.3) == 2        # shaky grid -> shorter
    assert derive_duration(techs["long_blend"], a_exit, b_entry, 0.9) == 32     # phrase-multiple, plenty of material
    # limited clean material shrinks the blend below the 32-bar want.
    a_short = Anchor("exit", "clean_out", 32, 16.0, 1.0, 0.9, 10.0, 10.0)
    assert derive_duration(techs["long_blend"], a_short, b_entry, 0.9) == 10


def test_stem_dependent_techniques_are_disabled_until_step2():
    names = {c.technique for c in generate_transition_candidates(_track(tid="A"), _track(tid="B"), {})}
    assert "bass_swap" not in names, "bass_swap needs per-beat stem activity (Step 2)"
    assert "double_drop" not in names, "double_drop needs per-beat stem activity (Step 2)"
    assert names, "something viable must still be generated"


def test_low_confidence_grid_abstains_from_fragile_blend():
    lo = {c.technique for c in generate_transition_candidates(_track(bpm_conf=0.3, tid="A"),
                                                              _track(bpm_conf=0.3, tid="B"), {})}
    assert "long_blend" not in lo, "a shaky grid must not attempt a long blend"
    assert "hard_cut" in lo, "the robust cut is the abstention path"
    hi = {c.technique for c in generate_transition_candidates(_track(bpm_conf=0.9, tid="A"),
                                                              _track(bpm_conf=0.9, tid="B"), {})}
    assert "long_blend" in hi, "a trustworthy grid unlocks the blend"


def test_tempo_warp_folds_octave_errors():
    # 60 vs 120 BPM is a half-tempo detection, not a real 2x warp -> blend allowed.
    fold = {c.technique for c in generate_transition_candidates(_track(bpm=120, tid="A"),
                                                               _track(bpm=60, tid="B"), {})}
    assert "long_blend" in fold
    # 120 vs 100 is a genuine ~17% warp -> blend blocked, cut survives.
    warp = {c.technique for c in generate_transition_candidates(_track(bpm=120, tid="A"),
                                                               _track(bpm=100, tid="C"), {})}
    assert "long_blend" not in warp and "hard_cut" in warp


def test_candidates_ranked_and_deterministic():
    a, b = _track(tid="A"), _track(tid="B")
    cs = generate_transition_candidates(a, b, {})
    assert [c.total_score for c in cs] == sorted((c.total_score for c in cs), reverse=True)
    c1 = [c.as_dict() for c in generate_transition_candidates(a, b, {})]
    c2 = [c.as_dict() for c in generate_transition_candidates(copy.deepcopy(a), copy.deepcopy(b), {})]
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_energy_intent_shapes_the_score():
    # An incoming louder section scores better when the set wants to LIFT than when
    # it wants to RELEASE -- energy trajectory is a real term, not similarity.
    a = _track(energy=0.3, tid="A")
    b = _track(energy=0.9, tid="B")
    lift = best_transition(a, b, {"energy_intent": "lift"})
    release = best_transition(a, b, {"energy_intent": "release"})
    assert lift.scores["energy"] > release.scores["energy"]


def _state(nb, bass, kick=0.8, vocal=0.1, nov=None):
    return {"activity": {"kick": [kick] * nb, "bass": bass, "snare": [0.1] * nb,
                         "vocal": [vocal] * nb, "lead": [0.1] * nb, "hat": [0.2] * nb},
            "novelty": nov if nov is not None else [0.2] * nb}


def _aligned_track(tid):
    bars = 16; n = bars * 4; bi = 60.0 / 128.0
    beats = [round(i * bi, 4) for i in range(n + 1)]
    db = [beats[i] for i in range(0, n, 4)]
    return {"id": tid, "bpm": 128.0, "bpm_confidence": 0.9, "key_root": 0, "key_mode": 1,
            "energy": 0.6, "beats": beats, "downbeats": db, "duration_s": beats[-1],
            "sections": [{"start": 0.0, "end": beats[8], "label": "intro", "energy": 0.2},
                         {"start": beats[8], "end": beats[-1], "label": "chorus", "energy": 0.8}]}, n - 1


def test_step2_state_unlocks_stem_techniques():
    (a, nb), (b, _) = _aligned_track("A"), _aligned_track("B")
    a_state = _state(nb, [0.8] * 8 + [0.1] * (nb - 8))      # bass exits at beat 8
    b_state = _state(nb, [0.1] * 16 + [0.8] * (nb - 16))    # clean drums, bass in later
    # Without state, stem techniques are impossible; with state they become reachable.
    # top_k is large so the rarer stem candidates are not truncated behind the many
    # cut/echo/blend candidates.
    without = {c.technique for c in generate_transition_candidates(a, b, {}, top_k=999)}
    with_st = {c.technique for c in generate_transition_candidates(a, b, {}, top_k=999,
                                                                   a_state=a_state, b_state=b_state)}
    assert "bass_swap" not in without and "double_drop" not in without
    assert "bass_swap" in with_st, "a bass exit + a clean drum entrance should enable a bass swap"


def test_role_collision_penalizes_bass_over_bass():
    (a, nb), (b, _) = _aligned_track("A"), _aligned_track("B")
    bassy = _state(nb, [0.9] * nb)
    clean = _state(nb, [0.05] * nb)
    def max_blend_collision(b_state):
        cs = [c for c in generate_transition_candidates(a, b, {}, top_k=64, a_state=bassy, b_state=b_state)
              if c.technique == "long_blend"]
        return max((c.scores["role_collision"] for c in cs), default=0.0)
    both_bassy = max_blend_collision(bassy)
    b_clean = max_blend_collision(clean)
    assert both_bassy > b_clean, (both_bassy, b_clean)
    assert both_bassy > 0.1, "two sustained bass lines across the overlap must be penalized"
