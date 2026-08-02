from __future__ import annotations

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import re

from earcrate.floor.model import FloorError
from earcrate.floor.release import floor_adapt_source_only_recurrence_receipt
from earcrate.floor.release_governance import (
    floor_decide_governed_release,
    floor_issue_publish_permit,
    floor_open_blind_review_campaign,
    floor_publish_release,
    floor_review_assignments,
    floor_seal_arbitration_assignment,
    floor_seal_arbitration_review,
    floor_seal_blind_review,
    floor_seal_rights_decision,
    floor_verify_published_release,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _raises(match: str):
    try:
        yield
    except FloorError as exc:
        assert re.search(match, str(exc), flags=re.IGNORECASE), f"{str(exc)!r} did not match {match!r}"
    else:
        raise AssertionError(f"expected FloorError matching {match!r}")


def _legacy_receipt(audition: bytes, master: bytes) -> dict:
    sample_rate = 1_000
    crossfade_frames = 35
    output_frames = 8_000 + 4_000 - crossfade_frames
    return {
        "schema_version": 1,
        "kind": "earcrate_source_only_recurrence_release_receipt",
        "specimen_id": "governance_fixture",
        "title": "Governed recurrence candidate",
        "source": {
            "sha256": "1" * 64,
            "decoded_pcm_sha256": "2" * 64,
            "decoded_sample_rate": sample_rate,
            "channels": 2,
            "frames": 30_000,
            "size_bytes": 1_024,
            "media_kind": "audio/wav",
        },
        "edit": {
            "prefix_seconds": [0.0, 8.0],
            "target_replaced_seconds": [8.0, 12.0],
            "donor_seconds": [20.0, 24.0],
            "prefix_bars": 8,
            "donor_bars": 4,
            "meter": "4/4",
            "crossfade_ms": 35.0,
            "crossfade_curve": "equal_power",
            "declared_operations": ["source_seek", "source_copy", "gain", "equal_power_crossfade"],
            "prohibited_operations": [
                "synthesis",
                "midi_overlay",
                "stem_layering",
                "filtered_intro",
                "beat_chopping",
                "silent_preroll",
            ],
            "source_only": True,
        },
        "metrics": {
            "first_audible_seconds": 0.0,
            "longest_silence_below_minus_55_db_seconds": 0.0,
            "integrated_loudness_lufs": -9.0,
            "true_peak_dbfs_4x": -0.5,
            "sample_peak_dbfs": -0.6,
            "target_donor_similarity": {
                "chroma_frame_cosine_mean": 0.99,
                "mel_frame_cosine_mean": 0.99,
                "onset_envelope_correlation": 0.90,
                "raw_waveform_correlation": 0.1,
            },
            "output_duration_seconds": output_frames / sample_rate,
            "output_frames": output_frames,
            "crossfade_frames": crossfade_frames,
            "applied_gain_db": -2.0,
        },
        "reproducibility": {
            "independent_build_count": 2,
            "authoritative_pcm_bit_exact": True,
            "wav_container_bit_exact": True,
            "mp3_container_bit_exact": True,
            "mp3_30s_container_bit_exact": True,
            "metrics_bit_exact": True,
        },
        "artifacts": {
            "decoded_stereo_f32le_sha256": _sha(master),
            "wav_sha256": _sha(master),
            "wav_size_bytes": len(master),
            "mp3_sha256": _sha(b"full delivery"),
            "mp3_size_bytes": len(b"full delivery"),
            "mp3_30s_sha256": _sha(audition),
            "mp3_30s_size_bytes": len(audition),
        },
        "status": {
            "custody": "passed",
            "build_reproducibility": "passed",
            "signal_sanity": "passed",
            "recurrence_identity": "passed",
            "transition_integrity": "provisional_pass",
            "musical_acceptance": "pending",
            "rights_eligibility": "not_evaluated",
            "whole_organism_status": "not_claimed",
            "release_status": "blocked",
            "summary": "signal_sane_human_review_pending",
        },
        "builder_may_not_approve_music": True,
        "receipt_sha256": "7" * 64,
    }


def _campaign() -> tuple[dict, bytes, bytes]:
    audition = b"candidate audition bytes\n"
    master = b"candidate authoritative master bytes\n"
    adapted = floor_adapt_source_only_recurrence_receipt(
        _legacy_receipt(audition, master),
        builder={"identity_id": "org.earcrate.builder", "identity_type": "provider", "version": "1.0.0"},
        signal_evaluator={"identity_id": "org.earcrate.signal", "identity_type": "evaluator", "version": "1.0.0"},
    )
    control_audition = b"untouched control audition\n"
    control_master = b"untouched control master\n"
    campaign = floor_open_blind_review_campaign(
        {
            "campaign_id": "empire-state-governance-v2-fixture",
            "candidate": adapted["release_candidate"],
            "signal_evaluation": adapted["signal_evaluation"],
            "candidate_artifact_roles": {
                "reviewed_audition": "candidate_mp3_30s",
                "authoritative_master": "candidate_wav",
            },
            "control": {
                "control_id": "untouched-source-control",
                "artifacts": [
                    {
                        "artifact_id": "control_audition",
                        "role": "reviewed_audition",
                        "sha256": _sha(control_audition),
                        "media_kind": "audio/mpeg",
                        "size_bytes": len(control_audition),
                    },
                    {
                        "artifact_id": "control_master",
                        "role": "authoritative_master",
                        "sha256": _sha(control_master),
                        "media_kind": "audio/wav",
                        "size_bytes": len(control_master),
                    },
                ],
            },
            "review_role": "reviewed_audition",
            "reviewers": [
                {"reviewer_id": "human.listener.one", "authentication_sha256": "8" * 64},
                {"reviewer_id": "human.listener.two", "authentication_sha256": "9" * 64},
                {"reviewer_id": "human.listener.three", "authentication_sha256": "a" * 64},
            ],
            "minimum_reviewers": 2,
            "review_policy": {
                "policy_id": "blind-pairwise-v1",
                "dimensions": ["transition_seamlessness", "musical_coherence"],
                "allow_abstain": True,
            },
            "blinding_seed_sha256": "b" * 64,
        }
    )
    return campaign, audition, master


def _option_for_role(campaign: dict, assignment_id: str, role: str) -> str:
    assignment = next(
        row
        for row in campaign["private_assignment_authority"]["assignments"]
        if row["assignment_id"] == assignment_id
    )
    return next(option for option, mapped in assignment["option_map"].items() if mapped == role)


def _review(campaign: dict, packet: dict, role: str) -> dict:
    return floor_seal_blind_review(
        campaign,
        {
            "assignment_id": packet["assignment_id"],
            "reviewer_id": packet["reviewer_id"],
            "authentication_sha256": packet["authentication_sha256"],
            "review_token": packet["review_token"],
            "preferred_option": _option_for_role(campaign, packet["assignment_id"], role),
            "dimensions": {
                "transition_seamlessness": 0.9,
                "musical_coherence": 0.9,
            },
            "notes": [],
        },
    )


def _rights(campaign: dict, *, expires_at: str = "2026-08-31T00:00:00Z", decided_by: str = "human.rights.reviewer") -> dict:
    return floor_seal_rights_decision(
        campaign,
        {
            "status": "accepted_by_policy",
            "policy_id": "fixture-rights-policy-v1",
            "declared_use": "private evaluation release",
            "jurisdictions": ["US"],
            "channels": ["private_download"],
            "valid_from": "2026-07-01T00:00:00Z",
            "expires_at": expires_at,
            "decided_by": decided_by,
            "authentication_sha256": "c" * 64,
            "evidence_refs": ["rights:fixture:001"],
            "legal_determination": False,
        },
    )


def test_campaign_commits_private_authority_and_independent_option_permutations() -> None:
    campaign, _, _ = _campaign()
    packets = floor_review_assignments(campaign)
    private = campaign["private_assignment_authority"]

    assert campaign["public_campaign"]["private_assignment_authority_sha256"] == private["private_assignment_authority_sha256"]
    assert len({row["review_token"] for row in packets}) == len(packets)
    assert len({tuple(sorted(row["option_map"].items())) for row in private["assignments"]}) > 1
    for packet in packets:
        assert set(option["option"] for option in packet["options"]) == {"A", "B"}
        assert "candidate" not in repr(packet["options"]).lower()
        assert "control" not in repr(packet["options"]).lower()

    tampered = deepcopy(campaign)
    original = tampered["private_assignment_authority"]["assignments"][0]["option_map"]
    tampered["private_assignment_authority"]["assignments"][0]["option_map"] = (
        {"A": "control", "B": "candidate"}
        if original["A"] == "candidate"
        else {"A": "candidate", "B": "control"}
    )
    with _raises("hash mismatch|immutable|authority"):
        floor_review_assignments(tampered)


def test_review_binds_campaign_policy_assignment_token_and_authentication() -> None:
    campaign, _, _ = _campaign()
    packet = floor_review_assignments(campaign)[0]
    review = _review(campaign, packet, "candidate")

    assert review["campaign_sha256"] == campaign["public_campaign"]["campaign_sha256"]
    assert review["candidate_sha256"] == campaign["candidate"]["candidate_sha256"]
    assert review["control_sha256"] == campaign["control"]["control_sha256"]
    assert review["review_policy_sha256"] == campaign["public_campaign"]["review_policy_sha256"]
    assert review["private_assignment_authority_sha256"] == campaign["private_assignment_authority"]["private_assignment_authority_sha256"]

    wrong_auth = deepcopy(packet)
    wrong_auth["authentication_sha256"] = "0" * 64
    with _raises("authentication"):
        _review(campaign, wrong_auth, "candidate")

    wrong_token = deepcopy(packet)
    wrong_token["review_token"] = "0" * 64
    with _raises("token"):
        _review(campaign, wrong_token, "candidate")

    mutated = deepcopy(review)
    mutated["preferred_option"] = "B" if review["preferred_option"] == "A" else "A"
    with _raises("hash mismatch|mutated"):
        floor_seal_blind_review(campaign, mutated)


def test_split_vote_requires_sealed_independent_arbitration() -> None:
    campaign, _, _ = _campaign()
    packets = floor_review_assignments(campaign)
    split = [_review(campaign, packets[0], "candidate"), _review(campaign, packets[1], "control")]

    blocked = floor_decide_governed_release(
        campaign,
        split,
        _rights(campaign),
        as_of="2026-07-31T00:00:00Z",
    )
    assert blocked["status"] == "blocked"
    assert blocked["summary"] == "needs_arbitration"

    with _raises("independent"):
        floor_seal_arbitration_assignment(
            campaign,
            split,
            {
                "arbitrator_id": "org.earcrate.builder",
                "authentication_sha256": "d" * 64,
                "reason": "split review",
            },
        )

    assignment = floor_seal_arbitration_assignment(
        campaign,
        split,
        {
            "arbitrator_id": "human.arbitrator",
            "authentication_sha256": "d" * 64,
            "reason": "split review",
        },
    )
    arbitration = floor_seal_arbitration_review(
        campaign,
        assignment,
        {
            "arbitrator_id": "human.arbitrator",
            "authentication_sha256": "d" * 64,
            "verdict": "candidate",
            "notes": ["candidate preserves the phrase transition more convincingly"],
        },
    )
    eligible = floor_decide_governed_release(
        campaign,
        split,
        _rights(campaign),
        as_of="2026-07-31T00:00:00Z",
        arbitration_review=arbitration,
    )
    assert eligible["status"] == "eligible"
    assert eligible["release_eligible"] is True
    assert eligible["arbitration_review_sha256"] == arbitration["arbitration_review_sha256"]


def test_rights_are_use_scoped_time_bounded_and_independent() -> None:
    campaign, _, _ = _campaign()
    packets = floor_review_assignments(campaign)
    reviews = [_review(campaign, packets[0], "candidate"), _review(campaign, packets[1], "candidate")]

    with _raises("independent"):
        _rights(campaign, decided_by="human.listener.one")

    expired = floor_decide_governed_release(
        campaign,
        reviews,
        _rights(campaign, expires_at="2026-07-15T00:00:00Z"),
        as_of="2026-07-31T00:00:00Z",
    )
    assert expired["status"] == "refused"
    assert expired["summary"] == "rights_expired"

    eligible = floor_decide_governed_release(
        campaign,
        reviews,
        _rights(campaign),
        as_of="2026-07-31T00:00:00Z",
    )
    assert eligible["rights"]["declared_use"] == "private evaluation release"
    assert eligible["rights"]["jurisdictions"] == ["US"]
    assert eligible["rights"]["expires_at"] == "2026-08-31T00:00:00Z"


def _eligible(campaign: dict) -> dict:
    packets = floor_review_assignments(campaign)
    return floor_decide_governed_release(
        campaign,
        [_review(campaign, packets[0], "candidate"), _review(campaign, packets[1], "candidate")],
        _rights(campaign),
        as_of="2026-07-31T00:00:00Z",
    )


def test_format_neutral_permit_and_atomic_publication_receipt(tmp_path: Path) -> None:
    campaign, audition, master = _campaign()
    decision = _eligible(campaign)
    permit = floor_issue_publish_permit(
        campaign,
        decision,
        [
            {"artifact_id": "candidate_mp3_30s", "role": "reviewed_audition", "output_name": "audition-preview.mp3"},
            {"artifact_id": "candidate_wav", "role": "authoritative_master", "output_name": "master-delivery.wav"},
        ],
        issued_at="2026-07-31T01:00:00Z",
        expires_at="2026-08-15T00:00:00Z",
    )
    audition_path = tmp_path / "candidate.audition"
    master_path = tmp_path / "candidate.master"
    audition_path.write_bytes(audition)
    master_path.write_bytes(master)
    output = tmp_path / "published-release"

    published = floor_publish_release(
        permit,
        artifact_paths={
            "candidate_mp3_30s": audition_path,
            "candidate_wav": master_path,
        },
        output_dir=output,
        published_at="2026-07-31T02:00:00Z",
    )
    assert published["complete"] is True
    assert (output / "audition-preview.mp3").read_bytes() == audition
    assert (output / "master-delivery.wav").read_bytes() == master
    assert (output / "publication-receipt.json").is_file()
    assert not any(path.name.startswith(".published-release.staging-") for path in tmp_path.iterdir())
    assert floor_verify_published_release(output)["publication_receipt_sha256"] == published["publication_receipt_sha256"]

    (output / "master-delivery.wav").write_bytes(master + b"mutation")
    with _raises("custody|changed"):
        floor_verify_published_release(output)


def test_publication_refuses_unreviewed_bytes_and_leaves_no_partial_directory(tmp_path: Path) -> None:
    campaign, audition, master = _campaign()
    permit = floor_issue_publish_permit(
        campaign,
        _eligible(campaign),
        [
            {"artifact_id": "candidate_mp3_30s", "role": "reviewed_audition", "output_name": "audition.bin"},
            {"artifact_id": "candidate_wav", "role": "authoritative_master", "output_name": "master.bin"},
        ],
        issued_at="2026-07-31T01:00:00Z",
        expires_at="2026-08-15T00:00:00Z",
    )
    audition_path = tmp_path / "audition"
    master_path = tmp_path / "master"
    audition_path.write_bytes(audition)
    master_path.write_bytes(master + b"post-review mutation")
    output = tmp_path / "must-not-exist"

    with _raises("changed"):
        floor_publish_release(
            permit,
            artifact_paths={"candidate_mp3_30s": audition_path, "candidate_wav": master_path},
            output_dir=output,
            published_at="2026-07-31T02:00:00Z",
        )
    assert not output.exists()
    assert not any(path.name.startswith(".must-not-exist.staging-") for path in tmp_path.iterdir())

    if hasattr(Path, "symlink_to"):
        master_path.write_bytes(master)
        link = tmp_path / "master-link"
        try:
            link.symlink_to(master_path)
        except (OSError, NotImplementedError):
            return
        with _raises("symlink"):
            floor_publish_release(
                permit,
                artifact_paths={"candidate_mp3_30s": audition_path, "candidate_wav": link},
                output_dir=output,
                published_at="2026-07-31T02:00:00Z",
            )
        assert not output.exists()
