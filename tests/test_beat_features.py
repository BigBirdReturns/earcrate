"""Gates for Step-2 per-beat features (earcrate/analyze/beat_features.py).

Real DSP, run in-cloud on SYNTHETIC signals with known content: a kick loop must
read as kick (not vocal/bass), a sustained tone as bass or voice (not a phantom
kick), silence as nothing, a tonal tone as more tonally-confident than noise, and
an on-beat pattern as un-syncopated. Determinism is asserted byte-for-byte.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.analyze.beat_features import (
    beat_activity, groove_descriptor, local_harmony, beat_state_features,
)

SR = 22050
BPM = 120.0
BEAT = 60.0 / BPM
DUR = 4.0
N = int(SR * DUR)
T = np.arange(N) / SR
BEATS = np.arange(0.0, DUR, BEAT)
DOWNBEATS = BEATS[::4]


def _kick_loop():
    y = np.zeros(N)
    env = np.exp(-np.arange(0, int(0.08 * SR)) / (0.02 * SR))
    tone = np.sin(2 * np.pi * 60 * np.arange(env.size) / SR)
    hit = env * tone
    for bt in BEATS:
        i = int(bt * SR)
        seg = hit[: max(0, min(hit.size, N - i))]
        y[i:i + seg.size] += seg
    return (y / (np.max(np.abs(y)) + 1e-9)).astype(np.float32)


def test_kick_loop_reads_as_kick_not_voice():
    a = beat_activity(_kick_loop(), SR, BEATS)
    kick, vocal, bass = np.mean(a["kick"]), np.mean(a["vocal"]), np.mean(a["bass"])
    assert kick > 0.5, kick
    assert kick > vocal and kick > bass, (kick, vocal, bass)
    assert vocal < 0.1, vocal  # a 60 Hz kick is not a vocal


def test_sustained_bass_tone_is_bass_not_a_phantom_kick():
    a = beat_activity(0.5 * np.sin(2 * np.pi * 90 * T).astype(np.float32), SR, BEATS)
    assert np.mean(a["bass"]) > 0.5, a["bass"][:4]
    assert np.mean(a["kick"]) < 0.15, "a steady tone must not read as a transient kick"


def test_vocal_band_tone_reads_as_vocal():
    y = (0.4 * (np.sin(2 * np.pi * 500 * T) + 0.5 * np.sin(2 * np.pi * 1000 * T))).astype(np.float32)
    a = beat_activity(y, SR, BEATS)
    assert np.mean(a["vocal"]) > 0.4
    assert np.mean(a["vocal"]) > np.mean(a["bass"])
    assert np.mean(a["kick"]) < 0.15


def test_silence_reads_as_no_activity():
    a = beat_activity(np.zeros(N, dtype=np.float32), SR, BEATS)
    for role, vals in a.items():
        assert max(vals) < 1e-3, (role, max(vals))


def test_activity_is_bounded_and_beat_aligned():
    a = beat_activity(_kick_loop(), SR, BEATS)
    for role, vals in a.items():
        assert len(vals) == len(BEATS) - 1, (role, len(vals))
        assert all(0.0 <= v <= 1.0 for v in vals), role


def test_on_beat_loop_is_not_syncopated():
    g = groove_descriptor(_kick_loop(), SR, BEATS, DOWNBEATS)
    on = sum(g["onset_histogram"][i] for i in (0, 4, 8, 12))
    off = 1.0 - on
    assert on > off, (on, off)              # energy concentrates on the quarter-note grid
    assert g["syncopation"] < 0.5, g["syncopation"]
    assert len(g["onset_histogram"]) == 16
    assert 0.0 <= g["swing_ratio"] <= 1.0


def test_local_harmony_tonal_beats_noise():
    tone = (0.5 * np.sin(2 * np.pi * 220 * T)).astype(np.float32)
    rng = np.random.default_rng(0)
    noise = (0.3 * rng.standard_normal(N)).astype(np.float32)
    ht = local_harmony(tone, SR, BEATS)
    hn = local_harmony(noise, SR, BEATS)
    assert ht and hn
    assert all(set(w) >= {"start_s", "key_root", "key_mode", "tonal_confidence"} for w in ht)
    assert np.mean([w["tonal_confidence"] for w in ht]) > np.mean([w["tonal_confidence"] for w in hn])


def test_beat_state_is_deterministic_and_serializable():
    y = _kick_loop()
    s1 = beat_state_features(y, SR, BEATS, DOWNBEATS)
    s2 = beat_state_features(y.copy(), SR, BEATS, DOWNBEATS)
    a = json.dumps(s1, sort_keys=True)
    assert a == json.dumps(s2, sort_keys=True)
    assert s1["n_beats"] == len(BEATS) - 1
    assert set(s1["roles"]) == {"kick", "bass", "snare", "vocal", "lead", "hat"}


def test_short_audio_degrades_without_crashing():
    tiny = np.zeros(256, dtype=np.float32)
    assert beat_activity(tiny, SR, BEATS) == {r: [] for r in
        ("kick", "bass", "snare", "vocal", "lead", "hat")}
    assert local_harmony(tiny, SR, BEATS) == []
    g = groove_descriptor(tiny, SR, BEATS, DOWNBEATS)
    assert g["onset_histogram"] == [0.0] * 16
