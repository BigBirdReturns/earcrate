from __future__ import annotations

import base64
import hashlib
import io
import lzma
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent
EXPECTED = (BOOTSTRAP / "PAYLOAD_SHA256").read_text(encoding="ascii").strip()
ENCODED = "".join(path.read_text(encoding="ascii") for path in sorted(BOOTSTRAP.glob("payload.*")))
COMPRESSED = base64.b85decode(ENCODED.encode("ascii"))
ACTUAL = hashlib.sha256(COMPRESSED).hexdigest()
if ACTUAL != EXPECTED:
    raise SystemExit(f"release-candidate payload identity mismatch: expected {EXPECTED}, found {ACTUAL}")
RAW = lzma.decompress(COMPRESSED, format=lzma.FORMAT_XZ)

with tarfile.open(fileobj=io.BytesIO(RAW), mode="r:") as archive:
    members = archive.getmembers()
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise SystemExit(f"unsafe release-candidate payload path: {member.name!r}")
        if member.issym() or member.islnk() or not member.isfile():
            raise SystemExit(f"unsupported release-candidate payload member: {member.name!r}")
        target = ROOT.joinpath(*pure.parts).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise SystemExit(f"release-candidate payload escapes repository: {member.name!r}") from exc
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"cannot read release-candidate payload member: {member.name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read())
        target.chmod(member.mode)

fixture = ROOT / "proofs" / "specimens" / "pretty_lights_empire_release_candidate_v1"
for suffix in (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"):
    if any(fixture.rglob(f"*{suffix}")):
        raise SystemExit(f"source or delivery media entered committed fixture: {suffix}")

commands = [
    ["python", "-m", "compileall", "-q", "earcrate/floor", "tests/test_floor_release_gate.py"],
    ["python", "-m", "pytest", "-q", "tests/test_floor_release_gate.py", "tests/test_floor_protocol.py"],
    ["python", "build/make_singlefile.py"],
    ["python", "-m", "earcrate", "floor", "release-capability"],
    ["python", "dist/earcrate.py", "floor", "release-capability"],
    ["python", "dist/earcrate.py", "floor", "verify", str(fixture / "release_gate.pending.json")],
    ["python", "scripts/oss_audit.py"],
    ["python", "VERIFY_PACKAGE.py", "--skip-gates"],
]
for command in commands:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

# Leave an ordinary review tree. The one-shot transport must not survive the commit.
(ROOT / ".github" / "workflows" / "export-release-floor-base.yml").unlink(missing_ok=True)
shutil.rmtree(BOOTSTRAP, ignore_errors=True)
subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
subprocess.run(["git", "config", "user.name", "EarCrate release-gate integration"], cwd=ROOT, check=True)
subprocess.run(["git", "config", "user.email", "noreply@github.com"], cwd=ROOT, check=True)
subprocess.run(["git", "commit", "-m", "Add reviewed release-candidate gate"], cwd=ROOT, check=True)
subprocess.run(["git", "push", "origin", "HEAD:agent/release-candidate-review-gate"], cwd=ROOT, check=True)
print(f"applied reviewed release-candidate payload {ACTUAL} ({len(RAW)} bytes)")
