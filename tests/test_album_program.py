from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "configs" / "album_one" / "manifest.v1.json"


def _load() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _seal(payload: dict) -> str:
    body = dict(payload)
    body.pop("manifest_sha256", None)
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_album_one_manifest_is_sealed() -> None:
    manifest = _load()
    assert manifest["kind"] == "earcrate_album_program"
    assert manifest["schema_version"] == 1
    assert manifest["manifest_sha256"] == _seal(manifest)


def test_album_one_is_the_append_only_seven_track_commission() -> None:
    manifest = _load()
    tracks = manifest["tracks"]
    assert manifest["commission_order_is_append_only"] is True
    assert [row["track_id"] for row in tracks] == [
        "A1-01",
        "A1-02",
        "A1-03",
        "A1-04",
        "A1-05",
        "A1-06",
        "A1-07",
    ]
    assert [row["commission_order"] for row in tracks] == list(range(1, 8))
    assert manifest["active_track_id"] == "A1-07"
    assert manifest["repository_contract"]["new_work_must_declare"] == [
        "album_scope",
        "musical_gap",
        "control_or_baseline",
        "owner_audition_effect",
        "private_execution_required",
    ]
    assert [row["track_id"] for row in tracks if row["status"]["active"]] == ["A1-07"]


def test_album_one_completion_ledger_cannot_claim_music_we_rejected() -> None:
    """Counters are derived, and an acceptance must be backed by a landed receipt.

    This gate used to assert both counters were zero. That was true and useless at
    the same time: it would fail the moment a real master was accepted, and it never
    checked that an acceptance was evidenced by anything. What actually needs
    protecting is that no track can claim acceptance without an owner verdict that
    names the same mastered object, and that the autonomy claim never runs ahead of
    the album claim.
    """
    manifest = _load()
    tracks = manifest["tracks"]
    accepted = [row for row in tracks if row["status"]["album_master"] == "accepted"]
    complete = [row for row in tracks if row["status"]["system_reference"] == "complete"]

    assert manifest["completed_album_master_count"] == len(accepted)
    assert manifest["completed_system_reference_count"] == len(complete)

    for row in tracks:
        if row["status"]["album_master"] != "accepted":
            assert row["status"]["human_acceptance"] is False, (
                f"{row['track_id']} reports human acceptance without an accepted master")
            assert "accepted_master" not in row, (
                f"{row['track_id']} carries an accepted master it has not accepted")
            continue

        assert row["status"]["human_acceptance"] is True, (
            f"{row['track_id']} is accepted but records no human acceptance")
        master = row["accepted_master"]
        landed = [json.loads((ROOT / relative).read_text(encoding="utf-8"))
                  for relative in row["repo_evidence"] if relative.endswith(".public.json")]
        # Only an acceptance receipt can support the claim. A qualification receipt
        # describes machine evidence and can never carry an owner verdict.
        receipts = [value for value in landed
                    if str(value.get("kind", "")).endswith("master_acceptance_receipt")
                    and value.get("receipt_sha256") == master["acceptance_receipt_sha256"]]
        assert receipts, (
            f"{row['track_id']} claims acceptance with no landed acceptance receipt")
        receipt = receipts[0]
        assert receipt["verdict"] == "ACCEPT_MASTER"
        assert receipt["state"]["accepted_album_master"] is True
        assert receipt["audited_object"]["canonical_pcm_sha256"] == (
            master["canonical_pcm_sha256"]), (
            "the ledger and the acceptance receipt name different masters")
        assert receipt["state"]["system_reference_complete"] is (
            row["status"]["system_reference"] == "complete")

    for row in complete:
        assert row["status"]["album_master"] == "accepted", (
            f"{row['track_id']} completes a system reference with no accepted master")


def test_a_qualified_master_is_not_an_accepted_one() -> None:
    """The distinction this ledger exists to hold.

    A deterministic, compliant, exactly reproducible master is machine evidence. It
    says nothing about whether anyone has heard it. The tempting shortcut is that a
    linear gain of a known size makes the mastered object a transparent function of
    an already accepted render -- but that replaces a listening decision with an
    inference, so `master_qualified` may never imply `master_accepted`.
    """
    manifest = _load()
    states = manifest["completion_model"]["master_states"]
    assert states == ["frontier_selected", "master_qualified", "master_accepted"]

    for row in manifest["tracks"]:
        qualification = row.get("master_qualification")
        if not qualification:
            continue
        assert qualification["master_state"] in states

        # The ledger's pointer at its qualification receipt must resolve. A sealed
        # identity that names nothing is how a document stops describing its object,
        # and this lane has already been bitten by exactly that.
        landed_receipts = {
            json.loads((ROOT / relative).read_text(encoding="utf-8")).get("receipt_sha256")
            for relative in row["repo_evidence"] if relative.endswith(".public.json")}
        assert qualification["receipt_sha256"] in landed_receipts, (
            f"{row['track_id']} names a qualification receipt that no landed file carries")

        if qualification["master_state"] != "master_accepted":
            assert qualification["owner_master_acceptance"] is False
            assert row["status"]["album_master"] == "unaccepted", (
                f"{row['track_id']} is accepted on a master that was never auditioned")
            assert row["status"]["human_acceptance"] is False

        # A qualification receipt must not claim anywhere that it accepted anything.
        landed = [json.loads((ROOT / relative).read_text(encoding="utf-8"))
                  for relative in row["repo_evidence"] if relative.endswith(".public.json")]
        for value in landed:
            if not str(value.get("kind", "")).endswith("public_master_receipt"):
                continue
            assert value["state"]["accepted_album_master"] is False, (
                "a qualification receipt claimed an owner acceptance")
            assert value["state"]["accepted_album_masters"] == 0
            assert value["state"]["owner_master_acceptance"] is False
            assert value["review"]["post_master_audition_complete"] is False


def test_every_landed_album_receipt_still_validates_its_own_seal() -> None:
    """A sealed public receipt that no longer matches its body is not evidence."""
    directory = ROOT / "proofs" / "album_one"
    receipts = sorted(directory.glob("*.public.json"))
    assert receipts, "the album program lands its public evidence here"
    for path in receipts:
        value = json.loads(path.read_text(encoding="utf-8"))
        field = next((name for name in ("receipt_sha256", "addendum_sha256") if name in value), "")
        assert field, f"{path.name} carries no seal"
        claimed = value[field]
        body = {key: item for key, item in value.items() if key != field}
        assert claimed == hashlib.sha256(json.dumps(
            body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest(), f"{path.name} seal mismatch"


def test_every_album_track_has_a_musical_contract_and_next_control() -> None:
    manifest = _load()
    for row in manifest["tracks"]:
        assert row["reference_class"].strip()
        assert row["musical_objective"].strip()
        assert row["control_question"].strip().endswith("?")
        assert row["next_gate"].strip()
        assert row["source_requirements"]
        assert row["status"]["album_master"] in {"unaccepted", "accepted"}
        assert row["status"]["system_reference"] in {"incomplete", "complete"}


def test_answer_keys_remain_calibration_and_do_not_inflate_the_album() -> None:
    manifest = _load()
    album_ids = {row["track_id"] for row in manifest["tracks"]}
    answer_keys = manifest["answer_key_corpora"]
    assert len(answer_keys) == 8
    assert all(row["role"] == "calibration_only" for row in answer_keys)
    assert album_ids.isdisjoint({row["id"] for row in answer_keys})


def test_repository_front_door_names_album_one_as_the_program() -> None:
    manifest = _load()
    counter = f"{manifest['completed_album_master_count']}/{len(manifest['tracks'])}"
    required = {
        "README.md": ("Album One", "A1-07", counter),
        "AGENTS.md": ("Album One", "album_scope", counter),
        "PRODUCT.md": ("Album One", counter),
        "MILESTONES.md": ("Album One", "Beggin", counter),
        "README_FIRST.txt": ("ALBUM_ONE.md", "Album One", counter),
        # The ledger document must quote the seal of the manifest it claims to
        # describe. A stale quoted seal is how a document silently stops being about
        # the object it names.
        "ALBUM_ONE.md": ("Album One", counter, manifest["manifest_sha256"]),
    }
    for relative, needles in required.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{relative} does not surface {needle!r}"
