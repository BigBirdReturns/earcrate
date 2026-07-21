from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from typing import Any, Iterable, Mapping

MIDI_LEDGER_SCHEMA_VERSION = 1
MIDI_LEDGER_KIND = "earcrate_midi_ledger"
MIDI_DEFAULT_TEMPO_US_PER_BEAT = 500_000


class MidiLedgerError(ValueError):
    """Raised when a canonical EarCrate MIDI ledger is invalid."""


def midi_jsonable(value: Any) -> Any:
    """Convert Mido/native values into deterministic JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise MidiLedgerError("MIDI ledger cannot contain non-finite numbers")
        return value
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, Mapping):
        return {str(key): midi_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [midi_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return midi_jsonable(value.item())
    return str(value)


def midi_canonical_json(value: Any) -> str:
    return json.dumps(midi_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def midi_sha256_json(value: Any) -> str:
    return hashlib.sha256(midi_canonical_json(value).encode("utf-8")).hexdigest()


def midi_semantic_payload(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Return source-independent musical content for semantic round-trip identity.

    ``end_of_track`` is an SMF container terminator rather than a musical event.
    Its delta may contain arbitrary trailing padding, so it remains visible in the
    parsed ledger and file-extent receipt but is excluded from musical identity.
    """
    tracks = []
    for track in ledger.get("tracks") or []:
        tracks.append({
            "track_index": int(track.get("track_index") or 0),
            "name": str(track.get("name") or ""),
            "events": [
                midi_jsonable(event)
                for event in track.get("events") or []
                if not (bool(event.get("is_meta")) and (event.get("message") or {}).get("type") == "end_of_track")
            ],
        })
    return {
        "schema_version": int(ledger.get("schema_version") or 0),
        "kind": str(ledger.get("kind") or ""),
        "midi_type": int(ledger.get("midi_type") or 0),
        "ticks_per_beat": int(ledger.get("ticks_per_beat") or 0),
        "tracks": tracks,
    }


def midi_compute_semantic_sha256(ledger: Mapping[str, Any]) -> str:
    return midi_sha256_json(midi_semantic_payload(ledger))


def midi_seal_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(ledger))
    out.setdefault("schema_version", MIDI_LEDGER_SCHEMA_VERSION)
    out.setdefault("kind", MIDI_LEDGER_KIND)
    out["semantic_sha256"] = midi_compute_semantic_sha256(out)
    midi_validate_ledger(out, require_sealed=True)
    return out


def midi_iter_events(ledger: Mapping[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for track in ledger.get("tracks") or []:
        for event in track.get("events") or []:
            yield track, event


def midi_validate_ledger(ledger: Mapping[str, Any], require_sealed: bool = True) -> None:
    if int(ledger.get("schema_version") or 0) != MIDI_LEDGER_SCHEMA_VERSION:
        raise MidiLedgerError(f"unsupported MIDI ledger schema: {ledger.get('schema_version')}")
    if str(ledger.get("kind") or "") != MIDI_LEDGER_KIND:
        raise MidiLedgerError(f"unsupported MIDI ledger kind: {ledger.get('kind')}")
    midi_type = int(ledger.get("midi_type") or 0)
    if midi_type not in {0, 1, 2}:
        raise MidiLedgerError(f"unsupported Standard MIDI File type: {midi_type}")
    ticks_per_beat = int(ledger.get("ticks_per_beat") or 0)
    if ticks_per_beat <= 0:
        raise MidiLedgerError("ticks_per_beat must be positive")
    tracks = ledger.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise MidiLedgerError("MIDI ledger must contain at least one track")
    for expected_index, track in enumerate(tracks):
        if not isinstance(track, Mapping):
            raise MidiLedgerError(f"track {expected_index} is not an object")
        track_index = int(track.get("track_index", -1))
        if track_index != expected_index:
            raise MidiLedgerError(f"track index mismatch: expected {expected_index}, got {track_index}")
        events = track.get("events")
        if not isinstance(events, list):
            raise MidiLedgerError(f"track {expected_index} events must be a list")
        previous = (-1, -1)
        for event_index, event in enumerate(events):
            if not isinstance(event, Mapping):
                raise MidiLedgerError(f"track {expected_index} event {event_index} is not an object")
            tick = int(event.get("tick", -1))
            order = int(event.get("order", -1))
            if tick < 0 or order < 0:
                raise MidiLedgerError(f"track {expected_index} event {event_index} has a negative tick/order")
            if (tick, order) < previous:
                raise MidiLedgerError(f"track {expected_index} events are not ordered")
            previous = (tick, order)
            message = event.get("message")
            if not isinstance(message, Mapping) or not str(message.get("type") or ""):
                raise MidiLedgerError(f"track {expected_index} event {event_index} has no MIDI message type")
            if not isinstance(event.get("is_meta"), bool):
                raise MidiLedgerError(f"track {expected_index} event {event_index} is missing is_meta")
    if require_sealed:
        expected = midi_compute_semantic_sha256(ledger)
        actual = str(ledger.get("semantic_sha256") or "")
        if actual != expected:
            raise MidiLedgerError("semantic_sha256 does not match the MIDI ledger contents")


def midi_tempo_map(ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the effective piecewise tempo map, with deterministic same-tick precedence."""
    midi_validate_ledger(ledger)
    candidates: list[tuple[int, int, int, int]] = []
    for track, event in midi_iter_events(ledger):
        message = event["message"]
        if event["is_meta"] and message.get("type") == "set_tempo":
            tempo = int(message.get("tempo") or 0)
            if tempo <= 0:
                raise MidiLedgerError("set_tempo values must be positive")
            candidates.append((int(event["tick"]), int(track["track_index"]), int(event["order"]), tempo))
    candidates.sort()
    effective: dict[int, int] = {0: MIDI_DEFAULT_TEMPO_US_PER_BEAT}
    for tick, _track_index, _order, tempo in candidates:
        effective[tick] = tempo
    rows = []
    for tick in sorted(effective):
        tempo = effective[tick]
        rows.append({
            "tick": tick,
            "tempo_us_per_beat": tempo,
            "bpm": round(60_000_000.0 / tempo, 9),
        })
    return rows


class MidiTempoClock:
    """Piecewise tick/second conversion for one canonical ledger."""

    def __init__(self, ledger: Mapping[str, Any]):
        self.ticks_per_beat = int(ledger["ticks_per_beat"])
        rows = midi_tempo_map(ledger)
        self.ticks: list[int] = []
        self.tempos: list[int] = []
        self.seconds_at_tick: list[float] = []
        elapsed = 0.0
        previous_tick = 0
        previous_tempo = MIDI_DEFAULT_TEMPO_US_PER_BEAT
        for row in rows:
            tick = int(row["tick"])
            if tick > previous_tick:
                elapsed += (tick - previous_tick) * previous_tempo / 1_000_000.0 / self.ticks_per_beat
            self.ticks.append(tick)
            self.tempos.append(int(row["tempo_us_per_beat"]))
            self.seconds_at_tick.append(elapsed)
            previous_tick = tick
            previous_tempo = int(row["tempo_us_per_beat"])

    def tick_to_seconds(self, tick: int | float) -> float:
        tick_value = float(tick)
        if tick_value < 0:
            raise MidiLedgerError("tick must be nonnegative")
        lo = 0
        hi = len(self.ticks)
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self.ticks[mid] <= tick_value:
                lo = mid
            else:
                hi = mid
        base_tick = self.ticks[lo]
        base_seconds = self.seconds_at_tick[lo]
        tempo = self.tempos[lo]
        return base_seconds + (tick_value - base_tick) * tempo / 1_000_000.0 / self.ticks_per_beat


def midi_file_extent_ticks(ledger: Mapping[str, Any]) -> int:
    """Last encoded event tick, including a possibly padded end_of_track marker."""
    return max((int(event["tick"]) for _track, event in midi_iter_events(ledger)), default=0)


def midi_duration_ticks(ledger: Mapping[str, Any]) -> int:
    """Last musically meaningful event tick; end_of_track padding is ignored."""
    return max(
        (
            int(event["tick"])
            for _track, event in midi_iter_events(ledger)
            if not (bool(event.get("is_meta")) and (event.get("message") or {}).get("type") == "end_of_track")
        ),
        default=0,
    )


def midi_duration_seconds(ledger: Mapping[str, Any]) -> float:
    return MidiTempoClock(ledger).tick_to_seconds(midi_duration_ticks(ledger))


def _midi_event_kind(message: Mapping[str, Any]) -> str:
    typ = str(message.get("type") or "")
    if typ == "note_on" and int(message.get("velocity") or 0) == 0:
        return "note_off"
    return typ


def midi_statistics(ledger: Mapping[str, Any]) -> dict[str, Any]:
    midi_validate_ledger(ledger)
    counts: Counter[str] = Counter()
    channels: set[int] = set()
    track_summaries: list[dict[str, Any]] = []
    poly_events: list[tuple[int, int, int, int, int]] = []
    for track in ledger["tracks"]:
        track_counts: Counter[str] = Counter()
        track_channels: set[int] = set()
        programs: set[int] = set()
        for event in track["events"]:
            message = event["message"]
            kind = _midi_event_kind(message)
            counts[kind] += 1
            track_counts[kind] += 1
            if "channel" in message:
                channel = int(message["channel"])
                channels.add(channel)
                track_channels.add(channel)
            if kind == "program_change":
                programs.add(int(message.get("program") or 0))
            if kind == "note_on":
                poly_events.append((int(event["tick"]), 2, int(track["track_index"]), int(event["order"]), 1))
            elif kind == "note_off":
                poly_events.append((int(event["tick"]), 0, int(track["track_index"]), int(event["order"]), -1))
        track_summaries.append({
            "track_index": int(track["track_index"]),
            "name": str(track.get("name") or f"Track {int(track['track_index']) + 1}"),
            "event_count": len(track["events"]),
            "note_on_count": int(track_counts["note_on"]),
            "note_off_count": int(track_counts["note_off"]),
            "channels": sorted(track_channels),
            "programs": sorted(programs),
        })
    active = 0
    max_polyphony = 0
    unmatched_offs = 0
    for _tick, _priority, _track, _order, delta in sorted(poly_events):
        if delta < 0 and active <= 0:
            unmatched_offs += 1
        active = max(0, active + delta)
        max_polyphony = max(max_polyphony, active)
    note_tracks = [row["track_index"] for row in track_summaries if row["note_on_count"]]
    tempo = midi_tempo_map(ledger)
    clock = MidiTempoClock(ledger)
    return {
        "semantic_sha256": ledger["semantic_sha256"],
        "midi_type": int(ledger["midi_type"]),
        "ticks_per_beat": int(ledger["ticks_per_beat"]),
        "declared_track_count": len(ledger["tracks"]),
        "occupied_note_track_count": len(note_tracks),
        "occupied_note_tracks": note_tracks,
        "event_count": sum(len(track["events"]) for track in ledger["tracks"]),
        "note_on_count": int(counts["note_on"]),
        "note_off_count": int(counts["note_off"]),
        "control_change_count": int(counts["control_change"]),
        "pitchwheel_count": int(counts["pitchwheel"]),
        "program_change_count": int(counts["program_change"]),
        "tempo_event_count": int(counts["set_tempo"]),
        "time_signature_count": int(counts["time_signature"]),
        "sysex_count": int(counts["sysex"]),
        "channels": sorted(channels),
        "max_polyphony": int(max_polyphony),
        "unmatched_note_off_count": int(unmatched_offs),
        "duration_ticks": midi_duration_ticks(ledger),
        "duration_seconds": round(midi_duration_seconds(ledger), 9),
        "file_extent_ticks": midi_file_extent_ticks(ledger),
        "file_extent_seconds": round(clock.tick_to_seconds(midi_file_extent_ticks(ledger)), 9),
        "tempo_map": tempo,
        "tracks": track_summaries,
    }


def midi_first_semantic_difference(left: Any, right: Any, path: str = "$") -> dict[str, Any] | None:
    """Return the first deterministic semantic difference for a failed round trip."""
    if type(left) is not type(right):
        return {"path": path, "left": left, "right": right}
    if isinstance(left, Mapping):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left:
                return {"path": child, "left": None, "right": right[key]}
            if key not in right:
                return {"path": child, "left": left[key], "right": None}
            diff = midi_first_semantic_difference(left[key], right[key], child)
            if diff:
                return diff
        return None
    if isinstance(left, list):
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left):
                return {"path": child, "left": None, "right": right[index]}
            if index >= len(right):
                return {"path": child, "left": left[index], "right": None}
            diff = midi_first_semantic_difference(left[index], right[index], child)
            if diff:
                return diff
        return None
    return None if left == right else {"path": path, "left": left, "right": right}
