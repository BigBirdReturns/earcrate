from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import optimize, signal

from . import buffalo
from .lower import TempoMap, lower_revision
from .model import seal_render_program, seal_revision
from .store import ProjectStore
from .util import (
    ProjectError,
    RenderError,
    SourceChangedError,
    ValidationError,
    atomic_write_json,
    ensure_within,
    now_utc,
    random_id,
    sha256_file,
    stable_id,
)


def _gain_curve(length: int, start: float, end: float, curve: str) -> np.ndarray:
    if length <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.linspace(0.0, 1.0, length, endpoint=True, dtype=np.float64)
    if curve == "equal_power_in":
        base = np.sin(t * math.pi / 2.0)
    elif curve == "equal_power_out":
        base = np.cos(t * math.pi / 2.0)
    elif curve == "s_curve":
        base = t * t * (3.0 - 2.0 * t)
    else:
        base = t
    return (float(start) + (float(end) - float(start)) * base).astype(np.float32)


def _apply_envelope(audio: np.ndarray, segments: list[Mapping[str, Any]]) -> np.ndarray:
    if audio.size == 0 or not segments:
        return audio
    env = np.ones(audio.shape[0], dtype=np.float32)
    for segment in segments:
        start = max(0, min(audio.shape[0], int(segment.get("start_sample") or 0)))
        end = max(start, min(audio.shape[0], int(segment.get("end_sample") or 0)))
        if end <= start:
            continue
        env[start:end] *= _gain_curve(
            end - start,
            float(segment.get("start_gain") if segment.get("start_gain") is not None else 1.0),
            float(segment.get("end_gain") if segment.get("end_gain") is not None else 1.0),
            str(segment.get("curve") or "linear"),
        )
    return audio * env[:, None]


def _equal_power_pan(mono: np.ndarray, pan: float) -> np.ndarray:
    pan = max(-1.0, min(1.0, float(pan)))
    angle = (pan + 1.0) * math.pi / 4.0
    left = math.cos(angle)
    right = math.sin(angle)
    return np.column_stack((mono * left, mono * right)).astype(np.float32)


def _tile_with_crossfade(segment: np.ndarray, target_len: int, crossfade: int = 512) -> np.ndarray:
    segment = np.asarray(segment, dtype=np.float32)
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    if segment.size == 0:
        raise RenderError("cannot tile an empty source segment")
    if segment.size >= target_len:
        return segment[:target_len].copy()
    crossfade = max(0, min(int(crossfade), segment.size // 4))
    out = np.zeros(target_len, dtype=np.float32)
    position = 0
    first = True
    while position < target_len:
        take = min(segment.size, target_len - position)
        if first or crossfade <= 0 or position < crossfade:
            out[position : position + take] += segment[:take]
        else:
            overlap = min(crossfade, take, position)
            if overlap:
                fade_out = np.cos(np.linspace(0.0, math.pi / 2.0, overlap, endpoint=True)).astype(np.float32)
                fade_in = np.sin(np.linspace(0.0, math.pi / 2.0, overlap, endpoint=True)).astype(np.float32)
                out[position - overlap : position] = out[position - overlap : position] * fade_out + segment[:overlap] * fade_in
            if take > overlap:
                out[position : position + take - overlap] += segment[overlap:take]
        position += max(1, take - crossfade if not first and crossfade else take)
        first = False
    return out[:target_len]


def _pitch_shift(audio: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    if abs(float(semitones)) < 1e-6:
        return audio
    channels = []
    for channel in range(audio.shape[1]):
        shifted = librosa.effects.pitch_shift(audio[:, channel].astype(np.float32), sr=sr, n_steps=float(semitones)).astype(np.float32)
        if shifted.size < audio.shape[0]:
            shifted = np.pad(shifted, (0, audio.shape[0] - shifted.size))
        channels.append(shifted[: audio.shape[0]])
    return np.column_stack(channels).astype(np.float32)


def _split_low_high(audio: np.ndarray, sr: int, cutoff: float = 170.0) -> tuple[np.ndarray, np.ndarray]:
    sos = signal.butter(2, cutoff, btype="lowpass", fs=sr, output="sos")
    low = np.column_stack([signal.sosfilt(sos, audio[:, ch]).astype(np.float32) for ch in range(audio.shape[1])])
    return low, audio - low


def _apply_transition_processing(
    audio: np.ndarray,
    event: Mapping[str, Any],
    transitions: list[Mapping[str, Any]],
    sr: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    out = audio
    event_id = str(event["event_id"])
    for transition in transitions:
        outgoing = event_id in set(transition.get("outgoing_event_ids") or [])
        incoming = event_id in set(transition.get("incoming_event_ids") or [])
        if not outgoing and not incoming:
            continue
        duration = int(transition.get("duration_samples") or 0)
        algorithm = str(transition.get("algorithm") or "hard_cut")
        if duration <= 0:
            receipts.append({"transition_id": transition["transition_id"], "event_id": event_id, "algorithm": algorithm, "processed": True, "zero_overlap": True})
            continue
        if outgoing:
            start = int(event["active_samples"])
            end = min(out.shape[0], start + duration)
        else:
            start = 0
            end = min(out.shape[0], duration)
        if end <= start:
            raise RenderError(f"transition {transition['transition_id']} has no event audio to process")
        region = out[start:end].copy()
        if algorithm == "echo_out" and outgoing:
            delay = max(1, int(round(sr * 0.18)))
            wet = region.copy()
            for tap, gain in ((1, 0.42), (2, 0.24), (3, 0.12)):
                offset = delay * tap
                if offset < wet.shape[0]:
                    wet[offset:] += region[: wet.shape[0] - offset] * gain
            region = wet
        elif algorithm == "low_stripped_overlap":
            low, high = _split_low_high(region, sr, 180.0)
            region = high + low * (0.18 if outgoing else 0.35)
        elif algorithm == "bass_swap":
            low, high = _split_low_high(region, sr, 170.0)
            t = np.linspace(0.0, 1.0, region.shape[0], endpoint=True, dtype=np.float32)
            if outgoing:
                low_env = np.clip(1.0 - t * 2.0, 0.0, 1.0)
            else:
                low_env = np.clip((t - 0.5) * 2.0, 0.0, 1.0)
            region = high + low * low_env[:, None]
        elif algorithm == "hard_cut_to_air":
            region *= 0.0
        out[start:end] = region
        receipts.append({
            "transition_id": transition["transition_id"],
            "event_id": event_id,
            "algorithm": algorithm,
            "processed": True,
            "zero_overlap": False,
            "region": [start, end],
            "side": "outgoing" if outgoing else "incoming",
        })
    return out, receipts


def _source_file_for_event(source: Mapping[str, Any], event: Mapping[str, Any]) -> Path:
    stem = str(event.get("stem") or "mix")
    stems = source.get("stems") or {}
    if stem not in stems:
        raise RenderError(f"source {source['source_id']} does not expose stem {stem}")
    path = Path(str(stems[stem])).expanduser().resolve()
    if not path.exists():
        raise RenderError(f"source stem missing: {path}")
    return path


def _render_event(
    event: Mapping[str, Any],
    source: Mapping[str, Any],
    source_audio: np.ndarray,
    transitions: list[Mapping[str, Any]],
    sr: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    start = int(event["source_start_sample"])
    end = int(event["source_end_sample"])
    if start < 0 or end <= start or end > source_audio.size:
        raise RenderError(f"event {event['event_id']} has invalid source range {start}:{end}/{source_audio.size}")
    active_source = source_audio[start:end].astype(np.float32, copy=True)
    post_roll = int(event.get("post_roll_samples") or 0)
    transform_rate = max(1e-6, float((event.get("transform") or {}).get("rate") or 1.0))
    target_render = int(event["render_samples"])
    source_needed = max(active_source.size, int(math.ceil(target_render * transform_rate)))
    available_end = min(source_audio.size, start + source_needed)
    material = source_audio[start:available_end].astype(np.float32, copy=True)
    if material.size < source_needed:
        if bool((event.get("loop") or {}).get("enabled")):
            material = _tile_with_crossfade(active_source, source_needed, int((event.get("loop") or {}).get("crossfade_samples") or 512))
        else:
            raise RenderError(f"event {event['event_id']} requires {source_needed} source samples, only {material.size} are available")
    fitted, resample_receipt = buffalo.resample_or_fit(material, target_render)
    stereo = _equal_power_pan(fitted, float(event.get("pan") or 0.0))
    stereo = _pitch_shift(stereo, sr, float((event.get("transform") or {}).get("pitch_semitones") or 0.0))
    gain = 10.0 ** (float(event.get("gain_db") or 0.0) / 20.0)
    stereo *= gain
    stereo = _apply_envelope(stereo, list(event.get("envelope") or []))
    stereo, transition_receipts = _apply_transition_processing(stereo, event, transitions, sr)
    return stereo.astype(np.float32), {
        "event_id": event["event_id"],
        "clip_id": event["clip_id"],
        "source_id": event["source_id"],
        "stem": event["stem"],
        "timeline_start_sample": event["timeline_start_sample"],
        "active_samples": event["active_samples"],
        "render_samples": event["render_samples"],
        "source_range": [start, end],
        "gain_db": event["gain_db"],
        "pan": event["pan"],
        "transform": event["transform"],
        "resample": resample_receipt,
        "transition_processing": transition_receipts,
        "executed": True,
    }


def render_program_audio(program: Mapping[str, Any], revision: Mapping[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    sr = int(program["sample_rate"])
    sources = revision["sources"]
    mix = np.zeros((int(program["total_samples"]), 2), dtype=np.float32)
    source_cache: dict[tuple[str, str], np.ndarray] = {}
    source_receipts: dict[str, Any] = {}
    event_receipts: list[dict[str, Any]] = []

    # Verify every selected source/stem before the first decode. A stem is a distinct
    # audio artifact and must never render under the parent mix identity by accident.
    selected_stems = sorted({(str(event["source_id"]), str(event.get("stem") or "mix")) for event in program["events"]})
    before_hashes: dict[str, str] = {}
    for source_id, stem in selected_stems:
        identity = ((program["source_identities"].get(source_id) or {}).get("stems") or {}).get(stem)
        if not identity:
            raise SourceChangedError(f"render program has no sealed identity for {source_id}:{stem}")
        path = Path(str(identity["path"])).expanduser().resolve()
        actual = sha256_file(path)
        key = f"{source_id}:{stem}"
        before_hashes[key] = actual
        if actual != str(identity["byte_sha256"]):
            raise SourceChangedError(f"source stem changed since revision: {path}")

    for event in program["events"]:
        source = sources[event["source_id"]]
        path = _source_file_for_event(source, event)
        key = (str(event["source_id"]), str(event["stem"]))
        if key not in source_cache:
            audio, decode_receipt = buffalo.decode_audio(path, sr)
            source_cache[key] = audio
            source_receipts[f"{key[0]}:{key[1]}"] = {"path": str(path), "samples": int(audio.size), "decode": decode_receipt}
        event_audio, receipt = _render_event(event, source, source_cache[key], list(program["transitions"]), sr)
        start = int(event["timeline_start_sample"])
        end = min(mix.shape[0], start + event_audio.shape[0])
        if end <= start or end - start != event_audio.shape[0]:
            raise RenderError(f"event {event['event_id']} extends outside render program")
        mix[start:end] += event_audio
        event_receipts.append(receipt)

    # Verify the same selected stem bytes again after all reads. This closes the
    # replace-during-render race for project-scoped stems as well as source mixes.
    after_hashes: dict[str, str] = {}
    for source_id, stem in selected_stems:
        identity = program["source_identities"][source_id]["stems"][stem]
        path = Path(str(identity["path"])).expanduser().resolve()
        actual = sha256_file(path)
        key = f"{source_id}:{stem}"
        after_hashes[key] = actual
        if actual != before_hashes[key]:
            raise SourceChangedError(f"source stem changed during render: {path}")

    transition_receipts = []
    processed_pairs = {(item["transition_id"], item["event_id"]) for receipt in event_receipts for item in receipt["transition_processing"]}
    for transition in program["transitions"]:
        expected_events = set(transition.get("outgoing_event_ids") or []) | set(transition.get("incoming_event_ids") or [])
        if int(transition.get("duration_samples") or 0) == 0:
            executed = True
        else:
            executed = all((transition["transition_id"], event_id) in processed_pairs for event_id in expected_events)
        if not executed:
            raise RenderError(f"planned transition did not execute: {transition['transition_id']}")
        transition_receipts.append({
            "transition_id": transition["transition_id"],
            "technique": transition["technique"],
            "algorithm": transition["algorithm"],
            "boundary_sample": transition["boundary_sample"],
            "duration_samples": transition["duration_samples"],
            "outgoing_event_ids": transition["outgoing_event_ids"],
            "incoming_event_ids": transition["incoming_event_ids"],
            "executed": True,
            "zero_overlap": int(transition.get("duration_samples") or 0) == 0,
            "fallback_used": False,
        })

    if len(event_receipts) != len(program["events"]):
        raise RenderError("selected event count does not match executed event count")
    return mix, {
        "program_sha": program["program_sha"],
        "selected_event_count": len(program["events"]),
        "executed_event_count": len(event_receipts),
        "events": event_receipts,
        "transitions": transition_receipts,
        "sources": source_receipts,
        "source_hashes_before": before_hashes,
        "source_hashes_after": after_hashes,
        "integrity": {"passed": True, "rule": "every selected event and transition executed exactly; no render-time fallback"},
    }


def _mono(audio: np.ndarray) -> np.ndarray:
    return np.mean(audio, axis=1, dtype=np.float32) if audio.ndim == 2 else np.asarray(audio, dtype=np.float32)


def _band_power(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    mono = _mono(audio)
    stft = np.abs(librosa.stft(mono, n_fft=4096, hop_length=2048)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    bins = stft.sum(axis=1)
    total = float(bins.sum() + 1e-12)
    low = float(bins[freqs < 200].sum() / total)
    high = float(bins[freqs > 3000].sum() / total)
    return freqs, bins, total, low, high


def _presence_weight(freqs: np.ndarray, lo_hz: float = 3000.0, hi_hz: float = 4000.0) -> np.ndarray:
    safe = np.maximum(freqs, np.finfo(float).tiny)
    t = np.log2(safe / lo_hz) / np.log2(hi_hz / lo_hz)
    t = np.clip(t, 0.0, 1.0)
    return t**3 * (t * (t * 6.0 - 15.0) + 10.0)


def _low_weight(freqs: np.ndarray, full_hz: float = 160.0, zero_hz: float = 260.0) -> np.ndarray:
    safe = np.maximum(freqs, np.finfo(float).tiny)
    t = np.log2(safe / full_hz) / np.log2(zero_hz / full_hz)
    t = np.clip(t, 0.0, 1.0)
    smooth = t**3 * (t * (t * 6.0 - 15.0) + 10.0)
    return 1.0 - smooth


def _share_after_gain(freqs: np.ndarray, bins: np.ndarray, boundary: float, weight: np.ndarray, db: float, high: bool) -> float:
    gain = np.power(10.0, db * weight / 10.0)
    corrected = bins * gain
    mask = freqs > boundary if high else freqs < boundary
    return float(corrected[mask].sum() / corrected.sum())


def _solve_presence(freqs: np.ndarray, bins: np.ndarray, target: float, max_db: float) -> float | None:
    weight = _presence_weight(freqs)
    current = _share_after_gain(freqs, bins, 3000.0, weight, 0.0, True)
    if current >= target:
        return 0.0
    maximum = _share_after_gain(freqs, bins, 3000.0, weight, max_db, True)
    if maximum < target:
        return None
    return float(optimize.brentq(lambda db: _share_after_gain(freqs, bins, 3000.0, weight, db, True) - target, 0.0, max_db, xtol=1e-5))


def _solve_low_cut(freqs: np.ndarray, bins: np.ndarray, target: float, max_cut_db: float) -> float:
    weight = _low_weight(freqs)
    current = _share_after_gain(freqs, bins, 200.0, weight, 0.0, False)
    if current <= target:
        return 0.0
    minimum = _share_after_gain(freqs, bins, 200.0, weight, -abs(max_cut_db), False)
    if minimum > target:
        return -abs(max_cut_db)
    return float(optimize.brentq(lambda db: _share_after_gain(freqs, bins, 200.0, weight, db, False) - target, -abs(max_cut_db), 0.0, xtol=1e-5))


def _loudness(audio: np.ndarray, sr: int) -> float:
    meter = pyln.Meter(sr)
    try:
        return float(meter.integrated_loudness(audio.astype(np.float64)))
    except Exception:
        mono = _mono(audio)
        rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
        return float(20.0 * math.log10(max(1e-9, rms)))


def derive_master_actions(audio: np.ndarray, sr: int, revision: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = revision["intent"]["compiled_policy"]["mastering"]
    freqs, bins, _, before_low, before_high = _band_power(audio, sr)
    actions: list[dict[str, Any]] = []
    low_policy = policy.get("low_shelf") or {}
    low_cut = 0.0
    if low_policy.get("allowed") and before_low > float(low_policy.get("trigger_share") or 1.0):
        low_cut = _solve_low_cut(freqs, bins, float(low_policy.get("target_share") or 0.20), float(low_policy.get("max_cut_db") or 14.0))
        if low_cut < -1e-4:
            actions.append({
                "action_id": stable_id("master", {"revision": revision["revision_sha"], "type": "low_shelf", "db": round(low_cut, 6)}),
                "type": "low_shelf",
                "parameters": {"full_hz": 160.0, "zero_hz": 260.0, "gain_db": round(low_cut, 6)},
                "decision": {"reason": "low200_share exceeded persona trigger", "before_share": before_low, "target_share": low_policy.get("target_share"), "max_cut_db": low_policy.get("max_cut_db")},
            })
            # Update bin powers before solving presence so the prediction matches action order.
            bins = bins * np.power(10.0, low_cut * _low_weight(freqs) / 10.0)
    high_policy = policy.get("presence_shelf") or {}
    required_high = 0.0
    if high_policy.get("allowed") and before_high < float(high_policy.get("trigger_share") or 0.0):
        required = _solve_presence(freqs, bins, float(high_policy.get("target_share") or 0.0), float(high_policy.get("max_boost_db") or 0.0))
        if required is None:
            uncapped = _solve_presence(freqs, bins, float(high_policy.get("target_share") or 0.0), float(high_policy.get("system_ceiling_db") or 6.0) * 4.0)
            raise RenderError(
                "presence restoration refused: required %.3f dB exceeds persona cap %.3f dB"
                % (float(uncapped or 24.0), float(high_policy.get("max_boost_db") or 0.0))
            )
        required_high = float(required)
        if required_high > 1e-4:
            actions.append({
                "action_id": stable_id("master", {"revision": revision["revision_sha"], "type": "presence_shelf", "db": round(required_high, 6)}),
                "type": "presence_shelf",
                "parameters": {
                    "measurement_boundary_hz": float(high_policy.get("measurement_boundary_hz") or 3000.0),
                    "lower_knee_hz": float(high_policy.get("lower_knee_hz") or 3000.0),
                    "upper_knee_hz": float(high_policy.get("upper_knee_hz") or 4000.0),
                    "gain_db": round(required_high, 6),
                },
                "decision": {
                    "reason": "high3000_share below persona trigger",
                    "before_share": before_high,
                    "target_share": high_policy.get("target_share"),
                    "persona_cap_db": high_policy.get("max_boost_db"),
                    "solver": "response_aware_brentq",
                },
            })
    current_lufs = _loudness(audio, sr)
    target_lufs = float(policy.get("integrated_lufs") or -14.0)
    actions.append({
        "action_id": stable_id("master", {"revision": revision["revision_sha"], "type": "loudness", "target": target_lufs}),
        "type": "loudness_normalize",
        "parameters": {"target_lufs": target_lufs, "measured_lufs": current_lufs, "gain_db": round(target_lufs - current_lufs, 6)},
        "decision": {"reason": "persona/system publication loudness", "target_lufs": target_lufs},
    })
    actions.append({
        "action_id": stable_id("master", {"revision": revision["revision_sha"], "type": "true_peak", "ceiling": policy.get("true_peak")}),
        "type": "true_peak_limit",
        "parameters": {"linear_ceiling": float(policy.get("true_peak") or 0.94)},
        "decision": {"reason": "publication peak backstop"},
    })
    return actions, {
        "before_low200_share": before_low,
        "before_high3000_share": before_high,
        "low_cut_db": low_cut,
        "required_high_boost_db": required_high,
        "persona_high_boost_cap_db": high_policy.get("max_boost_db"),
        "measured_lufs": current_lufs,
        "target_lufs": target_lufs,
    }


def _fft_gain(audio: np.ndarray, sr: int, gain_curve: np.ndarray) -> np.ndarray:
    n = int(2 ** math.ceil(math.log2(max(32, audio.shape[0]))))
    channels = []
    for channel in range(audio.shape[1]):
        spectrum = np.fft.rfft(audio[:, channel], n=n)
        channels.append(np.fft.irfft(spectrum * gain_curve, n=n)[: audio.shape[0]].astype(np.float32))
    return np.column_stack(channels).astype(np.float32)


def apply_master_actions(audio: np.ndarray, sr: int, actions: list[Mapping[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    out = np.asarray(audio, dtype=np.float32).copy()
    receipts: list[dict[str, Any]] = []
    for action in actions:
        typ = str(action["type"])
        params = action.get("parameters") or {}
        if typ in {"low_shelf", "presence_shelf"}:
            n = int(2 ** math.ceil(math.log2(max(32, out.shape[0]))))
            freqs = np.fft.rfftfreq(n, 1.0 / sr)
            if typ == "low_shelf":
                weight = _low_weight(freqs, float(params.get("full_hz") or 160.0), float(params.get("zero_hz") or 260.0))
            else:
                weight = _presence_weight(freqs, float(params.get("lower_knee_hz") or 3000.0), float(params.get("upper_knee_hz") or 4000.0))
            gain = np.power(10.0, float(params.get("gain_db") or 0.0) * weight / 20.0)
            out = _fft_gain(out, sr, gain)
        elif typ == "loudness_normalize":
            # The planner already measured the premaster and sealed the exact gain
            # into the revision. Re-measuring here would make the renderer invent a
            # new decision and can create one-LSB drift across identical renders.
            gain_db = float(params.get("gain_db") or 0.0)
            out *= 10.0 ** (gain_db / 20.0)
        elif typ == "true_peak_limit":
            ceiling = float(params.get("linear_ceiling") or 0.94)
            peak = float(np.max(np.abs(out))) if out.size else 0.0
            if peak > ceiling:
                out *= ceiling / peak
        else:
            raise RenderError(f"unsupported master action at execution: {typ}")
        receipts.append({"action_id": action["action_id"], "type": typ, "parameters": dict(params), "executed": True})
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32), receipts


def _finalize_mastering_revision(store: ProjectStore, project_id: str, revision: Mapping[str, Any], premaster: np.ndarray, sr: int) -> dict[str, Any]:
    actions, decision_receipt = derive_master_actions(premaster, sr, revision)
    child = json.loads(json.dumps(revision, ensure_ascii=False))
    child.pop("revision_sha", None)
    child["parent_revision_sha"] = revision["revision_sha"]
    child["created_at"] = now_utc()
    child["created_by"] = {"actor": "compiler", "reason": "mastering_plan_resolved", "compiler": "mastering_planner_v1"}
    child["mastering"] = {"state": "finalized", "actions": actions, "decision_receipt": decision_receipt}
    child.setdefault("decisions", []).append({
        "decision_id": stable_id("decision", {"parent": revision["revision_sha"], "master_actions": actions}),
        "kind": "mastering_plan",
        "selected": [action["action_id"] for action in actions],
        "evidence": decision_receipt,
        "policy": revision["intent"]["compiled_policy"]["mastering"],
    })
    sealed = seal_revision(child)
    committed = store.commit_revision(
        project_id,
        sealed,
        expected_head=revision["revision_sha"],
        event="mastering_finalized",
        event_payload={"actions": actions, "decision_receipt": decision_receipt},
    )
    return committed["revision"]


def _write_wav(path: Path, audio: np.ndarray, sr: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{random_id('tmp')}.wav")
    try:
        sf.write(str(tmp), audio, sr, subtype="PCM_24", format="WAV")
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return path


def render_project(
    store: ProjectStore,
    project_id: str,
    *,
    revision_sha: str | None = None,
    output: str | Path | None = None,
    finalize_mastering: bool = True,
) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    project = store.load_project(project_id)
    if revision_sha and revision_sha != project["active_revision_sha"] and finalize_mastering and revision["mastering"]["state"] != "finalized":
        raise ProjectError("cannot finalize mastering on a historical revision; redo or branch it into the active head first")
    run = store.new_run(project_id, revision["revision_sha"], "render")
    try:
        program = lower_revision(revision)
        store.write_run_artifact(run, "render_program.json", program)
        premaster, execution = render_program_audio(program, revision)
        store.write_run_artifact(run, "execution.json", execution)
        if revision["mastering"]["state"] != "finalized":
            if not finalize_mastering:
                raise RenderError("revision has unresolved mastering; render with finalization enabled")
            revision = _finalize_mastering_revision(store, project_id, revision, premaster, int(program["sample_rate"]))
            program = lower_revision(revision)
            store.write_run_artifact(run, "finalized_revision.json", revision)
            store.write_run_artifact(run, "final_render_program.json", program)
            final_audio, master_receipts = apply_master_actions(premaster, int(program["sample_rate"]), list(program["master_actions"]))
        else:
            # Exact revision render. The event program and master actions are both read
            # from the immutable revision; no decision is made in this branch.
            final_audio, master_receipts = apply_master_actions(premaster, int(program["sample_rate"]), list(program["master_actions"]))
        mono = _mono(final_audio)
        metrics, metrics_receipt = buffalo.quality_metrics(mono, int(program["sample_rate"]))
        spectral = revision["intent"]["compiled_policy"]["spectral"]
        gate, gate_receipt = buffalo.quality_gate(metrics, float(revision["intent"]["target_seconds"]), spectral)
        if not gate.get("passed"):
            outcome = {
                "ok": False,
                "failure_kind": "post_render_gate",
                "project_id": project_id,
                "revision_sha": revision["revision_sha"],
                "program_sha": program["program_sha"],
                "quality_gate": gate,
                "metrics_backend": metrics_receipt,
                "gate_backend": gate_receipt,
                "execution": execution,
                "master_actions": master_receipts,
            }
            store.write_run_artifact(run, "rejected_report.json", outcome)
            store.finish_run(run, False, outcome)
            raise RenderError("post-render gate refused publication: " + "; ".join(gate.get("failures") or []))

        project_render_dir = store.project_dir(project_id) / "renders"
        project_render_dir.mkdir(parents=True, exist_ok=True)
        default_name = f"{project_id}-{revision['revision_sha'][:12]}.wav"
        destination = Path(output).expanduser().resolve() if output else (project_render_dir / default_name).resolve()
        # Default writes remain inside the project. A caller-provided output is an
        # explicit outward action, but the canonical project copy is always retained.
        canonical_path = ensure_within(project_render_dir / default_name, project_render_dir)
        _write_wav(canonical_path, final_audio, int(program["sample_rate"]))
        if destination != canonical_path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(canonical_path, destination)
        audio_sha = sha256_file(canonical_path)
        after_freqs, after_bins, _, after_low, after_high = _band_power(final_audio, int(program["sample_rate"]))
        report = {
            "schema_version": 1,
            "run_id": run["run_id"],
            "project_id": project_id,
            "revision_sha": revision["revision_sha"],
            "parent_revision_sha": revision.get("parent_revision_sha"),
            "program_sha": program["program_sha"],
            "rendered_at": now_utc(),
            "sample_rate": int(program["sample_rate"]),
            "samples": int(final_audio.shape[0]),
            "channels": int(final_audio.shape[1]),
            "canonical_path": str(canonical_path),
            "output_path": str(destination),
            "audio_sha256": audio_sha,
            "quality_gate": gate,
            "metrics_backend": metrics_receipt,
            "gate_backend": gate_receipt,
            "execution": execution,
            "master_actions": master_receipts,
            "mastering_decision": revision["mastering"].get("decision_receipt"),
            "after_low200_share": after_low,
            "after_high3000_share": after_high,
            "integrity": {
                "passed": execution["integrity"]["passed"] and len(execution["events"]) == len(program["events"]) and all(item["executed"] for item in execution["transitions"]),
                "selected_events": len(program["events"]),
                "executed_events": len(execution["events"]),
                "planned_transitions": len(program["transitions"]),
                "executed_transitions": sum(1 for item in execution["transitions"] if item["executed"]),
                "fallbacks": 0,
            },
        }
        if not report["integrity"]["passed"]:
            raise RenderError("render integrity failed after publication staging")
        sidecar = canonical_path.with_suffix(".render_report.json")
        atomic_write_json(sidecar, report)
        if destination != canonical_path:
            atomic_write_json(destination.with_suffix(".render_report.json"), report)
        store.write_run_artifact(run, "report.json", report)
        store.finish_run(run, True, report)
        store.record_last_render(project_id, {
            "run_id": run["run_id"],
            "revision_sha": revision["revision_sha"],
            "program_sha": program["program_sha"],
            "path": str(destination),
            "audio_sha256": audio_sha,
            "quality_gate_passed": True,
        })
        return {"ok": True, "project_id": project_id, "revision": revision, "program": program, "report": report}
    except Exception as exc:
        # finish_run may already have written a gate-failure receipt.
        status_path = Path(str(run["global_path"])) / "status.json"
        already_finished = False
        with contextlib.suppress(Exception):
            already_finished = json.loads(status_path.read_text(encoding="utf-8")).get("state") in {"succeeded", "failed"}
        if not already_finished:
            store.finish_run(run, False, {"ok": False, "error": str(exc), "exception_type": type(exc).__name__, "project_id": project_id, "revision_sha": revision.get("revision_sha")})
        raise



def preview_project(
    store: ProjectStore,
    project_id: str,
    *,
    revision_sha: str | None = None,
    clip_id: str | None = None,
    start_beat: float | None = None,
    end_beat: float | None = None,
    output: str | Path,
) -> dict[str, Any]:
    """Render an audition artifact without changing the project head.

    A clip preview lowers only that selected event. A beat-range preview renders the
    exact score program and crops the requested timeline range, preserving any
    transitions inside it. Mastering is deliberately omitted so audition reflects the
    editable score rather than an unrelated publication action.
    """
    if bool(clip_id) == bool(start_beat is not None or end_beat is not None):
        raise ValidationError("preview requires exactly one of --clip-id or a --start-beat/--end-beat range")
    revision = store.load_revision(project_id, revision_sha)
    run = store.new_run(project_id, revision["revision_sha"], "preview")
    try:
        program = lower_revision(revision)
        sr = int(program["sample_rate"])
        if clip_id:
            matches = [event for event in program["events"] if str(event["clip_id"]) == str(clip_id)]
            if len(matches) != 1:
                raise ValidationError(f"clip not found in executable score: {clip_id}")
            event = dict(matches[0])
            event["timeline_start_sample"] = 0
            preview_program = seal_render_program({
                **{key: value for key, value in program.items() if key not in {"program_sha", "created_at", "events", "transitions", "master_actions", "total_samples"}},
                "events": [event],
                "transitions": [],
                "master_actions": [],
                "total_samples": int(event["render_samples"]),
                "preview": {"kind": "clip", "clip_id": str(clip_id), "source_revision_sha": revision["revision_sha"]},
            })
            audio, execution = render_program_audio(preview_program, revision)
            selection = preview_program["preview"]
        else:
            if start_beat is None or end_beat is None:
                raise ValidationError("beat-range preview requires both --start-beat and --end-beat")
            if float(start_beat) < 0 or float(end_beat) <= float(start_beat):
                raise ValidationError("preview beat range is invalid")
            audio_full, execution = render_program_audio(program, revision)
            tempo = TempoMap(list(revision["tempo_map"]), sr)
            start_sample = tempo.beat_to_sample(float(start_beat))
            end_sample = min(audio_full.shape[0], tempo.beat_to_sample(float(end_beat)))
            if end_sample <= start_sample:
                raise ValidationError("preview range contains no executable audio")
            audio = audio_full[start_sample:end_sample].copy()
            preview_program = program
            selection = {"kind": "beat_range", "start_beat": float(start_beat), "end_beat": float(end_beat), "start_sample": start_sample, "end_sample": end_sample}
        destination = Path(output).expanduser().resolve()
        _write_wav(destination, audio, sr)
        report = {
            "schema_version": 1,
            "run_id": run["run_id"],
            "kind": "preview",
            "project_id": project_id,
            "revision_sha": revision["revision_sha"],
            "program_sha": preview_program["program_sha"],
            "selection": selection,
            "output_path": str(destination),
            "audio_sha256": sha256_file(destination),
            "sample_rate": sr,
            "samples": int(audio.shape[0]),
            "mastering_applied": False,
            "execution": execution,
        }
        store.write_run_artifact(run, "preview_program.json", preview_program)
        store.write_run_artifact(run, "execution.json", execution)
        store.write_run_artifact(run, "report.json", report)
        store.finish_run(run, True, report)
        return {"ok": True, "report": report}
    except Exception as exc:
        store.finish_run(run, False, {"ok": False, "error": str(exc), "exception_type": type(exc).__name__})
        raise

def verify_render(store: ProjectStore, project_id: str, wav_path: str | Path, *, revision_sha: str | None = None) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    path = Path(wav_path).expanduser().resolve()
    audio, sr = sf.read(str(path), always_2d=True, dtype="float32")
    mono = _mono(audio)
    metrics, metrics_receipt = buffalo.quality_metrics(mono, int(sr))
    gate, gate_receipt = buffalo.quality_gate(metrics, float(revision["intent"]["target_seconds"]), revision["intent"]["compiled_policy"]["spectral"])
    return {
        "ok": bool(gate.get("passed")),
        "project_id": project_id,
        "revision_sha": revision["revision_sha"],
        "path": str(path),
        "audio_sha256": sha256_file(path),
        "quality_gate": gate,
        "metrics_backend": metrics_receipt,
        "gate_backend": gate_receipt,
    }
