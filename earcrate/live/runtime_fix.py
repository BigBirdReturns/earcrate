from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

_live_runtime_module = None
import earcrate.live.runtime as _live_runtime_module
from earcrate.midi.model import midi_sha256_json

_EVENT_MARKERS = (
    "_generated_note_id",
    "_generated_note_off_id",
    "_generated_control_id",
    "_live_note_id",
    "_live_note_off_id",
    "_live_control_id",
)


def _live_message_priority(message: Mapping[str, Any], is_meta: bool) -> int:
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


def _marker_identity(row: Mapping[str, Any]) -> str:
    return next((str(row[marker]) for marker in _EVENT_MARKERS if row.get(marker)), "")


def _live_track_events(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order MIDI events while retaining both arranger and live provenance markers."""
    ordered = sorted(
        [deepcopy(dict(row)) for row in raw],
        key=lambda row: (
            int(row["tick"]),
            _live_message_priority(row["message"], bool(row["is_meta"])),
            midi_sha256_json(row["message"]),
            _marker_identity(row),
        ),
    )
    events = []
    for index, row in enumerate(ordered):
        event = {
            "tick": int(row["tick"]),
            "order": index,
            "is_meta": bool(row["is_meta"]),
            "message": deepcopy(dict(row["message"])),
        }
        for marker in _EVENT_MARKERS:
            if row.get(marker):
                event[marker] = str(row[marker])
        events.append(event)
    return events


if _live_runtime_module is not None:
    _live_runtime_module._arranger_track_events = _live_track_events
    live_build_session = _live_runtime_module.live_build_session
    live_compile_cpu_program = _live_runtime_module.live_compile_cpu_program
    live_execute_cpu_program = _live_runtime_module.live_execute_cpu_program
    live_lower_session_to_midi = _live_runtime_module.live_lower_session_to_midi
    live_validate_cpu_execution = _live_runtime_module.live_validate_cpu_execution
    live_validate_cpu_program = _live_runtime_module.live_validate_cpu_program
    live_validate_midi_lowering = _live_runtime_module.live_validate_midi_lowering
else:
    _arranger_track_events = _live_track_events
