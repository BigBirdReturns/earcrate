"""Proof-carrying review, rights, and publication governance.

This module is the stable public facade. Builders and signal evaluators may
propose and qualify artifacts; only committed independent review, a separate
use-scoped rights decision, and an exact publish permit may authorize atomic
publication.
"""

from __future__ import annotations

from typing import Any, Sequence

from earcrate.floor.model import floor_sha256_json
from earcrate.floor._governance_common import GOVERNANCE_SCHEMA_VERSION
from earcrate.floor._governance_campaign import (
    floor_open_blind_review_campaign,
    floor_review_assignments,
)
from earcrate.floor._governance_decision import (
    floor_decide_governed_release,
    floor_seal_arbitration_assignment,
    floor_seal_arbitration_review,
    floor_seal_blind_review,
    floor_seal_rights_decision,
)
from earcrate.floor._governance_publish import (
    floor_issue_publish_permit,
    floor_publish_release,
    floor_verify_published_release,
)


def floor_release_governance_capability() -> dict[str, Any]:
    value = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_release_governance_capability",
        "ready": True,
        "objects": [
            "ReviewPolicy",
            "PublicReviewCampaign",
            "PrivateAssignmentAuthority",
            "ReviewCampaignBundle",
            "BlindHumanReview",
            "ArbitrationAssignment",
            "ArbitrationReview",
            "RightsDecision",
            "GovernedReleaseDecision",
            "PublishPermit",
            "PublicationReceipt",
        ],
        "invariants": {
            "per_reviewer_option_permutation": True,
            "private_assignment_authority_committed": True,
            "review_binds_campaign_candidate_control_policy_assignment_and_authentication": True,
            "split_vote_requires_independent_arbitration": True,
            "rights_are_use_scoped_and_time_bounded": True,
            "publication_roles_are_format_neutral": True,
            "publication_is_staged_and_atomic": True,
            "publication_receipt_is_content_addressed": True,
            "whole_organism_passage_implied": False,
        },
        "schema_files": sorted(floor_release_governance_schema_bundle()),
    }
    value["capability_sha256"] = floor_sha256_json(value)
    return value


def floor_release_governance_schema_bundle() -> dict[str, dict[str, Any]]:
    """Return the versioned public schemas for governance-v2 sealed objects."""

    sha_schema = {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }

    def schema(
        kind: str,
        title: str,
        hash_field: str,
        required: Sequence[str],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "schema_version": {"const": GOVERNANCE_SCHEMA_VERSION},
            "kind": {"const": kind},
            hash_field: sha_schema,
        }
        for field in required:
            if field.endswith("_sha256") or field == "review_token":
                properties[field] = sha_schema
            elif field in {
                "assignments",
                "options",
                "artifacts",
                "review_sha256s",
                "jurisdictions",
                "channels",
                "files",
                "dimensions",
                "notes",
            }:
                properties[field] = {"type": "array"}
            elif field in {
                "candidate",
                "signal_evaluation",
                "control",
                "public_campaign",
                "private_assignment_authority",
                "review_policy",
                "rights",
                "arbitration_assignment",
            }:
                properties[field] = {"type": "object"}
            elif field in {
                "complete",
                "release_eligible",
                "whole_organism_passed",
                "atomic_directory_promotion",
                "machine_generated",
            }:
                properties[field] = {"type": "boolean"}
            elif field in {"minimum_reviewers", "artifact_count"}:
                properties[field] = {
                    "type": "integer",
                    "minimum": 0,
                }
            else:
                properties[field] = {
                    "type": ["string", "null"],
                }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://earcrate.local/schema/{kind}_v2.schema.json",
            "title": title,
            "type": "object",
            "required": [
                "schema_version",
                "kind",
                *required,
                hash_field,
            ],
            "properties": properties,
            "additionalProperties": True,
        }

    specifications = [
        (
            "earcrate_floor_review_policy",
            "EarCrate review policy v2",
            "review_policy_sha256",
            [
                "policy_id",
                "minimum_reviewers",
                "dimensions",
                "comparison",
            ],
        ),
        (
            "earcrate_floor_public_review_assignment",
            "EarCrate public review assignment v2",
            "public_assignment_sha256",
            [
                "campaign_core_sha256",
                "assignment_id",
                "reviewer_id",
                "review_policy_sha256",
                "options",
            ],
        ),
        (
            "earcrate_floor_private_assignment_authority",
            "EarCrate private assignment authority v2",
            "private_assignment_authority_sha256",
            [
                "campaign_core_sha256",
                "blinding_seed_sha256",
                "assignments",
            ],
        ),
        (
            "earcrate_floor_public_review_campaign",
            "EarCrate public review campaign v2",
            "campaign_sha256",
            [
                "campaign_id",
                "candidate_sha256",
                "control_sha256",
                "signal_evaluation_sha256",
                "campaign_core_sha256",
                "review_role",
                "minimum_reviewers",
                "review_policy",
                "assignments",
                "private_assignment_authority_sha256",
            ],
        ),
        (
            "earcrate_floor_review_campaign_bundle",
            "EarCrate review campaign bundle v2",
            "campaign_bundle_sha256",
            [
                "candidate",
                "signal_evaluation",
                "control",
                "public_campaign",
                "private_assignment_authority",
            ],
        ),
        (
            "earcrate_floor_blind_human_review",
            "EarCrate blind human review v2",
            "review_sha256",
            [
                "campaign_sha256",
                "campaign_core_sha256",
                "candidate_sha256",
                "control_sha256",
                "review_policy_sha256",
                "private_assignment_authority_sha256",
                "assignment_id",
                "public_assignment_sha256",
                "private_assignment_sha256",
                "reviewer_id",
                "authentication_sha256",
                "review_token",
                "preferred_option",
                "dimensions",
                "notes",
                "machine_generated",
            ],
        ),
        (
            "earcrate_floor_arbitration_assignment",
            "EarCrate arbitration assignment v2",
            "arbitration_assignment_sha256",
            [
                "campaign_sha256",
                "candidate_sha256",
                "control_sha256",
                "review_sha256s",
                "arbitrator_id",
                "authentication_sha256",
                "reason",
            ],
        ),
        (
            "earcrate_floor_arbitration_review",
            "EarCrate arbitration review v2",
            "arbitration_review_sha256",
            [
                "campaign_sha256",
                "candidate_sha256",
                "control_sha256",
                "arbitration_assignment_sha256",
                "arbitration_assignment",
                "review_sha256s",
                "arbitrator_id",
                "authentication_sha256",
                "verdict",
                "notes",
            ],
        ),
        (
            "earcrate_floor_rights_decision",
            "EarCrate use-scoped rights decision v2",
            "rights_decision_sha256",
            [
                "campaign_sha256",
                "candidate_sha256",
                "status",
                "policy_id",
                "declared_use",
                "jurisdictions",
                "channels",
                "valid_from",
                "expires_at",
                "decided_by",
                "authentication_sha256",
            ],
        ),
        (
            "earcrate_floor_governed_release_decision",
            "EarCrate governed release decision v2",
            "decision_sha256",
            [
                "campaign_sha256",
                "campaign_bundle_sha256",
                "candidate_sha256",
                "control_sha256",
                "as_of",
                "status",
                "summary",
                "release_eligible",
                "review_sha256s",
                "whole_organism_passed",
            ],
        ),
        (
            "earcrate_floor_publish_permit",
            "EarCrate publish permit v2",
            "permit_sha256",
            [
                "campaign_sha256",
                "decision_sha256",
                "candidate_sha256",
                "rights_decision_sha256",
                "declared_use",
                "jurisdictions",
                "channels",
                "issued_at",
                "expires_at",
                "artifacts",
            ],
        ),
        (
            "earcrate_floor_publication_receipt",
            "EarCrate publication receipt v2",
            "publication_receipt_sha256",
            [
                "campaign_sha256",
                "candidate_sha256",
                "permit_sha256",
                "publication_manifest_sha256",
                "checksums_sha256",
                "published_at",
                "artifact_count",
                "atomic_directory_promotion",
                "durability_mode",
                "complete",
            ],
        ),
    ]
    bundle = {
        f"{kind}_v2.schema.json": schema(
            kind,
            title,
            hash_field,
            required,
        )
        for kind, title, hash_field, required in specifications
    }

    blind = bundle["earcrate_floor_blind_human_review_v2.schema.json"]
    blind["properties"]["dimensions"] = {
        "type": "object",
        "minProperties": 1,
        "additionalProperties": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
    }
    blind["properties"]["notes"] = {
        "type": "array",
        "items": {"type": "string"},
    }
    blind["properties"]["machine_generated"] = {"const": False}
    blind["additionalProperties"] = False

    arbitration_assignment = bundle[
        "earcrate_floor_arbitration_assignment_v2.schema.json"
    ]
    arbitration_assignment["additionalProperties"] = False

    arbitration_review = bundle[
        "earcrate_floor_arbitration_review_v2.schema.json"
    ]
    arbitration_review["properties"]["notes"] = {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
    }
    arbitration_review["additionalProperties"] = False

    return bundle


__all__ = [
    "GOVERNANCE_SCHEMA_VERSION",
    "floor_open_blind_review_campaign",
    "floor_review_assignments",
    "floor_seal_blind_review",
    "floor_seal_arbitration_assignment",
    "floor_seal_arbitration_review",
    "floor_seal_rights_decision",
    "floor_decide_governed_release",
    "floor_issue_publish_permit",
    "floor_publish_release",
    "floor_verify_published_release",
    "floor_release_governance_schema_bundle",
    "floor_release_governance_capability",
]
