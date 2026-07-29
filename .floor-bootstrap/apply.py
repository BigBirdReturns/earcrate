from __future__ import annotations

import base64
import hashlib
import io
import shutil
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bootstrap = Path(__file__).resolve().parent
expected = (bootstrap / "PAYLOAD_SHA256").read_text(encoding="ascii").strip()
encoded = "".join(path.read_text(encoding="ascii") for path in sorted(bootstrap.glob("payload.*")))
raw = base64.b85decode(encoded.encode("ascii"))
if hashlib.sha256(raw).hexdigest() != expected:
    raise SystemExit("Floor core payload identity mismatch")
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    for info in archive.infolist():
        path = Path(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe Floor core payload path: {info.filename}")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))
shutil.rmtree(bootstrap)
(root / ".github" / "workflows" / "apply-floor-core.yml").unlink(missing_ok=True)
