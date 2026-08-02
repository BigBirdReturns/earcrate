from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
V1_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.json"
V1_CORRECTIONS_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.corrections.json"
V2_PATH = ROOT / "docs" / "canon" / "canon-ledger.v2.json"
V2_SCHEMA_PATH = ROOT / "schemas" / "earcrate_canon_and_nonlanding_ledger_v2.schema.json"
V2_README_PATH = ROOT / "docs" / "CANON_AND_CAMPAIGN_V2.md"
BRANCH_MAP_PATH = ROOT / "docs" / "BRANCH_RETENTION_MAP.md"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_sha256(value: dict) -> str:
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def test_canon_v2_is_self_hashed_schema_bound_and_supersedes_exact_v1() -> None:
    ledger = _load(V2_PATH)
    schema = _load(V2_SCHEMA_PATH)
    claimed = ledger.pop("ledger_sha256")
    assert _SHA64.fullmatch(claimed)
    assert claimed == _canonical_sha256(ledger)
    ledger["ledger_sha256"] = claimed

    assert ledger["schema_version"] == 2
    assert ledger["kind"] == "earcrate_canon_and_nonlanding_ledger"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == 2
    assert {"branch_retention", "campaign_fanout", "supersedes"}.issubset(schema["required"])

    v1_raw = V1_PATH.read_bytes()
    corrections_raw = V1_CORRECTIONS_PATH.read_bytes()
    corrections = json.loads(corrections_raw)
    supersedes = ledger["supersedes"]
    assert supersedes["ledger_sha256"] == hashlib.sha256(v1_raw).hexdigest()
    assert supersedes["corrections_sha256"] == hashlib.sha256(corrections_raw).hexdigest()
    assert supersedes["ledger_git_blob_sha1"] == _git_blob_sha1(v1_raw)
    assert supersedes["corrections_git_blob_sha1"] == _git_blob_sha1(corrections_raw)
    assert supersedes["effective_ledger_sha256"] == corrections["corrected_effective_sha256"]


def test_canon_v2_covers_every_pr_and_records_terminal_post_v1_dispositions() -> None:
    ledger = _load(V2_PATH)
    assert ledger["audit"]["audited_main_sha"] == "d511414daa0c7127c1e9cfdc64726979e6682e1f"
    assert ledger["audit"]["pending_pull_requests"] == []
    prs = ledger["pull_requests"]
    assert [row["pr"] for row in prs] == list(range(1, 57))
    assert all(row["disposition"] in ledger["status_vocabulary"] for row in prs)
    by_pr = {row["pr"]: row for row in prs}
    assert by_pr[53]["disposition"] == "landed_main_direct_merge"
    assert by_pr[54]["disposition"] == "superseded_duplicate"
    assert by_pr[55]["disposition"] == "landed_main_direct_merge"
    assert by_pr[56]["disposition"] == "completed_transport_harvested"
    assert by_pr[53]["head_sha"] == "886040ceb7ca5e6782e5c19d69f696d6f97a9ccc"
    assert by_pr[54]["head_sha"] == "91db11e68bff42ad35de9767cf2bf36fadeb0f13"
    assert by_pr[55]["head_sha"] == "d2444b507153acff90b0f3d3b80892ebf46afab0"
    assert by_pr[56]["head_sha"] == "ab9e89a4966f5656c3e86c0ce4d080e5e85e1100"
    assert by_pr[54]["why_not_main"] and by_pr[56]["why_not_main"]


def test_canon_v2_branch_retention_and_campaign_fanout_are_complete() -> None:
    ledger = _load(V2_PATH)
    branches = ledger["branch_retention"]
    assert len(branches) == 13
    assert len({row["ref"] for row in branches}) == len(branches)
    assert all(_SHA40.fullmatch(row["terminal_head_sha"]) for row in branches)
    identical = [row for row in branches if row["pointer_check"]["result"] == "identical_to_terminal_head"]
    assert len(identical) == 12
    transport = next(row for row in branches if row["ref"] == "agent/homelab-integrity-transport")
    assert transport["pointer_check"]["result"] == "diverged_after_terminal_close"
    assert transport["pointer_check"]["ahead_by"] == 2
    assert transport["pointer_check"]["behind_by"] == 23

    fanout = ledger["campaign_fanout"]
    assert fanout["epic_issue"] == 57
    assert [row["issue"] for row in fanout["issues"]] == list(range(58, 69))
    obligations = {row["obligation_id"]: row for row in ledger["open_obligations"]}
    assert "repo.ci_cross_platform_main_trigger" not in obligations
    assert obligations["estate.first_real_read_only_sweep"]["tracking_issue"] == 58
    assert obligations["flim.blind_recording_control"]["tracking_issue"] == 59
    assert obligations["buffalo.children.audio_inference"]["tracking_issue"] == 60
    assert obligations["pretty_lights.provider_tournament"]["tracking_issue"] == 62


def test_canon_v2_preserves_evidence_tiers_and_human_indexes() -> None:
    ledger = _load(V2_PATH)
    evidence = {row["evidence_id"]: row for row in ledger["external_evidence"]}
    flim = evidence["flim.community_symbolic_pack"]
    assert flim["evidence_tier"] == "community_symbolic_witness"
    assert flim["proof_pack_sha256"] == "a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52"
    assert "not used" in flim["boundary"]
    children = evidence["children.authoritative_score_pdf"]
    assert children["evidence_tier"] == "authoritative_score"
    assert children["expected_sha256"] == "e029e1a3030800d7fb04c9f5163acb9270579a571d57f63eb63787df692d5845"
    assert evidence["pretty_lights.release_candidate_reported_v3"]["status"] == "unresolved_external_revision"
    assert evidence["pretty_lights.release_candidate_main_v1"]["status"] == "main_contract_external_pack"

    for thesis in ledger["theses"]:
        for ref in thesis["main_refs"]:
            assert (ROOT / ref).exists(), f"missing thesis reference: {ref}"
    for item in ledger["external_evidence"]:
        for ref in item.get("main_refs", []):
            assert (ROOT / ref).exists(), f"missing evidence reference: {ref}"

    readme = V2_README_PATH.read_text(encoding="utf-8")
    branch_map = BRANCH_MAP_PATH.read_text(encoding="utf-8")
    assert ledger["ledger_sha256"] in readme
    assert ledger["audit"]["audited_main_sha"] in readme
    assert "community-symbolic witness" in readme
    assert "D.S. al Coda" in readme
    for row in ledger["branch_retention"]:
        assert row["ref"] in branch_map
        assert row["terminal_head_sha"] in branch_map
