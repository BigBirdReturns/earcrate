from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent
EXPECTED_OVERLAY_SHA256 = "646c4fffbb904f4ebb0dae3495f6481d8d98e4c9a396c97b16d2aa972398a2ac"
EXPECTED_BASE_COMMIT = "57fa1e05af12387b1825d63be108d6e3c23ab96c"
CLEAN_PARENT_COMMIT = "46411c3fc32e8af17e8b9b9cf32cb3de41fe3837"


def _safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"unsafe overlay path: {name!r}")
    return path


def main() -> None:
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXPECTED_BASE_COMMIT, "HEAD"],
        cwd=ROOT,
        check=True,
    )
    encoded = "".join(path.read_text(encoding="ascii") for path in sorted(BOOTSTRAP.glob("overlay.*.b64")))
    overlay = base64.b64decode("".join(encoded.split()), validate=True)
    actual = hashlib.sha256(overlay).hexdigest()
    if actual != EXPECTED_OVERLAY_SHA256:
        raise SystemExit(f"overlay identity changed: expected {EXPECTED_OVERLAY_SHA256}, found {actual}")

    with tempfile.TemporaryDirectory(prefix="earcrate-production-overlay-") as raw_tmp:
        tmp = Path(raw_tmp)
        archive_path = tmp / "overlay.tar.xz"
        archive_path.write_bytes(overlay)
        extracted = tmp / "extracted"
        extracted.mkdir()
        with tarfile.open(archive_path, mode="r:xz") as archive:
            for member in archive.getmembers():
                relative = _safe_relative(member.name)
                if not member.isfile():
                    raise SystemExit(f"overlay may contain regular files only: {member.name!r}")
                target = (extracted / Path(*relative.parts)).resolve()
                target.relative_to(extracted.resolve())
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit(f"cannot read overlay member: {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                target.chmod(member.mode & 0o777)

        manifest = json.loads((extracted / "PRODUCTION_CLEANUP_MANIFEST.json").read_text(encoding="utf-8"))
        if manifest.get("base_commit") != EXPECTED_BASE_COMMIT or manifest.get("parent_commit") != CLEAN_PARENT_COMMIT:
            raise SystemExit("overlay manifest belongs to another lineage")

        for name in manifest["write"]:
            relative = _safe_relative(name)
            source = extracted / Path(*relative.parts)
            if not source.is_file():
                raise SystemExit(f"overlay omits declared write: {name}")
            target = ROOT / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        for name in manifest["delete"]:
            relative = _safe_relative(name)
            target = ROOT / Path(*relative.parts)
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                raise SystemExit(f"refusing to recursively delete non-file path: {name}")

    for path in (
        ROOT / ".production-cleanup-bootstrap",
        ROOT / ".github" / "workflows" / "apply-production-cleanup.yml",
    ):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    for directory in sorted(ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and directory.name not in {".git"}:
            try:
                directory.rmdir()
            except OSError:
                pass

    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"applied production cleanup overlay {actual}")


if __name__ == "__main__":
    main()
