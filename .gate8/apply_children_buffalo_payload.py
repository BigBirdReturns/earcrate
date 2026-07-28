#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import lzma
import shutil
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CHUNK_ROOT = ROOT / ".gate8" / "children_buffalo_payload"
ARCHIVE_SHA256 = "1c5c9494a8d794599555094e6aa3d5b1e5af443e75577d57a276849bfc6c0a8f"
PARTS = tuple(f"part{index:02d}" for index in range(6))
EXPECTED_FILES = 25
FORBIDDEN_MEDIA_SUFFIXES = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mid", ".midi",
    ".mp3", ".ogg", ".opus", ".pdf", ".wav", ".wma",
}


def _safe_member_path(name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise SystemExit(f"unsafe payload path: {name!r}")
    target = ROOT.joinpath(*pure.parts)
    resolved = target.resolve()
    root_resolved = ROOT.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SystemExit(f"payload path escapes repository: {name!r}") from exc
    if pure.parts[0] == ".git":
        raise SystemExit("payload may not write .git")
    return target


def main() -> int:
    missing = [name for name in PARTS if not (CHUNK_ROOT / name).is_file()]
    if missing:
        raise SystemExit(f"missing payload chunks: {missing}")
    archive = b"".join((CHUNK_ROOT / name).read_bytes() for name in PARTS)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise SystemExit(f"payload archive SHA-256 mismatch: {digest}")
    try:
        tar_bytes = lzma.decompress(archive)
    except lzma.LZMAError as exc:
        raise SystemExit("payload archive is not valid xz") from exc

    files_written: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as bundle:
        members = bundle.getmembers()
        regular = [member for member in members if member.isfile()]
        if len(regular) != EXPECTED_FILES:
            raise SystemExit(f"payload file count mismatch: {len(regular)} != {EXPECTED_FILES}")
        for member in members:
            if not (member.isdir() or member.isfile()):
                raise SystemExit(f"payload contains unsupported member type: {member.name}")
            target = _safe_member_path(member.name)
            if target.suffix.lower() in FORBIDDEN_MEDIA_SUFFIXES:
                raise SystemExit(f"source media may not enter the repository: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"cannot read payload member: {member.name}")
            data = source.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.children-buffalo.tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
            files_written.append(member.name)

    shutil.rmtree(CHUNK_ROOT)
    for ephemeral in (
        ROOT / ".gate8" / "apply_children_buffalo_payload.py",
        ROOT / ".github" / "workflows" / "apply-children-buffalo-gate.yml",
    ):
        ephemeral.unlink(missing_ok=True)

    print(
        f"applied {len(files_written)} Children Buffalo Gate files "
        f"from archive {ARCHIVE_SHA256[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
