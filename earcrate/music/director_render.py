from __future__ import annotations

"""Deterministic proof renderer for DJ stage-score custody."""

from collections import Counter, defaultdict
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from earcrate.music.model import music_sha256_json
from earcrate.music.director_validation import DJ_PPQ, MusicDirectorError, music_validate_stage_score

DJ_ROLE_PROGRAMS = {
    "kick": 0,
    "snare": 0,
    "hat": 0,
    "cymbal": 0,
    "percussion": 0,
    "bass": 33,
    "sub_bass": 38,
    "harmony": 4,
    "pad": 89,
    "lead": 81,
    "vocal": 54,
    "sample_trigger": 0,
    "impact": 48,
    "fx": 97,
    "texture": 96,
}


def _dj_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    import json
    import os
    import tempfile

    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite DJ Director artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return {"path": str(destination), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _dj_track_gain(role: str) -> float:
    return {
        "kick": 0.92,
        "snare": 0.76,
        "hat": 0.45,
        "cymbal": 0.54,
        "percussion": 0.50,
        "bass": 0.70,
        "sub_bass": 0.78,
        "harmony": 0.52,
        "pad": 0.40,
        "lead": 0.62,
        "vocal": 0.58,
        "sample_trigger": 0.0,
        "impact": 0.60,
        "fx": 0.33,
        "texture": 0.25,
    }.get(role, 0.45)


def _dj_role_pan(role: str, track: str) -> float:
    base = {
        "kick": 0.0,
        "snare": 0.0,
        "hat": 0.36,
        "cymbal": -0.28,
        "percussion": -0.22,
        "bass": 0.0,
        "sub_bass": 0.0,
        "harmony": -0.18,
        "pad": 0.24,
        "lead": 0.14,
        "vocal": 0.0,
        "impact": 0.0,
        "fx": -0.32,
        "texture": 0.30,
    }.get(role, 0.0)
    if role == "harmony":
        digest = int(hashlib.sha256(track.encode("utf-8")).hexdigest()[:8], 16)
        base += -0.23 if digest % 2 == 0 else 0.23
    return max(-0.88, min(0.88, base))


def _dj_soft_limit(audio):
    import numpy as np
    drive = 1.12
    out = np.tanh(audio * drive) / np.tanh(drive)
    peak = float(np.max(np.abs(out)))
    if peak > 0.985:
        out *= 0.985 / peak
    return out.astype(np.float32)


def _dj_note_frequency(pitch: int) -> float:
    return 440.0 * (2.0 ** ((int(pitch) - 69) / 12.0))


def _dj_lfo_triangle(phase):
    import numpy as np
    return 2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0


def _dj_filter_fft(audio, sample_rate: int, *, lowpass_hz: float | None = None, highpass_hz: float | None = None, resonance: float = 0.0):
    import numpy as np
    if audio.size < 8:
        return audio
    spectrum = np.fft.rfft(audio.astype(np.float64))
    frequencies = np.fft.rfftfreq(audio.size, 1.0 / sample_rate)
    response = np.ones_like(frequencies)
    if highpass_hz and highpass_hz > 5.0:
        ratio = frequencies / max(1e-9, highpass_hz)
        response *= np.clip(ratio ** 3.2, 0.0, 1.0)
    if lowpass_hz and lowpass_hz < sample_rate * 0.49:
        ratio = lowpass_hz / np.maximum(frequencies, 1e-9)
        response *= np.clip(ratio ** 3.0, 0.0, 1.0)
        if resonance > 0.0:
            response *= 1.0 + resonance * np.exp(-0.5 * ((frequencies - lowpass_hz) / max(35.0, lowpass_hz * 0.12)) ** 2)
    return np.fft.irfft(spectrum * response, n=audio.size).astype(np.float32)


def _dj_envelope(frames: int, sample_rate: int, *, attack: float, release: float, decay: float = 0.0, sustain: float = 1.0):
    import numpy as np
    envelope = np.ones(frames, dtype=np.float32) * float(sustain)
    attack_frames = min(frames, max(1, int(round(attack * sample_rate))))
    envelope[:attack_frames] = np.linspace(0.0, 1.0, attack_frames, endpoint=False, dtype=np.float32)
    if decay > 0 and frames > attack_frames:
        decay_frames = min(frames - attack_frames, max(1, int(round(decay * sample_rate))))
        envelope[attack_frames:attack_frames + decay_frames] = np.linspace(1.0, sustain, decay_frames, endpoint=False, dtype=np.float32)
    release_frames = min(frames, max(1, int(round(release * sample_rate))))
    envelope[-release_frames:] *= np.linspace(1.0, 0.0, release_frames, endpoint=True, dtype=np.float32)
    return envelope


def _dj_deterministic_noise(frames: int, seed_text: str):
    import numpy as np
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16) & 0xFFFFFFFF
    return np.random.default_rng(seed).standard_normal(frames).astype(np.float32)


def _dj_tonal_voice(role: str, pitch: int, velocity: int, frames: int, sample_rate: int, seed_text: str):
    import numpy as np
    if frames <= 0:
        return np.zeros(0, dtype=np.float32)
    frequency = _dj_note_frequency(pitch)
    t = np.arange(frames, dtype=np.float64) / sample_rate
    phase = np.mod(frequency * t, 1.0)
    sine = np.sin(2.0 * math.pi * phase)
    triangle = _dj_lfo_triangle(phase)
    saw = 2.0 * phase - 1.0
    if role == "sub_bass":
        voice = 0.93 * sine + 0.07 * np.sin(4.0 * math.pi * phase)
        env = _dj_envelope(frames, sample_rate, attack=0.012, release=0.12, sustain=0.98)
    elif role == "bass":
        voice = 0.52 * triangle + 0.28 * sine + 0.20 * np.sin(4.0 * math.pi * phase)
        env = _dj_envelope(frames, sample_rate, attack=0.006, release=0.065, decay=0.06, sustain=0.73)
    elif role == "harmony":
        voice = 0.58 * triangle + 0.27 * sine + 0.15 * np.sin(6.0 * math.pi * phase)
        env = _dj_envelope(frames, sample_rate, attack=0.004, release=0.085, decay=0.18, sustain=0.64)
    elif role == "pad":
        voice = 0.48 * triangle + 0.32 * sine + 0.12 * np.sin(6.0 * math.pi * phase)
        voice += 0.08 * _dj_deterministic_noise(frames, seed_text)
        env = _dj_envelope(frames, sample_rate, attack=0.16, release=0.28, sustain=0.82)
    elif role == "vocal":
        vibrato = 0.006 * np.sin(2.0 * math.pi * 5.1 * t)
        voice = 0.72 * np.sin(2.0 * math.pi * phase + vibrato) + 0.20 * triangle
        voice += 0.08 * _dj_deterministic_noise(frames, seed_text)
        env = _dj_envelope(frames, sample_rate, attack=0.015, release=0.08, sustain=0.86)
    else:
        voice = 0.43 * saw + 0.37 * triangle + 0.20 * sine
        env = _dj_envelope(frames, sample_rate, attack=0.002, release=0.055, decay=0.04, sustain=0.74)
    amplitude = (max(1, min(127, int(velocity))) / 127.0) ** 0.86
    return (voice.astype(np.float32) * env * amplitude).astype(np.float32)


def _dj_percussion_voice(role: str, pitch: int, velocity: int, frames: int, sample_rate: int, seed_text: str):
    import numpy as np
    if frames <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(frames, dtype=np.float64) / sample_rate
    noise = _dj_deterministic_noise(frames, seed_text)
    amplitude = (max(1, min(127, int(velocity))) / 127.0) ** 0.78
    if role == "kick":
        frequency = 94.0 * np.exp(-t * 24.0) + 42.0
        phase = 2.0 * math.pi * np.cumsum(frequency) / sample_rate
        body = np.sin(phase) * np.exp(-t * 15.0)
        click = _dj_filter_fft(noise, sample_rate, highpass_hz=2400.0) * np.exp(-t * 95.0)
        voice = 0.92 * body + 0.14 * click
    elif role == "snare":
        tone = np.sin(2.0 * math.pi * 185.0 * t) * np.exp(-t * 24.0)
        body = _dj_filter_fft(noise, sample_rate, lowpass_hz=7200.0, highpass_hz=520.0)
        voice = 0.66 * body * np.exp(-t * 22.0) + 0.34 * tone
    elif role in {"hat", "cymbal"}:
        cut = 5400.0 if role == "hat" else 3200.0
        bright = _dj_filter_fft(noise, sample_rate, highpass_hz=cut)
        voice = bright * np.exp(-t * (62.0 if role == "hat" else 9.0))
    elif role in {"impact", "fx"}:
        body = _dj_filter_fft(noise, sample_rate, lowpass_hz=8500.0, highpass_hz=60.0)
        low = np.sin(2.0 * math.pi * 62.0 * t) * np.exp(-t * 4.8)
        voice = 0.57 * body * np.exp(-t * 5.4) + 0.43 * low
    else:
        body = _dj_filter_fft(noise, sample_rate, lowpass_hz=9200.0, highpass_hz=160.0)
        voice = body * np.exp(-t * 26.0)
    return (voice.astype(np.float32) * amplitude).astype(np.float32)


def _dj_section_at(score: Mapping[str, Any], tick: int) -> Mapping[str, Any] | None:
    for section in score.get("sections") or []:
        if int(section["start_tick"]) <= int(tick) < int(section["end_tick"]):
            return section
    return None


def _dj_cc11_envelope(score: Mapping[str, Any], track: str, frames: int, sample_rate: int):
    import numpy as np
    tempo = float(score.get("tempo_bpm") or 120.0)
    values = np.ones(frames, dtype=np.float32)
    events = [row for row in score.get("events") or [] if str(row.get("kind") or "") == "cc" and str(row.get("track") or "") == track and int(row.get("controller") or 0) == 11]
    if not events:
        return values
    events.sort(key=lambda row: (int(row["start_tick"]), int(row.get("value") or 0)))
    points = [(max(0, min(frames - 1, int(round(int(row["start_tick"]) / DJ_PPQ * 60.0 / tempo * sample_rate)))), max(0.0, min(1.0, int(row.get("value") or 0) / 127.0))) for row in events]
    for index, (frame, value) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else frames
        next_value = points[index + 1][1] if index + 1 < len(points) else value
        if end > frame:
            values[frame:end] = np.linspace(value, next_value, end - frame, endpoint=False, dtype=np.float32)
    values[:points[0][0]] = points[0][1]
    return values


def _dj_apply_sidechain(score: Mapping[str, Any], role_buffers: Mapping[str, Any], sample_rate: int):
    import numpy as np
    kick = role_buffers.get("kick")
    if kick is None:
        return
    detector = np.mean(np.abs(kick), axis=1)
    window = max(1, int(round(0.012 * sample_rate)))
    detector = np.convolve(detector, np.ones(window) / window, mode="same")
    detector /= max(float(np.max(detector)), 1e-9)
    amount = 0.42
    gain = 1.0 - amount * detector
    release_frames = max(1, int(round(0.15 * sample_rate)))
    kernel = np.exp(-np.arange(release_frames, dtype=np.float32) / max(1.0, release_frames * 0.28))
    kernel /= max(float(kernel.max()), 1e-9)
    gain = 1.0 - np.convolve(1.0 - gain, kernel, mode="same")
    gain = np.clip(gain, 0.45, 1.0).astype(np.float32)
    for role in ("bass", "sub_bass", "harmony", "pad"):
        if role in role_buffers:
            role_buffers[role] *= gain[:, None]


def _dj_apply_section_filters(score: Mapping[str, Any], role_buffers: Mapping[str, Any], sample_rate: int):
    import numpy as np
    tempo = float(score.get("tempo_bpm") or 120.0)
    total_frames = next(iter(role_buffers.values())).shape[0]
    for section in score.get("sections") or []:
        start = max(0, min(total_frames, int(round(int(section["start_tick"]) / DJ_PPQ * 60.0 / tempo * sample_rate))))
        end = max(start, min(total_frames, int(round(int(section["end_tick"]) / DJ_PPQ * 60.0 / tempo * sample_rate))))
        if end <= start:
            continue
        kinds = {str(action.get("kind") or "") for action in section.get("mix_actions") or []}
        if "filter_close" in kinds or str(section.get("function") or "") == "air":
            for role in ("harmony", "pad", "lead", "texture"):
                if role in role_buffers:
                    role_buffers[role][start:end] = _dj_filter_fft(role_buffers[role][start:end, 0], sample_rate, lowpass_hz=1450.0, resonance=0.12)[:, None]
        if "filter_open" in kinds or str(section.get("function") or "") == "build":
            span = end - start
            for role in ("harmony", "pad", "lead"):
                if role not in role_buffers:
                    continue
                segment = role_buffers[role][start:end]
                split = max(1, span // 4)
                for index in range(4):
                    a, b = index * split, span if index == 3 else min(span, (index + 1) * split)
                    cutoff = 1150.0 + index * 2100.0
                    for channel in range(segment.shape[1]):
                        segment[a:b, channel] = _dj_filter_fft(segment[a:b, channel], sample_rate, lowpass_hz=cutoff, resonance=0.08)
                role_buffers[role][start:end] = segment


def _dj_short_delay(audio, sample_rate: int, seconds: float, amount: float):
    import numpy as np
    delay = max(1, int(round(seconds * sample_rate)))
    out = audio.copy()
    if delay < out.shape[0]:
        out[delay:, 0] += amount * audio[:-delay, 1]
        out[delay:, 1] += amount * audio[:-delay, 0]
    return out


def _dj_audio_metrics(audio, sample_rate: int) -> dict[str, Any]:
    import numpy as np
    from scipy.signal import stft
    mono = np.mean(audio, axis=1)
    block = max(1, int(round(0.5 * sample_rate)))
    values = [float(np.sqrt(np.mean(mono[i:i + block] ** 2) + 1e-12)) for i in range(0, mono.size, block)]
    db = 20.0 * np.log10(np.maximum(np.asarray(values), 1e-9))
    frequencies, _, spectrum = stft(mono, fs=sample_rate, nperseg=2048, noverlap=1536, boundary=None)
    power = np.abs(spectrum) ** 2
    total = float(np.sum(power) + 1e-12)
    return {"rms_std_db": float(np.std(db)), "low200_share": float(np.sum(power[frequencies < 200]) / total), "high3000_share": float(np.sum(power[frequencies >= 3000]) / total), "peak": float(np.max(np.abs(audio))), "stereo_side_mid_db": float(20.0 * np.log10((np.sqrt(np.mean(((audio[:, 0] - audio[:, 1]) * 0.5) ** 2)) + 1e-9) / (np.sqrt(np.mean(((audio[:, 0] + audio[:, 1]) * 0.5) ** 2)) + 1e-9)))}


def _dj_band_gains(audio, sample_rate: int, targets: Mapping[str, float]):
    import numpy as np
    spectrum = np.fft.rfft(audio.astype(np.float64), axis=0)
    frequencies = np.fft.rfftfreq(audio.shape[0], 1.0 / sample_rate)
    low = frequencies < 200
    high = frequencies >= 3000
    power = np.abs(spectrum) ** 2
    total = float(np.sum(power) + 1e-12)
    low_share = float(np.sum(power[low]) / total)
    high_share = float(np.sum(power[high]) / total)
    low_target = float(targets.get("low200_ceiling") or 0.50)
    high_target = float(targets.get("high3000_target") or 0.12)
    low_gain = min(1.0, math.sqrt(low_target / max(low_share, 1e-9))) if low_share > low_target else 1.0
    high_gain = min(3.2, math.sqrt(high_target / max(high_share, 1e-9))) if high_share < high_target else 1.0
    response = np.ones(frequencies.shape, dtype=np.float64)
    response[low] *= low_gain
    response[high] *= high_gain
    return np.fft.irfft(spectrum * response[:, None], n=audio.shape[0], axis=0).astype(np.float32), {"low_gain": low_gain, "high_gain": high_gain, "before_low200": low_share, "before_high3000": high_share}


def _dj_midi_priority(message: Mapping[str, Any], is_meta: bool) -> int:
    typ = str(message.get("type") or "")
    if is_meta and typ == "track_name": return 0
    if typ == "program_change": return 1
    if typ in {"control_change", "pitchwheel"}: return 2
    if typ == "note_off" or (typ == "note_on" and int(message.get("velocity") or 0) == 0): return 3
    if typ == "note_on": return 4
    if is_meta and typ == "end_of_track": return 9
    return 5


def _dj_midi_track(name: str, absolute: Sequence[Mapping[str, Any]], total_ticks: int) -> dict[str, Any]:
    rows = [{"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": name}}, *[dict(row) for row in absolute], {"tick": total_ticks, "is_meta": True, "message": {"type": "end_of_track"}}]
    rows.sort(key=lambda row: (int(row["tick"]), _dj_midi_priority(row["message"], bool(row["is_meta"])), music_sha256_json(row["message"])))
    return {"track_index": 0, "name": name, "events": [{"tick": int(row["tick"]), "order": i, "is_meta": bool(row["is_meta"]), "message": dict(row["message"])} for i, row in enumerate(rows)]}


def _dj_apply_presence_targets(audio, sample_rate: int, *, targets: Mapping[str, float] | None = None):
    return _dj_band_gains(audio, sample_rate, targets or {})


def music_render_director_score(score: Mapping[str, Any], output_path: str | Path, *, sample_rate: int = 44_100, overwrite: bool = False) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    music_validate_stage_score(score)
    destination = Path(output_path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite DJ Director proof render: {destination}")
    duration = float(score.get("duration_seconds") or 0.0)
    frames = int(round(duration * sample_rate))
    if frames <= 0:
        raise MusicDirectorError("DJ Director render duration must be positive")
    tempo = float(score.get("tempo_bpm") or 120.0)
    seconds_per_tick = 60.0 / tempo / DJ_PPQ
    tracks = {str(row.get("track") or "") for row in score.get("events") or [] if row.get("track")}
    track_roles = {}
    for track in tracks:
        roles = Counter(str(row.get("role") or "") for row in score.get("events") or [] if str(row.get("track") or "") == track and str(row.get("kind") or "") in {"note", "sample_trigger"})
        track_roles[track] = roles.most_common(1)[0][0] if roles else "texture"
    track_buffers = {track: np.zeros((frames, 2), dtype=np.float32) for track in tracks}
    rendered_notes = 0
    for event in score.get("events") or []:
        kind = str(event.get("kind") or "")
        if kind not in {"note", "sample_trigger"} or kind == "sample_trigger":
            continue
        track = str(event.get("track") or "")
        role = str(event.get("role") or track_roles.get(track) or "texture")
        start = max(0, int(round(int(event.get("start_tick") or 0) * seconds_per_tick * sample_rate)))
        end = min(frames, start + max(1, int(round(int(event.get("duration_tick") or 0) * seconds_per_tick * sample_rate))))
        if end <= start or track not in track_buffers:
            continue
        voice = _dj_percussion_voice(role, int(event.get("pitch") or 36), int(event.get("velocity") or 64), end - start, sample_rate, str(event.get("event_id") or "")) if role in {"kick", "snare", "hat", "cymbal", "percussion", "impact", "fx"} else _dj_tonal_voice(role, int(event.get("pitch") or 60), int(event.get("velocity") or 64), end - start, sample_rate, str(event.get("event_id") or ""))
        gain = _dj_track_gain(role)
        pan = _dj_role_pan(role, track)
        left, right = math.sqrt(0.5 * (1.0 - pan)), math.sqrt(0.5 * (1.0 + pan))
        track_buffers[track][start:end, 0] += voice * gain * left
        track_buffers[track][start:end, 1] += voice * gain * right
        rendered_notes += 1
    for track, buffer in track_buffers.items():
        envelope = _dj_cc11_envelope(score, track, frames, sample_rate)
        buffer *= envelope[:, None]
    role_buffers = defaultdict(lambda: np.zeros((frames, 2), dtype=np.float32))
    for track, buffer in track_buffers.items():
        role_buffers[track_roles.get(track, "texture")] += buffer
    _dj_apply_section_filters(score, role_buffers, sample_rate)
    _dj_apply_sidechain(score, role_buffers, sample_rate)
    mix = np.zeros((frames, 2), dtype=np.float32)
    for buffer in role_buffers.values():
        mix += buffer
    mix = _dj_short_delay(mix, sample_rate, 0.021, 0.10)
    mix, presence = _dj_apply_presence_targets(mix, sample_rate, targets={"low200_ceiling": 0.50, "high3000_target": 0.12})
    mix = _dj_soft_limit(mix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), mix, sample_rate, subtype="PCM_16")
    decoded, decoded_rate = sf.read(str(destination), always_2d=True, dtype="float32")
    metrics = _dj_audio_metrics(decoded, decoded_rate)
    receipt = {"schema": "earcrate/dj-director-render@1", "ok": True, "score_sha256": score["score_sha256"], "output_path": str(destination), "raw_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "sample_rate": decoded_rate, "channels": int(decoded.shape[1]), "frames": int(decoded.shape[0]), "duration_seconds": float(decoded.shape[0] / decoded_rate), "rendered_note_count": rendered_notes, "track_count": len(track_buffers), "presence_processing": presence, "metrics": metrics}
    receipt["render_sha256"] = music_sha256_json(receipt)
    return receipt

__all__ = ["music_render_director_score"]
