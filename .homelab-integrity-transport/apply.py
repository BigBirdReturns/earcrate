#!/usr/bin/env python3
from __future__ import annotations

"""Verify and apply the temporary Homelab integrity source overlay."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

ARCHIVE_SHA256 = "af1de518de5a5ed50da56c1d2b6f94850f158397c6e5273a100f36af4d22e468"
CHUNK_COUNT = 8
EXPECTED_FILES = {
    "earcrate/estate/homelab.py": "1089d481b07343b2ebf1f99862389848b5b2464f460df2ffaa823d6a8409ba0e",
    "earcrate/estate/homelab_review.py": "49bf08675db30fea87848c48a2c75499a988930aaa6c45fd423b25409c9c0a23",
    "earcrate/estate/homelab_common.py": "7da4392ee85cbc43a93c20fadacbdc01c80421476e07cc660bd8c199d7736f19",
    "earcrate/estate/homelab_cli.py": "0a2d1dd016ab4e8dbcbac9bba6106260575c78447222b99a401394b6d1bc9fd2",
    "earcrate/estate/markers.py": "7136285fe76f311657eb648f3dea04390f523b20e5b2d2455e26fe74e6ef6eea",
    "earcrate/estate/__init__.py": "3aca68b4851f831588283c91f41dbd5a1927d6e5ea26687515a8551c823fbe62",
    "schemas/earcrate_homelab_v1.schema.json": "73758b35e9b5dd88a3ea1f075cd78b2bdfb0746d175b1cfdb7c70f093c9fd028",
    "tests/test_homelab_integrity.py": "29a8b937fe0cf5fd4bae62ba236cb9bb8b41a35d59a01d51d65fc59f25bf9cbe",
}
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name or _DRIVE_PREFIX.match(name):
        raise ValueError(f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe archive member: {name!r}")
    return path


def _load_archive(chunks_dir: Path) -> bytes:
    chunks = [chunks_dir / f"overlay.{index:02d}.b64" for index in range(CHUNK_COUNT)]
    missing = [path.name for path in chunks if not path.is_file()]
    if missing:
        raise ValueError("missing overlay chunks: " + ", ".join(missing))
    encoded = "".join("".join(path.read_text(encoding="ascii").split()) for path in chunks)
    archive = base64.b64decode(encoded, validate=True)
    digest = _sha256(archive)
    if digest != ARCHIVE_SHA256:
        raise ValueError(f"overlay archive SHA-256 mismatch: expected {ARCHIVE_SHA256}, got {digest}")
    return archive


def _read_verified_members(archive: bytes) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    with zipfile.ZipFile(__import__("io").BytesIO(archive), "r") as bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member refused")
        if set(names) != set(EXPECTED_FILES):
            missing = sorted(set(EXPECTED_FILES) - set(names))
            extra = sorted(set(names) - set(EXPECTED_FILES))
            raise ValueError(f"overlay member set mismatch; missing={missing}, extra={extra}")
        for info in infos:
            path = _safe_member(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or info.is_dir():
                raise ValueError(f"non-regular archive member refused: {info.filename}")
            data = bundle.read(info)
            expected = EXPECTED_FILES[path.as_posix()]
            actual = _sha256(data)
            if actual != expected:
                raise ValueError(f"member SHA-256 mismatch for {path}: expected {expected}, got {actual}")
            values[path.as_posix()] = data
    return values


def _git_head(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise ValueError(f"cannot read repository HEAD: {process.stderr.strip()}")
    return process.stdout.strip()


def apply_overlay(chunks_dir: Path, repo: Path, expected_head: str | None) -> dict[str, object]:
    chunks_dir = chunks_dir.expanduser().resolve()
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"repository directory missing: {repo}")
    current_head = _git_head(repo)
    if expected_head and current_head != expected_head:
        raise ValueError(f"repository HEAD mismatch: expected {expected_head}, got {current_head}")

    archive = _load_archive(chunks_dir)
    members = _read_verified_members(archive)
    staged: list[tuple[Path, Path]] = []
    replaced: list[str] = []
    try:
        for relative, data in sorted(members.items()):
            target = repo / PurePosixPath(relative)
            resolved_parent = target.parent.resolve()
            if resolved_parent != repo and repo not in resolved_parent.parents:
                raise ValueError(f"target escaped repository: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.overlay-{os.getpid()}-{hashlib.sha256(relative.encode()).hexdigest()[:12]}")
            if temporary.exists():
                raise FileExistsError(temporary)
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if _sha256(temporary.read_bytes()) != EXPECTED_FILES[relative]:
                raise ValueError(f"staged file changed: {relative}")
            staged.append((temporary, target))

        for temporary, target in staged:
            os.replace(temporary, target)
            _fsync_directory(target.parent)
            replaced.append(target.relative_to(repo).as_posix())

        for relative, expected in EXPECTED_FILES.items():
            target = repo / PurePosixPath(relative)
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"materialized target is missing or unsafe: {relative}")
            actual = _sha256(target.read_bytes())
            if actual != expected:
                raise ValueError(f"materialized target SHA-256 mismatch: {relative}")
        json.loads((repo / "schemas/earcrate_homelab_v1.schema.json").read_text(encoding="utf-8"))
    finally:
        for temporary, _target in staged:
            if temporary.exists():
                temporary.unlink()

    result = {
        "ok": True,
        "archive_sha256": ARCHIVE_SHA256,
        "base_head": current_head,
        "files": sorted(replaced),
        "file_sha256": dict(sorted(EXPECTED_FILES.items())),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    apply_overlay(Path(args.chunks), Path(args.repo), args.expected_head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
