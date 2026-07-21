from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from earcrate.midi.codec import midi_read, midi_sha256_file
from earcrate.midi.model import MidiLedgerError, MidiTempoClock, midi_duration_seconds, midi_jsonable, midi_validate_ledger

MIDI_RENDER_SCHEMA_VERSION = 1
MIDI_RENDER_WAVEFORMS = {"sine", "triangle", "square"}


def _midi_kind(message: Mapping[str, Any]) -> str:
    typ = str(message.get("type") or "")
    if typ == "note_on" and int(message.get("velocity") or 0) == 0:
        return "note_off"
    return typ


def _midi_curve_append(
    curves: dict[tuple[int, int], dict[str, list[tuple[int, int]]]],
    key: tuple[int, int],
    name: str,
    tick: int,
    value: int,
) -> None:
    rows = curves[key][name]
    if rows and rows[-1][0] == tick:
        rows[-1] = (tick, value)
    else:
        rows.append((tick, value))


def midi_compile_note_spans(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Compile note lifetimes and channel curves without rasterizing tracks."""
    midi_validate_ledger(ledger)
    if int(ledger["midi_type"]) == 2:
        raise MidiLedgerError(
            "SMF type 2 contains asynchronous sequences; exact parse and round-trip are supported, "
            "but neutral rendering requires an explicit sequence selection"
        )

    global_max_tick = max(
        (int(event["tick"]) for track in ledger["tracks"] for event in track["events"]),
        default=0,
    )
    close_tick = global_max_tick + int(ledger["ticks_per_beat"])
    curves: dict[tuple[int, int], dict[str, list[tuple[int, int]]]] = defaultdict(
        lambda: {
            "pitchwheel": [(0, 0)],
            "volume": [(0, 100)],
            "expression": [(0, 127)],
            "pan": [(0, 64)],
        }
    )
    spans: list[dict[str, Any]] = []
    dangling_note_ons = 0
    unmatched_note_offs = 0
    sustained_releases = 0

    for track in ledger["tracks"]:
        track_index = int(track["track_index"])
        active: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        sustained: dict[int, list[dict[str, Any]]] = defaultdict(list)
        sustain_down: dict[int, bool] = defaultdict(bool)
        program: dict[int, int] = defaultdict(int)

        def finalize(note_state: dict[str, Any], end_tick: int, reason: str) -> None:
            nonlocal sustained_releases
            spans.append({
                "event_id": note_state["event_id"],
                "track_index": track_index,
                "track_name": str(track.get("name") or f"Track {track_index + 1}"),
                "channel": int(note_state["channel"]),
                "note": int(note_state["note"]),
                "velocity": int(note_state["velocity"]),
                "program": int(note_state["program"]),
                "start_tick": int(note_state["start_tick"]),
                "end_tick": max(int(end_tick), int(note_state["start_tick"]) + 1),
                "end_reason": reason,
            })
            if reason == "sustain_release":
                sustained_releases += 1

        for event in sorted(track["events"], key=lambda item: (int(item["tick"]), int(item["order"]))):
            tick = int(event["tick"])
            message = event["message"]
            kind = _midi_kind(message)
            if event["is_meta"]:
                continue
            channel = int(message.get("channel") or 0)
            curve_key = (track_index, channel)
            _ = curves[curve_key]
            if kind == "program_change":
                program[channel] = int(message.get("program") or 0)
            elif kind == "pitchwheel":
                _midi_curve_append(curves, curve_key, "pitchwheel", tick, int(message.get("pitch") or 0))
            elif kind == "control_change":
                control = int(message.get("control") or 0)
                value = int(message.get("value") or 0)
                if control == 7:
                    _midi_curve_append(curves, curve_key, "volume", tick, value)
                elif control == 10:
                    _midi_curve_append(curves, curve_key, "pan", tick, value)
                elif control == 11:
                    _midi_curve_append(curves, curve_key, "expression", tick, value)
                elif control == 64:
                    was_down = sustain_down[channel]
                    sustain_down[channel] = value >= 64
                    if was_down and not sustain_down[channel]:
                        for state in sustained.pop(channel, []):
                            finalize(state, tick, "sustain_release")
            elif kind == "note_on":
                note = int(message.get("note") or 0)
                digest = hashlib.sha256(
                    f"{ledger['semantic_sha256']}:{track_index}:{event['order']}:{tick}:{channel}:{note}".encode("utf-8")
                ).hexdigest()[:24]
                active[(channel, note)].append({
                    "event_id": f"mnote_{digest}",
                    "channel": channel,
                    "note": note,
                    "velocity": int(message.get("velocity") or 0),
                    "program": program[channel],
                    "start_tick": tick,
                })
            elif kind == "note_off":
                key = (channel, int(message.get("note") or 0))
                if not active[key]:
                    unmatched_note_offs += 1
                    continue
                state = active[key].pop(0)
                if sustain_down[channel]:
                    sustained[channel].append(state)
                else:
                    finalize(state, tick, "note_off")

        for states in active.values():
            for state in states:
                dangling_note_ons += 1
                finalize(state, close_tick, "end_of_file")
        for states in sustained.values():
            for state in states:
                finalize(state, close_tick, "end_of_file_sustain")

    spans.sort(
        key=lambda item: (
            int(item["start_tick"]),
            int(item["track_index"]),
            int(item["channel"]),
            int(item["note"]),
            str(item["event_id"]),
        )
    )
    serialized_curves = {
        f"{track_index}:{channel}": {
            name: [{"tick": tick, "value": value} for tick, value in rows]
            for name, rows in values.items()
        }
        for (track_index, channel), values in sorted(curves.items())
    }
    return {
        "schema_version": MIDI_RENDER_SCHEMA_VERSION,
        "semantic_sha256": ledger["semantic_sha256"],
        "note_spans": spans,
        "channel_curves": serialized_curves,
        "diagnostics": {
            "note_span_count": len(spans),
            "dangling_note_on_count": dangling_note_ons,
            "unmatched_note_off_count": unmatched_note_offs,
            "sustain_release_count": sustained_releases,
            "occupied_track_count": len({int(span["track_index"]) for span in spans}),
            "declared_track_count": len(ledger["tracks"]),
            "dangling_close_tick": close_tick,
        },
    }


def _midi_curve_from_compiled(
    compiled: Mapping[str, Any], track_index: int, channel: int, name: str
) -> list[tuple[int, int]]:
    channel_row = (compiled.get("channel_curves") or {}).get(f"{track_index}:{channel}") or {}
    return [(int(row["tick"]), int(row["value"])) for row in channel_row.get(name) or []]


def _midi_step_curve(
    rows: list[tuple[int, int]], clock: MidiTempoClock, sample_times: np.ndarray, default: int
) -> np.ndarray | float:
    if not rows:
        return float(default)
    times = np.asarray([clock.tick_to_seconds(tick) for tick, _value in rows], dtype=np.float64)
    values = np.asarray([value for _tick, value in rows], dtype=np.float64)
    if len(values) == 1:
        return float(values[0])
    indices = np.searchsorted(times, sample_times, side="right") - 1
    return values[np.clip(indices, 0, len(values) - 1)]


def _midi_waveform(phase: np.ndarray, waveform: str) -> np.ndarray:
    sine = np.sin(phase)
    if waveform == "sine":
        return sine
    if waveform == "triangle":
        return (2.0 / math.pi) * np.arcsin(sine)
    if waveform == "square":
        return np.where(sine >= 0.0, 1.0, -1.0)
    raise MidiLedgerError(f"unsupported neutral waveform: {waveform}")


def _midi_render_notes_into(
    target: np.ndarray,
    notes: list[Mapping[str, Any]],
    compiled: Mapping[str, Any],
    clock: MidiTempoClock,
    sample_rate: int,
    waveform: str,
    pitch_bend_range_semitones: float,
) -> dict[str, int]:
    rendered = 0
    truncated = 0
    total_frames = int(target.shape[0])
    for note_event in notes:
        start_seconds = clock.tick_to_seconds(int(note_event["start_tick"]))
        end_seconds = clock.tick_to_seconds(int(note_event["end_tick"]))
        start_frame = max(0, int(round(start_seconds * sample_rate)))
        end_frame = max(start_frame + 1, int(round(end_seconds * sample_rate)))
        if start_frame >= total_frames:
            truncated += 1
            continue
        if end_frame > total_frames:
            end_frame = total_frames
            truncated += 1
        count = end_frame - start_frame
        if count <= 0:
            continue

        sample_times = start_seconds + np.arange(count, dtype=np.float64) / float(sample_rate)
        track_index = int(note_event["track_index"])
        channel = int(note_event["channel"])
        pitch = _midi_step_curve(
            _midi_curve_from_compiled(compiled, track_index, channel, "pitchwheel"),
            clock,
            sample_times,
            0,
        )
        semitone_bend = np.asarray(pitch, dtype=np.float64) / 8192.0 * float(pitch_bend_range_semitones)
        base_frequency = 440.0 * 2.0 ** ((int(note_event["note"]) - 69) / 12.0)
        frequency = base_frequency * np.power(2.0, semitone_bend / 12.0)
        if np.ndim(frequency) == 0:
            phase = np.arange(count, dtype=np.float64) * (2.0 * math.pi * float(frequency) / sample_rate)
        else:
            phase = np.cumsum(np.asarray(frequency, dtype=np.float64)) * (2.0 * math.pi / sample_rate)
        seed = int(hashlib.sha256(str(note_event["event_id"]).encode("utf-8")).hexdigest()[:8], 16)
        phase += seed / 0xFFFFFFFF * 2.0 * math.pi
        mono = _midi_waveform(phase, waveform)

        volume = np.asarray(
            _midi_step_curve(
                _midi_curve_from_compiled(compiled, track_index, channel, "volume"),
                clock,
                sample_times,
                100,
            ),
            dtype=np.float64,
        ) / 127.0
        expression = np.asarray(
            _midi_step_curve(
                _midi_curve_from_compiled(compiled, track_index, channel, "expression"),
                clock,
                sample_times,
                127,
            ),
            dtype=np.float64,
        ) / 127.0
        velocity = (max(1, int(note_event["velocity"])) / 127.0) ** 1.35
        mono *= 0.20 * velocity * volume * expression

        attack = min(count // 2, max(1, int(round(0.005 * sample_rate))))
        release = min(count // 2, max(1, int(round(0.020 * sample_rate))))
        envelope = np.ones(count, dtype=np.float64)
        if attack > 1:
            envelope[:attack] *= np.linspace(0.0, 1.0, attack)
        if release > 1:
            envelope[-release:] *= np.linspace(1.0, 0.0, release)
        mono *= envelope

        pan = np.asarray(
            _midi_step_curve(
                _midi_curve_from_compiled(compiled, track_index, channel, "pan"),
                clock,
                sample_times,
                64,
            ),
            dtype=np.float64,
        )
        angle = np.clip(pan / 127.0, 0.0, 1.0) * math.pi / 2.0
        target[start_frame:end_frame, 0] += (mono * np.cos(angle)).astype(target.dtype, copy=False)
        target[start_frame:end_frame, 1] += (mono * np.sin(angle)).astype(target.dtype, copy=False)
        rendered += 1
    return {"rendered": rendered, "truncated": truncated}


def _midi_safe_stem_name(track_index: int, name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._") or f"track_{track_index + 1}"
    return f"{track_index:04d}_{safe}.wav"


def midi_render_ledger(
    ledger: Mapping[str, Any],
    output_path: str | Path,
    *,
    stems_dir: str | Path | None = None,
    sample_rate: int = 44_100,
    waveform: str = "sine",
    pitch_bend_range_semitones: float = 2.0,
    max_seconds: float = 0.0,
    target_peak: float = 0.92,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render type-0/1 MIDI with neutral tones; cost follows events and voices."""
    midi_validate_ledger(ledger)
    if sample_rate <= 0:
        raise MidiLedgerError("sample_rate must be positive")
    if waveform not in MIDI_RENDER_WAVEFORMS:
        raise MidiLedgerError(f"waveform must be one of {sorted(MIDI_RENDER_WAVEFORMS)}")
    if pitch_bend_range_semitones <= 0:
        raise MidiLedgerError("pitch_bend_range_semitones must be positive")
    if not 0.0 < target_peak <= 1.0:
        raise MidiLedgerError("target_peak must be in (0,1]")

    destination = Path(output_path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing render: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    compiled = midi_compile_note_spans(ledger)
    clock = MidiTempoClock(ledger)
    natural_duration = max(
        midi_duration_seconds(ledger),
        max((clock.tick_to_seconds(int(span["end_tick"])) for span in compiled["note_spans"]), default=0.0),
    ) + 0.05
    duration = min(natural_duration, float(max_seconds)) if max_seconds and max_seconds > 0 else natural_duration
    total_frames = max(1, int(math.ceil(duration * sample_rate)))
    master = np.zeros((total_frames, 2), dtype=np.float32)
    first_pass = _midi_render_notes_into(
        master,
        list(compiled["note_spans"]),
        compiled,
        clock,
        sample_rate,
        waveform,
        pitch_bend_range_semitones,
    )
    peak_before = float(np.max(np.abs(master))) if master.size else 0.0
    scale = min(1.0, target_peak / peak_before) if peak_before > 0 else 1.0
    master *= np.float32(scale)

    try:
        import soundfile as sf
    except Exception as exc:
        raise RuntimeError("neutral MIDI rendering requires soundfile") from exc
    sf.write(str(destination), master, sample_rate, subtype="FLOAT")

    stem_receipts: list[dict[str, Any]] = []
    occupied_tracks = sorted({int(span["track_index"]) for span in compiled["note_spans"]})
    if stems_dir is not None:
        stem_root = Path(stems_dir).expanduser().resolve()
        stem_root.mkdir(parents=True, exist_ok=True)
        names = {
            int(track["track_index"]): str(track.get("name") or f"Track {int(track['track_index']) + 1}")
            for track in ledger["tracks"]
        }
        for track_index in occupied_tracks:
            stem_path = stem_root / _midi_safe_stem_name(track_index, names[track_index])
            if stem_path.exists() and not overwrite:
                raise FileExistsError(f"refusing to overwrite existing stem: {stem_path}")
            track_audio = np.zeros((total_frames, 2), dtype=np.float32)
            track_notes = [span for span in compiled["note_spans"] if int(span["track_index"]) == track_index]
            counts = _midi_render_notes_into(
                track_audio,
                track_notes,
                compiled,
                clock,
                sample_rate,
                waveform,
                pitch_bend_range_semitones,
            )
            track_audio *= np.float32(scale)
            sf.write(str(stem_path), track_audio, sample_rate, subtype="FLOAT")
            stem_receipts.append({
                "track_index": track_index,
                "track_name": names[track_index],
                "path": str(stem_path),
                "sha256": midi_sha256_file(stem_path),
                "note_count": len(track_notes),
                **counts,
            })

    receipt = {
        "schema_version": MIDI_RENDER_SCHEMA_VERSION,
        "ok": True,
        "kind": "neutral_midi_render",
        "semantic_sha256": ledger["semantic_sha256"],
        "output_path": str(destination),
        "output_sha256": midi_sha256_file(destination),
        "sample_rate": int(sample_rate),
        "channels": 2,
        "frames": int(total_frames),
        "duration_seconds": round(total_frames / sample_rate, 9),
        "natural_duration_seconds": round(natural_duration, 9),
        "max_seconds": float(max_seconds),
        "waveform": waveform,
        "pitch_bend_range_semitones": float(pitch_bend_range_semitones),
        "target_peak": float(target_peak),
        "peak_before_scale": round(peak_before, 9),
        "applied_scale": round(scale, 12),
        "declared_track_count": len(ledger["tracks"]),
        "rendered_track_count": len(occupied_tracks),
        "note_span_count": len(compiled["note_spans"]),
        "first_pass": first_pass,
        "compile_diagnostics": compiled["diagnostics"],
        "stems": stem_receipts,
    }
    receipt_path = destination.with_suffix(destination.suffix + ".render.json")
    if receipt_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing render receipt: {receipt_path}")
    receipt_path.write_text(
        json.dumps(midi_jsonable(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = midi_sha256_file(receipt_path)
    return receipt


def midi_render_file(input_path: str | Path, output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    return midi_render_ledger(midi_read(input_path), output_path, **kwargs)
