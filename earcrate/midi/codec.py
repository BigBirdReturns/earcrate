from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from earcrate.midi.model import (
    MIDI_LEDGER_KIND,
    MIDI_LEDGER_SCHEMA_VERSION,
    MidiLedgerError,
    midi_compute_semantic_sha256,
    midi_first_semantic_difference,
    midi_jsonable,
    midi_seal_ledger,
    midi_semantic_payload,
    midi_statistics,
    midi_validate_ledger,
)


def midi_require_mido():
    try:
        import mido
    except Exception as exc:
        raise RuntimeError("MIDI support requires mido>=1.3,<2; install the EarCrate requirements") from exc
    return mido


def midi_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _midi_message_payload(message: Any) -> dict[str, Any]:
    try:
        payload = dict(message.dict())
    except Exception:
        payload = {"type": str(getattr(message, "type", "unknown"))}
        for key, value in vars(message).items():
            if key != "time" and not key.startswith("_"):
                payload[key] = value
        if getattr(message, "is_meta", False) and hasattr(message, "_type_byte"):
            payload = {
                "type": "unknown_meta",
                "type_byte": int(getattr(message, "_type_byte")),
                "data": list(getattr(message, "_data", ())),
            }
    payload.pop("time", None)
    return midi_jsonable(payload)


def _midi_message_from_payload(payload: Mapping[str, Any], is_meta: bool, delta: int) -> Any:
    mido = midi_require_mido()
    data = deepcopy(dict(payload))
    typ = str(data.get("type") or "")
    data["time"] = int(delta)
    if is_meta:
        if typ == "unknown_meta":
            try:
                from mido.midifiles.meta import UnknownMetaMessage

                return UnknownMetaMessage(
                    type_byte=int(data.get("type_byte") or 0),
                    data=tuple(int(value) for value in data.get("data") or []),
                    time=int(delta),
                )
            except Exception as exc:
                raise MidiLedgerError("cannot reconstruct unknown MIDI meta message") from exc
        return mido.MetaMessage.from_dict(data)
    return mido.Message.from_dict(data)


def midi_read(path: str | Path) -> dict[str, Any]:
    """Parse a Standard MIDI File into EarCrate's source-independent event ledger."""
    mido = midi_require_mido()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        midi_file = mido.MidiFile(str(source), clip=False)
    except TypeError:
        midi_file = mido.MidiFile(str(source))
    tracks: list[dict[str, Any]] = []
    for track_index, midi_track in enumerate(midi_file.tracks):
        absolute_tick = 0
        events: list[dict[str, Any]] = []
        track_name = ""
        for order, message in enumerate(midi_track):
            absolute_tick += int(message.time)
            payload = _midi_message_payload(message)
            if not track_name and bool(getattr(message, "is_meta", False)):
                if payload.get("type") == "track_name":
                    track_name = str(payload.get("name") or "")
                elif payload.get("type") == "text" and absolute_tick == 0:
                    track_name = str(payload.get("text") or "")
            events.append({
                "tick": absolute_tick,
                "order": order,
                "is_meta": bool(getattr(message, "is_meta", False)),
                "message": payload,
            })
        tracks.append({
            "track_index": track_index,
            "name": track_name or f"Track {track_index + 1}",
            "events": events,
        })
    ledger = {
        "schema_version": MIDI_LEDGER_SCHEMA_VERSION,
        "kind": MIDI_LEDGER_KIND,
        "midi_type": int(midi_file.type),
        "ticks_per_beat": int(midi_file.ticks_per_beat),
        "tracks": tracks,
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "byte_sha256": midi_sha256_file(source),
        },
    }
    return midi_seal_ledger(ledger)


def midi_write(ledger: Mapping[str, Any], path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Serialize a canonical ledger to a Standard MIDI File without silent event loss."""
    midi_validate_ledger(ledger)
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing MIDI file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    mido = midi_require_mido()
    midi_file = mido.MidiFile(type=int(ledger["midi_type"]), ticks_per_beat=int(ledger["ticks_per_beat"]))
    for track in ledger["tracks"]:
        midi_track = mido.MidiTrack()
        previous_tick = 0
        has_end = False
        for event in sorted(track["events"], key=lambda item: (int(item["tick"]), int(item["order"]))):
            tick = int(event["tick"])
            delta = tick - previous_tick
            if delta < 0:
                raise MidiLedgerError(f"negative delta while writing track {track['track_index']}")
            message = _midi_message_from_payload(event["message"], bool(event["is_meta"]), delta)
            midi_track.append(message)
            previous_tick = tick
            has_end = has_end or (bool(event["is_meta"]) and event["message"].get("type") == "end_of_track")
        if not has_end:
            midi_track.append(mido.MetaMessage("end_of_track", time=0))
        midi_file.tracks.append(midi_track)
    midi_file.save(str(destination))
    return {
        "ok": True,
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "byte_sha256": midi_sha256_file(destination),
        "semantic_sha256": str(ledger["semantic_sha256"]),
        "track_count": len(ledger["tracks"]),
    }


def midi_roundtrip(input_path: str | Path, output_path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    before = midi_read(input_path)
    write_receipt = midi_write(before, output_path, overwrite=overwrite)
    after = midi_read(output_path)
    equal = before["semantic_sha256"] == after["semantic_sha256"]
    receipt = {
        "ok": equal,
        "input": before.get("source"),
        "output": write_receipt,
        "semantic_sha256_before": before["semantic_sha256"],
        "semantic_sha256_after": after["semantic_sha256"],
        "statistics_before": midi_statistics(before),
        "statistics_after": midi_statistics(after),
    }
    if not equal:
        receipt["first_difference"] = midi_first_semantic_difference(
            midi_semantic_payload(before), midi_semantic_payload(after)
        )
        raise MidiLedgerError(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return receipt


def midi_write_ledger_json(ledger: Mapping[str, Any], path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    midi_validate_ledger(ledger)
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing ledger: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(midi_jsonable(dict(ledger)), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    destination.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "semantic_sha256": str(ledger["semantic_sha256"]),
    }


def midi_read_ledger_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    ledger = json.loads(source.read_text(encoding="utf-8"))
    midi_validate_ledger(ledger)
    if midi_compute_semantic_sha256(ledger) != ledger["semantic_sha256"]:
        raise MidiLedgerError("ledger semantic hash changed after JSON decoding")
    return ledger
