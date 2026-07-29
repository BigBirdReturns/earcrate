from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent
EXPECTED = (BOOTSTRAP / "PAYLOAD_SHA256").read_text(encoding="ascii").strip().split()[0]
ENCODED = "".join(path.read_text(encoding="ascii") for path in sorted(BOOTSTRAP.glob("payload.*")))
PAYLOAD = base64.b85decode(ENCODED.encode("ascii"))
ACTUAL = hashlib.sha256(PAYLOAD).hexdigest()
if ACTUAL != EXPECTED:
    raise SystemExit(f"release Floor payload identity mismatch: expected {EXPECTED}, found {ACTUAL}")

with tarfile.open(fileobj=io.BytesIO(PAYLOAD), mode="r:gz") as archive:
    members = archive.getmembers()
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or any(part in {"", ".."} for part in pure.parts):
            raise SystemExit(f"unsafe release Floor payload path: {member.name!r}")
        if member.issym() or member.islnk():
            raise SystemExit(f"release Floor payload may not contain links: {member.name!r}")
    archive.extractall(ROOT, filter="data")

# Leave a normal review tree after the workflow commits the extracted sources.
shutil.rmtree(BOOTSTRAP, ignore_errors=True)
(ROOT / ".github" / "workflows" / "apply-release-floor.yml").unlink(missing_ok=True)
print(f"applied release Floor payload {ACTUAL} ({len(PAYLOAD)} bytes, {len(members)} members)")
