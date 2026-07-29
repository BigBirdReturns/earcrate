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
actual = hashlib.sha256(raw).hexdigest()
if actual != expected:
    raise SystemExit(f"Floor payload identity mismatch: expected {expected}, found {actual}")

seen: set[str] = set()
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    for info in archive.infolist():
        path = Path(info.filename)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise SystemExit(f"unsafe Floor payload path: {info.filename}")
        normalized = path.as_posix()
        if normalized in seen:
            raise SystemExit(f"duplicate Floor payload path: {normalized}")
        seen.add(normalized)
        target = (root / path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise SystemExit(f"Floor payload escaped repository: {info.filename}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))

# Remove both source-export/bootstrap mechanisms. The resulting review tree is
# ordinary source, schemas, tests, docs, and workflows.
(root / ".floor-export-trigger").unlink(missing_ok=True)
(root / ".github" / "workflows" / "export-floor-base.yml").unlink(missing_ok=True)
(root / ".github" / "workflows" / "apply-floor-core.yml").unlink(missing_ok=True)
shutil.rmtree(root / ".floor-bootstrap", ignore_errors=True)
shutil.rmtree(bootstrap, ignore_errors=True)
(root / ".github" / "workflows" / "apply-floor-full.yml").unlink(missing_ok=True)
print(f"materialized {len(seen)} Floor files from {actual}")
