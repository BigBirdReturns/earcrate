from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

import numpy as np

from earcrate.mix.audio import _mixscore_beat_to_frame
from earcrate.mix.model import (
    MIX_EXECUTION_KIND,
    MIX_EXECUTION_SCHEMA_VERSION,
    MixScoreError,
    mixscore_sha256_json,
)


def _mixscore_db_to_gain(db: float) -> float:
    return 0.0 if float(db) <= -119.0 else float(10.0 ** (float(db) / 20.0))

def _mixscore_fade_values(start_db: float, end_db: float, count: int, curve: str) -> np.ndarray:
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    x = np.linspace(0.0, 1.0, count, endpoint=False, dtype=np.float64)
    start_gain = _mixscore_db_to_gain(start_db)
    end_gain = _mixscore_db_to_gain(end_db)
    if curve == "linear_db":
        db = float(start_db) + (float(end_db) - float(start_db)) * x
        return np.where(db <= -119.0, 0.0, np.power(10.0, db / 20.0)).astype(np.float32)
    if curve == "s_curve":
        weight = x * x * (3.0 - 2.0 * x)
    elif curve == "equal_power" and start_gain <= 1e-8:
        return (end_gain * np.sin(x * math.pi / 2.0)).astype(np.float32)
    elif curve == "equal_power" and end_gain <= 1e-8:
        return (start_gain * np.cos(x * math.pi / 2.0)).astype(np.float32)
    elif curve == "equal_power":
        weight = 0.5 - 0.5 * np.cos(math.pi * x)
    else:
        weight = x
    return (start_gain * (1.0 - weight) + end_gain * weight).astype(np.float32)

def _mixscore_curve_positions(start: float, end: float, count: int, curve: str) -> np.ndarray:
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    x = np.linspace(0.0, 1.0, count, endpoint=False, dtype=np.float64)
    if curve == "s_curve":
        x = x * x * (3.0 - 2.0 * x)
    return (float(start) + (float(end) - float(start)) * x).astype(np.float32)

def _mixscore_mark_event(
    ledger_rows: dict[str, dict[str, Any]],
    event: Mapping[str, Any],
    *,
    details: Mapping[str, Any],
) -> None:
    row = ledger_rows[str(event["event_id"])]
    if str(row["status"]) != "pending":
        raise MixScoreError(f"event {event['event_id']} was executed more than once")
    row["status"] = "executed"
    row["details"] = deepcopy(dict(details))

def _mixscore_build_gain_envelope(
    deck: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    ledger_rows: dict[str, dict[str, Any]],
    *,
    total_frames: int,
    sample_rate: int,
    bpm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gain = np.full(total_frames, _mixscore_db_to_gain(float(deck["gain_db"])), dtype=np.float32)
    muted = np.zeros(total_frames, dtype=np.bool_)
    pan = np.full(total_frames, float(deck["pan"]), dtype=np.float32)
    ordered = sorted(
        events,
        key=lambda row: (
            float(row.get("from_beat", row.get("at_beat", 0.0))),
            int(row["ordinal"]),
        ),
    )
    for event in ordered:
        op = str(event["op"])
        if op == "set_gain":
            frame = min(total_frames, _mixscore_beat_to_frame(event["at_beat"], sample_rate=sample_rate, bpm=bpm))
            gain[frame:] = np.float32(_mixscore_db_to_gain(float(event["gain_db"])))
            _mixscore_mark_event(ledger_rows, event, details={"start_frame": frame, "gain_db": float(event["gain_db"])})
        elif op == "fade":
            start = min(total_frames, _mixscore_beat_to_frame(event["from_beat"], sample_rate=sample_rate, bpm=bpm))
            end = min(total_frames, _mixscore_beat_to_frame(event["to_beat"], sample_rate=sample_rate, bpm=bpm))
            gain[start:end] = _mixscore_fade_values(
                float(event["from_db"]),
                float(event["to_db"]),
                end - start,
                str(event["curve"]),
            )
            gain[end:] = np.float32(_mixscore_db_to_gain(float(event["to_db"])))
            _mixscore_mark_event(
                ledger_rows,
                event,
                details={
                    "start_frame": start,
                    "end_frame": end,
                    "from_db": float(event["from_db"]),
                    "to_db": float(event["to_db"]),
                    "curve": str(event["curve"]),
                },
            )
        elif op == "mute":
            frame = min(total_frames, _mixscore_beat_to_frame(event["at_beat"], sample_rate=sample_rate, bpm=bpm))
            muted[frame:] = True
            _mixscore_mark_event(ledger_rows, event, details={"start_frame": frame, "muted": True})
        elif op == "unmute":
            frame = min(total_frames, _mixscore_beat_to_frame(event["at_beat"], sample_rate=sample_rate, bpm=bpm))
            muted[frame:] = False
            _mixscore_mark_event(ledger_rows, event, details={"start_frame": frame, "muted": False})
        elif op == "set_pan":
            frame = min(total_frames, _mixscore_beat_to_frame(event["at_beat"], sample_rate=sample_rate, bpm=bpm))
            pan[frame:] = np.float32(float(event["pan"]))
            _mixscore_mark_event(ledger_rows, event, details={"start_frame": frame, "pan": float(event["pan"])})
        else:
            raise MixScoreError(f"internal deck automation dispatcher does not implement {op}")
    return gain, muted, pan

def _mixscore_build_crossfader_envelope(
    events: Sequence[Mapping[str, Any]],
    ledger_rows: dict[str, dict[str, Any]],
    *,
    total_frames: int,
    sample_rate: int,
    bpm: float,
) -> np.ndarray:
    position = np.zeros(total_frames, dtype=np.float32)
    ordered = sorted(
        events,
        key=lambda row: (
            float(row.get("from_beat", row.get("at_beat", 0.0))),
            int(row["ordinal"]),
        ),
    )
    for event in ordered:
        op = str(event["op"])
        if op == "set_crossfader":
            frame = min(total_frames, _mixscore_beat_to_frame(event["at_beat"], sample_rate=sample_rate, bpm=bpm))
            position[frame:] = np.float32(float(event["position"]))
            _mixscore_mark_event(
                ledger_rows,
                event,
                details={"start_frame": frame, "position": float(event["position"])},
            )
        elif op == "crossfade":
            start = min(total_frames, _mixscore_beat_to_frame(event["from_beat"], sample_rate=sample_rate, bpm=bpm))
            end = min(total_frames, _mixscore_beat_to_frame(event["to_beat"], sample_rate=sample_rate, bpm=bpm))
            position[start:end] = _mixscore_curve_positions(
                float(event["from_position"]),
                float(event["to_position"]),
                end - start,
                str(event["curve"]),
            )
            position[end:] = np.float32(float(event["to_position"]))
            _mixscore_mark_event(
                ledger_rows,
                event,
                details={
                    "start_frame": start,
                    "end_frame": end,
                    "from_position": float(event["from_position"]),
                    "to_position": float(event["to_position"]),
                    "curve": str(event["curve"]),
                },
            )
        else:
            raise MixScoreError(f"internal crossfader dispatcher does not implement {op}")
    return np.clip(position, -1.0, 1.0)

def _mixscore_execution_ledger(
    score: Mapping[str, Any],
    rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered = [rows[str(event["event_id"])] for event in score["events"]]
    pending = [row for row in ordered if str(row["status"]) != "executed"]
    if pending:
        raise MixScoreError(
            "MixScore execution failed to account for events: "
            + ", ".join(str(row["event_id"]) for row in pending[:12])
        )
    ledger = {
        "schema_version": MIX_EXECUTION_SCHEMA_VERSION,
        "kind": MIX_EXECUTION_KIND,
        "score_sha256": str(score["score_sha256"]),
        "complete": True,
        "selected_event_count": len(ordered),
        "executed_event_count": len(ordered),
        "refused_event_count": 0,
        "events": ordered,
    }
    ledger["ledger_sha256"] = mixscore_sha256_json(ledger)
    return ledger
