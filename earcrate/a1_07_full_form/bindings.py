"""Content-addressed rebinding of the A1-07 sources to their current custody.

The gold-v7 bindings still name `S:\\Temp\\EarCrate\\...`, the pre-migration
volume prefix. The cold-volume migration moved those exact bytes without changing
them, so the historical receipts point at paths that no longer resolve while every
declared identity still holds. Rebinding by container identity — never by rewriting
a path pattern — is what makes the old evidence executable again, and it fails
closed if any byte actually differs.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping
import zipfile

from .. import reference_zero as rz
from ..a1_07_gold_v8 import common as c
from .contract import FullFormError

AUDIO_SUFFIXES = c.AUDIO_SUFFIXES


def materialize_from_archive(
    archive: Path,
    destination: Path,
    wanted: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Extract exactly the wanted objects from the verified CORE archive.

    The archive is the private store of record; the renderer needs loose files.
    Extraction is by declared identity, never by member name, and every extracted
    object is re-hashed on disk before it is allowed to bind. Nothing outside
    `wanted` is written, so the private store is not unpacked wholesale.
    """
    destination.mkdir(parents=True, exist_ok=True)
    by_digest = {str(v).lower(): k for k, v in wanted.items()}
    rows: list[dict[str, Any]] = []
    resolved: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            suffix = Path(info.filename).suffix.lower()
            if suffix not in AUDIO_SUFFIXES:
                continue
            with bundle.open(info) as handle:
                payload = handle.read()
            digest = c.sha256_bytes(payload)
            name = by_digest.get(digest)
            if name is None:
                continue
            target = destination / f"{name}{suffix}"
            if target.exists() and c.sha256_file(target) == digest:
                rows.append({"source_id": name, "path": str(target), "container_sha256": digest,
                             "action": "already_present_verified"})
                resolved.add(digest)
                continue
            partial = target.with_suffix(target.suffix + ".partial")
            partial.write_bytes(payload)
            if c.sha256_file(partial) != digest:
                partial.unlink(missing_ok=True)
                raise FullFormError(f"extraction verification failed for {name}")
            partial.replace(target)
            rows.append({"source_id": name, "path": str(target), "container_sha256": digest,
                         "archive_member": info.filename, "action": "extracted_verified"})
            resolved.add(digest)
    missing = sorted(name for digest, name in by_digest.items() if digest not in resolved)
    if missing:
        raise FullFormError(f"CORE archive does not carry required sources: {missing}")
    return rows


def index_custody(roots: Iterable[Path]) -> dict[str, Path]:
    """Map container SHA-256 -> path for every audio object in the custody roots."""
    index: dict[str, Path] = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                digest = c.sha256_file(path)
                # First writer wins, so a deterministic walk yields a deterministic bind.
                index.setdefault(digest, path)
    return index


def rebind(
    bindings: Mapping[str, Any],
    custody_index: Mapping[str, Path],
    *,
    score: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repoint every binding at the object whose bytes match its declared identity."""
    value = deepcopy(dict(bindings))
    value.pop("bindings_sha256", None)
    moves: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for row in value.get("bindings") or []:
        row = dict(row)
        declared = str(row.get("container_sha256") or "").lower()
        if not c.HEX64.fullmatch(declared):
            raise FullFormError(f"binding {row.get('source_id')} has no container identity")
        found = custody_index.get(declared)
        if found is None:
            raise FullFormError(
                f"no object in current custody carries the declared identity for "
                f"{row.get('source_id')}: {declared}")
        observed = c.sha256_file(found)
        if observed != declared:
            raise FullFormError(f"custody index is stale for {row.get('source_id')}")
        previous = row.get("artifact_path")
        row["artifact_path"] = str(found)
        row["bytes"] = found.stat().st_size
        rows.append(row)
        moves.append({
            "source_id": row.get("source_id"),
            "container_sha256": declared,
            "previous_artifact_path": previous,
            "current_artifact_path": str(found),
            "rebound": str(previous) != str(found),
            "identity_reverified": True,
        })
    value["bindings"] = rows
    if score is not None:
        value["score_sha256"] = score["score_sha256"]
    result = rz.seal(value)
    if score is not None:
        rz.validate_source_bindings(result, score)
    return result, moves
