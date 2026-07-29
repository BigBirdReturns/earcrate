from __future__ import annotations

"""Provider catalog discovery and compatibility filtering."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adapters import floor_earcrate_provider_manifests
from .model import (
    FloorError,
    floor_capability_for_manifest,
    floor_read_json,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_sha256_file,
)

FLOOR_MANIFEST_SUFFIXES = (
    ".floor-provider.json",
    ".provider.floor.json",
    ".provider.json",
)


def _floor_media_matches(pattern: str, actual: str) -> bool:
    p = str(pattern).lower()
    a = str(actual).lower()
    if p in {"*", "*/*"}:
        return True
    if p.endswith("/*"):
        return a.startswith(p[:-1])
    return p == a


def floor_manifest_compatibility(manifest: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    sealed_manifest = floor_seal_provider_manifest(manifest)
    sealed_request = floor_seal_provider_request(request)
    reasons: list[str] = []
    try:
        capability = floor_capability_for_manifest(sealed_manifest, sealed_request["capability"])
    except FloorError as exc:
        return {
            "compatible": False,
            "provider_id": sealed_manifest["provider_id"],
            "manifest_sha256": sealed_manifest["manifest_sha256"],
            "reasons": [str(exc)],
        }
    if sealed_request["evidence_branch"] not in capability["evidence_branches"]:
        reasons.append("evidence branch is unsupported")
    if sealed_request["evidence_tier"] not in capability["evidence_tiers"]:
        reasons.append("evidence tier is unsupported")
    if sealed_request["network_policy"] == "forbidden" and capability["network_policy"] == "required":
        reasons.append("provider requires network but request forbids it")
    if sealed_request["network_policy"] == "required" and capability["network_policy"] == "forbidden":
        reasons.append("request requires network but provider forbids it")
    unsupported_results = sorted(set(sealed_request["allowed_result_kinds"]) - set(capability["result_kinds"]))
    if unsupported_results:
        reasons.append("provider cannot emit requested result kinds: " + ", ".join(unsupported_results))
    for artifact in sealed_request["inputs"]:
        if not any(_floor_media_matches(pattern, artifact["media_kind"]) for pattern in capability["input_media_kinds"]):
            reasons.append(f"input {artifact['artifact_id']} media kind {artifact['media_kind']} is unsupported")
    return {
        "compatible": not reasons,
        "provider_id": sealed_manifest["provider_id"],
        "provider_version": sealed_manifest["provider_version"],
        "manifest_sha256": sealed_manifest["manifest_sha256"],
        "capability": deepcopy(capability),
        "reasons": reasons,
    }


def _floor_manifest_paths(paths: Iterable[str | Path]) -> list[Path]:
    found: set[Path] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_file():
            if path.name.endswith(FLOOR_MANIFEST_SUFFIXES):
                found.add(path)
            continue
        if not path.is_dir():
            continue
        for candidate in path.rglob("*.json"):
            if candidate.name.endswith(FLOOR_MANIFEST_SUFFIXES):
                found.add(candidate.resolve())
    return sorted(found)


def floor_discover_provider_catalog(
    paths: Iterable[str | Path],
    *,
    request: Mapping[str, Any] | None = None,
    include_earcrate_adapters: bool = True,
) -> dict[str, Any]:
    sealed_request = floor_seal_provider_request(request) if request is not None else None
    entries: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}

    def add_manifest(raw: Mapping[str, Any], *, source: str, source_sha256: str | None) -> None:
        try:
            sealed = floor_seal_provider_manifest(raw)
        except Exception as exc:
            refusals.append({"source": source, "code": "invalid_manifest", "message": str(exc)})
            return
        key = (sealed["provider_id"], sealed["provider_version"])
        prior = by_identity.get(key)
        if prior is not None and prior["manifest"]["manifest_sha256"] != sealed["manifest_sha256"]:
            refusals.append(
                {
                    "source": source,
                    "code": "conflicting_provider_identity",
                    "message": f"{key[0]} {key[1]} resolves to more than one manifest identity",
                    "prior_source": prior["source"],
                }
            )
            prior["conflicted"] = True
            return
        compatibility = None if sealed_request is None else floor_manifest_compatibility(sealed, sealed_request)
        entry = {
            "source": source,
            "source_sha256": source_sha256,
            "manifest": sealed,
            "compatibility": compatibility,
            "conflicted": False,
        }
        by_identity[key] = entry
        entries.append(entry)

    for path in _floor_manifest_paths(paths):
        try:
            raw = floor_read_json(path)
            add_manifest(raw, source=str(path), source_sha256=floor_sha256_file(path))
        except Exception as exc:
            refusals.append({"source": str(path), "code": "manifest_read_failed", "message": str(exc)})

    if include_earcrate_adapters:
        for manifest in floor_earcrate_provider_manifests():
            add_manifest(manifest, source="earcrate:in-process-registry", source_sha256=None)

    accepted = [
        entry
        for entry in entries
        if not entry.get("conflicted") and (sealed_request is None or bool((entry.get("compatibility") or {}).get("compatible")))
    ]
    incompatible = [
        entry
        for entry in entries
        if not entry.get("conflicted") and sealed_request is not None and not bool((entry.get("compatibility") or {}).get("compatible"))
    ]
    accepted.sort(key=lambda item: (item["manifest"]["provider_id"], item["manifest"]["provider_version"], item["manifest"]["manifest_sha256"]))
    incompatible.sort(key=lambda item: (item["manifest"]["provider_id"], item["manifest"]["provider_version"], item["manifest"]["manifest_sha256"]))
    refusals.sort(key=lambda item: (str(item.get("source") or ""), str(item.get("code") or "")))
    return {
        "schema_version": 1,
        "kind": "earcrate_floor_provider_catalog",
        "request_sha256": None if sealed_request is None else sealed_request["request_sha256"],
        "accepted": accepted,
        "incompatible": incompatible,
        "refusals": refusals,
        "counts": {
            "accepted": len(accepted),
            "incompatible": len(incompatible),
            "refused": len(refusals),
        },
        "selection_authority": False,
        "quality_claimed": False,
    }


__all__ = [
    "FLOOR_MANIFEST_SUFFIXES",
    "floor_manifest_compatibility",
    "floor_discover_provider_catalog",
]
