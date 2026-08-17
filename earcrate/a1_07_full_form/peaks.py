"""Separate the three peak conditions a delivery decision actually depends on.

A true-peak reading above 0 dBTP does not prove the stored samples are clipped:
it may be an intersample overshoot that only a reconstruction filter ever sees.
The distinction decides the remedy. With no flat-topped samples, a deterministic
attenuation stage yields a safe master without touching the arrangement. With
hard-clipped samples already in the PCM, attenuation cannot restore what was
destroyed, and the frontier needs a bounded reconstruction or a documented
source-defect waiver.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..a1_07_gold_v8 import common as c


def _full_scale(bits: int) -> int:
    """Positive full scale on the s32 grid for audio stored at `bits`."""
    return (2 ** (bits - 1) - 1) * (2 ** (32 - bits))


def source_bit_depth(path: Path, ffprobe: str = "ffprobe") -> int:
    result = c.run([
        ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=bits_per_raw_sample,bits_per_sample,sample_fmt", "-of", "json", str(path)
    ], timeout=120)
    if result.returncode != 0:
        raise c.DescentError(f"ffprobe failed for {path}: {result.stderr[-500:]}")
    import json
    stream = (json.loads(result.stdout).get("streams") or [{}])[0]
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = int(stream.get(key) or 0)
        if value:
            return value
    return 24


def true_peak_dbtp(path: Path, ffmpeg: str = "ffmpeg") -> float | None:
    """Oversampled true peak, from the ebur128 summary block."""
    result = c.run([
        ffmpeg, "-nostdin", "-hide_banner", "-i", str(path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-",
    ], timeout=1800)
    tail = result.stderr.rsplit("Summary:", 1)[-1]
    match = re.search(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", tail, flags=re.S)
    return float(match.group(1)) if match else None


def peak_conditions(
    path: Path,
    *,
    sample_rate: int,
    channels: int,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    run_length: int = 2,
) -> dict[str, Any]:
    """Sample peak, oversampled true peak, and the flat-top census, as three facts."""
    import math

    bits = source_bit_depth(path, ffprobe)
    ceiling = _full_scale(bits)
    step = 2 ** (32 - bits)
    pcm = c.decode_s32(path, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
    samples = c.bytes_to_samples(pcm)
    frames = len(samples) // channels

    peak = 0
    at_full_scale = 0
    runs: list[int] = []
    for channel in range(channels):
        run = 0
        previous: int | None = None
        for index in range(channel, len(samples), channels):
            value = int(samples[index])
            magnitude = -value if value < 0 else value
            if magnitude > peak:
                peak = magnitude
            # Within one quantisation step of full scale counts as pinned: a
            # rounded s24 sample can land one LSB below the exact ceiling.
            if magnitude >= ceiling - step:
                at_full_scale += 1
                run = run + 1 if previous is not None and value == previous else 1
                if run >= run_length:
                    if run == run_length:
                        runs.append(run)
                    else:
                        runs[-1] = run
                previous = value
            else:
                run = 0
                previous = None

    sample_peak_dbfs = (20.0 * math.log10(peak / ceiling)) if peak else -float("inf")
    tp = true_peak_dbtp(path, ffmpeg)
    flat_top_samples = sum(runs)
    return {
        "source_bit_depth": bits,
        "sample_peak_dbfs": round(sample_peak_dbfs, 4) if peak else None,
        "sample_peak_at_or_above_full_scale": bool(peak >= ceiling - step),
        "oversampled_true_peak_dbtp": tp,
        "true_peak_over_zero": bool(tp is not None and tp > 0.0),
        "full_scale_sample_count": at_full_scale,
        "flat_top_run_count": len(runs),
        "flat_top_sample_count": flat_top_samples,
        "longest_flat_top_run_samples": max(runs) if runs else 0,
        "longest_flat_top_run_ms": round(1000.0 * max(runs) / sample_rate, 4) if runs else 0.0,
        "flat_top_total_duration_ms": round(1000.0 * flat_top_samples / sample_rate, 4),
        "hard_clipped": bool(runs),
        "diagnosis": (
            "hard-clipped: consecutive samples pinned at full scale; attenuation cannot "
            "restore them, so a bounded reconstruction or a documented source-defect "
            "waiver is required before mastering"
            if runs else
            "intersample overshoot only: no flat-topped run, so a deterministic "
            "attenuation stage yields a safe delivery master without changing the "
            "arrangement"
            if (tp is not None and tp > 0.0) else
            "no peak condition"
        ),
        "frames_examined": frames,
    }
