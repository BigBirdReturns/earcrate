"""Play a bound performance through its rack, deterministically.

The only sound here comes from the samples the binding named. Nothing is synthesized,
nothing is substituted, and the mix is a pure function of the binding, so two
executions produce identical bytes.

Two tempo interpretations must be comparable, which means everything except the clock
has to be held constant: the same zones, the same gains, the same resampling, the same
summing order. A difference a listener hears should be the tempo, not the renderer.
"""

from __future__ import annotations

from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
import wave

import numpy as np

SAMPLE_RATE = 48000
CHANNELS = 2
BIT_DEPTH = 24
HEADROOM_DBFS = -3.0


class RackRenderError(RuntimeError):
    pass


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16- or 24-bit PCM wav into float32 channels."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())

    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        stream = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = stream[:, 0] | (stream[:, 1] << 8) | (stream[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        data = values.astype(np.float32) / 8388608.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise RackRenderError(f"unsupported sample width {width} in {path.name}")

    return data.reshape(-1, channels) if channels > 1 else data.reshape(-1, 1), rate


def _resample(block: np.ndarray, ratio: float) -> np.ndarray:
    """Linear resampling for pitch shift. Deterministic and adequate at ±1 semitone."""
    if ratio == 1.0:
        return block
    length = int(len(block) / ratio)
    if length <= 1:
        return block[:1]
    positions = np.arange(length, dtype=np.float64) * ratio
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, len(block) - 1)
    weight = (positions - left).astype(np.float32)[:, None]
    return block[left] * (1 - weight) + block[right] * weight


def render(binding: Mapping[str, Any], sample_root: Path, *, tempo_bpm: float,
           destination: Path, sample_rate: int = SAMPLE_RATE,
           stems: bool = True) -> dict[str, Any]:
    """Sum every bound event into a master, and the two hands into stems."""
    events: Sequence[Mapping[str, Any]] = binding["bindings"]
    if not events:
        raise RackRenderError("nothing bound to render")
    if binding["refused_event_count"]:
        raise RackRenderError(
            f"{binding['refused_event_count']} events were refused; a render may not "
            "quietly proceed without them")

    seconds_per_beat = 60.0 / float(tempo_bpm)
    cache: dict[str, tuple[np.ndarray, int]] = {}

    last = max(float(row["start_beat"]) + float(row["duration_beats"]) for row in events)
    total = int((last * seconds_per_beat + 8.0) * sample_rate)
    buses = {"1": np.zeros((total, CHANNELS), dtype=np.float32),
             "2": np.zeros((total, CHANNELS), dtype=np.float32)}

    for row in events:
        sample = str(row["sample"])
        if sample not in cache:
            path = Path(sample_root) / sample
            if not path.is_file():
                raise RackRenderError(f"bound sample is missing: {sample}")
            cache[sample] = _read_wav(path)
        block, rate = cache[sample]
        if rate != sample_rate:
            raise RackRenderError(
                f"{sample} is {rate} Hz; the rack and the render must agree, and "
                "resampling the instrument would change its identity")

        shifted = _resample(block, 2.0 ** (int(row["transposition_semitones"]) / 12.0))

        # Velocity: the zone's own tracking, so the instrument's dynamics are its own.
        track = float(row["velocity_track"])
        level = 1.0 - track + track * (int(row["velocity"]) / 127.0) ** 2
        release = float(row["release_seconds"])
        held = float(row["duration_beats"]) * seconds_per_beat + release
        length = min(len(shifted), int(held * sample_rate))
        voice = shifted[:length] * level

        # A short decay over the release window, so a note stops rather than truncating.
        tail = int(min(release, held) * sample_rate)
        if tail > 1 and tail <= len(voice):
            voice = voice.copy()
            voice[-tail:] *= np.linspace(1.0, 0.0, tail, dtype=np.float32)[:, None]

        start = int(float(row["start_beat"]) * seconds_per_beat * sample_rate)
        stop = min(total, start + len(voice))
        if stop > start:
            buses[str(row["staff"])][start:stop] += voice[:stop - start]

    master = buses["1"] + buses["2"]
    peak = float(np.abs(master).max()) or 1.0
    gain = (10.0 ** (HEADROOM_DBFS / 20.0)) / peak
    master = master * gain

    written = {"master": _write(destination, master, sample_rate)}
    if stems:
        for staff, name in (("1", "right-hand"), ("2", "left-hand")):
            path = destination.with_name(f"{destination.stem}-{name}.wav")
            written[name] = _write(path, buses[staff] * gain, sample_rate)

        # The stems must reconstruct the master, or the accounting is decorative.
        rebuilt = (buses["1"] + buses["2"]) * gain
        residual = float(np.abs(rebuilt - master).max())
        if residual > 1e-6:
            raise RackRenderError(f"stems do not sum to the master: residual {residual}")

    return {
        "files": written,
        "tempo_bpm": tempo_bpm,
        "events_rendered": len(events),
        "samples_used": len(cache),
        "duration_seconds": round(total / sample_rate, 4),
        "applied_gain_db": round(20.0 * float(np.log10(gain)), 4),
        "headroom_target_dbfs": HEADROOM_DBFS,
        "stem_sum_reproduces_master": stems,
        "deterministic": "no randomness, no dither, no clock input",
        "reference_recording_consulted": False,
    }


def _write(path: Path, audio: np.ndarray, sample_rate: int) -> str:
    from ...evidence.identity import sha256_file

    ceiling = 2 ** 23 - 1
    clipped = np.clip(audio, -1.0, 1.0)
    values = np.round(clipped * ceiling).astype(np.int32)
    packed = values.astype("<i4").tobytes()
    frames = bytearray()
    for index in range(0, len(packed), 4):
        frames += packed[index:index + 3]

    path.parent.mkdir(parents=True, exist_ok=True)
    byte_rate = sample_rate * CHANNELS * BIT_DEPTH // 8
    block = CHANNELS * BIT_DEPTH // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, sample_rate,
                                    byte_rate, block, BIT_DEPTH)
    header += b"data" + struct.pack("<I", len(frames))
    path.write_bytes(header + bytes(frames))
    return sha256_file(path)
