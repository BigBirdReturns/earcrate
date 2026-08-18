"""Content identity and seals, in one place.

The algorithm is deliberately re-implemented here rather than imported from
`a1_07_gold_v8.common`. That module sits inside the render provenance path set, so
editing it -- even to move a function -- would move the digest that identifies the
code which produced A1-07's accepted render, and invalidate a manifest the change
could not have affected. The accepted lineage stays frozen; the shared spine gets
its own home.

`tests/test_evidence_spine.py` asserts the two implementations agree byte for byte
on the same payloads, so the duplication cannot drift. This is the same cross-check
that keeps the two provenance digests honest.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class IdentityError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    """The one serialization every seal in this repository is computed over."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seal(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    """Return a copy carrying its own digest under `field`."""
    value = deepcopy(dict(payload))
    value.pop(field, None)
    value[field] = sha256_bytes(canonical_json_bytes(value))
    return value


def validate_seal(payload: Mapping[str, Any], field: str) -> str:
    claimed = str(payload.get(field) or "").lower()
    if not HEX64.fullmatch(claimed):
        raise IdentityError(f"missing or invalid {field}")
    observed = seal(payload, field)[field]
    if observed != claimed:
        raise IdentityError(f"{field} mismatch: declared {claimed}, observed {observed}")
    return claimed


@dataclass(frozen=True)
class ObjectIdentity:
    """What it means to name an audio object without shipping it.

    A container digest alone is not enough: two containers can hold the same audio
    and two decodes of one container must agree. A canonical PCM digest alone is not
    enough either, because a delivered object is a file. Decisions in this repository
    bind both, which is why a verdict naming only one of them is refused.
    """

    canonical_pcm_sha256: str
    container_sha256: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if not HEX64.fullmatch(self.canonical_pcm_sha256 or ""):
            raise IdentityError(f"not a canonical PCM digest: {self.canonical_pcm_sha256!r}")
        if self.container_sha256 is not None and not HEX64.fullmatch(self.container_sha256):
            raise IdentityError(f"not a container digest: {self.container_sha256!r}")

    def matches(self, other: "ObjectIdentity", *, require_container: bool = True) -> bool:
        if self.canonical_pcm_sha256 != other.canonical_pcm_sha256:
            return False
        if not require_container:
            return True
        if self.container_sha256 is None or other.container_sha256 is None:
            return False
        return self.container_sha256 == other.container_sha256

    def describe(self) -> str:
        container = (self.container_sha256 or "")[:12] or "no container"
        return f"{self.canonical_pcm_sha256[:12]} ({container})"
