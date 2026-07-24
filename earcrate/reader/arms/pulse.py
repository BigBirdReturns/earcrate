from __future__ import annotations

"""Pulse arm: beat hypotheses, nontrivial recurrence lag, and phrase heartbeat."""

import math
from typing import Any, Mapping

import librosa
import numpy as np

from earcrate.reader.model import reader_observation_id


def _reader_pulse_unit(row: np.ndarray) -> np.ndarray:
    return row / (float(np.linalg.norm(row)) + 1e-12)


def _reader_beat_embedding(segment: np.ndarray, sample_rate: int) -> np.ndarray:
    if segment.size < 256:
        return np.zeros(45, dtype=np.float64)
    n_fft = min(1024, 2 ** int(np.floor(np.log2(max(256, segment.size)))))
    hop = max(64, n_fft // 4)
    mel = librosa.feature.melspectrogram(y=segment, sr=sample_rate, n_fft=n_fft, hop_length=hop, n_mels=24)
    mel_mean = librosa.power_to_db(mel + 1e-12, ref=np.max).mean(axis=1)
    chroma = librosa.feature.chroma_stft(y=segment, sr=sample_rate, n_fft=n_fft, hop_length=hop).mean(axis=1)
    rms = librosa.feature.rms(y=segment, frame_length=n_fft, hop_length=hop)[0]
    return np.r_[mel_mean, chroma, [float(rms.mean()), float(rms.std()), float(np.max(np.abs(segment)))], np.zeros(6)]


def reader_pulse_arm(pcm: np.ndarray, sample_rate: int, body: Mapping[str, Any], persona: Mapping[str, Any]) -> dict[str, Any]:
    mono = np.asarray(pcm, dtype=np.float32).mean(axis=1)
    hop = 512
    onset = librosa.onset.onset_strength(y=mono, sr=sample_rate, hop_length=hop, aggregate=np.median)
    tempo, beat_times = librosa.beat.beat_track(
        onset_envelope=onset,
        sr=sample_rate,
        hop_length=hop,
        units="time",
        trim=False,
    )
    beat_times = np.asarray(beat_times, dtype=np.float64)
    duration = float(len(mono) / sample_rate)
    beat_times = beat_times[(beat_times >= 0.0) & (beat_times < duration)]
    if beat_times.size < 10:
        raise ValueError("pulse arm could not establish enough beats for recurrence")
    tempo_bpm = float(np.asarray(tempo).reshape(-1)[0])

    rows = []
    for start, end in zip(beat_times[:-1], beat_times[1:]):
        segment = mono[int(round(start * sample_rate)) : int(round(end * sample_rate))]
        rows.append(_reader_beat_embedding(segment, sample_rate))
    matrix = np.asarray(rows, dtype=np.float64)
    matrix = (matrix - matrix.mean(axis=0)) / (matrix.std(axis=0) + 1e-6)
    matrix = np.asarray([_reader_pulse_unit(row) for row in matrix])

    phrase_policy = persona.get("phrase") or {}
    minimum = max(2, int(phrase_policy.get("minimum_cycle_beats") or 4))
    maximum = min(int(phrase_policy.get("maximum_cycle_beats") or 16), max(2, len(matrix) // 3))
    lag_scores = []
    for lag in range(2, maximum + 1):
        similarities = np.sum(matrix[:-lag] * matrix[lag:], axis=1)
        mean = float(np.mean(similarities))
        median = float(np.median(similarities))
        score = (0.65 * mean + 0.35 * median) * (1.0 + 0.08 * math.log2(lag))
        lag_scores.append(
            {
                "lag_beats": int(lag),
                "mean_similarity": mean,
                "median_similarity": median,
                "score": score,
            }
        )
    local_maxima = []
    for index, row in enumerate(lag_scores):
        previous = lag_scores[index - 1]["score"] if index else -99.0
        following = lag_scores[index + 1]["score"] if index + 1 < len(lag_scores) else -99.0
        if row["score"] >= previous and row["score"] >= following:
            local_maxima.append(row)
    plausible = [row for row in local_maxima if int(row["lag_beats"]) >= minimum]
    selected = max(plausible or lag_scores, key=lambda row: float(row["score"]))
    phrase_beats = int(selected["lag_beats"])
    phrase_starts = beat_times[::phrase_beats]

    observations = []
    body_sha = str(body["body_sha256"])
    for index, time_seconds in enumerate(beat_times):
        frame = min(int(body["frames"]) - 1, max(0, int(round(time_seconds * sample_rate))))
        payload = {"beat_index": int(index), "time_seconds": float(time_seconds), "tempo_bpm": tempo_bpm}
        observations.append(
            {
                "observation_id": reader_observation_id(body_sha, "pulse", "beat", frame, min(int(body["frames"]), frame + 1), payload),
                "body_sha256": body_sha,
                "arm": "pulse",
                "kind": "beat",
                "start_frame": frame,
                "end_frame": min(int(body["frames"]), frame + 1),
                "payload": payload,
                "confidence": 1.0,
            }
        )
    return {
        "arm": "pulse",
        "tempo_bpm": tempo_bpm,
        "beats": [float(value) for value in beat_times],
        "phrase_beats": phrase_beats,
        "phrase_starts": [float(value) for value in phrase_starts],
        "lag_scores": lag_scores,
        "selected_lag": selected,
        "observations": observations,
    }


__all__ = ["reader_pulse_arm"]
