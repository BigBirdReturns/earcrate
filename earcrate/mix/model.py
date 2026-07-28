from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

MIX_SCORE_SCHEMA_VERSION = 1
MIX_SCORE_KIND = "earcrate_mix_score"
MIX_EXECUTION_SCHEMA_VERSION = 1
MIX_EXECUTION_KIND = "earcrate_mix_execution_ledger"
MIX_RENDER_RECEIPT_SCHEMA_VERSION = 1
MIX_RENDER_RECEIPT_KIND = "earcrate_mix_render_receipt"

MIX_TRANSPORT_OPERATIONS = (
    "load",
    "play",
    "stop",
    "cut",
    "jump",
    "seek",
    "loop",
    "exit_loop",
    "set_rate",
    "nudge",
)
MIX_DECK_AUTOMATION_OPERATIONS = (
    "set_gain",
    "fade",
    "mute",
    "unmute",
    "set_pan",
)
MIX_MASTER_AUTOMATION_OPERATIONS = (
    "set_crossfader",
    "crossfade",
)
MIX_OPERATIONS = tuple(
    [*MIX_TRANSPORT_OPERATIONS, *MIX_DECK_AUTOMATION_OPERATIONS, *MIX_MASTER_AUTOMATION_OPERATIONS]
)


class MixScoreError(ValueError):
    """Raised when a MixScore cannot be validated or executed exactly."""


def mixscore_canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def mixscore_sha256_json(value: Any) -> str:
    return hashlib.sha256(mixscore_canonical_json_bytes(value)).hexdigest()


def mixscore_payload(score: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(score))
    payload.pop("score_sha256", None)
    return payload


def _mixscore_number(
    value: Any,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
    inclusive_minimum: bool = True,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MixScoreError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise MixScoreError(f"{field} must be a finite number")
    if minimum is not None:
        if inclusive_minimum and number < minimum:
            raise MixScoreError(f"{field} must be >= {minimum}")
        if not inclusive_minimum and number <= minimum:
            raise MixScoreError(f"{field} must be > {minimum}")
    if maximum is not None and number > maximum:
        raise MixScoreError(f"{field} must be <= {maximum}")
    return number


def _mixscore_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MixScoreError(f"{field} must be nonempty")
    return text


def _mixscore_normalize_cues(raw: Any, *, asset_id: str) -> dict[str, float]:
    if raw is None or raw == "":
        return {"start": 0.0}
    if not isinstance(raw, Mapping):
        raise MixScoreError(f"asset {asset_id} cues must be an object")
    cues: dict[str, float] = {}
    for cue_name, cue_value in raw.items():
        name = _mixscore_text(cue_name, field=f"asset {asset_id} cue name")
        value = cue_value.get("source_beat") if isinstance(cue_value, Mapping) else cue_value
        cues[name] = _mixscore_number(
            value,
            field=f"asset {asset_id} cue {name}",
            minimum=0.0,
        )
    if not cues:
        cues["start"] = 0.0
    return dict(sorted(cues.items()))


def _mixscore_normalize_asset(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    asset_id = _mixscore_text(raw.get("asset_id"), field=f"asset {index} asset_id")
    path = _mixscore_text(raw.get("path"), field=f"asset {asset_id} path")
    source_bpm = _mixscore_number(
        raw.get("source_bpm"),
        field=f"asset {asset_id} source_bpm",
        minimum=20.0,
        maximum=400.0,
    )
    downbeat_seconds = _mixscore_number(
        raw.get("downbeat_seconds", 0.0),
        field=f"asset {asset_id} downbeat_seconds",
        minimum=0.0,
    )
    expected_file_sha256 = str(raw.get("expected_file_sha256") or "").strip().lower() or None
    if expected_file_sha256 is not None:
        if len(expected_file_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_file_sha256):
            raise MixScoreError(f"asset {asset_id} expected_file_sha256 must be a lowercase SHA-256")
    return {
        "asset_id": asset_id,
        "path": path,
        "source_bpm": source_bpm,
        "downbeat_seconds": downbeat_seconds,
        "cues": _mixscore_normalize_cues(raw.get("cues"), asset_id=asset_id),
        "expected_file_sha256": expected_file_sha256,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def _mixscore_normalize_deck(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    deck_id = _mixscore_text(raw.get("deck_id"), field=f"deck {index} deck_id")
    side = str(raw.get("crossfader_side") or "none").strip().upper()
    if side not in {"A", "B", "NONE"}:
        raise MixScoreError(f"deck {deck_id} crossfader_side must be A, B, or none")
    gain_db = _mixscore_number(
        raw.get("gain_db", 0.0),
        field=f"deck {deck_id} gain_db",
        minimum=-120.0,
        maximum=24.0,
    )
    pan = _mixscore_number(
        raw.get("pan", 0.0),
        field=f"deck {deck_id} pan",
        minimum=-1.0,
        maximum=1.0,
    )
    return {
        "deck_id": deck_id,
        "crossfader_side": side,
        "gain_db": gain_db,
        "pan": pan,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def _mixscore_event_time(raw: Mapping[str, Any], *, op: str, ordinal: int) -> dict[str, float]:
    label = f"event {ordinal} ({op})"
    if op in {"fade", "crossfade"}:
        start = _mixscore_number(raw.get("from_beat"), field=f"{label} from_beat", minimum=0.0)
        end = _mixscore_number(raw.get("to_beat"), field=f"{label} to_beat", minimum=0.0)
        if end <= start:
            raise MixScoreError(f"{label} to_beat must be greater than from_beat")
        return {"from_beat": start, "to_beat": end}
    at = _mixscore_number(raw.get("at_beat"), field=f"{label} at_beat", minimum=0.0)
    return {"at_beat": at}


def _mixscore_normalize_event(
    raw: Mapping[str, Any],
    ordinal: int,
    *,
    asset_ids: set[str],
    deck_ids: set[str],
) -> dict[str, Any]:
    op = str(raw.get("op") or "").strip().lower()
    if op not in MIX_OPERATIONS:
        raise MixScoreError(f"event {ordinal} has unsupported operation {op!r}")
    out: dict[str, Any] = {"ordinal": int(ordinal), "op": op}
    out.update(_mixscore_event_time(raw, op=op, ordinal=ordinal))

    if op not in MIX_MASTER_AUTOMATION_OPERATIONS:
        deck_id = _mixscore_text(raw.get("deck_id"), field=f"event {ordinal} deck_id")
        if deck_id not in deck_ids:
            raise MixScoreError(f"event {ordinal} references unknown deck {deck_id}")
        out["deck_id"] = deck_id

    if op in {"load", "play"}:
        asset_id = str(raw.get("asset_id") or "").strip()
        if op == "load" or asset_id:
            if asset_id not in asset_ids:
                raise MixScoreError(f"event {ordinal} references unknown asset {asset_id!r}")
            out["asset_id"] = asset_id
    if op in {"play", "jump", "seek", "loop"}:
        cue = str(raw.get("cue") or "").strip()
        if cue:
            out["cue"] = cue
        if raw.get("source_beat") is not None:
            out["source_beat"] = _mixscore_number(
                raw.get("source_beat"),
                field=f"event {ordinal} source_beat",
                minimum=0.0,
            )
    if op == "play":
        out["sync"] = bool(raw.get("sync", True))
        out["rate"] = _mixscore_number(
            raw.get("rate", 1.0),
            field=f"event {ordinal} rate",
            minimum=0.05,
            maximum=8.0,
            inclusive_minimum=False,
        )
    if op == "loop":
        out["length_beats"] = _mixscore_number(
            raw.get("length_beats"),
            field=f"event {ordinal} length_beats",
            minimum=1.0 / 64.0,
            maximum=256.0,
        )
        out["crossfade_ms"] = _mixscore_number(
            raw.get("crossfade_ms", 8.0),
            field=f"event {ordinal} crossfade_ms",
            minimum=0.0,
            maximum=100.0,
        )
    if op == "set_rate":
        out["rate"] = _mixscore_number(
            raw.get("rate"),
            field=f"event {ordinal} rate",
            minimum=0.05,
            maximum=8.0,
            inclusive_minimum=False,
        )
        if "sync" in raw:
            out["sync"] = bool(raw.get("sync"))
    if op == "nudge":
        has_beats = raw.get("delta_source_beats") is not None
        has_ms = raw.get("delta_ms") is not None
        if has_beats == has_ms:
            raise MixScoreError(f"event {ordinal} nudge requires exactly one of delta_source_beats or delta_ms")
        if has_beats:
            out["delta_source_beats"] = _mixscore_number(
                raw.get("delta_source_beats"),
                field=f"event {ordinal} delta_source_beats",
            )
        else:
            out["delta_ms"] = _mixscore_number(raw.get("delta_ms"), field=f"event {ordinal} delta_ms")
    if op == "set_gain":
        out["gain_db"] = _mixscore_number(
            raw.get("gain_db"),
            field=f"event {ordinal} gain_db",
            minimum=-120.0,
            maximum=24.0,
        )
    if op == "fade":
        out["from_db"] = _mixscore_number(
            raw.get("from_db"),
            field=f"event {ordinal} from_db",
            minimum=-120.0,
            maximum=24.0,
        )
        out["to_db"] = _mixscore_number(
            raw.get("to_db"),
            field=f"event {ordinal} to_db",
            minimum=-120.0,
            maximum=24.0,
        )
        curve = str(raw.get("curve") or "equal_power").strip().lower()
        if curve not in {"linear", "linear_db", "equal_power", "s_curve"}:
            raise MixScoreError(f"event {ordinal} fade curve is unsupported: {curve}")
        out["curve"] = curve
    if op == "set_pan":
        out["pan"] = _mixscore_number(
            raw.get("pan"),
            field=f"event {ordinal} pan",
            minimum=-1.0,
            maximum=1.0,
        )
    if op == "set_crossfader":
        out["position"] = _mixscore_number(
            raw.get("position"),
            field=f"event {ordinal} position",
            minimum=-1.0,
            maximum=1.0,
        )
    if op == "crossfade":
        out["from_position"] = _mixscore_number(
            raw.get("from_position"),
            field=f"event {ordinal} from_position",
            minimum=-1.0,
            maximum=1.0,
        )
        out["to_position"] = _mixscore_number(
            raw.get("to_position"),
            field=f"event {ordinal} to_position",
            minimum=-1.0,
            maximum=1.0,
        )
        curve = str(raw.get("curve") or "s_curve").strip().lower()
        if curve not in {"linear", "s_curve"}:
            raise MixScoreError(f"event {ordinal} crossfade curve is unsupported: {curve}")
        out["curve"] = curve

    identity_payload = deepcopy(out)
    event_id = str(raw.get("event_id") or "").strip()
    out["event_id"] = event_id or "mix_event_" + mixscore_sha256_json(identity_payload)[:24]
    return out


def _mixscore_normalize(score: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(score, Mapping):
        raise MixScoreError("MixScore must be a JSON object")
    raw = deepcopy(dict(score))
    schema_version = int(raw.get("schema_version") or MIX_SCORE_SCHEMA_VERSION)
    kind = str(raw.get("kind") or MIX_SCORE_KIND)
    if schema_version != MIX_SCORE_SCHEMA_VERSION:
        raise MixScoreError(f"unsupported MixScore schema version: {schema_version}")
    if kind != MIX_SCORE_KIND:
        raise MixScoreError(f"unsupported MixScore kind: {kind}")

    clock = raw.get("clock") or {}
    if not isinstance(clock, Mapping):
        raise MixScoreError("MixScore clock must be an object")
    bpm = _mixscore_number(clock.get("bpm"), field="clock bpm", minimum=20.0, maximum=300.0)
    beats_per_bar = int(
        _mixscore_number(clock.get("beats_per_bar", 4), field="clock beats_per_bar", minimum=1.0, maximum=32.0)
    )
    sample_rate = int(
        _mixscore_number(clock.get("sample_rate", 48_000), field="clock sample_rate", minimum=8_000.0, maximum=192_000.0)
    )
    end_beat = _mixscore_number(raw.get("end_beat"), field="end_beat", minimum=0.0, inclusive_minimum=False)
    peak_ceiling = _mixscore_number(raw.get("peak_ceiling", 0.95), field="peak_ceiling", minimum=0.01, maximum=1.0)
    master_gain_db = _mixscore_number(
        raw.get("master_gain_db", 0.0),
        field="master_gain_db",
        minimum=-120.0,
        maximum=24.0,
    )

    raw_assets = raw.get("assets") or []
    raw_decks = raw.get("decks") or []
    raw_events = raw.get("events") or []
    if not isinstance(raw_assets, Sequence) or isinstance(raw_assets, (str, bytes)) or not raw_assets:
        raise MixScoreError("MixScore requires at least one asset")
    if not isinstance(raw_decks, Sequence) or isinstance(raw_decks, (str, bytes)) or not raw_decks:
        raise MixScoreError("MixScore requires at least one deck")
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)) or not raw_events:
        raise MixScoreError("MixScore requires at least one event")

    assets = [_mixscore_normalize_asset(dict(row), index) for index, row in enumerate(raw_assets)]
    asset_ids = [row["asset_id"] for row in assets]
    if len(asset_ids) != len(set(asset_ids)):
        raise MixScoreError("MixScore asset IDs must be unique")
    decks = [_mixscore_normalize_deck(dict(row), index) for index, row in enumerate(raw_decks)]
    deck_ids = [row["deck_id"] for row in decks]
    if len(deck_ids) != len(set(deck_ids)):
        raise MixScoreError("MixScore deck IDs must be unique")

    events = [
        _mixscore_normalize_event(
            dict(row),
            ordinal,
            asset_ids=set(asset_ids),
            deck_ids=set(deck_ids),
        )
        for ordinal, row in enumerate(raw_events)
    ]
    event_ids = [row["event_id"] for row in events]
    if len(event_ids) != len(set(event_ids)):
        raise MixScoreError("MixScore event IDs must be unique")
    for event in events:
        terminal = float(event.get("to_beat", event.get("at_beat", 0.0)))
        if terminal > end_beat + 1e-9:
            raise MixScoreError(
                f"event {event['event_id']} ends at beat {terminal}, after MixScore end_beat {end_beat}"
            )

    return {
        "schema_version": MIX_SCORE_SCHEMA_VERSION,
        "kind": MIX_SCORE_KIND,
        "title": str(raw.get("title") or "Untitled MixScore"),
        "clock": {
            "bpm": bpm,
            "beats_per_bar": beats_per_bar,
            "sample_rate": sample_rate,
        },
        "end_beat": end_beat,
        "peak_ceiling": peak_ceiling,
        "master_gain_db": master_gain_db,
        "assets": assets,
        "decks": decks,
        "events": events,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def mixscore_seal(score: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _mixscore_normalize(score)
    computed = mixscore_sha256_json(normalized)
    supplied = str(score.get("score_sha256") or "")
    if supplied and supplied != computed:
        raise MixScoreError("score_sha256 does not match MixScore contents")
    normalized["score_sha256"] = computed
    return normalized


def mixscore_validate(score: Mapping[str, Any]) -> None:
    mixscore_seal(score)

def mixscore_load(path: str | Path) -> tuple[dict[str, Any], Path]:
    score_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(score_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MixScoreError(f"MixScore file does not exist: {score_path}") from exc
    except json.JSONDecodeError as exc:
        raise MixScoreError(f"MixScore is not valid JSON: {exc}") from exc
    return mixscore_seal(value), score_path.parent


def mixscore_write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def mixscore_capability() -> dict[str, Any]:
    value = {
        "schema_version": MIX_SCORE_SCHEMA_VERSION,
        "kind": MIX_SCORE_KIND,
        "ready": True,
        "renderer": "deterministic_offline_n_deck_source_transport",
        "time_domains": ["master_beat", "source_beat", "source_frame"],
        "operations": list(MIX_OPERATIONS),
        "transport_operations": list(MIX_TRANSPORT_OPERATIONS),
        "automation_operations": [
            *MIX_DECK_AUTOMATION_OPERATIONS,
            *MIX_MASTER_AUTOMATION_OPERATIONS,
        ],
        "features": {
            "independent_playheads": True,
            "simultaneous_decks": True,
            "tempo_sync_varispeed": True,
            "cue_jump": True,
            "bounded_loop": True,
            "hard_cut": True,
            "deck_gain_automation": True,
            "equal_power_crossfader": True,
            "per_deck_stems": True,
            "event_execution_ledger": True,
            "source_identity_revalidation": True,
        },
        "requires_network": False,
        "requires_cloud": False,
        "requires_gpu": False,
        "authority": "MixScore, source identities, event execution ledger, stems, and render receipt remain EarCrate data",
    }
    value["capability_sha256"] = mixscore_sha256_json(value)
    return value
