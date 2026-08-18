"""Loading a landed receipt, and refusing one that is not what it claims.

Four sealed public receipts exist in `proofs/album_one/` and they were written four
separate times, each with its own hand-rolled boundary block. Two of them landed
with pointers that no longer resolved. This module is the single place that knows
how to read one safely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .identity import IdentityError, validate_seal

# The seal field a receipt carries, in the order we look for it. Different kinds of
# evidence name their digest differently; all of them seal the same way.
SEAL_FIELDS = ("receipt_sha256", "addendum_sha256", "manifest_sha256",
               "master_manifest_sha256", "verdict_sha256", "proof_sha256",
               "projection_sha256")

# A public receipt carries mechanism and identity. Anything here means it is
# carrying a body, a location, or a credential instead.
FORBIDDEN_SUBSTRINGS = ("\\Projects\\", "/Projects/", "private-custody", "C:\\", "D:\\",
                        "S:\\", ".wav", ".flac", ".zip")


class EvidenceError(RuntimeError):
    pass


def seal_field(value: Mapping[str, Any]) -> str:
    for field in SEAL_FIELDS:
        if field in value:
            return field
    raise EvidenceError(f"no seal field on {value.get('kind') or 'receipt'}")


def load_sealed(path: Path, *, kind: str | None = None,
                field: str | None = None) -> dict[str, Any]:
    """Read a receipt, prove it still matches its own seal, and check its kind."""
    path = Path(path)
    if not path.is_file():
        raise EvidenceError(f"receipt does not exist: {path}")
    try:
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - the reason matters more than the type
        raise EvidenceError(f"receipt is unreadable: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"receipt is not a JSON object: {path}")
    if kind is not None and value.get("kind") != kind:
        raise EvidenceError(
            f"receipt kind is {value.get('kind')!r}, expected {kind!r}: {path.name}")
    try:
        validate_seal(value, field or seal_field(value))
    except IdentityError as exc:
        raise EvidenceError(f"{path.name}: {exc}") from exc
    return value


def verify_body_free(receipt: Mapping[str, Any]) -> list[str]:
    """Findings, not a boolean: a leak should say what leaked and where.

    Walks keys and string values rather than grepping the serialized blob, because
    receipt prose legitimately contains words like "executions" and a substring match
    over prose fails for the wrong reason.
    """
    findings: list[str] = []

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("artifact_path", "path", "executions", "local_path"):
                    findings.append(f"private field at {path}/{key}")
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for needle in FORBIDDEN_SUBSTRINGS:
                if needle in node:
                    findings.append(f"{needle!r} at {path}")

    walk(dict(receipt))
    boundary = receipt.get("boundary") or {}
    for flag in ("private_paths_included", "source_audio_exported"):
        if boundary.get(flag):
            findings.append(f"boundary declares {flag} true")
    return findings
