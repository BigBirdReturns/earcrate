from __future__ import annotations

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import re

from earcrate.floor.model import FloorError
from earcrate.floor.release_governance import (
    floor_decide_governed_release,
    floor_issue_publish_permit,
    floor_open_blind_review_campaign,
    floor_publish_release,
    floor_review_assignments,
    floor_seal_blind_review,
    floor_seal_rights_decision,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _raises(match: str):
    try:
        yield
    except FloorError as exc:
        assert re.search(match, str(exc)), f"{str(exc)!r} did not match {match!r}"
    else:
        raise AssertionError(f"expected FloorError matching {match!r}")


def _campaign(tmp_path: Path) -> tuple[dict, bytes, bytes]:
    audition = b"candidate audition bytes\n"
    master = b"candidate master bytes\n"
    campaign = floor_open_blind_review_campaign(
        {
            "campaign_id": "empire-state-governance-v2-fixture",
            "candidate": {
                "candidate_sha256": "a" * 64,
                "builder_identity_id": "org.earcrate.builder",
                "audition_sha256": _sha(audition),
                "master_sha256": _sha(master),
            },
            "signal_evaluation": {
                "signal_evaluation_sha256": "b" * 64,
                "candidate_sha256": "a" * 64,
                "evaluator_identity_id": "org.earcrate.signal",
                "status": "passed",
            },
            "control": {
                "control_sha256": "c" * 64,
                "audition_sha256": _sha(b"untouched control audition\n"),
                "master_sha256": _sha(b"untouched control master\n"),
            },
            "reviewer_ids": ["human.listener.one", "human.listener.two"],
            "minimum_reviewers": 2,
        }
    )
    return campaign, audition, master


def _reviews(campaign: dict, preferred_private_role: str) -> list[dict]:
    option = next(
        label
        for label, role in campaign["private_option_map"].items()
        if role == preferred_private_role
    )
    return [
        floor_seal_blind_review(
            campaign,
            {
                "review_token": assignment["review_token"],
                "reviewer_id": assignment["reviewer_id"],
                "preferred_option": option,
                "dimensions": {
                    "transition_seamlessness": 0.9,
                    "musical_coherence": 0.9,
                },
            },
        )
        for assignment in floor_review_assignments(campaign)
    ]


def _rights(campaign: dict) -> dict:
    return floor_seal_rights_decision(
        campaign,
        {
            "status": "accepted_by_policy",
            "policy_id": "fixture-rights-policy",
            "decided_by": "human.rights.reviewer",
            "legal_determination": False,
        },
    )


def test_campaign_assignments_are_blinded_and_independent(tmp_path: Path) -> None:
    campaign, _, _ = _campaign(tmp_path)
    assignments = floor_review_assignments(campaign)

    assert len(assignments) == 2
    assert len({row["review_token"] for row in assignments}) == 2
    assert len({row["reviewer_id"] for row in assignments}) == 2
    for assignment in assignments:
        assert set(assignment["options"]) == {"A", "B"}
        assert "candidate" not in repr(assignment).lower()
        assert "control" not in repr(assignment).lower()


def test_builder_and_signal_evaluator_cannot_be_reviewers(tmp_path: Path) -> None:
    campaign, _, _ = _campaign(tmp_path)
    raw = {
        "campaign_id": campaign["campaign_id"],
        "candidate": campaign["candidate"],
        "signal_evaluation": campaign["signal_evaluation"],
        "control": campaign["control"],
        "reviewer_ids": ["org.earcrate.builder", "human.listener.two"],
        "minimum_reviewers": 2,
    }
    with _raises("builder|independent"):
        floor_open_blind_review_campaign(raw)

    raw["reviewer_ids"] = ["org.earcrate.signal", "human.listener.two"]
    with _raises("signal|independent"):
        floor_open_blind_review_campaign(raw)


def test_quorum_duplicates_and_post_commit_review_mutation_are_refused(tmp_path: Path) -> None:
    campaign, _, _ = _campaign(tmp_path)
    reviews = _reviews(campaign, "candidate")

    pending = floor_decide_governed_release(campaign, reviews[:1], rights_decision=None)
    assert pending["status"] == "blocked"
    assert pending["summary"] == "review_quorum_pending"

    with _raises("duplicate|one immutable"):
        floor_decide_governed_release(campaign, [reviews[0], reviews[0]], rights_decision=None)

    mutated = deepcopy(reviews[0])
    mutated["preferred_option"] = "B" if mutated["preferred_option"] == "A" else "A"
    with _raises("hash|immutable|tamper"):
        floor_decide_governed_release(campaign, [mutated, reviews[1]], rights_decision=None)


def test_split_vote_and_control_preference_do_not_force_an_edit(tmp_path: Path) -> None:
    campaign, _, _ = _campaign(tmp_path)
    candidate_reviews = _reviews(campaign, "candidate")
    control_reviews = _reviews(campaign, "control")

    split = floor_decide_governed_release(
        campaign,
        [candidate_reviews[0], control_reviews[1]],
        rights_decision=_rights(campaign),
    )
    assert split["status"] == "blocked"
    assert split["summary"] == "needs_arbitration"
    assert split["release_eligible"] is False

    control = floor_decide_governed_release(
        campaign,
        control_reviews,
        rights_decision=_rights(campaign),
    )
    assert control["status"] == "refused"
    assert control["summary"] == "no_edit_preferred"
    assert control["release_eligible"] is False


def test_rights_authority_is_separate_and_required(tmp_path: Path) -> None:
    campaign, _, _ = _campaign(tmp_path)
    reviews = _reviews(campaign, "candidate")

    pending = floor_decide_governed_release(campaign, reviews, rights_decision=None)
    assert pending["status"] == "blocked"
    assert pending["summary"] == "rights_review_pending"

    for forbidden in ("org.earcrate.builder", "org.earcrate.signal", "human.listener.one"):
        with _raises("rights|separate|independent"):
            floor_seal_rights_decision(
                campaign,
                {
                    "status": "accepted_by_policy",
                    "policy_id": "fixture-rights-policy",
                    "decided_by": forbidden,
                    "legal_determination": False,
                },
            )

    accepted = floor_decide_governed_release(campaign, reviews, rights_decision=_rights(campaign))
    assert accepted["status"] == "accepted"
    assert accepted["summary"] == "release_eligible"
    assert accepted["release_eligible"] is True


def test_publish_permit_binds_exact_reviewed_bytes_and_scope(tmp_path: Path) -> None:
    campaign, audition, master = _campaign(tmp_path)
    decision = floor_decide_governed_release(
        campaign,
        _reviews(campaign, "candidate"),
        rights_decision=_rights(campaign),
    )
    permit = floor_issue_publish_permit(
        campaign,
        decision,
        publication_scope=["accepted-audition.mp3", "accepted-master.mp3"],
    )

    audition_path = tmp_path / "audition.mp3"
    master_path = tmp_path / "master.mp3"
    audition_path.write_bytes(audition)
    master_path.write_bytes(master)
    published = floor_publish_release(
        permit,
        audition_path=audition_path,
        master_path=master_path,
        output_dir=tmp_path / "release",
    )
    assert set(published["files"]) == {
        "accepted-audition.mp3",
        "accepted-master.mp3",
        "publication-manifest.json",
        "release-permit.json",
        "SHA256SUMS",
    }
    assert (tmp_path / "release" / "accepted-audition.mp3").read_bytes() == audition
    assert (tmp_path / "release" / "accepted-master.mp3").read_bytes() == master

    master_path.write_bytes(master + b"post-review mutation")
    with _raises("hash|mutat|reviewed"):
        floor_publish_release(
            permit,
            audition_path=audition_path,
            master_path=master_path,
            output_dir=tmp_path / "mutated-release",
        )
