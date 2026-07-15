"""Gates for the external-target remix path: anchor inversion, a pinned continuous
vocal over a library bed, and the honest bed-feasibility verdict."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.app import EarcrateCore
from earcrate.remix.external import (remix_anchor, external_foreground_atom,
                                     external_vocal_window, external_remix_feasibility,
                                     fit_external_clip, external_edge_fades)


def configured_core(tmp_path: Path) -> EarcrateCore:
    master = tmp_path / "music"; work = tmp_path / "work"; agent = tmp_path / "agent"
    master.mkdir(); work.mkdir(); agent.mkdir()
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        c = EarcrateCore()
        c.configure({"master_root": str(master), "working_root": str(work), "agent_root": str(agent), "workers": 2})
    return c


# ---- pure: anchor is READ off the target, folded into a sane render range ----

def test_remix_anchor_reads_and_guards_tempo():
    # In-band, confident read: passes through untouched (root/mode/tempo).
    a = remix_anchor({"bpm": 96.0, "key_root": 7, "key_mode": 0, "key_confidence": 0.8})
    assert a["bpm"] == 96.0 and a["key_root"] == 7 and a["key_mode"] == 0
    assert a["anchor_source"]["bpm_from"] == "vocal" and a["anchor_source"]["key_from"] == "vocal"
    # A garbage/near-zero tempo (arrhythmic acapella) falls back to a usable grid.
    assert remix_anchor({"bpm": 0.0, "key_root": 13})["bpm"] == 120.0
    assert remix_anchor({"bpm": 0.0, "key_root": 13})["key_root"] == 1  # 13 % 12


# ---- F2: BPM octave disambiguation. A bare acapella's tempo estimate famously
#      doubles (76 -> a confident 152). The vocal-plausible band [60,120] must fold
#      that clean 2x back down — box-verified regression: v1 matched unconditionally
#      to the bed's tempo MEDIAN, which on a fast crate (girl_talk ~150 native) meant
#      a doubled 76->152 acapella matched the bed and the fold silently never fired.
#      The fold must not depend on what the bed's tempo happens to be. ----

def test_remix_anchor_bpm_folds_regardless_of_bed_tempo():
    # Bill Withers case, box-reproduced: vocal reads a confident 152 (2x of ~76). A
    # FAST crate (girl_talk-like, median ~150 -- the exact case v1 got wrong) must
    # NOT trap the anchor at 152 just because the bed lives there too.
    fast_bed = [148.0, 150.0, 152.0, 149.0, 151.0]
    a = remix_anchor({"bpm": 152.0, "key_root": 9, "key_mode": 0, "key_confidence": 0.9,
                      "bpm_confidence": 0.96}, bed_tempos=fast_bed)
    assert abs(a["bpm"] - 76.0) < 1e-6, a["bpm"]  # NOT 152 -- the v1 regression
    assert a["anchor_source"]["bpm_from"] == "vocal_band_fold"
    assert a["anchor_source"]["bpm_fold_choice"] == "single_plausible_octave"
    assert a["anchor_source"]["bpm_raw"] == 152.0
    assert a["anchor_source"]["bpm_fold_tested"] == [152.0, 76.0]


def test_remix_anchor_bpm_folds_without_bed():
    # No bed hints at all: still folds via the vocal-plausible band, same result.
    a = remix_anchor({"bpm": 152.0, "key_root": 9, "key_mode": 0, "key_confidence": 0.9})
    assert abs(a["bpm"] - 76.0) < 1e-6, a["bpm"]
    assert a["anchor_source"]["bpm_from"] == "vocal_band_fold"
    assert a["anchor_source"]["bpm_fold_choice"] == "single_plausible_octave"


def test_remix_anchor_bpm_slow_bed_also_folds_to_vocal_band():
    # And a SLOW bed (median ~76) gives the identical 76 result -- proving the fold
    # is driven by vocal plausibility, not by agreement with whatever bed happens to
    # be on hand (that dependency was the bug).
    slow_bed = [74.0, 76.0, 78.0, 75.0, 77.0]
    a = remix_anchor({"bpm": 152.0, "key_root": 9, "key_mode": 0, "key_confidence": 0.9},
                     bed_tempos=slow_bed)
    assert abs(a["bpm"] - 76.0) < 1e-6, a["bpm"]
    assert a["anchor_source"]["bpm_from"] == "vocal_band_fold"


def test_remix_anchor_bpm_multi_plausible_octave_uses_bed_tiebreak():
    # The one real ambiguous case: a very fast read (240) has TWO octaves that both
    # land in the vocal-plausible band (120 and 60). With a bed, the median tie-breaks.
    a_fast_bed = remix_anchor({"bpm": 240.0, "key_root": 0, "key_mode": 0, "key_confidence": 0.9},
                              bed_tempos=[112.0, 118.0, 120.0, 115.0])
    assert abs(a_fast_bed["bpm"] - 120.0) < 1e-6, a_fast_bed["bpm"]
    assert a_fast_bed["anchor_source"]["bpm_fold_choice"] == "bed_median_tiebreak"
    a_slow_bed = remix_anchor({"bpm": 240.0, "key_root": 0, "key_mode": 0, "key_confidence": 0.9},
                              bed_tempos=[58.0, 62.0, 60.0, 61.0])
    assert abs(a_slow_bed["bpm"] - 60.0) < 1e-6, a_slow_bed["bpm"]
    # Without a bed, prefer the smaller fold (halve once, not twice).
    a_no_bed = remix_anchor({"bpm": 240.0, "key_root": 0, "key_mode": 0, "key_confidence": 0.9})
    assert abs(a_no_bed["bpm"] - 120.0) < 1e-6, a_no_bed["bpm"]
    assert a_no_bed["anchor_source"]["bpm_fold_choice"] == "nearest_to_raw_tiebreak"


# ---- F2: key-confidence floor. A key pinned at ~0.2 is a guess. We must NOT transpose
#      a whole library bed to it — defer to the bed's own dominant key ----

def test_remix_anchor_guessed_key_defers_to_bed():
    # Vocal key is a 0.17-confidence guess (root 3); the bed is overwhelmingly root 8.
    bed_keys = [(8, 0.9), (8, 0.8), (8, 0.7), (3, 0.2), (1, 0.1)]
    a = remix_anchor({"bpm": 100.0, "key_root": 3, "key_mode": 1, "key_confidence": 0.17},
                     bed_keys=bed_keys)
    assert a["key_root"] == 8, a["key_root"]  # bed's dominant key wins, not the guess
    assert a["anchor_source"]["key_from"] == "bed_dominant"


def test_remix_anchor_confident_key_is_kept():
    # A confident key (0.85) is NOT overridden even when the bed leans elsewhere.
    bed_keys = [(8, 0.9), (8, 0.9), (8, 0.9)]
    a = remix_anchor({"bpm": 100.0, "key_root": 3, "key_mode": 1, "key_confidence": 0.85},
                     bed_keys=bed_keys)
    assert a["key_root"] == 3, a["key_root"]  # vocal key stands
    assert a["anchor_source"]["key_from"] == "vocal"


def test_remix_anchor_backward_compatible_no_hints():
    # Calling with no bed hints still returns every original key (nothing downstream breaks).
    a = remix_anchor({"bpm": 100.0, "key_root": 5, "key_mode": 1, "key_confidence": 0.6,
                      "bpm_confidence": 0.7, "vocal_likelihood": 0.8})
    for k in ("bpm", "key_root", "key_mode", "key_confidence", "bpm_confidence", "vocal_likelihood"):
        assert k in a, k
    assert a["bpm"] == 100.0 and a["key_root"] == 5 and a["key_mode"] == 1
    # Low-confidence key with no bed keeps the vocal key (marked, not transposed).
    b = remix_anchor({"bpm": 100.0, "key_root": 5, "key_mode": 1, "key_confidence": 0.1})
    assert b["key_root"] == 5 and b["anchor_source"]["key_from"] == "vocal"


def test_remix_anchor_is_deterministic():
    feats = {"bpm": 152.0, "key_root": 3, "key_mode": 0, "key_confidence": 0.15}
    bed_tempos = [96.0, 104.0, 110.0]
    bed_keys = [(8, 0.9), (8, 0.8), (3, 0.2)]
    first = remix_anchor(feats, bed_tempos=bed_tempos, bed_keys=bed_keys)
    for _ in range(5):
        assert remix_anchor(feats, bed_tempos=list(bed_tempos), bed_keys=list(bed_keys)) == first


# ---- pure: the dropped vocal atom is shaped so the composer rails it, and its
#      OWN transform is identity (bpm/key ARE the anchor) so it never degrades ----

def test_external_foreground_atom_is_identity_and_marked():
    anchor = remix_anchor({"bpm": 100.0, "key_root": 5, "key_mode": 1})
    atom = external_foreground_atom("My Take", anchor, 90.0, "abc123def456", "/x/take.wav")
    assert atom["is_external"] and atom["ear_role"] == "VOX_HOOK" and atom["role"] == "vocal"
    assert atom["bpm"] == 100.0 and atom["key_root"] == 5  # == anchor -> identity transform
    assert atom["external_ref"]["path"] == "/x/take.wav"
    assert atom["external_ref"]["duration_s"] == 90.0
    assert atom["low_share"] < 0.34  # keeps the bass gate open under the acapella
    # Deterministic: same inputs -> same atom id.
    again = external_foreground_atom("My Take", anchor, 90.0, "abc123def456", "/x/take.wav")
    assert again["id"] == atom["id"]


# ---- pure: the continuous take plays front-to-back; windows advance, clamp, and
#      go None once the vocal is spent (bed continues instrumental) ----

def test_external_vocal_window_advances_and_ends():
    bpm = 120.0  # 1 bar = 2s, 4 bars = 8s
    w0 = external_vocal_window(0, 4, bpm, 30.0)
    w1 = external_vocal_window(4, 4, bpm, 30.0)
    assert w0 == (0.0, 8.0) and w1 == (8.0, 8.0)  # linear progression
    # Section straddling the end gets only the remaining audio.
    tail = external_vocal_window(12, 4, bpm, 30.0)  # starts at 24s of a 30s vocal
    assert tail is not None and abs(tail[0] - 24.0) < 1e-6 and abs(tail[1] - 6.0) < 1e-6
    # Past the end -> None: the bed plays on with no vocal.
    assert external_vocal_window(16, 4, bpm, 30.0) is None


# ---- pure: a continuous take is NEVER loop-tiled (a short final window must not
#      stutter-echo the last words) and fades only at its true edges ----

def test_fit_external_clip_never_tiles():
    import numpy as np
    long = np.ones(1000, dtype=np.float32)
    assert fit_external_clip(long, 600).size == 600            # trim when long
    short = np.arange(400, dtype=np.float32)
    out = fit_external_clip(short, 1000)
    assert out.size == 400                                      # NO tiling: stays short
    assert np.array_equal(out, short)                           # and untouched
    exact = np.ones(512, dtype=np.float32)
    assert fit_external_clip(exact, 512).size == 512


def test_external_edge_fades_only_at_true_edges():
    dur = 30.0
    # First window of the take: fade in, no fade out (take continues).
    fi, fo = external_edge_fades(0, 0, 0.0, 8.0, dur)
    assert fi and not fo
    # Interior window: NO fades — a 14ms dip in a held word every 4 bars is audible.
    fi, fo = external_edge_fades(0, 1, 8.0, 8.0, dur)
    assert not fi and not fo
    # Final window (reaches the end of the vocal): fade out.
    fi, fo = external_edge_fades(0, 3, 24.0, 6.0, dur)
    assert fo
    # Unknown duration: fade out defensively.
    assert external_edge_fades(0, 1, 8.0, 8.0, 0.0)[1] is True
    # Mid-section entry fades in regardless of position.
    assert external_edge_fades(4410, 2, 16.0, 8.0, dur)[0] is True


# ---- pure: honest buildability at the anchor ----

def test_external_remix_feasibility_verdict():
    ok = external_remix_feasibility({"have": {"floor": 6, "bass": 2, "spark": 3, "sources": 4},
                                     "render_bpm": 100.0, "target_key": 5}, needed_sources=3)
    assert ok["buildable"] and any("bed OK" in r for r in ok["reasons"])
    no_floor = external_remix_feasibility({"have": {"floor": 0, "bass": 1, "spark": 0, "sources": 3}}, 2)
    assert not no_floor["buildable"] and any("no structural bed" in r for r in no_floor["reasons"])
    thin = external_remix_feasibility({"have": {"floor": 3, "bass": 0, "spark": 0, "sources": 1}}, 4)
    assert not thin["buildable"] and any("drone" in r for r in thin["reasons"])


# ---- integration: the composer pins the anchor to the target, rides ONE external
#      vocal across the whole track, and pulls the bed from the library pool ----

def _bed(atom_id, ear_role, role, key_root, bpm, artist):
    return {"id": atom_id, "atom_id": atom_id, "ear_role": ear_role, "role": role,
            "render_role": role, "key_root": key_root, "key_mode": 1, "bpm": bpm,
            "score": 0.8, "hook_score": 0.4, "high_share": 0.3, "low_share": 0.4,
            "artist": artist, "title": ear_role.lower()}


def test_compose_pins_anchor_and_rides_external_vocal(tmp_path):
    core = configured_core(tmp_path)
    anchor = remix_anchor({"bpm": 96.0, "key_root": 2, "key_mode": 1})
    ext = external_foreground_atom("Dropped", anchor, 40.0, "sha_ext_0001", str(tmp_path / "v.wav"))
    # A library bed at the SAME anchor (identity-playable): two distinct floor sources
    # + a bass, so feasibility clears and the composer has real material to rotate.
    pool = [
        _bed("b1", "DRUM_BREAK", "drum_anchor", 2, 96.0, "LibA"),
        _bed("b2", "BED_CHORD", "harmony", 2, 96.0, "LibB"),
        _bed("b3", "BASS_RIFF", "bass", 2, 96.0, "LibC"),
    ]
    params = {"taste_profile": "girl_talk_v1", "target_seconds": 40.0,
              "pin_bpm": anchor["bpm"], "pin_key": anchor["key_root"],
              "external_foreground": ext}
    arr = core.compose_taste_arrangement(pool, params, seed=7)
    # Anchor inversion: the render is pinned to the target, not searched.
    assert abs(arr["bpm"] - 96.0) < 1e-6 and arr["target_key"] == 2
    secs = arr["sections"]
    assert secs, "must produce sections"
    # Every section (while the 40s vocal lasts) carries the external vocal, and only
    # ONE external source is ever used (it never rotates to a library vox).
    vocal_secs = [s for s in secs if any(l.get("external_ref") for l in s["layers"])]
    assert len(vocal_secs) >= 1
    starts = []
    for s in vocal_secs:
        vlayers = [l for l in s["layers"] if l.get("external_ref")]
        assert len(vlayers) == 1
        er = vlayers[0]["external_ref"]
        assert er["path"] == str(tmp_path / "v.wav") and "start_s" in er and "len_s" in er
        starts.append(er["start_s"])
    assert starts == sorted(starts), "vocal windows advance monotonically front-to-back"
    # The bed under the vocal is LIBRARY material, never the external source.
    bed_layers = [l for s in secs for l in s["layers"] if not l.get("external_ref")]
    assert bed_layers, "library bed must play under the vocal"
    assert all(l.get("source_track_key") != "external" for l in bed_layers)
