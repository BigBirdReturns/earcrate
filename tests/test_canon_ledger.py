from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.json"
CORRECTIONS_PATH = ROOT / "docs" / "canon" / "canon-ledger.v1.corrections.json"
SCHEMA_PATH = ROOT / "schemas" / "earcrate_canon_and_nonlanding_ledger_v1.schema.json"
CORRECTIONS_SCHEMA_PATH = ROOT / "schemas" / "earcrate_canon_ledger_corrections_v1.schema.json"
README_PATH = ROOT / "docs" / "CANON_AND_NONLANDING_LEDGER.md"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_sha256(value: dict) -> str:
    canonical = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _git_blob_sha1(payload: bytes) -> str:
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).hexdigest()


def _effective_ledger() -> tuple[dict, dict]:
    raw = LEDGER_PATH.read_bytes()
    base = json.loads(raw)
    corrections = _load(CORRECTIONS_PATH)

    claimed = base.pop("ledger_sha256")
    assert claimed == corrections["base_claimed_sha256"]
    assert _git_blob_sha1(raw) == corrections["base_git_blob_sha1"]

    actual = _canonical_sha256(base)
    assert actual == corrections["base_actual_sha256"]
    assert actual != claimed, "the append-only correction unexpectedly became unnecessary"

    effective = deepcopy(base)
    for operation in corrections["operations"]:
        assert operation["op"] == "replace"
        parts = operation["path"].lstrip("/").split("/")
        target = effective
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        leaf = parts[-1]
        current = target[int(leaf)] if isinstance(target, list) else target[leaf]
        assert current == operation["old"], (
            f"canon correction precondition changed at {operation['path']}: "
            f"expected {operation['old']!r}, found {current!r}"
        )
        if isinstance(target, list):
            target[int(leaf)] = operation["value"]
        else:
            target[leaf] = operation["value"]

    effective_sha = _canonical_sha256(effective)
    assert effective_sha == corrections["corrected_effective_sha256"]
    effective["ledger_sha256"] = effective_sha
    return effective, corrections


def _unique(rows: list[dict], field: str) -> set[str]:
    values = [str(row[field]) for row in rows]
    assert len(values) == len(set(values)), f"duplicate {field}: {values}"
    return set(values)


def test_canon_ledger_is_complete_hashed_corrected_and_schema_bound() -> None:
    ledger, corrections = _effective_ledger()
    schema = _load(SCHEMA_PATH)
    correction_schema = _load(CORRECTIONS_SCHEMA_PATH)

    assert ledger["schema_version"] == 1
    assert ledger["kind"] == "earcrate_canon_and_nonlanding_ledger"
    assert schema["properties"]["kind"]["const"] == ledger["kind"]
    assert schema["properties"]["schema_version"]["const"] == 1

    assert corrections["schema_version"] == 1
    assert corrections["kind"] == "earcrate_canon_ledger_corrections"
    assert correction_schema["properties"]["kind"]["const"] == corrections["kind"]
    assert correction_schema["properties"]["schema_version"]["const"] == 1
    assert corrections["operations"]
    assert _SHA64.fullmatch(ledger["ledger_sha256"])

    audit = ledger["audit"]
    assert audit["repository"] == "BigBirdReturns/earcrate"
    assert _SHA40.fullmatch(audit["audited_main_sha"])
    assert audit["method"] and audit["limitations"]

    prs = ledger["pull_requests"]
    assert [row["pr"] for row in prs] == list(range(1, 53))
    assert all(row["url"].endswith(f"/{row['pr']}") for row in prs)
    assert all(row["disposition"] in ledger["status_vocabulary"] for row in prs)
    assert all(
        not row.get("head_sha") or _SHA40.fullmatch(row["head_sha"])
        for row in prs
    )

    by_pr = {row["pr"]: row for row in prs}
    for number in range(29, 37):
        assert by_pr[number]["disposition"] == "landed_main_via_pr_37"
    for number in (40, 43, 44, 47):
        assert by_pr[number]["disposition"] == "landed_main_via_pr_49"
    assert by_pr[30]["main_reachability"] == "not_reachable_unique_snapshot_scaffold"
    assert by_pr[38]["main_reachability"] == "not_reachable_unique_overlay_commits"
    assert by_pr[41]["disposition"] == "deferred_concept_canon"
    assert by_pr[45]["disposition"] == "harvested_competing_implementation"
    assert by_pr[50]["disposition"] == "failed_delivery"
    assert by_pr[52]["disposition"] == "pending_verified_pr"
    assert by_pr[52]["main_reachability"] == "not_in_audited_main"

    nonmain = [
        row
        for row in prs
        if not row["disposition"].startswith("landed_main")
    ]
    assert all(row.get("why_not_main") for row in nonmain)

    assert _unique(ledger["theses"], "canon_id")
    assert _unique(ledger["non_pr_lineage"], "lineage_id")
    assert _unique(ledger["external_evidence"], "evidence_id")
    assert _unique(ledger["claim_corrections"], "claim_id")
    assert _unique(ledger["open_obligations"], "obligation_id")
    assert _unique(ledger["retired_decisions"], "decision_id")


def test_canon_ledger_preserves_evidence_tiers_and_unresolved_revisions() -> None:
    ledger, _ = _effective_ledger()
    evidence = {row["evidence_id"]: row for row in ledger["external_evidence"]}

    children = evidence["children.authoritative_score_pdf"]
    assert children["evidence_tier"] == "authoritative_score"
    assert children["expected_sha256"] == (
        "e029e1a3030800d7fb04c9f5163acb9270579a571d57f63eb63787df692d5845"
    )

    flim = evidence["flim.community_symbolic_pack"]
    assert flim["evidence_tier"] == "community_symbolic_witness"
    assert flim["proof_pack_sha256"] == (
        "a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52"
    )
    assert "not used" in flim["boundary"]

    first30 = evidence["pretty_lights.first30_reader_proof"]
    assert first30["status"] == "source_free_proof_landed_main"
    assert "publication" in first30["boundary"]

    reported = evidence["pretty_lights.release_candidate_reported_v3"]
    committed = evidence["pretty_lights.release_candidate_main_v1"]
    assert reported["status"] == "unresolved_external_revision"
    assert reported["reported_pack_sha256"] == (
        "b82e8895ab938c651d82da03233a6a8efc6aec8c2eb1b5004ba813071432f4a7"
    )
    assert committed["external_proof_pack_sha256"] == (
        "97bd2d4c3e7a38097956e2000db475e714c7fad67b51782b2905aeecfd8d0f9e"
    )
    assert reported["reported_pack_sha256"] != committed["external_proof_pack_sha256"]

    conversation = evidence["pretty_lights.full_recording_blind_report"]
    assert conversation["status"] == "conversation_report_unbound_to_main"
    assert conversation["reopen_condition"]


def test_canon_ledger_keeps_claim_corrections_and_open_debt_visible() -> None:
    ledger, _ = _effective_ledger()
    corrections = {row["claim_id"]: row for row in ledger["claim_corrections"]}
    obligations = {row["obligation_id"]: row for row in ledger["open_obligations"]}

    assert corrections["correction.production_cleanup_complete"]["status"] == "retracted"
    assert (
        corrections["correction.governance_v2_already_complete"]["status"]
        == "corrected_by_pending_pr"
    )
    assert corrections["correction.download_links_are_custody"]["status"] == (
        "retracted_as_general_rule"
    )

    required_open = {
        "buffalo.children.audio_inference",
        "buffalo.children.cross_modal_convergence",
        "buffalo.review_patch_circulation",
        "buffalo.campaign_evolution",
        "flim.blind_recording_control",
        "pretty_lights.provider_tournament",
        "pretty_lights.release_human_and_rights",
        "floor.network_sandbox",
        "floor.resource_isolation",
        "floor.remote_attestation",
        "floor.privacy_locality",
        "floor.normative_license",
        "repo.installable_distribution",
        "repo.reproducible_dependencies",
        "pending.governance_v2",
    }
    assert required_open.issubset(obligations)
    assert all(obligations[key]["reason"] for key in required_open)


def test_canon_ledger_main_references_exist_and_human_index_is_aligned() -> None:
    ledger, corrections = _effective_ledger()
    for thesis in ledger["theses"]:
        for ref in thesis["main_refs"]:
            assert (ROOT / ref).exists(), f"missing main reference: {ref}"
    for evidence in ledger["external_evidence"]:
        for ref in evidence.get("main_refs", []):
            assert (ROOT / ref).exists(), f"missing evidence reference: {ref}"

    text = README_PATH.read_text(encoding="utf-8")
    assert "Nothing disappears because it missed `main`" in text
    assert "merely because we once described it confidently" in text
    assert "entry for every pull request from **#1 through #52**" in text
    assert corrections["base_actual_sha256"] in text
    assert corrections["corrected_effective_sha256"] in text
    for number in (30, 38, 41, 45, 46, 48, 50, 52):
        assert f"#{number}" in text
