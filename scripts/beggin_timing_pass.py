#!/usr/bin/env python3
from __future__ import annotations

"""Reality-first phrase-local timing campaign for Beggin' percussion transplantation.

The tool never downloads media and never writes inside the source directories. It
uses exact local source bytes, an existing Måneskin vocal timing witness, and a
locally separated drum stem to produce three level-matched short auditions:

1. onset-only placement, no vocal time stretch;
2. one global first-hook block stretch;
3. constrained-DTW phrase placement with terminal calls independently anchored.

Every analysis, time map, command, output identity, signal measurement, and blind
assignment is retained beneath a new output directory. Nothing here promotes a
provider trial into provider adoption or a render into musical acceptance.
"""

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

try:
    import numpy as np
    from scipy import fft as scipy_fft
    from scipy import ndimage, signal
except Exception as exc:  # pragma: no cover - checked by CLI preflight
    raise SystemExit(f"numpy and scipy are required: {type(exc).__name__}: {exc}")

SCHEMA_VERSION = 1
DEFAULT_TARGET_WITNESS_SHA256 = "0dfa10dbfe5523e261bfb2cbf4219ec0e77692d555977e4107ff10a22b790d81"
DEFAULT_SOURCE_START = 4.556803
DEFAULT_SOURCE_END = 22.974059
DEFAULT_TARGET_START = 0.666939
DEFAULT_TARGET_END = 17.499592
DEFAULT_OUTPUT_DURATION = 20.0
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_RENDER_RATE = 48000
DEFAULT_TARGET_LUFS = -14.0
DEFAULT_TARGET_TP = -2.0
DEFAULT_VOCAL_COMPONENT_LUFS = -18.0
DEFAULT_DRUM_COMPONENT_LUFS = -20.0
REVIEW_DIMENSIONS = [
    "Frankie vocal authority",
    "terminal phrase placement",
    "percussion impact",
    "groove continuity",
    "separator residue",
    "cymbal and room continuity",
    "source recognizability",
    "desire to hear the next phrase",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def seal(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    payload.pop(field, None)
    payload[field] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def validate_seal(value: Mapping[str, Any], field: str) -> str:
    payload = deepcopy(dict(value))
    claimed = str(payload.pop(field, "")).lower()
    if not is_sha256(claimed):
        raise ValueError(f"invalid or missing {field}")
    actual = sha256_bytes(canonical_json_bytes(payload))
    if actual != claimed:
        raise ValueError(f"{field} mismatch: expected {claimed}, computed {actual}")
    return claimed


def atomic_write_bytes(path: str | Path, body: bytes, *, exclusive: bool = True) -> Path:
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and target.exists():
        raise FileExistsError(target)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}")
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and target.exists():
            raise FileExistsError(target)
        os.replace(temporary, target)
        if os.name != "nt":
            descriptor = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def write_json(path: str | Path, value: Mapping[str, Any], *, exclusive: bool = True) -> Path:
    return atomic_write_bytes(
        path,
        (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        exclusive=exclusive,
    )


def write_text(path: str | Path, value: str, *, exclusive: bool = True) -> Path:
    return atomic_write_bytes(path, value.encode("utf-8"), exclusive=exclusive)


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def require_new_directory(path: str | Path) -> Path:
    target = Path(path).expanduser().absolute()
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise FileExistsError(f"output directory must be new or empty: {target}")
    else:
        target.mkdir(parents=True)
    return target


def run_command(argv: Sequence[str], *, cwd: str | Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "command failed\n"
            + json.dumps(list(argv), ensure_ascii=False)
            + f"\nreturncode={process.returncode}\nstdout={process.stdout[-4000:].decode(errors='replace')}"
            + f"\nstderr={process.stderr[-8000:].decode(errors='replace')}"
        )
    return process


def executable_identity(name: str) -> dict[str, Any]:
    resolved = shutil.which(name)
    if not resolved:
        raise FileNotFoundError(f"required executable not found on PATH: {name}")
    path = Path(resolved).resolve()
    process = subprocess.run([str(path), "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    first = process.stdout.decode(errors="replace").splitlines()[:2]
    return {
        "name": name,
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": int(path.stat().st_size),
        "version_lines": first,
    }


def probe_audio(path: str | Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    source = Path(path).expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"audio input must be a regular non-symlink file: {source}")
    process = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,bit_rate,format_name:stream=codec_name,sample_rate,channels,channel_layout",
            "-of",
            "json",
            str(source),
        ],
        timeout=60,
    )
    value = json.loads(process.stdout.decode("utf-8"))
    streams = value.get("streams") or []
    if not streams:
        raise ValueError(f"no audio stream found: {source}")
    stream = dict(streams[0])
    fmt = dict(value.get("format") or {})
    return {
        "path": str(source),
        "sha256": sha256_file(source),
        "bytes": int(source.stat().st_size),
        "duration_seconds": float(fmt.get("duration") or 0.0),
        "format_name": fmt.get("format_name"),
        "bit_rate": int(fmt.get("bit_rate") or 0) or None,
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0) or None,
        "channels": int(stream.get("channels") or 0) or None,
        "channel_layout": stream.get("channel_layout"),
    }


def _verify_bound_path(path: Path, claimed_sha: str | None = None, claimed_bytes: int | None = None) -> dict[str, Any]:
    source = path.expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"bound source is missing or unsafe: {source}")
    before = source.stat()
    digest = sha256_file(source)
    after = source.stat()
    if int(before.st_size) != int(after.st_size) or int(before.st_mtime_ns) != int(after.st_mtime_ns):
        raise ValueError(f"source changed during hashing: {source}")
    if claimed_sha and digest != claimed_sha:
        raise ValueError(f"bound source SHA-256 mismatch: {source}")
    if claimed_bytes is not None and int(after.st_size) != int(claimed_bytes):
        raise ValueError(f"bound source byte-count mismatch: {source}")
    return {"path": str(source), "sha256": digest, "bytes": int(after.st_size)}


def source_from_binding(path: str | Path) -> tuple[Path, dict[str, Any]]:
    value = load_json(path)
    if value.get("binding_sha256"):
        validate_seal(value, "binding_sha256")
    artifact_path = value.get("artifact_path")
    if not artifact_path:
        raise ValueError(f"binding has no artifact_path: {path}")
    identity = _verify_bound_path(
        Path(str(artifact_path)),
        str(value.get("artifact_sha256") or "") or None,
        int(value["artifact_bytes"]) if value.get("artifact_bytes") is not None else None,
    )
    return Path(identity["path"]), value


def locate_witness(inventory_path: str | Path, digest: str) -> dict[str, Any]:
    if not is_sha256(digest):
        raise ValueError("witness digest must be a SHA-256 identity")
    inventory = load_json(inventory_path)
    matches: list[dict[str, Any]] = []
    declared_only: list[str] = []
    for row in inventory.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        raw = str(row.get("raw_sha256") or "").lower()
        item_id = str(row.get("item_id") or "")
        if raw == digest:
            path_text = str(row.get("absolute_path") or "")
            status = str(row.get("hash_status") or "")
            if path_text and status.startswith("strong"):
                try:
                    identity = _verify_bound_path(Path(path_text), digest, int(row.get("bytes") or 0))
                    identity["item_id"] = item_id
                    identity["hash_status"] = status
                    matches.append(identity)
                except Exception:
                    pass
        for declared in (row.get("metadata") or {}).get("declared_sha256") or []:
            if str((declared or {}).get("sha256") or "").lower() == digest:
                declared_only.append(item_id)
    if not matches:
        suffix = f"; declared only by {len(set(declared_only))} inventory rows" if declared_only else ""
        raise FileNotFoundError(f"exact current witness bytes not found for {digest}{suffix}")
    matches.sort(key=lambda row: (row["path"], row["item_id"]))
    return {
        "schema": "earcrate.timing_witness_location.v1",
        "inventory": str(Path(inventory_path).expanduser().absolute()),
        "sha256": digest,
        "matches": matches,
        "selected": matches[0],
        "declared_only_item_ids": sorted(set(declared_only)),
        "selected_by": "lexicographically first exact current strong-identity path",
    }


def decode_mono(
    path: str | Path,
    *,
    start: float,
    end: float,
    sample_rate: int,
    ffmpeg: str,
) -> np.ndarray:
    if end <= start:
        raise ValueError("audio decode interval must have positive duration")
    process = run_command(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{start:.9f}",
            "-to",
            f"{end:.9f}",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=120,
    )
    audio = np.frombuffer(process.stdout, dtype="<f4").astype(np.float64)
    expected = int(round((end - start) * sample_rate))
    if audio.size < max(100, int(expected * 0.8)):
        raise ValueError(f"decoded interval is unexpectedly short: {audio.size} samples")
    if audio.size > expected:
        audio = audio[:expected]
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-8:
        raise ValueError(f"decoded interval is silent: {path}")
    audio = audio - float(np.mean(audio))
    scale = float(np.percentile(np.abs(audio), 99.5))
    if scale > 1e-8:
        audio = np.clip(audio / scale, -4.0, 4.0)
    return audio


def hz_to_mel(value: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(value) / 700.0)


def mel_to_hz(value: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(value) / 2595.0) - 1.0)


def mel_filterbank(sample_rate: int, n_fft: int, n_mels: int, fmin: float = 80.0, fmax: float | None = None) -> np.ndarray:
    maximum = float(fmax or sample_rate / 2.0)
    mel_points = np.linspace(hz_to_mel(fmin), hz_to_mel(maximum), n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)
    bank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for index in range(n_mels):
        left, center, right = int(bins[index]), int(bins[index + 1]), int(bins[index + 2])
        if center <= left:
            center = min(left + 1, n_fft // 2)
        if right <= center:
            right = min(center + 1, n_fft // 2)
        if center > left:
            bank[index, left:center] = np.linspace(0.0, 1.0, center - left, endpoint=False)
        if right > center:
            bank[index, center:right] = np.linspace(1.0, 0.0, right - center, endpoint=False)
    row_sums = bank.sum(axis=1, keepdims=True)
    bank = bank / np.maximum(row_sums, 1e-12)
    return bank


@dataclass
class FeatureBundle:
    features: np.ndarray
    times: np.ndarray
    rms_db: np.ndarray
    hop_seconds: float
    duration_seconds: float


def audio_features(audio: np.ndarray, sample_rate: int) -> FeatureBundle:
    window = max(256, int(round(0.025 * sample_rate)))
    hop = max(64, int(round(0.010 * sample_rate)))
    n_fft = 1
    while n_fft < window:
        n_fft *= 2
    frequencies, times, stft = signal.stft(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=window,
        noverlap=window - hop,
        nfft=n_fft,
        boundary=None,
        padded=False,
    )
    del frequencies
    power = np.abs(stft) ** 2 + 1e-12
    bank = mel_filterbank(sample_rate, n_fft, 40, fmin=80.0, fmax=min(7600.0, sample_rate / 2.0))
    log_mel = np.log(np.maximum(bank @ power, 1e-12))
    mfcc = scipy_fft.dct(log_mel, type=2, axis=0, norm="ortho")[:16].T
    delta = np.gradient(mfcc, axis=0)
    rms = np.sqrt(np.maximum(power.mean(axis=0), 1e-14))
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    flux = np.maximum(np.diff(log_mel, axis=1, prepend=log_mel[:, :1]), 0.0).mean(axis=0)
    combined = np.column_stack([mfcc, delta, rms_db, flux])
    median = np.median(combined, axis=0)
    mad = np.median(np.abs(combined - median), axis=0)
    combined = (combined - median) / np.maximum(mad * 1.4826, 1e-4)
    combined = np.clip(combined, -8.0, 8.0)
    norms = np.linalg.norm(combined, axis=1, keepdims=True)
    combined = combined / np.maximum(norms, 1e-8)
    return FeatureBundle(
        features=combined.astype(np.float32),
        times=times.astype(np.float64),
        rms_db=rms_db.astype(np.float64),
        hop_seconds=float(hop / sample_rate),
        duration_seconds=float(audio.size / sample_rate),
    )


def _runs(mask: np.ndarray) -> list[tuple[int, int, bool]]:
    if mask.size == 0:
        return []
    result: list[tuple[int, int, bool]] = []
    start = 0
    current = bool(mask[0])
    for index in range(1, mask.size):
        value = bool(mask[index])
        if value != current:
            result.append((start, index, current))
            start = index
            current = value
    result.append((start, mask.size, current))
    return result


def clean_activity(mask: np.ndarray, *, min_active_frames: int, fill_gap_frames: int) -> np.ndarray:
    value = mask.astype(bool).copy()
    for start, end, active in _runs(value):
        if active and end - start < min_active_frames:
            value[start:end] = False
    for start, end, active in _runs(value):
        if not active and start > 0 and end < value.size and end - start <= fill_gap_frames:
            value[start:end] = True
    return value


def activity_segments(bundle: FeatureBundle, *, merge_gap_seconds: float = 0.14, min_active_seconds: float = 0.10) -> list[dict[str, float]]:
    smooth = ndimage.gaussian_filter1d(bundle.rms_db, sigma=max(1.0, 0.025 / bundle.hop_seconds))
    low = float(np.percentile(smooth, 20.0))
    high = float(np.percentile(smooth, 92.0))
    threshold = low + 0.32 * max(6.0, high - low)
    threshold = min(threshold, high - 1.5)
    active = smooth >= threshold
    active = clean_activity(
        active,
        min_active_frames=max(1, int(round(min_active_seconds / bundle.hop_seconds))),
        fill_gap_frames=max(1, int(round(0.06 / bundle.hop_seconds))),
    )
    raw: list[tuple[float, float]] = []
    for start, end, state in _runs(active):
        if state:
            raw.append((float(bundle.times[start]), float(bundle.times[min(end - 1, bundle.times.size - 1)] + bundle.hop_seconds)))
    merged: list[list[float]] = []
    for start, end in raw:
        if merged and start - merged[-1][1] < merge_gap_seconds:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    output: list[dict[str, float]] = []
    for start, end in merged:
        if end - start >= min_active_seconds:
            output.append({"start": max(0.0, start), "end": min(bundle.duration_seconds, end), "duration": end - start})
    return output


def constrained_dtw(source: FeatureBundle, target: FeatureBundle, *, band_seconds: float = 2.0, insertion_penalty: float = 0.10) -> dict[str, Any]:
    x = source.features
    y = target.features
    n, m = int(x.shape[0]), int(y.shape[0])
    if n < 2 or m < 2:
        raise ValueError("feature sequences are too short for DTW")
    cost = 1.0 - np.clip(x @ y.T, -1.0, 1.0)
    matrix = np.full((n, m), np.inf, dtype=np.float64)
    back = np.full((n, m), -1, dtype=np.int8)
    band = max(4, int(round(band_seconds / min(source.hop_seconds, target.hop_seconds))))
    matrix[0, 0] = float(cost[0, 0])
    for i in range(n):
        center = int(round(i * (m - 1) / max(1, n - 1)))
        j0 = max(0, center - band)
        j1 = min(m, center + band + 1)
        for j in range(j0, j1):
            if i == 0 and j == 0:
                continue
            choices: list[tuple[float, int]] = []
            if i > 0 and j > 0:
                choices.append((matrix[i - 1, j - 1], 0))
            if i > 0:
                choices.append((matrix[i - 1, j] + insertion_penalty, 1))
            if j > 0:
                choices.append((matrix[i, j - 1] + insertion_penalty, 2))
            best, direction = min(choices, key=lambda row: row[0])
            if math.isfinite(best):
                matrix[i, j] = float(cost[i, j]) + best
                back[i, j] = direction
    if not math.isfinite(float(matrix[-1, -1])):
        raise ValueError("constrained DTW could not connect the selected windows")
    i, j = n - 1, m - 1
    path: list[tuple[int, int]] = [(i, j)]
    while i > 0 or j > 0:
        direction = int(back[i, j])
        if direction == 0:
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        elif direction == 2:
            j -= 1
        else:
            raise ValueError("DTW backtrace is incomplete")
        path.append((i, j))
    path.reverse()
    source_index = np.array([row[0] for row in path], dtype=np.int32)
    target_index = np.array([row[1] for row in path], dtype=np.int32)
    mapping = np.full(n, np.nan, dtype=np.float64)
    for index in range(n):
        matched = target_index[source_index == index]
        if matched.size:
            mapping[index] = float(np.median(matched))
    valid = np.flatnonzero(np.isfinite(mapping))
    mapping = np.interp(np.arange(n), valid, mapping[valid])
    mapping = np.maximum.accumulate(mapping)
    source_times = source.times
    target_times = np.interp(mapping, np.arange(m), target.times)
    return {
        "normalized_cost": float(matrix[-1, -1] / max(1, len(path))),
        "path_length": len(path),
        "band_frames": band,
        "source_frames": n,
        "target_frames": m,
        "source_times": source_times,
        "mapped_target_times": target_times,
        "path": path,
    }


def _map_time(mapping: Mapping[str, Any], time_seconds: float) -> float:
    source_times = np.asarray(mapping["source_times"], dtype=np.float64)
    target_times = np.asarray(mapping["mapped_target_times"], dtype=np.float64)
    return float(np.interp(time_seconds, source_times, target_times))


def _gap_midpoints(segments: Sequence[Mapping[str, float]], duration: float) -> list[float]:
    values: list[float] = []
    previous = 0.0
    for row in segments:
        start = float(row["start"])
        if start > previous:
            values.append((previous + start) / 2.0)
        previous = max(previous, float(row["end"]))
    if previous < duration:
        values.append((previous + duration) / 2.0)
    return values


def _snap(value: float, candidates: Sequence[float], tolerance: float) -> float:
    if not candidates:
        return value
    best = min(candidates, key=lambda item: abs(item - value))
    return float(best if abs(best - value) <= tolerance else value)


def _compress_boundaries(boundaries: Iterable[float], *, start: float, end: float, minimum: float = 0.25, maximum_segments: int = 12) -> list[float]:
    values = sorted(set(max(start, min(end, float(value))) for value in boundaries))
    if not values or values[0] > start + 1e-6:
        values.insert(0, start)
    else:
        values[0] = start
    if values[-1] < end - 1e-6:
        values.append(end)
    else:
        values[-1] = end
    changed = True
    while changed and len(values) > 2:
        changed = False
        for index in range(1, len(values) - 1):
            if values[index] - values[index - 1] < minimum or values[index + 1] - values[index] < minimum:
                del values[index]
                changed = True
                break
    while len(values) - 1 > maximum_segments:
        interior = [
            (min(values[index] - values[index - 1], values[index + 1] - values[index]), index)
            for index in range(1, len(values) - 1)
        ]
        _, remove = min(interior)
        del values[remove]
    return values


def _atempo_chain(ratio: float) -> str:
    if ratio <= 0:
        raise ValueError("atempo ratio must be positive")
    values: list[float] = []
    remaining = ratio
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    values.append(remaining)
    return ",".join(f"atempo={value:.9f}" for value in values)


def _segment(source_start: float, source_end: float, target_start: float, target_end: float, mode: str, **extra: Any) -> dict[str, Any]:
    source_duration = source_end - source_start
    target_duration = target_end - target_start
    if source_duration <= 0.01 or target_duration <= 0.01:
        raise ValueError(f"invalid timing segment {mode}: {source_duration=}, {target_duration=}")
    ratio = source_duration / target_duration
    value: dict[str, Any] = {
        "source_start": float(source_start),
        "source_end": float(source_end),
        "source_duration": float(source_duration),
        "target_start": float(target_start),
        "target_end": float(target_end),
        "target_duration": float(target_duration),
        "atempo": float(ratio),
        "mode": mode,
    }
    value.update(extra)
    return value


def create_time_maps(
    *,
    source_bundle: FeatureBundle,
    target_bundle: FeatureBundle,
    mapping: Mapping[str, Any],
    source_segments: Sequence[Mapping[str, float]],
    target_segments: Sequence[Mapping[str, float]],
    source_window_start: float,
    source_window_end: float,
    target_window_start: float,
    target_window_end: float,
    final_call_count: int,
) -> dict[str, list[dict[str, Any]]]:
    source_span = source_window_end - source_window_start
    target_span = target_window_end - target_window_start
    onset_only = [
        _segment(
            source_window_start,
            source_window_end,
            target_window_start,
            target_window_start + source_span,
            "onset_only_native_duration",
        )
    ]
    global_block = [
        _segment(
            source_window_start,
            source_window_end,
            target_window_start,
            target_window_end,
            "global_endpoint_block",
        )
    ]

    call_count = min(final_call_count, len(source_segments), len(target_segments))
    source_calls = list(source_segments[-call_count:]) if call_count else []
    target_calls = list(target_segments[-call_count:]) if call_count else []
    first_call_source = float(source_calls[0]["start"]) if source_calls else source_span
    first_call_target = float(target_calls[0]["start"]) if target_calls else target_span

    source_gaps = _gap_midpoints([row for row in source_segments if float(row["end"]) <= first_call_source + 1e-6], first_call_source)
    early_boundaries = _compress_boundaries([0.0, *source_gaps, first_call_source], start=0.0, end=first_call_source, minimum=0.30, maximum_segments=8)
    target_gap_points = _gap_midpoints([row for row in target_segments if float(row["end"]) <= first_call_target + 1e-6], first_call_target)
    mapped_boundaries: list[float] = []
    for index, boundary in enumerate(early_boundaries):
        if index == 0:
            mapped = 0.0
        elif index == len(early_boundaries) - 1:
            mapped = first_call_target
        else:
            mapped = _snap(_map_time(mapping, boundary), target_gap_points, tolerance=0.32)
        mapped_boundaries.append(mapped)
    mapped_boundaries = list(np.maximum.accumulate(np.asarray(mapped_boundaries, dtype=np.float64)))
    if mapped_boundaries:
        mapped_boundaries[-1] = first_call_target

    phrase_local: list[dict[str, Any]] = []
    for index in range(max(0, len(early_boundaries) - 1)):
        s0, s1 = early_boundaries[index], early_boundaries[index + 1]
        t0, t1 = mapped_boundaries[index], mapped_boundaries[index + 1]
        if s1 - s0 >= 0.05 and t1 - t0 >= 0.05:
            phrase_local.append(
                _segment(
                    source_window_start + s0,
                    source_window_start + s1,
                    target_window_start + t0,
                    target_window_start + t1,
                    "dtw_phrase_partition",
                )
            )

    previous_source = first_call_source
    previous_target = first_call_target
    call_rows: list[dict[str, Any]] = []
    for index, (source_call, target_call) in enumerate(zip(source_calls, target_calls), start=1):
        source_call_start = float(source_call["start"])
        source_call_end = float(source_call["end"])
        target_call_start = float(target_call["start"])
        target_call_end = float(target_call["end"])
        if source_call_start > previous_source + 0.04 and target_call_start > previous_target + 0.04:
            phrase_local.append(
                _segment(
                    source_window_start + previous_source,
                    source_window_start + source_call_start,
                    target_window_start + previous_target,
                    target_window_start + target_call_start,
                    "inter_call_gap",
                )
            )
        source_duration = source_call_end - source_call_start
        target_duration = target_call_end - target_call_start
        raw_ratio = source_duration / max(target_duration, 0.01)
        ratio = min(1.08, max(0.92, raw_ratio))
        rendered_duration = source_duration / ratio
        call_end = target_call_start + rendered_duration
        if index < call_count:
            next_target_start = float(target_calls[index]["start"])
            if call_end > next_target_start - 0.02:
                available = max(0.08, next_target_start - target_call_start - 0.02)
                ratio = min(1.12, max(ratio, source_duration / available))
                rendered_duration = source_duration / ratio
                call_end = target_call_start + rendered_duration
        row = _segment(
            source_window_start + source_call_start,
            source_window_start + source_call_end,
            target_window_start + target_call_start,
            target_window_start + call_end,
            "terminal_call_onset_locked",
            call_index=index,
            raw_target_duration=target_duration,
            raw_ratio=raw_ratio,
            duration_preservation_cap=[0.92, 1.08],
        )
        phrase_local.append(row)
        call_rows.append(row)
        previous_source = source_call_end
        previous_target = call_end

    if previous_source < source_span - 0.04 and previous_target < target_span - 0.04:
        phrase_local.append(
            _segment(
                source_window_start + previous_source,
                source_window_end,
                target_window_start + previous_target,
                target_window_end,
                "post_call_tail",
            )
        )

    phrase_local.sort(key=lambda row: (row["target_start"], row["source_start"]))
    return {
        "onset_only": onset_only,
        "global_block": global_block,
        "phrase_local": phrase_local,
        "terminal_calls": call_rows,
    }


def render_vocal_map(
    *,
    source: Path,
    segments: Sequence[Mapping[str, Any]],
    output: Path,
    output_duration: float,
    ffmpeg: str,
) -> dict[str, Any]:
    if not segments:
        raise ValueError("vocal time map has no segments")
    filters: list[str] = []
    labels: list[str] = []
    for index, row in enumerate(segments):
        source_start = float(row["source_start"])
        source_end = float(row["source_end"])
        target_start = float(row["target_start"])
        target_duration = float(row["target_duration"])
        ratio = float(row["atempo"])
        fade = min(0.008, max(0.002, target_duration / 10.0))
        fade_out_start = max(0.0, target_duration - fade)
        label = f"s{index}"
        chain = _atempo_chain(ratio)
        filters.append(
            f"[0:a]atrim=start={source_start:.9f}:end={source_end:.9f},"
            f"asetpts=PTS-STARTPTS,aresample={DEFAULT_RENDER_RATE},{chain},"
            f"afade=t=in:st=0:d={fade:.6f},afade=t=out:st={fade_out_start:.6f}:d={fade:.6f},"
            f"adelay={int(round(target_start * 1000.0))}:all=1[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
        + f"atrim=start=0:end={output_duration:.9f},asetpts=N/SR/TB[out]"
    )
    graph = ";".join(filters)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(source),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-ar",
        str(DEFAULT_RENDER_RATE),
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(output),
    ]
    run_command(command, timeout=180)
    return {"argv": command, "filter_complex": graph, "output": str(output), "sha256": sha256_file(output)}


def loudness(path: str | Path, ffmpeg: str) -> dict[str, float | None]:
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    text = process.stderr.decode(errors="replace")
    matches = re.findall(r"\{\s*\"input_i\".*?\}", text, flags=re.DOTALL)
    if process.returncode != 0 or not matches:
        raise RuntimeError(f"loudness measurement failed for {path}: {text[-4000:]}")
    value = json.loads(matches[-1])

    def number(key: str) -> float | None:
        raw = str(value.get(key) or "")
        if raw in {"-inf", "inf", "+inf", "nan"}:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    return {
        "integrated_lufs": number("input_i"),
        "true_peak_dbtp": number("input_tp"),
        "lra_lu": number("input_lra"),
        "threshold_lufs": number("input_thresh"),
        "target_offset_db": number("target_offset"),
    }


def component_gain(path: Path, target_lufs: float, ffmpeg: str) -> tuple[float, dict[str, Any]]:
    measurement = loudness(path, ffmpeg)
    current = measurement["integrated_lufs"]
    if current is None:
        raise ValueError(f"component is silent or unmeasurable: {path}")
    return float(target_lufs - current), measurement


def mix_and_match_loudness(
    *,
    vocal: Path,
    drums: Path,
    output: Path,
    output_duration: float,
    ffmpeg: str,
    vocal_target_lufs: float,
    drum_target_lufs: float,
    final_target_lufs: float,
    final_target_tp: float,
) -> dict[str, Any]:
    vocal_gain, vocal_measurement = component_gain(vocal, vocal_target_lufs, ffmpeg)
    drum_gain, drum_measurement = component_gain(drums, drum_target_lufs, ffmpeg)
    premix = output.with_name(f".{output.stem}.premix.wav")
    mix_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(vocal),
        "-i",
        str(drums),
        "-filter_complex",
        (
            f"[0:a]aresample={DEFAULT_RENDER_RATE},volume={vocal_gain:.6f}dB[v];"
            f"[1:a]atrim=start=0:end={output_duration:.9f},asetpts=PTS-STARTPTS,"
            f"aresample={DEFAULT_RENDER_RATE},volume={drum_gain:.6f}dB[d];"
            f"[v][d]amix=inputs=2:normalize=0:dropout_transition=0,"
            f"atrim=start=0:end={output_duration:.9f},asetpts=N/SR/TB[m]"
        ),
        "-map",
        "[m]",
        "-ar",
        str(DEFAULT_RENDER_RATE),
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(premix),
    ]
    run_command(mix_command, timeout=180)
    premix_measurement = loudness(premix, ffmpeg)
    current_lufs = premix_measurement["integrated_lufs"]
    current_tp = premix_measurement["true_peak_dbtp"]
    current_lra = premix_measurement["lra_lu"]
    current_thresh = premix_measurement["threshold_lufs"]
    target_offset = premix_measurement["target_offset_db"]
    if any(value is None for value in (current_lufs, current_tp, current_lra, current_thresh, target_offset)):
        raise ValueError("premix loudness could not be measured")
    loudnorm_filter = (
        f"loudnorm=I={final_target_lufs:.3f}:TP={final_target_tp:.3f}:LRA=11:"
        f"measured_I={current_lufs:.3f}:measured_TP={current_tp:.3f}:"
        f"measured_LRA={current_lra:.3f}:measured_thresh={current_thresh:.3f}:"
        f"offset={target_offset:.3f}:linear=true:print_format=summary"
    )
    loudnorm_output = output.with_name(f".{output.stem}.loudnorm.wav")
    final_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(premix),
        "-af",
        loudnorm_filter,
        "-ar",
        str(DEFAULT_RENDER_RATE),
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(loudnorm_output),
    ]
    correction_command: list[str] | None = None
    try:
        run_command(final_command, timeout=180)
        first_final_measurement = loudness(loudnorm_output, ffmpeg)
        measured_i = first_final_measurement["integrated_lufs"]
        if measured_i is None:
            raise ValueError("loudnorm output is silent")
        correction_gain = final_target_lufs - float(measured_i)
        linear_limit = 10.0 ** (final_target_tp / 20.0)
        correction_filter = (
            f"volume={correction_gain:.6f}dB,"
            f"alimiter=limit={linear_limit:.9f}:attack=5:release=50:level=false"
        )
        correction_command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-i",
            str(loudnorm_output),
            "-af",
            correction_filter,
            "-ar",
            str(DEFAULT_RENDER_RATE),
            "-ac",
            "2",
            "-c:a",
            "pcm_f32le",
            str(output),
        ]
        run_command(correction_command, timeout=180)
    finally:
        if premix.exists():
            premix.unlink()
        if loudnorm_output.exists():
            loudnorm_output.unlink()
    final_measurement = loudness(output, ffmpeg)
    return {
        "vocal_component_measurement": vocal_measurement,
        "drum_component_measurement": drum_measurement,
        "vocal_gain_db": vocal_gain,
        "drum_gain_db": drum_gain,
        "premix_measurement": premix_measurement,
        "normalization_mode": "ebu_r128_two_pass_loudnorm",
        "loudnorm_filter": loudnorm_filter,
        "final_measurement": final_measurement,
        "mix_argv": mix_command,
        "final_argv": final_command,
        "correction_argv": correction_command,
        "output_sha256": sha256_file(output),
    }


def first_audible_seconds(path: Path, ffmpeg: str, threshold_db: float = -60.0) -> float | None:
    process = run_command(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"],
        timeout=180,
    )
    audio = np.frombuffer(process.stdout, dtype="<f4")
    threshold = 10.0 ** (threshold_db / 20.0)
    indices = np.flatnonzero(np.abs(audio) >= threshold)
    return float(indices[0] / 48000.0) if indices.size else None


def signal_receipt(path: Path, ffmpeg: str, ffprobe: str, expected_duration: float) -> dict[str, Any]:
    probe = probe_audio(path, ffprobe)
    measurement = loudness(path, ffmpeg)
    return {
        "sha256": probe["sha256"],
        "bytes": probe["bytes"],
        "duration_seconds": probe["duration_seconds"],
        "sample_rate": probe["sample_rate"],
        "channels": probe["channels"],
        "first_audible_seconds_below_minus_60_db": first_audible_seconds(path, ffmpeg),
        **measurement,
        "hard_gates": {
            "duration_within_50ms": abs(float(probe["duration_seconds"]) - expected_duration) <= 0.05,
            "true_peak_at_or_below_minus_0_8_dbtp": measurement["true_peak_dbtp"] is not None and float(measurement["true_peak_dbtp"]) <= -0.8,
            "integrated_loudness_between_minus_15_5_and_minus_13": measurement["integrated_lufs"] is not None and -15.5 <= float(measurement["integrated_lufs"]) <= -13.0,
            "not_silent": measurement["integrated_lufs"] is not None,
        },
    }


def _public_option(path: Path, ffprobe: str) -> dict[str, Any]:
    probe = probe_audio(path, ffprobe)
    return {
        "sha256": probe["sha256"],
        "bytes": probe["bytes"],
        "duration_seconds": probe["duration_seconds"],
        "media_kind": "audio/wav",
    }


def prepare_blind_assignment(
    *,
    candidates: Mapping[str, Path],
    public_dir: Path,
    private_dir: Path,
    ffprobe: str,
    context: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    public_dir.mkdir(parents=True, exist_ok=False)
    private_dir.mkdir(parents=True, exist_ok=False)
    candidate_ids = sorted(candidates)
    labels = [chr(ord("A") + index) for index in range(len(candidate_ids))]
    shuffled = candidate_ids[:]
    secrets.SystemRandom().shuffle(shuffled)
    option_map = dict(zip(labels, shuffled))
    options: dict[str, Any] = {}
    for label, candidate_id in option_map.items():
        source = candidates[candidate_id]
        destination = public_dir / f"{label}.wav"
        shutil.copyfile(source, destination)
        options[label] = _public_option(destination, ffprobe)
    authority_seed = {
        "schema_version": SCHEMA_VERSION,
        "kind": "earcrate_beggin_timing_private_assignment",
        "created_at": now_utc(),
        "nonce": secrets.token_hex(32),
        "option_map": option_map,
        "candidate_sources": {
            candidate_id: {
                "sha256": sha256_file(path),
                "bytes": int(path.stat().st_size),
                "path": str(path),
            }
            for candidate_id, path in candidates.items()
        },
        "context": deepcopy(dict(context)),
    }
    authority = seal(authority_seed, "authority_sha256")
    public_seed = {
        "schema_version": SCHEMA_VERSION,
        "kind": "earcrate_beggin_timing_public_assignment",
        "created_at": now_utc(),
        "options": options,
        "dimensions": REVIEW_DIMENSIONS,
        "choices": [*labels, "tie", "reject_all", "abstain"],
        "private_authority_sha256": authority["authority_sha256"],
        "instructions": "Listen level matched. Select the option whose terminal calls land and whose percussion adds force without reducing Frankie to an effect.",
        "context": {
            "case_id": context.get("case_id"),
            "campaign_sha256": context.get("campaign_sha256"),
            "source_identities": context.get("source_identities"),
        },
    }
    public = seal(public_seed, "assignment_sha256")
    write_json(public_dir / "assignment.json", public)
    write_json(private_dir / "assignment-authority.json", authority)
    write_text(
        public_dir / "REVIEW.txt",
        "Choose A, B, C, tie, reject_all, or abstain.\n"
        "Score terminal phrase placement, vocal authority, percussion impact, groove continuity, residue, room continuity, recognizability, and desire to hear the next phrase.\n",
    )
    return public, authority


def verify_output(root: str | Path) -> dict[str, Any]:
    directory = Path(root).expanduser().absolute()
    sums = directory / "SHA256SUMS.txt"
    if not sums.is_file():
        raise ValueError("output has no SHA256SUMS.txt")
    failures: list[str] = []
    checked = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        path = directory / relative
        if path.is_symlink() or not path.is_file():
            failures.append(f"missing_or_unsafe:{relative}")
        elif sha256_file(path) != digest:
            failures.append(f"hash_mismatch:{relative}")
        checked += 1
    receipt = load_json(directory / "campaign-receipt.json")
    validate_seal(receipt, "receipt_sha256")
    return {
        "ok": not failures,
        "root": str(directory),
        "checked": checked,
        "failures": failures,
        "receipt_sha256": receipt["receipt_sha256"],
    }


def write_checksums(root: Path) -> Path:
    rows: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            rows.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    return write_text(root / "SHA256SUMS.txt", "\n".join(rows) + "\n")


def _binding_identity(binding: Mapping[str, Any] | None) -> str | None:
    if not binding:
        return None
    for field in ("binding_sha256", "receipt_sha256"):
        if is_sha256(binding.get(field)):
            return str(binding[field])
    return None


def run_campaign(args: argparse.Namespace) -> dict[str, Any]:
    output = require_new_directory(args.output)
    analysis_dir = output / "analysis"
    maps_dir = output / "time-maps"
    vocals_dir = output / "aligned-vocals"
    candidates_dir = output / "candidates"
    receipts_dir = output / "receipts"
    for directory in (analysis_dir, maps_dir, vocals_dir, candidates_dir, receipts_dir):
        directory.mkdir()

    source_binding: dict[str, Any] | None = None
    target_binding: dict[str, Any] | None = None
    if args.source_vocal_binding:
        source_path, source_binding = source_from_binding(args.source_vocal_binding)
    elif args.source_vocal:
        source_path = Path(args.source_vocal).expanduser().absolute()
        _verify_bound_path(source_path)
    else:
        raise ValueError("--source-vocal-binding or --source-vocal is required")
    if args.target_instrumental_binding:
        target_instrumental, target_binding = source_from_binding(args.target_instrumental_binding)
    elif args.target_instrumental:
        target_instrumental = Path(args.target_instrumental).expanduser().absolute()
        _verify_bound_path(target_instrumental)
    else:
        target_instrumental = None

    if args.target_vocal_witness:
        witness_path = Path(args.target_vocal_witness).expanduser().absolute()
        witness_identity = _verify_bound_path(witness_path, args.target_vocal_sha256 if args.verify_target_vocal_sha else None)
        witness_location = {
            "schema": "earcrate.timing_witness_location.v1",
            "selected": witness_identity,
            "selected_by": "explicit command-line path",
            "sha256": witness_identity["sha256"],
        }
    elif args.estate_inventory:
        witness_location = locate_witness(args.estate_inventory, args.target_vocal_sha256)
        witness_path = Path(witness_location["selected"]["path"])
    else:
        raise ValueError("phrase-local repair requires --target-vocal-witness or --estate-inventory")

    drums = Path(args.drum_stem).expanduser().absolute()
    _verify_bound_path(drums)
    baseline = Path(args.baseline_smoke).expanduser().absolute() if args.baseline_smoke else None
    if baseline:
        _verify_bound_path(baseline)

    source_probe = probe_audio(source_path, args.ffprobe)
    witness_probe = probe_audio(witness_path, args.ffprobe)
    drum_probe = probe_audio(drums, args.ffprobe)
    target_probe = probe_audio(target_instrumental, args.ffprobe) if target_instrumental else None
    if source_probe["duration_seconds"] < args.source_end:
        raise ValueError("source vocal is shorter than the selected source window")
    if witness_probe["duration_seconds"] < args.target_end:
        raise ValueError("target vocal witness is shorter than the selected target window")
    if drum_probe["duration_seconds"] + 0.05 < args.output_duration:
        raise ValueError("drum stem is shorter than the requested audition duration")

    source_audio = decode_mono(
        source_path,
        start=args.source_start,
        end=args.source_end,
        sample_rate=args.analysis_rate,
        ffmpeg=args.ffmpeg,
    )
    target_audio = decode_mono(
        witness_path,
        start=args.target_start,
        end=args.target_end,
        sample_rate=args.analysis_rate,
        ffmpeg=args.ffmpeg,
    )
    source_bundle = audio_features(source_audio, args.analysis_rate)
    target_bundle = audio_features(target_audio, args.analysis_rate)
    activity_gap_candidates = []
    for value in (args.activity_merge_gap, args.activity_merge_gap * 0.65, args.activity_merge_gap * 0.4, 0.03):
        normalized = max(0.02, float(value))
        if all(abs(normalized - existing) > 1e-6 for existing in activity_gap_candidates):
            activity_gap_candidates.append(normalized)
    source_activity: list[dict[str, float]] = []
    target_activity: list[dict[str, float]] = []
    selected_activity_gap = activity_gap_candidates[-1]
    for activity_gap in activity_gap_candidates:
        source_candidate = activity_segments(source_bundle, merge_gap_seconds=activity_gap)
        target_candidate = activity_segments(target_bundle, merge_gap_seconds=activity_gap)
        source_activity, target_activity = source_candidate, target_candidate
        selected_activity_gap = activity_gap
        if len(source_activity) >= args.final_call_count and len(target_activity) >= args.final_call_count:
            break
    if len(source_activity) < args.final_call_count or len(target_activity) < args.final_call_count:
        raise ValueError(
            f"not enough vocal activity groups for {args.final_call_count} terminal calls after adaptive segmentation: "
            f"source={len(source_activity)}, target={len(target_activity)}, gaps={activity_gap_candidates}"
        )
    mapping = constrained_dtw(source_bundle, target_bundle, band_seconds=args.dtw_band_seconds)
    time_maps = create_time_maps(
        source_bundle=source_bundle,
        target_bundle=target_bundle,
        mapping=mapping,
        source_segments=source_activity,
        target_segments=target_activity,
        source_window_start=args.source_start,
        source_window_end=args.source_end,
        target_window_start=args.target_start,
        target_window_end=args.target_end,
        final_call_count=args.final_call_count,
    )

    mapping_public = {
        "schema": "earcrate.beggin_phrase_alignment_analysis.v1",
        "created_at": now_utc(),
        "source_window": {"start": args.source_start, "end": args.source_end},
        "target_window": {"start": args.target_start, "end": args.target_end},
        "source_activity_segments": source_activity,
        "target_activity_segments": target_activity,
        "activity_merge_gap_seconds": selected_activity_gap,
        "activity_merge_gap_candidates_seconds": activity_gap_candidates,
        "terminal_call_count": args.final_call_count,
        "dtw": {
            "normalized_cost": mapping["normalized_cost"],
            "path_length": mapping["path_length"],
            "band_frames": mapping["band_frames"],
            "source_frames": mapping["source_frames"],
            "target_frames": mapping["target_frames"],
            "global_source_to_target_ratio": (args.source_end - args.source_start) / (args.target_end - args.target_start),
        },
        "authority": {
            "analysis_is_observation": True,
            "time_map_is_candidate_not_accepted_state": True,
            "target_witness_is_timing_only": True,
        },
    }
    mapping_public = seal(mapping_public, "analysis_sha256")
    write_json(analysis_dir / "phrase-alignment-analysis.json", mapping_public)
    write_json(analysis_dir / "target-witness-location.private.json", witness_location)

    candidate_paths: dict[str, Path] = {}
    render_commands: dict[str, Any] = {}
    mix_receipts: dict[str, Any] = {}
    signal_receipts: dict[str, Any] = {}
    for candidate_id in ("onset_only", "global_block", "phrase_local"):
        segments = time_maps[candidate_id]
        map_value = seal(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "earcrate_beggin_candidate_time_map",
                "candidate_id": candidate_id,
                "created_at": now_utc(),
                "source_sha256": source_probe["sha256"],
                "target_timing_witness_sha256": witness_probe["sha256"],
                "segments": segments,
                "terminal_call_segments": time_maps["terminal_calls"] if candidate_id == "phrase_local" else [],
                "boundary": {
                    "source_pitch_preserved": True,
                    "percussion_only_target_allows_native_source_key": True,
                    "global_delay_is_not_phrase_alignment": True,
                    "terminal_calls_independently_placed": candidate_id == "phrase_local",
                },
            },
            "time_map_sha256",
        )
        write_json(maps_dir / f"{candidate_id}.time-map.json", map_value)
        vocal_path = vocals_dir / f"{candidate_id}.vocal.wav"
        render_commands[candidate_id] = render_vocal_map(
            source=source_path,
            segments=segments,
            output=vocal_path,
            output_duration=args.output_duration,
            ffmpeg=args.ffmpeg,
        )
        candidate_path = candidates_dir / f"{candidate_id}.wav"
        mix_receipts[candidate_id] = mix_and_match_loudness(
            vocal=vocal_path,
            drums=drums,
            output=candidate_path,
            output_duration=args.output_duration,
            ffmpeg=args.ffmpeg,
            vocal_target_lufs=args.vocal_component_lufs,
            drum_target_lufs=args.drum_component_lufs,
            final_target_lufs=args.target_lufs,
            final_target_tp=args.target_tp,
        )
        candidate_paths[candidate_id] = candidate_path
        signal_receipts[candidate_id] = signal_receipt(candidate_path, args.ffmpeg, args.ffprobe, args.output_duration)

    if baseline:
        baseline_copy = candidates_dir / "unaligned_smoke_reference.wav"
        shutil.copyfile(baseline, baseline_copy)

    context = {
        "case_id": "beggin-four-seasons-x-maneskin-handoff",
        "campaign_sha256": args.campaign_sha256,
        "source_identities": {
            "four_seasons_vocal": source_probe["sha256"],
            "maneskin_timing_witness": witness_probe["sha256"],
            "maneskin_drums": drum_probe["sha256"],
            "maneskin_instrumental": target_probe["sha256"] if target_probe else None,
        },
    }
    public_assignment, private_authority = prepare_blind_assignment(
        candidates=candidate_paths,
        public_dir=output / "review-public",
        private_dir=output / "review-private",
        ffprobe=args.ffprobe,
        context=context,
    )

    measured_loudness = [float(row["integrated_lufs"]) for row in signal_receipts.values() if row.get("integrated_lufs") is not None]
    cross_candidate_loudness_range = (max(measured_loudness) - min(measured_loudness)) if measured_loudness else float("inf")
    all_signal_pass = (
        all(all((row.get("hard_gates") or {}).values()) for row in signal_receipts.values())
        and cross_candidate_loudness_range <= 0.75
    )
    receipt = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_beggin_timing_campaign_receipt",
            "created_at": now_utc(),
            "case_id": "beggin-four-seasons-x-maneskin-handoff",
            "campaign_sha256": args.campaign_sha256,
            "suite_sha256": args.suite_sha256,
            "source_bindings": {
                "four_seasons": _binding_identity(source_binding),
                "maneskin_instrumental": _binding_identity(target_binding),
            },
            "source_identities": {
                "four_seasons_vocal": source_probe,
                "maneskin_timing_witness": witness_probe,
                "maneskin_drums": drum_probe,
                "maneskin_instrumental": target_probe,
                "baseline_smoke": probe_audio(baseline, args.ffprobe) if baseline else None,
            },
            "tools": {
                "ffmpeg": executable_identity(args.ffmpeg),
                "ffprobe": executable_identity(args.ffprobe),
                "python": sys.version,
                "numpy": np.__version__,
            },
            "analysis_sha256": mapping_public["analysis_sha256"],
            "time_map_sha256s": {
                candidate_id: load_json(maps_dir / f"{candidate_id}.time-map.json")["time_map_sha256"]
                for candidate_id in candidate_paths
            },
            "candidate_sha256s": {candidate_id: sha256_file(path) for candidate_id, path in candidate_paths.items()},
            "render_commands": render_commands,
            "mix_receipts": mix_receipts,
            "signal_receipts": signal_receipts,
            "cross_candidate_loudness_range_lu": cross_candidate_loudness_range,
            "review_assignment_sha256": public_assignment["assignment_sha256"],
            "private_assignment_sha256": private_authority["authority_sha256"],
            "status": "signal_sane_human_review_pending" if all_signal_pass else "signal_gate_failed",
            "control_question": (
                "Does the Måneskin percussion make Frankie physically larger while every terminal call lands naturally, "
                "rather than merely becoming louder, busier, or mathematically synchronized?"
            ),
            "authority": {
                "musical_acceptance": False,
                "provider_adoption": False,
                "release_decision": False,
                "source_media_embedded": False,
                "human_review_required": True,
            },
        },
        "receipt_sha256",
    )
    write_json(output / "campaign-receipt.json", receipt)
    write_json(receipts_dir / "signal-evaluation.json", seal({
        "schema_version": SCHEMA_VERSION,
        "kind": "earcrate_beggin_signal_evaluation",
        "created_at": now_utc(),
        "candidate_receipts": signal_receipts,
        "all_hard_gates_pass": all_signal_pass,
        "evaluator": "beggin_timing_pass.signal_v1",
        "evaluator_is_builder": True,
        "boundary": {
            "diagnostic_only": True,
            "independent_signal_evaluation_still_required_for_release": True,
            "musical_acceptance_not_claimed": True,
        },
    }, "evaluation_sha256"))
    write_text(
        output / "NEXT.txt",
        "1. Listen only through review-public/A.wav, B.wav, and C.wav at a fixed playback level.\n"
        "2. Submit one choice with submit-review. Reject all if none makes the terminal calls land.\n"
        "3. Only after a phrase-local winner exists should the 3090 separation tournament begin.\n",
    )
    write_checksums(output)
    verification = verify_output(output)
    return {
        "ok": verification["ok"],
        "output": str(output),
        "receipt_sha256": receipt["receipt_sha256"],
        "candidate_sha256s": receipt["candidate_sha256s"],
        "assignment_sha256": public_assignment["assignment_sha256"],
        "status": receipt["status"],
        "verification": verification,
    }


def submit_review(args: argparse.Namespace) -> dict[str, Any]:
    public = load_json(args.assignment)
    authority = load_json(args.private_authority)
    public_sha = validate_seal(public, "assignment_sha256")
    authority_sha = validate_seal(authority, "authority_sha256")
    if public.get("private_authority_sha256") != authority_sha:
        raise ValueError("public assignment and private authority do not match")
    choice = args.choice
    options = dict(public.get("options") or {})
    option_map = dict(authority.get("option_map") or {})
    if choice not in {*options.keys(), "tie", "reject_all", "abstain"}:
        raise ValueError(f"invalid review choice: {choice}")
    dimensions: dict[str, Any] = {}
    if args.dimensions_json:
        value = json.loads(args.dimensions_json)
        if not isinstance(value, dict):
            raise ValueError("--dimensions-json must decode to an object")
        dimensions.update(value)
    selected_candidate = option_map.get(choice)
    receipt = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_beggin_timing_human_review",
            "reviewed_at": now_utc(),
            "assignment_sha256": public_sha,
            "private_authority_sha256": authority_sha,
            "reviewer_id": args.reviewer_id,
            "choice": choice,
            "selected_candidate_id": selected_candidate,
            "verdict": "reject" if choice == "reject_all" else ("abstain" if choice == "abstain" else ("tie" if choice == "tie" else "prefer")),
            "dimensions": dimensions,
            "notes": list(args.note or []),
            "control_question_answered": bool(choice in options),
            "authority": {
                "human_review": True,
                "provider_adoption": False,
                "release_decision": False,
            },
        },
        "review_sha256",
    )
    write_json(args.output, receipt, exclusive=not args.replace)
    return {
        "ok": True,
        "output": str(Path(args.output).expanduser().absolute()),
        "review_sha256": receipt["review_sha256"],
        "choice": choice,
        "selected_candidate_id": selected_candidate,
        "verdict": receipt["verdict"],
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    locate = sub.add_parser("locate-witness", help="find exact current timing-witness bytes in an authoritative estate inventory")
    locate.add_argument("--inventory", required=True)
    locate.add_argument("--sha256", default=DEFAULT_TARGET_WITNESS_SHA256)
    locate.add_argument("--output")

    run = sub.add_parser("run", help="analyze, render, level-match, and blind the three timing candidates")
    run.add_argument("--source-vocal-binding")
    run.add_argument("--source-vocal")
    run.add_argument("--target-instrumental-binding")
    run.add_argument("--target-instrumental")
    run.add_argument("--target-vocal-witness")
    run.add_argument("--estate-inventory")
    run.add_argument("--target-vocal-sha256", default=DEFAULT_TARGET_WITNESS_SHA256)
    run.add_argument("--verify-target-vocal-sha", action="store_true")
    run.add_argument("--drum-stem", required=True)
    run.add_argument("--baseline-smoke")
    run.add_argument("--output", required=True)
    run.add_argument("--campaign-sha256")
    run.add_argument("--suite-sha256")
    run.add_argument("--source-start", type=float, default=DEFAULT_SOURCE_START)
    run.add_argument("--source-end", type=float, default=DEFAULT_SOURCE_END)
    run.add_argument("--target-start", type=float, default=DEFAULT_TARGET_START)
    run.add_argument("--target-end", type=float, default=DEFAULT_TARGET_END)
    run.add_argument("--output-duration", type=float, default=DEFAULT_OUTPUT_DURATION)
    run.add_argument("--analysis-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    run.add_argument("--dtw-band-seconds", type=float, default=2.0)
    run.add_argument("--activity-merge-gap", type=float, default=0.14)
    run.add_argument("--final-call-count", type=int, default=3)
    run.add_argument("--target-lufs", type=float, default=DEFAULT_TARGET_LUFS)
    run.add_argument("--target-tp", type=float, default=DEFAULT_TARGET_TP)
    run.add_argument("--vocal-component-lufs", type=float, default=DEFAULT_VOCAL_COMPONENT_LUFS)
    run.add_argument("--drum-component-lufs", type=float, default=DEFAULT_DRUM_COMPONENT_LUFS)
    run.add_argument("--ffmpeg", default="ffmpeg")
    run.add_argument("--ffprobe", default="ffprobe")

    review = sub.add_parser("submit-review", help="seal the owner's choice without exposing the mapping in the public review directory")
    review.add_argument("--assignment", required=True)
    review.add_argument("--private-authority", required=True)
    review.add_argument("--reviewer-id", default="operator:owner")
    review.add_argument("--choice", required=True)
    review.add_argument("--dimensions-json")
    review.add_argument("--note", action="append", default=[])
    review.add_argument("--output", required=True)
    review.add_argument("--replace", action="store_true")

    verify = sub.add_parser("verify", help="verify every content identity in one timing-campaign output")
    verify.add_argument("output")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "locate-witness":
            result = locate_witness(args.inventory, args.sha256)
            if args.output:
                write_json(args.output, result)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            if args.campaign_sha256 and not is_sha256(args.campaign_sha256):
                raise ValueError("--campaign-sha256 must be a SHA-256 identity")
            if args.suite_sha256 and not is_sha256(args.suite_sha256):
                raise ValueError("--suite-sha256 must be a SHA-256 identity")
            result = run_campaign(args)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "submit-review":
            result = submit_review(args)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            result = verify_output(args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
