"""Turn a recording into bars, because bars are the unit both sides share.

Seconds are the wrong currency here. The score claims 130 bpm and the control
candidate measures 136, and forcing either duration onto the other is exactly what
the comparison is forbidden to do. So the audio is quantized to its own measured
pulse and compared bar against bar, where a bar means the same musical thing on both
sides even though it lasts a different number of seconds.

Nothing in this module knows what the score expects. It describes a recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ANALYSIS_SR = 22050
HOP = 512
BEATS_PER_BAR = 4


class FeatureError(RuntimeError):
    pass


@dataclass(frozen=True)
class BarFeatures:
    """One bar of audio, described in the terms a score can be compared against."""

    index: int
    start_seconds: float
    end_seconds: float
    chroma: tuple[float, ...]      # 12 pitch classes, L1-normalized
    onset_density: float
    energy: float

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "start_seconds": round(self.start_seconds, 4),
                "end_seconds": round(self.end_seconds, 4),
                "chroma": [round(v, 6) for v in self.chroma],
                "onset_density": round(self.onset_density, 6),
                "energy": round(self.energy, 6)}


def load(path: Path, *, sr: int = ANALYSIS_SR):
    import librosa

    y, actual = librosa.load(str(path), sr=sr, mono=True)
    if not len(y):
        raise FeatureError(f"no audio decoded from {Path(path).name}")
    return y, actual


def measure_pulse(y, sr: int) -> dict[str, Any]:
    """The recording's own pulse, from four estimators that must agree.

    Agreement is reported rather than assumed. A recording whose estimators disagree
    is one whose bar grid is a guess, and the comparison should say so instead of
    quietly picking one.
    """
    import librosa

    onset = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median, hop_length=HOP)
    frame = librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=HOP, aggregate=None)
    static = float(librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=HOP)[0])
    tracked, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP,
                                             units="time")
    tracked = float(np.atleast_1d(tracked)[0])
    if len(beats) < BEATS_PER_BAR * 2:
        raise FeatureError("too few beats to build a bar grid")
    intervals = np.diff(beats)
    estimators = {
        "median_frame_tempo_bpm": round(float(np.median(frame)), 3),
        "static_tempo_bpm": round(static, 3),
        "beat_track_tempo_bpm": round(tracked, 3),
        "inter_beat_interval_tempo_bpm": round(float(60.0 / np.median(intervals)), 3),
    }
    values = list(estimators.values())
    return {
        "estimators": estimators,
        "agreement": len(set(values)) == 1,
        "spread_bpm": round(max(values) - min(values), 3),
        "pulse_bpm": round(float(np.median(values)), 2),
        "beats": [float(t) for t in beats],
        "inter_beat_sd_seconds": round(float(np.std(intervals)), 5),
    }


def bar_features(path: Path, *, sr: int = ANALYSIS_SR,
                 beats_per_bar: int = BEATS_PER_BAR) -> dict[str, Any]:
    """Bar-quantized chroma, onset density and energy for a whole recording."""
    import librosa

    y, actual_sr = load(path, sr=sr)
    pulse = measure_pulse(y, actual_sr)
    beats = pulse["beats"]

    chroma = librosa.feature.chroma_cqt(y=y, sr=actual_sr, hop_length=HOP)
    onset = librosa.onset.onset_strength(y=y, sr=actual_sr, hop_length=HOP)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]

    rows: list[BarFeatures] = []
    for index in range(0, len(beats) - beats_per_bar, beats_per_bar):
        start, end = beats[index], beats[index + beats_per_bar]
        first = librosa.time_to_frames(start, sr=actual_sr, hop_length=HOP)
        last = max(first + 1, librosa.time_to_frames(end, sr=actual_sr, hop_length=HOP))
        window = chroma[:, first:last]
        if not window.size:
            continue
        vector = window.mean(axis=1)
        total = float(vector.sum()) or 1.0
        rows.append(BarFeatures(
            index=len(rows), start_seconds=float(start), end_seconds=float(end),
            chroma=tuple(float(v / total) for v in vector),
            onset_density=float(onset[first:last].mean()) if last <= len(onset) else 0.0,
            energy=float(rms[first:last].mean()) if last <= len(rms) else 0.0))

    if not rows:
        raise FeatureError("no bars could be formed from the beat grid")
    return {
        "pulse": {key: value for key, value in pulse.items() if key != "beats"},
        "beats_per_bar": beats_per_bar,
        "bar_count": len(rows),
        "bars": rows,
        "analysis_sample_rate": actual_sr,
        "hop_length": HOP,
    }


def chroma_matrix(bars: list[BarFeatures]) -> np.ndarray:
    return np.array([row.chroma for row in bars], dtype=float)


def onset_vector(bars: list[BarFeatures]) -> np.ndarray:
    values = np.array([row.onset_density for row in bars], dtype=float)
    span = values.max() - values.min()
    return (values - values.min()) / span if span else np.zeros_like(values)
