from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.analyze.decode import decoded_audio_sha256
from earcrate.midi.anatomy import midi_arrangement_anatomy, midi_validate_arrangement_anatomy
from earcrate.midi.codec import midi_write
from earcrate.midi.model import (
    MIDI_LEDGER_KIND,
    MIDI_LEDGER_SCHEMA_VERSION,
    midi_seal_ledger,
    midi_sha256_json,
    midi_statistics,
    midi_validate_ledger,
)
from earcrate.midi.render import midi_compile_note_spans
from earcrate.providers.notes import notes_sha256_file
from earcrate.study.reference_grid import (
    ReferenceBundleError,
    _reference_finite,
    _reference_source_duration,
    reference_validate_drum_observation,
    reference_validate_grid,
    reference_validate_note_observation,
)

REFERENCE_BUNDLE_SCHEMA_VERSION = 1
REFERENCE_BUNDLE_KIND = "earcrate_reference_bundle"


def _reference_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite reference bundle: {destination}")
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


def _reference_grid_times(grid: Mapping[str, Any]) -> list[float]:
    return [float(row["time_seconds"]) for row in grid["beats"]]


def _reference_time_to_tick(grid: Mapping[str, Any], time_seconds: float, ppq: int) -> float:
    times = _reference_grid_times(grid)
    time_value = float(time_seconds)
    if time_value <= times[0]:
        interval = times[1] - times[0]
        return (time_value - times[0]) / interval * ppq
    for index in range(len(times) - 1):
        if times[index] <= time_value < times[index + 1]:
            fraction = (time_value - times[index]) / (times[index + 1] - times[index])
            return (index + fraction) * ppq
    interval = times[-1] - times[-2]
    return (len(times) - 1 + (time_value - times[-1]) / interval) * ppq


def _reference_tick_to_time(grid: Mapping[str, Any], tick: float, ppq: int) -> float:
    times = _reference_grid_times(grid)
    beat_value = float(tick) / ppq
    if beat_value <= 0:
        return times[0] + beat_value * (times[1] - times[0])
    index = int(math.floor(beat_value))
    fraction = beat_value - index
    if index >= len(times) - 1:
        return times[-1] + (beat_value - (len(times) - 1)) * (times[-1] - times[-2])
    return times[index] + fraction * (times[index + 1] - times[index])


def _reference_quantize_time(
    grid: Mapping[str, Any],
    time_seconds: float,
    *,
    ppq: int,
    subdivisions: int,
) -> dict[str, Any]:
    raw_tick = _reference_time_to_tick(grid, time_seconds, ppq)
    step = ppq // subdivisions
    quantized_tick = max(0, int(round(raw_tick / step) * step))
    quantized_seconds = _reference_tick_to_time(grid, quantized_tick, ppq)
    return {
        "raw_tick": round(raw_tick, 12),
        "quantized_tick": quantized_tick,
        "raw_seconds": round(float(time_seconds), 12),
        "quantized_seconds": round(quantized_seconds, 12),
        "error_seconds": round(quantized_seconds - float(time_seconds), 12),
        "error_ticks": round(quantized_tick - raw_tick, 12),
    }


def _reference_tempo_events(grid: Mapping[str, Any], ppq: int) -> list[dict[str, Any]]:
    times = _reference_grid_times(grid)
    rows = []
    previous = None
    for index in range(len(times) - 1):
        tempo = max(1, int(round((times[index + 1] - times[index]) * 1_000_000.0)))
        if tempo == previous:
            continue
        rows.append({"tick": index * ppq, "tempo": tempo})
        previous = tempo
    return rows


def _reference_message_priority(message: Mapping[str, Any], is_meta: bool) -> int:
    typ = str(message.get("type") or "")
    if is_meta and typ == "track_name":
        return 0
    if typ == "program_change":
        return 1
    if typ in {"control_change", "pitchwheel"}:
        return 2
    if typ == "note_off" or (typ == "note_on" and int(message.get("velocity") or 0) == 0):
        return 3
    if typ == "note_on":
        return 4
    if is_meta and typ == "end_of_track":
        return 9
    return 5


def _reference_track_events(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        [deepcopy(dict(row)) for row in raw],
        key=lambda row: (
            int(row["tick"]),
            _reference_message_priority(row["message"], bool(row["is_meta"])),
            midi_sha256_json(row["message"]),
        ),
    )
    out = []
    for order, row in enumerate(ordered):
        event = {
            "tick": int(row["tick"]),
            "order": order,
            "is_meta": bool(row["is_meta"]),
            "message": deepcopy(dict(row["message"])),
        }
        for marker in ("_decision_id", "_note_off_decision_id", "_bend_decision_id"):
            if row.get(marker):
                event[marker] = str(row[marker])
        out.append(event)
    return out


def _reference_channels(track_specs: Sequence[Mapping[str, Any]], have_drums: bool) -> dict[str, int]:
    ordered = sorted(
        [spec for spec in track_specs],
        key=lambda row: (str(row.get("role") or ""), str(row.get("name") or ""), str(row.get("track_id") or "")),
    )
    available = [channel for channel in range(16) if channel != 9]
    if len(ordered) > len(available):
        raise ReferenceBundleError("reference reconstruction exceeds the 15 pitched MIDI channels available beside drums")
    mapping = {str(spec["track_id"]): available[index] for index, spec in enumerate(ordered)}
    if have_drums:
        mapping["__drums__"] = 9
    return mapping


def _reference_overlap(notes: Sequence[Mapping[str, Any]]) -> bool:
    active_end = -1
    for row in sorted(notes, key=lambda value: (int(value["start_tick"]), int(value["end_tick"]), str(value["decision_id"]))):
        if int(row["start_tick"]) < active_end:
            return True
        active_end = max(active_end, int(row["end_tick"]))
    return False


def _reference_pitchwheel_value(bend_units: int, units_per_semitone: float, range_semitones: float) -> tuple[int, bool]:
    semitones = float(bend_units) / float(units_per_semitone)
    raw = int(round(semitones / float(range_semitones) * 8192.0))
    clipped = raw < -8192 or raw > 8191
    return max(-8192, min(8191, raw)), clipped


def _reference_bundle_payload(bundle: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(bundle))
    value.pop("bundle_sha256", None)
    source = value.get("source") or {}
    source.pop("path", None)
    for track in value.get("observation_receipts") or []:
        track.pop("source_path", None)
    return value


def _reference_note_bend_units(track: Mapping[str, Any], observation: Mapping[str, Any], default: float) -> float:
    value = track.get("pitch_bend_units_per_semitone")
    if value in {None, ""}:
        value = (observation.get("config") or {}).get("pitch_bend_units_per_semitone", default)
    units = _reference_finite(value, "pitch_bend_units_per_semitone")
    if units <= 0:
        raise ReferenceBundleError("pitch_bend_units_per_semitone must be positive")
    return units


def reference_compile_bundle(
    audio_path: str | Path,
    grid: Mapping[str, Any],
    track_specs: Sequence[Mapping[str, Any]],
    *,
    drum_observation: Mapping[str, Any] | None = None,
    ppq: int = 480,
    quantization_subdivisions: int = 4,
    maximum_quantization_error_seconds: float = 0.080,
    sample_rate: int = 44_100,
    default_pitch_bend_units_per_semitone: float = 3.0,
    pitch_bend_range_semitones: float = 2.0,
) -> dict[str, Any]:
    """Align accepted local observations to one authored beat grid and exact MIDI ledger."""
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"reference audio does not exist: {source}")
    reference_validate_grid(grid, require_accepted=True)
    if ppq <= 0 or quantization_subdivisions <= 0 or ppq % quantization_subdivisions:
        raise ReferenceBundleError("ppq must be positive and divisible by quantization_subdivisions")
    if maximum_quantization_error_seconds < 0:
        raise ReferenceBundleError("maximum_quantization_error_seconds cannot be negative")
    if sample_rate <= 0 or default_pitch_bend_units_per_semitone <= 0 or pitch_bend_range_semitones <= 0:
        raise ReferenceBundleError("sample and pitch-bend configuration must be positive")
    source_duration = _reference_source_duration(source)
    if _reference_grid_times(grid)[-1] > source_duration + 0.250:
        raise ReferenceBundleError("accepted beat grid extends beyond the reference audio duration")
    source_byte_sha256 = notes_sha256_file(source)
    source_pcm_sha256 = decoded_audio_sha256(source, sample_rate, source_duration)
    if str(grid.get("source_pcm_sha256") or "") and str(grid["source_pcm_sha256"]) != source_pcm_sha256:
        raise ReferenceBundleError("accepted beat grid belongs to another decoded reference source")
    grid_value = deepcopy(dict(grid))
    if not str(grid_value.get("source_pcm_sha256") or ""):
        grid_value["source_binding_parent_grid_sha256"] = str(grid_value["grid_sha256"])
        grid_value["source_pcm_sha256"] = source_pcm_sha256
        grid_value["grid_sha256"] = midi_sha256_json({key: value for key, value in grid_value.items() if key != "grid_sha256"})
    reference_validate_grid(grid_value, require_accepted=True)

    normalized_tracks = []
    seen_track_ids = set()
    observation_receipts = []
    for raw in track_specs:
        track_id = str(raw.get("track_id") or "")
        if not track_id or track_id in seen_track_ids:
            raise ReferenceBundleError("reference track IDs must be unique and nonempty")
        seen_track_ids.add(track_id)
        observation = deepcopy(dict(raw.get("observation") or {}))
        reference_validate_note_observation(observation)
        normalized = {
            "track_id": track_id,
            "name": str(raw.get("name") or track_id),
            "role": str(raw.get("role") or "other"),
            "program": int(raw.get("program") or 0),
            "pitch_bend_units_per_semitone": _reference_note_bend_units(raw, observation, default_pitch_bend_units_per_semitone),
            "observation": observation,
        }
        if not 0 <= normalized["program"] <= 127:
            raise ReferenceBundleError("reference track program is outside MIDI range")
        normalized_tracks.append(normalized)
        observation_receipts.append(
            {
                "track_id": track_id,
                "name": normalized["name"],
                "role": normalized["role"],
                "provider": str(observation.get("provider") or ""),
                "provider_version": str(observation.get("provider_version") or ""),
                "model_sha256": str(observation.get("model_sha256") or ""),
                "source_identity": str(observation.get("source_identity") or ""),
                "source_path": str(observation.get("source_path") or ""),
                "observation_sha256": str(observation["observation_sha256"]),
                "note_count": int(observation["note_count"]),
                "pitch_bend_units_per_semitone": float(normalized["pitch_bend_units_per_semitone"]),
            }
        )
    normalized_tracks.sort(key=lambda row: (str(row["role"]), str(row["name"]), str(row["track_id"])))
    drums = deepcopy(dict(drum_observation)) if drum_observation is not None else None
    if drums is not None:
        reference_validate_drum_observation(drums)
        provider = drums.get("provider") or {}
        observation_receipts.append(
            {
                "track_id": "__drums__",
                "name": "Drums",
                "role": "drums",
                "provider": str(provider.get("name") or ""),
                "provider_version": str(provider.get("version") or ""),
                "model_sha256": "",
                "source_identity": str(drums.get("source_identity") or ""),
                "source_path": "",
                "observation_sha256": str(drums["observation_sha256"]),
                "note_count": int(drums["event_count"]),
                "pitch_bend_units_per_semitone": None,
            }
        )
    channels = _reference_channels(normalized_tracks, drums is not None and bool(drums.get("events")))
    decisions = []
    refusals = []
    quantized_by_track: dict[str, list[dict[str, Any]]] = {track["track_id"]: [] for track in normalized_tracks}

    for track in normalized_tracks:
        observation = track["observation"]
        for note in observation["notes"]:
            start = _reference_quantize_time(grid_value, float(note["start_s"]), ppq=ppq, subdivisions=quantization_subdivisions)
            end = _reference_quantize_time(grid_value, float(note["end_s"]), ppq=ppq, subdivisions=quantization_subdivisions)
            if int(end["quantized_tick"]) <= int(start["quantized_tick"]):
                end["quantized_tick"] = int(start["quantized_tick"]) + ppq // quantization_subdivisions
                end["quantized_seconds"] = round(_reference_tick_to_time(grid_value, end["quantized_tick"], ppq), 12)
                end["error_seconds"] = round(float(end["quantized_seconds"]) - float(note["end_s"]), 12)
                end["error_ticks"] = round(float(end["quantized_tick"]) - float(end["raw_tick"]), 12)
            decision_id = "reference_note_" + midi_sha256_json(
                {"track_id": track["track_id"], "observation_sha256": observation["observation_sha256"], "note_id": note["note_id"]}
            )[:24]
            reasons = []
            if abs(float(start["error_seconds"])) > maximum_quantization_error_seconds:
                reasons.append("start_quantization_error_exceeded")
            if abs(float(end["error_seconds"])) > maximum_quantization_error_seconds:
                reasons.append("end_quantization_error_exceeded")
            row = {
                "decision_id": decision_id,
                "kind": "note",
                "track_id": track["track_id"],
                "source_observation_sha256": observation["observation_sha256"],
                "source_event_id": str(note["note_id"]),
                "pitch_midi": int(note["pitch_midi"]),
                "velocity": int(note["velocity"]),
                "start": start,
                "end": end,
                "pitch_bends": deepcopy(note.get("pitch_bends") or []),
                "pitch_bend_units_per_semitone": float(track["pitch_bend_units_per_semitone"]),
                "accepted": not reasons,
                "reasons": reasons,
            }
            decisions.append(row)
            if reasons:
                refusals.append({"decision_id": decision_id, "track_id": track["track_id"], "source_event_id": note["note_id"], "reasons": reasons})
                continue
            quantized_by_track[track["track_id"]].append(
                {
                    "decision_id": decision_id,
                    "start_tick": int(start["quantized_tick"]),
                    "end_tick": int(end["quantized_tick"]),
                    "pitch_midi": int(note["pitch_midi"]),
                    "velocity": int(note["velocity"]),
                    "pitch_bends": deepcopy(note.get("pitch_bends") or []),
                    "pitch_bend_units_per_semitone": float(track["pitch_bend_units_per_semitone"]),
                }
            )

    for track in normalized_tracks:
        notes = quantized_by_track[track["track_id"]]
        bend_notes = [row for row in notes if row.get("pitch_bends")]
        if bend_notes and _reference_overlap(notes):
            bend_ids = {row["decision_id"] for row in bend_notes}
            for decision in decisions:
                if decision["decision_id"] in bend_ids and decision["accepted"]:
                    decision["accepted"] = False
                    decision["reasons"] = ["polyphonic_pitch_bend_requires_per_note_channels"]
                    refusals.append({"decision_id": decision["decision_id"], "track_id": track["track_id"], "source_event_id": decision["source_event_id"], "reasons": decision["reasons"]})
            quantized_by_track[track["track_id"]] = [row for row in notes if row["decision_id"] not in bend_ids]

    drum_rows = []
    if drums is not None:
        for event in drums["events"]:
            start = _reference_quantize_time(grid_value, float(event["time_seconds"]), ppq=ppq, subdivisions=quantization_subdivisions)
            decision_id = "reference_drum_" + midi_sha256_json({"observation_sha256": drums["observation_sha256"], "event_id": event["event_id"]})[:24]
            reasons = []
            if abs(float(start["error_seconds"])) > maximum_quantization_error_seconds:
                reasons.append("trigger_quantization_error_exceeded")
            row = {
                "decision_id": decision_id,
                "kind": "drum",
                "track_id": "__drums__",
                "source_observation_sha256": drums["observation_sha256"],
                "source_event_id": str(event["event_id"]),
                "pitch_midi": int(event["note"]),
                "velocity": int(event["velocity"]),
                "start": start,
                "duration_beats": float(event["duration_beats"]),
                "role": str(event.get("role") or "drum"),
                "confidence": float(event.get("confidence") or 0.0),
                "accepted": not reasons,
                "reasons": reasons,
            }
            decisions.append(row)
            if reasons:
                refusals.append({"decision_id": decision_id, "track_id": "__drums__", "source_event_id": event["event_id"], "reasons": reasons})
                continue
            drum_rows.append(
                {
                    "decision_id": decision_id,
                    "start_tick": int(start["quantized_tick"]),
                    "end_tick": int(start["quantized_tick"]) + max(1, int(round(float(event["duration_beats"]) * ppq))),
                    "pitch_midi": int(event["note"]),
                    "velocity": int(event["velocity"]),
                    "pitch_bends": [],
                }
            )

    raw_by_track: dict[str, list[dict[str, Any]]] = {track["track_id"]: [] for track in normalized_tracks}
    bend_provenance = []
    for track in normalized_tracks:
        channel = channels[track["track_id"]]
        for row in quantized_by_track[track["track_id"]]:
            raw_by_track[track["track_id"]].extend(
                [
                    {
                        "tick": row["start_tick"],
                        "is_meta": False,
                        "_decision_id": row["decision_id"],
                        "message": {"type": "note_on", "channel": channel, "note": row["pitch_midi"], "velocity": row["velocity"]},
                    },
                    {
                        "tick": row["end_tick"],
                        "is_meta": False,
                        "_note_off_decision_id": row["decision_id"],
                        "message": {"type": "note_off", "channel": channel, "note": row["pitch_midi"], "velocity": 0},
                    },
                ]
            )
            bends = row.get("pitch_bends") or []
            if bends:
                for index, bend in enumerate(bends):
                    fraction = index / max(1, len(bends) - 1)
                    tick = int(round(row["start_tick"] + fraction * (row["end_tick"] - row["start_tick"])))
                    pitch, clipped = _reference_pitchwheel_value(
                        int(bend),
                        float(row["pitch_bend_units_per_semitone"]),
                        pitch_bend_range_semitones,
                    )
                    bend_id = "reference_bend_" + midi_sha256_json({"decision_id": row["decision_id"], "index": index, "tick": tick, "pitch": pitch})[:24]
                    raw_by_track[track["track_id"]].append(
                        {
                            "tick": tick,
                            "is_meta": False,
                            "_bend_decision_id": bend_id,
                            "message": {"type": "pitchwheel", "channel": channel, "pitch": pitch},
                        }
                    )
                    bend_provenance.append({"bend_decision_id": bend_id, "note_decision_id": row["decision_id"], "track_id": track["track_id"], "tick": tick, "pitch": pitch, "source_bend_units": int(bend), "clipped": clipped})
                reset_id = "reference_bend_" + midi_sha256_json({"decision_id": row["decision_id"], "reset": True, "tick": row["end_tick"]})[:24]
                raw_by_track[track["track_id"]].append(
                    {
                        "tick": row["end_tick"],
                        "is_meta": False,
                        "_bend_decision_id": reset_id,
                        "message": {"type": "pitchwheel", "channel": channel, "pitch": 0},
                    }
                )
                bend_provenance.append({"bend_decision_id": reset_id, "note_decision_id": row["decision_id"], "track_id": track["track_id"], "tick": row["end_tick"], "pitch": 0, "source_bend_units": 0, "clipped": False})
    if drum_rows:
        raw_by_track["__drums__"] = []
        for row in drum_rows:
            raw_by_track["__drums__"].extend(
                [
                    {
                        "tick": row["start_tick"],
                        "is_meta": False,
                        "_decision_id": row["decision_id"],
                        "message": {"type": "note_on", "channel": 9, "note": row["pitch_midi"], "velocity": row["velocity"]},
                    },
                    {
                        "tick": row["end_tick"],
                        "is_meta": False,
                        "_note_off_decision_id": row["decision_id"],
                        "message": {"type": "note_off", "channel": 9, "note": row["pitch_midi"], "velocity": 0},
                    },
                ]
            )

    accepted_rows = [row for rows in quantized_by_track.values() for row in rows] + drum_rows
    end_tick = max([row["end_tick"] for row in accepted_rows] + [(len(grid_value["beats"]) - 1) * ppq])
    conductor_raw = [
        {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": "Reference Conductor"}},
        {
            "tick": 0,
            "is_meta": True,
            "message": {
                "type": "time_signature",
                "numerator": int(grid_value["meter"]["numerator"]),
                "denominator": int(grid_value["meter"]["denominator"]),
                "clocks_per_click": 24,
                "notated_32nd_notes_per_beat": 8,
            },
        },
        *[
            {"tick": row["tick"], "is_meta": True, "message": {"type": "set_tempo", "tempo": row["tempo"]}}
            for row in _reference_tempo_events(grid_value, ppq)
        ],
        {"tick": end_tick, "is_meta": True, "message": {"type": "end_of_track"}},
    ]
    tracks = [{"track_index": 0, "name": "Reference Conductor", "events": _reference_track_events(conductor_raw)}]
    note_on_locators = {}
    note_off_locators = {}
    bend_locators = {}
    output_order = [track["track_id"] for track in normalized_tracks if raw_by_track[track["track_id"]]]
    if drum_rows:
        output_order.append("__drums__")
    specs = {track["track_id"]: track for track in normalized_tracks}
    for track_id in output_order:
        if track_id == "__drums__":
            name, program, channel = "Drums", 0, 9
        else:
            name = str(specs[track_id]["name"])
            program = int(specs[track_id]["program"])
            channel = int(channels[track_id])
        raw = [
            {"tick": 0, "is_meta": True, "message": {"type": "track_name", "name": name}},
            {"tick": 0, "is_meta": False, "message": {"type": "program_change", "channel": channel, "program": program}},
            *raw_by_track[track_id],
            {"tick": end_tick, "is_meta": True, "message": {"type": "end_of_track"}},
        ]
        track_index = len(tracks)
        events = _reference_track_events(raw)
        for event in events:
            decision_id = event.pop("_decision_id", None)
            note_off_id = event.pop("_note_off_decision_id", None)
            bend_id = event.pop("_bend_decision_id", None)
            locator = {"track_index": track_index, "order": int(event["order"]), "tick": int(event["tick"]), "message": deepcopy(dict(event["message"]))}
            if decision_id:
                note_on_locators[str(decision_id)] = locator
            if note_off_id:
                note_off_locators[str(note_off_id)] = locator
            if bend_id:
                bend_locators[str(bend_id)] = locator
        tracks.append({"track_index": track_index, "name": name, "events": events})

    ledger = midi_seal_ledger(
        {
            "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
            "kind": MIDI_LEDGER_KIND,
            "midi_type": 1,
            "ticks_per_beat": int(ppq),
            "tracks": tracks,
        }
    )
    compiled = midi_compile_note_spans(ledger)
    spans = {str(span["event_id"]): span for span in compiled["note_spans"]}
    accepted_decisions = {str(row["decision_id"]): row for row in decisions if row["accepted"]}
    note_execution = []
    for decision_id in sorted(accepted_decisions):
        decision = accepted_decisions[decision_id]
        locator = note_on_locators.get(decision_id)
        note_off = note_off_locators.get(decision_id)
        if locator is None or note_off is None:
            raise ReferenceBundleError(f"accepted observation has no exact MIDI locator: {decision_id}")
        message = locator["message"]
        digest = hashlib.sha256(
            f"{ledger['semantic_sha256']}:{locator['track_index']}:{locator['order']}:{locator['tick']}:{message['channel']}:{message['note']}".encode("utf-8")
        ).hexdigest()[:24]
        output_event_id = "mnote_" + digest
        span = spans.get(output_event_id)
        if span is None:
            raise ReferenceBundleError(f"accepted observation is missing from exact note spans: {decision_id}")
        note_execution.append(
            {
                "decision_id": decision_id,
                "source_event_id": decision["source_event_id"],
                "track_id": decision["track_id"],
                "output_event_id": output_event_id,
                "output_note_off_event_id": "mnoteoff_" + midi_sha256_json({"semantic_sha256": ledger["semantic_sha256"], **note_off})[:24],
                "output_track_index": int(locator["track_index"]),
                "output_channel": int(message["channel"]),
                "start_tick": int(span["start_tick"]),
                "message_end_tick": int(note_off["tick"]),
                "sounding_end_tick": int(span["end_tick"]),
                "end_reason": str(span["end_reason"]),
                "note": int(span["note"]),
                "velocity": int(span["velocity"]),
            }
        )
    if {row["output_event_id"] for row in note_execution} != set(spans):
        raise ReferenceBundleError("reference note execution does not account for every output note span")
    for row in bend_provenance:
        locator = bend_locators.get(str(row["bend_decision_id"]))
        if locator is None:
            raise ReferenceBundleError(f"pitch-bend decision is missing from output MIDI: {row['bend_decision_id']}")
        row["output_event_id"] = "mbend_" + midi_sha256_json({"semantic_sha256": ledger["semantic_sha256"], **locator})[:24]
        row["output_track_index"] = int(locator["track_index"])
        row["output_event_order"] = int(locator["order"])

    anatomy = midi_arrangement_anatomy(ledger)
    stats = midi_statistics(ledger)
    complete = not refusals and len(note_execution) == len(accepted_decisions)
    bundle = {
        "schema_version": REFERENCE_BUNDLE_SCHEMA_VERSION,
        "kind": REFERENCE_BUNDLE_KIND,
        "source": {
            "path": str(source),
            "byte_sha256": source_byte_sha256,
            "pcm_sha256": source_pcm_sha256,
            "sample_rate": int(sample_rate),
            "duration_seconds": round(source_duration, 9),
        },
        "grid": grid_value,
        "configuration": {
            "ppq": int(ppq),
            "quantization_subdivisions": int(quantization_subdivisions),
            "maximum_quantization_error_seconds": float(maximum_quantization_error_seconds),
            "sample_rate": int(sample_rate),
            "default_pitch_bend_units_per_semitone": float(default_pitch_bend_units_per_semitone),
            "pitch_bend_range_semitones": float(pitch_bend_range_semitones),
        },
        "observation_receipts": sorted(observation_receipts, key=lambda row: str(row["track_id"])),
        "decision_count": len(decisions),
        "accepted_decision_count": sum(bool(row["accepted"]) for row in decisions),
        "refused_decision_count": len(refusals),
        "complete": complete,
        "decisions": sorted(decisions, key=lambda row: (str(row["track_id"]), str(row["decision_id"]))),
        "refusals": sorted(refusals, key=lambda row: (str(row["track_id"]), str(row["decision_id"]))),
        "note_execution": sorted(note_execution, key=lambda row: (int(row["start_tick"]), str(row["track_id"]), str(row["decision_id"]))),
        "pitch_bend_execution": sorted(bend_provenance, key=lambda row: (int(row["tick"]), str(row["track_id"]), str(row["bend_decision_id"]))),
        "midi_semantic_sha256": str(ledger["semantic_sha256"]),
        "midi_statistics": stats,
        "midi_ledger": ledger,
        "anatomy_sha256": str(anatomy["anatomy_sha256"]),
        "structural_sha256": str(anatomy["structural_sha256"]),
        "anatomy": anatomy,
    }
    bundle["bundle_sha256"] = midi_sha256_json(_reference_bundle_payload(bundle))
    reference_validate_bundle(bundle)
    return bundle


def reference_validate_bundle(bundle: Mapping[str, Any]) -> None:
    if int(bundle.get("schema_version") or 0) != REFERENCE_BUNDLE_SCHEMA_VERSION:
        raise ReferenceBundleError(f"unsupported reference-bundle schema: {bundle.get('schema_version')}")
    if str(bundle.get("kind") or "") != REFERENCE_BUNDLE_KIND:
        raise ReferenceBundleError(f"unsupported reference-bundle kind: {bundle.get('kind')}")
    reference_validate_grid(bundle.get("grid") or {}, require_accepted=True)
    ledger = bundle.get("midi_ledger") or {}
    midi_validate_ledger(ledger)
    anatomy = bundle.get("anatomy") or {}
    midi_validate_arrangement_anatomy(anatomy)
    if str(bundle.get("midi_semantic_sha256") or "") != str(ledger.get("semantic_sha256") or ""):
        raise ReferenceBundleError("bundle MIDI identity disagrees with its embedded ledger")
    if str(bundle.get("anatomy_sha256") or "") != str(anatomy.get("anatomy_sha256") or ""):
        raise ReferenceBundleError("bundle anatomy identity disagrees with its embedded anatomy")
    decisions = bundle.get("decisions")
    refusals = bundle.get("refusals")
    execution = bundle.get("note_execution")
    if not isinstance(decisions, list) or not isinstance(refusals, list) or not isinstance(execution, list):
        raise ReferenceBundleError("bundle decisions, refusals, and execution must be lists")
    decision_ids = [str(row.get("decision_id") or "") for row in decisions]
    if not all(decision_ids) or len(decision_ids) != len(set(decision_ids)):
        raise ReferenceBundleError("reference decision IDs must be unique and nonempty")
    accepted = {str(row["decision_id"]) for row in decisions if bool(row.get("accepted"))}
    refused = {str(row["decision_id"]) for row in refusals}
    executed = {str(row["decision_id"]) for row in execution}
    if accepted != executed:
        raise ReferenceBundleError("accepted reference decisions do not equal exact MIDI execution")
    if refused != {str(row["decision_id"]) for row in decisions if not bool(row.get("accepted"))}:
        raise ReferenceBundleError("reference refusals do not equal rejected decisions")
    complete = bool(bundle.get("complete"))
    if complete != (not refused and len(executed) == len(decisions)):
        raise ReferenceBundleError("reference bundle completeness disagrees with decisions")
    if int(bundle.get("decision_count") or 0) != len(decisions):
        raise ReferenceBundleError("reference decision_count disagrees with decisions")
    if int(bundle.get("accepted_decision_count") or 0) != len(accepted):
        raise ReferenceBundleError("reference accepted_decision_count disagrees with decisions")
    if int(bundle.get("refused_decision_count") or 0) != len(refused):
        raise ReferenceBundleError("reference refused_decision_count disagrees with refusals")
    compiled = midi_compile_note_spans(ledger)
    spans = {str(row["event_id"]): row for row in compiled["note_spans"]}
    execution_ids = {str(row["output_event_id"]) for row in execution}
    if execution_ids != set(spans):
        raise ReferenceBundleError("reference execution IDs do not equal exact MIDI note spans")
    for row in execution:
        span = spans[str(row["output_event_id"])]
        if (
            int(row["output_track_index"]) != int(span["track_index"])
            or int(row["output_channel"]) != int(span["channel"])
            or int(row["start_tick"]) != int(span["start_tick"])
            or int(row["sounding_end_tick"]) != int(span["end_tick"])
            or int(row["note"]) != int(span["note"])
            or int(row["velocity"]) != int(span["velocity"])
            or str(row["end_reason"]) != str(span["end_reason"])
        ):
            raise ReferenceBundleError("reference execution row disagrees with exact MIDI span")
    expected = midi_sha256_json(_reference_bundle_payload(bundle))
    if str(bundle.get("bundle_sha256") or "") != expected:
        raise ReferenceBundleError("bundle_sha256 does not match reference contents")


def reference_verify_source(bundle: Mapping[str, Any], *, raise_on_error: bool = True) -> dict[str, Any]:
    reference_validate_bundle(bundle)
    source = Path(str((bundle.get("source") or {}).get("path") or "")).expanduser().resolve()
    failures = []
    if not source.is_file():
        failures.append("reference source is missing")
    else:
        expected_byte = str(bundle["source"]["byte_sha256"])
        actual_byte = notes_sha256_file(source)
        if actual_byte != expected_byte:
            failures.append("reference source byte identity changed")
        else:
            actual_pcm = decoded_audio_sha256(source, int(bundle["source"]["sample_rate"]), float(bundle["source"]["duration_seconds"]))
            if actual_pcm != str(bundle["source"]["pcm_sha256"]):
                failures.append("reference decoded PCM identity changed")
    receipt = {"ok": not failures, "path": str(source), "failures": failures}
    if failures and raise_on_error:
        raise ReferenceBundleError("; ".join(failures))
    return receipt


def reference_load_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    reference_validate_bundle(value)
    return value


def reference_write_bundle(
    bundle: Mapping[str, Any],
    bundle_path: str | Path,
    midi_path: str | Path,
    *,
    overwrite: bool = False,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    reference_validate_bundle(bundle)
    if not bool(bundle["complete"]) and not allow_incomplete:
        raise ReferenceBundleError("refusing to publish an incomplete reference bundle")
    bundle_destination = Path(bundle_path).expanduser().resolve()
    midi_destination = Path(midi_path).expanduser().resolve()
    if bundle_destination == midi_destination:
        raise ReferenceBundleError("bundle and MIDI output paths must be distinct")
    if not overwrite:
        conflicts = [str(path) for path in (bundle_destination, midi_destination) if path.exists()]
        if conflicts:
            raise FileExistsError("refusing partial reference write because output path(s) already exist: " + ", ".join(conflicts))
    midi_receipt = midi_write(bundle["midi_ledger"], midi_destination, overwrite=overwrite)
    bundle_receipt = _reference_atomic_json(bundle_destination, bundle, overwrite=overwrite)
    return {
        "ok": True,
        "complete": bool(bundle["complete"]),
        "bundle_path": bundle_receipt["path"],
        "bundle_file_sha256": bundle_receipt["sha256"],
        "bundle_sha256": str(bundle["bundle_sha256"]),
        "midi": midi_receipt,
        "midi_semantic_sha256": str(bundle["midi_semantic_sha256"]),
        "anatomy_sha256": str(bundle["anatomy_sha256"]),
        "decision_count": int(bundle["decision_count"]),
        "refused_decision_count": int(bundle["refused_decision_count"]),
    }
