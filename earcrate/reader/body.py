from __future__ import annotations

"""Exact waveform-indexed body map shared by every reader arm."""

import hashlib
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from earcrate.reader.model import ReaderError, reader_sha256_file, reader_sha256_json


def reader_decode_stereo(
    path: str | Path,
    *,
    sample_rate: int = 22_050,
    start_seconds: float = 0.0,
    duration_seconds: float = 30.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if sample_rate <= 0 or start_seconds < 0 or duration_seconds <= 0:
        raise ReaderError("reader decode configuration must be positive")
    args = ["ffmpeg", "-nostdin", "-v", "error"]
    if start_seconds:
        args += ["-ss", f"{float(start_seconds):.9f}"]
    args += ["-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn"]
    args += ["-t", f"{float(duration_seconds):.9f}", "-f", "f32le", "-ac", "2", "-ar", str(int(sample_rate)), "pipe:1"]
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=max(60, int(duration_seconds * 4.0)),
    )
    if completed.returncode != 0:
        raise ReaderError(completed.stderr.decode("utf-8", "replace")[:1000])
    raw = completed.stdout
    usable = len(raw) - (len(raw) % 8)
    if usable <= 0:
        raise ReaderError("ffmpeg decoded zero stereo frames")
    pcm = np.frombuffer(raw[:usable], dtype="<f4").reshape(-1, 2).astype(np.float32, copy=True)
    pcm = np.nan_to_num(pcm, nan=0.0, posinf=0.0, neginf=0.0)
    raw_pcm_sha256 = hashlib.sha256(raw[:usable]).hexdigest()
    body = {
        "kind": "earcrate_pcm_body",
        "source_path": str(source),
        "source_byte_sha256": reader_sha256_file(source),
        "decoded_pcm_sha256": raw_pcm_sha256,
        "sample_rate": int(sample_rate),
        "channels": 2,
        "frames": int(pcm.shape[0]),
        "duration_seconds": float(pcm.shape[0] / sample_rate),
        "window_start_seconds": float(start_seconds),
        "requested_duration_seconds": float(duration_seconds),
    }
    body["body_sha256"] = reader_sha256_json({key: value for key, value in body.items() if key != "source_path"})
    return pcm, body


__all__ = ["reader_decode_stereo"]
