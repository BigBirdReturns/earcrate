from __future__ import annotations

"""Conflict reconciliation, inventory sealing, and shareable redaction."""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from earcrate.estate.markers import _estate_now_utc
from earcrate.estate.model import (
    ESTATE_SCHEMA_VERSION,
    default_estate_policy,
    estate_seal,
    estate_sha256_file,
    estate_sha256_json,
    load_estate_json,
    validate_estate_policy,
)
from earcrate.estate.traverse import _estate_hash_duplicate_candidates, _estate_scan_root

def _estate_postprocess(roots: list[dict[str, Any]], items: list[dict[str, Any]], issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    duplicates: list[dict[str, Any]] = []
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("raw_sha256"):
            by_hash[str(item["raw_sha256"])].append(item)
    for digest, group in sorted(by_hash.items()):
        if len(group) > 1:
            duplicates.append({
                "sha256": digest,
                "bytes": int(group[0].get("bytes") or 0),
                "items": [str(item["item_id"]) for item in group],
                "paths": [str(item["absolute_path"]) for item in group],
            })

    resolved_roots = [(str(root["root_id"]), Path(str(root["path"])).resolve()) for root in roots if root.get("exists")]
    for index, (left_id, left) in enumerate(resolved_roots):
        for right_id, right in resolved_roots[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                issues.append({
                    "issue": "overlapping_roots",
                    "severity": "warning",
                    "root_ids": [left_id, right_id],
                    "paths": [str(left), str(right)],
                })

    pointers: list[tuple[dict[str, Any], str]] = []
    for item in items:
        if item.get("classification") == "workspace_pointer":
            target = str((item.get("metadata") or {}).get("pointer_target") or "").strip()
            pointers.append((item, target))
            if not target:
                issues.append({"issue": "pointer_missing_target", "severity": "error", "item_id": item["item_id"]})
            elif not Path(target).expanduser().exists():
                issues.append({"issue": "stale_workspace_pointer", "severity": "warning", "item_id": item["item_id"], "target": target})
    targets = sorted({os.path.normcase(str(Path(target).expanduser())) for _item, target in pointers if target})
    if len(targets) > 1:
        issues.append({"issue": "conflicting_workspace_pointers", "severity": "error", "targets": targets, "items": [item["item_id"] for item, _target in pointers]})

    project_heads: dict[str, set[str]] = defaultdict(set)
    project_items: dict[str, list[str]] = defaultdict(list)
    for item in items:
        meta = item.get("metadata") or {}
        project_id = str(meta.get("project_id") or "")
        active = str(meta.get("active_revision_sha") or "")
        if project_id:
            project_items[project_id].append(str(item["item_id"]))
            if active:
                project_heads[project_id].add(active)
    for project_id, heads in sorted(project_heads.items()):
        if len(heads) > 1:
            issues.append({"issue": "conflicting_project_heads", "severity": "error", "project_id": project_id, "heads": sorted(heads), "items": project_items[project_id]})

    rel_index = {(str(item["root_id"]), str(item["relative_path"])): item for item in items}
    for item in items:
        rel = str(item["relative_path"])
        if item.get("classification") == "artifact_metadata" and rel.endswith(".meta.json"):
            blob_rel = rel[: -len(".meta.json")] + ".bin"
            if (str(item["root_id"]), blob_rel) not in rel_index:
                item["classification"] = "orphan_artifact"
                item["disposition"] = "quarantine_candidate"
                issues.append({"issue": "orphan_artifact_metadata", "severity": "warning", "item_id": item["item_id"], "missing": blob_rel})
        if item.get("classification") == "artifact_blob" and rel.endswith(".bin"):
            meta_rel = rel[: -len(".bin")] + ".meta.json"
            if (str(item["root_id"]), meta_rel) not in rel_index:
                item["classification"] = "orphan_artifact"
                item["disposition"] = "quarantine_candidate"
                issues.append({"issue": "orphan_artifact_blob", "severity": "warning", "item_id": item["item_id"], "missing": meta_rel})

    role_by_root = {str(root["root_id"]): str(root.get("role") or "") for root in roots}
    for item in items:
        if role_by_root.get(str(item["root_id"])) == "repository" and item.get("classification") in {"source_audio", "source_score", "audition_audio", "render_audio", "stem_audio"}:
            issues.append({"issue": "media_inside_repository", "severity": "warning", "item_id": item["item_id"], "path": item["relative_path"]})

    return duplicates, issues


def scan_estate(
    roots: Iterable[str | Path],
    *,
    policy: Mapping[str, Any] | None = None,
    hash_mode: str | None = None,
    canon_ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    active_policy = validate_estate_policy(policy or default_estate_policy())
    selected_hash_mode = str(hash_mode or (active_policy.get("scan") or {}).get("hash_mode") or "evidence")
    if selected_hash_mode not in {"none", "evidence", "duplicates", "all"}:
        raise ValueError(f"invalid hash mode: {selected_hash_mode}")

    normalized_roots: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        resolved = Path(raw).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            normalized_roots.append(resolved)
    if not normalized_roots:
        raise ValueError("at least one explicit estate root is required")

    root_records: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for root in normalized_roots:
        root_record, root_items, root_issues = _estate_scan_root(root, active_policy, selected_hash_mode)
        root_records.append(root_record)
        items.extend(root_items)
        issues.extend(root_issues)

    if selected_hash_mode == "duplicates":
        _estate_hash_duplicate_candidates(items, active_policy)

    duplicates, issues = _estate_postprocess(root_records, items, issues)
    items.sort(key=lambda item: (str(item["root_id"]), str(item["relative_path"]).casefold()))
    issues.sort(key=lambda issue: (str(issue.get("severity") or ""), str(issue.get("issue") or ""), estate_sha256_json(issue)))

    canon: dict[str, Any] | None = None
    if canon_ledger_path:
        try:
            source = Path(canon_ledger_path).expanduser().resolve()
            value = load_estate_json(source)
            canon = {
                "path": str(source),
                "raw_sha256": estate_sha256_file(source),
                "kind": value.get("kind"),
                "ledger_sha256": value.get("effective_ledger_sha256") or value.get("ledger_sha256"),
                "audited_main_sha": (value.get("audit") or {}).get("audited_main_sha"),
                "open_obligations": [str(row.get("obligation_id")) for row in value.get("open_obligations") or []],
            }
        except Exception as exc:
            issues.append({"issue": "canon_ledger_unreadable", "severity": "warning", "path": str(canon_ledger_path), "error": f"{type(exc).__name__}: {exc}"[:240]})

    counts = Counter(str(item["classification"]) for item in items)
    dispositions = Counter(str(item["disposition"]) for item in items)
    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_estate_inventory",
        "created_at": _estate_now_utc(),
        "policy_sha256": active_policy["policy_sha256"],
        "hash_mode": selected_hash_mode,
        "roots": root_records,
        "items": items,
        "duplicates": duplicates,
        "issues": issues,
        "canon": canon,
        "summary": {
            "roots": len(root_records),
            "files": len(items),
            "bytes": sum(int(item.get("bytes") or 0) for item in items),
            "strong_hashes": sum(1 for item in items if item.get("raw_sha256")),
            "duplicates": len(duplicates),
            "issues": len(issues),
            "error_issues": sum(1 for issue in issues if issue.get("severity") == "error"),
            "classifications": dict(sorted(counts.items())),
            "dispositions": dict(sorted(dispositions.items())),
        },
    }
    return estate_seal(payload)


def redact_estate_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shareable path-redacted inventory while retaining relative structure."""
    value = deepcopy(dict(inventory))
    for root in value.get("roots") or []:
        root["path_sha256"] = hashlib.sha256(os.path.normcase(str(root.get("path") or "")).encode("utf-8")).hexdigest()
        root.pop("path", None)
    for item in value.get("items") or []:
        item.pop("absolute_path", None)
        metadata = dict(item.get("metadata") or {})
        if metadata.get("pointer_target"):
            metadata["pointer_target_sha256"] = hashlib.sha256(os.path.normcase(str(metadata["pointer_target"])).encode("utf-8")).hexdigest()
            metadata.pop("pointer_target", None)
        if metadata.get("declared_roots"):
            metadata["declared_root_names"] = sorted(metadata["declared_roots"])
            metadata.pop("declared_roots", None)
        item["metadata"] = metadata
    for duplicate in value.get("duplicates") or []:
        duplicate.pop("paths", None)
    for issue in value.get("issues") or []:
        issue.pop("path", None)
        issue.pop("paths", None)
        issue.pop("target", None)
        issue.pop("targets", None)
    if value.get("canon"):
        value["canon"].pop("path", None)
    value.pop("inventory_sha256", None)
    value["kind"] = "earcrate_estate_inventory"
    return estate_seal(value)


__all__ = ["scan_estate", "redact_estate_inventory"]
