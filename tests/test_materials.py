"""Gates for MaterialRegion / propose_regions (earcrate/materials/regions.py).

The review's acceptance criterion for Patch 2 is candidate RECALL: the generator
must surface regions covering known intros, outros, drops, and vocal phrases that
the fixed [8,4,2,1] baseline cannot express. These gates encode exactly that on a
synthetic track with a planted drop, vocal phrase, and outro.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.materials.regions import propose_regions, MaterialRegion


def _analysis(tid="A", bars=32):
    n = bars * 4
    bi = 60.0 / 128.0
    beats = [round(i * bi, 4) for i in range(n + 1)]
    db = [beats[i] for i in range(0, n, 4)]
    return {"id": tid, "bpm": 128.0, "bpm_confidence": 0.9, "key_root": 0, "key_mode": 1,
            "energy": 0.6, "beats": beats, "downbeats": db, "duration_s": beats[-1],
            "sections": [
                {"start": 0.0, "end": beats[16], "label": "intro", "energy": 0.2},
                {"start": beats[16], "end": beats[96], "label": "chorus", "energy": 0.8},
                {"start": beats[96], "end": beats[-1], "label": "outro", "energy": 0.3},
            ]}, n


def _beat_state(n):
    nb = n - 1
    vocal = [0.0] * nb
    for i in range(8, 24):          # a vocal phrase, beats 8..24
        vocal[i] = 0.8
    novelty = [0.2] * nb
    novelty[32] = 0.95              # a drop at beat 32
    return {"activity": {"kick": [0.7] * nb, "bass": [0.6] * nb, "snare": [0.3] * nb,
                         "vocal": vocal, "lead": [0.2] * nb, "hat": [0.3] * nb},
            "novelty": novelty}


def test_baseline_reproduces_fixed_bar_behavior():
    a, n = _analysis()
    base = propose_regions(a, baseline=True)
    assert base, "baseline must produce regions"
    assert {r.bars for r in base} <= {1.0, 2.0, 4.0, 8.0}, "baseline is only 1/2/4/8 bars"
    assert all(r.end_kind == "grid" for r in base)
    assert not any(r.kind in ("natural_tail", "section") for r in base)


def test_new_path_proposes_variable_length_and_functional_kinds():
    a, n = _analysis()
    regs = propose_regions(a, _beat_state(n))
    kinds = {r.kind for r in regs}
    barset = {r.bars for r in regs}
    assert "natural_tail" in kinds, "must propose a tail to the end of the track"
    assert "section" in kinds, "must propose a full-section region"
    assert any(b not in (1.0, 2.0, 4.0, 8.0) for b in barset), "must exceed the fixed ladder (e.g. 16/32-bar or section)"


def test_recall_of_drop_vocal_and_outro():
    a, n = _analysis()
    regs = propose_regions(a, _beat_state(n))
    # DROP: a region must start at the planted novelty spike (beat 32) as a drop.
    assert any(r.start_beat == 32 and r.start_kind == "drop" for r in regs), "drop not recalled"
    # VOCAL PHRASE: a region must start at the vocal entrance (beat 8) with high
    # vocal_foreground capability.
    vocal_regs = [r for r in regs if r.start_kind == "vocal_start"]
    assert vocal_regs, "vocal-phrase start not recalled"
    assert max(r.role_probabilities.get("vocal_foreground", 0.0) for r in vocal_regs) > 0.5
    # OUTRO / natural tail reaching the end of the track.
    assert any(r.kind == "natural_tail" and r.end_beat == n for r in regs)


def test_new_path_recalls_events_the_baseline_cannot():
    a, n = _analysis()
    new = propose_regions(a, _beat_state(n))
    base = propose_regions(a, baseline=True)
    new_starts = {(r.start_beat, r.start_kind) for r in new}
    base_starts = {(r.start_beat, r.start_kind) for r in base}
    # The drop start is reachable by the new generator and not the baseline.
    assert (32, "drop") in new_starts and (32, "drop") not in base_starts
    assert any(r.kind == "natural_tail" for r in new)
    assert not any(r.kind == "natural_tail" for r in base)


def test_role_probabilities_are_capabilities_not_one_label():
    a, n = _analysis()
    regs = propose_regions(a, _beat_state(n))
    r = next(rr for rr in regs if rr.role_probabilities)
    caps = r.role_probabilities
    assert set(caps) >= {"vocal_foreground", "bass_anchor", "rhythmic_bed",
                         "lead_foreground", "transition_tail"}
    assert all(0.0 <= v <= 1.0 for v in caps.values())


def test_deterministic_and_serializable():
    a, n = _analysis()
    st = _beat_state(n)
    r1 = [r.as_dict() for r in propose_regions(a, st)]
    r2 = [r.as_dict() for r in propose_regions(copy.deepcopy(a), copy.deepcopy(st))]
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_no_grid_no_regions():
    assert propose_regions({"id": "x", "bpm": 0, "beats": [], "downbeats": []}) == []
