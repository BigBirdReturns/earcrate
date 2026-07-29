from __future__ import annotations

"""Reviewed audio release-candidate authority for the Open Music Evidence Floor.

A renderer or edit provider may construct a candidate. A separate signal evaluator may
qualify machine-checkable delivery properties. Only a human musical review may accept
the music, and a separate rights policy must admit the intended use before the release
gate can open. Passing signal checks is therefore never promoted into aesthetic
approval.
"""

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .model import (
    FLOOR_SCHEMA_VERSION,
    FloorError,
    floor_fraction,
    floor_fraction_value,
    floor_jsonable,
    floor_seal_phrase_contract,
    floor_seal_time_map,
    floor_sha256_json,
)

_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

FLOOR_RELEASE_SIGNAL_STATUSES = ("not_run", "passed", "failed")
FLOOR_RELEASE_REPRO_STATUSES = ("not_run", "passed", "failed")
FLOOR_RELEASE_CUSTODY_STATUSES = ("pending", "passed", "failed")
FLOOR_RELEASE_RECURRENCE_STATUSES = ("not_evaluated", "passed", "failed")
FLOOR_RELEASE_TRANSITION_STATUSES = ("not_evaluated", "provisional_pass", "passed", "failed")
FLOOR_RELEASE_HUMAN_VERDICTS = ("pending", "accept", "revise", "reject")
FLOOR_RELEASE_RIGHTS_STATUSES = ("not_evaluated", "accepted_by_policy", "blocked", "expired")
FLOOR_RELEASE_STATUSES = ("blocked", "approved", "rejected")
FLOOR_RELEASE_SUMMARIES = (
    "candidate_unqualified",
    "signal_failed",
    "signal_sane_human_review_pending",
    "human_revision_requested",
    "human_rejected",
    "rights_review_pending",
    "rights_blocked",
    "release_approved",
)
FLOOR_EDIT_OPERATIONS = (
    "source_seek",
    "source_copy",
    "gain",
    "equal_power_crossfade",
    "linear_crossfade",
    "tempo",
    "transpose",
    "slice",
    "loop",
    "reverse",
    "synthesis",
    "midi_overlay",
    "stem_layering",
    "filter",
    "filtered_intro",
    "beat_chopping",
    "silent_preroll",
)


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FloorError(f"{field} must be nonempty")
    return text


def _sha(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip().lower()
    if optional and not text:
        return None
    if not _RELEASE_SHA_RE.fullmatch(text):
        raise FloorError(f"{field} must be a lowercase SHA-256")
    return text


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise FloorError(f"{field} must be an integer") from exc
    if number < minimum:
        raise FloorError(f"{field} must be >= {minimum}")
    return number


def _number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FloorError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise FloorError(f"{field} must be finite")
    if minimum is not None and number < minimum:
        raise FloorError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise FloorError(f"{field} must be <= {maximum}")
    return number


def _check_hash(raw: Mapping[str, Any], sealed: Mapping[str, Any], field: str, label: str) -> None:
    supplied = str(raw.get(field) or "")
    if supplied and supplied != str(sealed.get(field) or ""):
        raise FloorError(f"{field} does not match {label}")


def _identity(raw: Mapping[str, Any], field: str, *, require_manifest: bool = False) -> dict[str, Any]:
    identity = {
        "identity_id": _text(raw.get("identity_id") or raw.get("provider_id") or raw.get("reviewer_id") or raw.get("evaluator_id"), f"{field} identity_id"),
        "identity_type": _text(raw.get("identity_type") or "provider", f"{field} identity_type"),
        "version": str(raw.get("version") or raw.get("provider_version") or ""),
        "manifest_sha256": _sha(raw.get("manifest_sha256"), f"{field} manifest_sha256", optional=not require_manifest),
        "display_name": str(raw.get("display_name") or ""),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    return identity


def _artifact(raw: Mapping[str, Any], field: str, *, require_pcm: bool = False) -> dict[str, Any]:
    artifact = {
        "artifact_id": _text(raw.get("artifact_id"), f"{field} artifact_id"),
        "sha256": _sha(raw.get("sha256"), f"{field} sha256"),
        "decoded_pcm_sha256": _sha(raw.get("decoded_pcm_sha256"), f"{field} decoded_pcm_sha256", optional=not require_pcm),
        "media_kind": _text(raw.get("media_kind"), f"{field} media_kind"),
        "size_bytes": _integer(raw.get("size_bytes", 0), f"{field} size_bytes", minimum=0),
        "sample_rate": _integer(raw.get("sample_rate", 0), f"{field} sample_rate", minimum=0),
        "channels": _integer(raw.get("channels", 0), f"{field} channels", minimum=0),
        "frames": _integer(raw.get("frames", 0), f"{field} frames", minimum=0),
        "role": str(raw.get("role") or ""),
        "path": str(raw.get("path") or ""),
        "uri": str(raw.get("uri") or ""),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if require_pcm and (artifact["sample_rate"] <= 0 or artifact["channels"] <= 0 or artifact["frames"] <= 0):
        raise FloorError(f"{field} requires positive sample_rate, channels, and frames")
    return artifact


def _artifact_semantic(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in {"path", "uri"}}


def floor_seal_audio_edit_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a sample-accurate edit plan without granting musical approval."""
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported AudioEditPlan schema")
    if str(raw.get("kind") or "earcrate_floor_audio_edit_plan") != "earcrate_floor_audio_edit_plan":
        raise FloorError("unsupported AudioEditPlan kind")

    sample_rate = _integer(raw.get("sample_rate"), "AudioEditPlan sample_rate", minimum=1)
    channels = _integer(raw.get("channels"), "AudioEditPlan channels", minimum=1)
    output_frames = _integer(raw.get("output_frames"), "AudioEditPlan output_frames", minimum=1)

    sources = []
    source_ids: set[str] = set()
    for index, item in enumerate(raw.get("source_artifacts") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"AudioEditPlan source artifact {index} must be an object")
        row = _artifact(item, f"AudioEditPlan source artifact {index}")
        if row["artifact_id"] in source_ids:
            raise FloorError(f"duplicate AudioEditPlan source artifact {row['artifact_id']}")
        source_ids.add(row["artifact_id"])
        sources.append(row)
    if not sources:
        raise FloorError("AudioEditPlan requires at least one source artifact")

    segments = []
    segment_ids: set[str] = set()
    for index, item in enumerate(raw.get("segments") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"AudioEditPlan segment {index} must be an object")
        segment_id = str(item.get("segment_id") or f"segment_{index:04d}")
        if segment_id in segment_ids:
            raise FloorError(f"duplicate AudioEditPlan segment_id {segment_id}")
        segment_ids.add(segment_id)
        source_id = _text(item.get("source_artifact_id"), f"AudioEditPlan segment {index} source_artifact_id")
        if source_id not in source_ids:
            raise FloorError(f"AudioEditPlan segment {segment_id} references unknown source {source_id}")
        output_start = _integer(item.get("output_start_frame"), f"segment {segment_id} output_start_frame")
        output_end = _integer(item.get("output_end_frame"), f"segment {segment_id} output_end_frame", minimum=1)
        source_start = _integer(item.get("source_start_frame"), f"segment {segment_id} source_start_frame")
        source_end = _integer(item.get("source_end_frame"), f"segment {segment_id} source_end_frame", minimum=1)
        if output_end <= output_start or source_end <= source_start:
            raise FloorError(f"AudioEditPlan segment {segment_id} intervals must be positive")
        if output_end > output_frames:
            raise FloorError(f"AudioEditPlan segment {segment_id} escapes output_frames")
        operation = str(item.get("operation") or "source_copy")
        if operation not in FLOOR_EDIT_OPERATIONS:
            raise FloorError(f"AudioEditPlan segment {segment_id} has unsupported operation {operation!r}")
        segments.append(
            {
                "segment_id": segment_id,
                "output_start_frame": output_start,
                "output_end_frame": output_end,
                "source_artifact_id": source_id,
                "source_start_frame": source_start,
                "source_end_frame": source_end,
                "operation": operation,
                "gain_db": _number(item.get("gain_db", 0.0), f"segment {segment_id} gain_db"),
                "metadata": deepcopy(dict(item.get("metadata") or {})),
            }
        )
    if not segments:
        raise FloorError("AudioEditPlan requires segments")
    segments.sort(key=lambda row: (row["output_start_frame"], row["output_end_frame"], row["segment_id"]))
    if segments[0]["output_start_frame"] != 0 or max(row["output_end_frame"] for row in segments) != output_frames:
        raise FloorError("AudioEditPlan segments must begin at frame zero and reach output_frames")

    transitions = []
    transition_ids: set[str] = set()
    for index, item in enumerate(raw.get("transitions") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"AudioEditPlan transition {index} must be an object")
        transition_id = str(item.get("transition_id") or f"transition_{index:04d}")
        if transition_id in transition_ids:
            raise FloorError(f"duplicate AudioEditPlan transition_id {transition_id}")
        transition_ids.add(transition_id)
        left = _text(item.get("left_segment_id"), f"transition {transition_id} left_segment_id")
        right = _text(item.get("right_segment_id"), f"transition {transition_id} right_segment_id")
        if left not in segment_ids or right not in segment_ids or left == right:
            raise FloorError(f"transition {transition_id} references invalid segments")
        operation = str(item.get("operation") or "equal_power_crossfade")
        if operation not in {"equal_power_crossfade", "linear_crossfade", "hard_cut"}:
            raise FloorError(f"transition {transition_id} has unsupported operation {operation!r}")
        overlap = _integer(item.get("overlap_frames", 0), f"transition {transition_id} overlap_frames", minimum=0)
        if operation == "hard_cut" and overlap != 0:
            raise FloorError(f"hard-cut transition {transition_id} may not overlap")
        if operation != "hard_cut" and overlap <= 0:
            raise FloorError(f"crossfade transition {transition_id} requires overlap_frames")
        left_row = next(row for row in segments if row["segment_id"] == left)
        right_row = next(row for row in segments if row["segment_id"] == right)
        if right_row["output_start_frame"] != left_row["output_end_frame"] - overlap:
            raise FloorError(f"transition {transition_id} overlap disagrees with segment placement")
        transitions.append(
            {
                "transition_id": transition_id,
                "left_segment_id": left,
                "right_segment_id": right,
                "operation": operation,
                "overlap_frames": overlap,
                "curve": str(item.get("curve") or ("equal_power" if operation == "equal_power_crossfade" else "linear")),
                "metadata": deepcopy(dict(item.get("metadata") or {})),
            }
        )

    # The segment chain must cover the output exactly. Any overlap must be declared
    # by one transition between the adjacent segments; undeclared overlap and gaps are
    # both custody failures because they leave sample ownership ambiguous.
    transition_map = {
        (row["left_segment_id"], row["right_segment_id"]): row
        for row in transitions
    }
    for left_row, right_row in zip(segments, segments[1:]):
        if right_row["output_start_frame"] > left_row["output_end_frame"]:
            raise FloorError(
                f"AudioEditPlan contains an uncovered output gap between {left_row['segment_id']} and {right_row['segment_id']}"
            )
        overlap = left_row["output_end_frame"] - right_row["output_start_frame"]
        transition = transition_map.get((left_row["segment_id"], right_row["segment_id"]))
        if overlap > 0:
            if transition is None or transition["overlap_frames"] != overlap:
                raise FloorError("AudioEditPlan overlap is not accounted for by an exact adjacent transition")
        elif transition is not None and transition["overlap_frames"] != 0:
            raise FloorError("AudioEditPlan transition declares overlap where segments do not overlap")

    declared = sorted({str(item) for item in raw.get("declared_operations") or []})
    prohibited = sorted({str(item) for item in raw.get("prohibited_operations") or []})
    unknown = sorted((set(declared) | set(prohibited)) - set(FLOOR_EDIT_OPERATIONS))
    if unknown:
        raise FloorError(f"AudioEditPlan contains unknown operations: {unknown}")
    conflict = sorted(set(declared) & set(prohibited))
    if conflict:
        raise FloorError(f"AudioEditPlan declares and prohibits the same operations: {conflict}")
    used = {row["operation"] for row in segments} | {row["operation"] for row in transitions}
    undeclared = sorted(used - set(declared))
    if undeclared:
        raise FloorError(f"AudioEditPlan uses undeclared operations: {undeclared}")
    if used & set(prohibited):
        raise FloorError("AudioEditPlan uses a prohibited operation")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_audio_edit_plan",
        "sample_rate": sample_rate,
        "channels": channels,
        "output_frames": output_frames,
        "source_artifacts": sources,
        "segments": segments,
        "transitions": transitions,
        "declared_operations": declared,
        "prohibited_operations": prohibited,
        "source_only": bool(raw.get("source_only", False)),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if out["source_only"] and any(operation in used for operation in {"synthesis", "midi_overlay", "stem_layering"}):
        raise FloorError("source-only AudioEditPlan may not synthesize or layer undeclared sources")
    semantic = deepcopy(out)
    for artifact in semantic["source_artifacts"]:
        artifact.pop("path", None)
        artifact.pop("uri", None)
    out["edit_plan_sha256"] = floor_sha256_json(semantic)
    out["edit_plan_id"] = str(raw.get("edit_plan_id") or "floor_edit_plan_" + out["edit_plan_sha256"][:24])
    semantic = deepcopy(out)
    semantic.pop("edit_plan_sha256", None)
    for artifact in semantic["source_artifacts"]:
        artifact.pop("path", None)
        artifact.pop("uri", None)
    out["edit_plan_sha256"] = floor_sha256_json(semantic)
    _check_hash(raw, out, "edit_plan_sha256", "AudioEditPlan")
    return out


def floor_seal_release_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a candidate whose builder has no authority to approve its own music."""
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported ReleaseCandidate schema")
    if str(raw.get("kind") or "earcrate_floor_release_candidate") != "earcrate_floor_release_candidate":
        raise FloorError("unsupported ReleaseCandidate kind")

    builder = _identity(raw.get("builder") or {}, "ReleaseCandidate builder")
    edit_plan = floor_seal_audio_edit_plan(raw.get("audio_edit_plan") or {})
    time_map = floor_seal_time_map(raw.get("time_map") or {})
    contracts = [floor_seal_phrase_contract(item) for item in raw.get("phrase_contracts") or []]
    if not contracts:
        raise FloorError("ReleaseCandidate requires at least one PhraseContract")

    output = _artifact(raw.get("authoritative_output") or {}, "ReleaseCandidate authoritative_output", require_pcm=True)
    if output["sample_rate"] != edit_plan["sample_rate"] or output["channels"] != edit_plan["channels"] or output["frames"] != edit_plan["output_frames"]:
        raise FloorError("ReleaseCandidate authoritative output disagrees with AudioEditPlan shape")
    deliveries = []
    for index, item in enumerate(raw.get("delivery_artifacts") or []):
        deliveries.append(_artifact(item, f"ReleaseCandidate delivery artifact {index}"))

    evidence_branch = str(raw.get("evidence_branch") or "audio")
    evidence_tier = str(raw.get("evidence_tier") or "blind_audio_inference")
    if evidence_branch not in {"audio", "performance", "review"}:
        raise FloorError("ReleaseCandidate evidence_branch must be audio, performance, or review")
    if evidence_tier not in {"blind_audio_inference", "performance_realization", "human_review"}:
        raise FloorError("ReleaseCandidate evidence_tier is not a release-candidate evidence tier")

    declared_status = dict(raw.get("status") or {})
    if declared_status.get("musical_acceptance") not in {None, "pending"}:
        raise FloorError("candidate builder may not declare musical acceptance")
    if declared_status.get("release_status") not in {None, "blocked"}:
        raise FloorError("candidate builder may not open its own release gate")
    if raw.get("builder_may_not_approve_music") is False:
        raise FloorError("ReleaseCandidate must retain the builder approval prohibition")

    initial_status = {
        "custody": str(declared_status.get("custody") or "pending"),
        "build_reproducibility": str(declared_status.get("build_reproducibility") or "not_run"),
        "signal_sanity": str(declared_status.get("signal_sanity") or "not_run"),
        "recurrence_identity": str(declared_status.get("recurrence_identity") or "not_evaluated"),
        "transition_integrity": str(declared_status.get("transition_integrity") or "not_evaluated"),
        "musical_acceptance": "pending",
        "rights_eligibility": str(declared_status.get("rights_eligibility") or "not_evaluated"),
        "whole_organism_status": "not_claimed",
        "release_status": "blocked",
        "summary": str(declared_status.get("summary") or "candidate_unqualified"),
    }
    if initial_status["custody"] not in FLOOR_RELEASE_CUSTODY_STATUSES:
        raise FloorError("ReleaseCandidate custody status is invalid")
    if initial_status["build_reproducibility"] not in FLOOR_RELEASE_REPRO_STATUSES:
        raise FloorError("ReleaseCandidate reproducibility status is invalid")
    if initial_status["signal_sanity"] not in FLOOR_RELEASE_SIGNAL_STATUSES:
        raise FloorError("ReleaseCandidate signal status is invalid")
    if initial_status["recurrence_identity"] not in FLOOR_RELEASE_RECURRENCE_STATUSES:
        raise FloorError("ReleaseCandidate recurrence status is invalid")
    if initial_status["transition_integrity"] not in FLOOR_RELEASE_TRANSITION_STATUSES:
        raise FloorError("ReleaseCandidate transition status is invalid")
    if initial_status["rights_eligibility"] not in FLOOR_RELEASE_RIGHTS_STATUSES:
        raise FloorError("ReleaseCandidate rights status is invalid")
    if initial_status["summary"] not in FLOOR_RELEASE_SUMMARIES:
        raise FloorError("ReleaseCandidate summary is invalid")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_candidate",
        "candidate_id": str(raw.get("candidate_id") or ""),
        "title": _text(raw.get("title"), "ReleaseCandidate title"),
        "builder": builder,
        "evidence_branch": evidence_branch,
        "evidence_tier": evidence_tier,
        "source_evidence_refs": sorted({str(item) for item in raw.get("source_evidence_refs") or []}),
        "audio_edit_plan": edit_plan,
        "time_map": time_map,
        "phrase_contracts": contracts,
        "authoritative_output": output,
        "delivery_artifacts": deliveries,
        "status": initial_status,
        "builder_may_not_approve_music": True,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    semantic = deepcopy(out)
    semantic["authoritative_output"] = _artifact_semantic(semantic["authoritative_output"])
    semantic["delivery_artifacts"] = [_artifact_semantic(item) for item in semantic["delivery_artifacts"]]
    semantic["audio_edit_plan"]["source_artifacts"] = [_artifact_semantic(item) for item in semantic["audio_edit_plan"]["source_artifacts"]]
    out["candidate_sha256"] = floor_sha256_json(semantic)
    if not out["candidate_id"]:
        out["candidate_id"] = "release_candidate_" + out["candidate_sha256"][:24]
        semantic = deepcopy(out)
        semantic.pop("candidate_sha256", None)
        semantic["authoritative_output"] = _artifact_semantic(semantic["authoritative_output"])
        semantic["delivery_artifacts"] = [_artifact_semantic(item) for item in semantic["delivery_artifacts"]]
        semantic["audio_edit_plan"]["source_artifacts"] = [_artifact_semantic(item) for item in semantic["audio_edit_plan"]["source_artifacts"]]
        out["candidate_sha256"] = floor_sha256_json(semantic)
    _check_hash(raw, out, "candidate_sha256", "ReleaseCandidate")
    return out


def floor_seal_signal_evaluation(value: Mapping[str, Any], candidate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported SignalEvaluation schema")
    if str(raw.get("kind") or "earcrate_floor_signal_evaluation") != "earcrate_floor_signal_evaluation":
        raise FloorError("unsupported SignalEvaluation kind")
    sealed_candidate = floor_seal_release_candidate(candidate) if candidate is not None else None
    candidate_sha = _sha(raw.get("candidate_sha256"), "SignalEvaluation candidate_sha256")
    if sealed_candidate is not None and candidate_sha != sealed_candidate["candidate_sha256"]:
        raise FloorError("SignalEvaluation belongs to another ReleaseCandidate")
    evaluator = _identity(raw.get("evaluator") or {}, "SignalEvaluation evaluator")
    builder_id = _text(raw.get("builder_identity_id") or (sealed_candidate or {}).get("builder", {}).get("identity_id"), "SignalEvaluation builder_identity_id")
    if evaluator["identity_id"] == builder_id:
        raise FloorError("candidate builder may not act as the independent signal evaluator")

    metrics = {}
    for key, item in dict(raw.get("metrics") or {}).items():
        metrics[str(key)] = _number(item, f"SignalEvaluation metric {key}")
    if not metrics:
        raise FloorError("SignalEvaluation requires metrics")
    gates = []
    for index, item in enumerate(raw.get("gates") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"SignalEvaluation gate {index} must be an object")
        gates.append(
            {
                "gate_id": _text(item.get("gate_id"), f"SignalEvaluation gate {index} gate_id"),
                "metric": _text(item.get("metric"), f"SignalEvaluation gate {index} metric"),
                "operator": _text(item.get("operator"), f"SignalEvaluation gate {index} operator"),
                "threshold": floor_jsonable(item.get("threshold")),
                "measured": floor_jsonable(item.get("measured")),
                "passed": bool(item.get("passed", False)),
                "metadata": deepcopy(dict(item.get("metadata") or {})),
            }
        )
    if not gates:
        raise FloorError("SignalEvaluation requires gates")
    passed = bool(raw.get("passed", all(row["passed"] for row in gates)))
    if passed != all(row["passed"] for row in gates):
        raise FloorError("SignalEvaluation passed flag disagrees with gate results")
    status = str(raw.get("status") or ("passed" if passed else "failed"))
    if status not in {"passed", "failed"} or (status == "passed") != passed:
        raise FloorError("SignalEvaluation status disagrees with gate results")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_signal_evaluation",
        "candidate_sha256": candidate_sha,
        "builder_identity_id": builder_id,
        "evaluator": evaluator,
        "metrics": metrics,
        "gates": gates,
        "passed": passed,
        "status": status,
        "recurrence_identity": str(raw.get("recurrence_identity") or "not_evaluated"),
        "transition_integrity": str(raw.get("transition_integrity") or "not_evaluated"),
        "notes": deepcopy(list(raw.get("notes") or [])),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if out["recurrence_identity"] not in FLOOR_RELEASE_RECURRENCE_STATUSES:
        raise FloorError("SignalEvaluation recurrence_identity is invalid")
    if out["transition_integrity"] not in FLOOR_RELEASE_TRANSITION_STATUSES:
        raise FloorError("SignalEvaluation transition_integrity is invalid")
    out["signal_evaluation_sha256"] = floor_sha256_json(out)
    _check_hash(raw, out, "signal_evaluation_sha256", "SignalEvaluation")
    return out


def floor_seal_human_musical_review(value: Mapping[str, Any], candidate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported HumanMusicalReview schema")
    if str(raw.get("kind") or "earcrate_floor_human_musical_review") != "earcrate_floor_human_musical_review":
        raise FloorError("unsupported HumanMusicalReview kind")
    sealed_candidate = floor_seal_release_candidate(candidate) if candidate is not None else None
    candidate_sha = _sha(raw.get("candidate_sha256"), "HumanMusicalReview candidate_sha256")
    if sealed_candidate is not None and candidate_sha != sealed_candidate["candidate_sha256"]:
        raise FloorError("HumanMusicalReview belongs to another ReleaseCandidate")
    reviewer_raw = dict(raw.get("reviewer") or {})
    reviewer = _identity(
        {
            "identity_id": reviewer_raw.get("reviewer_id") or reviewer_raw.get("identity_id"),
            "identity_type": reviewer_raw.get("reviewer_type") or reviewer_raw.get("identity_type") or "human",
            "version": reviewer_raw.get("version") or "",
            "manifest_sha256": reviewer_raw.get("manifest_sha256"),
            "display_name": reviewer_raw.get("display_name") or "",
            "metadata": reviewer_raw.get("metadata") or {},
        },
        "HumanMusicalReview reviewer",
    )
    if reviewer["identity_type"] != "human":
        raise FloorError("musical acceptance requires a human reviewer identity")
    if bool(raw.get("machine_generated", False)):
        raise FloorError("machine-generated output may not masquerade as human musical review")
    if sealed_candidate is not None and reviewer["identity_id"] == sealed_candidate["builder"]["identity_id"]:
        raise FloorError("candidate builder may not self-approve as the human reviewer")
    verdict = str(raw.get("verdict") or "pending")
    if verdict not in FLOOR_RELEASE_HUMAN_VERDICTS:
        raise FloorError(f"unsupported human musical verdict {verdict!r}")
    dimensions = {}
    for key, item in dict(raw.get("dimensions") or {}).items():
        dimensions[str(key)] = _number(item, f"HumanMusicalReview dimension {key}", minimum=0.0, maximum=1.0)
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_human_musical_review",
        "candidate_sha256": candidate_sha,
        "reviewer": {
            "reviewer_id": reviewer["identity_id"],
            "reviewer_type": "human",
            "display_name": reviewer["display_name"],
            "metadata": reviewer["metadata"],
        },
        "verdict": verdict,
        "dimensions": dimensions,
        "notes": deepcopy(list(raw.get("notes") or [])),
        "review_patch_refs": sorted({str(item) for item in raw.get("review_patch_refs") or []}),
        "listening_context": deepcopy(dict(raw.get("listening_context") or {})),
        "machine_generated": False,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if verdict == "revise" and not out["review_patch_refs"] and not out["notes"]:
        raise FloorError("revision verdict requires review notes or a ReviewPatch reference")
    out["human_review_sha256"] = floor_sha256_json(out)
    _check_hash(raw, out, "human_review_sha256", "HumanMusicalReview")
    return out


def floor_release_review_template(candidate: Mapping[str, Any], reviewer_id: str = "unassigned-human-reviewer") -> dict[str, Any]:
    sealed = floor_seal_release_candidate(candidate)
    return floor_seal_human_musical_review(
        {
            "candidate_sha256": sealed["candidate_sha256"],
            "reviewer": {
                "reviewer_id": reviewer_id,
                "reviewer_type": "human",
                "display_name": "",
            },
            "verdict": "pending",
            "dimensions": {},
            "notes": [],
            "review_patch_refs": [],
            "listening_context": {},
            "machine_generated": False,
            "metadata": {"template": True},
        },
        sealed,
    )


def _status_record(value: Mapping[str, Any] | None, *, field: str, allowed: Sequence[str], default: str) -> dict[str, Any]:
    raw = dict(value or {})
    status = str(raw.get("status") or default)
    if status not in set(allowed):
        raise FloorError(f"{field} status is invalid: {status!r}")
    return {
        "status": status,
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "checks": deepcopy(dict(raw.get("checks") or {})),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def floor_seal_release_gate_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a release gate independently of the builder that assembled it."""
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported ReleaseGateReceipt schema")
    if str(raw.get("kind") or "earcrate_floor_release_gate_receipt") != "earcrate_floor_release_gate_receipt":
        raise FloorError("unsupported ReleaseGateReceipt kind")

    status = deepcopy(dict(raw.get("status") or {}))
    required_status = {
        "custody": FLOOR_RELEASE_CUSTODY_STATUSES,
        "build_reproducibility": FLOOR_RELEASE_REPRO_STATUSES,
        "signal_sanity": FLOOR_RELEASE_SIGNAL_STATUSES,
        "recurrence_identity": FLOOR_RELEASE_RECURRENCE_STATUSES,
        "transition_integrity": FLOOR_RELEASE_TRANSITION_STATUSES,
        "musical_acceptance": FLOOR_RELEASE_HUMAN_VERDICTS,
        "rights_eligibility": FLOOR_RELEASE_RIGHTS_STATUSES,
        "release_status": FLOOR_RELEASE_STATUSES,
        "summary": FLOOR_RELEASE_SUMMARIES,
    }
    for field, allowed in required_status.items():
        current = str(status.get(field) or "")
        if current not in set(allowed):
            raise FloorError(f"ReleaseGateReceipt status.{field} is invalid: {current!r}")
        status[field] = current
    if str(status.get("whole_organism_status") or "") != "not_claimed":
        raise FloorError("ReleaseGateReceipt may not imply whole-organism passage")
    status["whole_organism_status"] = "not_claimed"

    custody = deepcopy(dict(raw.get("custody") or {}))
    reproducibility = deepcopy(dict(raw.get("reproducibility") or {}))
    rights = deepcopy(dict(raw.get("rights") or {}))
    if str(custody.get("status") or "") != status["custody"]:
        raise FloorError("ReleaseGateReceipt custody disagrees with status vector")
    if str(reproducibility.get("status") or "") != status["build_reproducibility"]:
        raise FloorError("ReleaseGateReceipt reproducibility disagrees with status vector")
    if str(rights.get("status") or "") != status["rights_eligibility"]:
        raise FloorError("ReleaseGateReceipt rights disagree with status vector")
    if bool(rights.get("legal_determination", False)):
        raise FloorError("ReleaseGateReceipt rights may not claim a legal determination")

    blockers = [str(item) for item in raw.get("blockers") or []]
    failures = [str(item) for item in raw.get("failures") or []]
    release_allowed = bool(raw.get("release_allowed", False))
    approval_conditions = bool(
        status["custody"] == "passed"
        and status["build_reproducibility"] == "passed"
        and status["signal_sanity"] == "passed"
        and status["musical_acceptance"] == "accept"
        and status["rights_eligibility"] == "accepted_by_policy"
        and status["release_status"] == "approved"
        and status["summary"] == "release_approved"
        and not blockers
        and not failures
    )
    if release_allowed != approval_conditions:
        raise FloorError("ReleaseGateReceipt release_allowed disagrees with mandatory promotion conditions")
    if status["release_status"] == "approved" and not release_allowed:
        raise FloorError("ReleaseGateReceipt marks an unapproved candidate approved")
    if status["release_status"] == "rejected" and not failures:
        raise FloorError("rejected ReleaseGateReceipt requires at least one failure")
    if status["release_status"] == "blocked" and not blockers and not failures:
        raise FloorError("blocked ReleaseGateReceipt requires a blocker or failure")
    if bool(raw.get("builder_self_approval_refused", False)) is not True:
        raise FloorError("ReleaseGateReceipt must refuse builder self-approval")
    if bool(raw.get("signal_evaluation_is_musical_acceptance", True)) is not False:
        raise FloorError("ReleaseGateReceipt may not treat signal evaluation as musical acceptance")
    if bool(raw.get("whole_organism_passed", True)) is not False:
        raise FloorError("ReleaseGateReceipt may not claim whole-organism passage")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_gate_receipt",
        "candidate_sha256": _sha(raw.get("candidate_sha256"), "ReleaseGateReceipt candidate_sha256"),
        "candidate_id": _text(raw.get("candidate_id"), "ReleaseGateReceipt candidate_id"),
        "selected_signal_evaluation_sha256": _sha(
            raw.get("selected_signal_evaluation_sha256"),
            "ReleaseGateReceipt selected_signal_evaluation_sha256",
            optional=True,
        ),
        "selected_human_review_sha256": _sha(
            raw.get("selected_human_review_sha256"),
            "ReleaseGateReceipt selected_human_review_sha256",
            optional=True,
        ),
        "custody": custody,
        "reproducibility": reproducibility,
        "rights": rights,
        "status": status,
        "release_allowed": release_allowed,
        "blockers": blockers,
        "failures": failures,
        "builder_self_approval_refused": True,
        "signal_evaluation_is_musical_acceptance": False,
        "whole_organism_passed": False,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if out["status"]["signal_sanity"] == "passed" and out["selected_signal_evaluation_sha256"] is None:
        raise FloorError("passed signal status requires a selected SignalEvaluation")
    if out["status"]["musical_acceptance"] != "pending" and out["selected_human_review_sha256"] is None:
        raise FloorError("non-pending musical acceptance requires a selected HumanMusicalReview")
    out["release_gate_sha256"] = floor_sha256_json(out)
    _check_hash(raw, out, "release_gate_sha256", "ReleaseGateReceipt")
    return out


def floor_build_release_gate(
    candidate: Mapping[str, Any],
    *,
    signal_evaluations: Sequence[Mapping[str, Any]] = (),
    human_reviews: Sequence[Mapping[str, Any]] = (),
    custody: Mapping[str, Any] | None = None,
    reproducibility: Mapping[str, Any] | None = None,
    rights: Mapping[str, Any] | None = None,
    selected_signal_evaluation_sha256: str | None = None,
    selected_human_review_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the release gate. No input object alone can open it."""
    sealed_candidate = floor_seal_release_candidate(candidate)
    signals = [floor_seal_signal_evaluation(item, sealed_candidate) for item in signal_evaluations]
    reviews = [floor_seal_human_musical_review(item, sealed_candidate) for item in human_reviews]

    selected_signal = None
    if selected_signal_evaluation_sha256:
        selected_signal = next((row for row in signals if row["signal_evaluation_sha256"] == selected_signal_evaluation_sha256), None)
        if selected_signal is None:
            raise FloorError("selected signal evaluation is not present")
    elif signals:
        selected_signal = signals[-1]

    selected_review = None
    if selected_human_review_sha256:
        selected_review = next((row for row in reviews if row["human_review_sha256"] == selected_human_review_sha256), None)
        if selected_review is None:
            raise FloorError("selected human review is not present")
    elif reviews:
        selected_review = reviews[-1]

    custody_row = _status_record(custody, field="custody", allowed=FLOOR_RELEASE_CUSTODY_STATUSES, default="pending")
    repro_row = _status_record(reproducibility, field="reproducibility", allowed=FLOOR_RELEASE_REPRO_STATUSES, default="not_run")
    rights_row = _status_record(rights, field="rights", allowed=FLOOR_RELEASE_RIGHTS_STATUSES, default="not_evaluated")
    rights_value = dict(rights or {})
    rights_decision = {
        "status": rights_row["status"],
        "policy_id": str(rights_value.get("policy_id") or ""),
        "decided_by": str(rights_value.get("decided_by") or ""),
        "legal_determination": bool(rights_value.get("legal_determination", False)),
        "evidence_refs": rights_row["evidence_refs"],
        "checks": rights_row["checks"],
        "metadata": rights_row["metadata"],
    }
    if rights_decision["legal_determination"]:
        raise FloorError("release rights policy may not claim a legal determination")
    if rights_decision["status"] == "accepted_by_policy" and (not rights_decision["policy_id"] or not rights_decision["decided_by"]):
        raise FloorError("accepted rights status requires policy_id and decided_by")

    signal_status = "not_run" if selected_signal is None else selected_signal["status"]
    recurrence_status = "not_evaluated" if selected_signal is None else selected_signal["recurrence_identity"]
    transition_status = "not_evaluated" if selected_signal is None else selected_signal["transition_integrity"]
    human_status = "pending" if selected_review is None else selected_review["verdict"]

    blockers = []
    failures = []
    if custody_row["status"] != "passed":
        (failures if custody_row["status"] == "failed" else blockers).append("exact candidate custody has not passed")
    if repro_row["status"] != "passed":
        (failures if repro_row["status"] == "failed" else blockers).append("clean-build reproducibility has not passed")
    if signal_status != "passed":
        (failures if signal_status == "failed" else blockers).append("independent signal evaluation has not passed")
    if human_status != "accept":
        if human_status == "reject":
            failures.append("human musical review rejected the candidate")
        elif human_status == "revise":
            blockers.append("human musical review requested revision")
        else:
            blockers.append("human musical acceptance is pending")
    if rights_decision["status"] != "accepted_by_policy":
        if rights_decision["status"] in {"blocked", "expired"}:
            failures.append("rights policy blocks the intended release")
        else:
            blockers.append("rights eligibility has not been accepted by policy")

    release_allowed = not blockers and not failures
    if failures:
        release_status = "rejected"
    elif release_allowed:
        release_status = "approved"
    else:
        release_status = "blocked"

    if signal_status == "failed":
        summary = "signal_failed"
    elif human_status == "reject":
        summary = "human_rejected"
    elif human_status == "revise":
        summary = "human_revision_requested"
    elif signal_status == "passed" and human_status == "pending":
        summary = "signal_sane_human_review_pending"
    elif signal_status == "passed" and human_status == "accept" and rights_decision["status"] == "not_evaluated":
        summary = "rights_review_pending"
    elif rights_decision["status"] in {"blocked", "expired"}:
        summary = "rights_blocked"
    elif release_allowed:
        summary = "release_approved"
    else:
        summary = "candidate_unqualified"

    status_vector = {
        "custody": custody_row["status"],
        "build_reproducibility": repro_row["status"],
        "signal_sanity": signal_status,
        "recurrence_identity": recurrence_status,
        "transition_integrity": transition_status,
        "musical_acceptance": human_status,
        "rights_eligibility": rights_decision["status"],
        "whole_organism_status": "not_claimed",
        "release_status": release_status,
        "summary": summary,
    }
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_gate_receipt",
        "candidate_sha256": sealed_candidate["candidate_sha256"],
        "candidate_id": sealed_candidate["candidate_id"],
        "selected_signal_evaluation_sha256": None if selected_signal is None else selected_signal["signal_evaluation_sha256"],
        "selected_human_review_sha256": None if selected_review is None else selected_review["human_review_sha256"],
        "custody": custody_row,
        "reproducibility": repro_row,
        "rights": rights_decision,
        "status": status_vector,
        "release_allowed": release_allowed,
        "blockers": blockers,
        "failures": failures,
        "builder_self_approval_refused": True,
        "signal_evaluation_is_musical_acceptance": False,
        "whole_organism_passed": False,
        "metadata": {},
    }
    if out["release_allowed"] and out["status"]["musical_acceptance"] != "accept":
        raise FloorError("release gate opened without human musical acceptance")
    return floor_seal_release_gate_receipt(out)


def floor_adapt_source_only_recurrence_receipt(
    receipt: Mapping[str, Any],
    *,
    builder: Mapping[str, Any],
    signal_evaluator: Mapping[str, Any],
    source_artifact_id: str = "source_master",
    rights_status: str = "not_evaluated",
) -> dict[str, Any]:
    """Adapt the conservative Empire-style recurrence receipt into Floor objects."""
    raw = deepcopy(dict(receipt))
    if str(raw.get("kind") or "") != "earcrate_source_only_recurrence_release_receipt":
        raise FloorError("unsupported source-only recurrence receipt kind")
    source = dict(raw.get("source") or {})
    edit = dict(raw.get("edit") or {})
    metrics = dict(raw.get("metrics") or {})
    artifacts = dict(raw.get("artifacts") or {})
    sample_rate = _integer(source.get("decoded_sample_rate"), "recurrence receipt sample rate", minimum=1)
    channels = _integer(source.get("channels"), "recurrence receipt channels", minimum=1)
    output_frames = _integer(metrics.get("output_frames"), "recurrence receipt output_frames", minimum=1)
    crossfade_frames = _integer(metrics.get("crossfade_frames"), "recurrence receipt crossfade_frames", minimum=1)

    prefix_seconds = list(edit.get("prefix_seconds") or [])
    donor_seconds = list(edit.get("donor_seconds") or [])
    target_seconds = list(edit.get("target_replaced_seconds") or [])
    if len(prefix_seconds) != 2 or len(donor_seconds) != 2 or len(target_seconds) != 2:
        raise FloorError("recurrence receipt requires prefix, target, and donor intervals")
    prefix_start = round(_number(prefix_seconds[0], "prefix start") * sample_rate)
    prefix_end = round(_number(prefix_seconds[1], "prefix end") * sample_rate)
    donor_start = round(_number(donor_seconds[0], "donor start") * sample_rate)
    donor_end = round(_number(donor_seconds[1], "donor end") * sample_rate)
    target_start = round(_number(target_seconds[0], "target start") * sample_rate)
    target_end = round(_number(target_seconds[1], "target end") * sample_rate)
    if prefix_end != target_start:
        raise FloorError("recurrence receipt prefix must lead directly into replaced target")
    if donor_end - donor_start != target_end - target_start:
        raise FloorError("recurrence donor and target must have equal frame length")
    prefix_frames = prefix_end - prefix_start
    if prefix_frames + (donor_end - donor_start) - crossfade_frames != output_frames:
        raise FloorError("recurrence receipt output frame count disagrees with edit intervals")

    source_descriptor = {
        "artifact_id": source_artifact_id,
        "sha256": _sha(source.get("sha256"), "recurrence source sha256"),
        "decoded_pcm_sha256": _sha(source.get("decoded_pcm_sha256"), "recurrence decoded source PCM", optional=True),
        "media_kind": str(source.get("media_kind") or "audio/*"),
        "size_bytes": _integer(source.get("size_bytes", 0), "recurrence source size_bytes"),
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": _integer(source.get("frames", 0), "recurrence source frames"),
        "role": "source_master",
        "path": str(source.get("path") or ""),
        "uri": str(source.get("uri") or ""),
        "metadata": {"external_source_media": True},
    }
    edit_plan = floor_seal_audio_edit_plan(
        {
            "sample_rate": sample_rate,
            "channels": channels,
            "output_frames": output_frames,
            "source_artifacts": [source_descriptor],
            "segments": [
                {
                    "segment_id": "retained_prefix",
                    "output_start_frame": 0,
                    "output_end_frame": prefix_frames,
                    "source_artifact_id": source_artifact_id,
                    "source_start_frame": prefix_start,
                    "source_end_frame": prefix_end,
                    "operation": "source_copy",
                    "gain_db": metrics.get("applied_gain_db", 0.0),
                    "metadata": {"bars": int(edit.get("prefix_bars") or 0)},
                },
                {
                    "segment_id": "donor_recurrence",
                    "output_start_frame": prefix_frames - crossfade_frames,
                    "output_end_frame": output_frames,
                    "source_artifact_id": source_artifact_id,
                    "source_start_frame": donor_start,
                    "source_end_frame": donor_end,
                    "operation": "source_seek",
                    "gain_db": metrics.get("applied_gain_db", 0.0),
                    "metadata": {
                        "replaces_source_start_frame": target_start,
                        "replaces_source_end_frame": target_end,
                        "bars": int(edit.get("donor_bars") or 0),
                    },
                },
            ],
            "transitions": [
                {
                    "transition_id": "prefix_to_donor",
                    "left_segment_id": "retained_prefix",
                    "right_segment_id": "donor_recurrence",
                    "operation": "equal_power_crossfade",
                    "overlap_frames": crossfade_frames,
                    "curve": str(edit.get("crossfade_curve") or "equal_power"),
                    "metadata": {"crossfade_ms": edit.get("crossfade_ms")},
                }
            ],
            "declared_operations": edit.get("declared_operations") or ["source_copy", "source_seek", "gain", "equal_power_crossfade"],
            "prohibited_operations": edit.get("prohibited_operations") or [],
            "source_only": bool(edit.get("source_only", False)),
            "metadata": {"legacy_receipt_sha256": raw.get("receipt_sha256")},
        }
    )

    output_seconds = _number(metrics.get("output_duration_seconds"), "recurrence output duration", minimum=0.0)
    prefix_seconds_duration = prefix_frames / sample_rate
    transition_seconds = crossfade_frames / sample_rate
    time_map = floor_seal_time_map(
        {
            "time_unit": "second",
            "segments": [
                {
                    "segment_id": "retained_prefix",
                    "target_start": 0,
                    "target_end": prefix_seconds_duration - transition_seconds,
                    "source_artifact_id": source_artifact_id,
                    "source_start": prefix_seconds[0],
                    "source_end": prefix_seconds[1] - transition_seconds,
                    "mode": "continuous",
                    "rate": 1,
                    "metadata": {},
                },
                {
                    "segment_id": "donor_recurrence",
                    "target_start": prefix_seconds_duration - transition_seconds,
                    "target_end": output_seconds,
                    "source_artifact_id": source_artifact_id,
                    "source_start": donor_seconds[0],
                    "source_end": donor_seconds[1],
                    "mode": "jump",
                    "rate": 1,
                    "metadata": {"transition_overlap_seconds": transition_seconds},
                },
            ],
            "metadata": {"crossfade_authority": "audio_edit_plan"},
        }
    )

    meter_text = str(edit.get("meter") or "4/4")
    try:
        numerator, denominator = (int(item) for item in meter_text.split("/", 1))
    except Exception as exc:
        raise FloorError("recurrence receipt meter must be n/d") from exc
    donor_bars = int(edit.get("donor_bars") or 4)
    prefix_bars = int(edit.get("prefix_bars") or 8)
    source_sha = str(source_descriptor["sha256"])
    similarity = dict(metrics.get("target_donor_similarity") or {})
    phrase_contract = floor_seal_phrase_contract(
        {
            "role": "hook_reprise",
            "start_beat": prefix_bars * numerator,
            "length_beats": donor_bars * numerator,
            "meter": {"numerator": numerator, "denominator": denominator},
            "entry_grammar": {"phrase_downbeat_required": True},
            "exit_grammar": {"complete_phrase_required": True},
            "transforms": {
                "allowed_operations": ["source_seek", "source_copy", "gain", "equal_power_crossfade"],
                "reverse_allowed": False,
                "loop_allowed": False,
                "slice_allowed": False,
            },
            "hard_constraints": {
                "nonoverlapping_source_occurrence": True,
                "source_only": True,
                "no_silence_preroll": True,
                "no_undeclared_source": True,
                "prohibited_operations": edit_plan["prohibited_operations"],
            },
            "soft_objectives": [
                {"metric": "chroma_frame_cosine_mean", "target": "maximize"},
                {"metric": "mel_frame_cosine_mean", "target": "maximize"},
                {"metric": "onset_envelope_correlation", "target": "maximize"},
            ],
            "identity_obligations": [
                {"kind": "hook_harmonic_identity", "minimum": 0.95, "measured": similarity.get("chroma_frame_cosine_mean")},
                {"kind": "hook_timbre_identity", "minimum": 0.95, "measured": similarity.get("mel_frame_cosine_mean")},
                {"kind": "hook_onset_identity", "minimum": 0.80, "measured": similarity.get("onset_envelope_correlation")},
            ],
            "future_obligations": [{"kind": "human_seam_and_phrase_judgment", "status": "open"}],
            "evidence_refs": [str(raw.get("receipt_sha256") or "")],
            "rights": {
                "source_artifact_sha256": source_sha,
                "assertion_status": "unknown",
                "license_expression": "NOASSERTION",
                "allowed_uses": [],
                "prohibited_uses": [],
                "evidence_refs": [source_sha],
            },
            "metadata": {"target_replaced_seconds": target_seconds, "donor_seconds": donor_seconds},
        }
    )

    output_descriptor = {
        "artifact_id": "authoritative_candidate_pcm",
        "sha256": _sha(artifacts.get("decoded_stereo_f32le_sha256"), "candidate decoded PCM sha256"),
        "decoded_pcm_sha256": _sha(artifacts.get("decoded_stereo_f32le_sha256"), "candidate decoded PCM sha256"),
        "media_kind": "audio/vnd.earcrate.pcm-f32le-stereo",
        "size_bytes": output_frames * channels * 4,
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": output_frames,
        "role": "authoritative_candidate_pcm",
        "path": "",
        "uri": "",
        "metadata": {"authority": True},
    }
    deliveries = []
    for key, size_key, artifact_id, media_kind in (
        ("wav_sha256", "wav_size_bytes", "candidate_wav", "audio/wav"),
        ("mp3_sha256", "mp3_size_bytes", "candidate_mp3", "audio/mpeg"),
        ("mp3_30s_sha256", "mp3_30s_size_bytes", "candidate_mp3_30s", "audio/mpeg"),
    ):
        if artifacts.get(key):
            deliveries.append(
                {
                    "artifact_id": artifact_id,
                    "sha256": artifacts[key],
                    "media_kind": media_kind,
                    "size_bytes": _integer(artifacts.get(size_key, 0), f"delivery {artifact_id} size_bytes"),
                    "sample_rate": 0,
                    "channels": 0,
                    "frames": 0,
                    "role": "delivery",
                    "metadata": {"non_authoritative_container": True},
                }
            )

    candidate = floor_seal_release_candidate(
        {
            "title": str(raw.get("title") or "Source-only recurrence release candidate"),
            "builder": builder,
            "evidence_branch": "audio",
            "evidence_tier": "blind_audio_inference",
            "source_evidence_refs": [str(raw.get("receipt_sha256") or ""), source_sha],
            "audio_edit_plan": edit_plan,
            "time_map": time_map,
            "phrase_contracts": [phrase_contract],
            "authoritative_output": output_descriptor,
            "delivery_artifacts": deliveries,
            "status": {
                "custody": str((raw.get("status") or {}).get("custody") or "pending"),
                "build_reproducibility": str((raw.get("status") or {}).get("build_reproducibility") or "not_run"),
                "signal_sanity": "not_run",
                "recurrence_identity": "not_evaluated",
                "transition_integrity": "not_evaluated",
                "musical_acceptance": "pending",
                "rights_eligibility": rights_status,
                "release_status": "blocked",
                "summary": "candidate_unqualified",
            },
            "builder_may_not_approve_music": True,
            "metadata": {"legacy_receipt_sha256": raw.get("receipt_sha256")},
        }
    )

    signal_metrics = {
        "first_audible_seconds": _number(metrics.get("first_audible_seconds"), "first audible", minimum=0.0),
        "longest_silence_seconds": _number(metrics.get("longest_silence_below_minus_55_db_seconds"), "longest silence", minimum=0.0),
        "integrated_loudness_lufs": _number(metrics.get("integrated_loudness_lufs"), "integrated loudness"),
        "true_peak_dbfs": _number(metrics.get("true_peak_dbfs_4x"), "true peak"),
        "chroma_frame_cosine_mean": _number(similarity.get("chroma_frame_cosine_mean"), "chroma similarity"),
        "mel_frame_cosine_mean": _number(similarity.get("mel_frame_cosine_mean"), "mel similarity"),
        "onset_envelope_correlation": _number(similarity.get("onset_envelope_correlation"), "onset correlation"),
    }
    gate_specs = (
        ("audible_from_start", "first_audible_seconds", "lte", 0.001),
        ("no_extended_silence", "longest_silence_seconds", "lte", 0.050),
        ("true_peak_bounded", "true_peak_dbfs", "lte", -0.1),
        ("loudness_floor", "integrated_loudness_lufs", "gte", -14.0),
        ("loudness_ceiling", "integrated_loudness_lufs", "lte", -6.0),
        ("hook_chroma", "chroma_frame_cosine_mean", "gte", 0.95),
        ("hook_timbre", "mel_frame_cosine_mean", "gte", 0.95),
        ("hook_onset", "onset_envelope_correlation", "gte", 0.80),
    )
    gates = []
    for gate_id, metric_name, operator, threshold in gate_specs:
        measured = signal_metrics[metric_name]
        passed = measured <= threshold if operator == "lte" else measured >= threshold
        gates.append(
            {
                "gate_id": gate_id,
                "metric": metric_name,
                "operator": operator,
                "threshold": threshold,
                "measured": measured,
                "passed": passed,
                "metadata": {},
            }
        )
    signal = floor_seal_signal_evaluation(
        {
            "candidate_sha256": candidate["candidate_sha256"],
            "builder_identity_id": candidate["builder"]["identity_id"],
            "evaluator": signal_evaluator,
            "metrics": signal_metrics,
            "gates": gates,
            "passed": all(row["passed"] for row in gates),
            "status": "passed" if all(row["passed"] for row in gates) else "failed",
            "recurrence_identity": "passed" if gates[-3]["passed"] and gates[-2]["passed"] and gates[-1]["passed"] else "failed",
            "transition_integrity": "provisional_pass" if all(row["passed"] for row in gates[:5]) else "failed",
            "notes": ["transition integrity remains provisional until human seam and phrase review"],
            "metadata": {"legacy_receipt_sha256": raw.get("receipt_sha256")},
        },
        candidate,
    )
    review_template = floor_release_review_template(candidate)
    pending_gate = floor_build_release_gate(
        candidate,
        signal_evaluations=[signal],
        human_reviews=[],
        custody={"status": str((raw.get("status") or {}).get("custody") or "pending"), "evidence_refs": [source_sha]},
        reproducibility={"status": str((raw.get("status") or {}).get("build_reproducibility") or "not_run")},
        rights={"status": rights_status},
    )
    return {
        "audio_edit_plan": edit_plan,
        "time_map": time_map,
        "phrase_contract": phrase_contract,
        "release_candidate": candidate,
        "signal_evaluation": signal,
        "human_review_template": review_template,
        "release_gate": pending_gate,
    }


def floor_release_profile_capability() -> dict[str, Any]:
    value = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_candidate_profile_capability",
        "ready": True,
        "objects": [
            "AudioEditPlan",
            "ReleaseCandidate",
            "SignalEvaluation",
            "HumanMusicalReview",
            "ReleaseGateReceipt",
        ],
        "candidate_builder_may_approve_music": False,
        "signal_evaluation_is_musical_acceptance": False,
        "human_musical_review_required": True,
        "rights_policy_acceptance_required": True,
        "whole_organism_passage_implied": False,
        "pending_summary": "signal_sane_human_review_pending",
    }
    value["capability_sha256"] = floor_sha256_json(value)
    return value


def floor_verify_release_object(value: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(value.get("kind") or "")
    if kind == "earcrate_floor_audio_edit_plan":
        return floor_seal_audio_edit_plan(value)
    if kind == "earcrate_floor_release_candidate":
        return floor_seal_release_candidate(value)
    if kind == "earcrate_floor_signal_evaluation":
        return floor_seal_signal_evaluation(value)
    if kind == "earcrate_floor_human_musical_review":
        return floor_seal_human_musical_review(value)
    if kind == "earcrate_floor_release_gate_receipt":
        return floor_seal_release_gate_receipt(value)
    raise FloorError(f"unsupported release object kind: {kind!r}")


__all__ = [
    "FLOOR_RELEASE_SIGNAL_STATUSES",
    "FLOOR_RELEASE_REPRO_STATUSES",
    "FLOOR_RELEASE_CUSTODY_STATUSES",
    "FLOOR_RELEASE_RECURRENCE_STATUSES",
    "FLOOR_RELEASE_TRANSITION_STATUSES",
    "FLOOR_RELEASE_HUMAN_VERDICTS",
    "FLOOR_RELEASE_RIGHTS_STATUSES",
    "FLOOR_RELEASE_STATUSES",
    "floor_seal_audio_edit_plan",
    "floor_seal_release_candidate",
    "floor_seal_signal_evaluation",
    "floor_seal_human_musical_review",
    "floor_release_review_template",
    "floor_seal_release_gate_receipt",
    "floor_build_release_gate",
    "floor_adapt_source_only_recurrence_receipt",
    "floor_release_profile_capability",
    "floor_verify_release_object",
]
