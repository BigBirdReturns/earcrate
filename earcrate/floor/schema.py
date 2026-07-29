from __future__ import annotations

"""Committed JSON Schemas for the EarCrate Open Music Evidence Floor."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .model import (
    FLOOR_EMISSION_KINDS,
    FLOOR_EVIDENCE_BRANCHES,
    FLOOR_EVIDENCE_TIERS,
    FLOOR_NETWORK_POLICIES,
    FLOOR_RESULT_STATUSES,
    FLOOR_SCHEMA_VERSION,
    floor_write_json_atomic,
)

_FLOOR_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_FLOOR_SHA_OR_NULL = {"anyOf": [_FLOOR_SHA, {"type": "null"}]}


def _floor_schema_base(kind: str, title: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://earcrate.local/schema/{kind}_v1.schema.json",
        "title": title,
        "type": "object",
    }


def floor_schema_bundle() -> dict[str, dict[str, Any]]:
    artifact = {
        "type": "object",
        "required": ["artifact_id", "sha256", "size_bytes", "media_kind"],
        "properties": {
            "artifact_id": {"type": "string", "minLength": 1},
            "sha256": _FLOOR_SHA,
            "size_bytes": {"type": "integer", "minimum": 0},
            "media_kind": {"type": "string", "minLength": 1},
            "role": {"type": "string"},
            "branch": {"enum": list(FLOOR_EVIDENCE_BRANCHES) + [""]},
            "ancestor_branches": {"type": "array", "items": {"enum": list(FLOOR_EVIDENCE_BRANCHES)}, "uniqueItems": True},
            "path": {"type": "string"},
            "uri": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "additionalProperties": False,
    }

    manifest = _floor_schema_base("earcrate_floor_provider_manifest", "EarCrate Floor provider manifest v1")
    manifest.update(
        {
            "required": [
                "schema_version", "kind", "provider_id", "provider_version", "display_name",
                "protocol", "entrypoint", "capabilities", "authority", "supply_chain", "metadata", "manifest_sha256",
            ],
            "properties": {
                "schema_version": {"const": FLOOR_SCHEMA_VERSION},
                "kind": {"const": "earcrate_floor_provider_manifest"},
                "provider_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,127}$"},
                "provider_version": {"type": "string", "minLength": 1},
                "display_name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "protocol": {
                    "type": "object",
                    "required": ["name", "version"],
                    "properties": {"name": {"const": "earcrate-floor-stdio-json"}, "version": {"const": 1}},
                    "additionalProperties": False,
                },
                "entrypoint": {
                    "type": "object",
                    "required": ["argv", "working_directory", "environment"],
                    "properties": {
                        "argv": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                        "working_directory": {"type": "string", "minLength": 1},
                        "environment": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
                "capabilities": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "capability", "input_media_kinds", "result_kinds", "evidence_branches",
                            "evidence_tiers", "network_policy", "determinism", "max_runtime_seconds",
                            "max_output_bytes", "parameter_schema", "metadata",
                        ],
                        "properties": {
                            "capability": {"type": "string", "minLength": 1},
                            "input_media_kinds": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                            "result_kinds": {"type": "array", "minItems": 1, "items": {"enum": list(FLOOR_EMISSION_KINDS)}, "uniqueItems": True},
                            "evidence_branches": {"type": "array", "minItems": 1, "items": {"enum": list(FLOOR_EVIDENCE_BRANCHES)}, "uniqueItems": True},
                            "evidence_tiers": {"type": "array", "minItems": 1, "items": {"enum": list(FLOOR_EVIDENCE_TIERS)}, "uniqueItems": True},
                            "network_policy": {"enum": list(FLOOR_NETWORK_POLICIES)},
                            "determinism": {"enum": ["unknown", "best_effort", "repeatable", "bit_exact"]},
                            "max_runtime_seconds": {"type": "integer", "minimum": 1},
                            "max_output_bytes": {"type": "integer", "minimum": 1},
                            "parameter_schema": {"type": "object"},
                            "metadata": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                },
                "authority": {
                    "type": "object",
                    "required": ["may_emit", "may_not_emit", "canonical_write_access", "review_patch_apply_access", "legal_decision_access"],
                    "properties": {
                        "may_emit": {"type": "array", "items": {"enum": list(FLOOR_EMISSION_KINDS)}, "uniqueItems": True},
                        "may_not_emit": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                        "canonical_write_access": {"const": False},
                        "review_patch_apply_access": {"const": False},
                        "legal_decision_access": {"const": False},
                    },
                    "additionalProperties": False,
                },
                "supply_chain": {"type": "object"},
                "metadata": {"type": "object"},
                "manifest_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    request = _floor_schema_base("earcrate_floor_provider_request", "EarCrate Floor provider request v1")
    request.update(
        {
            "required": [
                "schema_version", "kind", "capability", "evidence_branch", "evidence_tier", "inputs",
                "parameters", "allowed_result_kinds", "forbidden_authority_claims", "network_policy",
                "limits", "context", "metadata", "request_sha256", "request_id",
            ],
            "properties": {
                "schema_version": {"const": FLOOR_SCHEMA_VERSION},
                "kind": {"const": "earcrate_floor_provider_request"},
                "capability": {"type": "string", "minLength": 1},
                "evidence_branch": {"enum": list(FLOOR_EVIDENCE_BRANCHES)},
                "evidence_tier": {"enum": list(FLOOR_EVIDENCE_TIERS)},
                "inputs": {"type": "array", "minItems": 1, "items": artifact},
                "parameters": {"type": "object"},
                "allowed_result_kinds": {"type": "array", "items": {"enum": list(FLOOR_EMISSION_KINDS)}, "uniqueItems": True},
                "forbidden_authority_claims": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "network_policy": {"enum": list(FLOOR_NETWORK_POLICIES)},
                "limits": {"type": "object"},
                "context": {"type": "object"},
                "metadata": {"type": "object"},
                "request_sha256": _FLOOR_SHA,
                "request_id": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
    )

    result = _floor_schema_base("earcrate_floor_provider_result", "EarCrate Floor provider result v1")
    result.update(
        {
            "required": [
                "schema_version", "kind", "request_sha256", "provider_manifest_sha256", "provider_id",
                "provider_version", "status", "emissions", "artifacts", "refusals", "metrics", "metadata",
                "semantic_result_sha256", "result_sha256",
            ],
            "properties": {
                "schema_version": {"const": FLOOR_SCHEMA_VERSION},
                "kind": {"const": "earcrate_floor_provider_result"},
                "request_sha256": _FLOOR_SHA,
                "provider_manifest_sha256": _FLOOR_SHA,
                "provider_id": {"type": "string", "minLength": 1},
                "provider_version": {"type": "string", "minLength": 1},
                "status": {"enum": list(FLOOR_RESULT_STATUSES)},
                "emissions": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": artifact},
                "refusals": {"type": "array", "items": {"type": "object"}},
                "metrics": {"type": "object"},
                "metadata": {"type": "object"},
                "semantic_result_sha256": _FLOOR_SHA,
                "result_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    phrase = _floor_schema_base("earcrate_floor_phrase_contract", "EarCrate Floor phrase contract v1")
    phrase.update(
        {
            "required": [
                "schema_version", "kind", "contract_id", "role", "start_beat", "length_beats", "meter",
                "transforms", "hard_constraints", "soft_objectives", "identity_obligations", "future_obligations",
                "evidence_refs", "rights", "metadata", "phrase_contract_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_phrase_contract"},
                "contract_id": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
                "start_beat": {"type": "string"},
                "length_beats": {"type": "string"},
                "meter": {"type": "object"},
                "entry_grammar": {"type": "object"},
                "exit_grammar": {"type": "object"},
                "transforms": {"type": "object"},
                "hard_constraints": {"type": "object"},
                "soft_objectives": {"type": "array"},
                "identity_obligations": {"type": "array", "minItems": 1},
                "future_obligations": {"type": "array"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "rights": {"type": "object"},
                "metadata": {"type": "object"},
                "phrase_contract_sha256": _FLOOR_SHA,
            },
            "additionalProperties": True,
        }
    )

    review = _floor_schema_base("earcrate_floor_review_patch", "EarCrate Floor unapplied review patch v1")
    review.update(
        {
            "required": [
                "schema_version", "kind", "patch_id", "target_revision_sha256", "target_object", "operations",
                "reason", "evidence_refs", "invalidation_hints", "proposed_by", "applied", "metadata", "review_patch_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_review_patch"},
                "patch_id": {"type": "string", "minLength": 1},
                "target_revision_sha256": _FLOOR_SHA,
                "target_object": {"type": "string", "minLength": 1},
                "operations": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "reason": {"type": "string", "minLength": 1},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "invalidation_hints": {"type": "array", "items": {"type": "string"}},
                "proposed_by": {"type": "object"},
                "applied": {"const": False},
                "metadata": {"type": "object"},
                "review_patch_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    receipt = _floor_schema_base("earcrate_floor_invocation_receipt", "EarCrate Floor invocation receipt v1")
    receipt.update(
        {
            "required": [
                "schema_version", "kind", "provider_id", "provider_version", "provider_manifest_sha256",
                "request_sha256", "argv", "working_directory", "executable", "input_custody", "output_custody",
                "stdout", "stderr", "process", "network", "resource_limits", "complete", "refusals", "metadata",
                "receipt_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_invocation_receipt"},
                "provider_id": {"type": "string"},
                "provider_version": {"type": "string"},
                "provider_manifest_sha256": _FLOOR_SHA,
                "request_sha256": _FLOOR_SHA,
                "result_sha256": _FLOOR_SHA_OR_NULL,
                "semantic_result_sha256": _FLOOR_SHA_OR_NULL,
                "argv": {"type": "array", "items": {"type": "string"}},
                "working_directory": {"type": "string"},
                "executable": {"type": "object"},
                "input_custody": {"type": "array"},
                "output_custody": {"type": "array"},
                "stdout": {"type": "object"},
                "stderr": {"type": "object"},
                "process": {"type": "object"},
                "network": {"type": "object"},
                "resource_limits": {"type": "object"},
                "complete": {"type": "boolean"},
                "refusals": {"type": "array"},
                "metadata": {"type": "object"},
                "receipt_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    evaluation = _floor_schema_base("earcrate_floor_evaluation_ledger", "EarCrate Floor evaluation ledger v1")
    evaluation.update(
        {
            "required": [
                "schema_version", "kind", "provider_id", "provider_manifest_sha256", "request_sha256",
                "result_sha256", "evaluator", "metrics", "hard_gate_evidence", "notes", "metadata", "evaluation_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_evaluation_ledger"},
                "provider_id": {"type": "string"},
                "provider_manifest_sha256": _FLOOR_SHA,
                "request_sha256": _FLOOR_SHA,
                "result_sha256": _FLOOR_SHA,
                "evaluator": {"type": "object"},
                "fixture_sha256": _FLOOR_SHA_OR_NULL,
                "metrics": {"type": "object", "additionalProperties": {"type": "number"}},
                "hard_gate_evidence": {"type": "object"},
                "notes": {"type": "array"},
                "metadata": {"type": "object"},
                "evaluation_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    crate = _floor_schema_base("earcrate_floor_crate", "EarCrate Floor portable crate v1")
    crate.update(
        {
            "required": ["schema_version", "kind", "files", "source_media_copied", "standards_mappings", "crate_sha256"],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_crate"},
                "files": {"type": "array", "items": {"type": "object"}},
                "source_media_copied": {"const": False},
                "standards_mappings": {"type": "array", "items": {"type": "string"}},
                "crate_sha256": _FLOOR_SHA,
            },
            "additionalProperties": True,
        }
    )

    rational = {"type": "string", "pattern": "^-?[0-9]+(?:/[1-9][0-9]*)?$"}

    time_map = _floor_schema_base("earcrate_floor_time_map", "EarCrate Floor source/performance TimeMap v1")
    time_map.update(
        {
            "required": ["schema_version", "kind", "time_unit", "segments", "metadata", "time_map_sha256"],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_time_map"},
                "time_unit": {"type": "string", "minLength": 1},
                "segments": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "segment_id", "lane_id", "target_start", "target_end", "source_artifact_id",
                            "source_start", "source_end", "mode", "rate", "metadata"
                        ],
                        "properties": {
                            "segment_id": {"type": "string", "minLength": 1},
                            "lane_id": {"type": "string", "minLength": 1},
                            "target_start": rational,
                            "target_end": rational,
                            "source_artifact_id": {"type": "string", "minLength": 1},
                            "source_start": rational,
                            "source_end": rational,
                            "mode": {"enum": ["continuous", "jump", "loop", "retrigger", "reverse", "hold"]},
                            "rate": rational,
                            "metadata": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                },
                "metadata": {"type": "object"},
                "time_map_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    rights = _floor_schema_base("earcrate_floor_rights_envelope", "EarCrate Floor rights assertion envelope v1")
    rights.update(
        {
            "required": [
                "schema_version", "kind", "source_artifact_sha256", "assertion_status",
                "license_expression", "policy_uri", "allowed_uses", "prohibited_uses",
                "attribution", "evidence_refs", "jurisdiction", "expires_at",
                "provider_may_not_decide_legality", "metadata", "rights_envelope_sha256"
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_rights_envelope"},
                "source_artifact_sha256": _FLOOR_SHA_OR_NULL,
                "assertion_status": {
                    "enum": ["unknown", "asserted", "user_verified", "provider_verified", "externally_certified"]
                },
                "license_expression": {"type": "string"},
                "policy_uri": {"type": "string"},
                "allowed_uses": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "prohibited_uses": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "attribution": {"type": "array"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "jurisdiction": {"type": "string"},
                "expires_at": {"type": "string"},
                "provider_may_not_decide_legality": {"const": True},
                "metadata": {"type": "object"},
                "rights_envelope_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    evaluation_policy = _floor_schema_base("earcrate_floor_evaluation_policy", "EarCrate Floor evaluation policy v1")
    evaluation_policy.update(
        {
            "required": [
                "schema_version", "kind", "policy_id", "hard_gates", "lexicographic_stages",
                "higher_is_better", "lower_is_better", "metadata", "policy_sha256"
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_evaluation_policy"},
                "policy_id": {"type": "string", "minLength": 1},
                "hard_gates": {"type": "array", "items": {"type": "object"}},
                "lexicographic_stages": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "higher_is_better": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "lower_is_better": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "metadata": {"type": "object"},
                "policy_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    conformance = _floor_schema_base("earcrate_floor_conformance_report", "EarCrate Floor protocol conformance report v1")
    conformance.update(
        {
            "required": [
                "schema_version", "kind", "requested_runs", "completed_runs", "runs", "failures",
                "checks", "complete", "quality_claimed", "selection_authority", "conformance_sha256"
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_conformance_report"},
                "requested_runs": {"type": "integer", "minimum": 1},
                "completed_runs": {"type": "integer", "minimum": 0},
                "runs": {"type": "array", "items": {"type": "object"}},
                "failures": {"type": "array", "items": {"type": "object"}},
                "checks": {"type": "object"},
                "complete": {"type": "boolean"},
                "quality_claimed": {"const": False},
                "selection_authority": {"const": False},
                "conformance_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    tournament = _floor_schema_base("earcrate_floor_tournament_report", "EarCrate Floor provider tournament report v1")
    tournament.update(
        {
            "required": [
                "schema_version", "kind", "policy_sha256", "request_sha256", "competitors",
                "winner", "winner_semantics", "canonical_authority",
                "selection_requires_earcrate_adjudication",
                "quality_is_distinct_from_protocol_conformance", "tournament_sha256"
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_tournament_report"},
                "policy_sha256": _FLOOR_SHA,
                "request_sha256": _FLOOR_SHA,
                "competitors": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "winner": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "winner_semantics": {"type": "string"},
                "canonical_authority": {"const": False},
                "selection_requires_earcrate_adjudication": {"const": True},
                "quality_is_distinct_from_protocol_conformance": {"const": True},
                "tournament_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    gap_register = _floor_schema_base("earcrate_floor_gap_register", "EarCrate Floor interoperability gap register v1")
    gap_register.update(
        {
            "required": ["schema_version", "kind", "gaps", "counts", "standards", "gap_register_sha256"],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_gap_register"},
                "gaps": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "counts": {"type": "object"},
                "standards": {"type": "array", "items": {"type": "object"}},
                "gap_register_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    release_candidate = _floor_schema_base("earcrate_floor_release_candidate", "EarCrate Floor release candidate v1")
    release_candidate.update(
        {
            "required": [
                "schema_version", "kind", "candidate_id", "builder", "source_artifact_sha256s",
                "artifacts", "time_map", "phrase_contract", "status_vector", "boundary", "metadata",
                "release_candidate_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_release_candidate"},
                "candidate_id": {"type": "string", "minLength": 1},
                "builder": {"type": "object"},
                "source_artifact_sha256s": {"type": "array", "items": _FLOOR_SHA, "uniqueItems": True},
                "artifacts": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "time_map": {"type": "object"},
                "phrase_contract": {"type": "object"},
                "status_vector": {
                    "type": "object",
                    "required": [
                        "custody", "build_reproducibility", "signal_sanity", "recurrence_identity",
                        "transition_integrity", "human_musical_review", "rights_eligibility", "release_state",
                    ],
                },
                "boundary": {
                    "type": "object",
                    "required": [
                        "builder_may_approve_music", "human_review_required", "legal_clearance_claimed",
                        "whole_organism_passed",
                    ],
                    "properties": {
                        "builder_may_approve_music": {"const": False},
                        "human_review_required": {"const": True},
                        "legal_clearance_claimed": {"const": False},
                        "whole_organism_passed": {"const": False},
                    },
                    "additionalProperties": True,
                },
                "metadata": {"type": "object"},
                "release_candidate_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    human_review_request = _floor_schema_base(
        "earcrate_floor_human_musical_review_request", "EarCrate Floor human musical review request v1"
    )
    human_review_request.update(
        {
            "required": [
                "schema_version", "kind", "candidate_id", "release_candidate_sha256", "questions",
                "allowed_verdicts", "status", "builder_may_answer", "review_request_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_human_musical_review_request"},
                "candidate_id": {"type": "string", "minLength": 1},
                "release_candidate_sha256": _FLOOR_SHA,
                "questions": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "allowed_verdicts": {
                    "type": "array", "items": {"enum": ["accepted", "rejected", "revise"]}, "uniqueItems": True
                },
                "status": {"const": "pending"},
                "builder_may_answer": {"const": False},
                "review_request_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    human_review = _floor_schema_base("earcrate_floor_human_musical_review", "EarCrate Floor human musical review v1")
    human_review.update(
        {
            "required": [
                "schema_version", "kind", "release_candidate_sha256", "reviewer", "verdict", "blind_review",
                "ratings", "notes", "comparison_artifact_sha256s", "metadata", "human_review_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_human_musical_review"},
                "release_candidate_sha256": _FLOOR_SHA,
                "reviewer": {"type": "object"},
                "verdict": {"enum": ["pending", "accepted", "rejected", "revise"]},
                "blind_review": {"type": "boolean"},
                "ratings": {"type": "object", "additionalProperties": {"type": "number"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "comparison_artifact_sha256s": {"type": "array", "items": _FLOOR_SHA, "uniqueItems": True},
                "metadata": {"type": "object"},
                "human_review_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    rights_review = _floor_schema_base("earcrate_floor_rights_review", "EarCrate Floor use-scoped rights review v1")
    rights_review.update(
        {
            "required": [
                "schema_version", "kind", "release_candidate_sha256", "declared_use", "status", "reviewer",
                "evidence_refs", "conditions", "legal_determination", "metadata", "rights_review_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_rights_review"},
                "release_candidate_sha256": _FLOOR_SHA,
                "declared_use": {"type": "string", "minLength": 1},
                "status": {
                    "enum": [
                        "not_evaluated", "eligible_for_declared_use", "ineligible_for_declared_use", "unknown"
                    ]
                },
                "reviewer": {"type": "object"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "conditions": {"type": "array", "items": {"type": "string"}},
                "legal_determination": {"const": False},
                "metadata": {"type": "object"},
                "rights_review_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    release_gate = _floor_schema_base("earcrate_floor_release_gate_receipt", "EarCrate Floor release gate receipt v1")
    release_gate.update(
        {
            "required": [
                "schema_version", "kind", "release_candidate_sha256", "builder_provider_id",
                "conformance_sha256", "signal_evaluation_sha256", "human_review_sha256",
                "rights_review_sha256", "checks", "status_vector", "release_eligible",
                "musical_acceptance_decided_by_builder", "legal_determination_claimed",
                "whole_organism_passed", "release_gate_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "kind": {"const": "earcrate_floor_release_gate_receipt"},
                "release_candidate_sha256": _FLOOR_SHA,
                "builder_provider_id": {"type": "string", "minLength": 1},
                "conformance_sha256": _FLOOR_SHA_OR_NULL,
                "signal_evaluation_sha256": _FLOOR_SHA_OR_NULL,
                "human_review_sha256": _FLOOR_SHA_OR_NULL,
                "rights_review_sha256": _FLOOR_SHA_OR_NULL,
                "checks": {"type": "object"},
                "status_vector": {"type": "object"},
                "release_eligible": {"type": "boolean"},
                "musical_acceptance_decided_by_builder": {"const": False},
                "legal_determination_claimed": {"const": False},
                "whole_organism_passed": {"const": False},
                "release_gate_sha256": _FLOOR_SHA,
            },
            "additionalProperties": False,
        }
    )

    return {
        "earcrate_floor_provider_manifest_v1.schema.json": manifest,
        "earcrate_floor_provider_request_v1.schema.json": request,
        "earcrate_floor_provider_result_v1.schema.json": result,
        "earcrate_floor_phrase_contract_v1.schema.json": phrase,
        "earcrate_floor_review_patch_v1.schema.json": review,
        "earcrate_floor_invocation_receipt_v1.schema.json": receipt,
        "earcrate_floor_evaluation_ledger_v1.schema.json": evaluation,
        "earcrate_floor_crate_v1.schema.json": crate,
        "earcrate_floor_time_map_v1.schema.json": time_map,
        "earcrate_floor_rights_envelope_v1.schema.json": rights,
        "earcrate_floor_evaluation_policy_v1.schema.json": evaluation_policy,
        "earcrate_floor_conformance_report_v1.schema.json": conformance,
        "earcrate_floor_tournament_report_v1.schema.json": tournament,
        "earcrate_floor_gap_register_v1.schema.json": gap_register,
        "earcrate_floor_release_candidate_v1.schema.json": release_candidate,
        "earcrate_floor_human_musical_review_request_v1.schema.json": human_review_request,
        "earcrate_floor_human_musical_review_v1.schema.json": human_review,
        "earcrate_floor_rights_review_v1.schema.json": rights_review,
        "earcrate_floor_release_gate_receipt_v1.schema.json": release_gate,
    }


def floor_write_schema_bundle(output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for name, schema in sorted(floor_schema_bundle().items()):
        path = floor_write_json_atomic(destination / name, schema)
        files.append(str(path))
    return {"ok": True, "output_dir": str(destination), "files": files}


__all__ = ["floor_schema_bundle", "floor_write_schema_bundle"]
