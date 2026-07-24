from __future__ import annotations

"""Content identities and validation for the cephalopod song reader.

The reader is deliberately observation-first.  It may preserve competing claims,
but no audible event may exist without exact decoded-PCM coordinates and an
observation chain back to those coordinates.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SONG_GENOME_SCHEMA = "earcrate/song-genome@1"
OBSERVATION_LEDGER_SCHEMA = "earcrate/observation-ledger@1"
READER_RECEIPT_SCHEMA = "earcrate/song-reader-receipt@1"


class ReaderError(ValueError):
    pass


def reader_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): reader_jsonable(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, (list, tuple)):
        return [reader_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return reader_jsonable(value.item())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ReaderError("reader data cannot contain NaN or infinity")
        return round(float(value), 12)
    return value


def reader_canonical_json(value: Any) -> str:
    return json.dumps(reader_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reader_sha256_json(value: Any) -> str:
    return hashlib.sha256(reader_canonical_json(value).encode("utf-8")).hexdigest()


def reader_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reader_observation_id(body_sha256: str, arm: str, kind: str, start_frame: int, end_frame: int, payload: Mapping[str, Any]) -> str:
    value = {
        "body_sha256": str(body_sha256),
        "arm": str(arm),
        "kind": str(kind),
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "payload": dict(payload),
    }
    return "obs_" + reader_sha256_json(value)[:24]


def reader_seal(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    out = deepcopy(dict(value))
    out.pop(field, None)
    out[field] = reader_sha256_json(out)
    return out


def reader_validate_observation_ledger(ledger: Mapping[str, Any]) -> None:
    if str(ledger.get("schema") or "") != OBSERVATION_LEDGER_SCHEMA:
        raise ReaderError("unsupported observation-ledger schema")
    body = ledger.get("body") or {}
    frames = int(body.get("frames") or 0)
    if frames <= 0 or int(body.get("sample_rate") or 0) <= 0:
        raise ReaderError("observation ledger requires a positive PCM body")
    rows = ledger.get("observations")
    if not isinstance(rows, list) or not rows:
        raise ReaderError("observation ledger requires observations")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReaderError("observations must be objects")
        observation_id = str(row.get("observation_id") or "")
        if not observation_id or observation_id in seen:
            raise ReaderError("observation IDs must be unique and nonempty")
        seen.add(observation_id)
        start = int(row.get("start_frame") or 0)
        end = int(row.get("end_frame") or 0)
        if start < 0 or end <= start or end > frames:
            raise ReaderError(f"observation {observation_id} lies outside the PCM body")
        if str(row.get("body_sha256") or "") != str(body.get("body_sha256") or ""):
            raise ReaderError(f"observation {observation_id} belongs to another PCM body")
    expected = reader_sha256_json({key: value for key, value in ledger.items() if key != "ledger_sha256"})
    if str(ledger.get("ledger_sha256") or "") != expected:
        raise ReaderError("observation ledger hash does not match its contents")


def reader_validate_genome(genome: Mapping[str, Any]) -> None:
    if str(genome.get("schema") or "") != SONG_GENOME_SCHEMA:
        raise ReaderError("unsupported SongGenome schema")
    body = genome.get("body") or {}
    if not str(body.get("body_sha256") or ""):
        raise ReaderError("SongGenome requires a decoded-PCM body identity")
    events = genome.get("canonical_events")
    instances = genome.get("instances")
    if not isinstance(events, list) or not isinstance(instances, list):
        raise ReaderError("SongGenome event collections must be lists")
    event_ids = [str(row.get("canonical_event_id") or "") for row in events]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise ReaderError("canonical event IDs must be unique and nonempty")
    event_set = set(event_ids)
    instance_ids: set[str] = set()
    observation_set = set(str(value) for value in genome.get("observation_ids") or [])
    for row in instances:
        instance_id = str(row.get("instance_id") or "")
        if not instance_id or instance_id in instance_ids:
            raise ReaderError("instance IDs must be unique and nonempty")
        instance_ids.add(instance_id)
        if str(row.get("canonical_event_id") or "") not in event_set:
            raise ReaderError(f"instance {instance_id} names an unknown canonical event")
        if not row.get("observation_ids"):
            raise ReaderError(f"instance {instance_id} has no acoustic observations")
        if not set(str(value) for value in row.get("observation_ids") or []).issubset(observation_set):
            raise ReaderError(f"instance {instance_id} cites unknown observations")
        start = int(row.get("start_frame") or 0)
        end = int(row.get("end_frame") or 0)
        if start < 0 or end <= start or end > int(body.get("frames") or 0):
            raise ReaderError(f"instance {instance_id} lies outside the PCM body")
    expected = reader_sha256_json({key: value for key, value in genome.items() if key != "genome_sha256"})
    if str(genome.get("genome_sha256") or "") != expected:
        raise ReaderError("SongGenome hash does not match its contents")


__all__ = [
    "SONG_GENOME_SCHEMA",
    "OBSERVATION_LEDGER_SCHEMA",
    "READER_RECEIPT_SCHEMA",
    "ReaderError",
    "reader_jsonable",
    "reader_canonical_json",
    "reader_sha256_json",
    "reader_sha256_file",
    "reader_observation_id",
    "reader_seal",
    "reader_validate_observation_ledger",
    "reader_validate_genome",
]
