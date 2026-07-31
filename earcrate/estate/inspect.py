from __future__ import annotations

"""Bounded JSON, ZIP, and SQLite metadata inspection."""

from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import urllib.parse
import zipfile
from typing import Any, Mapping

from earcrate.estate.model import estate_collect_sha256_values

def _estate_json_metadata(path: Path, max_bytes: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        if path.stat().st_size > max_bytes:
            return {"json_status": "skipped_size_limit"}
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"json_status": "invalid", "json_error": f"{type(exc).__name__}: {exc}"[:240]}
    out["json_status"] = "parsed"
    out["json_type"] = type(value).__name__
    if isinstance(value, Mapping):
        for key in (
            "kind",
            "schema",
            "schema_version",
            "project_id",
            "revision_sha",
            "active_revision_sha",
            "run_id",
            "specimen_id",
            "candidate_sha256",
            "receipt_sha256",
            "report_sha256",
            "proof_sha256",
            "status",
            "engine_version",
            "version",
        ):
            if key in value and isinstance(value[key], (str, int, float, bool, type(None))):
                out[key] = value[key]
        if path.name.lower() in {"earcrate_workspace.json", "config_pointer.json"}:
            out["pointer_target"] = str(value.get("config_json") or value.get("config") or value.get("workspace") or "")
        roots: dict[str, str] = {}
        for key in ("master_root", "working_root", "agent_root", "stems_root", "playlists_root", "workspace_folder", "workspace_root", "music_folder", "cache_root"):
            if value.get(key):
                roots[key] = str(value[key])
        if roots:
            out["declared_roots"] = roots
        identities = estate_collect_sha256_values(value)
        if identities:
            out["declared_sha256"] = identities[:1000]
            out["declared_sha256_truncated"] = len(identities) > 1000
        kind = str(value.get("kind") or "")
        if kind:
            out["kind"] = kind
    return out


def _estate_zip_metadata(path: Path, max_members: int) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            unsafe: list[str] = []
            top: Counter[str] = Counter()
            for info in infos[:max_members]:
                text = info.filename.replace("\\", "/")
                pure = Path(text)
                if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", text):
                    unsafe.append(text)
                first = text.split("/", 1)[0]
                if first:
                    top[first] += 1
            return {
                "zip_status": "parsed",
                "zip_members": len(infos),
                "zip_members_inspected": min(len(infos), max_members),
                "zip_uncompressed_bytes": sum(int(info.file_size) for info in infos),
                "zip_unsafe_members": unsafe[:100],
                "zip_top_level": [name for name, _count in top.most_common(20)],
            }
    except Exception as exc:
        return {"zip_status": "invalid", "zip_error": f"{type(exc).__name__}: {exc}"[:240]}


def _estate_sqlite_metadata(path: Path, max_bytes: int) -> dict[str, Any]:
    try:
        if path.stat().st_size > max_bytes:
            return {"sqlite_status": "skipped_size_limit"}
        quoted = urllib.parse.quote(str(path.resolve()).replace("\\", "/"), safe="/:_")
        connection = sqlite3.connect(f"file:{quoted}?mode=ro&immutable=1", uri=True, timeout=1.0)
        try:
            rows = connection.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
            ).fetchall()
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        finally:
            connection.close()
        return {
            "sqlite_status": "parsed",
            "sqlite_user_version": user_version,
            "sqlite_page_count": page_count,
            "sqlite_page_size": page_size,
            "sqlite_objects": [{"name": str(name), "type": str(kind)} for name, kind in rows[:500]],
            "sqlite_objects_truncated": len(rows) > 500,
        }
    except Exception as exc:
        return {"sqlite_status": "unreadable", "sqlite_error": f"{type(exc).__name__}: {exc}"[:240]}


__all__ = ["_estate_json_metadata", "_estate_zip_metadata", "_estate_sqlite_metadata"]
