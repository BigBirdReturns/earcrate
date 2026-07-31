from __future__ import annotations

"""Validation and hashing for immutable causal-score ProjectRevisions.

The existing project model remains the authority for audio-clip revisions. This
module extends the same content-addressed lineage to symbolic causal scores
without weakening the legacy validator or laundering MIDI events into clips.
"""

from copy import deepcopy
from typing import Any, Mapping

from .util import ValidationError, now_utc, sha256_json

CAUSAL_AUTHORITY_KIND = "causal_score"
CAUSAL_PERFORMANCE_SCHEMA = "earcrate/causal-performance-custody@1"
CAUSAL_STAGES = {"historical_custody", "semantic_adoption", "source_execution", "production"}
TRACK_ROLES = {"floor", "foreground", "spark", "aux", "master"}


def causal_revision_payload(revision: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(revision))
    payload.pop("revision_sha", None)
    payload.pop("created_at", None)
    return payload


def causal_compute_revision_sha(revision: Mapping[str, Any]) -> str:
    return sha256_json(causal_revision_payload(revision))


def is_causal_revision(revision: Mapping[str, Any]) -> bool:
    return str(revision.get("authority_kind") or "") == CAUSAL_AUTHORITY_KIND


def _require(mapping: Mapping[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValidationError(f"{where} is missing: {', '.join(missing)}")


def causal_validate_revision(revision: Mapping[str, Any], *, require_sealed: bool = True) -> None:
    _require(
        revision,
        (
            "schema_version", "project_id", "parent_revision_sha", "created_by", "intent",
            "sources", "tempo_map", "tracks", "transitions", "automation", "mastering",
            "decisions", "locks", "static_gate_receipt", "compiler_receipt", "compile_request",
            "authority_kind", "performance",
        ),
        "causal revision",
    )
    if int(revision.get("schema_version") or 0) != 1:
        raise ValidationError("unsupported project revision schema")
    if not str(revision.get("project_id") or ""):
        raise ValidationError("causal revision requires project_id")
    if not isinstance(revision.get("created_by"), Mapping):
        raise ValidationError("causal revision created_by must be an object")
    if not isinstance(revision.get("intent"), Mapping):
        raise ValidationError("causal revision intent must be an object")
    if not isinstance(revision.get("sources"), Mapping):
        raise ValidationError("causal revision sources must be an object")
    if not is_causal_revision(revision):
        raise ValidationError("causal validator received a non-causal revision")

    performance = revision.get("performance") or {}
    _require(
        performance,
        ("schema", "stage", "artifacts", "midi_semantic_sha256", "score_sha256", "note_ledger_sha256", "duration_seconds"),
        "causal revision.performance",
    )
    if str(performance.get("schema") or "") != CAUSAL_PERFORMANCE_SCHEMA:
        raise ValidationError("unsupported causal performance schema")
    if str(performance.get("stage") or "") not in CAUSAL_STAGES:
        raise ValidationError("unsupported causal performance stage")
    if float(performance.get("duration_seconds") or 0.0) <= 0.0:
        raise ValidationError("causal performance duration must be positive")
    for field in ("midi_semantic_sha256", "score_sha256", "note_ledger_sha256"):
        value = str(performance.get(field) or "")
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
            raise ValidationError(f"causal performance {field} must be a SHA-256")
    artifacts = performance.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        raise ValidationError("causal performance artifacts must be an object")
    for key in ("midi", "score", "historical_neutral_render", "seed_selection"):
        artifact = artifacts.get(key) or {}
        _require(artifact, ("relative_path", "raw_sha256"), f"causal artifact {key}")

    tempo_map = revision.get("tempo_map") or []
    if not isinstance(tempo_map, list) or not tempo_map:
        raise ValidationError("causal revision requires a tempo map")
    previous = -1.0
    for row in tempo_map:
        if not isinstance(row, Mapping):
            raise ValidationError("causal tempo rows must be objects")
        _require(row, ("beat", "bpm", "meter"), "causal tempo row")
        beat, bpm = float(row["beat"]), float(row["bpm"])
        if beat < 0.0 or beat < previous or bpm <= 0.0:
            raise ValidationError("causal tempo map must be ordered and positive")
        previous = beat

    tracks = revision.get("tracks") or []
    if not isinstance(tracks, list) or not tracks:
        raise ValidationError("causal revision requires descriptive tracks")
    seen: set[str] = set()
    for track in tracks:
        if not isinstance(track, Mapping):
            raise ValidationError("causal tracks must be objects")
        _require(track, ("track_id", "role", "clips"), "causal track")
        track_id = str(track.get("track_id") or "")
        if not track_id or track_id in seen:
            raise ValidationError(f"duplicate or empty causal track_id: {track_id}")
        seen.add(track_id)
        if str(track.get("role") or "") not in TRACK_ROLES:
            raise ValidationError(f"causal track {track_id} has unsupported role")
        if track.get("clips"):
            raise ValidationError("causal tracks are descriptive; note events may not be laundered into audio clips")

    mastering = revision.get("mastering") or {}
    if not isinstance(mastering, Mapping) or mastering.get("state") not in {"unresolved", "finalized"}:
        raise ValidationError("causal mastering state is invalid")
    gate = revision.get("static_gate_receipt") or {}
    if gate and gate.get("passed") is False:
        raise ValidationError("a sealed causal revision cannot fail its static gate")
    if require_sealed:
        expected = causal_compute_revision_sha(revision)
        if str(revision.get("revision_sha") or "") != expected:
            raise ValidationError("revision_sha does not match causal revision contents")


def causal_seal_revision(revision: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(revision))
    out.setdefault("schema_version", 1)
    out.setdefault("created_at", now_utc())
    out["authority_kind"] = CAUSAL_AUTHORITY_KIND
    out["revision_sha"] = causal_compute_revision_sha(out)
    causal_validate_revision(out, require_sealed=True)
    return out


__all__ = [
    "CAUSAL_AUTHORITY_KIND", "CAUSAL_PERFORMANCE_SCHEMA", "CAUSAL_STAGES",
    "is_causal_revision", "causal_revision_payload", "causal_compute_revision_sha",
    "causal_validate_revision", "causal_seal_revision",
]
