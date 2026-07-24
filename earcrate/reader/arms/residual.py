from __future__ import annotations

"""Residual arm: route what recurrence cannot explain to explicit unique objects."""

from typing import Any, Mapping

import librosa
import numpy as np
from scipy.signal import butter, sosfiltfilt

from earcrate.reader.model import reader_observation_id, reader_sha256_json


def _reader_bandpass(signal: np.ndarray, sample_rate: int, low_hz: float, high_hz: float) -> np.ndarray:
    nyquist = sample_rate / 2.0
    low = max(5.0, min(low_hz, nyquist * 0.90))
    high = max(low + 10.0, min(high_hz, nyquist * 0.98))
    return sosfiltfilt(butter(4, [low, high], btype="bandpass", fs=sample_rate, output="sos"), signal).astype(np.float32)


def reader_residual_arm(
    reference: np.ndarray,
    recurrence_render: np.ndarray,
    pulse: Mapping[str, Any],
    sample_rate: int,
    body: Mapping[str, Any],
    persona: Mapping[str, Any],
) -> dict[str, Any]:
    residual = np.asarray(reference, dtype=np.float32) - np.asarray(recurrence_render, dtype=np.float32)
    phrase_starts = list(float(value) for value in pulse.get("phrase_starts") or [])
    if len(phrase_starts) < 2:
        phrase_starts = [0.0, float(body["duration_seconds"])]
    boundaries = phrase_starts + [float(body["duration_seconds"])]
    mono = residual.mean(axis=1)
    center_energy = []
    onset_density = []
    center = _reader_bandpass(mono, sample_rate, 180.0, min(6500.0, sample_rate * 0.47))
    phrase_durations = [boundaries[index + 1] - boundaries[index] for index in range(len(boundaries) - 1)]
    median_phrase_duration = float(np.median(phrase_durations)) if phrase_durations else float(body["duration_seconds"])
    for index in range(len(boundaries) - 1):
        start = int(round(boundaries[index] * sample_rate))
        end = min(int(body["frames"]), int(round(boundaries[index + 1] * sample_rate)))
        segment = center[start:end]
        center_energy.append(float(np.sqrt(np.mean(segment ** 2) + 1e-12)))
        phrase_onset = librosa.onset.onset_strength(y=segment, sr=sample_rate, hop_length=256)
        phrase_hits = librosa.onset.onset_detect(
            onset_envelope=phrase_onset,
            sr=sample_rate,
            hop_length=256,
            units="time",
            backtrack=False,
            delta=0.12,
            wait=2,
        )
        onset_density.append(float(len(phrase_hits) / max(phrase_durations[index], 1e-6)))
    split = max(1, len(center_energy) // 2)
    baseline_energy = float(np.median(center_energy[:split]))
    baseline_density = float(np.median(onset_density[:split]))
    candidate_indices = [
        index
        for index in range(split, len(center_energy))
        if phrase_durations[index] >= 0.75 * median_phrase_duration
        and center_energy[index] >= 1.15 * max(baseline_energy, 1e-9)
        and onset_density[index] >= 1.35 * max(baseline_density, 1e-9)
    ]
    if candidate_indices:
        foreground_phrase_index = candidate_indices[0]
    else:
        fallback = [
            index for index in range(split, len(center_energy))
            if phrase_durations[index] >= 0.75 * median_phrase_duration
        ] or list(range(split, len(center_energy)))
        foreground_phrase_index = max(
            fallback or [0],
            key=lambda index: center_energy[index] * max(onset_density[index], 1e-6),
        )
    phrase_start = boundaries[foreground_phrase_index]
    phrase_end = boundaries[foreground_phrase_index + 1]
    frame_start = int(round(phrase_start * sample_rate))
    frame_end = min(int(body["frames"]), int(round(phrase_end * sample_rate)))

    onset = librosa.onset.onset_strength(y=center[frame_start:frame_end], sr=sample_rate, hop_length=256)
    onset_times = librosa.onset.onset_detect(
        onset_envelope=onset,
        sr=sample_rate,
        hop_length=256,
        units="time",
        backtrack=False,
        delta=0.12,
        wait=2,
    )
    eligible_onsets = [float(value) for value in onset_times if float(value) >= 0.20]
    foreground_offset = eligible_onsets[0] if eligible_onsets else (float(onset_times[0]) if len(onset_times) else 0.0)
    foreground_start = min(phrase_end, phrase_start + foreground_offset)
    foreground_start_frame = int(round(foreground_start * sample_rate))

    policy = persona.get("unique_residual") or {}
    low_hz = float(policy.get("foreground_low_hz") or 180.0)
    high_hz = float(policy.get("foreground_high_hz") or 6500.0)
    side_low = float(policy.get("side_low_hz") or 450.0)
    side_high = float(policy.get("side_high_hz") or 8500.0)
    side_gain = float(policy.get("side_gain") or 0.22)
    mid = _reader_bandpass(mono, sample_rate, low_hz, high_hz)
    side = (residual[:, 0] - residual[:, 1]) * 0.5
    side = _reader_bandpass(side, sample_rate, side_low, side_high) * side_gain
    foreground = np.stack([mid + side, mid - side], axis=1).astype(np.float32)
    foreground[:foreground_start_frame] = 0.0
    transition = np.zeros_like(residual)
    transition_start_frame = int(round(phrase_start * sample_rate))
    transition[transition_start_frame:foreground_start_frame] = residual[transition_start_frame:foreground_start_frame]
    transition_fade = min(max(1, int(round(0.01 * sample_rate))), max(1, foreground_start_frame - transition_start_frame))
    if foreground_start_frame > transition_start_frame:
        transition[transition_start_frame:transition_start_frame + transition_fade] *= np.linspace(
            0.0, 1.0, transition_fade, endpoint=False, dtype=np.float32
        )[:, None]
        transition[foreground_start_frame - transition_fade:foreground_start_frame] *= np.linspace(
            1.0, 0.0, transition_fade, endpoint=False, dtype=np.float32
        )[:, None]
    fade_frames = min(max(1, int(round(0.03 * sample_rate))), max(1, len(foreground) - foreground_start_frame))
    foreground[foreground_start_frame : foreground_start_frame + fade_frames] *= np.linspace(
        0.0, 1.0, fade_frames, endpoint=False, dtype=np.float32
    )[:, None]

    body_sha = str(body["body_sha256"])
    transition_payload = {
        "phrase_index": int(foreground_phrase_index),
        "phrase_start_seconds": float(phrase_start),
        "foreground_start_seconds": float(foreground_start),
        "classification": "unique_drop_transition",
        "publication_eligible": False,
    }
    transition_observation_id = reader_observation_id(
        body_sha,
        "residual",
        "unique_drop_transition",
        transition_start_frame,
        max(transition_start_frame + 1, foreground_start_frame),
        transition_payload,
    )
    foreground_payload = {
        "phrase_index": int(foreground_phrase_index),
        "phrase_start_seconds": float(phrase_start),
        "foreground_start_seconds": float(foreground_start),
        "phrase_end_seconds": float(phrase_end),
        "center_residual_rms": center_energy[foreground_phrase_index],
        "classification": "foreground_source_phrase_candidate",
        "publication_eligible": False,
    }
    foreground_observation_id = reader_observation_id(
        body_sha,
        "residual",
        "foreground_source_phrase_candidate",
        foreground_start_frame,
        int(body["frames"]),
        foreground_payload,
    )
    observations = [
        {
            "observation_id": transition_observation_id,
            "body_sha256": body_sha,
            "arm": "residual",
            "kind": "unique_drop_transition",
            "start_frame": transition_start_frame,
            "end_frame": max(transition_start_frame + 1, foreground_start_frame),
            "payload": transition_payload,
            "confidence": 0.84,
        },
        {
            "observation_id": foreground_observation_id,
            "body_sha256": body_sha,
            "arm": "residual",
            "kind": "foreground_source_phrase_candidate",
            "start_frame": foreground_start_frame,
            "end_frame": int(body["frames"]),
            "payload": foreground_payload,
            "confidence": 0.72,
        },
    ]
    canonical_events = []
    instances = []
    for observation in observations:
        event_id = "event_" + reader_sha256_json(
            {"observation_id": observation["observation_id"], "kind": observation["kind"]}
        )[:20]
        event = {
            "canonical_event_id": event_id,
            "kind": observation["kind"],
            "layer": "foreground" if observation["kind"] == "foreground_source_phrase_candidate" else "spark",
            "instance_count": 1,
            "prototype_observation_id": observation["observation_id"],
            "publication_eligible": False,
        }
        canonical_events.append(event)
        instances.append(
            {
                "instance_id": event_id + ":unique",
                "canonical_event_id": event_id,
                "layer": event["layer"],
                "start_frame": int(observation["start_frame"]),
                "end_frame": int(observation["end_frame"]),
                "start_seconds": float(observation["start_frame"] / sample_rate),
                "end_seconds": float(observation["end_frame"] / sample_rate),
                "observation_ids": [observation["observation_id"]],
                "same_time_source_used": True,
                "publication_eligible": False,
            }
        )
    diagnostic_render = transition + foreground
    return {
        "arm": "residual",
        "raw_residual": residual,
        "transition_render": transition,
        "foreground_render": foreground,
        "diagnostic_render": diagnostic_render,
        "canonical_events": canonical_events,
        "instances": instances,
        "observations": observations,
        "diagnostics": {
            "phrase_center_residual_rms": center_energy,
            "phrase_onset_density": onset_density,
            "selected_phrase_index": int(foreground_phrase_index),
            "selected_phrase_start_seconds": float(phrase_start),
            "foreground_start_seconds": float(foreground_start),
            "transition_rms": float(np.sqrt(np.mean(transition[transition_start_frame:foreground_start_frame] ** 2) + 1e-12)) if foreground_start_frame > transition_start_frame else 0.0,
            "foreground_rms": float(np.sqrt(np.mean(foreground[foreground_start_frame:] ** 2) + 1e-12)),
            "reference_derived_unique_audio_used": True,
            "publication_eligible": False,
        },
    }


__all__ = ["reader_residual_arm"]
