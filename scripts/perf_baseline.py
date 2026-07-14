#!/usr/bin/env python3
"""Deterministic, self-contained perf baseline for the v0.8.30 perf campaign.

Run with:  python scripts/perf_baseline.py

Measures three things the v0.8.30 commits touched:
  1. Per-track analyze cost (earcrate.analyze.features.analyze_file_worker),
     broken down into decode (decode_audio_with_full_sha) vs DSP
     (compute_pcm_features).
  2. Deck-search cost (earcrate.deck.lattice.score_bpm_lattice) over a
     synthetic ~2000-atom pool, cold vs warm, to show the
     plan_varispeed_transform memoization win.
  3. Beat-state DSP cost (earcrate.analyze.beat_features.beat_state_features)
     on a 120s signal.

No GPU, no network, no configured workspace: everything is synthesized with
numpy/soundfile and run against temp directories. Writes a JSON report to
scripts/perf_baseline_last.json alongside a human-readable table on stdout.
"""
from __future__ import annotations

import json
import os
import platform
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

for _thread_var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    # Deterministic single-threaded BLAS/numba, same reasoning as tests/run_gates.py:
    # a benchmark whose wall time depends on the caller's thread-count env is not
    # a baseline anyone can compare against.
    os.environ.setdefault(_thread_var, "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SR = 44100
DURATION_S = 120.0


def _synthesize_track(seed: int, duration_s: float = DURATION_S, sr: int = SR) -> np.ndarray:
    """Deterministic multi-band 'music-like' signal: kick pulses, a bassline,
    mid-range chords, and hi-hat noise bursts on a steady ~120 BPM grid."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * sr)
    t = np.arange(n, dtype=np.float64) / sr
    bpm = 118.0 + rng.uniform(-6, 6)
    beat_s = 60.0 / bpm
    y = np.zeros(n, dtype=np.float64)

    # Kick: a decaying 60Hz thump on every beat.
    beat_times = np.arange(0.0, duration_s, beat_s)
    for bt in beat_times:
        i0 = int(bt * sr)
        if i0 >= n:
            break
        env_len = min(int(0.15 * sr), n - i0)
        env = np.exp(-np.arange(env_len) / (0.03 * sr))
        y[i0:i0 + env_len] += 0.6 * env * np.sin(2 * np.pi * 60.0 * np.arange(env_len) / sr)

    # Bassline: a low sine that steps between a few notes every 2 beats.
    bass_notes_hz = [55.0, 65.4, 49.0, 73.4]
    for i, bt in enumerate(beat_times[::2]):
        i0 = int(bt * sr)
        i1 = min(n, int((bt + 2 * beat_s) * sr))
        if i0 >= n:
            break
        f = bass_notes_hz[i % len(bass_notes_hz)]
        y[i0:i1] += 0.25 * np.sin(2 * np.pi * f * t[i0:i1])

    # Mid chords: sum of a few harmonics, amplitude-modulated slowly.
    chord_hz = [220.0, 277.18, 329.63]
    for f in chord_hz:
        y += 0.08 * np.sin(2 * np.pi * f * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.1 * t))

    # Hi-hat: filtered noise bursts on the off-beat.
    for bt in beat_times:
        i0 = int((bt + beat_s / 2) * sr)
        if i0 >= n:
            break
        env_len = min(int(0.05 * sr), n - i0)
        burst = rng.standard_normal(env_len) * np.exp(-np.arange(env_len) / (0.005 * sr))
        y[i0:i0 + env_len] += 0.15 * burst

    # A little broadband noise floor so spectral-flatness / vocal-likelihood
    # style features have something non-trivial to chew on.
    y += 0.01 * rng.standard_normal(n)

    peak = float(np.max(np.abs(y))) or 1.0
    y = (y / peak * 0.9).astype(np.float32)
    return y


def _write_wav(y: np.ndarray, sr: int, path: Path) -> None:
    sf.write(str(path), y, sr, subtype="PCM_16")


def _timeit(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - t0


def bench_analyze(tmp_dir: Path, n_tracks: int = 3) -> Dict[str, Any]:
    from earcrate.analyze.decode import decode_audio_with_full_sha
    from earcrate.analyze.features import analyze_file_worker, compute_pcm_features, warmup_dsp

    # Numba JIT-compiles librosa's hot paths on first call (~seconds, one-time).
    # Pay that cost here, off the clock, so per-track numbers below measure
    # steady-state DSP cost instead of a cold-start artifact.
    warmup_dsp()

    cache_dir = tmp_dir / "analyze_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    per_track: List[Dict[str, Any]] = []

    for i in range(n_tracks):
        y = _synthesize_track(seed=1000 + i)
        wav_path = tmp_dir / f"track_{i}.wav"
        _write_wav(y, SR, wav_path)

        # 1a. decode + hash timing (isolated).
        (decoded_y, pcm_sha), decode_s = _timeit(
            decode_audio_with_full_sha, wav_path, SR, keep_seconds=DURATION_S, duration_hint=DURATION_S)

        # 1b. DSP feature-compute timing (isolated), on the same decoded PCM.
        _feats, dsp_s = _timeit(compute_pcm_features, decoded_y, SR)

        # 1c. full end-to-end analyze_file_worker (decode + DSP + npz cache write).
        job = {
            "path": str(wav_path), "sr": SR, "max_sec": int(DURATION_S) + 1,
            "cache_path": str(cache_dir / f"track_{i}.npz"),
            "file_id": i, "sha256": None, "duration": DURATION_S,
        }
        result, e2e_s = _timeit(analyze_file_worker, job)
        ok = bool(result.get("ok"))

        per_track.append({
            "track": i, "ok": ok, "duration_s": DURATION_S,
            "decode_s": round(decode_s, 4), "dsp_s": round(dsp_s, 4),
            "end_to_end_s": round(e2e_s, 4), "pcm_sha_prefix": pcm_sha[:12],
        })

    e2e_vals = [t["end_to_end_s"] for t in per_track]
    decode_vals = [t["decode_s"] for t in per_track]
    dsp_vals = [t["dsp_s"] for t in per_track]
    return {
        "n_tracks": n_tracks, "per_track": per_track,
        "avg_end_to_end_s": round(sum(e2e_vals) / len(e2e_vals), 4),
        "avg_decode_s": round(sum(decode_vals) / len(decode_vals), 4),
        "avg_dsp_s": round(sum(dsp_vals) / len(dsp_vals), 4),
        "decode_share_pct": round(100.0 * sum(decode_vals) / max(1e-9, sum(e2e_vals)), 1),
        "dsp_share_pct": round(100.0 * sum(dsp_vals) / max(1e-9, sum(e2e_vals)), 1),
    }


def _synthetic_pool(n: int = 2000, seed: int = 42) -> List[Dict[str, Any]]:
    """A pool with realistic clustering: a real DJ library has a handful of
    common BPMs (many tracks share the exact same value), not 2000 unique
    floats. This matters for the memoization benchmark below: the cache key
    is (role, source_bpm, target_bpm, loop_key, target_key, budgets), so how
    many *distinct* bpm/key/role combinations exist -- not how many atoms --
    determines whether repeated queries actually hit the cache."""
    rng = random.Random(seed)
    roles = ["full", "vocal", "instrumental", "perc"]
    bpm_choices = [round(85.0 + i * (175.0 - 85.0) / 14.0, 2) for i in range(15)]
    pool = []
    for i in range(n):
        pool.append({
            "id": i,
            "bpm": rng.choice(bpm_choices),
            "key_root": rng.randrange(12),
            "key_mode": rng.randrange(2),
            "role": rng.choice(roles),
            "score": round(rng.uniform(0.0, 1.0), 4),
        })
    return pool


def bench_deck_search(n_pool: int = 2000, n_keys: int = 12) -> Dict[str, Any]:
    from earcrate.deck.lattice import score_bpm_lattice
    from earcrate.deck.transform import _plan_varispeed_cached

    pool = _synthetic_pool(n_pool)

    def _run_all_keys() -> float:
        t0 = time.perf_counter()
        for target_key in range(n_keys):
            score_bpm_lattice(pool, target_bpm=126.0, target_key=target_key,
                               user_stretch_budget=6.0, residual_pitch_budget=2.0)
        return time.perf_counter() - t0

    # Cold: clear the process-wide memo cache first so this run pays full cost.
    _plan_varispeed_cached.cache_clear()
    cold_s = _run_all_keys()
    cold_hits = _plan_varispeed_cached.cache_info().hits

    # Warm: same pool/keys again, memo cache now populated -> should be much faster.
    warm_s = _run_all_keys()
    warm_info = _plan_varispeed_cached.cache_info()

    approx_calls = n_pool * n_keys * 3  # ~3 candidate BPMs per key in the default lattice steps
    return {
        "n_pool": n_pool, "n_keys": n_keys,
        "cold_s": round(cold_s, 4), "warm_s": round(warm_s, 4),
        "speedup_x": round(cold_s / warm_s, 2) if warm_s > 1e-9 else None,
        "cold_calls_per_sec": round(approx_calls / cold_s, 1) if cold_s > 1e-9 else None,
        "warm_calls_per_sec": round(approx_calls / warm_s, 1) if warm_s > 1e-9 else None,
        "cache_hits_cold_run": cold_hits,
        "cache_hits_after_warm": warm_info.hits,
        "cache_misses_total": warm_info.misses,
    }


def bench_beat_state() -> Dict[str, Any]:
    from earcrate.analyze.features import compute_pcm_features, warmup_dsp
    from earcrate.analyze.beat_features import beat_state_features

    warmup_dsp()  # numba JIT, off the clock (see bench_analyze).
    y = _synthesize_track(seed=7)
    # Use compute_pcm_features' beat grid as realistic input (beat_state_features
    # is what a real analyze call feeds), timed separately so it is not counted
    # against the DSP number below.
    feats, _ = _timeit(compute_pcm_features, y, SR)
    beats = feats["beats"]
    downbeats = feats["downbeats"]

    _state, dsp_s = _timeit(beat_state_features, y, SR, beats, downbeats)
    return {
        "duration_s": DURATION_S, "n_beats": int(len(beats)),
        "beat_state_features_s": round(dsp_s, 4),
    }


def _print_table(report: Dict[str, Any]) -> None:
    a = report["analyze"]
    print("== Per-track analyze (n=%d, %.0fs tracks) ==" % (a["n_tracks"], DURATION_S))
    print(f"{'track':>6} {'decode_s':>10} {'dsp_s':>10} {'end_to_end_s':>14}")
    for t in a["per_track"]:
        print(f"{t['track']:>6} {t['decode_s']:>10.3f} {t['dsp_s']:>10.3f} {t['end_to_end_s']:>14.3f}")
    print(f"{'avg':>6} {a['avg_decode_s']:>10.3f} {a['avg_dsp_s']:>10.3f} {a['avg_end_to_end_s']:>14.3f}")
    print(f"  decode share: {a['decode_share_pct']}%   dsp share: {a['dsp_share_pct']}%\n")

    d = report["deck_search"]
    print(f"== Deck search: score_bpm_lattice over {d['n_pool']} atoms x {d['n_keys']} keys ==")
    print(f"  cold: {d['cold_s']:.4f}s ({d['cold_calls_per_sec']} calls/s)")
    print(f"  warm: {d['warm_s']:.4f}s ({d['warm_calls_per_sec']} calls/s)")
    print(f"  speedup (memoization): {d['speedup_x']}x\n")

    b = report["beat_state"]
    print(f"== Beat-state DSP: beat_state_features on a {b['duration_s']:.0f}s signal ==")
    print(f"  n_beats={b['n_beats']}  time={b['beat_state_features_s']:.4f}s\n")


def main() -> int:
    report: Dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy_version": np.__version__,
        "soundfile_version": sf.__version__,
    }
    try:
        import librosa
        report["librosa_version"] = librosa.__version__
    except Exception as exc:
        report["librosa_version"] = f"unavailable ({exc})"

    with tempfile.TemporaryDirectory(prefix="earcrate-perf-baseline-") as tmp:
        tmp_dir = Path(tmp)
        report["analyze"] = bench_analyze(tmp_dir, n_tracks=3)

    report["deck_search"] = bench_deck_search(n_pool=2000, n_keys=12)
    report["beat_state"] = bench_beat_state()

    _print_table(report)

    out_path = ROOT / "scripts" / "perf_baseline_last.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
