from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from earcrate.midi.render import _midi_curve_from_compiled, _midi_step_curve
from earcrate.rack.render import _db_gain, _event_extent_seconds, _looped_audio

# In package mode patch the implementation module. In the concatenated single-file
# build the import line is stripped and this later definition replaces the earlier
# global _render_event directly.
_rack_render_module = None
import earcrate.rack.render as _rack_render_module


def _curve_array(value: Any, count: int, *, dtype: Any = np.float64) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    if array.ndim == 0:
        return np.full(count, array.item(), dtype=dtype)
    array = np.ravel(array)
    if array.shape[0] != count:
        try:
            array = np.broadcast_to(array, (count,))
        except ValueError as exc:
            raise ValueError(f"control curve has {array.shape[0]} values for {count} render frames") from exc
    return np.asarray(array, dtype=dtype)


def _render_event(
    target: np.ndarray,
    event: Mapping[str, Any],
    program: Mapping[str, Any],
    clock: Any,
    zone_audio: np.ndarray,
    *,
    sample_rate: int,
    pitch_bend_range_semitones: float,
    record_outcome: bool,
) -> dict[str, Any]:
    total_frames = int(target.shape[0])
    start_seconds = clock.tick_to_seconds(int(event["start_tick"]))
    start_frame = max(0, int(round(start_seconds * sample_rate)))
    note_end_frame = max(
        start_frame + 1,
        int(round(clock.tick_to_seconds(int(event["end_tick"])) * sample_rate)),
    )
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
    available_count = (
        total_frames - start_frame
        if one_shot
        else min(note_end_frame, total_frames) - start_frame
    )
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
    pitchwheel = _curve_array(
        _midi_step_curve(
            _midi_curve_from_compiled(program, track_index, channel, "pitchwheel"),
            clock,
            sample_times,
            0,
        ),
        available_count,
    )
    bend = pitchwheel / 8192.0 * float(pitch_bend_range_semitones)
    semitones = (
        int(event["note"])
        - int(event["root_key"])
        + float(event.get("tune_cents") or 0.0) / 100.0
        + bend
    )
    source_rate = float(event["sample"]["sample_rate"])
    increments = source_rate / float(sample_rate) * np.power(2.0, semitones / 12.0)
    increments = _curve_array(increments, available_count)
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
        requested_end = (
            start_frame + playable_count
            if complete
            else int(
                round(
                    _event_extent_seconds(
                        event,
                        event,
                        clock,
                        pitch_bend_range_semitones,
                    )
                    * sample_rate
                )
            )
        )
    else:
        complete = playable_count == available_count and note_end_frame <= total_frames
        status = "executed" if complete else "truncated"
        reason = "" if complete else (
            "sample_exhausted_before_note_off"
            if playable_count < available_count
            else "render_extent_truncated_gate"
        )
        requested_end = note_end_frame

    positions = positions[:playable_count]
    rendered = _looped_audio(zone_audio, positions, loop)
    local_times = sample_times[:playable_count]
    volume = _curve_array(
        _midi_step_curve(
            _midi_curve_from_compiled(program, track_index, channel, "volume"),
            clock,
            local_times,
            100,
        ),
        playable_count,
    ) / 127.0
    expression = _curve_array(
        _midi_step_curve(
            _midi_curve_from_compiled(program, track_index, channel, "expression"),
            clock,
            local_times,
            127,
        ),
        playable_count,
    ) / 127.0
    velocity = (max(1, int(event["velocity"])) / 127.0) ** 1.35
    gain_curve = np.asarray(
        velocity
        * volume
        * expression
        * _db_gain(float(event.get("gain_db") or 0.0)),
        dtype=np.float32,
    )
    rendered *= gain_curve[:, None]

    attack = min(
        playable_count // 2,
        max(0, int(round(float(event.get("attack_ms") or 0.0) / 1000.0 * sample_rate))),
    )
    release = min(
        playable_count // 2,
        max(0, int(round(float(event.get("release_ms") or 0.0) / 1000.0 * sample_rate))),
    )
    envelope = np.ones(playable_count, dtype=np.float32)
    if attack > 1:
        envelope[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
    if release > 1:
        envelope[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    rendered *= envelope[:, None]

    channel_pan = _curve_array(
        _midi_step_curve(
            _midi_curve_from_compiled(program, track_index, channel, "pan"),
            clock,
            local_times,
            64,
        ),
        playable_count,
    )
    pan = np.clip(
        (channel_pan - 64.0) / 63.0 + float(event.get("zone_pan") or 0.0),
        -1.0,
        1.0,
    )
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


if _rack_render_module is not None:
    _rack_render_module._render_event = _render_event
    rack_compile_render_program = _rack_render_module.rack_compile_render_program
    rack_render_ledger = _rack_render_module.rack_render_ledger
