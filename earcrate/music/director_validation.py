from __future__ import annotations

from typing import Any, Mapping

from earcrate.music.model import music_sha256_json

DJ_STAGE_SCORE_SCHEMA = 1


class MusicDirectorError(ValueError):
    """Raised when the DJ Director cannot compile a valid project."""


DJ_PPQ = 480


def music_validate_stage_score(score: Mapping[str, Any]) -> None:
    if int(score.get("schema_version") or 0) != DJ_STAGE_SCORE_SCHEMA:
        raise MusicDirectorError("unsupported DJ stage-score schema")
    if not str(score.get("score_sha256") or ""):
        raise MusicDirectorError("DJ stage score requires score_sha256")
    sections = score.get("sections") or []
    events = score.get("events") or []
    if not sections or not events:
        raise MusicDirectorError("DJ stage score requires sections and events")
    ids = [str(row.get("event_id") or "") for row in events]
    if not all(ids) or len(ids) != len(set(ids)):
        raise MusicDirectorError("DJ stage score event IDs must be unique")
    musical_events = [row for row in events if str(row.get("kind") or "") in {"note", "cc", "pitchwheel", "sample_trigger"}]
    if not musical_events:
        raise MusicDirectorError("DJ stage score contains no musical events")
    for row in musical_events:
        if int(row.get("start_tick", -1)) < 0:
            raise MusicDirectorError("DJ musical event has a negative start tick")
        if str(row.get("kind") or "") in {"note", "sample_trigger"} and int(row.get("duration_tick") or 0) <= 0:
            raise MusicDirectorError("DJ note/trigger event duration must be positive")
        source_ids = row.get("source_event_ids") or []
        if not source_ids:
            raise MusicDirectorError("DJ musical events require source evidence IDs")
    expected = music_sha256_json({key: value for key, value in score.items() if key != "score_sha256"})
    if str(score.get("score_sha256") or "") != expected:
        raise MusicDirectorError("DJ stage-score hash does not match contents")

__all__ = ["DJ_STAGE_SCORE_SCHEMA", "DJ_PPQ", "MusicDirectorError", "music_validate_stage_score"]
