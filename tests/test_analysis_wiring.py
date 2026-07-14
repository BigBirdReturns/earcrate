"""Gate: Step-2 beat_state is computed during real analysis, stored in the npz
cache, read back, and drives LIVE MaterialRegion proposal -- the wiring that makes
Patch 2 real on actual audio (not just synthetic fixtures).

Runs the real librosa analysis on a short synthetic track, so it is a touch
heavier than the pure-logic gates, but it proves the whole seam end to end.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.app import EarcrateCore
from earcrate.analyze.features import compute_pcm_features


def _synthetic(sr=22050, dur=16.0):
    n = int(sr * dur)
    t = np.arange(n) / sr
    bpm = 120.0
    beat = 60.0 / bpm
    y = np.zeros(n)
    env = np.exp(-np.arange(int(0.06 * sr)) / (0.02 * sr))
    tone = np.sin(2 * np.pi * 60 * np.arange(env.size) / sr)
    hit = env * tone
    for k in range(int(dur / beat)):
        i = int(k * beat * sr)
        seg = hit[: max(0, min(hit.size, n - i))]
        y[i:i + seg.size] += seg
    y[n // 2:] += 0.3 * np.sin(2 * np.pi * 3000 * t[n // 2:])  # brighter second half
    return (y / np.max(np.abs(y))).astype(np.float32)


def test_compute_features_now_includes_beat_state():
    f = compute_pcm_features(_synthetic(), 22050)
    bs = f.get("beat_state") or {}
    assert bs.get("activity"), "compute_pcm_features must attach per-beat activity"
    assert set(bs.get("roles") or []) == {"kick", "bass", "snare", "vocal", "lead", "hat"}


def test_beat_state_persists_and_drives_live_regions(tmp_path):
    try:
        import soundfile as sf
    except Exception:
        return  # soundfile unavailable -> skip (the pure-logic region gates still cover propose_regions)
    for d in ("music", "work", "agent"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    sf.write(str(tmp_path / "music" / "track.wav"), _synthetic(), 22050)
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        core = EarcrateCore()
        core.configure({"master_root": str(tmp_path / "music"), "working_root": str(tmp_path / "work"),
                        "agent_root": str(tmp_path / "agent"), "workers": 1, "analysis_seconds": 30})
        core.scan()
        fid = core.conn().execute("SELECT id FROM files LIMIT 1").fetchone()["id"]
        core.analyze_one(core.conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())

        # beat_state round-trips out of the npz cache (not the DB)
        bs = core.load_beat_state(fid)
        assert bs.get("activity") and bs.get("n_beats", 0) > 0

        # LIVE regions from real analysis: variable-length + functional kinds, and
        # strictly richer than the frozen fixed-bar baseline.
        live = core.material_regions(fid)
        base = core.material_regions(fid, baseline=True)
        assert live["ok"] and live["has_beat_state"]
        assert live["count"] > base["count"], (live["count"], base["count"])
        live_kinds = {r["kind"] for r in live["regions"]}
        assert "natural_tail" in live_kinds and "section" in live_kinds
        assert not base["has_beat_state"]
        assert {r["kind"] for r in base["regions"]} == {"phrase"}
        # region role capabilities are populated from the real beat_state
        assert any(r["role_probabilities"] for r in live["regions"])
