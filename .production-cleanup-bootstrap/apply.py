from __future__ import annotations

import base64
import hashlib
import lzma
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent
EXPECTED_PATCH_SHA256 = "11f22b49326a042df54b8945b57d68df0dc886b80a5ae5fbe4ea3570de218271"
EXPECTED_BASE_COMMIT = "57fa1e05af12387b1825d63be108d6e3c23ab96c"
CLEAN_PARENT_COMMIT = "46411c3fc32e8af17e8b9b9cf32cb3de41fe3837"
EXPECTED_TREE_SHA = "80dbddd4884f931ca2a1c86d3990487e1d65db5b"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def main() -> None:
    _run("git", "merge-base", "--is-ancestor", EXPECTED_BASE_COMMIT, "HEAD")

    parts = sorted(BOOTSTRAP.glob("patchpart.*.b64"))
    if parts:
        encoded = "".join(path.read_text(encoding="ascii") for path in parts)
        source_label = ",".join(path.name for path in parts)
    else:
        encoded = (BOOTSTRAP / "final.patch.xz.b64").read_text(encoding="ascii")
        source_label = "final.patch.xz.b64"
    normalized = "".join(encoded.split())
    print(f"patch transport source: {source_label}", flush=True)
    print(f"patch transport characters: {len(normalized)}", flush=True)
    print(
        f"patch transport text sha256: {hashlib.sha256(normalized.encode('ascii')).hexdigest()}",
        flush=True,
    )
    print(f"patch transport tail: {normalized[-96:]}", flush=True)

    compressed = base64.b64decode(normalized, validate=True)
    actual = hashlib.sha256(compressed).hexdigest()
    print(f"decoded patch bytes: {len(compressed)}", flush=True)
    if actual != EXPECTED_PATCH_SHA256:
        raise SystemExit(
            f"production patch identity changed: expected {EXPECTED_PATCH_SHA256}, found {actual}"
        )

    patch = lzma.decompress(compressed, format=lzma.FORMAT_XZ)
    with tempfile.NamedTemporaryFile(
        prefix="earcrate-production-cleanup-", suffix=".patch", delete=False
    ) as handle:
        patch_path = Path(handle.name)
        handle.write(patch)
    try:
        _run(
            "git",
            "apply",
            "--index",
            "--whitespace=error-all",
            str(patch_path),
        )
    finally:
        patch_path.unlink(missing_ok=True)

    image = ROOT / "PXL_20260709_201156075.MP.jpg"
    if image.is_symlink() or image.is_file():
        image.unlink()
    elif image.exists():
        raise SystemExit(f"refusing non-file cleanup target: {image}")

    shutil.rmtree(BOOTSTRAP, ignore_errors=False)
    (ROOT / ".github" / "workflows" / "apply-production-cleanup.yml").unlink(
        missing_ok=True
    )

    _run("git", "add", "-A")
    _run("git", "diff", "--cached", "--check")
    tree = _run("git", "write-tree").stdout.strip()
    if tree != EXPECTED_TREE_SHA:
        raise SystemExit(
            f"production tree mismatch: expected {EXPECTED_TREE_SHA}, found {tree}"
        )

    print(f"applied production cleanup patch {actual}")
    print(f"verified production tree {tree}")
    print(f"clean parent will be {CLEAN_PARENT_COMMIT}")


if __name__ == "__main__":
    main()
