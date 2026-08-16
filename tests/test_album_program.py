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
    manifest = _load()
    tracks = manifest["tracks"]
    accepted_masters = sum(row["status"]["album_master"] == "accepted" for row in tracks)
    completed_references = sum(row["status"]["system_reference"] == "complete" for row in tracks)
    assert manifest["completed_album_master_count"] == accepted_masters == 0
    assert manifest["completed_system_reference_count"] == completed_references == 0
    assert all(row["status"]["human_acceptance"] is False for row in tracks)


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
    required = {
        "README.md": ("Album One", "A1-07"),
        "AGENTS.md": ("Album One", "album_scope"),
        "PRODUCT.md": ("Album One", "0/7"),
        "MILESTONES.md": ("Album One", "Beggin"),
        "README_FIRST.txt": ("ALBUM_ONE.md", "Album One"),
    }
    for relative, needles in required.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{relative} does not surface {needle!r}"
