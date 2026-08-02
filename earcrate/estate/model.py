from __future__ import annotations

"""Canonical local-estate objects and policy.

The estate layer is deliberately metadata-first.  It inventories and plans around
existing EarCrate work without silently promoting one workspace, database, cache,
render, or repository checkout to authority.  Mutating operations are a separate,
content-bound step.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

ESTATE_SCHEMA_VERSION = 1
ESTATE_KINDS = {
    "earcrate_estate_architecture",
    "earcrate_estate_policy",
    "earcrate_estate_inventory",
    "earcrate_estate_plan",
    "earcrate_estate_apply_receipt",
    "earcrate_estate_rollback_receipt",
    "earcrate_rig_capability_receipt",
    "earcrate_local_acceptance_campaign",
}
ESTATE_HASH_FIELDS = {
    "earcrate_estate_architecture": "architecture_sha256",
    "earcrate_estate_policy": "policy_sha256",
    "earcrate_estate_inventory": "inventory_sha256",
    "earcrate_estate_plan": "plan_sha256",
    "earcrate_estate_apply_receipt": "receipt_sha256",
    "earcrate_estate_rollback_receipt": "receipt_sha256",
    "earcrate_rig_capability_receipt": "rig_sha256",
    "earcrate_local_acceptance_campaign": "campaign_sha256",
}
ESTATE_ITEM_CLASSES = {
    "repository",
    "workspace_pointer",
    "workspace_config",
    "project_index",
    "project_revision",
    "command_ledger",
    "database",
    "analysis_cache",
    "artifact_blob",
    "artifact_metadata",
    "orphan_artifact",
    "model_weight",
    "model_manifest",
    "schema",
    "proof_manifest",
    "proof_receipt",
    "proof_pack",
    "ci_ledger",
    "release_candidate",
    "human_review",
    "rights_record",
    "source_audio",
    "source_score",
    "render_audio",
    "audition_audio",
    "stem_audio",
    "midi",
    "distribution",
    "run_receipt",
    "documentation",
    "temporary",
    "unknown",
}
ESTATE_DISPOSITIONS = {
    "authority",
    "durable_evidence",
    "external_source_reference",
    "derived_rebuildable",
    "review_queue",
    "historical_archive",
    "quarantine_candidate",
    "temporary",
    "manual_review",
}
ESTATE_PLAN_ACTIONS = {
    "copy",
    "reference",
    "retain_in_place",
    "archive_candidate",
    "evict_candidate",
    "quarantine_candidate",
    "manual_review",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def estate_json_bytes(value: Any) -> bytes:
    """Canonical bytes for estate identities."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def estate_sha256_json(value: Any) -> str:
    return hashlib.sha256(estate_json_bytes(value)).hexdigest()


def estate_sha256_file(path: str | Path, chunk_bytes: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk_bytes)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def estate_seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    if kind not in ESTATE_HASH_FIELDS:
        raise ValueError(f"unknown estate kind: {kind!r}")
    field = ESTATE_HASH_FIELDS[kind]
    value.pop(field, None)
    value[field] = estate_sha256_json(value)
    return value


def estate_validate_seal(payload: Mapping[str, Any]) -> None:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    if kind not in ESTATE_HASH_FIELDS:
        raise ValueError(f"unknown estate kind: {kind!r}")
    if int(value.get("schema_version") or 0) != ESTATE_SCHEMA_VERSION:
        raise ValueError("unsupported estate schema version")
    field = ESTATE_HASH_FIELDS[kind]
    claimed = str(value.pop(field, ""))
    if not _SHA256_RE.fullmatch(claimed):
        raise ValueError(f"invalid or missing {field}")
    actual = estate_sha256_json(value)
    if actual != claimed:
        raise ValueError(f"{field} mismatch: expected {claimed}, computed {actual}")


def estate_safe_component(value: str, fallback: str = "item", max_length: int = 96) -> str:
    text = _SAFE_COMPONENT_RE.sub("_", str(value or "")).strip(" ._-")
    return (text or fallback)[:max_length]


def estate_normalize_relative_path(value: str) -> str:
    text = str(value or "").replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe relative path: {value!r}")
    if path.parts and re.fullmatch(r"[A-Za-z]:", path.parts[0]):
        raise ValueError(f"drive-prefixed relative path is unsafe: {value!r}")
    return path.as_posix()


def estate_ensure_within(path: str | Path, root: str | Path) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    resolved_path = Path(path).expanduser().resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"path escapes estate root: {resolved_path}")
    return resolved_path


def estate_root_id(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    normalized = os.path.normcase(str(resolved))
    return "root_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def estate_item_id(root_id: str, relative_path: str, size: int, mtime_ns: int, file_type: str) -> str:
    payload = "\x1f".join((root_id, relative_path, str(int(size)), str(int(mtime_ns)), file_type))
    return "item_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def estate_architecture() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_estate_architecture",
        "name": "EarCrate Local Estate v1",
        "directories": [
            {
                "path": "control",
                "authority": "estate policy, current inventory pointers, locks, and signed plans",
                "retention": "permanent",
            },
            {
                "path": "authority/projects",
                "authority": "immutable project revisions, command ledgers, judgments, and accepted musical state",
                "retention": "permanent",
            },
            {
                "path": "authority/workspaces",
                "authority": "workspace/database snapshots pending explicit adoption; never auto-merged",
                "retention": "permanent",
            },
            {
                "path": "evidence/manifests",
                "authority": "content identities, specimen manifests, proof receipts, provenance, and checksums",
                "retention": "permanent",
            },
            {
                "path": "evidence/packs",
                "authority": "proof packs and external evidence bundles; source media remains policy-controlled",
                "retention": "permanent",
            },
            {
                "path": "evidence/reviews",
                "authority": "human review, rights decisions, release decisions, and publication receipts",
                "retention": "permanent",
            },
            {
                "path": "material/approved",
                "authority": "approved source material managed by the user",
                "retention": "user-controlled",
            },
            {
                "path": "material/incoming",
                "authority": "unadjudicated incoming files",
                "retention": "temporary until classified",
            },
            {
                "path": "artifacts/analysis",
                "authority": "derived analysis and transcription artifacts",
                "retention": "rebuildable",
            },
            {
                "path": "artifacts/stems",
                "authority": "derived source separations with provider/model/source receipts",
                "retention": "rebuildable or pinned by policy",
            },
            {
                "path": "artifacts/renders",
                "authority": "rendered outputs bound to accepted revisions",
                "retention": "warm; accepted outputs may be pinned",
            },
            {
                "path": "artifacts/auditions",
                "authority": "candidate/control listening files and local review queues",
                "retention": "until review and dependent decisions are sealed",
            },
            {
                "path": "models",
                "authority": "pinned provider binaries, model weights, licenses, and checksums",
                "retention": "while referenced by reproducible receipts",
            },
            {
                "path": "runs/rig",
                "authority": "CPU, GPU, storage, audio-device, and tool capability receipts",
                "retention": "per machine/configuration revision",
            },
            {
                "path": "runs/campaigns",
                "authority": "provider tournaments, private-library acceptance, and local audition campaigns",
                "retention": "permanent receipts; bulky intermediates policy-controlled",
            },
            {
                "path": "runs/gates",
                "authority": "gate, package, benchmark, and local acceptance ledgers",
                "retention": "latest successful and every first failure for a distinct head/configuration",
            },
            {
                "path": "cache/ephemeral",
                "authority": "never authoritative; first eviction tier",
                "retention": "budgeted",
            },
            {
                "path": "cache/warm",
                "authority": "never authoritative; reusable derived material",
                "retention": "budgeted",
            },
            {
                "path": "cache/pinned",
                "authority": "derived but explicitly pinned by a project, proof, or human",
                "retention": "until unpinned",
            },
            {
                "path": "archive/repositories",
                "authority": "historical checkouts and exact branch snapshots kept for lineage, not execution",
                "retention": "content-addressed",
            },
            {
                "path": "archive/workspaces",
                "authority": "superseded workspace/database snapshots awaiting explicit migration decisions",
                "retention": "content-addressed",
            },
            {
                "path": "archive/releases",
                "authority": "historical distributions and release artifacts",
                "retention": "policy-controlled",
            },
            {
                "path": "quarantine",
                "authority": "unknown, conflicting, corrupt, or policy-violating objects; never silent deletion",
                "retention": "until reviewed",
            },
        ],
        "invariants": [
            "Discovery is read-only except for explicit report outputs.",
            "No source media, project, judgment, review, or database is deleted automatically.",
            "Databases are never merged merely because filenames match.",
            "A workspace pointer is evidence, not authority; stale and conflicting pointers are surfaced.",
            "Exact hashes are required before any byte-copying apply operation.",
            "Derived caches remain rebuildable and may be eviction candidates only under an explicit policy.",
            "Symlinks are recorded and not followed by default.",
            "Every mutating plan is content-addressed, signature-gated, journaled, and independently verifiable.",
            "Local hardware and audition passage are separate receipts from cloud or synthetic gates.",
            "Unknown objects are quarantined for review rather than guessed into a stronger evidence tier.",
        ],
    }
    return estate_seal(payload)


def default_estate_policy() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_estate_policy",
        "scan": {
            "follow_symlinks": False,
            "max_depth": 64,
            "max_files": 2_000_000,
            "hash_mode": "evidence",
            "max_hash_bytes_per_file": 2 * 1024 * 1024 * 1024,
            "json_parse_max_bytes": 64 * 1024 * 1024,
            "sqlite_inspect_max_bytes": 256 * 1024 * 1024 * 1024,
            "zip_inventory_max_members": 250_000,
            "exclude_directory_names": [
                "$RECYCLE.BIN",
                "System Volume Information",
                ".git",
                ".hg",
                ".svn",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".tox",
                ".venv",
                "node_modules",
            ],
            "hash_extensions": [
                ".json",
                ".jsonl",
                ".toml",
                ".yaml",
                ".yml",
                ".md",
                ".txt",
                ".sha256",
                ".mid",
                ".midi",
                ".zip",
            ],
        },
        "actions": {
            "authority": "copy",
            "durable_evidence": "copy",
            "external_source_reference": "reference",
            "derived_rebuildable": "retain_in_place",
            "review_queue": "copy",
            "historical_archive": "archive_candidate",
            "quarantine_candidate": "quarantine_candidate",
            "temporary": "evict_candidate",
            "manual_review": "manual_review",
        },
        "safety": {
            "allow_delete": False,
            "allow_move": False,
            "allow_hardlink": False,
            "allow_symlink_creation": False,
            "copy_source_media": False,
            "require_hash_for_copy": True,
            "refuse_source_symlinks": True,
            "refuse_target_symlink_parents": True,
            "overwrite_identical_only": True,
        },
        "retention": {
            "keep_latest_successful_gate_ledgers": 3,
            "keep_first_failure_per_head": True,
            "keep_latest_distributions_per_version": 2,
            "ephemeral_max_age_days": 14,
            "warm_cache_budget_bytes": 500 * 1024 * 1024 * 1024,
            "pinned_never_evicted": True,
        },
        "privacy": {
            "shareable_reports_redact_absolute_paths": True,
            "record_executable_versions": True,
            "record_environment_variable_names_not_values": True,
        },
    }
    return estate_seal(payload)


def validate_estate_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(policy))
    estate_validate_seal(value)
    if value.get("kind") != "earcrate_estate_policy":
        raise ValueError("not an estate policy")
    scan = dict(value.get("scan") or {})
    if scan.get("hash_mode") not in {"none", "evidence", "duplicates", "all"}:
        raise ValueError("invalid estate hash mode")
    actions = dict(value.get("actions") or {})
    for disposition in ESTATE_DISPOSITIONS:
        action = actions.get(disposition)
        if action not in ESTATE_PLAN_ACTIONS:
            raise ValueError(f"missing/invalid action for {disposition}: {action!r}")
    safety = dict(value.get("safety") or {})
    if safety.get("allow_delete"):
        raise ValueError("estate v1 does not permit automatic deletion")
    return value


def load_estate_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object at {path}")
    return value


def write_estate_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def estate_collect_sha256_values(value: Any, *, prefix: str = "") -> list[dict[str, str]]:
    """Collect declared SHA-256 identities from nested JSON without assigning authority."""
    out: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, str) and "sha256" in str(key).lower() and _SHA256_RE.fullmatch(child.lower()):
                out.append({"field": field, "sha256": child.lower()})
            out.extend(estate_collect_sha256_values(child, prefix=field))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            field = f"{prefix}[{index}]"
            out.extend(estate_collect_sha256_values(child, prefix=field))
    return out


def estate_unique_preserve(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


__all__ = [
    "ESTATE_SCHEMA_VERSION",
    "ESTATE_KINDS",
    "ESTATE_HASH_FIELDS",
    "ESTATE_ITEM_CLASSES",
    "ESTATE_DISPOSITIONS",
    "ESTATE_PLAN_ACTIONS",
    "estate_json_bytes",
    "estate_sha256_json",
    "estate_sha256_file",
    "estate_seal",
    "estate_validate_seal",
    "estate_safe_component",
    "estate_normalize_relative_path",
    "estate_ensure_within",
    "estate_root_id",
    "estate_item_id",
    "estate_architecture",
    "default_estate_policy",
    "validate_estate_policy",
    "load_estate_json",
    "write_estate_json",
    "estate_collect_sha256_values",
    "estate_unique_preserve",
]
