from __future__ import annotations

"""Public operational policy for the EarCrate Homelab.

Backup, restore, and dashboard mechanics live in the private core. Public export
is intentionally defined here because exported bytes require a stricter policy:
local paths and private review material must never leak merely because the source
object was stored with public visibility.
"""

from pathlib import Path
from typing import Any

from earcrate.estate._homelab_ops_core import (
    backup_homelab_store,
    render_homelab_dashboard,
    restore_homelab_backup,
    _fsync_directory,
)
from earcrate.estate.homelab_common import HOMELAB_SCHEMA_VERSION, _now_utc, homelab_seal
from earcrate.estate.homelab_redact import project_public_object
from earcrate.estate.homelab_store import HomelabStore
from earcrate.estate.model import estate_sha256_file, write_estate_json


def export_public_store(store_root: str | Path, destination: str | Path) -> dict[str, Any]:
    """Export source-free projections of public objects.

    The export never copies authoritative object bytes directly. Each public
    object becomes a separately sealed projection that retains the source
    identity but replaces absolute paths and private payload fields. The original
    object is still required for authoritative verification.
    """

    target = Path(destination).expanduser().absolute()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError(f"public export destination must be new or empty: {target}")
    target.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    with HomelabStore(store_root) as store:
        doctor = store.doctor()
        if not doctor["ok"]:
            raise ValueError("Homelab store doctor failed before public export")
        for row in store.list_objects(visibility="public"):
            source = store.load_object(str(row["identity"]))
            projection = project_public_object(source)
            source_kind = str(source["kind"])
            source_identity = str(row["identity"])
            relative = (
                f"projections/{source_kind}/{source_identity[:2]}/"
                f"{projection['projection_sha256']}.json"
            )
            output = write_estate_json(target / relative, projection)
            entries.append(
                {
                    "path": relative,
                    "source_kind": source_kind,
                    "source_identity": source_identity,
                    "projection_identity": projection["projection_sha256"],
                    "sha256": estate_sha256_file(output),
                    "bytes": int(output.stat().st_size),
                    "absolute_paths_redacted": int(projection["redaction"]["absolute_paths"]),
                    "sensitive_fields_redacted": int(projection["redaction"]["sensitive_fields"]),
                }
            )

        snapshot = store.snapshot(include_private_counts=False)
        snapshot_projection = project_public_object(snapshot)
        snapshot_path = write_estate_json(target / "store.snapshot.projection.json", snapshot_projection)
        entries.append(
            {
                "path": "store.snapshot.projection.json",
                "source_kind": snapshot["kind"],
                "source_identity": snapshot["snapshot_sha256"],
                "projection_identity": snapshot_projection["projection_sha256"],
                "sha256": estate_sha256_file(snapshot_path),
                "bytes": int(snapshot_path.stat().st_size),
                "absolute_paths_redacted": int(snapshot_projection["redaction"]["absolute_paths"]),
                "sensitive_fields_redacted": int(snapshot_projection["redaction"]["sensitive_fields"]),
            }
        )

    manifest = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_public_export_manifest",
            "created_at": _now_utc(),
            "entries": sorted(entries, key=lambda row: row["path"]),
            "boundary": {
                "authoritative_object_bytes_exported": False,
                "private_objects_exported": False,
                "sensitive_objects_exported": False,
                "absolute_paths_exported": False,
                "source_media_exported": False,
                "projections_require_source_objects_for_authoritative_verification": True,
            },
        }
    )
    manifest_path = write_estate_json(target / "manifest.json", manifest)
    checksums = [f"{row['sha256']}  {row['path']}" for row in manifest["entries"]]
    checksums.append(f"{estate_sha256_file(manifest_path)}  manifest.json")
    (target / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    _fsync_directory(target)
    return {
        "ok": True,
        "destination": str(target),
        "manifest_sha256": manifest["manifest_sha256"],
        "objects": len(entries) - 1,
        "source_media_exported": False,
        "authoritative_object_bytes_exported": False,
    }


__all__ = [
    "export_public_store",
    "backup_homelab_store",
    "restore_homelab_backup",
    "render_homelab_dashboard",
]
