#!/usr/bin/env python3
"""Record PR #52's terminal main disposition and reseal canon identities."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

MAIN_SHA = "36618a23f755b876e6d887be64a61389b5093e10"
PR52_HEAD = "43245748702848053af970d88011479e0044e4c7"
AUDIT_DATE = "2026-08-02"

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "docs/canon/canon-ledger.v1.json"
CORRECTIONS_PATH = ROOT / "docs/canon/canon-ledger.v1.corrections.json"
README_PATH = ROOT / "docs/CANON_AND_NONLANDING_LEDGER.md"
TESTS_PATH = ROOT / "tests/test_canon_ledger.py"


def canonical_sha(value: dict) -> str:
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    old_corrections = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))

    audit = ledger["audit"]
    audit["audit_date"] = AUDIT_DATE
    audit["audited_main_sha"] = MAIN_SHA
    audit["pending_pull_requests"] = []
    terminal_method = (
        "recorded PR #52 as squash-merged main authority only after its five "
        "review findings were resolved and its exact merge-ref gates passed"
    )
    if terminal_method not in audit["method"]:
        audit["method"].append(terminal_method)

    pr52 = next(row for row in ledger["pull_requests"] if row["pr"] == 52)
    pr52["head_sha"] = PR52_HEAD
    pr52["disposition"] = "landed_main_direct_merge"
    pr52["main_reachability"] = "reachable_from_audited_main"
    pr52["canon_summary"] = (
        "Committed per-reviewer blinding, independently revalidated arbitration, "
        "use-scoped time-bounded rights, format-neutral permits, atomic publication, "
        "complete publication verification, and runtime-aligned governance schemas."
    )
    pr52.pop("why_not_main", None)

    correction = next(
        row
        for row in ledger["claim_corrections"]
        if row["claim_id"] == "correction.governance_v2_already_complete"
    )
    correction.pop("pending_ref", None)
    correction["landed_ref"] = {
        "head_sha": PR52_HEAD,
        "main_sha": MAIN_SHA,
        "pr": 52,
    }
    correction["reason"] = (
        "Audited main documentation was ahead of implementation. PR #52 closed "
        "the contract, resolved all five review findings, passed its exact merge-ref "
        "gates, and landed on main as the recorded squash commit."
    )
    correction["status"] = "resolved_by_landed_pr"

    overlay = next(
        row
        for row in ledger["non_pr_lineage"]
        if row["lineage_id"] == "local.production_cleanup_overlay"
    )
    overlay["current_resolution"]["governance"] = (
        f"reimplemented by PR #52 and landed on main at {MAIN_SHA}"
    )

    ledger["open_obligations"] = [
        row
        for row in ledger["open_obligations"]
        if row["obligation_id"] != "pending.governance_v2"
    ]

    claimed = ledger["ledger_sha256"]
    write_json(LEDGER_PATH, ledger)
    raw = LEDGER_PATH.read_bytes()
    blob_header = b"blob " + str(len(raw)).encode("ascii") + b"\0"
    blob_sha = hashlib.sha1(blob_header + raw).hexdigest()

    base = deepcopy(ledger)
    base.pop("ledger_sha256")
    base_actual = canonical_sha(base)

    corrections = old_corrections
    corrections["base_claimed_sha256"] = claimed
    corrections["base_actual_sha256"] = base_actual
    corrections["base_git_blob_sha1"] = blob_sha

    effective = deepcopy(base)
    for operation in corrections["operations"]:
        if operation["op"] != "replace":
            raise RuntimeError("only replace canon corrections are supported")
        parts = operation["path"].lstrip("/").split("/")
        target = effective
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        leaf = parts[-1]
        current = target[int(leaf)] if isinstance(target, list) else target[leaf]
        if current != operation["old"]:
            raise RuntimeError(
                f"correction precondition changed at {operation['path']}: "
                f"expected {operation['old']!r}, found {current!r}"
            )
        if isinstance(target, list):
            target[int(leaf)] = operation["value"]
        else:
            target[leaf] = operation["value"]
    corrected_effective = canonical_sha(effective)
    corrections["corrected_effective_sha256"] = corrected_effective
    write_json(CORRECTIONS_PATH, corrections)

    readme = README_PATH.read_text(encoding="utf-8")
    readme = replace_once(
        readme,
        "audit main:             2fd50b9c72f80d7f0f6eb928cb61035e51a78fd3",
        f"audit main:             {MAIN_SHA}",
        "README audited main",
    )
    readme = replace_once(
        readme,
        "audit date:             2026-07-31",
        f"audit date:             {AUDIT_DATE}",
        "README audit date",
    )
    readme = replace_once(
        readme,
        "pending PR:             #52 @ a1babd796e6ea87aa9f6489c6d589540337248d6",
        f"landed PR #52 head:     {PR52_HEAD}\nlanded PR #52 main:     {MAIN_SHA}",
        "README PR52 terminal status",
    )
    readme = replace_once(
        readme,
        f"base actual SHA:        {old_corrections['base_actual_sha256']}",
        f"base actual SHA:        {base_actual}",
        "README base actual hash",
    )
    readme = replace_once(
        readme,
        f"effective corrected SHA: {old_corrections['corrected_effective_sha256']}",
        f"effective corrected SHA: {corrected_effective}",
        "README effective hash",
    )
    readme = replace_once(
        readme,
        "- **PRs #49 and #51:** cumulative product reconciliation and single-main lineage.\n",
        "- **PRs #49 and #51:** cumulative product reconciliation and single-main lineage.\n"
        "- **PR #52:** committed review assignments, independently revalidated arbitration,\n"
        "  use-scoped rights, temporally bounded permits, atomic publication, and complete\n"
        "  publication verification.\n",
        "README reached-main PR52 entry",
    )
    readme = replace_once(
        readme,
        "| PR #52 | pending canon | Green and mergeable, intentionally not main authority before review and merge. |\n",
        "",
        "README remove pending PR52 row",
    )
    readme = replace_once(
        readme,
        "- **Governance v2 fully enforced in PR #49:** corrected. PR #52 implements the\n"
        "  missing committed assignments, arbitration, scoped rights, format-neutral\n"
        "  permits, atomic publication, and `PublicationReceipt`; it remains pending.\n",
        "- **Governance v2 fully enforced in PR #49:** corrected and resolved. PR #52\n"
        "  supplied the missing committed assignments, independently revalidated\n"
        "  arbitration, scoped rights, time-bounded permits, atomic publication, complete\n"
        "  verification, and `PublicationReceipt`, then landed after exact-head review.\n",
        "README governance correction",
    )
    readme = replace_once(
        readme,
        "- PR #52 review and merge.\n",
        "",
        "README close governance obligation",
    )
    README_PATH.write_text(readme, encoding="utf-8", newline="\n")

    tests = TESTS_PATH.read_text(encoding="utf-8")
    tests = replace_once(
        tests,
        "    assert audit[\"method\"] and audit[\"limitations\"]\n",
        "    assert audit[\"method\"] and audit[\"limitations\"]\n"
        f"    assert audit[\"audited_main_sha\"] == \"{MAIN_SHA}\"\n"
        "    assert audit[\"pending_pull_requests\"] == []\n",
        "test audited main terminal identity",
    )
    tests = replace_once(
        tests,
        "    assert by_pr[52][\"disposition\"] == \"pending_verified_pr\"\n"
        "    assert by_pr[52][\"main_reachability\"] == \"not_in_audited_main\"\n",
        "    assert by_pr[52][\"disposition\"] == \"landed_main_direct_merge\"\n"
        "    assert by_pr[52][\"main_reachability\"] == \"reachable_from_audited_main\"\n",
        "test PR52 disposition",
    )
    tests = replace_once(
        tests,
        "        == \"corrected_by_pending_pr\"\n",
        "        == \"resolved_by_landed_pr\"\n",
        "test governance correction status",
    )
    tests = replace_once(
        tests,
        "        \"pending.governance_v2\",\n",
        "",
        "test remove pending governance obligation",
    )
    tests = replace_once(
        tests,
        "    assert required_open.issubset(obligations)\n",
        "    assert required_open.issubset(obligations)\n"
        "    assert \"pending.governance_v2\" not in obligations\n",
        "test closed governance obligation",
    )
    TESTS_PATH.write_text(tests, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "ok": True,
                "audited_main_sha": MAIN_SHA,
                "pr52_head_sha": PR52_HEAD,
                "base_claimed_sha256": claimed,
                "base_actual_sha256": base_actual,
                "base_git_blob_sha1": blob_sha,
                "corrected_effective_sha256": corrected_effective,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
