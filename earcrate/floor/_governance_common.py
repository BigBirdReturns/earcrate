"""Shared validators for proof-carrying release governance."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from earcrate.floor.model import FloorError, floor_sha256_json
from earcrate.floor.release import floor_seal_release_candidate

GOVERNANCE_SCHEMA_VERSION = 2
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_PUBLICATION_NAMES = {
    "publish-permit.json",
    "publication-manifest.json",
    "publication-receipt.json",
    "SHA256SUMS",
}


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FloorError(f"{field} must be a mapping")
    return deepcopy(dict(value))


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise FloorError(f"{field} must be a sequence")
    return list(value)


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise FloorError(f"{field} must be a non-empty string")
    return result


def _sha(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if not _SHA_RE.fullmatch(result):
        raise FloorError(f"{field} must be a lowercase SHA-256 digest")
    return result


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FloorError(f"{field} must be an integer") from exc
    if result < minimum:
        raise FloorError(f"{field} must be >= {minimum}")
    return result


def _sealed(payload: Mapping[str, Any], hash_field: str) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    claimed = result.pop(hash_field, None)
    digest = floor_sha256_json(result)
    if claimed is not None and str(claimed) != digest:
        raise FloorError(f"{hash_field} hash mismatch; sealed object is immutable")
    result[hash_field] = digest
    return result


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _parse_time(value: Any, field: str) -> tuple[str, datetime]:
    text = _text(value, field)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FloorError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FloorError(f"{field} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    normalized = utc.isoformat().replace("+00:00", "Z")
    return normalized, utc


def _artifact_descriptor(value: Mapping[str, Any], field: str, *, role: str | None = None) -> dict[str, Any]:
    raw = _mapping(value, field)
    descriptor = {
        "artifact_id": _text(raw.get("artifact_id"), f"{field} artifact_id"),
        "role": _text(role if role is not None else raw.get("role"), f"{field} role"),
        "sha256": _sha(raw.get("sha256"), f"{field} sha256"),
        "decoded_pcm_sha256": (
            None
            if not raw.get("decoded_pcm_sha256")
            else _sha(raw.get("decoded_pcm_sha256"), f"{field} decoded_pcm_sha256")
        ),
        "media_kind": _text(raw.get("media_kind"), f"{field} media_kind"),
        "size_bytes": _integer(raw.get("size_bytes", 0), f"{field} size_bytes", minimum=0),
    }
    return descriptor


def _candidate_artifacts(candidate: Mapping[str, Any], role_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    sealed = floor_seal_release_candidate(candidate)
    available = {
        sealed["authoritative_output"]["artifact_id"]: sealed["authoritative_output"],
        **{row["artifact_id"]: row for row in sealed["delivery_artifacts"]},
    }
    result: list[dict[str, Any]] = []
    roles: set[str] = set()
    ids: set[str] = set()
    for role, artifact_id_value in sorted(dict(role_map).items()):
        normalized_role = _text(role, "candidate artifact role")
        artifact_id = _text(artifact_id_value, f"candidate artifact id for role {normalized_role}")
        if normalized_role in roles:
            raise FloorError(f"duplicate candidate artifact role: {normalized_role}")
        if artifact_id in ids:
            raise FloorError(f"candidate artifact {artifact_id} cannot occupy multiple publication roles")
        if artifact_id not in available:
            raise FloorError(f"candidate artifact role {normalized_role} references unknown artifact {artifact_id}")
        roles.add(normalized_role)
        ids.add(artifact_id)
        result.append(_artifact_descriptor(available[artifact_id], f"candidate artifact {artifact_id}", role=normalized_role))
    if not result:
        raise FloorError("candidate_artifact_roles must select at least one candidate artifact")
    return result


def _seal_control(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(value, "control")
    artifacts = [_artifact_descriptor(row, f"control artifact {index}") for index, row in enumerate(_sequence(raw.get("artifacts"), "control artifacts"))]
    if not artifacts:
        raise FloorError("control requires at least one artifact")
    if len({row["artifact_id"] for row in artifacts}) != len(artifacts):
        raise FloorError("control artifact IDs must be unique")
    if len({row["role"] for row in artifacts}) != len(artifacts):
        raise FloorError("control artifact roles must be unique")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_review_control",
        "control_id": _text(raw.get("control_id"), "control_id"),
        "artifacts": sorted(artifacts, key=lambda row: row["role"]),
        "metadata": _mapping(raw.get("metadata") or {}, "control metadata"),
    }
    return _sealed(out, "control_sha256")


def _artifact_by_role(rows: Sequence[Mapping[str, Any]], role: str, field: str) -> dict[str, Any]:
    matches = [dict(row) for row in rows if str(row.get("role")) == role]
    if len(matches) != 1:
        raise FloorError(f"{field} requires exactly one artifact with role {role!r}")
    return matches[0]


def _review_policy(value: Mapping[str, Any], minimum_reviewers: int) -> dict[str, Any]:
    raw = _mapping(value, "review_policy")
    dimensions = [_text(row, "review dimension") for row in _sequence(raw.get("dimensions"), "review dimensions")]
    if not dimensions or len(set(dimensions)) != len(dimensions):
        raise FloorError("review dimensions must be non-empty and unique")
    out = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_review_policy",
        "policy_id": _text(raw.get("policy_id"), "review policy_id"),
        "minimum_reviewers": minimum_reviewers,
        "dimensions": dimensions,
        "allow_abstain": bool(raw.get("allow_abstain", True)),
        "comparison": "pairwise_blind",
        "metadata": _mapping(raw.get("metadata") or {}, "review policy metadata"),
    }
    return _sealed(out, "review_policy_sha256")


def _permutation(seed: str, assignment_id: str) -> dict[str, str]:
    bit = int(sha256(f"{seed}:{assignment_id}".encode("utf-8")).hexdigest(), 16) & 1
    return ({"A": "candidate", "B": "control"} if bit == 0 else {"A": "control", "B": "candidate"})


def _option_descriptor(label: str, artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "option": label,
        "artifact_sha256": artifact["sha256"],
        "decoded_pcm_sha256": artifact.get("decoded_pcm_sha256"),
        "media_kind": artifact["media_kind"],
        "size_bytes": artifact["size_bytes"],
    }
