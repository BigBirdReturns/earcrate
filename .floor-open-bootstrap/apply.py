from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    expected_payload = (BOOTSTRAP / "PAYLOAD_SHA256").read_text(encoding="ascii").strip()
    encoded = "".join(path.read_text(encoding="ascii") for path in sorted(BOOTSTRAP.glob("payload.*")))
    compressed = base64.b85decode(encoded.encode("ascii"))
    actual_payload = hashlib.sha256(compressed).hexdigest()
    if actual_payload != expected_payload:
        raise SystemExit(f"Floor payload identity mismatch: expected {expected_payload}, found {actual_payload}")
    value = json.loads(zlib.decompress(compressed))
    for rel, expected in sorted(value["expected"].items()):
        path = ROOT / rel
        if not path.is_file():
            raise SystemExit(f"Floor integration expected existing file: {rel}")
        actual = sha(path)
        if actual != expected:
            raise SystemExit(f"Floor integration base drift for {rel}: expected {expected}, found {actual}")
    for rel in value["absent"]:
        path = ROOT / rel
        if path.exists():
            raise SystemExit(f"Floor integration expected new path to be absent: {rel}")
    for rel, encoded_file in sorted(value["files"].items()):
        atomic_write(ROOT / rel, base64.b64decode(encoded_file))
    for rel in value["deleted"]:
        (ROOT / rel).unlink(missing_ok=True)
    for directory in (
        ROOT / ".floor-finish-bootstrap",
        ROOT / ".floor-full-bootstrap",
        ROOT / ".floor-bootstrap",
        ROOT / ".floor-open-bootstrap",
    ):
        shutil.rmtree(directory, ignore_errors=True)
    for workflow in (
        ROOT / ".github" / "workflows" / "apply-open-music-floor.yml",
        ROOT / ".github" / "workflows" / "apply-floor-core.yml",
        ROOT / ".github" / "workflows" / "apply-floor-full.yml",
        ROOT / ".github" / "workflows" / "apply-floor-finish.yml",
    ):
        workflow.unlink(missing_ok=True)
    print(f"materialized {len(value['files'])} Floor files; removed {len(value['deleted'])} bootstrap paths")


if __name__ == "__main__":
    main()
