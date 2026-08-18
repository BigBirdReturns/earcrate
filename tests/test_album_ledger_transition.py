"""Gates for the only supported path to change Album One authority.

Three hand-applied transitions produced three stale-copy defects in one day. The
central gate here is the migration proof: replaying A1-07's landed acceptance
through the tool must write zero bytes and derive the identical seal, which is what
demonstrates the hand-built state is exactly what the tool would have produced.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.album import transitions as tr  # noqa: E402

ACCEPTANCE = "proofs/album_one/a1-07-master-acceptance-v1.public.json"
QUALIFICATION = "proofs/album_one/a1-07-master-v1.public.json"
DOCUMENTS = ("ALBUM_ONE.md", "README.md", "PRODUCT.md", "MILESTONES.md", "AGENTS.md",
             "README_FIRST.txt")


def _sandbox(tmp_path: Path) -> Path:
    """A writable copy of exactly the files the ledger projects into."""
    root = tmp_path / "repo"
    (root / "configs" / "album_one").mkdir(parents=True)
    (root / "proofs" / "album_one").mkdir(parents=True)
    shutil.copy2(ROOT / tr.MANIFEST_RELATIVE, root / tr.MANIFEST_RELATIVE)
    for name in DOCUMENTS:
        shutil.copy2(ROOT / name, root / name)
    for path in (ROOT / "proofs" / "album_one").glob("*.json"):
        shutil.copy2(path, root / "proofs" / "album_one" / path.name)
    return root


def _reseal(root: Path, manifest: dict) -> None:
    from earcrate.evidence.identity import seal
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    sealed = seal(body, "manifest_sha256")
    (root / tr.MANIFEST_RELATIVE).write_text(
        json.dumps(sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8", newline="\n")


def test_the_live_ledger_verifies():
    assert tr.verify(ROOT) == []


def test_replaying_the_landed_acceptance_changes_zero_bytes(tmp_path):
    """The migration proof: the tool derives exactly the hand-built state."""
    root = _sandbox(tmp_path)
    before = {name: (root / name).read_bytes() for name in DOCUMENTS}
    before[tr.MANIFEST_RELATIVE] = (root / tr.MANIFEST_RELATIVE).read_bytes()

    result = tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                                 receipt_path=root / ACCEPTANCE)

    assert result["idempotent_replay"] is True
    assert result["files_written"] == [], "a replay must not rewrite anything"
    assert result["accepted_album_masters"] == 1
    assert result["completed_system_references"] == 0
    for name, content in before.items():
        assert (root / name).read_bytes() == content, f"{name} changed on replay"

    # And the seal the tool derives is the seal the repository already carries.
    live = json.loads((ROOT / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    assert result["manifest_sha256"] == live["manifest_sha256"]


def test_counters_are_outputs_and_a_wrong_one_is_corrected(tmp_path):
    """A counter can never be an input, so a bad one cannot survive a transition."""
    root = _sandbox(tmp_path)
    manifest = json.loads((root / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    manifest["completed_album_master_count"] = 6
    manifest["completed_system_reference_count"] = 4
    _reseal(root, manifest)

    problems = tr.verify(root)
    assert any("accepted-master counter disagrees" in row for row in problems)
    assert any("system-reference counter disagrees" in row for row in problems)

    result = tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                                 receipt_path=root / ACCEPTANCE)
    assert result["accepted_album_masters"] == 1
    assert result["completed_system_references"] == 0
    assert tr.verify(root) == []


def test_states_advance_in_order(tmp_path):
    root = _sandbox(tmp_path)

    # Wind A1-07 back behind qualification: an accepted master cannot precede one.
    manifest = json.loads((root / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    row.pop("accepted_master")
    row.pop("master_qualification")
    row["repo_evidence"] = [entry for entry in row["repo_evidence"]
                            if not entry.endswith("frontier.public.json")]
    row["status"]["album_master"] = "unaccepted"
    row["status"]["human_acceptance"] = False
    manifest["completed_album_master_count"] = 0
    _reseal(root, manifest)
    assert tr.current_state(row) == tr.NONE

    with pytest.raises(tr.LedgerTransitionError, match="requires 'master_qualified'"):
        tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                            receipt_path=root / ACCEPTANCE)

    # A receipt naming another track is refused before anything else is considered.
    with pytest.raises(tr.LedgerTransitionError, match="names track"):
        tr.apply_transition(root, track_id="A1-01", event_name="master-accepted",
                            receipt_path=root / ACCEPTANCE)

    # A1-07 is accepted but its system reference has no receipt kind in existence yet,
    # so the challenge cannot be claimed by pointing at any other evidence.
    with pytest.raises(tr.LedgerTransitionError, match="kind"):
        tr.apply_transition(root, track_id="A1-07", event_name="system-reference-passed",
                            receipt_path=root / ACCEPTANCE)


def test_a_qualification_cannot_be_replayed_as_an_acceptance(tmp_path):
    root = _sandbox(tmp_path)
    with pytest.raises(tr.LedgerTransitionError, match="kind"):
        tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                            receipt_path=root / QUALIFICATION)


def test_a_receipt_for_another_track_is_refused(tmp_path):
    root = _sandbox(tmp_path)
    with pytest.raises(tr.LedgerTransitionError, match="names track"):
        tr.apply_transition(root, track_id="A1-02", event_name="master-accepted",
                            receipt_path=root / ACCEPTANCE)


def test_a_receipt_outside_the_repository_is_refused(tmp_path):
    root = _sandbox(tmp_path)
    outside = tmp_path / "elsewhere.json"
    shutil.copy2(root / ACCEPTANCE, outside)
    with pytest.raises(tr.LedgerTransitionError, match="inside the repository"):
        tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                            receipt_path=outside)


def test_an_acceptance_naming_a_different_object_is_refused(tmp_path):
    """A decision binds to one exact object; a near miss is not a match."""
    from earcrate.evidence.identity import seal

    root = _sandbox(tmp_path)
    receipt = json.loads((root / ACCEPTANCE).read_text(encoding="utf-8"))
    receipt["audited_object"]["canonical_pcm_sha256"] = "f" * 64
    receipt.pop("receipt_sha256")
    forged = seal(receipt, "receipt_sha256")
    path = root / "proofs" / "album_one" / "forged.public.json"
    path.write_text(json.dumps(forged, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")

    # Wind the track back to qualified so the identity check is what refuses it.
    manifest = json.loads((root / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    row.pop("accepted_master")
    row["status"]["album_master"] = "unaccepted"
    row["status"]["human_acceptance"] = False
    row["master_qualification"]["master_state"] = "master_qualified"
    row["master_qualification"]["owner_master_acceptance"] = False
    manifest["completed_album_master_count"] = 0
    _reseal(root, manifest)

    with pytest.raises(tr.LedgerTransitionError, match="one exact object"):
        tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                            receipt_path=path)


def test_a_real_advance_writes_the_manifest_and_every_projection(tmp_path):
    """Wind A1-07 back to qualified, then accept it again through the tool."""
    root = _sandbox(tmp_path)
    manifest = json.loads((root / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    row.pop("accepted_master")
    row["status"]["album_master"] = "unaccepted"
    row["status"]["human_acceptance"] = False
    row["master_qualification"]["master_state"] = "master_qualified"
    row["master_qualification"]["owner_master_acceptance"] = False
    manifest["completed_album_master_count"] = 0
    _reseal(root, manifest)
    for name in DOCUMENTS:
        text = (root / name).read_text(encoding="utf-8")
        (root / name).write_text(text.replace("1/7", "0/7"), encoding="utf-8", newline="\n")

    assert tr.current_state(row) == tr.MASTER_QUALIFIED
    result = tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                                 receipt_path=root / ACCEPTANCE)

    assert result["from_state"] == tr.MASTER_QUALIFIED
    assert result["to_state"] == tr.MASTER_ACCEPTED
    assert result["idempotent_replay"] is False
    assert result["accepted_album_masters"] == 1
    assert tr.MANIFEST_RELATIVE in result["files_written"]
    for name in DOCUMENTS:
        assert name in result["files_written"], f"{name} was not re-projected"
        assert "1/7" in (root / name).read_text(encoding="utf-8")
    assert tr.verify(root) == []

    # Applying it a second time is now a no-op.
    again = tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                                receipt_path=root / ACCEPTANCE)
    assert again["files_written"] == []


def test_a_stale_quoted_seal_is_reported_and_repaired(tmp_path):
    root = _sandbox(tmp_path)
    document = root / tr.LEDGER_DOCUMENT
    text = document.read_text(encoding="utf-8")
    document.write_text(tr.SEAL_QUOTE.sub("0" * 64, text), encoding="utf-8", newline="\n")

    assert any("quotes" in row for row in tr.verify(root))
    tr.apply_transition(root, track_id="A1-07", event_name="master-accepted",
                        receipt_path=root / ACCEPTANCE)
    assert tr.verify(root) == []
