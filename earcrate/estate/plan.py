from __future__ import annotations

"""Estate architecture planning and signature-gated consolidation.

Estate v1 is intentionally copy/reference only.  It can build a coherent managed
index without deleting or merging the scattered originals.  Eviction, archival,
and quarantine decisions are surfaced as candidates for a later reviewed plan.
"""

from collections import Counter
from copy import deepcopy
import contextlib
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Mapping

from earcrate.estate.model import (
    ESTATE_SCHEMA_VERSION,
    ESTATE_PLAN_ACTIONS,
    default_estate_policy,
    estate_architecture,
    estate_ensure_within,
    estate_normalize_relative_path,
    estate_safe_component,
    estate_seal,
    estate_sha256_file,
    estate_sha256_json,
    estate_validate_seal,
    load_estate_json,
    validate_estate_policy,
    write_estate_json,
)


def _estate_plan_now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _estate_target_bucket(item: Mapping[str, Any]) -> str:
    classification = str(item.get("classification") or "unknown")
    disposition = str(item.get("disposition") or "manual_review")
    if classification in {"project_index", "project_revision", "command_ledger"}:
        return "authority/projects"
    if classification in {"workspace_pointer", "workspace_config", "database"}:
        return "authority/workspaces"
    if classification in {"schema", "proof_manifest", "model_manifest"}:
        return "evidence/manifests"
    if classification in {"proof_pack", "proof_receipt", "ci_ledger", "run_receipt"}:
        return "evidence/packs"
    if classification in {"human_review", "rights_record"}:
        return "evidence/reviews"
    if classification in {"release_candidate", "audition_audio"}:
        return "artifacts/auditions"
    if classification in {"render_audio", "midi"}:
        return "artifacts/renders"
    if classification == "stem_audio":
        return "artifacts/stems"
    if classification == "analysis_cache":
        return "artifacts/analysis"
    if classification in {"artifact_blob", "artifact_metadata"}:
        return "cache/warm"
    if classification in {"model_weight"}:
        return "models/weights"
    if classification == "distribution":
        return "archive/releases"
    if classification in {"repository", "documentation"}:
        return "archive/repositories"
    if classification in {"source_audio", "source_score"}:
        return "material/approved"
    if disposition == "temporary":
        return "cache/ephemeral"
    return "quarantine"


def _estate_target_relative(item: Mapping[str, Any]) -> str:
    bucket = _estate_target_bucket(item)
    root_id = estate_safe_component(str(item.get("root_id") or "root"), "root")
    relative = str(item.get("relative_path") or "item")
    name = Path(relative).name
    safe_name = estate_safe_component(name, "item", 140)
    digest = str(item.get("raw_sha256") or "")
    if digest:
        leaf = f"{digest[:16]}-{safe_name}"
        return estate_normalize_relative_path(f"{bucket}/{digest[:2]}/{leaf}")
    item_id = estate_safe_component(str(item.get("item_id") or "item"), "item")
    return estate_normalize_relative_path(f"{bucket}/unhashed/{root_id}/{item_id}-{safe_name}")


def _estate_action_for_item(item: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, list[str]]:
    disposition = str(item.get("disposition") or "manual_review")
    configured = str((policy.get("actions") or {}).get(disposition) or "manual_review")
    reasons: list[str] = [f"policy maps {disposition} to {configured}"]
    if configured not in ESTATE_PLAN_ACTIONS:
        return "manual_review", reasons + ["invalid policy action"]
    if item.get("file_type") == "symlink":
        return "manual_review", reasons + ["symlink source requires explicit human decision"]
    if item.get("classification") == "database":
        return "manual_review", reasons + ["SQLite authority requires an explicit online-backup/checkpoint adapter; generic file copy is refused"]
    if item.get("classification") in {"source_audio", "source_score"} and not (policy.get("safety") or {}).get("copy_source_media"):
        return "reference", reasons + ["source media remains external by default"]
    if configured == "copy" and not item.get("raw_sha256") and (policy.get("safety") or {}).get("require_hash_for_copy"):
        return "manual_review", reasons + ["copy requires a strong source hash; rerun inventory with a stronger hash mode"]
    return configured, reasons


def propose_estate_plan(
    inventory: Mapping[str, Any],
    estate_root: str | Path,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    estate_validate_seal(inventory)
    if inventory.get("kind") != "earcrate_estate_inventory":
        raise ValueError("not an estate inventory")
    active_policy = validate_estate_policy(policy or default_estate_policy())
    target_root = Path(estate_root).expanduser().resolve()

    operations: list[dict[str, Any]] = []
    for item in inventory.get("items") or []:
        action, reasons = _estate_action_for_item(item, active_policy)
        target_relative = _estate_target_relative(item) if action == "copy" else None
        operation = {
            "operation_id": "op_" + estate_sha256_json(
                {
                    "item_id": item.get("item_id"),
                    "action": action,
                    "target": target_relative,
                    "sha256": item.get("raw_sha256"),
                }
            )[:24],
            "item_id": item.get("item_id"),
            "classification": item.get("classification"),
            "disposition": item.get("disposition"),
            "action": action,
            "source_path": item.get("absolute_path"),
            "source_root_id": item.get("root_id"),
            "source_relative_path": item.get("relative_path"),
            "target_relative_path": target_relative,
            "expected_sha256": item.get("raw_sha256"),
            "expected_bytes": int(item.get("bytes") or 0),
            "reasons": reasons,
            "automatic_apply_supported": action in {"copy", "reference", "retain_in_place"},
        }
        operations.append(operation)

    operations.sort(key=lambda operation: (str(operation["action"]), str(operation["source_root_id"]), str(operation["source_relative_path"]).casefold()))
    counts = Counter(str(operation["action"]) for operation in operations)
    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_estate_plan",
        "created_at": _estate_plan_now_utc(),
        "inventory_sha256": inventory["inventory_sha256"],
        "policy_sha256": active_policy["policy_sha256"],
        "architecture_sha256": estate_architecture()["architecture_sha256"],
        "estate_root": str(target_root),
        "operations": operations,
        "summary": {
            "operations": len(operations),
            "actions": dict(sorted(counts.items())),
            "copy_bytes": sum(int(operation["expected_bytes"]) for operation in operations if operation["action"] == "copy"),
            "automatic_apply_operations": sum(1 for operation in operations if operation["automatic_apply_supported"]),
            "human_decisions": sum(1 for operation in operations if not operation["automatic_apply_supported"]),
            "source_files_deleted": 0,
            "databases_merged": 0,
        },
        "safety": {
            "copy_only_v1": True,
            "source_deletion_permitted": False,
            "database_merge_permitted": False,
            "approval_required": True,
        },
    }
    return estate_seal(payload)


def _estate_refuse_symlink_parents(path: Path, stop: Path) -> None:
    current = path
    resolved_stop = stop.resolve()
    while True:
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked estate path refused: {current}")
        if current == resolved_stop or current.parent == current:
            break
        current = current.parent


def _estate_fsync_directory(path: Path) -> bool:
    if os.name == "nt":
        return False
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def _estate_copy_verified(source: Path, target: Path, expected_sha256: str, expected_bytes: int) -> tuple[str, bool]:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"source must be a regular non-symlink file: {source}")
    before = source.stat()
    if int(before.st_size) != int(expected_bytes):
        raise ValueError(f"source size changed: {source}")
    actual_source = estate_sha256_file(source)
    if actual_source != expected_sha256:
        raise ValueError(f"source hash changed: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"target collision is not a regular file: {target}")
        actual_target = estate_sha256_file(target)
        if actual_target != expected_sha256 or int(target.stat().st_size) != int(expected_bytes):
            raise ValueError(f"target collision has different content: {target}")
        return actual_target, False

    temporary = target.with_name(f".{target.name}.estate-tmp-{os.getpid()}")
    if temporary.exists():
        raise ValueError(f"stale estate temporary exists: {temporary}")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=4 * 1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        copied_hash = estate_sha256_file(temporary)
        if copied_hash != expected_sha256 or int(temporary.stat().st_size) != int(expected_bytes):
            raise ValueError(f"copied bytes did not verify: {target}")
        after = source.stat()
        if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(before.st_mtime_ns):
            raise ValueError(f"source changed during copy: {source}")
        os.replace(temporary, target)
        _estate_fsync_directory(target.parent)
        return copied_hash, True
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def apply_estate_plan(
    plan: Mapping[str, Any],
    *,
    approve_sha256: str,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    estate_validate_seal(plan)
    if plan.get("kind") != "earcrate_estate_plan":
        raise ValueError("not an estate plan")
    expected_plan = str(plan["plan_sha256"])
    if str(approve_sha256 or "") != expected_plan:
        raise ValueError(f"approval mismatch; pass exact plan SHA-256 {expected_plan}")
    if not (plan.get("safety") or {}).get("copy_only_v1"):
        raise ValueError("estate v1 applies copy-only plans")

    estate_root = Path(str(plan["estate_root"])).expanduser().resolve()
    if estate_root.exists() and estate_root.is_symlink():
        raise ValueError("estate root may not be a symlink")
    estate_root.mkdir(parents=True, exist_ok=True)
    _estate_refuse_symlink_parents(estate_root, estate_root)

    for directory in estate_architecture()["directories"]:
        relative = estate_normalize_relative_path(str(directory["path"]))
        target = estate_ensure_within(estate_root / relative, estate_root)
        _estate_refuse_symlink_parents(target.parent, estate_root)
        target.mkdir(parents=True, exist_ok=True)

    created: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    try:
        for operation in plan.get("operations") or []:
            action = str(operation.get("action") or "")
            if action in {"reference", "retain_in_place"}:
                references.append({
                    "operation_id": operation["operation_id"],
                    "action": action,
                    "source_path": operation.get("source_path"),
                    "expected_sha256": operation.get("expected_sha256"),
                })
                continue
            if action != "copy":
                ignored.append({
                    "operation_id": operation["operation_id"],
                    "action": action,
                    "reason": "requires later human-reviewed cleanup decision",
                })
                continue
            expected_sha256 = str(operation.get("expected_sha256") or "")
            if len(expected_sha256) != 64:
                raise ValueError(f"copy operation lacks strong hash: {operation['operation_id']}")
            source = Path(str(operation["source_path"])).expanduser().resolve()
            target_relative = estate_normalize_relative_path(str(operation["target_relative_path"]))
            target = estate_ensure_within(estate_root / target_relative, estate_root)
            _estate_refuse_symlink_parents(target.parent, estate_root)
            copied_hash, was_created = _estate_copy_verified(
                source,
                target,
                expected_sha256,
                int(operation.get("expected_bytes") or 0),
            )
            record = {
                "operation_id": operation["operation_id"],
                "target_relative_path": target_relative,
                "sha256": copied_hash,
                "bytes": int(operation.get("expected_bytes") or 0),
            }
            (created if was_created else reused).append(record)

        plan_copy = estate_root / "control" / "plans" / f"{expected_plan}.json"
        plan_copy.parent.mkdir(parents=True, exist_ok=True)
        if plan_copy.exists():
            if load_estate_json(plan_copy) != dict(plan):
                raise ValueError(f"estate plan identity collision: {plan_copy}")
        else:
            write_estate_json(plan_copy, plan)

        payload: dict[str, Any] = {
            "schema_version": ESTATE_SCHEMA_VERSION,
            "kind": "earcrate_estate_apply_receipt",
            "applied_at": _estate_plan_now_utc(),
            "plan_sha256": expected_plan,
            "inventory_sha256": plan.get("inventory_sha256"),
            "policy_sha256": plan.get("policy_sha256"),
            "estate_root": str(estate_root),
            "created": created,
            "reused": reused,
            "references": references,
            "deferred_operations": ignored,
            "source_files_deleted": 0,
            "databases_merged": 0,
            "durability": {
                "files_fsynced": True,
                "directory_fsync_attempted": os.name != "nt",
                "atomic_file_replace": True,
            },
        }
        receipt = estate_seal(payload)
        destination = Path(receipt_path).expanduser() if receipt_path else estate_root / "control" / "receipts" / f"{receipt['receipt_sha256']}.json"
        write_estate_json(destination, receipt)
        return receipt
    except Exception:
        for record in reversed(created):
            target = estate_root / str(record["target_relative_path"])
            with contextlib.suppress(Exception):
                if target.is_file() and not target.is_symlink() and estate_sha256_file(target) == record["sha256"]:
                    target.unlink()
                    _estate_fsync_directory(target.parent)
        raise


def rollback_estate_apply(
    receipt: Mapping[str, Any],
    *,
    approve_sha256: str,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    estate_validate_seal(receipt)
    if receipt.get("kind") != "earcrate_estate_apply_receipt":
        raise ValueError("not an estate apply receipt")
    expected = str(receipt["receipt_sha256"])
    if str(approve_sha256 or "") != expected:
        raise ValueError(f"rollback approval mismatch; pass exact receipt SHA-256 {expected}")
    estate_root = Path(str(receipt["estate_root"])).expanduser().resolve()
    removed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for record in reversed(receipt.get("created") or []):
        relative = estate_normalize_relative_path(str(record["target_relative_path"]))
        target = estate_ensure_within(estate_root / relative, estate_root)
        if not target.exists():
            continue
        if target.is_symlink() or not target.is_file():
            refused.append({"target_relative_path": relative, "reason": "not a regular file"})
            continue
        actual = estate_sha256_file(target)
        if actual != str(record["sha256"]) or int(target.stat().st_size) != int(record["bytes"]):
            refused.append({"target_relative_path": relative, "reason": "post-apply bytes changed", "actual_sha256": actual})
            continue
        target.unlink()
        _estate_fsync_directory(target.parent)
        removed.append({"target_relative_path": relative, "sha256": actual})

    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_estate_rollback_receipt",
        "rolled_back_at": _estate_plan_now_utc(),
        "apply_receipt_sha256": expected,
        "estate_root": str(estate_root),
        "removed": removed,
        "refused": refused,
        "source_files_affected": 0,
    }
    result = estate_seal(payload)
    if output_path:
        write_estate_json(output_path, result)
    return result


def verify_estate_apply(receipt: Mapping[str, Any]) -> dict[str, Any]:
    estate_validate_seal(receipt)
    if receipt.get("kind") != "earcrate_estate_apply_receipt":
        raise ValueError("not an estate apply receipt")
    estate_root = Path(str(receipt["estate_root"])).expanduser().resolve()
    missing: list[str] = []
    mismatched: list[dict[str, str]] = []
    for record in list(receipt.get("created") or []) + list(receipt.get("reused") or []):
        relative = estate_normalize_relative_path(str(record["target_relative_path"]))
        target = estate_ensure_within(estate_root / relative, estate_root)
        if not target.is_file() or target.is_symlink():
            missing.append(relative)
            continue
        actual = estate_sha256_file(target)
        if actual != str(record["sha256"]):
            mismatched.append({"target_relative_path": relative, "expected_sha256": str(record["sha256"]), "actual_sha256": actual})
    return {
        "ok": not missing and not mismatched,
        "receipt_sha256": receipt["receipt_sha256"],
        "estate_root": str(estate_root),
        "verified_files": len(receipt.get("created") or []) + len(receipt.get("reused") or []) - len(missing) - len(mismatched),
        "missing": missing,
        "mismatched": mismatched,
    }


__all__ = [
    "propose_estate_plan",
    "apply_estate_plan",
    "rollback_estate_apply",
    "verify_estate_apply",
]
