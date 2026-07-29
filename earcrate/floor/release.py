from __future__ import annotations

"""Release-candidate discipline for the Open Music Evidence Floor.

A candidate builder can prove custody and produce a reviewable artifact. An
independent signal evaluator can prove machine-checkable integrity. Neither may
approve the music. Human musical review and use-scoped rights review remain
separate authorities, and the release gate composes their receipts without
laundering any one of them into whole-organism or legal truth.
"""

import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .model import (
    FLOOR_SCHEMA_VERSION,
    FloorError,
    floor_jsonable,
    floor_seal_conformance_report,
    floor_seal_evaluation_ledger,
    floor_seal_phrase_contract,
    floor_seal_time_map,
    floor_sha256_json,
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

RELEASE_CUSTODY_STATES = ("passed", "failed", "not_run")
RELEASE_REPRO_STATES = ("passed", "failed", "not_evaluated")
RELEASE_SIGNAL_STATES = ("passed", "failed", "not_evaluated")
RELEASE_PROVISIONAL_STATES = ("provisional_pass", "failed", "not_evaluated")
RELEASE_HUMAN_VERDICTS = ("pending", "accepted", "rejected", "revise")
RELEASE_RIGHTS_STATES = ("not_evaluated", "eligible_for_declared_use", "ineligible_for_declared_use", "unknown")
RELEASE_GATE_STATES = (
    "failed",
    "signal_sane_human_review_pending",
    "human_rejected",
    "revision_requested",
    "rights_review_pending",
    "rights_blocked",
    "release_eligible_for_declared_use",
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
    if not _SHA_RE.fullmatch(text):
        raise FloorError(f"{field} must be a lowercase SHA-256")
    return text


def _state(value: Any, allowed: Sequence[str], field: str) -> str:
    text = str(value or "")
    if text not in allowed:
        raise FloorError(f"{field} must be one of {list(allowed)}")
    return text


def _artifact(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    return {
        "artifact_id": _text(raw.get("artifact_id"), f"release artifact {index} artifact_id"),
        "sha256": _sha(raw.get("sha256"), f"release artifact {index} sha256"),
        "size_bytes": int(raw.get("size_bytes") or 0),
        "media_kind": _text(raw.get("media_kind"), f"release artifact {index} media_kind"),
        "role": str(raw.get("role") or ""),
        "musical_authority": bool(raw.get("musical_authority", False)),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def floor_seal_release_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported release candidate schema")
    if str(raw.get("kind") or "earcrate_floor_release_candidate") != "earcrate_floor_release_candidate":
        raise FloorError("unsupported release candidate kind")

    builder = deepcopy(dict(raw.get("builder") or {}))
    provider_id = _text(builder.get("provider_id"), "release candidate builder provider_id")
    artifacts = [_artifact(row, index) for index, row in enumerate(raw.get("artifacts") or [])]
    if not artifacts:
        raise FloorError("release candidate requires artifacts")
    ids = [row["artifact_id"] for row in artifacts]
    if len(ids) != len(set(ids)):
        raise FloorError("release candidate artifact IDs must be unique")
    if not any(row["musical_authority"] for row in artifacts):
        raise FloorError("release candidate requires one authoritative musical artifact")

    boundary = deepcopy(dict(raw.get("boundary") or {}))
    if boundary.get("builder_may_approve_music") not in {None, False}:
        raise FloorError("release candidate builder may not approve music")
    if boundary.get("legal_clearance_claimed") not in {None, False}:
        raise FloorError("release candidate may not claim legal clearance")
    if boundary.get("whole_organism_passed") not in {None, False}:
        raise FloorError("release candidate may not claim whole-organism passage")
    boundary.update(
        {
            "builder_may_approve_music": False,
            "human_review_required": True,
            "legal_clearance_claimed": False,
            "whole_organism_passed": False,
        }
    )

    status_raw = deepcopy(dict(raw.get("status_vector") or {}))
    status = {
        "custody": _state(status_raw.get("custody", "not_run"), RELEASE_CUSTODY_STATES, "release custody"),
        "build_reproducibility": _state(
            status_raw.get("build_reproducibility", "not_evaluated"),
            RELEASE_REPRO_STATES,
            "release build_reproducibility",
        ),
        "signal_sanity": _state(status_raw.get("signal_sanity", "not_evaluated"), RELEASE_SIGNAL_STATES, "release signal_sanity"),
        "recurrence_identity": _state(
            status_raw.get("recurrence_identity", "not_evaluated"),
            RELEASE_PROVISIONAL_STATES,
            "release recurrence_identity",
        ),
        "transition_integrity": _state(
            status_raw.get("transition_integrity", "not_evaluated"),
            RELEASE_PROVISIONAL_STATES,
            "release transition_integrity",
        ),
        "human_musical_review": "pending",
        "rights_eligibility": "not_evaluated",
        "release_state": "blocked",
    }
    # A builder-owned candidate can never embed later review or release approval.
    if status_raw.get("human_musical_review") not in {None, "pending"}:
        raise FloorError("release candidate cannot carry an accepted or rejected human review")
    if status_raw.get("rights_eligibility") not in {None, "not_evaluated"}:
        raise FloorError("release candidate cannot decide rights eligibility")
    if status_raw.get("release_state") not in {None, "blocked", "failed"}:
        raise FloorError("release candidate cannot approve its own release")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_candidate",
        "candidate_id": str(raw.get("candidate_id") or ""),
        "builder": {
            "provider_id": provider_id,
            "provider_version": str(builder.get("provider_version") or ""),
            "provider_manifest_sha256": _sha(builder.get("provider_manifest_sha256"), "release builder manifest_sha256"),
            "request_sha256": _sha(builder.get("request_sha256"), "release builder request_sha256"),
            "result_sha256": _sha(builder.get("result_sha256"), "release builder result_sha256"),
            "semantic_result_sha256": _sha(
                builder.get("semantic_result_sha256"), "release builder semantic_result_sha256"
            ),
            "invocation_receipt_sha256": _sha(
                builder.get("invocation_receipt_sha256"), "release builder invocation_receipt_sha256"
            ),
            "conformance_sha256": _sha(
                builder.get("conformance_sha256"), "release builder conformance_sha256", optional=True
            ),
        },
        "source_artifact_sha256s": sorted(
            {_sha(item, "release source artifact SHA-256") for item in raw.get("source_artifact_sha256s") or []}
        ),
        "artifacts": artifacts,
        "time_map": floor_seal_time_map(raw.get("time_map") or {}),
        "phrase_contract": floor_seal_phrase_contract(raw.get("phrase_contract") or {}),
        "status_vector": status,
        "boundary": boundary,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["release_candidate_sha256"] = floor_sha256_json(out)
    if not out["candidate_id"]:
        out["candidate_id"] = "release_candidate_" + out["release_candidate_sha256"][:24]
        out["release_candidate_sha256"] = floor_sha256_json(
            {key: item for key, item in out.items() if key != "release_candidate_sha256"}
        )
    supplied = str(raw.get("release_candidate_sha256") or "")
    if supplied and supplied != out["release_candidate_sha256"]:
        raise FloorError("release_candidate_sha256 does not match release candidate")
    return out


def floor_seal_human_review_request(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported human review request schema")
    if str(raw.get("kind") or "earcrate_floor_human_musical_review_request") != "earcrate_floor_human_musical_review_request":
        raise FloorError("unsupported human review request kind")
    verdicts = [str(item) for item in raw.get("allowed_verdicts") or []]
    if sorted(set(verdicts)) != ["accepted", "rejected", "revise"]:
        raise FloorError("human review request must allow accepted, rejected, and revise")
    questions = [str(item).strip() for item in raw.get("questions") or [] if str(item).strip()]
    if not questions:
        raise FloorError("human review request requires questions")
    if str(raw.get("status") or "pending") != "pending":
        raise FloorError("human review request must remain pending")
    if raw.get("builder_may_answer") not in {None, False}:
        raise FloorError("candidate builder may not answer the human review request")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_human_musical_review_request",
        "candidate_id": _text(raw.get("candidate_id"), "human review request candidate_id"),
        "release_candidate_sha256": _sha(
            raw.get("release_candidate_sha256"), "human review request release_candidate_sha256"
        ),
        "questions": questions,
        "allowed_verdicts": ["accepted", "rejected", "revise"],
        "status": "pending",
        "builder_may_answer": False,
    }
    out["review_request_sha256"] = floor_sha256_json(out)
    supplied = str(raw.get("review_request_sha256") or "")
    if supplied and supplied != out["review_request_sha256"]:
        raise FloorError("review_request_sha256 does not match human review request")
    return out


def floor_human_review_request(candidate: Mapping[str, Any], *, questions: Sequence[str] | None = None) -> dict[str, Any]:
    sealed = floor_seal_release_candidate(candidate)
    return floor_seal_human_review_request(
        {
            "schema_version": FLOOR_SCHEMA_VERSION,
            "kind": "earcrate_floor_human_musical_review_request",
            "candidate_id": sealed["candidate_id"],
            "release_candidate_sha256": sealed["release_candidate_sha256"],
            "questions": list(
                questions
                or [
                    "Is the edit musically continuous at every source-time discontinuity?",
                    "Does the replacement preserve the recognizable identity required by the PhraseContract?",
                    "Does the candidate improve or preserve the intended musical arc?",
                    "Should this candidate be accepted, rejected, or revised?",
                ]
            ),
            "allowed_verdicts": ["accepted", "rejected", "revise"],
            "status": "pending",
            "builder_may_answer": False,
        }
    )


def floor_seal_human_musical_review(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported human musical review schema")
    if str(raw.get("kind") or "earcrate_floor_human_musical_review") != "earcrate_floor_human_musical_review":
        raise FloorError("unsupported human musical review kind")
    verdict = _state(raw.get("verdict"), RELEASE_HUMAN_VERDICTS, "human review verdict")
    reviewer = deepcopy(dict(raw.get("reviewer") or {}))
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_human_musical_review",
        "release_candidate_sha256": _sha(
            raw.get("release_candidate_sha256"), "human review release_candidate_sha256"
        ),
        "reviewer": {
            "reviewer_id": _text(reviewer.get("reviewer_id"), "human review reviewer_id"),
            "display_name": str(reviewer.get("display_name") or ""),
            "role": str(reviewer.get("role") or "musician"),
        },
        "verdict": verdict,
        "blind_review": bool(raw.get("blind_review", False)),
        "ratings": {
            str(key): float(item) for key, item in dict(raw.get("ratings") or {}).items()
        },
        "notes": [str(item) for item in raw.get("notes") or []],
        "comparison_artifact_sha256s": sorted(
            {_sha(item, "human review comparison artifact SHA-256") for item in raw.get("comparison_artifact_sha256s") or []}
        ),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if verdict == "pending" and out["ratings"]:
        raise FloorError("pending human review may not carry final ratings")
    out["human_review_sha256"] = floor_sha256_json(out)
    supplied = str(raw.get("human_review_sha256") or "")
    if supplied and supplied != out["human_review_sha256"]:
        raise FloorError("human_review_sha256 does not match human review")
    return out


def floor_seal_rights_review(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported rights review schema")
    if str(raw.get("kind") or "earcrate_floor_rights_review") != "earcrate_floor_rights_review":
        raise FloorError("unsupported rights review kind")
    reviewer = deepcopy(dict(raw.get("reviewer") or {}))
    status = _state(raw.get("status"), RELEASE_RIGHTS_STATES, "rights review status")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_rights_review",
        "release_candidate_sha256": _sha(
            raw.get("release_candidate_sha256"), "rights review release_candidate_sha256"
        ),
        "declared_use": _text(raw.get("declared_use"), "rights review declared_use"),
        "status": status,
        "reviewer": {
            "reviewer_id": _text(reviewer.get("reviewer_id"), "rights review reviewer_id"),
            "role": str(reviewer.get("role") or "rights_policy"),
        },
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "conditions": [str(item) for item in raw.get("conditions") or []],
        "legal_determination": False,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if raw.get("legal_determination") not in {None, False}:
        raise FloorError("rights review is use-scoped policy evidence, not a legal determination")
    out["rights_review_sha256"] = floor_sha256_json(out)
    supplied = str(raw.get("rights_review_sha256") or "")
    if supplied and supplied != out["rights_review_sha256"]:
        raise FloorError("rights_review_sha256 does not match rights review")
    return out


def floor_seal_release_gate_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported release gate receipt schema")
    if str(raw.get("kind") or "earcrate_floor_release_gate_receipt") != "earcrate_floor_release_gate_receipt":
        raise FloorError("unsupported release gate receipt kind")
    status_raw = deepcopy(dict(raw.get("status_vector") or {}))
    status = {
        "custody": _state(status_raw.get("custody"), RELEASE_CUSTODY_STATES, "release gate custody"),
        "build_reproducibility": _state(
            status_raw.get("build_reproducibility"), RELEASE_REPRO_STATES, "release gate build reproducibility"
        ),
        "signal_sanity": _state(status_raw.get("signal_sanity"), RELEASE_SIGNAL_STATES, "release gate signal sanity"),
        "recurrence_identity": _state(
            status_raw.get("recurrence_identity"), RELEASE_PROVISIONAL_STATES, "release gate recurrence identity"
        ),
        "transition_integrity": _state(
            status_raw.get("transition_integrity"), RELEASE_PROVISIONAL_STATES, "release gate transition integrity"
        ),
        "human_musical_review": _state(
            status_raw.get("human_musical_review"), RELEASE_HUMAN_VERDICTS, "release gate human review"
        ),
        "rights_eligibility": _state(
            status_raw.get("rights_eligibility"), RELEASE_RIGHTS_STATES, "release gate rights eligibility"
        ),
        "whole_organism_status": str(status_raw.get("whole_organism_status") or "not_claimed"),
        "release_state": _state(status_raw.get("release_state"), RELEASE_GATE_STATES, "release gate state"),
    }
    if status["whole_organism_status"] != "not_claimed":
        raise FloorError("release gate may not claim whole-organism status")

    conformance_sha = _sha(raw.get("conformance_sha256"), "release gate conformance_sha256", optional=True)
    signal_sha = _sha(
        raw.get("signal_evaluation_sha256"), "release gate signal_evaluation_sha256", optional=True
    )
    human_sha = _sha(raw.get("human_review_sha256"), "release gate human_review_sha256", optional=True)
    rights_sha = _sha(raw.get("rights_review_sha256"), "release gate rights_review_sha256", optional=True)

    if status["build_reproducibility"] == "passed" and conformance_sha is None:
        raise FloorError("passed build reproducibility requires a conformance receipt")
    if status["signal_sanity"] == "passed" and signal_sha is None:
        raise FloorError("passed signal sanity requires an independent evaluation ledger")
    if status["human_musical_review"] == "pending" and human_sha is not None:
        raise FloorError("pending human musical review may not name a final review")
    if status["human_musical_review"] != "pending" and human_sha is None:
        raise FloorError("decided human musical review requires a review receipt")
    if status["rights_eligibility"] in {"eligible_for_declared_use", "ineligible_for_declared_use"} and rights_sha is None:
        raise FloorError("decided rights eligibility requires a rights review receipt")

    release_eligible = bool(raw.get("release_eligible", False))
    if release_eligible != (status["release_state"] == "release_eligible_for_declared_use"):
        raise FloorError("release_eligible disagrees with release_state")
    if release_eligible:
        required = {
            "custody": "passed",
            "build_reproducibility": "passed",
            "signal_sanity": "passed",
            "human_musical_review": "accepted",
            "rights_eligibility": "eligible_for_declared_use",
        }
        for key, expected in required.items():
            if status[key] != expected:
                raise FloorError(f"release eligibility requires {key}={expected}")

    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_gate_receipt",
        "release_candidate_sha256": _sha(
            raw.get("release_candidate_sha256"), "release gate release_candidate_sha256"
        ),
        "builder_provider_id": _text(raw.get("builder_provider_id"), "release gate builder_provider_id"),
        "conformance_sha256": conformance_sha,
        "signal_evaluation_sha256": signal_sha,
        "human_review_sha256": human_sha,
        "rights_review_sha256": rights_sha,
        "checks": {str(key): bool(item) for key, item in dict(raw.get("checks") or {}).items()},
        "status_vector": status,
        "release_eligible": release_eligible,
        "musical_acceptance_decided_by_builder": False,
        "legal_determination_claimed": False,
        "whole_organism_passed": False,
    }
    if raw.get("musical_acceptance_decided_by_builder") not in {None, False}:
        raise FloorError("release gate cannot assign musical acceptance to the builder")
    if raw.get("legal_determination_claimed") not in {None, False}:
        raise FloorError("release gate cannot claim a legal determination")
    if raw.get("whole_organism_passed") not in {None, False}:
        raise FloorError("release gate cannot claim whole-organism passage")
    out["release_gate_sha256"] = floor_sha256_json(out)
    supplied = str(raw.get("release_gate_sha256") or "")
    if supplied and supplied != out["release_gate_sha256"]:
        raise FloorError("release_gate_sha256 does not match release gate receipt")
    return out


def floor_build_release_gate(
    candidate: Mapping[str, Any],
    *,
    conformance: Mapping[str, Any] | None = None,
    signal_evaluation: Mapping[str, Any] | None = None,
    human_review: Mapping[str, Any] | None = None,
    rights_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cand = floor_seal_release_candidate(candidate)
    builder = cand["builder"]
    checks: dict[str, bool] = {
        "candidate_custody_passed": cand["status_vector"]["custody"] == "passed",
        "builder_cannot_approve_music": cand["boundary"]["builder_may_approve_music"] is False,
        "candidate_does_not_claim_legal_clearance": cand["boundary"]["legal_clearance_claimed"] is False,
    }

    conf = None
    if conformance is not None:
        conf = floor_seal_conformance_report(conformance)
        checks["builder_conformance_complete"] = bool(conf["complete"])
        checks["builder_semantically_repeatable"] = bool(
            conf["checks"].get("semantic_result_repeatable")
        )
        if builder.get("conformance_sha256") and builder["conformance_sha256"] != conf["conformance_sha256"]:
            raise FloorError("release candidate names another conformance report")
    else:
        checks["builder_conformance_complete"] = False
        checks["builder_semantically_repeatable"] = False

    evaluation = None
    signal_passed = False
    if signal_evaluation is not None:
        evaluation = floor_seal_evaluation_ledger(signal_evaluation)
        if evaluation["provider_id"] != builder["provider_id"]:
            raise FloorError("signal evaluation targets another builder provider")
        for field in ("provider_manifest_sha256", "request_sha256", "result_sha256"):
            if evaluation[field] != builder[field]:
                raise FloorError(f"signal evaluation {field} names another builder execution")
        if evaluation["evaluator"]["evaluator_id"] == builder["provider_id"]:
            raise FloorError("candidate builder may not grade its own release signal")
        signal_passed = bool(evaluation["metrics"].get("automatic_signal_passed", 0.0) >= 1.0)
        checks["independent_signal_evaluator"] = True
        checks["automatic_signal_passed"] = signal_passed
    else:
        checks["independent_signal_evaluator"] = False
        checks["automatic_signal_passed"] = False

    human = None if human_review is None else floor_seal_human_musical_review(human_review)
    if human and human["release_candidate_sha256"] != cand["release_candidate_sha256"]:
        raise FloorError("human review names another release candidate")
    if human and human["reviewer"]["reviewer_id"] in {
        builder["provider_id"],
        None if evaluation is None else evaluation["evaluator"]["evaluator_id"],
    }:
        raise FloorError("builder or signal evaluator may not supply the human musical verdict")

    rights = None if rights_review is None else floor_seal_rights_review(rights_review)
    if rights and rights["release_candidate_sha256"] != cand["release_candidate_sha256"]:
        raise FloorError("rights review names another release candidate")

    core_ok = bool(
        checks["candidate_custody_passed"]
        and checks["builder_cannot_approve_music"]
        and checks["candidate_does_not_claim_legal_clearance"]
        and checks["builder_conformance_complete"]
        and checks["builder_semantically_repeatable"]
        and checks["independent_signal_evaluator"]
        and checks["automatic_signal_passed"]
    )
    verdict = "pending" if human is None else human["verdict"]
    rights_state = "not_evaluated" if rights is None else rights["status"]
    if not core_ok:
        release_state = "failed"
    elif verdict == "pending":
        release_state = "signal_sane_human_review_pending"
    elif verdict == "rejected":
        release_state = "human_rejected"
    elif verdict == "revise":
        release_state = "revision_requested"
    elif rights is None or rights_state in {"not_evaluated", "unknown"}:
        release_state = "rights_review_pending"
    elif rights_state == "ineligible_for_declared_use":
        release_state = "rights_blocked"
    else:
        release_state = "release_eligible_for_declared_use"

    status = {
        "custody": "passed" if checks["candidate_custody_passed"] else "failed",
        "build_reproducibility": "passed"
        if checks["builder_conformance_complete"] and checks["builder_semantically_repeatable"]
        else "failed",
        "signal_sanity": "passed" if signal_passed else "failed",
        "recurrence_identity": "provisional_pass"
        if signal_passed and float((evaluation or {}).get("metrics", {}).get("chroma_frame_cosine_mean", 0.0)) >= 0.9
        else "failed",
        "transition_integrity": "provisional_pass"
        if signal_passed and float((evaluation or {}).get("metrics", {}).get("transition_integrity_passed", 0.0)) >= 1.0
        else "failed",
        "human_musical_review": verdict,
        "rights_eligibility": rights_state,
        "whole_organism_status": "not_claimed",
        "release_state": release_state,
    }
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_gate_receipt",
        "release_candidate_sha256": cand["release_candidate_sha256"],
        "builder_provider_id": builder["provider_id"],
        "conformance_sha256": None if conf is None else conf["conformance_sha256"],
        "signal_evaluation_sha256": None if evaluation is None else evaluation["evaluation_sha256"],
        "human_review_sha256": None if human is None else human["human_review_sha256"],
        "rights_review_sha256": None if rights is None else rights["rights_review_sha256"],
        "checks": checks,
        "status_vector": status,
        "release_eligible": release_state == "release_eligible_for_declared_use",
        "musical_acceptance_decided_by_builder": False,
        "legal_determination_claimed": False,
        "whole_organism_passed": False,
    }
    return floor_seal_release_gate_receipt(out)


__all__ = [
    "RELEASE_GATE_STATES",
    "RELEASE_HUMAN_VERDICTS",
    "RELEASE_RIGHTS_STATES",
    "floor_seal_release_candidate",
    "floor_seal_human_review_request",
    "floor_human_review_request",
    "floor_seal_human_musical_review",
    "floor_seal_rights_review",
    "floor_seal_release_gate_receipt",
    "floor_build_release_gate",
]
