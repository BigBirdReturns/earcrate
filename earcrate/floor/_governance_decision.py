"""Human review, arbitration, rights, and governed release decisions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from earcrate.floor.model import FloorError
from earcrate.floor._governance_common import (
    GOVERNANCE_SCHEMA_VERSION,
    _mapping,
    _parse_time,
    _sealed,
    _sequence,
    _sha,
    _text,
)
from earcrate.floor._governance_campaign import (
    _private_assignment,
    _seal_campaign_bundle,
)

def floor_seal_blind_review(campaign: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    raw = _mapping(value, "blind review")
    assignment_id = _text(raw.get("assignment_id"), "review assignment_id")
    assignment = _private_assignment(bundle, assignment_id)
    reviewer_id = _text(raw.get("reviewer_id"), "review reviewer_id")
    if reviewer_id != assignment["reviewer_id"]:
        raise FloorError("reviewer identity does not match the committed assignment")
    if _sha(raw.get("authentication_sha256"), "review authentication_sha256") != assignment["authentication_sha256"]:
        raise FloorError("review is missing the committed external authentication evidence")
    if _sha(raw.get("review_token"), "review_token") != assignment["review_token"]:
        raise FloorError("review token does not match the private assignment authority")
    preferred = _text(raw.get("preferred_option"), "preferred_option")
    if preferred not in {"A", "B", "abstain"}:
        raise FloorError("preferred_option must be A, B, or abstain")
    if preferred == "abstain" and not bundle["public_campaign"]["review_policy"]["allow_abstain"]:
        raise FloorError("review policy does not allow abstention")
    dimensions_raw = _mapping(raw.get("dimensions"), "review dimensions")
    dimensions: dict[str, float] = {}
    required_dimensions = bundle["public_campaign"]["review_policy"]["dimensions"]
    if set(dimensions_raw) != set(required_dimensions):
        raise FloorError("review dimensions do not match the committed review policy")
    for key in required_dimensions:
        try:
            number = float(dimensions_raw[key])
        except (TypeError, ValueError) as exc:
            raise FloorError(f"review dimension {key} must be numeric") from exc
        if not 0.0 <= number <= 1.0:
            raise FloorError(f"review dimension {key} must be between 0 and 1")
        dimensions[key] = number
    notes = [str(row) for row in _sequence(raw.get("notes") or [], "review notes")]
    if preferred == "abstain" and not notes:
        raise FloorError("abstaining review requires a note")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_blind_human_review",
        "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
        "campaign_core_sha256": bundle["public_campaign"]["campaign_core_sha256"],
        "candidate_sha256": bundle["candidate"]["candidate_sha256"],
        "control_sha256": bundle["control"]["control_sha256"],
        "review_policy_sha256": bundle["public_campaign"]["review_policy_sha256"],
        "private_assignment_authority_sha256": bundle["private_assignment_authority"]["private_assignment_authority_sha256"],
        "assignment_id": assignment_id,
        "public_assignment_sha256": assignment["public_assignment_sha256"],
        "private_assignment_sha256": assignment["private_assignment_sha256"],
        "reviewer_id": reviewer_id,
        "authentication_sha256": assignment["authentication_sha256"],
        "review_token": assignment["review_token"],
        "preferred_option": preferred,
        "dimensions": dimensions,
        "notes": notes,
        "machine_generated": False,
    }
    claimed = raw.get("review_sha256")
    sealed = _sealed(out, "review_sha256")
    if claimed is not None and claimed != sealed["review_sha256"]:
        raise FloorError("review_sha256 hash mismatch; review was mutated after commit")
    return sealed


def _seal_reviews(campaign: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sealed = [floor_seal_blind_review(campaign, row) for row in reviews]
    assignments = [row["assignment_id"] for row in sealed]
    reviewers = [row["reviewer_id"] for row in sealed]
    if len(set(assignments)) != len(assignments) or len(set(reviewers)) != len(reviewers):
        raise FloorError("one immutable review is allowed per reviewer assignment")
    return sealed


def _review_roles(bundle: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]]) -> list[str]:
    roles = []
    for review in reviews:
        if review["preferred_option"] == "abstain":
            continue
        assignment = _private_assignment(bundle, review["assignment_id"])
        roles.append(assignment["option_map"][review["preferred_option"]])
    return roles


def floor_seal_arbitration_assignment(campaign: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]], value: Mapping[str, Any]) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    sealed_reviews = _seal_reviews(bundle, reviews)
    roles = _review_roles(bundle, sealed_reviews)
    if not ("candidate" in roles and "control" in roles):
        raise FloorError("arbitration may be assigned only for a split completed review quorum")
    raw = _mapping(value, "arbitration assignment")
    arbitrator_id = _text(raw.get("arbitrator_id"), "arbitrator_id")
    forbidden = {
        bundle["candidate"]["builder"]["identity_id"],
        bundle["signal_evaluation"]["evaluator"]["identity_id"],
        *[row["reviewer_id"] for row in sealed_reviews],
    }
    if arbitrator_id in forbidden:
        raise FloorError("arbitrator must be independent of builder, evaluator, and assigned reviewers")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_arbitration_assignment",
        "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
        "candidate_sha256": bundle["candidate"]["candidate_sha256"],
        "control_sha256": bundle["control"]["control_sha256"],
        "review_sha256s": sorted(row["review_sha256"] for row in sealed_reviews),
        "arbitrator_id": arbitrator_id,
        "authentication_sha256": _sha(raw.get("authentication_sha256"), "arbitration authentication_sha256"),
        "reason": _text(raw.get("reason") or "split human review", "arbitration reason"),
    }
    return _sealed(out, "arbitration_assignment_sha256")


def floor_seal_arbitration_review(campaign: Mapping[str, Any], assignment: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    sealed_assignment = _sealed(_mapping(assignment, "arbitration assignment"), "arbitration_assignment_sha256")
    if sealed_assignment["campaign_sha256"] != bundle["public_campaign"]["campaign_sha256"]:
        raise FloorError("arbitration assignment belongs to another campaign")
    raw = _mapping(value, "arbitration review")
    if _text(raw.get("arbitrator_id"), "arbitrator_id") != sealed_assignment["arbitrator_id"]:
        raise FloorError("arbitration review identity does not match its assignment")
    if _sha(raw.get("authentication_sha256"), "arbitration authentication_sha256") != sealed_assignment["authentication_sha256"]:
        raise FloorError("arbitration review lacks the assigned authentication evidence")
    verdict = _text(raw.get("verdict"), "arbitration verdict")
    if verdict not in {"candidate", "control", "abstain"}:
        raise FloorError("arbitration verdict must be candidate, control, or abstain")
    notes = [str(row) for row in _sequence(raw.get("notes") or [], "arbitration notes")]
    if not notes:
        raise FloorError("arbitration review requires explanatory notes")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_arbitration_review",
        "campaign_sha256": sealed_assignment["campaign_sha256"],
        "candidate_sha256": sealed_assignment["candidate_sha256"],
        "control_sha256": sealed_assignment["control_sha256"],
        "arbitration_assignment_sha256": sealed_assignment["arbitration_assignment_sha256"],
        "review_sha256s": sealed_assignment["review_sha256s"],
        "arbitrator_id": sealed_assignment["arbitrator_id"],
        "authentication_sha256": sealed_assignment["authentication_sha256"],
        "verdict": verdict,
        "notes": notes,
    }
    return _sealed(out, "arbitration_review_sha256")


def floor_seal_rights_decision(campaign: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    raw = _mapping(value, "rights decision")
    if bool(raw.get("legal_determination", False)):
        raise FloorError("rights policy may not claim a legal determination")
    authority = _text(raw.get("decided_by"), "rights decided_by")
    forbidden = {
        bundle["candidate"]["builder"]["identity_id"],
        bundle["signal_evaluation"]["evaluator"]["identity_id"],
        *[row["reviewer_id"] for row in bundle["private_assignment_authority"]["assignments"]],
    }
    if authority in forbidden:
        raise FloorError("rights authority must be independent of execution and review roles")
    status_value = _text(raw.get("status"), "rights status")
    if status_value not in {"accepted_by_policy", "blocked", "expired", "not_evaluated"}:
        raise FloorError("unsupported rights status")
    valid_from, valid_from_dt = _parse_time(raw.get("valid_from"), "rights valid_from")
    expires_at, expires_at_dt = _parse_time(raw.get("expires_at"), "rights expires_at")
    if expires_at_dt <= valid_from_dt:
        raise FloorError("rights expiry must be later than valid_from")
    declared_use = _text(raw.get("declared_use"), "rights declared_use")
    jurisdictions = sorted({_text(row, "rights jurisdiction") for row in _sequence(raw.get("jurisdictions"), "rights jurisdictions")})
    channels = sorted({_text(row, "rights channel") for row in _sequence(raw.get("channels"), "rights channels")})
    if not jurisdictions or not channels:
        raise FloorError("rights decision requires at least one jurisdiction and channel")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_rights_decision",
        "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
        "candidate_sha256": bundle["candidate"]["candidate_sha256"],
        "status": status_value,
        "policy_id": _text(raw.get("policy_id"), "rights policy_id"),
        "declared_use": declared_use,
        "jurisdictions": jurisdictions,
        "channels": channels,
        "valid_from": valid_from,
        "expires_at": expires_at,
        "decided_by": authority,
        "authentication_sha256": _sha(raw.get("authentication_sha256"), "rights authentication_sha256"),
        "evidence_refs": sorted({_text(row, "rights evidence reference") for row in _sequence(raw.get("evidence_refs") or [], "rights evidence_refs")}),
        "legal_determination": False,
    }
    return _sealed(out, "rights_decision_sha256")


def floor_decide_governed_release(
    campaign: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    rights_decision: Mapping[str, Any] | None,
    *,
    as_of: str,
    arbitration_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    sealed_reviews = _seal_reviews(bundle, reviews)
    _, as_of_dt = _parse_time(as_of, "decision as_of")
    roles = _review_roles(bundle, sealed_reviews)
    minimum = bundle["public_campaign"]["minimum_reviewers"]
    completed = len(roles)
    selected: str | None = None
    arbitration_sha: str | None = None
    if completed < minimum:
        status_value, summary = "blocked", "review_quorum_pending"
    elif "candidate" in roles and "control" in roles:
        if arbitration_review is None:
            status_value, summary = "blocked", "needs_arbitration"
        else:
            arbitration = _sealed(_mapping(arbitration_review, "arbitration review"), "arbitration_review_sha256")
            if arbitration["campaign_sha256"] != bundle["public_campaign"]["campaign_sha256"]:
                raise FloorError("arbitration review belongs to another campaign")
            if sorted(arbitration["review_sha256s"]) != sorted(row["review_sha256"] for row in sealed_reviews):
                raise FloorError("arbitration review does not bind the exact review set")
            arbitration_sha = arbitration["arbitration_review_sha256"]
            if arbitration["verdict"] == "abstain":
                status_value, summary = "blocked", "arbitration_abstained"
            elif arbitration["verdict"] == "control":
                status_value, summary, selected = "refused", "no_edit_preferred", "control"
            else:
                selected = "candidate"
                status_value, summary = "blocked", "rights_review_pending"
    elif roles and all(role == "control" for role in roles):
        status_value, summary, selected = "refused", "no_edit_preferred", "control"
    elif roles and all(role == "candidate" for role in roles):
        selected = "candidate"
        status_value, summary = "blocked", "rights_review_pending"
    else:
        status_value, summary = "blocked", "review_quorum_pending"

    rights: dict[str, Any] | None = None
    if selected == "candidate":
        if rights_decision is not None:
            rights = floor_seal_rights_decision(bundle, rights_decision)
            valid_from_dt = _parse_time(rights["valid_from"], "rights valid_from")[1]
            expires_at_dt = _parse_time(rights["expires_at"], "rights expires_at")[1]
            if not (valid_from_dt <= as_of_dt < expires_at_dt):
                status_value, summary = "refused", "rights_expired"
            elif rights["status"] != "accepted_by_policy":
                status_value, summary = "refused" if rights["status"] in {"blocked", "expired"} else "blocked", "rights_blocked" if rights["status"] in {"blocked", "expired"} else "rights_review_pending"
            else:
                status_value, summary = "eligible", "release_eligible"
    decision = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_governed_release_decision",
        "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
        "campaign_bundle_sha256": bundle["campaign_bundle_sha256"],
        "candidate_sha256": bundle["candidate"]["candidate_sha256"],
        "control_sha256": bundle["control"]["control_sha256"],
        "as_of": _parse_time(as_of, "decision as_of")[0],
        "status": status_value,
        "summary": summary,
        "selected_role": selected,
        "release_eligible": status_value == "eligible" and selected == "candidate",
        "review_sha256s": sorted(row["review_sha256"] for row in sealed_reviews),
        "arbitration_review_sha256": arbitration_sha,
        "rights_decision_sha256": None if rights is None else rights["rights_decision_sha256"],
        "rights": rights,
        "whole_organism_passed": False,
    }
    return _sealed(decision, "decision_sha256")
