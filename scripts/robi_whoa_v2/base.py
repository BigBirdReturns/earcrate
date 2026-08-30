"""Produce the Robi WHOA 30-second loop by qualifying the bed before adding Robi.

The predecessor campaign asked ACE-Step to complete the supplied vocal and then gated the
combined result. Six candidates preserved Robi but failed the same coverage, high-band, and
dynamic-motion checks. That mechanism is closed by an exact refusal receipt. This campaign
changes the object under test, not the thresholds:

1. Generate or retrieve standalone accompaniment beds through two existing estate mechanisms:
   EarCrate's approved-atom graph renderer and ACE-Step's instrumental BGM role.
2. Apply the unchanged signal gates to each standalone bed. A failed bed never receives Robi.
3. Select one qualified bed machine-side, lay source-locked Robi slices over it with no time or
   pitch transform, and gate masking, lineage, headroom, and the circular boundary.
4. Publish one complete loop ZIP or one refusal receipt. There is no synthesized fallback and no
   owner audition pack.

Run through scripts/RUN_ROBI_WHOA_BED_FIRST_V2.cmd on the configured local estate.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
import zipfile

import numpy as np


class CampaignError(RuntimeError):
    """A fail-closed campaign error."""


@dataclasses.dataclass(frozen=True)
class BedCandidate:
    candidate_id: str
    mechanism: str
    seed: int
    profile: str | None
    directory: str
    raw_audio: str | None
    bed_audio: str | None
    qualified: bool
    score: float | None
    metrics: Mapping[str, Any]
    failures: Sequence[str]
    error: str | None = None
    estate_rollback: Mapping[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


DEFAULT_BASE_URLS = (
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:7860",
    "http://127.0.0.1:7861",
)


# ---------------------------------------------------------------------------
# Custody and filesystem helpers
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path: Path, value: Any, *, exclusive: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(value)
    mode = "xb" if exclusive else "wb"
    with path.open(mode) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    timeout: float = 3600.0,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = [str(value) for value in args]
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")[-4000:]
        raise CampaignError(f"command failed ({completed.returncode}): {' '.join(command)}\n{stderr}")
    return completed


def require_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise CampaignError(f"required executable is not on PATH: {name}")
    return found


def ffprobe(path: Path) -> dict[str, Any]:
    executable = require_tool("ffprobe")
    completed = run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,sample_rate,channels,channel_layout",
            "-of",
            "json",
            path,
        ]
    )
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignError(f"ffprobe returned invalid JSON for {path}") from exc


def decode_audio(path: Path, *, sample_rate: int, channels: int = 2) -> np.ndarray:
    executable = require_tool("ffmpeg")
    completed = run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-i",
            path,
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ar",
            sample_rate,
            "-ac",
            channels,
            "pipe:1",
        ],
        timeout=3600.0,
    )
    values = np.frombuffer(completed.stdout, dtype="<f4")
    if not len(values) or len(values) % channels:
        raise CampaignError(f"decoded audio has an invalid channel layout: {path}")
    return values.reshape(-1, channels).astype(np.float64, copy=False)


def decoded_pcm_sha256(path: Path, *, sample_rate: int, channels: int = 1) -> str:
    executable = require_tool("ffmpeg")
    process = subprocess.Popen(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    digest = hashlib.sha256()
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    stderr = process.stderr.read() if process.stderr else b""
    returncode = process.wait(timeout=3600)
    if returncode != 0:
        raise CampaignError(f"ffmpeg PCM identity failed: {stderr.decode(errors='replace')[-2000:]}")
    return digest.hexdigest()


def write_pcm24_wav(path: Path, audio: np.ndarray, *, sample_rate: int) -> None:
    if audio.ndim != 2 or audio.shape[1] != 2:
        raise CampaignError("write_pcm24_wav expects stereo audio")
    executable = require_tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False, dir=path.parent) as handle:
        raw_path = Path(handle.name)
        handle.write(np.asarray(audio, dtype="<f4").tobytes())
    try:
        run(
            [
                executable,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "f32le",
                "-ar",
                sample_rate,
                "-ac",
                2,
                "-i",
                raw_path,
                "-c:a",
                "pcm_s24le",
                "-map_metadata",
                "-1",
                "-fflags",
                "+bitexact",
                "-flags",
                "+bitexact",
                path,
            ]
        )
    finally:
        raw_path.unlink(missing_ok=True)


def make_mp3(source: Path, destination: Path) -> None:
    executable = require_tool("ffmpeg")
    run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            source,
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-map_metadata",
            "-1",
            destination,
        ]
    )


def write_repeated_wav(path: Path, audio: np.ndarray, *, sample_rate: int, repeats: int) -> None:
    if repeats <= 0:
        raise CampaignError("repeat count must be positive")
    write_pcm24_wav(path, np.tile(audio, (repeats, 1)), sample_rate=sample_rate)


def copy_with_hash(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"bytes": destination.stat().st_size, "sha256": sha256_file(destination)}


# ---------------------------------------------------------------------------
# Commission contract
# ---------------------------------------------------------------------------
