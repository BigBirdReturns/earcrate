from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from earcrate.midi.codec import midi_sha256_file
from earcrate.midi.model import MidiTempoClock, midi_duration_seconds, midi_sha256_json, midi_validate_ledger
from earcrate.midi.render import (
    _atomic_json,
    _atomic_wav,
    _midi_curve_from_compiled,
    _midi_safe_stem_name,
    _midi_step_curve,
    midi_compile_note_spans,
)
from earcrate.rack.binding import rack_validate_binding
from earcrate.rack.model import RackError, rack_validate_revision, rack_verify_sources

RACK_RENDER_SCHEMA_VERSION = 1
RACK_EXECUTION_SCHEMA_VERSION = 1


def _db_gain(value: float) -> float:
    return 10.0 ** (float(value) / 20.0)


def _load_zone_audio(zone: Mapping[str, Any]) -> np.ndarray:
    try:
        import soundfile as sf
    except Exception as exc:
        raise RackError("sample-rack rendering requires soundfile") from exc
    sample = zone["sample"]
    with sf.SoundFile(str(sample["path"]), mode="r") as handle:
        handle.seek(int(sample["start_frame"]))
        audio = handle.read(int(sample["slice_frames"]), dtype="float32", always_2d=True)
    audio = np.asarray(audio, dtype=np.float32, order="C")
    if audio.shape != (int(sample["slice_frames"]), int(sample["channels"])):
        raise RackError(f"zone {zone['zone_id']} decoded dimensions changed")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    if not np.isfinite(audio).all():
        raise RackError(f"zone {zone['zone_id']} contains non-finite PCM")
    return audio


def _hermite(audio: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Vectorized four-point Hermite interpolation for variable-rate sample playback."""
    if audio.shape[0] == 1:
        return np.repeat(audio[:1], len(positions), axis=0)
    pos = np.clip(np.asarray(positions, dtype=np.float64), 0.0, audio.shape[0] - 1.0)
    index = np.floor(pos).astype(np.int64)
    frac = (pos - index)[:, None]
    i0 = np.clip(index - 1, 0, audio.shape[0] - 1)
    i1 = np.clip(index, 0, audio.shape[0] - 1)
    i2 = np.clip(index + 1, 0, audio.shape[0] - 1)
    i3 = np.clip(index + 2, 0, audio.shape[0] - 1)
    y0, y1, y2, y3 = audio[i0], audio[i1], audio[i2], audio[i3]
    c0 = y1
    c1 = 0.5 * (y2 - y0)
    c2 = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3
    c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2)
    return np.asarray(((c3 * frac + c2) * frac + c1) * frac + c0, dtype=np.float32)


def _looped_audio(audio: np.ndarray, positions: np.ndarray, loop: Mapping[str, Any]) -> np.ndarray:
    if not bool(loop.get("enabled")):
        return _hermite(audio, positions)
    start = int(loop["start_frame"])
    end = int(loop["end_frame"])
    length = end - start
    mapped = np.asarray(positions, dtype=np.float64).copy()
    looping = mapped >= end
    mapped[looping] = start + np.mod(mapped[looping] - start, length)
    rendered = _hermite(audio, mapped)
    crossfade = int(loop.get("crossfade_frames") or 0)
    if crossfade <= 0:
        return rendered
    phase = start + np.mod(np.maximum(positions - start, 0.0), length)
    blend_mask = (positions >= start) & (phase >= end - crossfade)
    if not np.any(blend_mask):
        return rendered
    progress = np.clip((phase[blend_mask] - (end - crossfade)) / crossfade, 0.0, 1.0)
    incoming_positions = start + (phase[blend_mask] - (end - crossfade))
    incoming = _hermite(audio, incoming_positions)
    outgoing_gain = np.cos(progress * math.pi / 2.0)[:, None]
    incoming_gain = np.sin(progress * math.pi / 2.0)[:, None]
    rendered[blend_mask] = rendered[blend_mask] * outgoing_gain + incoming * incoming_gain
    return rendered


def _event_extent_seconds(
    span: Mapping[str, Any],
    zone: Mapping[str, Any],
    clock: MidiTempoClock,
    pitch_bend_range_semitones: float,
) -> float:
    start = clock.tick_to_seconds(int(span["start_tick"]))
    release = float(zone.get("release_ms") or 0.0) / 1000.0
    if str(zone["trigger_mode"]) != "one_shot":
        return clock.tick_to_seconds(int(span["end_tick"])) + release
    sample = zone["sample"]
    slowest_semitones = (
        int(span["note"])
        - int(zone["root_key"])
        + float(zone.get("tune_cents") or 0.0) / 100.0
        - float(pitch_bend_range_semitones)
    )
    slowest_ratio = max(1e-6, 2.0 ** (slowest_semitones / 12.0))
    return start + int(sample["slice_frames"]) / float(sample["sample_rate"]) / slowest_ratio + release


def rack_compile_render_program(
    ledger: Mapping[str, Any],
    binding: Mapping[str, Any],
    racks: Sequence[Mapping[str, Any]],
    *,
    sample_rate: int,
    pitch_bend_range_semitones: float,
) -> dict[str, Any]:
    midi_validate_ledger(ledger)
    rack_validate_binding(binding, ledger=ledger, racks=racks)
    if not bool(binding.get("complete")):
        raise RackError("rack rendering requires a complete binding plan")
    compiled = midi_compile_note_spans(ledger)
    spans = {str(span["event_id"]): span for span in compiled["note_spans"]}
    rack_map = {str(rack["rack_sha256"]): rack for rack in racks}
    events = []
    for row in binding["event_bindings"]:
        event_id = str(row["event_id"])
        span = spans.get(event_id)
        if span is None:
            raise RackError(f"binding references missing MIDI event {event_id}")
        rack = rack_map[str(row["rack_sha256"])]
        zone = next((zone for zone in rack["zones"] if str(zone["zone_id"]) == str(row["zone_id"])), None)
        if zone is None:
            raise RackError(f"binding references missing zone {row['zone_id']}")
        events.append(
            {
                **{key: span[key] for key in ("event_id", "track_index", "track_name", "channel", "note", "velocity", "program", "start_tick", "end_tick", "end_reason")},
                "slot_id": row["slot_id"],
                "rack_id": row["rack_id"],
                "rack_sha256": row["rack_sha256"],
                "zone_id": row["zone_id"],
                "trigger_mode": zone["trigger_mode"],
                "root_key": zone["root_key"],
                "tune_cents": zone["tune_cents"],
                "gain_db": zone["gain_db"],
                "zone_pan": zone["pan"],
                "attack_ms": zone["attack_ms"],
                "release_ms": zone["release_ms"],
                "loop": zone["loop"],
                "sample": zone["sample"],
            }
        )
    events.sort(key=lambda event: (int(event["start_tick"]), int(event["track_index"]), str(event["event_id"])))
    if len(events) != len(compiled["note_spans"]):
        raise RackError("render program does not account for every selected MIDI event")
    program = {
        "schema_version": RACK_RENDER_SCHEMA_VERSION,
        "kind": "rack_midi_render_program",
        "semantic_sha256": ledger["semantic_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "sample_rate": int(sample_rate),
        "pitch_bend_range_semitones": float(pitch_bend_range_semitones),
        "rack_revisions": list(binding["rack_revisions"]),
        "channel_curves": compiled["channel_curves"],
        "events": events,
        "compile_diagnostics": compiled["diagnostics"],
    }
    program["program_sha256"] = midi_sha256_json(program)
    return program


def _render_event(
    target: np.ndarray,
    event: Mapping[str, Any],
    program: Mapping[str, Any],
    clock: MidiTempoClock,
    zone_audio: np.ndarray,
    *,
    sample_rate: int,
    pitch_bend_range_semitones: float,
    record_outcome: bool,
) -> dict[str, Any]:
    total_frames = int(target.shape[0])
    start_seconds = clock.tick_to_seconds(int(event["start_tick"]))
    start_frame = max(0, int(round(start_seconds * sample_rate)))
    note_end_frame = max(start_frame + 1, int(round(clock.tick_to_seconds(int(event["end_tick"])) * sample_rate)))
    one_shot = str(event["trigger_mode"]) == "one_shot"
    if start_frame >= total_frames:
        return {
            "status": "refused",
            "reason": "starts_after_render_extent",
            "event_id": event["event_id"],
            "requested_start_frame": start_frame,
            "requested_end_frame": note_end_frame,
            "rendered_start_frame": None,
            "rendered_end_frame": None,
        }
    available_count = total_frames - start_frame if one_shot else min(note_end_frame, total_frames) - start_frame
    if available_count <= 0:
        return {
            "status": "refused",
            "reason": "empty_render_window",
            "event_id": event["event_id"],
            "requested_start_frame": start_frame,
            "requested_end_frame": note_end_frame,
            "rendered_start_frame": None,
            "rendered_end_frame": None,
        }

    sample_times = start_seconds + np.arange(available_count, dtype=np.float64) / float(sample_rate)
    track_index = int(event["track_index"])
    channel = int(event["channel"])
    pitchwheel = _midi_step_curve(
        _midi_curve_from_compiled(program, track_index, channel, "pitchwheel"),
        clock,
        sample_times,
        0,
    )
    bend = np.asarray(pitchwheel, dtype=np.float64) / 8192.0 * float(pitch_bend_range_semitones)
    semitones = (
        int(event["note"])
        - int(event["root_key"])
        + float(event.get("tune_cents") or 0.0) / 100.0
        + bend
    )
    source_rate = float(event["sample"]["sample_rate"])
    increments = source_rate / float(sample_rate) * np.power(2.0, semitones / 12.0)
    positions = np.zeros(available_count, dtype=np.float64)
    if available_count > 1:
        positions[1:] = np.cumsum(increments[:-1])

    loop = event["loop"]
    if bool(loop.get("enabled")):
        playable_count = available_count
        source_completed = False
    else:
        valid = positions <= zone_audio.shape[0] - 1.0
        invalid = np.flatnonzero(~valid)
        playable_count = int(invalid[0]) if invalid.size else available_count
        source_completed = bool(invalid.size)
    if playable_count <= 0:
        return {
            "status": "refused",
            "reason": "sample_has_no_playable_frames",
            "event_id": event["event_id"],
            "requested_start_frame": start_frame,
            "requested_end_frame": note_end_frame,
            "rendered_start_frame": None,
            "rendered_end_frame": None,
        }

    if one_shot:
        complete = source_completed
        status = "executed" if complete else "truncated"
        reason = "" if complete else "render_extent_truncated_one_shot"
        requested_end = start_frame + playable_count if complete else int(round(_event_extent_seconds(event, event, clock, pitch_bend_range_semitones) * sample_rate))
    else:
        complete = playable_count == available_count and note_end_frame <= total_frames
        status = "executed" if complete else "truncated"
        reason = "" if complete else ("sample_exhausted_before_note_off" if playable_count < available_count else "render_extent_truncated_gate")
        requested_end = note_end_frame

    positions = positions[:playable_count]
    rendered = _looped_audio(zone_audio, positions, loop)
    local_times = sample_times[:playable_count]
    volume = np.asarray(
        _midi_step_curve(_midi_curve_from_compiled(program, track_index, channel, "volume"), clock, local_times, 100),
        dtype=np.float64,
    ) / 127.0
    expression = np.asarray(
        _midi_step_curve(_midi_curve_from_compiled(program, track_index, channel, "expression"), clock, local_times, 127),
        dtype=np.float64,
    ) / 127.0
    velocity = (max(1, int(event["velocity"])) / 127.0) ** 1.35
    rendered *= np.asarray(velocity * volume * expression * _db_gain(float(event.get("gain_db") or 0.0)), dtype=np.float32)[:, None] if np.ndim(volume) else np.float32(velocity * float(volume) * float(expression) * _db_gain(float(event.get("gain_db") or 0.0)))

    attack = min(playable_count // 2, max(0, int(round(float(event.get("attack_ms") or 0.0) / 1000.0 * sample_rate))))
    release = min(playable_count // 2, max(0, int(round(float(event.get("release_ms") or 0.0) / 1000.0 * sample_rate))))
    envelope = np.ones(playable_count, dtype=np.float32)
    if attack > 1:
        envelope[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
    if release > 1:
        envelope[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    rendered *= envelope[:, None]

    channel_pan = np.asarray(
        _midi_step_curve(_midi_curve_from_compiled(program, track_index, channel, "pan"), clock, local_times, 64),
        dtype=np.float64,
    )
    pan = np.clip((channel_pan - 64.0) / 63.0 + float(event.get("zone_pan") or 0.0), -1.0, 1.0)
    if np.ndim(pan) == 0:
        rendered[:, 0] *= np.float32(min(1.0, 1.0 - float(pan)))
        rendered[:, 1] *= np.float32(min(1.0, 1.0 + float(pan)))
    else:
        rendered[:, 0] *= np.minimum(1.0, 1.0 - pan).astype(np.float32)
        rendered[:, 1] *= np.minimum(1.0, 1.0 + pan).astype(np.float32)

    end_frame = start_frame + playable_count
    target[start_frame:end_frame] += rendered
    outcome = {
        "event_id": event["event_id"],
        "status": status,
        "reason": reason,
        "slot_id": event["slot_id"],
        "rack_id": event["rack_id"],
        "rack_sha256": event["rack_sha256"],
        "zone_id": event["zone_id"],
        "source_slice_pcm_sha256": event["sample"]["slice_pcm_sha256"],
        "requested_start_frame": start_frame,
        "requested_end_frame": requested_end,
        "rendered_start_frame": start_frame,
        "rendered_end_frame": end_frame,
        "rendered_frame_count": playable_count,
    }
    return outcome if record_outcome else {"status": status}


def _render_pass(
    target: np.ndarray,
    events: list[Mapping[str, Any]],
    program: Mapping[str, Any],
    clock: MidiTempoClock,
    audio_cache: Mapping[tuple[str, str], np.ndarray],
    *,
    sample_rate: int,
    pitch_bend_range_semitones: float,
    record_outcomes: bool,
) -> list[dict[str, Any]]:
    outcomes = []
    for event in events:
        audio = audio_cache[(str(event["rack_sha256"]), str(event["zone_id"]))]
        outcomes.append(
            _render_event(
                target,
                event,
                program,
                clock,
                audio,
                sample_rate=sample_rate,
                pitch_bend_range_semitones=pitch_bend_range_semitones,
                record_outcome=record_outcomes,
            )
        )
    return outcomes


def rack_render_ledger(
    ledger: Mapping[str, Any],
    binding: Mapping[str, Any],
    racks: Sequence[Mapping[str, Any]],
    output_path: str | Path,
    *,
    stems_dir: str | Path | None = None,
    sample_rate: int = 44_100,
    max_seconds: float = 0.0,
    target_peak: float = 0.92,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Execute a complete binding plan through exact crate sample slices."""
    midi_validate_ledger(ledger)
    if sample_rate <= 0:
        raise RackError("sample_rate must be positive")
    if not 0.0 < target_peak <= 1.0:
        raise RackError("target_peak must be in (0,1]")
    normalized = [dict(rack) for rack in racks]
    for rack in normalized:
        rack_validate_revision(rack)
        rack_verify_sources(rack)
    rack_validate_binding(binding, ledger=ledger, racks=normalized)
    if not bool(binding.get("complete")):
        raise RackError("cannot render an incomplete rack binding")
    pitch_bend_range = float(binding["pitch_bend_range_semitones"])
    program = rack_compile_render_program(
        ledger,
        binding,
        normalized,
        sample_rate=sample_rate,
        pitch_bend_range_semitones=pitch_bend_range,
    )
    destination = Path(output_path).expanduser().resolve()
    receipt_path = destination.with_suffix(destination.suffix + ".rack.render.json")
    program_path = destination.with_suffix(destination.suffix + ".rack.program.json")
    execution_path = destination.with_suffix(destination.suffix + ".rack.execution.json")
    occupied_tracks = sorted({int(event["track_index"]) for event in program["events"]})
    names = {int(track["track_index"]): str(track.get("name") or f"Track {int(track['track_index']) + 1}") for track in ledger["tracks"]}
    stem_paths = {}
    if stems_dir is not None:
        root = Path(stems_dir).expanduser().resolve()
        stem_paths = {index: root / _midi_safe_stem_name(index, names[index]) for index in occupied_tracks}
    planned_paths = [destination, receipt_path, program_path, execution_path, *stem_paths.values()]
    if not overwrite:
        conflicts = [str(path) for path in planned_paths if path.exists()]
        if conflicts:
            raise FileExistsError("refusing partial rack render because output path(s) exist: " + ", ".join(conflicts))

    racks_by_sha = {str(rack["rack_sha256"]): rack for rack in normalized}
    audio_cache: dict[tuple[str, str], np.ndarray] = {}
    for event in program["events"]:
        key = (str(event["rack_sha256"]), str(event["zone_id"]))
        if key not in audio_cache:
            rack = racks_by_sha[key[0]]
            zone = next(zone for zone in rack["zones"] if str(zone["zone_id"]) == key[1])
            audio_cache[key] = _load_zone_audio(zone)

    clock = MidiTempoClock(ledger)
    natural_duration = max(
        midi_duration_seconds(ledger),
        max((_event_extent_seconds(event, event, clock, pitch_bend_range) for event in program["events"]), default=0.0),
    ) + 0.05
    duration = min(natural_duration, float(max_seconds)) if max_seconds and max_seconds > 0 else natural_duration
    total_frames = max(1, int(math.ceil(duration * sample_rate)))
    master = np.zeros((total_frames, 2), dtype=np.float32)
    outcomes = _render_pass(
        master,
        list(program["events"]),
        program,
        clock,
        audio_cache,
        sample_rate=sample_rate,
        pitch_bend_range_semitones=pitch_bend_range,
        record_outcomes=True,
    )
    if len(outcomes) != len(program["events"]):
        raise RackError("rack execution ledger does not account for every selected event")
    counts = {
        status: sum(str(outcome["status"]) == status for outcome in outcomes)
        for status in ("executed", "truncated", "refused")
    }
    complete = counts["truncated"] == 0 and counts["refused"] == 0
    if not max_seconds and not complete:
        failures = [f"{outcome['event_id']}:{outcome['reason']}" for outcome in outcomes if outcome["status"] != "executed"]
        raise RackError("full rack render failed: " + ", ".join(failures[:12]))

    peak_before = float(np.max(np.abs(master))) if master.size else 0.0
    scale = min(1.0, target_peak / peak_before) if peak_before > 0 else 1.0
    master *= np.float32(scale)
    try:
        import soundfile as sf
    except Exception as exc:
        raise RackError("sample-rack rendering requires soundfile") from exc
    _atomic_wav(destination, master, sample_rate, sf)
    _atomic_json(program_path, program)
    execution = {
        "schema_version": RACK_EXECUTION_SCHEMA_VERSION,
        "kind": "rack_midi_execution_ledger",
        "semantic_sha256": ledger["semantic_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "program_sha256": program["program_sha256"],
        "complete_execution": complete,
        "selected_event_count": len(program["events"]),
        "executed_event_count": counts["executed"],
        "truncated_event_count": counts["truncated"],
        "refused_event_count": counts["refused"],
        "events": outcomes,
    }
    execution["execution_sha256"] = midi_sha256_json(execution)
    _atomic_json(execution_path, execution)

    stem_receipts = []
    for track_index in occupied_tracks:
        if track_index not in stem_paths:
            continue
        track_audio = np.zeros_like(master)
        track_events = [event for event in program["events"] if int(event["track_index"]) == track_index]
        _render_pass(
            track_audio,
            track_events,
            program,
            clock,
            audio_cache,
            sample_rate=sample_rate,
            pitch_bend_range_semitones=pitch_bend_range,
            record_outcomes=False,
        )
        track_audio *= np.float32(scale)
        path = stem_paths[track_index]
        _atomic_wav(path, track_audio, sample_rate, sf)
        stem_receipts.append(
            {
                "track_index": track_index,
                "track_name": names[track_index],
                "path": str(path),
                "sha256": midi_sha256_file(path),
                "event_count": len(track_events),
            }
        )

    receipt = {
        "schema_version": RACK_RENDER_SCHEMA_VERSION,
        "kind": "rack_midi_render",
        "ok": True,
        "semantic_sha256": ledger["semantic_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "program_sha256": program["program_sha256"],
        "program_path": str(program_path),
        "program_file_sha256": midi_sha256_file(program_path),
        "execution_sha256": execution["execution_sha256"],
        "execution_path": str(execution_path),
        "execution_file_sha256": midi_sha256_file(execution_path),
        "complete_execution": complete,
        "selected_event_count": len(program["events"]),
        "executed_event_count": counts["executed"],
        "truncated_event_count": counts["truncated"],
        "refused_event_count": counts["refused"],
        "output_path": str(destination),
        "output_sha256": midi_sha256_file(destination),
        "sample_rate": sample_rate,
        "frames": total_frames,
        "duration_seconds": round(total_frames / sample_rate, 9),
        "natural_duration_seconds": round(natural_duration, 9),
        "max_seconds": float(max_seconds),
        "target_peak": float(target_peak),
        "peak_before_scale": round(peak_before, 9),
        "applied_scale": round(scale, 12),
        "declared_track_count": len(ledger["tracks"]),
        "rendered_track_count": len(occupied_tracks),
        "rack_revisions": list(binding["rack_revisions"]),
        "stems": stem_receipts,
    }
    _atomic_json(receipt_path, receipt)
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = midi_sha256_file(receipt_path)
    return receipt
