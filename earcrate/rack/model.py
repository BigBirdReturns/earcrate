from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from earcrate.midi.model import midi_jsonable, midi_sha256_json

RACK_SCHEMA_VERSION = 1
RACK_KIND = "earcrate_rack_revision"
RACK_MODES = {"pitched", "trigger", "hybrid"}
RACK_TRIGGER_MODES = {"gate", "one_shot"}


class RackError(ValueError):
    """Raised when a rack revision or bound sample is invalid."""


def rack_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RackError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise RackError(f"{label} must be finite")
    return number


def _range_pair(value: Any, label: str, minimum: int, maximum: int) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RackError(f"{label} must be [minimum, maximum]")
    lo, hi = int(value[0]), int(value[1])
    if lo < minimum or hi > maximum or hi < lo:
        raise RackError(f"{label} must satisfy {minimum} <= minimum <= maximum <= {maximum}")
    return [lo, hi]


def _normalize_tags(values: Any) -> list[str]:
    return sorted({str(value).strip().lower() for value in (values or []) if str(value).strip()})


def rack_sample_identity(
    path: str | Path,
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> dict[str, Any]:
    """Hash a source file and the exact decoded PCM slice a zone will play."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RackError(f"sample path is not a file: {source}")
    try:
        import soundfile as sf
    except Exception as exc:
        raise RackError("sample-rack sealing requires soundfile") from exc

    info = sf.info(str(source))
    total_frames = int(info.frames)
    sample_rate = int(info.samplerate)
    channels = int(info.channels)
    if sample_rate <= 0 or total_frames <= 0:
        raise RackError(f"sample has no decodable PCM: {source}")
    if channels not in {1, 2}:
        raise RackError(f"rack v1 supports mono or stereo samples, got {channels}: {source}")
    start = int(start_frame)
    stop = total_frames if end_frame is None else int(end_frame)
    if start < 0 or stop <= start or stop > total_frames:
        raise RackError(f"invalid sample slice {start}:{stop}/{total_frames}: {source}")

    with sf.SoundFile(str(source), mode="r") as handle:
        handle.seek(start)
        pcm = handle.read(stop - start, dtype="float32", always_2d=True)
    pcm = np.asarray(pcm, dtype="<f4", order="C")
    if pcm.shape != (stop - start, channels):
        raise RackError(f"decoded sample dimensions changed while reading: {source}")
    if not np.isfinite(pcm).all():
        raise RackError(f"sample contains non-finite PCM: {source}")

    pcm_digest = hashlib.sha256()
    pcm_digest.update(f"f32le:{sample_rate}:{channels}:{stop - start}:".encode("ascii"))
    pcm_digest.update(pcm.tobytes(order="C"))
    return {
        "path": str(source),
        "byte_sha256": rack_sha256_file(source),
        "slice_pcm_sha256": pcm_digest.hexdigest(),
        "sample_rate": sample_rate,
        "channels": channels,
        "total_frames": total_frames,
        "start_frame": start,
        "end_frame": stop,
        "slice_frames": stop - start,
    }


def rack_payload(rack: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(rack))
    payload.pop("rack_sha256", None)
    return midi_jsonable(payload)


def rack_compute_sha256(rack: Mapping[str, Any]) -> str:
    return midi_sha256_json(rack_payload(rack))


def _zone_defaults(zone: Mapping[str, Any], rack_mode: str, ordinal: int) -> dict[str, Any]:
    out = deepcopy(dict(zone))
    sample_path = out.pop("sample_path", None)
    if sample_path is not None and "sample" not in out:
        out["sample"] = {"path": str(sample_path)}
    sample = out.get("sample") or {}
    if not isinstance(sample, Mapping) or not str(sample.get("path") or ""):
        raise RackError(f"zone {ordinal} requires sample_path or sample.path")
    root_key = int(out.get("root_key", 60))
    if not 0 <= root_key <= 127:
        raise RackError(f"zone {ordinal} root_key must be in [0,127]")
    if "key_range" not in out:
        out["key_range"] = [root_key, root_key] if rack_mode == "trigger" else [0, 127]
    out["key_range"] = _range_pair(out["key_range"], f"zone {ordinal}.key_range", 0, 127)
    out["velocity_range"] = _range_pair(out.get("velocity_range", [1, 127]), f"zone {ordinal}.velocity_range", 1, 127)
    out["root_key"] = root_key
    out["trigger_mode"] = str(out.get("trigger_mode") or ("one_shot" if rack_mode == "trigger" else "gate"))
    out["tune_cents"] = _finite(out.get("tune_cents", 0.0), f"zone {ordinal}.tune_cents")
    out["gain_db"] = _finite(out.get("gain_db", 0.0), f"zone {ordinal}.gain_db")
    out["pan"] = _finite(out.get("pan", 0.0), f"zone {ordinal}.pan")
    out["attack_ms"] = _finite(out.get("attack_ms", 2.0), f"zone {ordinal}.attack_ms")
    out["release_ms"] = _finite(out.get("release_ms", 20.0), f"zone {ordinal}.release_ms")
    out["exclusive_group"] = str(out.get("exclusive_group") or "")
    out["tags"] = _normalize_tags(out.get("tags"))
    out.setdefault("loop", {"enabled": False, "start_frame": 0, "end_frame": 0, "crossfade_frames": 0})
    return out


def rack_seal_draft(draft: Mapping[str, Any], *, base_dir: str | Path | None = None) -> dict[str, Any]:
    """Resolve sample identities and seal an immutable RackRevision."""
    raw = deepcopy(dict(draft))
    rack_id = str(raw.get("rack_id") or "").strip()
    name = str(raw.get("name") or rack_id).strip()
    mode = str(raw.get("mode") or "").strip()
    if not rack_id or not name:
        raise RackError("rack_id and name are required")
    if mode not in RACK_MODES:
        raise RackError(f"rack mode must be one of {sorted(RACK_MODES)}")
    zones_in = raw.get("zones")
    if not isinstance(zones_in, list) or not zones_in:
        raise RackError("rack must contain at least one zone")
    base = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd().resolve()
    zones: list[dict[str, Any]] = []
    for ordinal, zone_value in enumerate(zones_in):
        if not isinstance(zone_value, Mapping):
            raise RackError(f"zone {ordinal} must be an object")
        zone = _zone_defaults(zone_value, mode, ordinal)
        sample_seed = dict(zone["sample"])
        sample_path = Path(str(sample_seed["path"])).expanduser()
        if not sample_path.is_absolute():
            sample_path = base / sample_path
        start = int(sample_seed.get("start_frame", zone.pop("sample_start_frame", 0)) or 0)
        end_raw = sample_seed.get("end_frame", zone.pop("sample_end_frame", None))
        end = None if end_raw in {None, ""} else int(end_raw)
        identity = rack_sample_identity(sample_path, start_frame=start, end_frame=end)
        zone_id = str(zone.get("zone_id") or "").strip()
        if not zone_id:
            zone_id = "zone_" + hashlib.sha256(
                f"{rack_id}:{ordinal}:{identity['slice_pcm_sha256']}:{zone['root_key']}:{zone['key_range']}:{zone['velocity_range']}".encode("utf-8")
            ).hexdigest()[:20]
        zone["zone_id"] = zone_id
        zone["sample"] = identity
        loop = dict(zone.get("loop") or {})
        enabled = bool(loop.get("enabled"))
        slice_frames = int(identity["slice_frames"])
        loop_start = int(loop.get("start_frame", 0) or 0)
        loop_end = int(loop.get("end_frame", slice_frames) or slice_frames)
        crossfade = int(loop.get("crossfade_frames", 0) or 0)
        if enabled:
            if loop_start < 0 or loop_end <= loop_start or loop_end > slice_frames:
                raise RackError(f"zone {zone_id} has invalid loop bounds")
            if crossfade < 0 or crossfade * 2 >= loop_end - loop_start:
                raise RackError(f"zone {zone_id} has invalid loop crossfade")
        else:
            loop_start, loop_end, crossfade = 0, slice_frames, 0
        zone["loop"] = {
            "enabled": enabled,
            "start_frame": loop_start,
            "end_frame": loop_end,
            "crossfade_frames": crossfade,
        }
        zones.append(zone)

    out = {
        "schema_version": RACK_SCHEMA_VERSION,
        "kind": RACK_KIND,
        "rack_id": rack_id,
        "name": name,
        "mode": mode,
        "metadata": {
            **midi_jsonable(dict(raw.get("metadata") or {})),
            "tags": _normalize_tags((raw.get("metadata") or {}).get("tags")),
        },
        "created_by": midi_jsonable(dict(raw.get("created_by") or {"actor": "user", "reason": "rack seal"})),
        "zones": zones,
    }
    out["rack_sha256"] = rack_compute_sha256(out)
    rack_validate_revision(out)
    return out


def rack_validate_revision(rack: Mapping[str, Any], *, require_sealed: bool = True) -> None:
    if int(rack.get("schema_version") or 0) != RACK_SCHEMA_VERSION:
        raise RackError(f"unsupported rack schema: {rack.get('schema_version')}")
    if str(rack.get("kind") or "") != RACK_KIND:
        raise RackError(f"unsupported rack kind: {rack.get('kind')}")
    if not str(rack.get("rack_id") or "") or not str(rack.get("name") or ""):
        raise RackError("rack_id and name are required")
    mode = str(rack.get("mode") or "")
    if mode not in RACK_MODES:
        raise RackError(f"rack mode must be one of {sorted(RACK_MODES)}")
    created_by = rack.get("created_by") or {}
    if not str(created_by.get("actor") or "") or not str(created_by.get("reason") or ""):
        raise RackError("rack.created_by requires actor and reason")
    zones = rack.get("zones")
    if not isinstance(zones, list) or not zones:
        raise RackError("rack must contain zones")
    seen: set[str] = set()
    for ordinal, zone in enumerate(zones):
        if not isinstance(zone, Mapping):
            raise RackError(f"zone {ordinal} must be an object")
        zone_id = str(zone.get("zone_id") or "")
        if not zone_id or zone_id in seen:
            raise RackError(f"duplicate or empty zone_id: {zone_id}")
        seen.add(zone_id)
        _range_pair(zone.get("key_range"), f"zone {zone_id}.key_range", 0, 127)
        _range_pair(zone.get("velocity_range"), f"zone {zone_id}.velocity_range", 1, 127)
        root = int(zone.get("root_key", -1))
        if not 0 <= root <= 127:
            raise RackError(f"zone {zone_id}.root_key must be in [0,127]")
        if str(zone.get("trigger_mode") or "") not in RACK_TRIGGER_MODES:
            raise RackError(f"zone {zone_id}.trigger_mode must be one of {sorted(RACK_TRIGGER_MODES)}")
        pan = _finite(zone.get("pan"), f"zone {zone_id}.pan")
        if pan < -1.0 or pan > 1.0:
            raise RackError(f"zone {zone_id}.pan must be in [-1,1]")
        if _finite(zone.get("attack_ms"), f"zone {zone_id}.attack_ms") < 0:
            raise RackError(f"zone {zone_id}.attack_ms must be nonnegative")
        if _finite(zone.get("release_ms"), f"zone {zone_id}.release_ms") < 0:
            raise RackError(f"zone {zone_id}.release_ms must be nonnegative")
        sample = zone.get("sample") or {}
        required = {
            "path", "byte_sha256", "slice_pcm_sha256", "sample_rate", "channels",
            "total_frames", "start_frame", "end_frame", "slice_frames",
        }
        missing = sorted(required - set(sample))
        if missing:
            raise RackError(f"zone {zone_id}.sample is missing {missing}")
        if int(sample["sample_rate"]) <= 0 or int(sample["channels"]) not in {1, 2}:
            raise RackError(f"zone {zone_id} has invalid sample dimensions")
        if int(sample["end_frame"]) - int(sample["start_frame"]) != int(sample["slice_frames"]):
            raise RackError(f"zone {zone_id} slice dimensions disagree")
        loop = zone.get("loop") or {}
        for key in ("enabled", "start_frame", "end_frame", "crossfade_frames"):
            if key not in loop:
                raise RackError(f"zone {zone_id}.loop is missing {key}")
        if bool(loop["enabled"]):
            if int(loop["start_frame"]) < 0 or int(loop["end_frame"]) <= int(loop["start_frame"]):
                raise RackError(f"zone {zone_id} has invalid loop bounds")
            if int(loop["end_frame"]) > int(sample["slice_frames"]):
                raise RackError(f"zone {zone_id} loop exceeds its sample slice")
    if require_sealed and str(rack.get("rack_sha256") or "") != rack_compute_sha256(rack):
        raise RackError("rack_sha256 does not match rack contents")


def rack_find_zone(rack: Mapping[str, Any], note: int, velocity: int) -> dict[str, Any] | None:
    rack_validate_revision(rack)
    matches = []
    for zone in rack["zones"]:
        key_lo, key_hi = [int(value) for value in zone["key_range"]]
        vel_lo, vel_hi = [int(value) for value in zone["velocity_range"]]
        if key_lo <= int(note) <= key_hi and vel_lo <= int(velocity) <= vel_hi:
            matches.append(zone)
    if not matches:
        return None
    matches.sort(
        key=lambda zone: (
            int(zone["key_range"][1]) - int(zone["key_range"][0]),
            int(zone["velocity_range"][1]) - int(zone["velocity_range"][0]),
            abs(int(note) - int(zone["root_key"])),
            str(zone["zone_id"]),
        )
    )
    return deepcopy(dict(matches[0]))


def rack_capabilities(rack: Mapping[str, Any]) -> dict[str, Any]:
    rack_validate_revision(rack)
    notes: set[int] = set()
    one_shot = 0
    looped = 0
    for zone in rack["zones"]:
        notes.update(range(int(zone["key_range"][0]), int(zone["key_range"][1]) + 1))
        one_shot += int(zone["trigger_mode"] == "one_shot")
        looped += int(bool((zone.get("loop") or {}).get("enabled")))
    return {
        "rack_id": rack["rack_id"],
        "rack_sha256": rack["rack_sha256"],
        "mode": rack["mode"],
        "tags": list((rack.get("metadata") or {}).get("tags") or []),
        "zone_count": len(rack["zones"]),
        "covered_note_count": len(notes),
        "minimum_note": min(notes) if notes else None,
        "maximum_note": max(notes) if notes else None,
        "one_shot_zone_count": one_shot,
        "looped_zone_count": looped,
    }


def rack_verify_sources(rack: Mapping[str, Any], *, raise_on_error: bool = True) -> dict[str, Any]:
    rack_validate_revision(rack)
    cache: dict[tuple[str, int, int], dict[str, Any]] = {}
    rows = []
    for zone in rack["zones"]:
        expected = zone["sample"]
        key = (str(expected["path"]), int(expected["start_frame"]), int(expected["end_frame"]))
        try:
            actual = cache.get(key)
            if actual is None:
                actual = rack_sample_identity(key[0], start_frame=key[1], end_frame=key[2])
                cache[key] = actual
            failures = [
                field
                for field in ("byte_sha256", "slice_pcm_sha256", "sample_rate", "channels", "total_frames", "start_frame", "end_frame", "slice_frames")
                if actual[field] != expected[field]
            ]
            rows.append({"zone_id": zone["zone_id"], "ok": not failures, "failures": failures})
        except Exception as exc:
            rows.append({"zone_id": zone["zone_id"], "ok": False, "failures": [str(exc)]})
    receipt = {
        "ok": all(row["ok"] for row in rows),
        "rack_id": rack["rack_id"],
        "rack_sha256": rack["rack_sha256"],
        "zones": rows,
    }
    if raise_on_error and not receipt["ok"]:
        failed = [f"{row['zone_id']}: {', '.join(row['failures'])}" for row in rows if not row["ok"]]
        raise RackError("rack source identity changed: " + "; ".join(failed))
    return receipt


def rack_template(*, mode: str = "pitched", rack_id: str = "my-rack", name: str = "My Rack") -> dict[str, Any]:
    if mode not in RACK_MODES:
        raise RackError(f"rack mode must be one of {sorted(RACK_MODES)}")
    root = 36 if mode == "trigger" else 60
    return {
        "rack_id": rack_id,
        "name": name,
        "mode": mode,
        "metadata": {"tags": ["drums" if mode == "trigger" else "instrument"]},
        "created_by": {"actor": "user", "reason": "crate substitution rack"},
        "zones": [
            {
                "zone_id": "replace-me",
                "sample_path": "/absolute/path/to/sample.wav",
                "sample_start_frame": 0,
                "sample_end_frame": None,
                "key_range": [root, root] if mode == "trigger" else [0, 127],
                "velocity_range": [1, 127],
                "root_key": root,
                "trigger_mode": "one_shot" if mode == "trigger" else "gate",
                "loop": {"enabled": False, "start_frame": 0, "end_frame": 0, "crossfade_frames": 0},
                "tune_cents": 0.0,
                "gain_db": 0.0,
                "pan": 0.0,
                "attack_ms": 2.0,
                "release_ms": 20.0,
                "tags": [],
            }
        ],
    }


def rack_load_revision(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    rack_validate_revision(value)
    return value


def rack_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(midi_jsonable(dict(value)), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return {"ok": True, "path": str(destination), "sha256": rack_sha256_file(destination)}
