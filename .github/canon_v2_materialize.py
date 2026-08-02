#!/usr/bin/env python3
"""Materialize the reviewed canon-v2 payload from issue #68 transport comments."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
import zlib

REPOSITORY = "BigBirdReturns/earcrate"
ISSUE = 68
PARTS = 5
PAYLOAD_LENGTH = 36424
PAYLOAD_SHA256 = "44a3c3d21c5f76475dea0bd91aa2cb9dc740187ea9795233c733e882a919dc6c"
EXPECTED_FILES = {
    ".github/workflows/gates.yml": "dad5d8acbf1d90e434bab0864f96abd0c028a20c13f3f1b40b69a273b5f4f8d5",
    "docs/BRANCH_RETENTION_MAP.md": "10e2c7b30b997b51ed5303c16f91fe10a41a6a8f81d4f31c8b9e73ed9dd2c7af",
    "docs/CANON_AND_CAMPAIGN_V2.md": "24e8b1bab46e9c0de0e6e63546fd618e3898fd11028d3d71cd88a6c116615ef7",
    "docs/CANON_AND_NONLANDING_LEDGER.md": "fb0f3942f4e5f5a26e4f9c4565051c47c49b699890396946e39c0d045bdec0ce",
    "docs/canon/canon-ledger.v2.json": "23f11e93d36e111b332965ad2e82f474315ab0a4bf5acb0188508eca13e0eaca",
    "schemas/earcrate_canon_and_nonlanding_ledger_v2.schema.json": "107970da1bdb263b0f91442de79e3a87108bb5d2f68c5e2f0325a5eef25c4244",
    "tests/test_canon_ledger_v2.py": "726dde1ac1f422de50b67dab3181f8af6a454b0a2caf7477ecf00b77ba38fcd4",
}


def _comments() -> list[dict]:
    token = os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE}/comments?per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "earcrate-canon-v2-materializer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, list):
        raise RuntimeError("issue comments response is not a list")
    return value


def main() -> int:
    marker = re.compile(r"<!-- CANON_V2_PAYLOAD ([1-5])/5 -->")
    block = re.compile(r"```text\s*([A-Za-z0-9+/=\s]+?)\s*```", re.DOTALL)
    parts: dict[int, str] = {}
    for comment in _comments():
        body = str(comment.get("body") or "")
        match = marker.search(body)
        if not match:
            continue
        number = int(match.group(1))
        payload_match = block.search(body)
        if payload_match is None:
            raise RuntimeError(f"payload comment {number} has no text block")
        payload = "".join(payload_match.group(1).split())
        if number in parts:
            raise RuntimeError(f"duplicate payload part {number}")
        parts[number] = payload

    if sorted(parts) != list(range(1, PARTS + 1)):
        raise RuntimeError(f"incomplete payload parts: {sorted(parts)}")
    encoded = "".join(parts[number] for number in range(1, PARTS + 1))
    if len(encoded) != PAYLOAD_LENGTH:
        raise RuntimeError(f"payload length mismatch: {len(encoded)}")
    measured = hashlib.sha256(encoded.encode("ascii")).hexdigest()
    if measured != PAYLOAD_SHA256:
        raise RuntimeError(f"payload hash mismatch: {measured}")

    packed = base64.b64decode(encoded, validate=True)
    data = json.loads(zlib.decompress(packed).decode("utf-8"))
    if set(data) != set(EXPECTED_FILES):
        raise RuntimeError(f"file set mismatch: {sorted(data)}")

    root = Path(__file__).resolve().parents[1]
    for relative in sorted(data):
        text = data[relative]
        if not isinstance(text, str):
            raise RuntimeError(f"non-text payload: {relative}")
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != EXPECTED_FILES[relative]:
            raise RuntimeError(f"materialized hash mismatch for {relative}: {digest}")
        print(f"MATERIALIZED {relative} {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
