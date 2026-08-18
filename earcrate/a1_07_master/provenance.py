"""Identify the code that actually produced a master.

Same algorithm as `a1_07_full_form.provenance`, deliberately over a different and
much smaller path set: the mastering stage is bound to the mastering code, and the
render is bound to the render code. Neither digest may move when the other stage
changes, or every commit to one stage would invalidate the other stage's evidence.

The identity source is Git blob hashes, not working-tree bytes, for the reason
recorded in the full-form module: this checkout runs `core.autocrlf=true`, so
on-disk bytes change under a plain checkout and blob identity does not.

The algorithm is duplicated rather than imported because the full-form
implementation hard-codes its own path set. `tests/test_a1_07_master.py` asserts
the two implementations agree on identical inputs, so the duplication cannot
silently drift.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

# Everything that can alter a delivery master: the chain, its entry points, and
# the receipt writer. The full-form package is NOT listed -- it produced the
# source render, which is bound separately by canonical PCM identity.
MASTER_PATHS: tuple[str, ...] = (
    "earcrate/a1_07_master",
    "scripts/earcrate_a1_07_master_v1.py",
)


class MasterProvenanceError(RuntimeError):
    pass


_ENTRY = re.compile(r"^\d{6}\s+([0-9a-f]{40})\s+\d\t(.+)$")


def tracked_blobs(repo_root: Path, paths: tuple[str, ...]) -> list[tuple[str, str]]:
    """(path, blob sha1) for every tracked file under `paths`."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-s", "--", *paths],
        capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise MasterProvenanceError(f"git ls-files failed: {result.stderr.strip()[:300]}")
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        match = _ENTRY.match(line.rstrip("\n"))
        if not match:
            continue
        blob, path = match.group(1), match.group(2).strip().strip('"')
        rows.append((path, blob))
    if not rows:
        raise MasterProvenanceError("no tracked files matched the declared master paths")
    return sorted(set(rows))


def tree_digest(repo_root: Path, paths: tuple[str, ...]) -> dict[str, object]:
    rows = tracked_blobs(Path(repo_root), paths)
    payload = "\n".join(f"{path}:{blob}" for path, blob in rows).encode("utf-8")
    return {
        "algorithm": "sha256 over sorted 'path:git-blob-sha1' lines",
        "identity_source": "git blob hashes (normalization-independent)",
        "member_count": len(rows),
        "declared_paths": list(paths),
        "digest": hashlib.sha256(payload).hexdigest(),
    }


def master_tree_digest(repo_root: Path) -> dict[str, object]:
    """Digest the exact tracked content that can change a delivery master."""
    return tree_digest(repo_root, MASTER_PATHS)
