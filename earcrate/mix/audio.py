from __future__ import annotations

import hashlib
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from earcrate.analyze.decode import resample_or_fit
from earcrate.mix.model import MixScoreError


def _mixscore_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _mixscore_pcm_sha256(audio: np.ndarray) -> str:
    payload = np.asarray(audio, dtype="<f4", order="C").tobytes(order="C")
    return hashlib.sha256(payload).hexdigest()


def _mixscore_resample_exact(audio: np.ndarray, target_frames: int) -> np.ndarray:
    source = np.asarray(audio, dtype=np.float32)
    if target_frames <= 0:
        return np.zeros((0, source.shape[1] if source.ndim == 2 else 1), dtype=np.float32)
    if source.ndim != 2 or source.shape[1] != 2:
        raise MixScoreError("internal resampler requires stereo PCM")
    if source.shape[0] <= 0:
        raise MixScoreError("cannot resample an empty source span")
    if source.shape[0] == target_frames:
        return source.astype(np.float32, copy=True)
    return np.column_stack(
        [resample_or_fit(source[:, channel], target_frames) for channel in range(2)]
    ).astype(np.float32)


def _mixscore_decode_stereo(path: Path, sample_rate: int) -> np.ndarray:
    args = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "f32le",
        "-ac",
        "2",
        "-ar",
        str(int(sample_rate)),
        "pipe:1",
    ]
    try:
        completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=1800)
    except FileNotFoundError as exc:
        raise MixScoreError("MixScore rendering requires ffmpeg on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise MixScoreError(f"ffmpeg timed out while decoding {path}") from exc
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", "replace")[-1200:]
        raise MixScoreError(f"ffmpeg could not decode {path}: {error}")
    raw = np.frombuffer(completed.stdout, dtype="<f4")
    if raw.size < 2 or raw.size % 2:
        raise MixScoreError(f"ffmpeg decoded invalid stereo PCM from {path}")
    audio = raw.reshape(-1, 2).astype(np.float32, copy=True)
    if not np.isfinite(audio).all():
        raise MixScoreError(f"decoded source contains non-finite PCM: {path}")
    return audio


def _mixscore_resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    resolved = path.resolve()
    if not resolved.is_file():
        raise MixScoreError(f"MixScore asset does not exist: {resolved}")
    return resolved


def _mixscore_beat_to_frame(beat: float, *, sample_rate: int, bpm: float) -> int:
    return max(0, int(round(float(beat) * float(sample_rate) * 60.0 / float(bpm))))


def _mixscore_source_beat_to_frame(asset: Mapping[str, Any], source_beat: float, sample_rate: int) -> int:
    seconds = float(asset["downbeat_seconds"]) + float(source_beat) * 60.0 / float(asset["source_bpm"])
    return max(0, int(round(seconds * sample_rate)))


def _mixscore_deck_speed(state: Mapping[str, Any], loaded: Mapping[str, Any], master_bpm: float) -> float:
    base = float(master_bpm) / float(loaded["asset"]["source_bpm"]) if bool(state["sync"]) else 1.0
    speed = base * float(state["rate"])
    if not 0.01 <= speed <= 16.0:
        raise MixScoreError(f"deck {state['deck_id']} playback speed {speed:.6f} is outside the renderer range")
    return speed


def _mixscore_circular_loop_audio(
    loaded: Mapping[str, Any],
    *,
    loop_start: int,
    loop_end: int,
    crossfade_frames: int,
) -> np.ndarray:
    source = np.asarray(loaded["audio"], dtype=np.float32)
    loop = source[loop_start:loop_end].copy()
    if loop.shape[0] <= 1:
        raise MixScoreError("loop is shorter than two source frames")
    fade = min(max(0, int(crossfade_frames)), loop.shape[0] // 4)
    if fade > 1:
        x = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        incoming = np.sin(x * math.pi / 2.0)
        outgoing = np.cos(x * math.pi / 2.0)
        blend = loop[-fade:] * outgoing[:, None] + loop[:fade] * incoming[:, None]
        loop[:fade] = blend
        loop[-fade:] = blend
    return loop


def _mixscore_render_transport_span(
    state: dict[str, Any],
    loaded_assets: Mapping[str, Mapping[str, Any]],
    *,
    output_frames: int,
    master_bpm: float,
) -> np.ndarray:
    if output_frames <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    if not bool(state["playing"]):
        return np.zeros((output_frames, 2), dtype=np.float32)
    asset_id = str(state.get("asset_id") or "")
    if asset_id not in loaded_assets:
        raise MixScoreError(f"deck {state['deck_id']} is playing without a loaded asset")
    loaded = loaded_assets[asset_id]
    speed = _mixscore_deck_speed(state, loaded, master_bpm)
    source_frames = max(1, int(round(output_frames * speed)))

    loop = state.get("loop")
    if loop is None:
        start = int(round(float(state["source_frame"])))
        end = start + source_frames
        if start < 0 or end > int(loaded["frames"]):
            raise MixScoreError(
                f"deck {state['deck_id']} exhausted asset {asset_id} while selected for playback"
            )
        source = np.asarray(loaded["audio"][start:end], dtype=np.float32)
        state["source_frame"] = float(end)
    else:
        loop_start = int(loop["start_frame"])
        loop_end = int(loop["end_frame"])
        loop_audio = np.asarray(loop["audio"], dtype=np.float32)
        loop_length = loop_end - loop_start
        phase = int(round(float(state["source_frame"]))) - loop_start
        indices = (phase + np.arange(source_frames, dtype=np.int64)) % loop_length
        source = loop_audio[indices]
        state["source_frame"] = float(loop_start + ((phase + source_frames) % loop_length))
    return _mixscore_resample_exact(source, output_frames)


def _mixscore_apply_pan(audio: np.ndarray, pan: np.ndarray) -> np.ndarray:
    output = np.asarray(audio, dtype=np.float32).copy()
    negative = pan < 0.0
    positive = pan > 0.0
    right = np.ones_like(pan, dtype=np.float32)
    left = np.ones_like(pan, dtype=np.float32)
    right[negative] = np.cos((-pan[negative]) * math.pi / 2.0)
    left[positive] = np.cos(pan[positive] * math.pi / 2.0)
    output[:, 0] *= left
    output[:, 1] *= right
    return output


def _mixscore_crossfader_side_gain(position: np.ndarray, side: str) -> np.ndarray:
    if side == "NONE":
        return np.ones_like(position, dtype=np.float32)
    theta = (np.asarray(position, dtype=np.float64) + 1.0) * math.pi / 4.0
    return (np.cos(theta) if side == "A" else np.sin(theta)).astype(np.float32)


def _mixscore_safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._")
    return text or "deck"
