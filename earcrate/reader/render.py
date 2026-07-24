from __future__ import annotations

"""Offline execution and correspondence metrics for SongGenome proofs."""

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import librosa
import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

from earcrate.reader.model import reader_sha256_file, reader_sha256_json


def _reader_resample_stereo(segment: np.ndarray, target_frames: int) -> np.ndarray:
    if target_frames <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    if len(segment) == target_frames:
        return segment.astype(np.float32, copy=True)
    if len(segment) < 8:
        return np.zeros((target_frames, segment.shape[1]), dtype=np.float32)
    channels = []
    divisor = math.gcd(int(target_frames), int(len(segment)))
    up = int(target_frames) // divisor
    down = int(len(segment)) // divisor
    for channel in range(segment.shape[1]):
        value = resample_poly(segment[:, channel], up, down)
        if len(value) < target_frames:
            value = np.pad(value, (0, target_frames - len(value)))
        channels.append(value[:target_frames])
    return np.stack(channels, axis=1).astype(np.float32)


def reader_render_recurrence(
    layers: Mapping[str, np.ndarray],
    cells: Sequence[Mapping[str, Any]],
    execution_map: Sequence[Mapping[str, Any]],
    sample_rate: int,
    total_frames: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    output = np.zeros((int(total_frames), 2), dtype=np.float32)
    executed = []
    audible_self_source_count = 0
    for mapping in execution_map:
        target = cells[int(mapping["target_cell_index"])]
        prototype = cells[int(mapping["prototype_cell_index"])]
        target_frames = int(target["end_frame"]) - int(target["start_frame"])
        audible = bool(mapping["audible"])
        if audible:
            source = layers[str(target["layer"])][int(prototype["start_frame"]) : int(prototype["end_frame"])]
            source_rms = float(np.sqrt(np.mean(source**2) + 1e-12))
            signal = _reader_resample_stereo(source, target_frames)
            gain = min(3.0, max(0.0, float(target["rms"]) / max(source_rms, 1e-8)))
            signal *= gain
            if int(mapping["target_cell_index"]) == int(mapping["prototype_cell_index"]):
                audible_self_source_count += 1
        else:
            signal = np.zeros((target_frames, 2), dtype=np.float32)
            gain = 0.0
        fade_frames = min(target_frames // 2, max(1, int(round(0.008 * sample_rate))))
        if fade_frames:
            ramp = np.linspace(0.0, 1.0, fade_frames, endpoint=False, dtype=np.float32)
            signal[:fade_frames] *= ramp[:, None]
            signal[-fade_frames:] *= ramp[::-1, None]
        start = int(target["start_frame"])
        end = int(target["end_frame"])
        output[start:end] += signal
        executed.append(
            {
                "target_observation_id": target["observation_id"],
                "prototype_observation_id": prototype["observation_id"],
                "layer": target["layer"],
                "start_frame": start,
                "end_frame": end,
                "audible": audible,
                "gain_ratio": gain,
                "time_ratio": float(target_frames / max(1, int(prototype["end_frame"]) - int(prototype["start_frame"]))),
                "same_time_source_used": int(mapping["target_cell_index"]) == int(mapping["prototype_cell_index"]),
            }
        )
    peak = float(np.max(np.abs(output)))
    scale = 1.0
    if peak > 0.98:
        scale = 0.98 / peak
        output *= scale
    receipt = {
        "kind": "earcrate_recurrence_leave_one_out_render",
        "executed_instance_count": len(executed),
        "audible_instance_count": sum(bool(row["audible"]) for row in executed),
        "silent_unique_instance_count": sum(not bool(row["audible"]) for row in executed),
        "audible_same_time_source_count": audible_self_source_count,
        "global_scale": scale,
        "executions": executed,
    }
    receipt["render_sha256"] = reader_sha256_json(receipt)
    return output, receipt


def _reader_audio_correlation(left: np.ndarray, right: np.ndarray) -> float:
    frames = min(len(left), len(right))
    left = left[:frames]
    right = right[:frames]
    if frames < 2 or float(np.std(left)) < 1e-9 or float(np.std(right)) < 1e-9:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def reader_compare_audio(reference: np.ndarray, candidate: np.ndarray, sample_rate: int) -> dict[str, Any]:
    frames = min(len(reference), len(candidate))
    first = reference[:frames].mean(axis=1)
    second = candidate[:frames].mean(axis=1)
    hop = 256
    onset_first = librosa.onset.onset_strength(y=first, sr=sample_rate, hop_length=hop)
    onset_second = librosa.onset.onset_strength(y=second, sr=sample_rate, hop_length=hop)
    mel_first = librosa.power_to_db(
        librosa.feature.melspectrogram(y=first, sr=sample_rate, n_fft=1024, hop_length=hop, n_mels=64) + 1e-9
    )
    mel_second = librosa.power_to_db(
        librosa.feature.melspectrogram(y=second, sr=sample_rate, n_fft=1024, hop_length=hop, n_mels=64) + 1e-9
    )
    columns = min(mel_first.shape[1], mel_second.shape[1])
    first_frames = mel_first[:, :columns].T
    second_frames = mel_second[:, :columns].T
    mel_cosine = np.sum(first_frames * second_frames, axis=1) / (
        np.linalg.norm(first_frames, axis=1) * np.linalg.norm(second_frames, axis=1) + 1e-9
    )
    chroma_first = librosa.feature.chroma_stft(y=first, sr=sample_rate, n_fft=1024, hop_length=hop)
    chroma_second = librosa.feature.chroma_stft(y=second, sr=sample_rate, n_fft=1024, hop_length=hop)
    columns = min(chroma_first.shape[1], chroma_second.shape[1])
    chroma_cosine = np.sum(chroma_first[:, :columns] * chroma_second[:, :columns], axis=0) / (
        np.linalg.norm(chroma_first[:, :columns], axis=0) * np.linalg.norm(chroma_second[:, :columns], axis=0) + 1e-9
    )
    return {
        "onset_envelope_correlation": _reader_audio_correlation(onset_first, onset_second),
        "mel_frame_cosine_mean": float(np.mean(mel_cosine)),
        "chroma_frame_cosine_mean": float(np.mean(chroma_cosine)),
        "raw_waveform_correlation": _reader_audio_correlation(first, second),
    }


def reader_write_wav(path: str | Path, audio: np.ndarray, sample_rate: int, *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), np.asarray(audio, dtype=np.float32), int(sample_rate), subtype="PCM_16")
    return {"path": str(destination), "raw_sha256": reader_sha256_file(destination)}


def reader_write_reference_then_candidate(
    path: str | Path,
    reference: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    *,
    gap_seconds: float = 0.75,
    overwrite: bool = False,
) -> dict[str, Any]:
    frames = min(len(reference), len(candidate))
    gap = np.zeros((int(round(gap_seconds * sample_rate)), 2), dtype=np.float32)
    value = np.concatenate([reference[:frames], gap, candidate[:frames]], axis=0)
    return reader_write_wav(path, value, sample_rate, overwrite=overwrite)


__all__ = [
    "reader_render_recurrence",
    "reader_compare_audio",
    "reader_write_wav",
    "reader_write_reference_then_candidate",
]
