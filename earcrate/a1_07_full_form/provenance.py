"""Identify the code that actually produced a render.

A commit SHA is too coarse. Any later commit -- a changelog line, a packaging fix,
a sealed verdict -- moves the head and would invalidate a render that the change
could not possibly have affected, forcing a re-render to prove something that was
never in doubt. A commit SHA is also, on its own, too weak: it says nothing about
whether the checkout was dirty when the render ran.

So the manifest records a digest over exactly the files that can change the audio.
The preflight recomputes it and compares. The head SHA is retained as context, not
as the predicate.
"""

from __future__ import annotations

import hashlib
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


def _members(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for entry in ADAPTER_PATHS:
        target = repo_root / entry
        if target.is_dir():
            found.extend(p for p in target.rglob("*.py") if "__pycache__" not in p.parts)
        elif target.is_file():
            found.append(target)
    return sorted(set(found))


def adapter_tree_digest(repo_root: Path) -> dict[str, object]:
    """Digest the exact file set that can change a rendered candidate."""
    repo_root = Path(repo_root)
    rows: list[tuple[str, str]] = []
    for path in _members(repo_root):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append((path.relative_to(repo_root).as_posix(), digest))
    payload = "\n".join(f"{name}:{digest}" for name, digest in rows).encode("utf-8")
    return {
        "algorithm": "sha256 over sorted 'relpath:sha256' lines",
        "member_count": len(rows),
        "declared_paths": list(ADAPTER_PATHS),
        "digest": hashlib.sha256(payload).hexdigest(),
    }
