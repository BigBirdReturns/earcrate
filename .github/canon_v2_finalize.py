#!/usr/bin/env python3
"""Generate and seal canon v2 from the immutable v1 ledger and terminal GitHub facts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.json"
V1_CORRECTIONS_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.corrections.json"
V2_PATH = ROOT / "docs" / "canon" / "canon-ledger.v2.json"
V2_README_PATH = ROOT / "docs" / "CANON_AND_CAMPAIGN_V2.md"
AUDITED_MAIN = "d511414daa0c7127c1e9cfdc64726979e6682e1f"
AUDIT_DATE = "2026-08-02"


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def effective_v1() -> tuple[dict, bytes, bytes, dict]:
    v1_raw = V1_PATH.read_bytes()
    corrections_raw = V1_CORRECTIONS_PATH.read_bytes()
    base = json.loads(v1_raw)
    corrections = json.loads(corrections_raw)
    base.pop("ledger_sha256")
    effective = deepcopy(base)
    for operation in corrections["operations"]:
        if operation["op"] != "replace":
            raise RuntimeError(f"unsupported v1 correction: {operation['op']}")
        parts = operation["path"].lstrip("/").split("/")
        target = effective
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        leaf = parts[-1]
        current = target[int(leaf)] if isinstance(target, list) else target[leaf]
        if current != operation["old"]:
            raise RuntimeError(f"v1 correction precondition changed at {operation['path']}")
        if isinstance(target, list):
            target[int(leaf)] = operation["value"]
        else:
            target[leaf] = operation["value"]
    measured = canonical_sha256(effective)
    if measured != corrections["corrected_effective_sha256"]:
        raise RuntimeError(f"v1 effective identity changed: {measured}")
    effective["ledger_sha256"] = measured
    return effective, v1_raw, corrections_raw, corrections


def branch_retention() -> list[dict]:
    rows = [
        ("agent/canon-nonlanding-ledger", "886040ceb7ca5e6782e5c19d69f696d6f97a9ccc", "landed_frozen", "identical_to_terminal_head", 0, 0, "PR #53 landed as 6da4a6111ed529cd470c5cec3f4a0a1988fc3a08."),
        ("agent/forge-material-breeder-v0", "d27c0b1ac1ebf464ddec726105c5fdd546f02563", "deferred_concept_canon", "identical_to_terminal_head", 0, 0, "Direction retained; branch scaffolding is not the material forge implementation."),
        ("agent/gate8-canonical-integration", "3b70d343997470d7af93daa84558306db3abdf0f", "retired_delivery_mechanism", "identical_to_terminal_head", 0, 0, "Unique overlay workflow retired after ordinary source landed through PR #39."),
        ("agent/harden-release-governance-v2", "43245748702848053af970d88011479e0044e4c7", "landed_frozen", "identical_to_terminal_head", 0, 0, "PR #52 landed as 36618a23f755b876e6d887be64a61389b5093e10."),
        ("agent/homelab-integrity-transport", "ab9e89a4966f5656c3e86c0ce4d080e5e85e1100", "completed_transport_no_authority", "diverged_after_terminal_close", 2, 23, "PR #56 terminal transport head; exact payload was harvested into PR #55."),
        ("agent/integrated-score-cutover", "b307dd613ffc61c7efa2faea2e8d6fdb15ab8fdb", "retired_snapshot_scaffold", "identical_to_terminal_head", 0, 0, "Snapshot/export mechanism retired after immutable project authority landed."),
        ("agent/local-estate-control-plane", "d2444b507153acff90b0f3d3b80892ebf46afab0", "landed_frozen", "identical_to_terminal_head", 0, 0, "PR #55 landed as d511414daa0c7127c1e9cfdc64726979e6682e1f."),
        ("agent/production-cleanup-inline-probe", "45ef903f15dd3b813f51193371f688ab46cfad94", "failed_delivery_diagnostic", "identical_to_terminal_head", 0, 0, "Single-file repository transport probe; not product work."),
        ("agent/production-cleanup-inline-probe-2", "a0e19f4b9320ce551c6c6e1d61bfe3d3b00bcc9b", "failed_delivery_diagnostic", "identical_to_terminal_head", 0, 0, "Second repository transport probe; not product work."),
        ("agent/release-candidate-discipline", "18e3af22d03c32f280198ea4416c43abbb827357", "harvested_competing_implementation", "identical_to_terminal_head", 0, 0, "Concepts were harvested; parallel authority was not."),
        ("agent/release-candidate-review-floor", "0816d0cee08f77b1873fec9c03ee065db3154eda", "retired_scaffold", "identical_to_terminal_head", 0, 0, "Export workflow and placeholders only."),
        ("agent/release-review-floor-v1", "1249ab673e4250d4c5f8361a5712e1f031fe9f9b", "retired_scaffold", "identical_to_terminal_head", 0, 0, "Export workflow and placeholders only."),
        ("claude/earcrate-v0.9.0-complete-wrz7lw", "95cb411da8e99cafbd64b9f0769da77f7e0e2a99", "deferred_unvalidated_branch", "identical_to_terminal_head", 0, 0, "Candidate organs remain harvestable only through current contracts and real-rig receipts."),
    ]
    result: list[dict] = []
    for ref, head, status, check, ahead, behind, authority in rows:
        pointer = {
            "as_of": AUDIT_DATE,
            "result": check,
            "ahead_by": ahead,
            "behind_by": behind,
        }
        if check == "diverged_after_terminal_close":
            pointer["note"] = (
                "Current pointer is two commits ahead and twenty-three behind the terminal-head comparison base; "
                "no later pointer content is product authority."
            )
        result.append({
            "ref": ref,
            "terminal_head_sha": head,
            "status": status,
            "authority": authority,
            "pointer_check": pointer,
        })
    return result


def campaign_fanout() -> dict:
    rows = [
        (58, "P0", "Run the first explicit-root, read-only estate sweep"),
        (59, "P0", "Run the Flim exact-recording blind control"),
        (60, "P0", "Seal Children audio inference and score/audio convergence"),
        (61, "P1", "Regenerate Children receipts and execute a sealed rack realization"),
        (62, "P0", "Run the Pretty Lights provider tournament and reconcile candidate revisions"),
        (63, "P0", "Complete Pretty Lights human, rights, and publication governance"),
        (64, "P1", "Circulate ReviewPatch evidence and prove campaign learning"),
        (65, "P1", "Enforce Floor host boundaries and settle normative policy"),
        (66, "P1", "Ship reproducible installable distribution and verifiable archive custody"),
        (67, "P2", "Lower real-time MixScore, key lock, controllers, and external sync"),
        (68, "P0", "Publish canon v2 and freeze the historical branch retention map"),
    ]
    return {
        "audited_main_sha": AUDITED_MAIN,
        "epic_issue": 57,
        "issues": [
            {
                "issue": number,
                "priority": priority,
                "state": "open",
                "title": title,
                "url": f"https://github.com/BigBirdReturns/earcrate/issues/{number}",
            }
            for number, priority, title in rows
        ],
    }


def post_v1_pull_requests() -> list[dict]:
    return [
        {
            "pr": 53,
            "title": "Record complete canon and nonlanding ledger",
            "url": "https://github.com/BigBirdReturns/earcrate/pull/53",
            "head_sha": "886040ceb7ca5e6782e5c19d69f696d6f97a9ccc",
            "disposition": "landed_main_direct_merge",
            "main_reachability": "reachable_from_audited_main",
            "canon_summary": "Append-only canon and nonlanding authority, landed as 6da4a6111ed529cd470c5cec3f4a0a1988fc3a08.",
        },
        {
            "pr": 54,
            "title": "[Superseded duplicate] Local estate and Homelab control plane",
            "url": "https://github.com/BigBirdReturns/earcrate/pull/54",
            "head_sha": "91db11e68bff42ad35de9767cf2bf36fadeb0f13",
            "disposition": "superseded_duplicate",
            "main_reachability": "not_reachable_no_unique_authority",
            "canon_summary": "Duplicate direct-to-main review view of the #55 implementation.",
            "why_not_main": "No unique code, evidence, review thread, or architectural authority existed only in this lane.",
        },
        {
            "pr": 55,
            "title": "Add the local estate and Homelab provider-acceptance control plane",
            "url": "https://github.com/BigBirdReturns/earcrate/pull/55",
            "head_sha": "d2444b507153acff90b0f3d3b80892ebf46afab0",
            "disposition": "landed_main_direct_merge",
            "main_reachability": "reachable_from_audited_main",
            "canon_summary": "Explicit-root estate reconciliation and MAME-style 87-target Homelab acceptance authority, landed as d511414daa0c7127c1e9cfdc64726979e6682e1f.",
        },
        {
            "pr": 56,
            "title": "[Completed temporary transport] Homelab integrity overlay",
            "url": "https://github.com/BigBirdReturns/earcrate/pull/56",
            "head_sha": "ab9e89a4966f5656c3e86c0ce4d080e5e85e1100",
            "disposition": "completed_transport_harvested",
            "main_reachability": "not_reachable_transport_payload_harvested",
            "canon_summary": "Content-addressed temporary transport whose intended integrity payload was incorporated into #55.",
            "why_not_main": "The transport lane carried no independent product authority and was closed after payload verification and harvest.",
        },
    ]


def upsert_obligation(rows: list[dict], value: dict) -> None:
    for index, row in enumerate(rows):
        if row.get("obligation_id") == value["obligation_id"]:
            merged = dict(row)
            merged.update(value)
            rows[index] = merged
            return
    rows.append(value)


def main() -> int:
    ledger, v1_raw, corrections_raw, corrections = effective_v1()
    ledger["schema_version"] = 2

    audit = ledger["audit"]
    audit["audit_date"] = AUDIT_DATE
    audit["audited_main_sha"] = AUDITED_MAIN
    audit["pending_pull_requests"] = []
    for line in (
        "reviewed pull requests 53 through 56 and their terminal dispositions",
        "verified current retained branch pointers against recorded terminal heads",
        "recorded the post-governance campaign fan-out in issues 57 through 68",
    ):
        if line not in audit["method"]:
            audit["method"].append(line)
    for line in (
        "Issue creation records obligations and sequencing, not execution passage.",
        "The PR #56 transport branch pointer drifted after terminal closure; only the closure head and harvested payload retain authority.",
    ):
        if line not in audit["limitations"]:
            audit["limitations"].append(line)

    vocabulary = ledger["status_vocabulary"]
    vocabulary.update({
        "superseded_duplicate": "Duplicate review lane with no unique authority; canonical work is retained elsewhere.",
        "completed_transport_harvested": "Temporary transport completed its bounded job; its payload was harvested and the lane retains no product authority.",
    })

    prs = [row for row in ledger["pull_requests"] if int(row["pr"]) <= 52]
    prs.extend(post_v1_pull_requests())
    prs.sort(key=lambda row: int(row["pr"]))
    if [row["pr"] for row in prs] != list(range(1, 57)):
        raise RuntimeError("generated PR coverage is not exactly 1 through 56")
    ledger["pull_requests"] = prs
    ledger["branch_retention"] = branch_retention()
    ledger["campaign_fanout"] = campaign_fanout()

    obligations = [
        dict(row)
        for row in ledger["open_obligations"]
        if row.get("obligation_id") != "repo.ci_cross_platform_main_trigger"
    ]
    tracking = {
        "buffalo.children.audio_inference": 60,
        "buffalo.children.cross_modal_convergence": 60,
        "buffalo.review_patch_circulation": 64,
        "buffalo.campaign_evolution": 64,
        "flim.blind_recording_control": 59,
        "pretty_lights.provider_tournament": 62,
        "pretty_lights.release_human_and_rights": 63,
        "mixscore.realtime_and_keylock": 67,
        "floor.network_sandbox": 65,
        "floor.resource_isolation": 65,
        "floor.remote_attestation": 65,
        "floor.privacy_locality": 65,
        "floor.normative_license": 65,
        "repo.installable_distribution": 66,
        "repo.reproducible_dependencies": 66,
        "repo.historical_branch_archive_custody": 66,
    }
    for row in obligations:
        issue = tracking.get(row.get("obligation_id"))
        if issue is not None:
            row["tracking_issue"] = issue

    new_obligations = [
        {
            "obligation_id": "estate.first_real_read_only_sweep",
            "source": "PR #55 Local Estate control plane",
            "status": "not_run",
            "reason": "The control plane has not inventoried the operator's actual roots, nodes, databases, models, caches, or evidence estate.",
            "tracking_issue": 58,
        },
        {
            "obligation_id": "homelab.real_fixture_bindings",
            "source": "PR #55 HomelabFixtureBinding boundary",
            "status": "blocked",
            "reason": "Exact target recordings, private-library material, project revisions, and physical playback fixtures have not been bound and reverified on the real estate.",
            "tracking_issue": 58,
        },
        {
            "obligation_id": "homelab.provider_terminal_dispositions",
            "source": "PR #55 Homelab Provider Arcade",
            "status": "not_run",
            "reason": "The 87-target catalog has not yet produced real accepted, rejected, deferred, or reference-only decisions.",
            "tracking_issue": 57,
        },
        {
            "obligation_id": "buffalo.children.adjacent_move_receipt_alignment",
            "source": "Children score-side proof",
            "status": "ledger_drift_to_reconcile",
            "reason": "The committed score-side receipt predates the current continuation authority and must be regenerated rather than hand-edited.",
            "tracking_issue": 61,
        },
        {
            "obligation_id": "buffalo.children.sealed_rack_realization",
            "source": "Children score-side proof",
            "status": "blocked",
            "reason": "The accepted score/performance program has not executed through an approved private-library rack body.",
            "tracking_issue": 61,
        },
        {
            "obligation_id": "repo.historical_branch_archive_custody",
            "source": "Historical branch retention map",
            "status": "partial",
            "reason": "Terminal heads are recorded, but independently restorable archive custody and restoration verification remain incomplete.",
            "tracking_issue": 66,
        },
    ]
    for value in new_obligations:
        upsert_obligation(obligations, value)
    ledger["open_obligations"] = obligations

    ledger["supersedes"] = {
        "schema_version": 1,
        "ledger_path": "docs/canon/canon-ledger.v1.json",
        "corrections_path": "docs/canon/canon-ledger.v1.corrections.json",
        "ledger_sha256": hashlib.sha256(v1_raw).hexdigest(),
        "corrections_sha256": hashlib.sha256(corrections_raw).hexdigest(),
        "ledger_git_blob_sha1": git_blob_sha1(v1_raw),
        "corrections_git_blob_sha1": git_blob_sha1(corrections_raw),
        "effective_ledger_sha256": corrections["corrected_effective_sha256"],
        "reason": "Canon v2 advances the audit through PR #56 and the post-governance campaign fan-out without rewriting immutable v1 bytes or correction history.",
    }

    ledger.pop("ledger_sha256", None)
    digest = canonical_sha256(ledger)
    ledger["ledger_sha256"] = digest
    raw = canonical_bytes(ledger)
    V2_PATH.parent.mkdir(parents=True, exist_ok=True)
    V2_PATH.write_bytes(raw)

    readme = V2_README_PATH.read_text(encoding="utf-8")
    readme, count = re.subn(
        r"(?m)^(ledger SHA-256:\s*)[0-9a-f]{64}$",
        rf"\g<1>{digest}",
        readme,
    )
    if count != 1:
        raise RuntimeError(f"expected one canon-v2 README hash line, found {count}")
    V2_README_PATH.write_text(readme, encoding="utf-8", newline="\n")

    print(f"FINALIZED {V2_PATH.relative_to(ROOT)} file_sha256={hashlib.sha256(raw).hexdigest()} ledger_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
