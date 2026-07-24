from __future__ import annotations

"""Layer arm: a complementary physical decomposition, not semantic stems."""

from typing import Any, Mapping

import librosa
import numpy as np

from earcrate.reader.model import reader_observation_id

N_FFT = 2048
HOP = 256


def _reader_raised_low(frequencies: np.ndarray, cutoff: float = 250.0, width: float = 100.0) -> np.ndarray:
    low = cutoff - width / 2.0
    high = cutoff + width / 2.0
    response = np.ones_like(frequencies)
    response[frequencies >= high] = 0.0
    middle = (frequencies > low) & (frequencies < high)
    response[middle] = 0.5 * (1.0 + np.cos(np.pi * (frequencies[middle] - low) / (high - low)))
    return response


def _reader_raised_high(frequencies: np.ndarray, cutoff: float = 5000.0, width: float = 1200.0) -> np.ndarray:
    low = cutoff - width / 2.0
    high = cutoff + width / 2.0
    response = np.zeros_like(frequencies)
    response[frequencies >= high] = 1.0
    middle = (frequencies > low) & (frequencies < high)
    response[middle] = 0.5 * (1.0 - np.cos(np.pi * (frequencies[middle] - low) / (high - low)))
    return response


def reader_layer_arm(pcm: np.ndarray, sample_rate: int, body: Mapping[str, Any]) -> dict[str, Any]:
    stereo = np.asarray(pcm, dtype=np.float32)
    mono = stereo.mean(axis=1)
    mono_stft = librosa.stft(mono, n_fft=N_FFT, hop_length=HOP, window="hann", center=True)
    harmonic, percussive = librosa.decompose.hpss(np.abs(mono_stft), margin=(2.0, 2.0))
    harmonic_mask = librosa.util.softmask(harmonic, percussive, power=2)
    percussive_mask = 1.0 - harmonic_mask
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=N_FFT)
    low = _reader_raised_low(frequencies)[:, None]
    high = _reader_raised_high(frequencies)[:, None]
    middle = np.clip(1.0 - low - high, 0.0, 1.0)
    masks = {
        "low_end": low,
        "floor": (middle + high) * percussive_mask,
        "harmonic": middle * harmonic_mask,
        "texture": high * harmonic_mask,
    }
    layers: dict[str, np.ndarray] = {}
    for layer_name, mask in masks.items():
        channels = []
        for channel in range(stereo.shape[1]):
            spectrum = librosa.stft(stereo[:, channel], n_fft=N_FFT, hop_length=HOP, window="hann", center=True)
            signal = librosa.istft(
                spectrum * mask,
                hop_length=HOP,
                window="hann",
                length=stereo.shape[0],
            )
            channels.append(signal.astype(np.float32))
        layers[layer_name] = np.stack(channels, axis=1)
    reconstruction = sum(layers.values())
    error = reconstruction - stereo
    rms_total = float(np.sqrt(np.mean(stereo**2) + 1e-12))
    diagnostics = {
        "maximum_reconstruction_error": float(np.max(np.abs(error))),
        "rms_reconstruction_error": float(np.sqrt(np.mean(error**2))),
        "relative_rms_error": float(np.sqrt(np.mean(error**2)) / max(rms_total, 1e-12)),
        "layer_energy_share": {
            name: float(np.mean(value**2) / max(np.mean(stereo**2), 1e-12)) for name, value in layers.items()
        },
    }
    body_sha = str(body["body_sha256"])
    observations = []
    for layer_name, audio in layers.items():
        payload = {
            "layer": layer_name,
            "rms": float(np.sqrt(np.mean(audio**2) + 1e-12)),
            "energy_share": diagnostics["layer_energy_share"][layer_name],
        }
        observations.append(
            {
                "observation_id": reader_observation_id(body_sha, "layers", "physical_layer", 0, int(body["frames"]), payload),
                "body_sha256": body_sha,
                "arm": "layers",
                "kind": "physical_layer",
                "start_frame": 0,
                "end_frame": int(body["frames"]),
                "payload": payload,
                "confidence": 1.0,
            }
        )
    return {"arm": "layers", "layers": layers, "diagnostics": diagnostics, "observations": observations}


__all__ = ["N_FFT", "HOP", "reader_layer_arm"]
