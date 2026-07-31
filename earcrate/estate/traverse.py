from __future__ import annotations

"""Bounded filesystem traversal and duplicate-candidate hashing."""

from collections import defaultdict, deque
import contextlib
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from earcrate.estate.classify import _estate_classify_path, _estate_should_hash
from earcrate.estate.inspect import _estate_json_metadata, _estate_sqlite_metadata, _estate_zip_metadata
from earcrate.estate.markers import (
    _JSON_KINDS,
    _estate_detect_root_role,
    _estate_git_metadata,
    _estate_root_version_metadata,
    _estate_text_version_metadata,
)
from earcrate.estate.model import estate_item_id, estate_root_id, estate_sha256_file

def _estate_scan_root(root: Path, policy: Mapping[str, Any], hash_mode: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scan = dict(policy.get("scan") or {})
    root_id = estate_root_id(root)
    role, role_reasons = _estate_detect_root_role(root)
    root_record: dict[str, Any] = {
        "root_id": root_id,
        "path": str(root),
        "role": role,
        "reasons": role_reasons,
        "exists": root.exists(),
    }
    if role == "repository":
        root_record["repository"] = {**_estate_git_metadata(root), **_estate_root_version_metadata(root)}
    items: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        issues.append({"issue": "missing_root", "root_id": root_id, "path": str(root), "severity": "error"})
        return root_record, items, issues

    excluded = {str(name).casefold() for name in scan.get("exclude_directory_names") or []}
    max_depth = int(scan.get("max_depth") or 64)
    max_files = int(scan.get("max_files") or 2_000_000)
    json_limit = int(scan.get("json_parse_max_bytes") or 0)
    sqlite_limit = int(scan.get("sqlite_inspect_max_bytes") or 0)
    zip_limit = int(scan.get("zip_inventory_max_members") or 0)
    follow_symlinks = bool(scan.get("follow_symlinks"))

    queue: deque[tuple[Path, int, str]] = deque([(root, 0, role)])
    nested_estates: list[dict[str, Any]] = []
    file_count = 0
    while queue:
        directory, depth, inherited_role = queue.popleft()
        local_role = inherited_role
        detected_role, detected_reasons = _estate_detect_root_role(directory)
        # A package subdirectory named ``music`` is not a source library once a
        # repository boundary has already been established.  Nested boundaries may
        # strengthen an unclassified/mixed root, but they never demote repository or
        # workspace context based on a directory name alone.
        if inherited_role in {"repository", "workspace", "project_store"} and detected_role == "source_library":
            detected_role = "unclassified"
        if detected_role != "unclassified" and (directory == root or detected_role != inherited_role):
            local_role = detected_role
            if directory != root:
                nested_estates.append({"path": str(directory), "relative_path": directory.relative_to(root).as_posix(), "role": detected_role, "reasons": detected_reasons, "repository": ({**_estate_git_metadata(directory), **_estate_root_version_metadata(directory)} if detected_role == "repository" else None)})
        if depth > max_depth:
            issues.append({"issue": "depth_limit", "root_id": root_id, "path": str(directory), "severity": "warning"})
            continue
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except Exception as exc:
            issues.append({"issue": "directory_unreadable", "root_id": root_id, "path": str(directory), "severity": "warning", "error": str(exc)[:240]})
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                relative = path.relative_to(root).as_posix()
                stat_result = entry.stat(follow_symlinks=False)
            except Exception as exc:
                issues.append({"issue": "stat_failed", "root_id": root_id, "path": str(path), "severity": "warning", "error": str(exc)[:240]})
                continue
            is_link = stat.S_ISLNK(stat_result.st_mode)
            if entry.is_dir(follow_symlinks=False):
                if entry.name.casefold() in excluded:
                    continue
                queue.append((path, depth + 1, local_role))
                continue
            if is_link and follow_symlinks and entry.is_dir(follow_symlinks=True):
                issues.append({"issue": "symlink_directory_not_followed", "root_id": root_id, "path": relative, "severity": "warning"})
            file_count += 1
            if file_count > max_files:
                issues.append({"issue": "file_limit", "root_id": root_id, "path": str(root), "severity": "error", "max_files": max_files})
                return root_record, items, issues

            file_type = "symlink" if is_link else "file"
            classification, disposition, reasons = _estate_classify_path(path, relative, local_role)
            item: dict[str, Any] = {
                "item_id": estate_item_id(root_id, relative, stat_result.st_size, stat_result.st_mtime_ns, file_type),
                "root_id": root_id,
                "relative_path": relative,
                "absolute_path": str(path),
                "file_type": file_type,
                "bytes": int(stat_result.st_size),
                "mtime_ns": int(stat_result.st_mtime_ns),
                "extension": path.suffix.lower(),
                "classification": classification,
                "disposition": disposition,
                "reasons": reasons,
                "hash_status": "not_requested",
                "raw_sha256": None,
                "metadata": {},
            }
            if is_link:
                with contextlib.suppress(Exception):
                    item["metadata"]["symlink_target"] = os.readlink(path)
                item["hash_status"] = "symlink_refused"
                items.append(item)
                continue

            if path.suffix.lower() == ".json" or path.name.lower().endswith(".json"):
                metadata = _estate_json_metadata(path, json_limit)
                item["metadata"].update(metadata)
                kind = str(metadata.get("kind") or "")
                if kind in _JSON_KINDS:
                    item["classification"], item["disposition"] = _JSON_KINDS[kind]
                    item["reasons"].append(f"JSON kind {kind}")
                if path.name.lower() == "project.json" and metadata.get("project_id"):
                    item["classification"] = "project_index"
                    item["disposition"] = "authority"
                if path.name.lower() in {"status.json", "report.json"} and metadata.get("run_id"):
                    item["classification"] = "run_receipt"
                    item["disposition"] = "durable_evidence"
            if path.suffix.lower() == ".zip":
                item["metadata"].update(_estate_zip_metadata(path, zip_limit))
            if item["classification"] == "database":
                item["metadata"].update(_estate_sqlite_metadata(path, sqlite_limit))
            if item["classification"] in {"repository", "distribution"} or path.suffix.lower() == ".py":
                item["metadata"].update(_estate_text_version_metadata(path))

            if _estate_should_hash(item, policy, hash_mode):
                try:
                    item["raw_sha256"] = estate_sha256_file(path)
                    item["hash_status"] = "strong"
                except Exception as exc:
                    item["hash_status"] = "failed"
                    item["metadata"]["hash_error"] = f"{type(exc).__name__}: {exc}"[:240]
            elif int(item["bytes"]) > int(scan.get("max_hash_bytes_per_file") or 0):
                item["hash_status"] = "skipped_size_limit"
            items.append(item)

    if nested_estates:
        root_record["nested_estates"] = nested_estates
    root_record["files"] = file_count
    root_record["bytes"] = sum(int(item["bytes"]) for item in items)
    return root_record, items, issues


def _estate_hash_duplicate_candidates(items: list[dict[str, Any]], policy: Mapping[str, Any]) -> None:
    scan = dict(policy.get("scan") or {})
    max_bytes = int(scan.get("max_hash_bytes_per_file") or 0)
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("file_type") == "file" and int(item.get("bytes") or 0) <= max_bytes:
            groups[int(item.get("bytes") or 0)].append(item)
    for size, group in groups.items():
        if size <= 0 or len(group) < 2:
            continue
        for item in group:
            if item.get("raw_sha256"):
                continue
            try:
                item["raw_sha256"] = estate_sha256_file(str(item["absolute_path"]))
                item["hash_status"] = "strong_duplicate_candidate"
            except Exception as exc:
                item["hash_status"] = "failed"
                item.setdefault("metadata", {})["hash_error"] = f"{type(exc).__name__}: {exc}"[:240]


__all__ = ["_estate_scan_root", "_estate_hash_duplicate_candidates"]
