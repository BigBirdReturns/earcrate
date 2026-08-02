"""Blinded review campaign and assignment authorities."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from earcrate.floor.model import FloorError, floor_sha256_json
from earcrate.floor.release import floor_seal_release_candidate, floor_seal_signal_evaluation
from earcrate.floor._governance_common import (
    GOVERNANCE_SCHEMA_VERSION,
    _artifact_by_role,
    _artifact_descriptor,
    _candidate_artifacts,
    _integer,
    _mapping,
    _option_descriptor,
    _permutation,
    _review_policy,
    _seal_control,
    _sealed,
    _sequence,
    _sha,
    _text,
)

def floor_open_blind_review_campaign(value: Mapping[str, Any]) -> dict[str, Any]:
    """Create an auditable campaign and a separately sealed private assignment authority."""
    raw = _mapping(value, "campaign")
    campaign_id = _text(raw.get("campaign_id"), "campaign_id")
    candidate = floor_seal_release_candidate(_mapping(raw.get("candidate"), "candidate"))
    signal = floor_seal_signal_evaluation(_mapping(raw.get("signal_evaluation"), "signal_evaluation"), candidate)
    if signal["status"] != "passed":
        raise FloorError("blind review may open only after independent signal evaluation passes")
    candidate_artifacts = _candidate_artifacts(candidate, _mapping(raw.get("candidate_artifact_roles"), "candidate_artifact_roles"))
    control = _seal_control(_mapping(raw.get("control"), "control"))
    review_role = _text(raw.get("review_role") or "reviewed_audition", "review_role")
    candidate_review_artifact = _artifact_by_role(candidate_artifacts, review_role, "candidate review")
    control_review_artifact = _artifact_by_role(control["artifacts"], review_role, "control review")
    if candidate_review_artifact["sha256"] == control_review_artifact["sha256"]:
        raise FloorError("candidate and control review artifacts must be content-distinct")

    reviewers_raw = _sequence(raw.get("reviewers"), "reviewers")
    reviewers: list[dict[str, str]] = []
    for index, item in enumerate(reviewers_raw):
        row = _mapping(item, f"reviewer {index}")
        reviewers.append(
            {
                "reviewer_id": _text(row.get("reviewer_id"), f"reviewer {index} reviewer_id"),
                "authentication_sha256": _sha(row.get("authentication_sha256"), f"reviewer {index} authentication_sha256"),
            }
        )
    if len(reviewers) < 2 or len({row["reviewer_id"] for row in reviewers}) != len(reviewers):
        raise FloorError("review campaign requires at least two unique human reviewers")
    minimum = _integer(raw.get("minimum_reviewers"), "minimum_reviewers", minimum=2)
    if minimum > len(reviewers):
        raise FloorError("minimum_reviewers exceeds assigned reviewers")

    builder_id = candidate["builder"]["identity_id"]
    evaluator_id = signal["evaluator"]["identity_id"]
    forbidden = {builder_id, evaluator_id}
    if forbidden.intersection({row["reviewer_id"] for row in reviewers}):
        raise FloorError("reviewers must be independent of the candidate builder and signal evaluator")

    policy = _review_policy(_mapping(raw.get("review_policy"), "review_policy"), minimum)
    seed = _sha(raw.get("blinding_seed_sha256"), "blinding_seed_sha256")
    core = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_public_review_campaign_core",
        "campaign_id": campaign_id,
        "candidate_sha256": candidate["candidate_sha256"],
        "control_sha256": control["control_sha256"],
        "signal_evaluation_sha256": signal["signal_evaluation_sha256"],
        "builder_identity_id": builder_id,
        "signal_evaluator_identity_id": evaluator_id,
        "review_role": review_role,
        "review_policy_sha256": policy["review_policy_sha256"],
        "minimum_reviewers": minimum,
    }
    core_sha = floor_sha256_json(core)

    public_assignments: list[dict[str, Any]] = []
    private_assignments: list[dict[str, Any]] = []
    for index, reviewer in enumerate(reviewers):
        assignment_id = floor_sha256_json(
            {
                "campaign_core_sha256": core_sha,
                "reviewer_id": reviewer["reviewer_id"],
                "assignment_index": index,
            }
        )
        option_map = _permutation(seed, assignment_id)
        role_to_artifact = {"candidate": candidate_review_artifact, "control": control_review_artifact}
        options = [_option_descriptor(label, role_to_artifact[option_map[label]]) for label in ("A", "B")]
        public_assignment = _sealed(
            {
                "schema_version": GOVERNANCE_SCHEMA_VERSION,
                "kind": "earcrate_floor_public_review_assignment",
                "campaign_core_sha256": core_sha,
                "assignment_id": assignment_id,
                "reviewer_id": reviewer["reviewer_id"],
                "review_policy_sha256": policy["review_policy_sha256"],
                "options": options,
            },
            "public_assignment_sha256",
        )
        review_token = floor_sha256_json(
            {
                "blinding_seed_sha256": seed,
                "assignment_id": assignment_id,
                "reviewer_id": reviewer["reviewer_id"],
            }
        )
        private_assignment = _sealed(
            {
                "schema_version": GOVERNANCE_SCHEMA_VERSION,
                "kind": "earcrate_floor_private_review_assignment",
                "campaign_core_sha256": core_sha,
                "assignment_id": assignment_id,
                "public_assignment_sha256": public_assignment["public_assignment_sha256"],
                "reviewer_id": reviewer["reviewer_id"],
                "authentication_sha256": reviewer["authentication_sha256"],
                "review_token": review_token,
                "option_map": option_map,
            },
            "private_assignment_sha256",
        )
        public_assignments.append(public_assignment)
        private_assignments.append(private_assignment)

    private_authority = _sealed(
        {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_private_assignment_authority",
            "campaign_core_sha256": core_sha,
            "blinding_seed_sha256": seed,
            "assignments": private_assignments,
        },
        "private_assignment_authority_sha256",
    )
    public_campaign = _sealed(
        {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_public_review_campaign",
            **{key: value for key, value in core.items() if key not in {"schema_version", "kind"}},
            "campaign_core_sha256": core_sha,
            "review_policy": policy,
            "assignments": public_assignments,
            "private_assignment_authority_sha256": private_authority["private_assignment_authority_sha256"],
        },
        "campaign_sha256",
    )
    bundle = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_review_campaign_bundle",
        "candidate": candidate,
        "candidate_artifacts": candidate_artifacts,
        "signal_evaluation": signal,
        "control": control,
        "public_campaign": public_campaign,
        "private_assignment_authority": private_authority,
    }
    return _sealed(bundle, "campaign_bundle_sha256")


def _seal_campaign_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(value, "campaign bundle")
    claimed_bundle = raw.get("campaign_bundle_sha256")
    candidate = floor_seal_release_candidate(_mapping(raw.get("candidate"), "campaign candidate"))
    signal = floor_seal_signal_evaluation(_mapping(raw.get("signal_evaluation"), "campaign signal"), candidate)
    candidate_artifacts = [_artifact_descriptor(row, f"campaign candidate artifact {index}") for index, row in enumerate(_sequence(raw.get("candidate_artifacts"), "campaign candidate_artifacts"))]
    control = _seal_control(_mapping(raw.get("control"), "campaign control"))
    public = _sealed(_mapping(raw.get("public_campaign"), "public campaign"), "campaign_sha256")
    authority = _sealed(_mapping(raw.get("private_assignment_authority"), "private assignment authority"), "private_assignment_authority_sha256")
    if public["candidate_sha256"] != candidate["candidate_sha256"]:
        raise FloorError("campaign candidate commitment does not match the sealed ReleaseCandidate")
    if public["signal_evaluation_sha256"] != signal["signal_evaluation_sha256"]:
        raise FloorError("campaign signal commitment does not match the sealed SignalEvaluation")
    if public["control_sha256"] != control["control_sha256"]:
        raise FloorError("campaign control commitment does not match the sealed control")
    if public["private_assignment_authority_sha256"] != authority["private_assignment_authority_sha256"]:
        raise FloorError("public campaign does not commit the private assignment authority")
    if public["campaign_core_sha256"] != authority["campaign_core_sha256"]:
        raise FloorError("public and private campaign authorities have different cores")
    public_by_id = {row["assignment_id"]: _sealed(row, "public_assignment_sha256") for row in public["assignments"]}
    private_by_id = {row["assignment_id"]: _sealed(row, "private_assignment_sha256") for row in authority["assignments"]}
    if set(public_by_id) != set(private_by_id):
        raise FloorError("public and private assignment sets differ")
    for assignment_id, private_row in private_by_id.items():
        if private_row["public_assignment_sha256"] != public_by_id[assignment_id]["public_assignment_sha256"]:
            raise FloorError("private assignment does not commit its public assignment")
    bundle = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_review_campaign_bundle",
        "candidate": candidate,
        "candidate_artifacts": candidate_artifacts,
        "signal_evaluation": signal,
        "control": control,
        "public_campaign": public,
        "private_assignment_authority": authority,
    }
    sealed = _sealed(bundle, "campaign_bundle_sha256")
    if claimed_bundle is not None and claimed_bundle != sealed["campaign_bundle_sha256"]:
        raise FloorError("campaign_bundle_sha256 hash mismatch; bundle was mutated")
    return sealed


def floor_review_assignments(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    bundle = _seal_campaign_bundle(campaign)
    public_by_id = {row["assignment_id"]: row for row in bundle["public_campaign"]["assignments"]}
    packets = []
    for private_row in bundle["private_assignment_authority"]["assignments"]:
        public = public_by_id[private_row["assignment_id"]]
        packets.append(
            {
                "schema_version": GOVERNANCE_SCHEMA_VERSION,
                "kind": "earcrate_floor_review_assignment_packet",
                "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
                "campaign_core_sha256": bundle["public_campaign"]["campaign_core_sha256"],
                "candidate_sha256": bundle["candidate"]["candidate_sha256"],
                "control_sha256": bundle["control"]["control_sha256"],
                "review_policy_sha256": bundle["public_campaign"]["review_policy_sha256"],
                "private_assignment_authority_sha256": bundle["private_assignment_authority"]["private_assignment_authority_sha256"],
                "assignment_id": private_row["assignment_id"],
                "public_assignment_sha256": private_row["public_assignment_sha256"],
                "private_assignment_sha256": private_row["private_assignment_sha256"],
                "reviewer_id": private_row["reviewer_id"],
                "authentication_sha256": private_row["authentication_sha256"],
                "review_token": private_row["review_token"],
                "options": deepcopy(public["options"]),
                "dimensions": deepcopy(bundle["public_campaign"]["review_policy"]["dimensions"]),
                "allow_abstain": bool(bundle["public_campaign"]["review_policy"]["allow_abstain"]),
            }
        )
    return packets


def _private_assignment(bundle: Mapping[str, Any], assignment_id: str) -> dict[str, Any]:
    matches = [row for row in bundle["private_assignment_authority"]["assignments"] if row["assignment_id"] == assignment_id]
    if len(matches) != 1:
        raise FloorError("review assignment does not belong to this campaign")
    return dict(matches[0])
