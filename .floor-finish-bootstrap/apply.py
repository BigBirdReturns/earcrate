from __future__ import annotations

import base64
import hashlib
import lzma
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bootstrap = Path(__file__).resolve().parent
expected = (bootstrap / "PAYLOAD_SHA256").read_text(encoding="ascii").strip()
encoded = "".join(path.read_text(encoding="ascii") for path in sorted(bootstrap.glob("payload.*")))
compressed = base64.b85decode(encoded.encode("ascii"))
actual = hashlib.sha256(compressed).hexdigest()
if actual != expected:
    raise SystemExit(f"Floor finish payload identity mismatch: expected {expected}, found {actual}")
patch_bytes = lzma.decompress(compressed, format=lzma.FORMAT_XZ)
patch_path = bootstrap / "floor-finish.patch"
patch_path.write_bytes(patch_bytes)
subprocess.run(
    ["patch", "-p1", "--batch", "--forward", "-i", str(patch_path)],
    cwd=root,
    check=True,
)
# Remove every transport/bootstrap artifact. The committed tree is normal source.
for path in (
    root / ".floor-export-trigger",
    root / ".github" / "workflows" / "export-floor-base.yml",
    root / ".github" / "workflows" / "apply-floor-core.yml",
    root / ".github" / "workflows" / "apply-floor-full.yml",
    root / ".github" / "workflows" / "apply-floor-finish.yml",
):
    path.unlink(missing_ok=True)
for path in (
    root / ".floor-bootstrap",
    root / ".floor-full-bootstrap",
    root / ".floor-finish-bootstrap",
):
    shutil.rmtree(path, ignore_errors=True)
print(f"applied Floor finish patch {actual} ({len(patch_bytes)} bytes)")
