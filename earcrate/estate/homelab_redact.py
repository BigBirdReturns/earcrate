from __future__ import annotations

"""Source-free public projections of sealed Homelab objects.

A projection never pretends that redacted bytes are the original authority. It
retains the source object identity, carries a redacted payload for inspection,
and receives a new content identity of its own.
"""

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from earcrate.estate.homelab_common import HOMELAB_SCHEMA_VERSION, _object_identity if False else _sha_json, homelab_seal, homelab_validate_seal

_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|file://)")
_SENSITIVE_KEYS = {
    "nonce",
    "option_map",
    "source_artifacts",
    "review_token",
    "lease_token",
}


def _source_identity(value: Mapping[str, Any]) -> str:
    for field in (
        "catalog_sha256",
        "node_sha256",
        "audit_sha256",
        "campaign_sha256",
        "receipt_sha256",
        "ledger_sha256",
        "decision_sha256",
        "assignment_sha256",
        "authority_sha256",
        "submission_sha256",
        "snapshot_sha256",
        "manifest_sha256",
    ):
        digest = str(value.get(field) or "")
        if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest.lower()):
            return digest.lower()
    raise ValueError(f"Homelab object has no supported identity field: {value.get('kind')!r}")


def _redacted_string(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"redacted:sha256:{digest}"


def _project(value: Any, *, key: str | None = None, counters: dict[str, int]) -> Any:
    normalized_key = str(key or "").casefold()
    if normalized_key in _SENSITIVE_KEYS:
        counters["sensitive_fields"] += 1
        return "redacted"
    if isinstance(value, Mapping):
        return {
            str(child_key): _project(child_value, key=str(child_key), counters=counters)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_project(child, key=key, counters=counters) for child in value]
    if isinstance(value, tuple):
        return [_project(child, key=key, counters=counters) for child in value]
    if isinstance(value, str) and _ABSOLUTE_PATH.match(value.strip()):
        counters["absolute_paths"] += 1
        return _redacted_string(value)
    return value


def _absolute_strings(value: Any, *, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            found.extend(_absolute_strings(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_absolute_strings(child, path=f"{path}[{index}]"))
    elif isinstance(value, str) and _ABSOLUTE_PATH.match(value.strip()):
        found.append(path)
    return found


def project_public_object(value: Mapping[str, Any]) -> dict[str, Any]:
    source = deepcopy(dict(value))
    homelab_validate_seal(source)
    source_identity = _source_identity(source)
    counters = {"absolute_paths": 0, "sensitive_fields": 0}
    payload = _project(source, counters=counters)
    remaining = _absolute_strings(payload)
    if remaining:
        raise ValueError("public projection still contains absolute paths: " + ", ".join(remaining[:20]))
    projection = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_public_projection",
            "source_kind": source["kind"],
            "source_identity": source_identity,
            "payload": payload,
            "redaction": {
                "absolute_paths": counters["absolute_paths"],
                "sensitive_fields": counters["sensitive_fields"],
                "payload_is_original_authority": False,
                "source_object_required_for_authoritative_verification": True,
            },
        }
    )
    return projection


__all__ = ["project_public_object"]
