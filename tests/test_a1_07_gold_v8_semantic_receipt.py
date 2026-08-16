from __future__ import annotations

import json
from pathlib import Path

from earcrate.a1_07_gold_v8 import common as c
from earcrate.a1_07_gold_v8 import custody as u


def test_owner_review_semantic_identity_ignores_json_formatting(tmp_path: Path) -> None:
    receipt = c.seal(
        {
            "schema_version": 1,
            "kind": "earcrate_reference_zero_review_ledger",
            "verdict": "candidate_beats_control",
            "assignment_sha256": "1" * 64,
        },
        "ledger_sha256",
    )
    path = tmp_path / "owner-review.receipt.json"
    path.write_text(json.dumps(receipt, indent=4) + "\n", encoding="utf-8")
    assert c.sha256_file(path) != receipt["ledger_sha256"]
    assert u.owner_review_identity(path) == receipt["ledger_sha256"]
