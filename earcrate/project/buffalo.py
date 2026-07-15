from __future__ import annotations

import contextlib
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

import librosa
import numpy as np
import soundfile as sf
from scipy import signal

from .util import ValidationError, clamp, sha256_file


BUFFALO_COMPONENTS = {
    "decode": "earcrate.analyze.decode",
    "analysis": "earcrate.analyze.features",
    "regions": "earcrate.materials.regions",
    "transform": "earcrate.deck.transform",
    "transitions": "earcrate.plan.transitions",
    "judge": "earcrate.judge.audio",
    "stems": "earcrate.providers",
}


def capabilities() -> dict[str, Any]:
    available: dict[str, bool] = {}
    errors: dict[str, str] = {}
    for name, module in BUFFALO_COMPONENTS.items():
        try:
            __import__(module)
            available[name] = True
        except Exception as exc:
            available[name] = False
            errors[name] = str(exc)
    return {
        "available": available,
        "errors": errors,
        "all_core_available": all(available.get(name, False) for name in ("decode", "analysis", "regions", "transform", "transitions", "judge")),
    }


def _soundfile_decode(path: Path, sr: int) -> np.ndarray:
    data, source_sr = sf.read(str(path), always_2d=True, dtype="float32")
    mono = np.mean(data, axis=1, dtype=np.float32)
    if int(source_sr) != int(sr):
        mono = librosa.resample(mono.astype(np.float32), orig_sr=int(source_sr), target_sr=int(sr), res_type="soxr_hq").astype(np.float32)
    if mono.size == 0:
        raise ValidationError(f"decoded zero samples: {path}")
    return np.nan_to_num(mono, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def decode_audio(path: str | Path, sr: int, *, start: float | None = None, duration: float | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        from earcrate.analyze.decode import decode_audio as buffalo_decode  # type: ignore

        audio = buffalo_decode(source, int(sr), start=start, duration=duration)
        return np.asarray(audio, dtype=np.float32), {"component": "decode", "backend": "earcrate.analyze.decode", "buffalo": True}
    except ImportError:
        audio = _soundfile_decode(source, int(sr))
        a = max(0, int(round(float(start or 0.0) * sr)))
        b = audio.size if not duration else min(audio.size, a + int(round(float(duration) * sr)))
        return audio[a:b].copy(), {"component": "decode", "backend": "soundfile+librosa", "buffalo": False, "reason": "package adapter unavailable"}


def decoded_pcm_sha(path: str | Path, sr: int, duration_hint: float = 0.0) -> tuple[str, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        from earcrate.analyze.decode import decoded_audio_sha256  # type: ignore

        return str(decoded_audio_sha256(source, int(sr), float(duration_hint))), {"component": "pcm_identity", "backend": "earcrate.analyze.decode", "buffalo": True}
    except ImportError:
        audio, receipt = decode_audio(source, sr)
        digest = hashlib.sha256(audio.astype("<f4", copy=False).tobytes()).hexdigest()
        return digest, {"component": "pcm_identity", "backend": receipt["backend"], "buffalo": False, "reason": "package adapter unavailable"}


def _fallback_features(y: np.ndarray, sr: int) -> dict[str, Any]:
    """Dependency-light deterministic analysis for isolated project-engine tests.

    Package mode uses EarCrate's full analyzer. This path deliberately avoids numba
    so the project store, compiler, and renderer remain testable on interpreters for
    which librosa's JIT stack is not available.
    """
    if y.size < 512 or float(np.max(np.abs(y))) < 1e-6:
        return {
            "bpm": 120.0, "bpm_confidence": 0.0, "beats": [], "downbeats": [],
            "key_root": 0, "key_mode": 1, "key_confidence": 0.0,
            "loudness_lufs": -70.0, "energy": 0.0, "vocal_likelihood": 0.0,
            "sections": [], "beat_state": {},
        }
    hop = 512
    frame = 2048
    padded = np.pad(y.astype(np.float64), (frame // 2, frame // 2))
    n = max(1, 1 + (padded.size - frame) // hop)
    rms = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = padded[i * hop : i * hop + frame]
        rms[i] = np.sqrt(np.mean(seg * seg) + 1e-12)
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    onset -= onset.mean()
    corr = signal.correlate(onset, onset, mode="full", method="fft")[onset.size - 1 :]
    min_lag = max(1, int(round((60.0 / 180.0) * sr / hop)))
    max_lag = max(min_lag + 1, int(round((60.0 / 70.0) * sr / hop)))
    window = corr[min_lag : min(max_lag + 1, corr.size)]
    lag = min_lag + int(np.argmax(window)) if window.size else int(round(0.5 * sr / hop))
    beat_period = max(hop, lag * hop)
    bpm = 60.0 * sr / beat_period
    while bpm < 70:
        bpm *= 2.0
    while bpm > 180:
        bpm /= 2.0
    peak_corr = float(window.max()) if window.size else 0.0
    bpm_confidence = clamp(peak_corr / (float(corr[0]) + 1e-9), 0.0, 1.0)
    duration = y.size / sr
    beats = np.arange(0.0, duration, 60.0 / bpm, dtype=np.float64)
    downbeats = beats[::4]
    # Pitch-class energy from one deterministic FFT projection.
    spectrum = np.abs(np.fft.rfft(y.astype(np.float64) * np.hanning(y.size))) ** 2
    freqs = np.fft.rfftfreq(y.size, 1.0 / sr)
    pcs = np.zeros(12, dtype=np.float64)
    valid = (freqs >= 55.0) & (freqs <= 5000.0)
    for freq, power in zip(freqs[valid], spectrum[valid]):
        midi = int(round(69.0 + 12.0 * math.log2(freq / 440.0)))
        pcs[midi % 12] += power
    key_root = int(np.argmax(pcs)) if pcs.sum() else 0
    key_confidence = float(pcs.max() / (pcs.sum() + 1e-9))
    energy = float(np.sqrt(np.mean(np.square(y.astype(np.float64)))))
    # Spectral vocal proxy.
    spec = np.abs(np.fft.rfft(y.astype(np.float64)))
    sfreq = np.fft.rfftfreq(y.size, 1.0 / sr)
    total = float(spec.sum() + 1e-12)
    vocal_likelihood = float(clamp(float(spec[(sfreq >= 300) & (sfreq <= 3400)].sum() / total) / 0.55, 0.0, 1.0))
    starts = list(downbeats[::4]) if downbeats.size else list(np.arange(0.0, duration, 16.0))
    if not starts or starts[0] > 0.05:
        starts.insert(0, 0.0)
    starts = [float(x) for x in starts if float(x) < duration] + [float(duration)]
    sections = []
    for i in range(len(starts) - 1):
        a, b = starts[i], starts[i + 1]
        if b - a < 0.5:
            continue
        label = "intro" if i == 0 else "outro" if i == len(starts) - 2 else "verse"
        sections.append({"start": round(a, 6), "end": round(b, 6), "label": label})
    return {
        "bpm": float(bpm), "bpm_confidence": float(bpm_confidence),
        "beats": beats.tolist(), "downbeats": downbeats.tolist(),
        "key_root": key_root, "key_mode": 1, "key_confidence": key_confidence,
        "loudness_lufs": float(20.0 * math.log10(max(1e-9, energy))),
        "energy": energy, "vocal_likelihood": vocal_likelihood,
        "sections": sections, "beat_state": {},
    }


def analyze_audio(path: str | Path, sr: int, analysis_seconds: float = 180.0) -> tuple[dict[str, Any], dict[str, Any]]:
    audio, decode_receipt = decode_audio(path, sr, duration=analysis_seconds)
    try:
        from earcrate.analyze.features import compute_pcm_features  # type: ignore

        raw = dict(compute_pcm_features(audio, int(sr)))
        backend = "earcrate.analyze.features.compute_pcm_features"
        buffalo = True
    except ImportError:
        raw = _fallback_features(audio, int(sr))
        backend = "project.fallback_features"
        buffalo = False
    serial = {}
    for key, value in raw.items():
        if isinstance(value, np.ndarray):
            serial[key] = value.tolist()
        elif isinstance(value, np.generic):
            serial[key] = value.item()
        else:
            serial[key] = value
    return serial, {
        "component": "analysis",
        "backend": backend,
        "buffalo": buffalo,
        "decode": decode_receipt,
        "analysis_samples": int(audio.size),
        "analysis_seconds": float(audio.size / sr),
    }


def spectral_metrics(y: np.ndarray, sr: int) -> dict[str, float]:
    if y.size == 0:
        return {"low_share": 0.0, "mid_share": 0.0, "high_share": 0.0, "rms": 0.0, "transient_density": 0.0, "loopability": 0.0, "intelligibility": 0.0}
    nperseg = min(2048, max(256, int(2 ** math.floor(math.log2(max(256, y.size))))))
    _, freqs_t, Z = signal.stft(y.astype(np.float64), fs=sr, nperseg=nperseg, noverlap=nperseg // 2, boundary=None)
    # scipy returns (freqs, times, Z); tolerate assignment name above for readability.
    freqs = _
    S = np.abs(Z) ** 2
    total = float(np.sum(S) + 1e-12)
    low = float(np.sum(S[freqs < 200]) / total)
    mid = float(np.sum(S[(freqs >= 520) & (freqs <= 3400)]) / total)
    high = float(np.sum(S[freqs > 3400]) / total)
    presence = float(np.sum(S[(freqs >= 1800) & (freqs <= 6500)]) / total)
    rms = float(np.sqrt(np.mean(np.square(y.astype(np.float64)))) )
    hop = 512
    frame = 1024
    energies = []
    for i in range(0, max(1, y.size - frame + 1), hop):
        seg = y[i : i + frame]
        energies.append(float(np.sqrt(np.mean(np.square(seg.astype(np.float64))) + 1e-12)))
    diff = np.maximum(0.0, np.diff(np.asarray(energies), prepend=energies[0] if energies else 0.0))
    threshold = float(np.median(diff) + 2.5 * np.median(np.abs(diff - np.median(diff)))) if diff.size else 0.0
    transient_density = float(clamp(float(np.sum(diff > threshold)) / max(1.0, y.size / sr) / 7.0, 0.0, 1.0))
    win = min(y.size // 4, sr // 2)
    if win >= 512:
        h1 = np.abs(np.fft.rfft(y[:win]))
        h2 = np.abs(np.fft.rfft(y[-win:]))
        distance = np.linalg.norm(h1 / (np.linalg.norm(h1) + 1e-9) - h2 / (np.linalg.norm(h2) + 1e-9))
        loopability = float(clamp(1.0 - distance / 1.55, 0.0, 1.0))
    else:
        loopability = 0.0
    intelligibility = float(clamp(0.48 * min(1.0, mid / 0.42) + 0.34 * min(1.0, presence / 0.24) + 0.18 * (1.0 - min(1.0, low / 0.42)), 0.0, 1.0))
    return {"low_share": low, "mid_share": mid, "high_share": high, "rms": rms, "transient_density": transient_density, "loopability": loopability, "intelligibility": intelligibility}


def regions_for_analysis(analysis: Mapping[str, Any], *, baseline: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from earcrate.materials.regions import propose_regions  # type: ignore

        regions = propose_regions(dict(analysis), (analysis.get("beat_state") or None), baseline=baseline)
        return [region.as_dict() if hasattr(region, "as_dict") else dict(region) for region in regions], {
            "component": "regions",
            "backend": "earcrate.materials.regions.propose_regions",
            "buffalo": True,
            "baseline": bool(baseline),
        }
    except ImportError:
        downbeats = [float(x) for x in analysis.get("downbeats") or []]
        duration = float(analysis.get("duration_s") or 0.0)
        if not downbeats:
            starts = [0.0]
            step = 8.0
            while starts[-1] + step < duration:
                starts.append(starts[-1] + step)
        else:
            starts = downbeats[::4]
            if not starts or starts[0] > 0.05:
                starts = [0.0] + starts
        out = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else duration
            if end - start < 0.5:
                continue
            out.append({
                "region_id": f"fallback:{i}",
                "start_s": start,
                "end_s": end,
                "bars": max(1, int(round((end - start) * float(analysis.get("bpm") or 120.0) / 240.0))),
                "kind": "grid",
                "confidence": float(analysis.get("bpm_confidence") or 0.0),
            })
        return out, {"component": "regions", "backend": "project.grid_regions", "buffalo": False, "reason": "package adapter unavailable"}


def transform_plan(role: str, source_bpm: float, render_bpm: float, source_key: int, target_key: int, stretch_budget: float, pitch_budget: float) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from earcrate.deck.transform import plan_varispeed_transform  # type: ignore

        plan = dict(plan_varispeed_transform(role, source_bpm, render_bpm, source_key, target_key, stretch_budget, pitch_budget))
        return plan, {"component": "transform", "backend": "earcrate.deck.transform.plan_varispeed_transform", "buffalo": True}
    except ImportError:
        if source_bpm <= 0 or render_bpm <= 0:
            return {"violation": "invalid tempo"}, {"component": "transform", "backend": "project.transform", "buffalo": False}
        ratio = render_bpm / source_bpm
        while ratio > 1.5:
            ratio /= 2.0
        while ratio < 0.67:
            ratio *= 2.0
        stretch_pct = abs(ratio - 1.0) * 100.0
        desired = ((target_key - source_key + 6) % 12) - 6
        violation = None
        if stretch_pct > stretch_budget + 1e-9:
            violation = f"varispeed {stretch_pct:.2f}% exceeds {stretch_budget:.2f}%"
        if abs(desired) > pitch_budget + 1e-9:
            violation = (violation + "; " if violation else "") + f"pitch {desired} exceeds {pitch_budget}"
        return {
            "violation": violation,
            "speed_ratio": ratio,
            "varispeed_pct": stretch_pct,
            "residual_pitch_shift": float(desired),
            "synthetic_pitch_shift": float(desired),
            "transform_mode": "varispeed_then_pitch",
            "artifact_risk": clamp(stretch_pct / max(stretch_budget, 1e-9) * 0.6 + abs(desired) / max(pitch_budget, 1e-9) * 0.4, 0.0, 1.0),
        }, {"component": "transform", "backend": "project.transform", "buffalo": False, "reason": "package adapter unavailable"}


def transition_candidates(
    outgoing_analysis: Mapping[str, Any],
    incoming_analysis: Mapping[str, Any],
    *,
    set_state: Mapping[str, Any] | None = None,
    outgoing_state: Mapping[str, Any] | None = None,
    incoming_state: Mapping[str, Any] | None = None,
    top_k: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from earcrate.plan.transitions import generate_transition_candidates  # type: ignore

        plans = generate_transition_candidates(
            dict(outgoing_analysis),
            dict(incoming_analysis),
            dict(set_state or {}),
            top_k=top_k,
            a_state=dict(outgoing_state or {}) or None,
            b_state=dict(incoming_state or {}) or None,
        )
        return [plan.as_dict() if hasattr(plan, "as_dict") else dict(plan) for plan in plans], {
            "component": "transitions",
            "backend": "earcrate.plan.transitions.generate_transition_candidates",
            "buffalo": True,
        }
    except ImportError:
        a_bpm = float(outgoing_analysis.get("bpm") or 0.0)
        b_bpm = float(incoming_analysis.get("bpm") or 0.0)
        warp = abs(a_bpm - b_bpm) / max(1e-9, max(a_bpm, b_bpm)) if a_bpm and b_bpm else 1.0
        confidence = min(float(outgoing_analysis.get("bpm_confidence") or 0.0), float(incoming_analysis.get("bpm_confidence") or 0.0))
        plans = [
            {
                "technique": "hard_cut",
                "duration_bars": 0,
                "total_score": 0.55 + 0.2 * confidence,
                "confidence": confidence,
                "scores": {"phrase": 0.5, "impact": 0.5, "harmonic": 0.5, "energy": 0.5},
                "predicted_failure_modes": [],
            }
        ]
        if warp <= 0.06 and confidence >= 0.6:
            plans.append({
                "technique": "long_blend",
                "duration_bars": 4,
                "total_score": 0.72 + 0.1 * confidence,
                "confidence": confidence,
                "scores": {"phrase": 0.7, "impact": 0.3, "harmonic": 0.7, "energy": 0.6},
                "predicted_failure_modes": [],
            })
        plans.sort(key=lambda row: (-float(row["total_score"]), str(row["technique"])))
        return plans[:top_k], {"component": "transitions", "backend": "project.transition_candidates", "buffalo": False, "reason": "package adapter unavailable"}


def resample_or_fit(y: np.ndarray, target_len: int) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        from earcrate.analyze.decode import resample_or_fit as buffalo_resample  # type: ignore

        return np.asarray(buffalo_resample(y, int(target_len)), dtype=np.float32), {"component": "resample", "backend": "earcrate.analyze.decode.resample_or_fit", "buffalo": True}
    except ImportError:
        if target_len <= 0:
            return np.zeros(0, dtype=np.float32), {"component": "resample", "backend": "scipy.signal.resample_poly", "buffalo": False}
        if y.size == target_len:
            return y.astype(np.float32, copy=False), {"component": "resample", "backend": "identity", "buffalo": False}
        if y.size == 0:
            return np.zeros(target_len, dtype=np.float32), {"component": "resample", "backend": "zeros", "buffalo": False}
        from fractions import Fraction

        frac = Fraction(int(target_len), int(y.size)).limit_denominator(1000)
        out = signal.resample_poly(y.astype(np.float64), frac.numerator, frac.denominator).astype(np.float32)
        if out.size < target_len:
            out = np.pad(out, (0, target_len - out.size))
        return out[:target_len], {"component": "resample", "backend": "scipy.signal.resample_poly", "buffalo": False, "reason": "package adapter unavailable"}


def quality_metrics(y: np.ndarray, sr: int) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from earcrate.judge.audio import drydeck_metrics  # type: ignore

        return dict(drydeck_metrics(y, int(sr))), {"component": "judge", "backend": "earcrate.judge.audio.drydeck_metrics", "buffalo": True}
    except ImportError:
        y = np.asarray(y, dtype=np.float32)
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(y.astype(np.float64))))) if y.size else 0.0
        frame = max(512, int(sr * 0.05))
        rms_frames = [float(np.sqrt(np.mean(np.square(y[i : i + frame].astype(np.float64))))) for i in range(0, max(1, y.size - frame + 1), frame)] if y.size else []
        floor = max(1e-4, peak * 0.01, rms * 0.18)
        active = np.asarray(rms_frames) >= floor if rms_frames else np.zeros(0, dtype=bool)
        S = np.abs(librosa.stft(y, n_fft=4096, hop_length=2048)) ** 2 if y.size else np.zeros((2049, 1))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
        total = float(np.sum(S) + 1e-12)
        metrics = {
            "duration_s": float(y.size / sr),
            "rms_std_db": float(np.std(20 * np.log10(np.asarray(rms_frames) + 1e-9))) if rms_frames else 0.0,
            "silence_ratio": float(1.0 - np.mean(active)) if active.size else 1.0,
            "active_coverage_ratio": float(np.mean(active)) if active.size else 0.0,
            "audible_seconds": float(np.mean(active) * y.size / sr) if active.size else 0.0,
            "first_audible_s": float(np.flatnonzero(active)[0] * 0.05) if np.any(active) else None,
            "last_audible_s": float((np.flatnonzero(active)[-1] + 1) * 0.05) if np.any(active) else None,
            "largest_silence_gap_s": 0.0,
            "low200_share": float(np.sum(S[freqs < 200]) / total),
            "high3000_share": float(np.sum(S[freqs > 3000]) / total),
            "peak": peak,
            "global_rms": rms,
            "audible_rms_floor": floor,
        }
        return metrics, {"component": "judge", "backend": "project.quality_metrics", "buffalo": False, "reason": "package adapter unavailable"}


def quality_gate(metrics: Mapping[str, Any], target_seconds: float, spectral_profile: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from earcrate.judge.audio import drydeck_quality_gate  # type: ignore

        return dict(drydeck_quality_gate(dict(metrics), float(target_seconds), dict(spectral_profile))), {"component": "judge_gate", "backend": "earcrate.judge.audio.drydeck_quality_gate", "buffalo": True}
    except ImportError:
        failures: list[str] = []
        warnings: list[str] = []
        if float(metrics.get("peak") or 0.0) < 0.01:
            failures.append("peak below audible floor")
        if float(metrics.get("active_coverage_ratio") or 0.0) < 0.62:
            failures.append("audible coverage catastrophically low")
        high = float(metrics.get("high3000_share") or 0.0)
        hi = spectral_profile.get("high3000_share") or {}
        if high < float(hi.get("floor_fail") or 0.09):
            failures.append("presence below profile failure floor")
        elif high < float(hi.get("floor_warn") or 0.15):
            warnings.append("presence below profile warning floor")
        low = float(metrics.get("low200_share") or 0.0)
        lo = spectral_profile.get("low200_share") or {}
        if low > float(lo.get("ceiling_fail") or 0.45):
            failures.append("low end exceeds profile failure ceiling")
        elif low > float(lo.get("ceiling_warn") or 0.34):
            warnings.append("low end exceeds profile warning ceiling")
        return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": dict(metrics)}, {"component": "judge_gate", "backend": "project.quality_gate", "buffalo": False, "reason": "package adapter unavailable"}


def load_crate_sources(profile_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Expose the existing approved EarAtom pool as project compiler candidates."""
    try:
        from earcrate.app import EarcrateCore  # type: ignore

        core = EarcrateCore()
        pool = [dict(item) for item in core.approved_atom_pool(profile_id)]
        return pool, {"component": "crate", "backend": "EarcrateCore.approved_atom_pool", "buffalo": True, "count": len(pool)}
    except Exception as exc:
        raise ValidationError(f"could not load existing EarCrate pool for {profile_id}: {exc}") from exc


def source_identity(path: str | Path, sr: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = Path(path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValidationError(f"source file not found: {source}")
    byte_sha = sha256_file(source)
    try:
        info = sf.info(str(source))
        duration_hint = float(info.frames / info.samplerate) if info.samplerate else 0.0
    except Exception:
        duration_hint = 0.0
    pcm_sha, pcm_receipt = decoded_pcm_sha(source, sr, duration_hint)
    audio, decode_receipt = decode_audio(source, sr)
    return {
        "byte_sha256": byte_sha,
        "pcm_sha256": pcm_sha,
        "sample_rate": int(sr),
        "duration_samples": int(audio.size),
        "duration_s": float(audio.size / sr),
        "stat": {"size": int(source.stat().st_size), "mtime_ns": int(source.stat().st_mtime_ns)},
    }, [pcm_receipt, decode_receipt]
