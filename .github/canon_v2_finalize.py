#!/usr/bin/env python3
"""Finalize canon v2 from three Git-blob-backed base64 parts."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = [
    ROOT / ".github" / "canon-v2-ledger.part0.b64",
    ROOT / ".github" / "canon-v2-ledger.part1.b64",
    ROOT / ".github" / "canon-v2-ledger.part2.b64",
]
OUTPUT = ROOT / "docs" / "canon" / "canon-ledger.v2.json"
EXPECTED_FILE_SHA256 = "23f11e93d36e111b332965ad2e82f474315ab0a4bf5acb0188508eca13e0eaca"
EXPECTED_LEDGER_SHA256 = "a8c0ab71fafcc8d2bbdf9b3c0310a67992fdd6362eca9b4cc06c4f68bca89f55"


def canonical_sha256(value: dict) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    fragments = ["".join(path.read_text(encoding="ascii").split()) for path in PARTS]
    encoded = "".join(fragments)
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    print(f"LEDGER_TRANSPORT part_lengths={[len(item) for item in fragments]} total={len(encoded)} padding={len(padding)}")
    raw = base64.b64decode(encoded + padding, validate=True)
    measured = hashlib.sha256(raw).hexdigest()
    print(f"LEDGER_DECODE bytes={len(raw)} sha256={measured}")
    if measured != EXPECTED_FILE_SHA256:
        raise RuntimeError(f"canon v2 file hash mismatch: {measured}")

    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("canon v2 payload is not an object")
    claimed = value.pop("ledger_sha256")
    effective = canonical_sha256(value)
    if claimed != EXPECTED_LEDGER_SHA256 or effective != EXPECTED_LEDGER_SHA256:
        raise RuntimeError(f"canon v2 self-hash mismatch: claimed={claimed} measured={effective}")
    value["ledger_sha256"] = claimed

    prs = value.get("pull_requests") or []
    branches = value.get("branch_retention") or []
    issues = ((value.get("campaign_fanout") or {}).get("issues") or [])
    if [row.get("pr") for row in prs] != list(range(1, 57)):
        raise RuntimeError("canon v2 PR coverage is not exactly 1 through 56")
    if len(branches) != 13:
        raise RuntimeError("canon v2 branch map is not exactly 13 entries")
    if [row.get("issue") for row in issues] != list(range(58, 69)):
        raise RuntimeError("canon v2 issue fan-out is not exactly 58 through 68")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(raw)
    print(f"FINALIZED {OUTPUT.relative_to(ROOT)} {measured}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
