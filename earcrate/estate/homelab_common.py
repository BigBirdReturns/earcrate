from __future__ import annotations

"""Shared identities and lifecycle constants for the EarCrate Homelab."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

HOMELAB_SCHEMA_VERSION = 1
HOMELAB_HASH_FIELDS = {
    "earcrate_homelab_catalog": "catalog_sha256",
    "earcrate_homelab_node_receipt": "node_sha256",
    "earcrate_homelab_audit": "audit_sha256",
    "earcrate_homelab_campaign": "campaign_sha256",
    "earcrate_homelab_stage_receipt": "receipt_sha256",
    "earcrate_homelab_audition_ledger": "ledger_sha256",
    "earcrate_homelab_adoption_decision": "decision_sha256",
    "earcrate_homelab_review_assignment": "assignment_sha256",
    "earcrate_homelab_private_assignment_authority": "authority_sha256",
    "earcrate_homelab_review_submission": "submission_sha256",
    "earcrate_homelab_store_snapshot": "snapshot_sha256",
    "earcrate_homelab_backup_manifest": "manifest_sha256",
    "earcrate_homelab_restore_receipt": "receipt_sha256",
    "earcrate_homelab_public_export_manifest": "manifest_sha256",
}
HOMELAB_STAGE_STATUSES = {"passed", "failed", "refused"}
HOMELAB_AUDITION_VERDICTS = {"accept", "reject", "revise", "abstain"}
HOMELAB_DECISIONS = {"accepted", "rejected", "deferred", "reference_only"}
AUDIO_STAGES = ("asset_audit", "load", "fixture_run", "benchmark", "blind_audition", "adoption_decision")
OBSERVATION_STAGES = ("asset_audit", "load", "fixture_run", "benchmark", "downstream_audition", "adoption_decision")
SERVICE_STAGES = ("credential_audit", "live_request", "custody_review", "downstream_audition", "adoption_decision")
HOST_STAGES = ("install_review", "workflow_audition", "interoperability_trial", "adoption_decision")
RESEARCH_STAGES = ("source_review", "implementation_audit", "fixture_trial", "workflow_audition", "organ_harvest_decision")
STANDARD_STAGES = ("mapping", "roundtrip", "external_validation", "disposition_decision")
CORE_STAGES = ("local_identity_audit", "real_fixture", "regression_audition", "retain_or_replace_decision")
LIBRARY_STAGES = ("asset_audit", "load", "real_fixture", "benchmark", "disposition_decision")


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha_json(value: Any) -> str:
    body = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def homelab_seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HOMELAB_HASH_FIELDS.get(kind)
    if not field:
        raise ValueError(f"unknown homelab kind: {kind!r}")
    value.pop(field, None)
    value[field] = _sha_json(value)
    return value


def homelab_validate_seal(payload: Mapping[str, Any]) -> None:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HOMELAB_HASH_FIELDS.get(kind)
    if not field:
        raise ValueError(f"unknown homelab kind: {kind!r}")
    if int(value.get("schema_version") or 0) != HOMELAB_SCHEMA_VERSION:
        raise ValueError("unsupported homelab schema version")
    claimed = str(value.pop(field, ""))
    if not _is_sha256(claimed):
        raise ValueError(f"invalid or missing {field}")
    actual = _sha_json(value)
    if actual != claimed:
        raise ValueError(f"{field} mismatch: expected {claimed}, computed {actual}")
