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
    if (
        PurePath(name).name != name
        or name in {".", ".."}
        or name in _RESERVED_PUBLICATION_NAMES
    ):
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
    sealed_decision = _sealed(
        _mapping(decision, "release decision"),
        "decision_sha256",
    )
    if (
        not sealed_decision.get("release_eligible")
        or sealed_decision.get("status") != "eligible"
    ):
        raise FloorError("release decision is not publish-ready")
    if (
        sealed_decision.get("campaign_sha256")
        != bundle["public_campaign"]["campaign_sha256"]
    ):
        raise FloorError("release decision belongs to another campaign")
    if sealed_decision.get("candidate_sha256") != bundle["candidate"]["candidate_sha256"]:
        raise FloorError("release decision names another candidate")

    rights = _sealed(
        _mapping(sealed_decision.get("rights"), "decision rights"),
        "rights_decision_sha256",
    )
    if sealed_decision.get("rights_decision_sha256") != rights["rights_decision_sha256"]:
        raise FloorError("release decision does not bind its embedded rights decision")
    if rights.get("campaign_sha256") != bundle["public_campaign"]["campaign_sha256"]:
        raise FloorError("rights decision belongs to another campaign")
    if rights.get("candidate_sha256") != bundle["candidate"]["candidate_sha256"]:
        raise FloorError("rights decision names another candidate")
    if rights.get("status") != "accepted_by_policy":
        raise FloorError("release decision does not carry accepted rights policy evidence")

    issued, issued_dt = _parse_time(issued_at, "permit issued_at")
    permit_expiry, permit_expiry_dt = _parse_time(expires_at, "permit expires_at")
    if permit_expiry_dt <= issued_dt:
        raise FloorError("permit expiry must be later than issuance")

    decision_as_of_dt = _parse_time(
        sealed_decision.get("as_of"),
        "release decision as_of",
    )[1]
    rights_valid_from_dt = _parse_time(
        rights.get("valid_from"),
        "rights valid_from",
    )[1]
    rights_expiry_dt = _parse_time(
        rights.get("expires_at"),
        "rights expires_at",
    )[1]
    if not (rights_valid_from_dt <= decision_as_of_dt < rights_expiry_dt):
        raise FloorError("release decision time is outside the rights validity interval")
    if issued_dt < decision_as_of_dt:
        raise FloorError("publish permit may not be issued before the governed release decision")
    if issued_dt < rights_valid_from_dt:
        raise FloorError("publish permit may not be issued before rights become valid")
    if permit_expiry_dt > rights_expiry_dt:
        raise FloorError("publish permit may not outlive the rights decision")

    available = {row["artifact_id"]: row for row in bundle["candidate_artifacts"]}
    scope: list[dict[str, Any]] = []
    output_names: set[str] = set()
    artifact_ids: set[str] = set()
    for index, item in enumerate(
        _sequence(publication_scope, "publication_scope")
    ):
        row = _mapping(item, f"publication scope {index}")
        artifact_id = _text(
            row.get("artifact_id"),
            f"publication scope {index} artifact_id",
        )
        if artifact_id not in available:
            raise FloorError(
                f"publication scope references uncommitted candidate artifact {artifact_id}"
            )
        role = _text(
            row.get("role"),
            f"publication scope {index} role",
        )
        artifact = available[artifact_id]
        if artifact["role"] != role:
            raise FloorError(
                f"publication scope role {role} does not match committed artifact role "
                f"{artifact['role']}"
            )
        output_name = _safe_output_name(row.get("output_name"))
        if output_name in output_names or artifact_id in artifact_ids:
            raise FloorError(
                "publication scope output names and artifact IDs must be unique"
            )
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


def _absolute_without_symlinks(path: str | Path, field: str) -> Path:
    absolute = Path(path).expanduser().absolute()
    current = absolute
    while True:
        if current.is_symlink():
            raise FloorError(f"{field} or one of its parents is a symlink: {absolute}")
        if current.parent == current:
            break
        current = current.parent
    return absolute


def _assert_regular_input(path: Path) -> None:
    _absolute_without_symlinks(path, "publication input")
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
    target_fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o644,
    )
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
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _promote_directory(staging: Path, output: Path) -> str:
    if os.name == "nt":
        import ctypes

        MOVEFILE_WRITE_THROUGH = 0x00000008
        if not ctypes.windll.kernel32.MoveFileExW(
            str(staging),
            str(output),
            MOVEFILE_WRITE_THROUGH,
        ):
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
    sealed_permit = _sealed(
        _mapping(permit, "publish permit"),
        "permit_sha256",
    )
    published, published_dt = _parse_time(published_at, "published_at")
    issued_dt = _parse_time(sealed_permit["issued_at"], "permit issued_at")[1]
    expires_dt = _parse_time(
        sealed_permit["expires_at"],
        "permit expires_at",
    )[1]
    if not (issued_dt <= published_dt < expires_dt):
        raise FloorError("publish permit is not valid at published_at")
    paths = {
        str(key): Path(value).expanduser().absolute()
        for key, value in dict(artifact_paths).items()
    }
    expected_ids = {
        row["artifact_id"]
        for row in sealed_permit["artifacts"]
    }
    if set(paths) != expected_ids:
        raise FloorError(
            "artifact_paths must contain exactly the permitted artifact IDs"
        )
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    for artifact in sealed_permit["artifacts"]:
        artifact_id = _text(
            artifact.get("artifact_id"),
            "publish permit artifact_id",
        )
        output_name = _safe_output_name(artifact.get("output_name"))
        if output_name != artifact.get("output_name"):
            raise FloorError("publish permit contains a non-canonical output name")
        if output_name in seen_names or artifact_id in seen_ids:
            raise FloorError(
                "publish permit artifact IDs and output names must be unique"
            )
        seen_names.add(output_name)
        seen_ids.add(artifact_id)
        source = paths[artifact_id]
        _assert_regular_input(source)
        if (
            source.stat().st_size != artifact["size_bytes"]
            or _file_sha256(source) != artifact["sha256"]
        ):
            raise FloorError(
                f"reviewed artifact bytes changed: {artifact['artifact_id']}"
            )

    output = _absolute_without_symlinks(
        output_dir,
        "publication path",
    )
    parent = output.parent
    if output.exists():
        raise FloorError(f"refusing existing publication path: {output}")
    if not parent.is_dir():
        raise FloorError(
            "publication parent must be an existing non-symlink directory"
        )
    staging = parent / (
        f".{output.name}.staging-{sealed_permit['permit_sha256'][:16]}"
    )
    if staging.exists() or staging.is_symlink():
        raise FloorError(
            f"refusing existing publication staging path: {staging}"
        )
    staging.mkdir(mode=0o755)
    promoted = False
    try:
        published_artifacts: list[dict[str, Any]] = []
        for artifact in sealed_permit["artifacts"]:
            target = staging / artifact["output_name"]
            _copy_fsync(
                paths[artifact["artifact_id"]],
                target,
            )
            if (
                target.stat().st_size != artifact["size_bytes"]
                or _file_sha256(target) != artifact["sha256"]
            ):
                raise FloorError(
                    "staged publication bytes differ from the permitted artifact"
                )
            published_artifacts.append(
                {**artifact, "published_name": target.name}
            )

        permit_path = staging / "publish-permit.json"
        _write_fsync(
            permit_path,
            _canonical_json_bytes(sealed_permit),
        )
        manifest = {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_publication_manifest",
            "campaign_sha256": sealed_permit["campaign_sha256"],
            "candidate_sha256": sealed_permit["candidate_sha256"],
            "permit_sha256": sealed_permit["permit_sha256"],
            "published_at": published,
            "artifacts": published_artifacts,
        }
        manifest = _sealed(
            manifest,
            "publication_manifest_sha256",
        )
        manifest_path = staging / "publication-manifest.json"
        _write_fsync(
            manifest_path,
            _canonical_json_bytes(manifest),
        )

        checksum_rows = []
        for path in sorted(
            staging.iterdir(),
            key=lambda row: row.name,
        ):
            if path.is_file():
                checksum_rows.append(
                    f"{_file_sha256(path)}  {path.name}\n"
                )
        sums_path = staging / "SHA256SUMS"
        _write_fsync(
            sums_path,
            "".join(checksum_rows).encode("utf-8"),
        )

        receipt = {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "kind": "earcrate_floor_publication_receipt",
            "campaign_sha256": sealed_permit["campaign_sha256"],
            "candidate_sha256": sealed_permit["candidate_sha256"],
            "permit_sha256": sealed_permit["permit_sha256"],
            "publication_manifest_sha256": manifest[
                "publication_manifest_sha256"
            ],
            "checksums_sha256": _file_sha256(sums_path),
            "published_at": published,
            "output_name": output.name,
            "artifact_count": len(published_artifacts),
            "atomic_directory_promotion": True,
            "durability_mode": (
                "windows_movefile_write_through"
                if os.name == "nt"
                else "posix_fsync_and_rename"
            ),
            "complete": True,
        }
        receipt = _sealed(
            receipt,
            "publication_receipt_sha256",
        )
        receipt_path = staging / "publication-receipt.json"
        _write_fsync(
            receipt_path,
            _canonical_json_bytes(receipt),
        )
        _fsync_directory(staging)
        mode = _promote_directory(staging, output)
        promoted = True
        if mode != receipt["durability_mode"]:
            raise FloorError(
                "publication durability mode changed during promotion"
            )
        return floor_verify_published_release(output)
    except Exception:
        if promoted and output.exists():
            shutil.rmtree(output, ignore_errors=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_sealed_json(
    path: Path,
    *,
    label: str,
    hash_field: str,
) -> dict[str, Any]:
    _assert_regular_input(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FloorError(f"{label} is unreadable or invalid JSON") from exc
    return _sealed(
        _mapping(value, label),
        hash_field,
    )


def floor_verify_published_release(
    output_dir: str | Path,
) -> dict[str, Any]:
    output = _absolute_without_symlinks(
        output_dir,
        "published release directory",
    )
    if not output.is_dir():
        raise FloorError(
            "published release directory is missing or symlinked"
        )

    entries = list(output.iterdir())
    entry_by_name: dict[str, Path] = {}
    for entry in entries:
        try:
            mode = entry.lstat().st_mode
        except FileNotFoundError as exc:
            raise FloorError(
                "published release changed during verification"
            ) from exc
        if stat.S_ISLNK(mode):
            raise FloorError(
                f"published release contains a symlink: {entry.name}"
            )
        if not stat.S_ISREG(mode):
            raise FloorError(
                f"published release contains an undeclared non-file entry: "
                f"{entry.name}"
            )
        if entry.name in entry_by_name:
            raise FloorError(
                f"published release contains duplicate entry name: {entry.name}"
            )
        entry_by_name[entry.name] = entry

    control_names = {
        "publish-permit.json",
        "publication-manifest.json",
        "publication-receipt.json",
        "SHA256SUMS",
    }
    missing_control = sorted(control_names - set(entry_by_name))
    if missing_control:
        raise FloorError(
            "published release is missing control files: "
            + ", ".join(missing_control)
        )

    permit = _read_sealed_json(
        entry_by_name["publish-permit.json"],
        label="publish permit",
        hash_field="permit_sha256",
    )
    manifest = _read_sealed_json(
        entry_by_name["publication-manifest.json"],
        label="publication manifest",
        hash_field="publication_manifest_sha256",
    )
    receipt = _read_sealed_json(
        entry_by_name["publication-receipt.json"],
        label="publication receipt",
        hash_field="publication_receipt_sha256",
    )
    if (
        receipt["permit_sha256"] != permit["permit_sha256"]
        or receipt["publication_manifest_sha256"]
        != manifest["publication_manifest_sha256"]
    ):
        raise FloorError(
            "publication receipt does not bind the permit and manifest"
        )
    if manifest.get("permit_sha256") != permit["permit_sha256"]:
        raise FloorError("publication manifest does not bind the permit")
    if (
        manifest.get("campaign_sha256") != permit.get("campaign_sha256")
        or manifest.get("candidate_sha256") != permit.get("candidate_sha256")
    ):
        raise FloorError(
            "publication manifest belongs to another campaign or candidate"
        )
    if (
        receipt.get("campaign_sha256") != permit.get("campaign_sha256")
        or receipt.get("candidate_sha256") != permit.get("candidate_sha256")
    ):
        raise FloorError(
            "publication receipt belongs to another campaign or candidate"
        )
    if receipt.get("output_name") != output.name:
        raise FloorError(
            "publication receipt names another output directory"
        )
    if not receipt.get("complete"):
        raise FloorError("publication receipt is not complete")

    artifacts = [
        _mapping(row, f"publish permit artifact {index}")
        for index, row in enumerate(
            _sequence(permit.get("artifacts"), "publish permit artifacts")
        )
    ]
    artifact_names: list[str] = []
    artifact_ids: list[str] = []
    for artifact in artifacts:
        output_name = _safe_output_name(artifact.get("output_name"))
        if output_name != artifact.get("output_name"):
            raise FloorError("publish permit contains a non-canonical output name")
        artifact_names.append(output_name)
        artifact_ids.append(
            _text(artifact.get("artifact_id"), "publish permit artifact_id")
        )
    if (
        len(set(artifact_names)) != len(artifact_names)
        or len(set(artifact_ids)) != len(artifact_ids)
    ):
        raise FloorError(
            "publish permit artifact IDs and output names must be unique"
        )

    expected_entry_names = control_names | set(artifact_names)
    actual_entry_names = set(entry_by_name)
    if actual_entry_names != expected_entry_names:
        extra = sorted(actual_entry_names - expected_entry_names)
        missing = sorted(expected_entry_names - actual_entry_names)
        details = []
        if extra:
            details.append("undeclared: " + ", ".join(extra))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise FloorError(
            "published release entries do not match the permit"
            + (": " + "; ".join(details) if details else "")
        )

    expected_manifest_artifacts = [
        {**artifact, "published_name": artifact["output_name"]}
        for artifact in artifacts
    ]
    if manifest.get("artifacts") != expected_manifest_artifacts:
        raise FloorError(
            "publication manifest does not bind the exact permitted artifacts"
        )
    if receipt.get("artifact_count") != len(artifacts):
        raise FloorError(
            "publication receipt artifact count does not match the permit"
        )
    if receipt.get("published_at") != manifest.get("published_at"):
        raise FloorError(
            "publication receipt and manifest disagree on publication time"
        )

    for artifact in artifacts:
        path = entry_by_name[artifact["output_name"]]
        _assert_regular_input(path)
        if (
            path.stat().st_size != artifact["size_bytes"]
            or _file_sha256(path) != artifact["sha256"]
        ):
            raise FloorError(
                f"published artifact failed custody: {artifact['artifact_id']}"
            )

    sums_path = entry_by_name["SHA256SUMS"]
    if _file_sha256(sums_path) != receipt["checksums_sha256"]:
        raise FloorError("publication checksum ledger changed")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in expected:
            raise FloorError("publication checksum ledger is malformed")
        expected[name] = _sha(digest, f"checksum for {name}")
    checksummed_names = expected_entry_names - {
        "publication-receipt.json",
        "SHA256SUMS",
    }
    if set(expected) != checksummed_names:
        raise FloorError(
            "publication checksum ledger does not cover the complete release"
        )
    for name, digest in expected.items():
        if _file_sha256(entry_by_name[name]) != digest:
            raise FloorError(
                f"publication file changed after promotion: {name}"
            )
    return {
        "output_dir": str(output),
        "files": sorted(actual_entry_names),
        "permit_sha256": permit["permit_sha256"],
        "publication_manifest_sha256": manifest[
            "publication_manifest_sha256"
        ],
        "publication_receipt_sha256": receipt[
            "publication_receipt_sha256"
        ],
        "complete": True,
    }
