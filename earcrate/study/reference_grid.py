from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from earcrate.analyze.decode import decode_audio, decoded_audio_sha256, ffprobe_json
from earcrate.midi.model import midi_sha256_json
from earcrate.providers.notes import notes_canonical_json, notes_observation_payload, notes_sha256_file

REFERENCE_GRID_SCHEMA_VERSION = 1
REFERENCE_GRID_KIND = "earcrate_accepted_beat_grid"
REFERENCE_NOTE_OBSERVATION_KIND = "note_transcription_observation"
REFERENCE_DRUM_OBSERVATION_KIND = "earcrate_drum_trigger_observation"


class ReferenceBundleError(ValueError):
    """Raised when reference evidence cannot be accepted without losing receipts."""


def _reference_finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ReferenceBundleError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ReferenceBundleError(f"{name} must be finite")
    return number


def _reference_source_duration(path: Path) -> float:
    data = ffprobe_json(path)
    values = []
    try:
        values.append(float((data.get("format") or {}).get("duration") or 0.0))
    except Exception:
        pass
    for stream in data.get("streams") or []:
        try:
            values.append(float(stream.get("duration") or 0.0))
        except Exception:
            continue
    return max(values or [0.0])


def reference_grid_from_beats(
    beats_seconds: Sequence[float],
    *,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    downbeat_indices: Sequence[int] | None = None,
    source_pcm_sha256: str = "",
    provider: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
    accepted: bool = True,
    accepted_by: str = "manual",
    acceptance_reason: str = "authored beat grid",
) -> dict[str, Any]:
    """Create a constant-meter beat grid. Tick zero is the first accepted beat."""
    beats = [_reference_finite(value, "beat time") for value in beats_seconds]
    if len(beats) < 2:
        raise ReferenceBundleError("an accepted beat grid requires at least two beats")
    if beats[0] < 0.0 or any(right <= left for left, right in zip(beats, beats[1:])):
        raise ReferenceBundleError("beat times must be nonnegative and strictly increasing")
    numerator = int(meter_numerator)
    denominator = int(meter_denominator)
    if numerator <= 0 or denominator <= 0 or denominator & (denominator - 1):
        raise ReferenceBundleError("meter requires a positive numerator and power-of-two denominator")
    downbeats = [int(value) for value in (downbeat_indices if downbeat_indices is not None else range(0, len(beats), numerator))]
    if not downbeats or downbeats[0] != 0:
        raise ReferenceBundleError("accepted grid must mark its first beat as a downbeat")
    if any(value < 0 or value >= len(beats) for value in downbeats):
        raise ReferenceBundleError("downbeat index is outside the beat grid")
    if downbeats != sorted(set(downbeats)):
        raise ReferenceBundleError("downbeat indices must be sorted and unique")
    if any(right - left != numerator for left, right in zip(downbeats, downbeats[1:])):
        raise ReferenceBundleError("beat grid v1 requires a constant meter")
    provider_row = {
        "name": str((provider or {}).get("name") or "manual"),
        "version": str((provider or {}).get("version") or "1"),
        "kind": str((provider or {}).get("kind") or "authored"),
        "configuration": dict((provider or {}).get("configuration") or {}),
    }
    status = "accepted" if accepted else "proposed"
    acceptance = {
        "actor": str(accepted_by) if accepted else "",
        "reason": str(acceptance_reason) if accepted else "",
        "parent_grid_sha256": "",
    }
    grid = {
        "schema_version": REFERENCE_GRID_SCHEMA_VERSION,
        "kind": REFERENCE_GRID_KIND,
        "status": status,
        "source_pcm_sha256": str(source_pcm_sha256),
        "origin_seconds": round(float(beats[0]), 12),
        "meter": {"numerator": numerator, "denominator": denominator},
        "provider": provider_row,
        "confidence": max(0.0, min(1.0, _reference_finite(confidence, "grid confidence"))),
        "acceptance": acceptance,
        "beats": [
            {"beat_index": index, "time_seconds": round(time, 12), "downbeat": index in set(downbeats)}
            for index, time in enumerate(beats)
        ],
        "downbeat_indices": downbeats,
    }
    grid["grid_sha256"] = midi_sha256_json(grid)
    reference_validate_grid(grid, require_accepted=False)
    return grid


def reference_accept_grid(
    proposed_grid: Mapping[str, Any],
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    reference_validate_grid(proposed_grid, require_accepted=False)
    if not str(actor).strip() or not str(reason).strip():
        raise ReferenceBundleError("accepting a grid requires an actor and reason")
    out = json.loads(json.dumps(dict(proposed_grid)))
    parent = str(out["grid_sha256"])
    out["status"] = "accepted"
    out["acceptance"] = {
        "actor": str(actor),
        "reason": str(reason),
        "parent_grid_sha256": parent,
    }
    out["grid_sha256"] = midi_sha256_json({key: value for key, value in out.items() if key != "grid_sha256"})
    reference_validate_grid(out, require_accepted=True)
    return out


def reference_validate_grid(grid: Mapping[str, Any], *, require_accepted: bool = True) -> None:
    if int(grid.get("schema_version") or 0) != REFERENCE_GRID_SCHEMA_VERSION:
        raise ReferenceBundleError(f"unsupported beat-grid schema: {grid.get('schema_version')}")
    if str(grid.get("kind") or "") != REFERENCE_GRID_KIND:
        raise ReferenceBundleError(f"unsupported beat-grid kind: {grid.get('kind')}")
    status = str(grid.get("status") or "")
    if status not in {"proposed", "accepted"}:
        raise ReferenceBundleError("beat-grid status must be proposed or accepted")
    if require_accepted and status != "accepted":
        raise ReferenceBundleError("reference compilation requires an explicitly accepted beat grid")
    beats = grid.get("beats")
    if not isinstance(beats, list) or len(beats) < 2:
        raise ReferenceBundleError("beat grid requires at least two beat rows")
    times = []
    for index, row in enumerate(beats):
        if int(row.get("beat_index", -1)) != index:
            raise ReferenceBundleError("beat-grid indices must be contiguous")
        times.append(_reference_finite(row.get("time_seconds"), "beat time"))
    if times[0] < 0.0 or any(right <= left for left, right in zip(times, times[1:])):
        raise ReferenceBundleError("beat-grid times must be nonnegative and increasing")
    if abs(_reference_finite(grid.get("origin_seconds"), "grid origin") - times[0]) > 1e-9:
        raise ReferenceBundleError("beat-grid origin_seconds disagrees with its first beat")
    meter = grid.get("meter") or {}
    numerator = int(meter.get("numerator") or 0)
    denominator = int(meter.get("denominator") or 0)
    if numerator <= 0 or denominator <= 0 or denominator & (denominator - 1):
        raise ReferenceBundleError("beat-grid meter is invalid")
    downbeats = [int(value) for value in grid.get("downbeat_indices") or []]
    if not downbeats or downbeats[0] != 0 or downbeats != sorted(set(downbeats)):
        raise ReferenceBundleError("beat-grid downbeats must be sorted, unique, and begin at zero")
    if any(value < 0 or value >= len(beats) for value in downbeats):
        raise ReferenceBundleError("beat-grid downbeat is outside the grid")
    if any(right - left != numerator for left, right in zip(downbeats, downbeats[1:])):
        raise ReferenceBundleError("beat-grid downbeats disagree with its constant meter")
    acceptance = grid.get("acceptance") or {}
    if status == "accepted" and (not str(acceptance.get("actor") or "") or not str(acceptance.get("reason") or "")):
        raise ReferenceBundleError("accepted beat grid requires an actor and reason")
    expected = midi_sha256_json({key: value for key, value in grid.items() if key != "grid_sha256"})
    if str(grid.get("grid_sha256") or "") != expected:
        raise ReferenceBundleError("grid_sha256 does not match beat-grid contents")


def reference_propose_grid_from_audio(
    audio_path: str | Path,
    *,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    sample_rate: int = 22_050,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    """Create a deterministic librosa beat-grid proposal. It is not accepted automatically."""
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if sample_rate <= 0:
        raise ReferenceBundleError("grid proposal sample_rate must be positive")
    import librosa

    duration = float(duration_seconds) if duration_seconds and duration_seconds > 0 else _reference_source_duration(source)
    y = decode_audio(source, sr=sample_rate, duration=duration if duration > 0 else None)
    onset = librosa.onset.onset_strength(y=y, sr=sample_rate)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sample_rate, units="frames")
    beat_frames = np.asarray(beat_frames, dtype=int)
    if beat_frames.size < meter_numerator + 2:
        raise ReferenceBundleError("deterministic beat tracker found too few beats")
    safe = np.clip(beat_frames, 0, max(0, onset.size - 1))
    phase_scores = [float(np.sum(onset[safe[phase::meter_numerator]])) for phase in range(meter_numerator)]
    phase = min(
        range(meter_numerator),
        key=lambda value: (-phase_scores[value], value),
    )
    beat_frames = beat_frames[phase:]
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate).astype(float).tolist()
    intervals = np.diff(np.asarray(beat_times, dtype=float))
    regularity = max(0.0, 1.0 - float(np.std(intervals) / max(1e-9, np.mean(intervals))))
    salience = onset[np.clip(beat_frames, 0, max(0, onset.size - 1))]
    salience_score = float(np.mean(salience) / max(1e-9, np.percentile(onset, 90))) if onset.size else 0.0
    confidence = max(0.0, min(1.0, 0.72 * regularity + 0.28 * min(1.0, salience_score)))
    try:
        version = importlib.metadata.version("librosa")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    pcm_sha = decoded_audio_sha256(source, sample_rate, duration)
    return reference_grid_from_beats(
        beat_times,
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        source_pcm_sha256=pcm_sha,
        provider={
            "name": "librosa-beat-track",
            "version": version,
            "kind": "deterministic_dsp_proposal",
            "configuration": {
                "sample_rate": int(sample_rate),
                "duration_seconds": float(duration_seconds),
                "estimated_tempo_bpm": float(np.asarray(tempo).reshape(-1)[0]),
                "downbeat_phase": int(phase),
                "phase_scores": [round(value, 9) for value in phase_scores],
            },
        },
        confidence=confidence,
        accepted=False,
    )


def reference_manual_note_observation(
    notes: Sequence[Mapping[str, Any]],
    *,
    source_identity: str,
    label: str = "manual",
) -> dict[str, Any]:
    canonical = []
    for index, raw in enumerate(notes):
        start = _reference_finite(raw.get("start_s", raw.get("start_seconds")), "note start")
        end = _reference_finite(raw.get("end_s", raw.get("end_seconds")), "note end")
        pitch = int(raw.get("pitch_midi", raw.get("note", -1)))
        velocity = int(raw.get("velocity", 100))
        if start < 0 or end <= start:
            raise ReferenceBundleError("manual note requires nonnegative start and positive duration")
        if not 0 <= pitch <= 127 or not 1 <= velocity <= 127:
            raise ReferenceBundleError("manual note pitch or velocity is outside MIDI range")
        bends = raw.get("pitch_bends")
        if bends is not None:
            if not isinstance(bends, list) or not all(isinstance(value, int) for value in bends):
                raise ReferenceBundleError("manual pitch_bends must be a list of integers")
            bends = [int(value) for value in bends]
        note_id = str(raw.get("note_id", raw.get("note_event_id")) or "manual_note_" + midi_sha256_json({"source": source_identity, "index": index, "start": start, "end": end, "pitch": pitch, "velocity": velocity})[:24])
        canonical.append(
            {
                "note_id": note_id,
                "ordinal": index,
                "start_s": round(start, 12),
                "end_s": round(end, 12),
                "pitch_midi": pitch,
                "amplitude": round(velocity / 127.0, 12),
                "velocity": velocity,
                "pitch_bends": bends or [],
            }
        )
    canonical.sort(key=lambda row: (float(row["start_s"]), float(row["end_s"]), int(row["pitch_midi"]), str(row["note_id"])))
    observation = {
        "schema_version": 1,
        "kind": REFERENCE_NOTE_OBSERVATION_KIND,
        "provider": "manual",
        "provider_version": "1",
        "model_path": "",
        "model_sha256": "",
        "source_path": "",
        "source_identity": str(source_identity),
        "config": {"label": str(label), "pitch_bend_units_per_semitone": 3.0},
        "artifact_key": "",
        "note_count": len(canonical),
        "notes": canonical,
        "midi_sha256": "",
        "model_outputs": {},
        "cache_status": "authored",
    }
    observation["observation_sha256"] = hashlib.sha256(
        notes_canonical_json(notes_observation_payload(observation)).encode("utf-8")
    ).hexdigest()
    reference_validate_note_observation(observation)
    return observation


def reference_validate_note_observation(observation: Mapping[str, Any]) -> None:
    if str(observation.get("kind") or "") != REFERENCE_NOTE_OBSERVATION_KIND:
        raise ReferenceBundleError(f"unsupported note observation kind: {observation.get('kind')}")
    notes = observation.get("notes")
    if not isinstance(notes, list):
        raise ReferenceBundleError("note observation notes must be a list")
    note_ids = []
    for row in notes:
        note_id = str(row.get("note_id") or "")
        note_ids.append(note_id)
        start = _reference_finite(row.get("start_s"), "note start")
        end = _reference_finite(row.get("end_s"), "note end")
        pitch = int(row.get("pitch_midi", -1))
        velocity = int(row.get("velocity", 0))
        if start < 0 or end <= start or not 0 <= pitch <= 127 or not 1 <= velocity <= 127:
            raise ReferenceBundleError("note observation contains an invalid note")
        bends = row.get("pitch_bends") or []
        if not isinstance(bends, list) or not all(isinstance(value, int) for value in bends):
            raise ReferenceBundleError("note observation pitch bends must be integer lists")
    if not all(note_ids) or len(note_ids) != len(set(note_ids)):
        raise ReferenceBundleError("note observation IDs must be unique and nonempty")
    if int(observation.get("note_count") or 0) != len(notes):
        raise ReferenceBundleError("note observation note_count disagrees with notes")
    expected = hashlib.sha256(notes_canonical_json(notes_observation_payload(observation)).encode("utf-8")).hexdigest()
    if str(observation.get("observation_sha256") or "") != expected:
        raise ReferenceBundleError("note observation SHA-256 does not match contents")


def reference_drum_observation(
    events: Sequence[Mapping[str, Any]],
    *,
    source_identity: str,
    label: str = "manual-drums",
    provider: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = []
    for index, raw in enumerate(events):
        time_seconds = _reference_finite(raw.get("time_s", raw.get("time_seconds")), "drum time")
        note = int(raw.get("note", -1))
        velocity = int(raw.get("velocity", 100))
        duration_beats = _reference_finite(raw.get("duration_beats", 0.125), "drum duration")
        if time_seconds < 0 or not 0 <= note <= 127 or not 1 <= velocity <= 127 or duration_beats <= 0:
            raise ReferenceBundleError("drum trigger is outside its valid range")
        event_id = str(raw.get("event_id") or "drum_" + midi_sha256_json({"source": source_identity, "index": index, "time": time_seconds, "note": note, "velocity": velocity})[:24])
        canonical.append(
            {
                "event_id": event_id,
                "time_seconds": round(time_seconds, 12),
                "note": note,
                "velocity": velocity,
                "duration_beats": round(duration_beats, 12),
                "role": str(raw.get("role") or "drum"),
                "confidence": max(0.0, min(1.0, _reference_finite(raw.get("confidence", 1.0), "drum confidence"))),
            }
        )
    canonical.sort(key=lambda row: (float(row["time_seconds"]), int(row["note"]), str(row["event_id"])))
    observation = {
        "schema_version": 1,
        "kind": REFERENCE_DRUM_OBSERVATION_KIND,
        "provider": {
            "name": str((provider or {}).get("name") or "manual"),
            "version": str((provider or {}).get("version") or "1"),
            "kind": str((provider or {}).get("kind") or "authored"),
            "configuration": dict((provider or {}).get("configuration") or {}),
        },
        "source_identity": str(source_identity),
        "label": str(label),
        "event_count": len(canonical),
        "events": canonical,
    }
    observation["observation_sha256"] = midi_sha256_json(observation)
    reference_validate_drum_observation(observation)
    return observation


def reference_validate_drum_observation(observation: Mapping[str, Any]) -> None:
    if str(observation.get("kind") or "") != REFERENCE_DRUM_OBSERVATION_KIND:
        raise ReferenceBundleError(f"unsupported drum observation kind: {observation.get('kind')}")
    events = observation.get("events")
    if not isinstance(events, list):
        raise ReferenceBundleError("drum observation events must be a list")
    ids = [str(row.get("event_id") or "") for row in events]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ReferenceBundleError("drum observation IDs must be unique and nonempty")
    for row in events:
        if _reference_finite(row.get("time_seconds"), "drum time") < 0:
            raise ReferenceBundleError("drum observation time cannot be negative")
        if not 0 <= int(row.get("note", -1)) <= 127 or not 1 <= int(row.get("velocity", 0)) <= 127:
            raise ReferenceBundleError("drum observation note or velocity is invalid")
    if int(observation.get("event_count") or 0) != len(events):
        raise ReferenceBundleError("drum observation event_count disagrees with events")
    expected = midi_sha256_json({key: value for key, value in observation.items() if key != "observation_sha256"})
    if str(observation.get("observation_sha256") or "") != expected:
        raise ReferenceBundleError("drum observation SHA-256 does not match contents")


def reference_propose_drum_observation_from_audio(
    audio_path: str | Path,
    *,
    source_identity: str = "",
    sample_rate: int = 22_050,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    """Extract deterministic kick/snare/hat trigger proposals from an isolated drum stem."""
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    import librosa

    duration = float(duration_seconds) if duration_seconds and duration_seconds > 0 else _reference_source_duration(source)
    y = decode_audio(source, sr=sample_rate, duration=duration if duration > 0 else None)
    onset_envelope = librosa.onset.onset_strength(y=y, sr=sample_rate)
    frames = librosa.onset.onset_detect(onset_envelope=onset_envelope, sr=sample_rate, backtrack=False, units="frames")
    times = librosa.frames_to_time(frames, sr=sample_rate)
    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=2048)
    rms = librosa.feature.rms(S=stft).reshape(-1)
    peak = float(np.percentile(rms, 95)) if rms.size else 1.0
    events = []
    for index, (frame, time_seconds) in enumerate(zip(frames, times)):
        column = stft[:, min(int(frame), stft.shape[1] - 1)] if stft.shape[1] else np.zeros(len(frequencies))
        total = float(np.sum(column) + 1e-12)
        low = float(np.sum(column[frequencies < 180.0]) / total)
        mid = float(np.sum(column[(frequencies >= 180.0) & (frequencies < 3200.0)]) / total)
        high = float(np.sum(column[frequencies >= 3200.0]) / total)
        role, note, confidence = max(
            [
                ("kick", 36, low),
                ("snare", 38, 0.72 * mid + 0.28 * high),
                ("hat", 42, high),
            ],
            key=lambda row: (row[2], -row[1]),
        )
        velocity = max(1, min(127, int(round(127.0 * min(1.0, float(rms[min(int(frame), len(rms) - 1)]) / max(1e-9, peak)))))) if rms.size else 96
        events.append(
            {
                "event_id": "dsp_drum_" + midi_sha256_json({"source": str(source), "index": index, "time": float(time_seconds), "role": role})[:24],
                "time_seconds": float(time_seconds),
                "note": note,
                "velocity": velocity,
                "duration_beats": 0.125,
                "role": role,
                "confidence": confidence,
            }
        )
    if not source_identity:
        source_identity = "byte_sha256:" + notes_sha256_file(source)
    try:
        version = importlib.metadata.version("librosa")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return reference_drum_observation(
        events,
        source_identity=source_identity,
        label="deterministic-drum-stem-onsets",
        provider={
            "name": "librosa-onset-band-classifier",
            "version": version,
            "kind": "deterministic_dsp_proposal",
            "configuration": {"sample_rate": int(sample_rate), "duration_seconds": float(duration_seconds)},
        },
    )
