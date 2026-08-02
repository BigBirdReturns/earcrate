from __future__ import annotations

"""Operational backup, restore, public export, and dashboard helpers."""

import contextlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import zipfile
from typing import Any, Mapping

from earcrate.estate.homelab_common import HOMELAB_SCHEMA_VERSION, _now_utc, homelab_seal, homelab_validate_seal
from earcrate.estate.homelab_store import HomelabStore, STORE_SCHEMA_VERSION
from earcrate.estate.model import estate_sha256_file, write_estate_json


def _refuse_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked Homelab operation path refused: {current}")


def _safe_archive_name(value: str) -> str:
    text = str(value).replace("\\", "/")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe Homelab archive path: {value!r}")
    if pure.parts and len(pure.parts[0]) == 2 and pure.parts[0][1] == ":":
        raise ValueError(f"drive-prefixed Homelab archive path: {value!r}")
    return pure.as_posix()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_verified(source: Path, destination: Path) -> dict[str, Any]:
    _refuse_symlink_components(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Homelab source is not a regular file: {source}")
    before = source.stat()
    expected_sha = estate_sha256_file(source)
    expected_bytes = int(before.st_size)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _refuse_symlink_components(destination.parent)
    if destination.exists():
        raise ValueError(f"Homelab destination already exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=4 * 1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        if int(temporary.stat().st_size) != expected_bytes or estate_sha256_file(temporary) != expected_sha:
            raise ValueError("Homelab copy verification failed")
        after = source.stat()
        if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(before.st_mtime_ns):
            raise ValueError(f"Homelab source changed during copy: {source}")
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return {"sha256": expected_sha, "bytes": expected_bytes}


def _zip_write_file(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    name = _safe_archive_name(arcname)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100644 & 0xFFFF) << 16
    archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def export_public_store(store_root: str | Path, destination: str | Path) -> dict[str, Any]:
    target = Path(destination).expanduser().absolute()
    _refuse_symlink_components(target)
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"public export destination must be new or empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    with HomelabStore(store_root) as store:
        doctor = store.doctor()
        if not doctor["ok"]:
            raise ValueError("Homelab store doctor failed before public export")
        entries: list[dict[str, Any]] = []
        for row in store.list_objects(visibility="public"):
            source = store.root / str(row["relative_path"])
            relative = f"objects/{row['kind']}/{row['identity'][:2]}/{row['identity']}.json"
            destination_path = target / relative
            copied = _copy_verified(source, destination_path)
            entries.append({
                "path": relative,
                "identity": row["identity"],
                "kind": row["kind"],
                "sha256": copied["sha256"],
                "bytes": copied["bytes"],
            })
        snapshot = store.snapshot(include_private_counts=False)
        snapshot_path = write_estate_json(target / "store.snapshot.json", snapshot)
        entries.append({
            "path": "store.snapshot.json",
            "identity": snapshot["snapshot_sha256"],
            "kind": snapshot["kind"],
            "sha256": estate_sha256_file(snapshot_path),
            "bytes": int(snapshot_path.stat().st_size),
        })
    manifest = homelab_seal({
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_public_export_manifest",
        "created_at": _now_utc(),
        "entries": sorted(entries, key=lambda row: row["path"]),
        "boundary": {
            "private_objects_exported": False,
            "sensitive_objects_exported": False,
            "absolute_paths_exported": False,
            "source_media_exported": False,
        },
    })
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
    }


def backup_homelab_store(
    store_root: str | Path,
    output_zip: str | Path,
    *,
    acknowledge_private_state: bool = False,
) -> dict[str, Any]:
    output = Path(output_zip).expanduser().absolute()
    _refuse_symlink_components(output)
    if output.exists():
        raise ValueError(f"Homelab backup output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.staging-{os.getpid()}-{secrets.token_hex(8)}"
    temporary_zip = output.parent / f".{output.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        staging.mkdir(parents=True, exist_ok=False)
        with HomelabStore(store_root) as store:
            doctor = store.doctor()
            if not doctor["ok"]:
                raise ValueError("Homelab store doctor failed before backup")
            rows = store.list_objects()
            private_count = sum(1 for row in rows if row["visibility"] in {"private", "sensitive"})
            if private_count and not acknowledge_private_state:
                raise ValueError("backup contains private/sensitive Homelab state; explicit acknowledgement is required")
            database_copy = staging / "db" / "homelab.sqlite3"
            database_copy.parent.mkdir(parents=True, exist_ok=True)
            destination_connection = sqlite3.connect(database_copy)
            try:
                store._connection.backup(destination_connection)
                destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                destination_connection.commit()
            finally:
                destination_connection.close()
            entries: list[dict[str, Any]] = []
            entries.append({
                "path": "db/homelab.sqlite3",
                "sha256": estate_sha256_file(database_copy),
                "bytes": int(database_copy.stat().st_size),
                "visibility": "private",
            })
            for row in rows:
                source = store.root / str(row["relative_path"])
                destination = staging / str(row["relative_path"])
                copied = _copy_verified(source, destination)
                entries.append({
                    "path": str(row["relative_path"]),
                    "sha256": copied["sha256"],
                    "bytes": copied["bytes"],
                    "visibility": row["visibility"],
                    "identity": row["identity"],
                    "kind": row["kind"],
                })
            snapshot = store.snapshot(include_private_counts=True)
        manifest = homelab_seal({
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_backup_manifest",
            "created_at": _now_utc(),
            "store_schema_version": STORE_SCHEMA_VERSION,
            "entries": sorted(entries, key=lambda row: row["path"]),
            "store_snapshot": snapshot,
            "boundary": {
                "contains_private_state": bool(private_count),
                "internally_encrypted": False,
                "operator_must_store_on_encrypted_media": True,
            },
        })
        write_estate_json(staging / "backup.manifest.json", manifest)
        archive_entries = sorted(path for path in staging.rglob("*") if path.is_file())
        with temporary_zip.open("xb") as raw:
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
                for path in archive_entries:
                    _zip_write_file(archive, path, path.relative_to(staging).as_posix())
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary_zip, output)
        _fsync_directory(output.parent)
        return {
            "ok": True,
            "output": str(output),
            "raw_sha256": estate_sha256_file(output),
            "bytes": int(output.stat().st_size),
            "manifest_sha256": manifest["manifest_sha256"],
            "entries": len(entries),
            "contains_private_state": bool(private_count),
            "internally_encrypted": False,
        }
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_zip.unlink()
        shutil.rmtree(staging, ignore_errors=True)


def restore_homelab_backup(
    backup_zip: str | Path,
    destination: str | Path,
    *,
    approve_sha256: str,
    max_uncompressed_bytes: int = 512 * 1024 * 1024 * 1024,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(backup_zip).expanduser()
    _refuse_symlink_components(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Homelab backup must be a regular non-symlink file")
    actual_backup_sha = estate_sha256_file(source)
    if approve_sha256 != actual_backup_sha:
        raise ValueError(f"backup approval mismatch; pass exact SHA-256 {actual_backup_sha}")
    target = Path(destination).expanduser().absolute()
    _refuse_symlink_components(target)
    if target.exists():
        raise ValueError("Homelab restore destination must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.restore-{os.getpid()}-{secrets.token_hex(8)}"
    if staging.exists():
        raise ValueError(f"stale Homelab restore staging directory exists: {staging}")
    try:
        staging.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            names = [_safe_archive_name(info.filename) for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Homelab backup contains duplicate archive members")
            if sum(int(info.file_size) for info in infos) > int(max_uncompressed_bytes):
                raise ValueError("Homelab backup exceeds the restore size limit")
            for info, name in zip(infos, names):
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and (mode & 0o170000) == 0o120000:
                    raise ValueError(f"Homelab backup symlink member refused: {name}")
                destination_path = staging / name
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                _refuse_symlink_components(destination_path.parent)
                with archive.open(info, "r") as source_handle, destination_path.open("xb") as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle, length=4 * 1024 * 1024)
                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())
        manifest_path = staging / "backup.manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Homelab backup manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        homelab_validate_seal(manifest)
        if manifest.get("kind") != "earcrate_homelab_backup_manifest":
            raise ValueError("invalid Homelab backup manifest kind")
        declared = {str(row["path"]): dict(row) for row in manifest.get("entries") or []}
        actual_files = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file() and path.name != "backup.manifest.json"
        }
        if actual_files != set(declared):
            raise ValueError("Homelab backup members do not match the manifest")
        for name, row in declared.items():
            path = staging / _safe_archive_name(name)
            if int(path.stat().st_size) != int(row["bytes"]) or estate_sha256_file(path) != str(row["sha256"]):
                raise ValueError(f"Homelab backup member failed verification: {name}")
        with HomelabStore(staging) as restored:
            staged_doctor = restored.doctor()
            if not staged_doctor["ok"]:
                raise ValueError("restored Homelab store failed doctor before promotion")
        os.replace(staging, target)
        _fsync_directory(target.parent)
        with HomelabStore(target) as restored:
            doctor = restored.doctor()
            if not doctor["ok"]:
                raise ValueError("restored Homelab store failed doctor after promotion")
        receipt = homelab_seal({
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_restore_receipt",
            "restored_at": _now_utc(),
            "backup_sha256": actual_backup_sha,
            "backup_manifest_sha256": manifest["manifest_sha256"],
            "destination_name": target.name,
            "store_schema_version": int(manifest["store_schema_version"]),
            "entries": len(declared),
            "doctor": doctor,
            "boundary": {
                "destination_was_new": True,
                "all_members_verified": True,
                "atomic_directory_promotion": True,
            },
        })
        if receipt_path:
            write_estate_json(receipt_path, receipt)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise


def render_homelab_dashboard(audit: Mapping[str, Any], campaign: Mapping[str, Any], output_html: str | Path) -> dict[str, Any]:
    homelab_validate_seal(audit)
    homelab_validate_seal(campaign)
    if audit.get("kind") != "earcrate_homelab_audit" or campaign.get("kind") != "earcrate_homelab_campaign":
        raise ValueError("dashboard requires a HomelabAudit and HomelabCampaign")
    if campaign.get("audit_sha256") != audit.get("audit_sha256"):
        raise ValueError("dashboard campaign belongs to another audit")
    rows = []
    for row in audit.get("targets") or []:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('target_id') or ''))}</td>"
            f"<td>{html.escape(str(row.get('feasibility') or ''))}</td>"
            f"<td>{html.escape(str(row.get('lifecycle') or ''))}</td>"
            f"<td>{html.escape(', '.join(row.get('completed_stages') or []))}</td>"
            f"<td>{html.escape(', '.join(row.get('missing_stages') or []))}</td>"
            f"<td>{html.escape('; '.join(row.get('blockers') or []))}</td>"
            "</tr>"
        )
    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EarCrate Homelab</title><style>
body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.4}table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border:1px solid #bbb;padding:.45rem;vertical-align:top}th{position:sticky;top:0;background:#eee;text-align:left}
code{background:#eee;padding:.1rem .25rem}.summary{display:flex;gap:1rem;flex-wrap:wrap}.card{border:1px solid #bbb;padding:.75rem;min-width:10rem}
</style></head><body><h1>EarCrate Homelab Provider Arcade</h1>
<p>Source-free status view. Feasibility, execution, audition, and adoption remain separate.</p>
<div class="summary">""" + "".join(
        f"<div class='card'><strong>{html.escape(str(key))}</strong><br>{html.escape(str(value))}</div>"
        for key, value in sorted((audit.get("summary") or {}).items()) if not isinstance(value, (dict, list))
    ) + """</div>
<table><thead><tr><th>Target</th><th>Feasibility</th><th>Lifecycle</th><th>Completed</th><th>Missing</th><th>Blockers</th></tr></thead>
<tbody>""" + "".join(rows) + """</tbody></table>
<p>Campaign: <code>""" + html.escape(str(campaign["campaign_sha256"])) + """</code></p>
</body></html>"""
    output = Path(output_html).expanduser().absolute()
    _refuse_symlink_components(output)
    if output.exists():
        raise ValueError(f"dashboard output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        _fsync_directory(output.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return {
        "ok": True,
        "output": str(output),
        "raw_sha256": estate_sha256_file(output),
        "bytes": int(output.stat().st_size),
    }


__all__ = [
    "export_public_store",
    "backup_homelab_store",
    "restore_homelab_backup",
    "render_homelab_dashboard",
]
