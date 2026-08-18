"""An engineering piano: plain on purpose, deterministic by requirement.

The point of this render is not that it sounds good. It is that it can be listened to,
and that listening to it exposes real problems -- whether the traversal is right,
whether the repeats and the D.S. return land where the score says, whether 130 bpm is
a plausible reading of this music -- while the approved rack is unbound.

Determinism is the part that is not provisional. The waveform is computed from the
note list with no randomness, no dither and no wall-clock input, so two executions
produce identical bytes. When a real rack arrives it replaces the voice, not the
accounting.

No audio is read here. Writing a WAV is output; the score branch's independence is
about never consulting the recording, and nothing in this file can.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any, Mapping, Sequence

SAMPLE_RATE = 48000
CHANNELS = 2
BIT_DEPTH = 24

# A struck-string caricature: a few decaying partials, a short attack, a long tail.
# Explicitly a caricature -- naming it that is more useful than pretending otherwise.
PARTIALS: tuple[tuple[float, float], ...] = ((1.0, 1.0), (2.0, 0.42), (3.0, 0.18),
                                             (4.0, 0.09), (5.0, 0.05))
ATTACK_SECONDS = 0.006
RELEASE_SECONDS = 0.35


class RenderError(RuntimeError):
    pass


def midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _voice(frequency: float, seconds: float, velocity: int, *,
           sample_rate: int = SAMPLE_RATE) -> list[float]:
    """One note, decaying. Pure function of its arguments, so the render repeats."""
    count = int(seconds * sample_rate)
    if count <= 0:
        return []
    amplitude = (velocity / 127.0) ** 1.4
    decay = max(0.35, min(3.0, 1.6 - frequency / 2000.0))
    attack = max(1, int(ATTACK_SECONDS * sample_rate))
    out = [0.0] * count
    for index in range(count):
        t = index / sample_rate
        envelope = (index / attack) if index < attack else math.exp(-t / decay)
        if envelope <= 1e-6:
            continue
        value = 0.0
        for ratio, weight in PARTIALS:
            value += weight * math.sin(2.0 * math.pi * frequency * ratio * t)
        out[index] = amplitude * envelope * value / len(PARTIALS)
    return out


def render_engineering_audio(realization: Mapping[str, Any], destination: Path, *,
                             sample_rate: int = SAMPLE_RATE) -> dict[str, Any]:
    """Sum the realized notes into a stereo WAV, with the hands panned apart.

    The hands are panned because that is the one production choice that helps a
    listener hear the thing this render exists to expose -- which voice is doing what
    across a traversal -- rather than a choice about how the music should sound.
    """
    notes: Sequence[Mapping[str, Any]] = realization.get("notes") or ()
    if not notes:
        raise RenderError("nothing to render: the realization carries no notes")
    tempo = float(realization["tempo_bpm"])
    seconds_per_beat = 60.0 / tempo

    last_beat = max(float(row["start_beat"]) + float(row["duration_beats"]) for row in notes)
    total = int((last_beat * seconds_per_beat + RELEASE_SECONDS) * sample_rate) + 1
    left = [0.0] * total
    right = [0.0] * total

    for row in notes:
        start = int(float(row["start_beat"]) * seconds_per_beat * sample_rate)
        seconds = float(row["duration_beats"]) * seconds_per_beat + RELEASE_SECONDS
        samples = _voice(midi_to_hz(int(row["pitch"])), seconds, int(row["velocity"]),
                         sample_rate=sample_rate)
        # Left hand sits left, right hand right; neither is hard-panned.
        gains = (0.68, 0.32) if row["hand"] == "left" else (0.32, 0.68)
        for offset, value in enumerate(samples):
            index = start + offset
            if index >= total:
                break
            left[index] += value * gains[0]
            right[index] += value * gains[1]

    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    # Deterministic headroom: a fixed target, not a measured-then-chosen level.
    scale = (10 ** (-3.0 / 20.0)) / peak
    frames = bytearray()
    ceiling = 2 ** 23 - 1
    for index in range(total):
        for channel in (left, right):
            value = int(max(-ceiling, min(ceiling, round(channel[index] * scale * ceiling))))
            frames += struct.pack("<i", value)[:3]

    _write_wav(Path(destination), bytes(frames), sample_rate=sample_rate)
    return {
        "path": str(destination),
        "sample_rate": sample_rate,
        "channels": CHANNELS,
        "bit_depth": BIT_DEPTH,
        "frames": total,
        "duration_seconds": round(total / sample_rate, 4),
        "peak_target_dbfs": -3.0,
        "voice": "engineering piano: five decaying partials, fixed attack and release",
        "deterministic": "no randomness, no dither, no clock input",
        "reference_pcm_used": False,
    }


def _write_wav(path: Path, frames: bytes, *, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    byte_rate = sample_rate * CHANNELS * BIT_DEPTH // 8
    block = CHANNELS * BIT_DEPTH // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, sample_rate,
                                    byte_rate, block, BIT_DEPTH)
    header += b"data" + struct.pack("<I", len(frames))
    path.write_bytes(header + frames)
