from __future__ import annotations

"""Portable filesystem facade for the durable Homelab store.

The core module owns the SQLite schema, event chain, scheduler, leases, and
recovery logic. This facade owns the one host-specific seam: stable path
containment while multiple workers concurrently materialize object directories.
"""

from contextlib import suppress
from copy import deepcopy
import os
from pathlib import Path
import secrets
from typing import Any, Mapping

from earcrate.estate._homelab_store_core import (
    STORE_SCHEMA_VERSION,
    HomelabStore as _CoreHomelabStore,
    _canonical_bytes,
    _fsync_directory,
    _now_utc,
    _object_identity,
    _refuse_symlink_components,
    _sha256_bytes,
    homelab_validate_seal,
)


def _portable_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is inside ``root`` under host path semantics.

    ``Path.resolve`` can produce different canonical spellings on Windows while
    another thread creates intermediate directories. Comparing ``Path.parents``
    can therefore reject a safe internally generated target. ``commonpath`` over
    normalized absolute spellings is stable before and after creation and still
    refuses drive changes and traversal.
    """

    normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    try:
        return os.path.commonpath((normalized_root, normalized_path)) == normalized_root
    except ValueError:
        return False


class HomelabStore(_CoreHomelabStore):
    """Cross-platform durable store with safe concurrent object materialization."""

    def ingest_object(self, value: Mapping[str, Any], *, visibility: str = "public") -> dict[str, Any]:
        payload = deepcopy(dict(value))
        homelab_validate_seal(payload)
        if visibility not in {"public", "private", "sensitive"}:
            raise ValueError(f"invalid Homelab object visibility: {visibility}")
        identity = _object_identity(payload)
        kind = str(payload["kind"])
        body = _canonical_bytes(payload)
        raw_sha = _sha256_bytes(body)
        relative = self._object_relative_path(kind, identity, visibility)
        target = self.root / relative
        if not _portable_within(target, self.root):
            raise ValueError("Homelab object path escaped the store")
        target.parent.mkdir(parents=True, exist_ok=True)
        _refuse_symlink_components(target.parent)
        created_file = False
        temporary: Path | None = None
        try:
            with self._transaction() as connection:
                existing = connection.execute("SELECT * FROM objects WHERE identity=?", (identity,)).fetchone()
                if existing is not None:
                    existing_path = self.root / str(existing["relative_path"])
                    if not _portable_within(existing_path, self.root):
                        raise ValueError(f"indexed Homelab object escaped the store: {identity}")
                    if existing_path.is_symlink() or not existing_path.is_file():
                        raise ValueError(f"indexed Homelab object file is missing or unsafe: {identity}")
                    current = existing_path.read_bytes()
                    if _sha256_bytes(current) != str(existing["raw_sha256"]) or current != body:
                        raise ValueError(f"Homelab object identity collision: {identity}")
                    if str(existing["kind"]) != kind or str(existing["visibility"]) != visibility:
                        raise ValueError(f"Homelab object visibility/kind collision: {identity}")
                    return {
                        "ok": True,
                        "identity": identity,
                        "kind": kind,
                        "visibility": visibility,
                        "relative_path": str(existing["relative_path"]),
                        "created": False,
                    }

                if target.exists():
                    if target.is_symlink() or not target.is_file() or target.read_bytes() != body:
                        raise ValueError(f"unindexed Homelab object collision: {target}")
                else:
                    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
                    with temporary.open("xb") as handle:
                        handle.write(body)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if _sha256_bytes(temporary.read_bytes()) != raw_sha:
                        raise ValueError("Homelab object changed during materialization")
                    os.replace(temporary, target)
                    created_file = True
                    _fsync_directory(target.parent)

                connection.execute(
                    "INSERT INTO objects(identity,kind,visibility,relative_path,raw_sha256,bytes,created_at) VALUES(?,?,?,?,?,?,?)",
                    (identity, kind, visibility, relative, raw_sha, len(body), _now_utc()),
                )
                self._append_event(
                    connection,
                    "object_ingested",
                    object_sha256=identity,
                    payload={"kind": kind, "visibility": visibility, "relative_path": relative, "raw_sha256": raw_sha},
                )
            return {
                "ok": True,
                "identity": identity,
                "kind": kind,
                "visibility": visibility,
                "relative_path": relative,
                "created": True,
            }
        except Exception:
            if temporary is not None:
                with suppress(FileNotFoundError):
                    temporary.unlink()
            if created_file:
                row = self._connection.execute("SELECT 1 FROM objects WHERE identity=?", (identity,)).fetchone()
                if row is None:
                    with suppress(FileNotFoundError):
                        target.unlink()
                    with suppress(Exception):
                        _fsync_directory(target.parent)
            raise


__all__ = ["STORE_SCHEMA_VERSION", "HomelabStore"]
