"""Identify the code that actually produced a render.

A commit SHA is too coarse. Any later commit -- a changelog line, a packaging fix,
a sealed verdict -- moves the head and would invalidate a render that the change
could not possibly have affected, forcing a re-render to prove something that was
never in doubt. A commit SHA is also, on its own, too weak: it says nothing about
whether the checkout was dirty when the render ran.

So the manifest records a digest over exactly the files that can change the audio.
The preflight recomputes it and compares. The head SHA is retained as context, not
as the predicate.

Identity comes from Git's own blob hashes, not from the bytes sitting on disk.
This checkout runs `core.autocrlf=true`, so Git rewrites LF to CRLF on checkout:
hashing working-tree bytes made the digest change when the very same commit was
checked out again, which is precisely the false alarm the digest exists to avoid.
Blob identity is normalization-independent and platform-independent, and it is the
authoritative answer to "which content is tracked here".
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

# Everything that can alter a rendered candidate: the adapter, its contract, its
# entry points, and the two upstream modules it renders and measures through.
ADAPTER_PATHS: tuple[str, ...] = (
    "earcrate/a1_07_full_form",
    "earcrate/a1_07_gold_v8",
    "earcrate/reference_zero.py",
    "configs/album_one/a1-07/full-form-v1.v1.json",
    "scripts/earcrate_a1_07_full_form_v1.py",
    "scripts/RUN_A1_07_FULL_FORM_V1.ps1",
)


class ProvenanceError(RuntimeError):
    pass


_ENTRY = re.compile(r"^\d{6}\s+([0-9a-f]{40})\s+\d\t(.+)$")


def _tracked_blobs(repo_root: Path) -> list[tuple[str, str]]:
    """(path, blob sha1) for every tracked file under the declared paths."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-s", "--", *ADAPTER_PATHS],
        capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise ProvenanceError(f"git ls-files failed: {result.stderr.strip()[:300]}")
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        match = _ENTRY.match(line.rstrip("\n"))
        if not match:
            continue
        blob, path = match.group(1), match.group(2).strip().strip('"')
        rows.append((path, blob))
    if not rows:
        raise ProvenanceError("no tracked files matched the declared adapter paths")
    return sorted(set(rows))


def adapter_tree_digest(repo_root: Path) -> dict[str, object]:
    """Digest the exact tracked content that can change a rendered candidate.

    Uses Git blob identity, so the digest is invariant under line-ending
    normalization, checkout, and platform. Hashing on-disk bytes is NOT invariant
    under any of those, which made the same commit hash differently after a
    checkout under core.autocrlf.
    """
    rows = _tracked_blobs(Path(repo_root))
    payload = "\n".join(f"{path}:{blob}" for path, blob in rows).encode("utf-8")
    return {
        "algorithm": "sha256 over sorted 'path:git-blob-sha1' lines",
        "identity_source": "git blob hashes (normalization-independent)",
        "member_count": len(rows),
        "declared_paths": list(ADAPTER_PATHS),
        "digest": hashlib.sha256(payload).hexdigest(),
    }
