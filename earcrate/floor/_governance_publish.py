"""Content-bound publish permits and atomic publication."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePath
import shutil
import stat
from typing import Any, Mapping, Sequence

from earcrate.floor.model import FloorError
from earcrate.floor._governance_common import (
    GOVERNANCE_SCHEMA_VERSION,
    _RESERVED_PUBLICATION_NAMES,
    _canonical_json_bytes,
    _mapping,
    _parse_time,
    _sealed,
    _sequence,
    _sha,
    _text,
)
from earcrate.floor._governance_campaign import _seal_campaign_bundle

def _safe_output_name(value: Any) -> str:
    name = _text(value, "publication output_name")
    if PurePath(name).name != name or name in {".", ".."} or name in _RESERVED_PUBLICATION_NAMES:
        raise FloorError(f"unsafe or reserved publication output_name: {name!r}")
    return name


def floor_issue_publish_permit(
    campaign: Mapping[str, Any],
    decision: Mapping[str, Any],
    publication_scope: Sequence[Mapping[str, Any]],
    *,
    issued_at: str,
    expires_at: str,
) -> dict[str, Any]:
    bundle = _seal_campaign_bundle(campaign)
    sealed_decision = _sealed(_mapping(decision, "release decision"), "decision_sha256")
    if not sealed_decision.get("release_eligible") or sealed_decision.get("status") != "eligible":
        raise FloorError("release decision is not publish-ready")
    if sealed_decision.get("campaign_sha256") != bundle["public_campaign"]["campaign_sha256"]:
        raise FloorError("release decision belongs to another campaign")
    issued, issued_dt = _parse_time(issued_at, "permit issued_at")
    permit_expiry, permit_expiry_dt = _parse_time(expires_at, "permit expires_at")
    if permit_expiry_dt <= issued_dt:
        raise FloorError("permit expiry must be later than issuance")
    rights = _mapping(sealed_decision.get("rights"), "decision rights")
    rights_expiry_dt = _parse_time(rights.get("expires_at"), "rights expires_at")[1]
    if permit_expiry_dt > rights_expiry_dt:
        raise FloorError("publish permit may not outlive the rights decision")

    available = {row["artifact_id"]: row for row in bundle["candidate_artifacts"]}
    scope: list[dict[str, Any]] = []
    output_names: set[str] = set()
    artifact_ids: set[str] = set()
    for index, item in enumerate(_sequence(publication_scope, "publication_scope")):
        row = _mapping(item, f"publication scope {index}")
        artifact_id = _text(row.get("artifact_id"), f"publication scope {index} artifact_id")
        if artifact_id not in available:
            raise FloorError(f"publication scope references uncommitted candidate artifact {artifact_id}")
        role = _text(row.get("role"), f"publication scope {index} role")
        artifact = available[artifact_id]
        if artifact["role"] != role:
            raise FloorError(f"publication scope role {role} does not match committed artifact role {artifact['role']}")
        output_name = _safe_output_name(row.get("output_name"))
        if output_name in output_names or artifact_id in artifact_ids:
            raise FloorError("publication scope output names and artifact IDs must be unique")
        output_names.add(output_name)
        artifact_ids.add(artifact_id)
        scope.append({**artifact, "output_name": output_name})
    if not scope:
        raise FloorError("publish permit requires at least one artifact")
    permit = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "kind": "earcrate_floor_publish_permit",
        "campaign_sha256": bundle["public_campaign"]["campaign_sha256"],
        "decision_sha256": sealed_decision["decision_sha256"],
        "candidate_sha256": bundle["candidate"]["candidate_sha256"],
        "rights_decision_sha256": rights["rights_decision_sha256"],
        "declared_use": rights["declared_use"],
        "jurisdictions": rights["jurisdictions"],
        "channels": rights["channels"],
        "issued_at": issued,
        "expires_at": permit_expiry,
        "artifacts": sorted(scope, key=lambda row: row["output_name"]),
    }
    return _sealed(permit, "permit_sha256")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_regular_input(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise FloorError(f"publication input or parent is a symlink: {path}")
        if current.parent == current:
            break
        current = current.parent
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as exc:
        raise FloorError(f"publication input is missing: {path}") from exc
    if not stat.S_ISREG(mode):
        raise FloorError(f"publication input is not a regular file: {path}")


def _write_fsync(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_fsync(source: Path, target: Path) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | nofollow)
    target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise FloorError(f"publication input is not a regular file: {source}")
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
    finally:
        os.close(source_fd)
        os.close(target_fd)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _promote_directory(staging: Path, output: Path) -> str:
    if os.name == "nt":
        import ctypes

        MOVEFILE_WRITE_THROUGH = 0x00000008
        if not ctypes.windll.kernel32.MoveFileExW(str(staging), str(output), MOVEFILE_WRITE_THROUGH):
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
        return "windows_movefile_write_through"
    _fsync_directory(staging)
    os.replace(staging, output)
    _fsync_directory(output.parent)
    return "posix_fsync_and_rename"


def floor_publish_release(
    permit: Mapping[str, Any],
    *,
    artifact_paths: Mapping[str, str | Path],
    output_dir: Path,
    published_at: str,
) -> dict[str, Any]:
    sealed_permit = _sealed(_mapping(permit, "publish permit"), "permit_sha256")
    published, published_dt = _parse_time(published_at, "published_at")
    issued_dt = _parse_time(sealed_permit["issued_at"], "permit issued_at")[1]
    expires_dt = _parse_time(sealed_permit["expires_at"], "permit expires_at")[1]
    if not (issued_dt <= published_dt < expires_dt):
        raise FloorError("publish permit is not valid at published_at")
    paths = {str(key): Path(value).expanduser().absolute() for key, value in dict(artifact_paths).items()}
    expected_ids = {row["artifact_id"] for row in sealed_permit["artifacts"]}
    if set(paths) != expected_ids:
        raise FloorError("artifact_paths must contain exactly the permitted artifact IDs")
    for artifact in sealed_permit["artifacts"]:
        source = paths[artifact["artifact_id"]]
        _assert_regular_input(source)
        if source.stat().st_size != artifact["size_bytes"] or _file_sha256(source) != artifact["sha256"]:
            raise FloorError(f"reviewed artifact bytes changed: {artifact['artifact_id']}")

    output = Path(output_dir).expanduser().absolute()
    parent = output.parent
    if output.exists() or output.is_symlink():
        raise FloorError(f"refusing existing publication path: {output}")
    if parent.is_symlink() or not parent.is_dir():
        raise FloorError("publication parent must be an existing non-symlink directory")
    staging = parent / f".{output.name}.staging-{sealed_permit['permit_sha256'][:16]}"
    if staging.exists() or staging.is_symlink():
        raise FloorError(f"refusing existing publication staging path: {staging}")
    staging.mkdir(mode=0o755)
    promoted = False
    try:
        published_artifacts: list[dict[str, Any]] = []
        for artifact in sealed_permit["artifacts"]:
            target = staging / artifact["output_name"]
            _copy_fsync(paths[artifact["artifact_id"]], target)
            if target.stat().st_size != artifact["size_bytes"] or _file_sha256(target) != artifact["sha256"]:
                raise FloorError("staged publication bytes differ from the permitted artifact")
            published_artifacts.append({**artifact, "published_name": target.name})

        permit_path = staging / "publish-permit.json"
        _write_fsync(permit_path, _canonical_json_bytes(sealed_permit))
        manifest = {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_publication_manifest",
            "campaign_sha256": sealed_permit["campaign_sha256"],
            "candidate_sha256": sealed_permit["candidate_sha256"],
            "permit_sha256": sealed_permit["permit_sha256"],
            "published_at": published,
            "artifacts": published_artifacts,
        }
        manifest = _sealed(manifest, "publication_manifest_sha256")
        manifest_path = staging / "publication-manifest.json"
        _write_fsync(manifest_path, _canonical_json_bytes(manifest))

        checksum_rows = []
        for path in sorted(staging.iterdir(), key=lambda row: row.name):
            if path.is_file():
                checksum_rows.append(f"{_file_sha256(path)}  {path.name}\n")
        sums_path = staging / "SHA256SUMS"
        _write_fsync(sums_path, "".join(checksum_rows).encode("utf-8"))

        receipt = {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_publication_receipt",
            "campaign_sha256": sealed_permit["campaign_sha256"],
            "candidate_sha256": sealed_permit["candidate_sha256"],
            "permit_sha256": sealed_permit["permit_sha256"],
            "publication_manifest_sha256": manifest["publication_manifest_sha256"],
            "checksums_sha256": _file_sha256(sums_path),
            "published_at": published,
            "output_name": output.name,
            "artifact_count": len(published_artifacts),
            "atomic_directory_promotion": True,
            "durability_mode": "windows_movefile_write_through" if os.name == "nt" else "posix_fsync_and_rename",
            "complete": True,
        }
        receipt = _sealed(receipt, "publication_receipt_sha256")
        receipt_path = staging / "publication-receipt.json"
        _write_fsync(receipt_path, _canonical_json_bytes(receipt))
        _fsync_directory(staging)
        mode = _promote_directory(staging, output)
        promoted = True
        if mode != receipt["durability_mode"]:
            raise FloorError("publication durability mode changed during promotion")
        verified = floor_verify_published_release(output)
        return verified
    except Exception:
        if promoted and output.exists():
            shutil.rmtree(output, ignore_errors=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def floor_verify_published_release(output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    if output.is_symlink() or not output.is_dir():
        raise FloorError("published release directory is missing or symlinked")
    permit = json.loads((output / "publish-permit.json").read_text(encoding="utf-8"))
    permit = _sealed(permit, "permit_sha256")
    manifest = json.loads((output / "publication-manifest.json").read_text(encoding="utf-8"))
    manifest = _sealed(manifest, "publication_manifest_sha256")
    receipt = json.loads((output / "publication-receipt.json").read_text(encoding="utf-8"))
    receipt = _sealed(receipt, "publication_receipt_sha256")
    if receipt["permit_sha256"] != permit["permit_sha256"] or receipt["publication_manifest_sha256"] != manifest["publication_manifest_sha256"]:
        raise FloorError("publication receipt does not bind the permit and manifest")
    for artifact in permit["artifacts"]:
        path = output / artifact["output_name"]
        _assert_regular_input(path)
        if path.stat().st_size != artifact["size_bytes"] or _file_sha256(path) != artifact["sha256"]:
            raise FloorError(f"published artifact failed custody: {artifact['artifact_id']}")
    sums_path = output / "SHA256SUMS"
    if _file_sha256(sums_path) != receipt["checksums_sha256"]:
        raise FloorError("publication checksum ledger changed")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in expected:
            raise FloorError("publication checksum ledger is malformed")
        expected[name] = _sha(digest, f"checksum for {name}")
    actual_names = {
        path.name
        for path in output.iterdir()
        if path.is_file() and path.name not in {"publication-receipt.json", "SHA256SUMS"}
    }
    if set(expected) != actual_names:
        raise FloorError("publication checksum ledger does not cover the complete release")
    for name, digest in expected.items():
        if _file_sha256(output / name) != digest:
            raise FloorError(f"publication file changed after promotion: {name}")
    return {
        "output_dir": str(output),
        "files": sorted(path.name for path in output.iterdir() if path.is_file()),
        "permit_sha256": permit["permit_sha256"],
        "publication_manifest_sha256": manifest["publication_manifest_sha256"],
        "publication_receipt_sha256": receipt["publication_receipt_sha256"],
        "complete": True,
    }
