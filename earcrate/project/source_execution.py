from __future__ import annotations

"""Execute an independently registered SourcePhrase inside a causal project.

The comparison reference is alignment evidence only.  Final PCM is assembled
from the current rack floor and the independently supplied source recording.
Every artifact is imported and a child ProjectRevision records the execution.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import soundfile as sf

from earcrate.music.model import music_sha256_json
from earcrate.music.source_phrase import (
    music_extract_reference_vocal_proxy,
    music_mix_source_phrase,
    music_render_source_phrase,
    music_resolve_source_phrase_registration,
    music_validate_source_phrase,
)

from .model import seal_revision
from .store import ProjectStore
from .util import ProjectError, ValidationError, sha256_file

SOURCE_EXECUTION_SCHEMA = "earcrate/project-source-phrase-execution@1"


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"missing {label}: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain a JSON object")
    return value


def _artifact_path(store: ProjectStore, project_id: str, revision: Mapping[str, Any], key: str) -> Path:
    artifact = dict(((revision.get("performance") or {}).get("artifacts") or {}).get(key) or {})
    if not artifact:
        raise ValidationError(f"revision has no performance artifact named {key}")
    return store.resolve_artifact(project_id, artifact)


def _preserve_prefix(
    parent_audition: Path,
    continuation_mix: Path,
    output: Path,
    *,
    prefix_seconds: float,
    total_duration_seconds: float,
) -> dict[str, Any]:
    parent, parent_rate = sf.read(str(parent_audition), always_2d=True, dtype="float32")
    child, child_rate = sf.read(str(continuation_mix), always_2d=True, dtype="float32")
    if parent_rate != child_rate:
        raise ValidationError("parent audition and continuation mix sample rates disagree")
    if parent.shape[1] != child.shape[1]:
        raise ValidationError("parent audition and continuation mix channel counts disagree")
    frames = int(round(float(total_duration_seconds) * parent_rate))
    prefix_frames = int(round(float(prefix_seconds) * parent_rate))
    if prefix_frames < 0 or prefix_frames > frames:
        raise ValidationError("preserved prefix duration is outside the project")
    if parent.shape[0] < prefix_frames or child.shape[0] < frames:
        raise ValidationError("audio inputs do not cover the requested project duration")
    combined = np.concatenate((parent[:prefix_frames], child[prefix_frames:frames]), axis=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), combined, parent_rate, subtype="PCM_16")
    decoded, decoded_rate = sf.read(str(output), always_2d=True, dtype="float32")
    parent_decoded, _ = sf.read(str(parent_audition), always_2d=True, dtype="float32")
    return {
        "sample_rate": int(decoded_rate),
        "channels": int(decoded.shape[1]),
        "frames": int(decoded.shape[0]),
        "duration_seconds": float(decoded.shape[0] / decoded_rate),
        "prefix_seconds": float(prefix_seconds),
        "prefix_pcm_equal": bool(np.array_equal(decoded[:prefix_frames], parent_decoded[:prefix_frames])),
        "seam_peak_delta": float(
            np.max(np.abs(decoded[prefix_frames] - decoded[prefix_frames - 1]))
            if 0 < prefix_frames < decoded.shape[0]
            else 0.0
        ),
        "raw_sha256": sha256_file(output),
    }


def project_execute_registered_source_phrase(
    store: ProjectStore,
    project_id: str,
    *,
    registration_path: str | Path,
    comparison_reference_path: str | Path,
    output_dir: str | Path,
    floor_artifact_key: str = "continuation_rack_floor",
    parent_audition_artifact_key: str = "producer_audition",
    preserve_prefix_seconds: float = 30.0,
    actor: str = "producer",
    reason: str = "execute registered SourcePhrase",
    expected_head: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    project = store.load_project(project_id)
    head = str(project.get("active_revision_sha") or "")
    if expected_head is not None and str(expected_head) != head:
        raise ValidationError(f"project head moved: expected {expected_head}, found {head}")
    revision = store.load_revision(project_id, head)
    if str(revision.get("authority_kind") or "") != "causal_score":
        raise ValidationError("SourcePhrase execution requires a causal-score revision")
    performance = dict(revision.get("performance") or {})
    total_duration = float(performance.get("duration_seconds") or 0.0)
    if total_duration <= 0.0:
        raise ValidationError("project performance duration is missing")

    spec = _load_json(registration_path, "SourcePhrase registration")
    source_path = Path(str(spec.get("source_path") or "")).expanduser().resolve()
    required_source_sha = str(spec.get("required_source_byte_sha256") or "")
    if not source_path.is_file():
        raise ProjectError(f"registered source recording is missing: {source_path}")
    actual_source_sha = sha256_file(source_path)
    if required_source_sha and required_source_sha != actual_source_sha:
        raise ValidationError("registered source recording byte identity changed")

    reference = Path(comparison_reference_path).expanduser().resolve()
    if not reference.is_file():
        raise ProjectError(f"comparison reference is missing: {reference}")
    reference_sha = sha256_file(reference)
    declared_reference_sha = str(spec.get("comparison_reference_sha256") or "")
    if declared_reference_sha and declared_reference_sha != reference_sha:
        raise ValidationError("comparison reference identity does not match registration")
    if actual_source_sha == reference_sha:
        raise ValidationError("comparison reference cannot be executed as the independent source phrase")

    floor = _artifact_path(store, project_id, revision, floor_artifact_key)
    parent_audition = _artifact_path(store, project_id, revision, parent_audition_artifact_key)
    floor_info = sf.info(str(floor))
    parent_info = sf.info(str(parent_audition))
    if int(floor_info.samplerate) != int(parent_info.samplerate):
        raise ValidationError("rack floor and preserved parent audition sample rates disagree")
    execution_sample_rate = int(floor_info.samplerate)

    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite nonempty SourcePhrase output: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    proxy = destination / "comparison-vocal-proxy.wav"
    proxy_receipt = music_extract_reference_vocal_proxy(
        reference,
        proxy,
        duration_seconds=total_duration,
        sample_rate=execution_sample_rate,
        overwrite=overwrite,
    )
    phrase = music_resolve_source_phrase_registration(
        spec,
        comparison_vocal_proxy_path=proxy,
        comparison_reference_sha256=reference_sha,
    )
    music_validate_source_phrase(phrase, verify_source=True)
    phrase_path = destination / "source-phrase.sealed.json"
    phrase_path.write_text(json.dumps(phrase, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stem = destination / "source-phrase.stem.wav"
    execution = music_render_source_phrase(
        phrase,
        stem,
        total_duration_seconds=total_duration,
        sample_rate=execution_sample_rate,
        overwrite=overwrite,
    )
    execution_path = destination / "source-phrase.execution.json"
    execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    mixed = destination / "rack-plus-source-phrase.wav"
    mix = music_mix_source_phrase(
        floor, stem, phrase, mixed, sample_rate=execution_sample_rate, overwrite=overwrite
    )
    mix_path = destination / "source-phrase.mix.json"
    mix_path.write_text(json.dumps(mix, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audition = destination / "GATE8_SOURCE_EXECUTION_PRODUCER_AUDITION.wav"
    audition_receipt = _preserve_prefix(
        parent_audition,
        mixed,
        audition,
        prefix_seconds=float(preserve_prefix_seconds),
        total_duration_seconds=total_duration,
    )
    if not audition_receipt["prefix_pcm_equal"]:
        raise ValidationError("SourcePhrase child changed the preserved parent audition prefix")

    intent_id = str(spec.get("intent_id") or phrase.get("phrase_id") or "source-phrase")
    receipt = {
        "schema": SOURCE_EXECUTION_SCHEMA,
        "ok": True,
        "project_id": project_id,
        "parent_revision_sha": head,
        "intent_id": intent_id,
        "comparison_reference_sha256": reference_sha,
        "comparison_reference_used_in_lowering": False,
        "source_recording_sha256": actual_source_sha,
        "source_phrase_sha256": phrase["phrase_sha256"],
        "source_phrase_execution_sha256": execution["execution_sha256"],
        "mix_sha256": mix["mix_sha256"],
        "audition_sha256": audition_receipt["raw_sha256"],
        "audition": audition_receipt,
        "proxy_receipt": proxy_receipt,
        "rights_status": str(spec.get("rights_status") or "unreviewed"),
        "producer_status": "pending",
        "publication_ok": False,
    }
    receipt["receipt_sha256"] = music_sha256_json(receipt)
    receipt_path = destination / "source-execution.receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    imported = {
        "source_phrase_definition": store.import_artifact(project_id, phrase_path, label=f"{intent_id}-definition"),
        "source_phrase_execution": store.import_artifact(project_id, execution_path, label=f"{intent_id}-execution"),
        "source_phrase_foreground": store.import_artifact(project_id, stem, label=f"{intent_id}-foreground"),
        "source_phrase_mix": store.import_artifact(project_id, mix_path, label=f"{intent_id}-mix"),
        "source_execution_audition": store.import_artifact(project_id, audition, label=f"{intent_id}-audition"),
        "source_execution_receipt": store.import_artifact(project_id, receipt_path, label=f"{intent_id}-receipt"),
    }
    imported["source_phrase_definition"]["phrase_sha256"] = phrase["phrase_sha256"]
    imported["source_phrase_execution"]["execution_sha256"] = execution["execution_sha256"]
    imported["source_execution_audition"]["audition_sha256"] = audition_receipt["raw_sha256"]
    imported["source_execution_receipt"]["receipt_sha256"] = receipt["receipt_sha256"]

    child = deepcopy(revision)
    child.pop("revision_sha", None)
    child.pop("created_at", None)
    child["parent_revision_sha"] = head
    child["created_by"] = {"actor": str(actor), "reason": str(reason)}
    child_performance = child["performance"]
    child_performance["stage"] = "production"
    child_performance["continuation_stage"] = "independent_source_phrase_execution"
    child_performance["artifacts"].update(imported)
    child_performance["producer_audition_sha256"] = audition_receipt["raw_sha256"]
    child_performance["source_execution_complete"] = True
    child_performance["diagnostic_source_execution_complete"] = False
    child_performance["producer_status"] = "pending"
    child_performance["publication_ok"] = False
    child_performance.setdefault("source_phrase_executions", []).append(
        {
            "intent_id": intent_id,
            "phrase_sha256": phrase["phrase_sha256"],
            "execution_sha256": execution["execution_sha256"],
            "source_recording_sha256": actual_source_sha,
            "comparison_reference_used_in_lowering": False,
            "rights_status": receipt["rights_status"],
            "producer_status": "pending",
        }
    )

    semantic = deepcopy(dict(child.get("semantic_state") or {}))
    intents = []
    matched = False
    for row_value in semantic.get("source_phrase_intentions") or []:
        row = deepcopy(dict(row_value))
        if str(row.get("intent_id") or "") == intent_id:
            row.update(
                {
                    "status": "executed_review_pending",
                    "phrase_id": phrase["phrase_id"],
                    "phrase_sha256": phrase["phrase_sha256"],
                    "execution_sha256": execution["execution_sha256"],
                    "source_content_sha256": actual_source_sha,
                    "source_start_seconds": phrase["source_region"]["start_seconds"],
                    "source_end_seconds": phrase["source_region"]["end_seconds"],
                    "publication_eligible": bool(phrase["provenance"]["publication_eligible"]),
                    "rights_status": receipt["rights_status"],
                }
            )
            matched = True
        intents.append(row)
    if not matched:
        intents.append(
            {
                "intent_id": intent_id,
                "identity_label": phrase["identity_label"],
                "role": phrase["source_role"],
                "status": "executed_review_pending",
                "phrase_id": phrase["phrase_id"],
                "phrase_sha256": phrase["phrase_sha256"],
                "execution_sha256": execution["execution_sha256"],
                "source_content_sha256": actual_source_sha,
                "destination_start_seconds": phrase["destination_region"]["start_seconds"],
                "destination_end_seconds": phrase["destination_region"]["end_seconds"],
                "rights_status": receipt["rights_status"],
            }
        )
    semantic["source_phrase_intentions"] = intents
    semantic["semantic_state_sha256"] = music_sha256_json(
        {key: value for key, value in semantic.items() if key != "semantic_state_sha256"}
    )
    child["semantic_state"] = semantic
    child.setdefault("decisions", []).append(
        {
            "decision_id": "source-execution:" + receipt["receipt_sha256"][:20],
            "kind": "independent_source_phrase_execution",
            "actor": str(actor),
            "reason": str(reason),
            "intent_id": intent_id,
            "comparison_reference_used_in_lowering": False,
            "source_phrase_sha256": phrase["phrase_sha256"],
        }
    )
    child["execution_decision"] = {
        "kind": "independent_source_phrase_execution",
        "base_revision_sha": head,
        "intent_id": intent_id,
        "source_phrase_sha256": phrase["phrase_sha256"],
        "execution_sha256": execution["execution_sha256"],
        "producer_verdict": "pending",
        "publication_ok": False,
    }
    sealed = seal_revision(child)
    committed = store.commit_revision(
        project_id,
        sealed,
        expected_head=head,
        event="source_phrase_execution_committed",
        event_payload={
            "intent_id": intent_id,
            "receipt_sha256": receipt["receipt_sha256"],
            "audition_sha256": audition_receipt["raw_sha256"],
        },
    )
    return {
        "ok": True,
        **committed,
        "receipt": receipt,
        "output_dir": str(destination),
        "audition_path": str(audition),
    }


__all__ = ["SOURCE_EXECUTION_SCHEMA", "project_execute_registered_source_phrase"]
