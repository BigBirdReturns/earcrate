from __future__ import annotations

"""Project-owned causal-score continuation.

Gate 8 windows are projections of one immutable PerformanceRevision.  This
module extends an adopted/production causal score without recomposing or
rewriting the accepted prefix.  Optional execution artifacts are attached as
receipts; they never replace the symbolic authority.
"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .custody import _score_note_ledger, _score_track_rows, _verify_score_midi_pair
from .model import seal_revision
from .store import ProjectStore
from .util import ProjectError, ValidationError, now_utc, sha256_file, sha256_json

CONTINUATION_SCHEMA = "earcrate/causal-score-continuation@1"
EXECUTION_MANIFEST_SCHEMA = "earcrate/causal-continuation-execution@1"


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"missing {label}: {source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationError(f"invalid {label}: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain a JSON object")
    return value


def _merge_mapping(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[str(key)] = _merge_mapping(out[str(key)], value)
        else:
            out[str(key)] = deepcopy(value)
    return out


def _prefix_receipt(parent_score: Mapping[str, Any], child_score: Mapping[str, Any]) -> dict[str, Any]:
    parent_rows = _score_note_ledger(parent_score)
    child_rows = _score_note_ledger(child_score)
    if not parent_rows:
        raise ValidationError("parent causal score contains no note events")
    if len(child_rows) <= len(parent_rows):
        raise ValidationError("continuation score must add note events")

    child_by_id: dict[str, dict[str, Any]] = {}
    for row in child_rows:
        event_id = str(row.get("event_id") or "")
        if not event_id or event_id in child_by_id:
            raise ValidationError(f"continuation score has duplicate or empty event_id: {event_id}")
        child_by_id[event_id] = row
    prefix = []
    for row in parent_rows:
        event_id = str(row["event_id"])
        actual = child_by_id.get(event_id)
        if actual is None:
            raise ValidationError(f"continuation deleted accepted event {event_id}")
        if actual != row:
            raise ValidationError(f"continuation rewrote accepted event {event_id}")
        prefix.append(actual)
    if prefix != parent_rows:
        raise ValidationError("continuation changed accepted note-ledger order")

    parent_ids = {str(row["event_id"]) for row in parent_rows}
    added = [row for row in child_rows if str(row["event_id"]) not in parent_ids]
    parent_end_tick = max(int(row["start_tick"]) + int(row["duration_tick"]) for row in parent_rows)
    earliest_added_tick = min(int(row["start_tick"]) for row in added)
    if earliest_added_tick < parent_end_tick:
        raise ValidationError(
            f"continuation backfilled before the accepted boundary: {earliest_added_tick} < {parent_end_tick}"
        )
    return {
        "parent_note_count": len(parent_rows),
        "child_note_count": len(child_rows),
        "added_note_count": len(added),
        "parent_note_ledger_sha256": sha256_json(parent_rows),
        "child_note_ledger_sha256": sha256_json(child_rows),
        "parent_end_tick": parent_end_tick,
        "earliest_added_tick": earliest_added_tick,
        "prefix_unchanged": True,
    }


def _import_execution_manifest(
    store: ProjectStore,
    project_id: str,
    manifest_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_json(manifest_path, "continuation execution manifest")
    if str(manifest.get("schema") or "") != EXECUTION_MANIFEST_SCHEMA:
        raise ValidationError("unsupported continuation execution manifest schema")
    artifacts: dict[str, Any] = {}
    base = Path(manifest_path).expanduser().resolve().parent
    for key, row_value in sorted(dict(manifest.get("artifacts") or {}).items()):
        row = dict(row_value or {})
        raw_path = str(row.get("path") or "")
        if not raw_path:
            raise ValidationError(f"execution artifact {key} is missing path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = base / path
        imported = store.import_artifact(project_id, path, label=f"continuation-{key}")
        expected = str(row.get("raw_sha256") or "")
        if expected and imported["raw_sha256"] != expected:
            raise ValidationError(f"execution artifact {key} hash mismatch")
        imported.update({str(k): deepcopy(v) for k, v in row.items() if k not in {"path", "raw_sha256"}})
        artifacts[str(key)] = imported
    performance = deepcopy(dict(manifest.get("performance") or {}))
    return artifacts, performance


def project_extend_causal_score(
    store: ProjectStore,
    project_id: str,
    *,
    score_path: str | Path,
    midi_path: str | Path,
    evidence_path: str | Path | None = None,
    semantic_annotations: Mapping[str, Any] | None = None,
    execution_manifest_path: str | Path | None = None,
    actor: str = "producer",
    reason: str = "extend causal score",
    expected_head: str | None = None,
) -> dict[str, Any]:
    """Append a causal-score interval while preserving the accepted prefix exactly."""
    project = store.load_project(project_id)
    head = str(project.get("active_revision_sha") or "")
    if expected_head is not None and head != str(expected_head):
        raise ValidationError(f"project head moved: expected {expected_head}, found {head}")
    parent = store.load_revision(project_id, head)
    if str(parent.get("authority_kind") or "") != "causal_score":
        raise ValidationError("continuation requires a causal-score project")
    parent_performance = dict(parent.get("performance") or {})
    if str(parent_performance.get("stage") or "") not in {"semantic_adoption", "source_execution", "production"}:
        raise ValidationError("causal score must be semantically adopted before continuation")

    parent_score_artifact = dict((parent_performance.get("artifacts") or {}).get("score") or {})
    parent_score = _load_json(store.resolve_artifact(project_id, parent_score_artifact), "parent score")
    child_score = _load_json(score_path, "continuation score")
    pair = _verify_score_midi_pair(child_score, midi_path)
    prefix = _prefix_receipt(parent_score, child_score)
    if prefix["parent_note_ledger_sha256"] != str(parent_performance.get("note_ledger_sha256") or ""):
        raise ValidationError("parent revision note-ledger receipt does not match its score")

    parent_duration = float(parent_performance.get("duration_seconds") or 0.0)
    child_duration = float(child_score.get("duration_seconds") or 0.0)
    if child_duration <= parent_duration:
        raise ValidationError("continuation duration must exceed parent duration")

    imported_score = store.import_artifact(project_id, score_path, label="continuation-score")
    imported_score.update({"score_sha256": pair["score_sha256"], "score_family": pair["score_family"]})
    imported_midi = store.import_artifact(project_id, midi_path, label="continuation-midi")
    imported_midi.update({"semantic_sha256": pair["midi_semantic_sha256"]})
    imported_evidence = None
    if evidence_path:
        imported_evidence = store.import_artifact(project_id, evidence_path, label="continuation-evidence")

    execution_artifacts: dict[str, Any] = {}
    execution_performance: dict[str, Any] = {}
    if execution_manifest_path:
        execution_artifacts, execution_performance = _import_execution_manifest(
            store, project_id, execution_manifest_path
        )

    revision = deepcopy(parent)
    revision.pop("revision_sha", None)
    revision.pop("created_at", None)
    revision["parent_revision_sha"] = head
    revision["created_by"] = {"actor": str(actor), "reason": str(reason)}
    revision["tracks"] = _score_track_rows(child_score)
    revision["intent"] = deepcopy(parent["intent"])
    revision["intent"]["target_seconds"] = child_duration
    revision["intent"]["mode"] = "causal_score_continuation"

    performance = deepcopy(parent_performance)
    performance["stage"] = str(execution_performance.pop("stage", performance.get("stage") or "semantic_adoption"))
    if performance["stage"] not in {"semantic_adoption", "source_execution", "production"}:
        raise ValidationError("continuation execution manifest requested an unsupported stage")
    performance["continuation_stage"] = str(
        execution_performance.pop("continuation_stage", "symbolic_continuation")
    )
    performance["duration_seconds"] = child_duration
    performance["score_sha256"] = pair["score_sha256"]
    performance["midi_semantic_sha256"] = pair["midi_semantic_sha256"]
    performance["note_count"] = prefix["child_note_count"]
    performance["note_ledger_sha256"] = prefix["child_note_ledger_sha256"]
    performance["continuation"] = {
        "schema": CONTINUATION_SCHEMA,
        "parent_revision_sha": head,
        "parent_duration_seconds": parent_duration,
        "duration_seconds": child_duration,
        **prefix,
    }
    performance.setdefault("artifacts", {})["score"] = imported_score
    performance["artifacts"]["midi"] = imported_midi
    if imported_evidence is not None:
        performance["artifacts"]["continuation_evidence"] = imported_evidence
    performance["artifacts"].update(execution_artifacts)
    performance.update(execution_performance)
    revision["performance"] = performance

    semantic_state = deepcopy(dict(parent.get("semantic_state") or {}))
    if semantic_annotations:
        semantic_state = _merge_mapping(semantic_state, semantic_annotations)
    semantic_state["continuation"] = {
        "parent_revision_sha": head,
        "parent_duration_seconds": parent_duration,
        "duration_seconds": child_duration,
        "prefix_unchanged": True,
        "added_note_count": prefix["added_note_count"],
    }
    semantic_state["semantic_state_sha256"] = sha256_json(
        {k: v for k, v in semantic_state.items() if k != "semantic_state_sha256"}
    )
    revision["semantic_state"] = semantic_state
    revision.setdefault("decisions", []).append(
        {
            "decision_id": "continuation:" + pair["score_sha256"][:20],
            "kind": "causal_score_continuation",
            "actor": str(actor),
            "reason": str(reason),
            "base_revision_sha": head,
            "prefix_unchanged": True,
            "added_note_count": prefix["added_note_count"],
        }
    )
    revision["compiler_receipt"] = {
        **deepcopy(dict(parent.get("compiler_receipt") or {})),
        "continuation": {
            "schema": CONTINUATION_SCHEMA,
            "score_sha256": pair["score_sha256"],
            "midi_semantic_sha256": pair["midi_semantic_sha256"],
            "prefix": prefix,
            "execution_manifest_raw_sha256": (
                sha256_file(execution_manifest_path) if execution_manifest_path else ""
            ),
        },
    }
    revision["static_gate_receipt"] = {
        "passed": True,
        "gate": "causal_score_continuation",
        "checked_at": now_utc(),
        "prefix_unchanged": True,
        "score_midi_pair_ok": True,
    }
    sealed = seal_revision(revision)
    result = store.commit_revision(
        project_id,
        sealed,
        expected_head=head,
        event="causal_score_continuation_committed",
        event_payload={
            "score_sha256": pair["score_sha256"],
            "midi_semantic_sha256": pair["midi_semantic_sha256"],
            "added_note_count": prefix["added_note_count"],
        },
    )
    verification = project_verify_causal_continuation(store, project_id, sealed["revision_sha"])
    return {"ok": bool(verification["continuation_ok"]), **result, "verification": verification}


def project_verify_causal_continuation(
    store: ProjectStore,
    project_id: str,
    revision_sha: str | None = None,
) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    performance = dict(revision.get("performance") or {})
    continuation = dict(performance.get("continuation") or {})
    if str(continuation.get("schema") or "") != CONTINUATION_SCHEMA:
        raise ValidationError("revision is not a causal-score continuation")
    parent_sha = str(continuation.get("parent_revision_sha") or revision.get("parent_revision_sha") or "")
    parent = store.load_revision(project_id, parent_sha)
    parent_score = _load_json(
        store.resolve_artifact(project_id, parent["performance"]["artifacts"]["score"]),
        "parent score",
    )
    score = _load_json(store.resolve_artifact(project_id, performance["artifacts"]["score"]), "continuation score")
    midi_path = store.resolve_artifact(project_id, performance["artifacts"]["midi"])
    pair = _verify_score_midi_pair(score, midi_path)
    prefix = _prefix_receipt(parent_score, score)
    artifacts = []
    failures = []
    for key, artifact in sorted(dict(performance.get("artifacts") or {}).items()):
        try:
            path = store.resolve_artifact(project_id, artifact)
            artifacts.append({"key": key, "ok": True, "raw_sha256": sha256_file(path)})
        except Exception as exc:
            artifacts.append({"key": key, "ok": False, "error": str(exc)})
            failures.append(f"artifact:{key}")
    checks = {
        "prefix_unchanged": bool(prefix["prefix_unchanged"]),
        "parent_note_ledger_matches_revision": (
            prefix["parent_note_ledger_sha256"] == str(parent["performance"]["note_ledger_sha256"])
        ),
        "child_note_ledger_matches_revision": (
            prefix["child_note_ledger_sha256"] == str(performance.get("note_ledger_sha256") or "")
        ),
        "score_sha_matches_revision": pair["score_sha256"] == str(performance.get("score_sha256") or ""),
        "midi_semantic_matches_revision": (
            pair["midi_semantic_sha256"] == str(performance.get("midi_semantic_sha256") or "")
        ),
        "duration_extended": float(performance.get("duration_seconds") or 0.0)
        > float(parent["performance"].get("duration_seconds") or 0.0),
        "artifacts_verified": not failures,
    }
    return {
        "schema": "earcrate/causal-score-continuation-verification@1",
        "project_id": project_id,
        "revision_sha": revision["revision_sha"],
        "parent_revision_sha": parent_sha,
        "continuation_ok": all(checks.values()),
        "checks": checks,
        "prefix": prefix,
        "score_midi_pair": pair,
        "artifacts": artifacts,
        "failures": failures + [key for key, value in checks.items() if not value],
    }


__all__ = [
    "CONTINUATION_SCHEMA",
    "EXECUTION_MANIFEST_SCHEMA",
    "project_extend_causal_score",
    "project_verify_causal_continuation",
]
