from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from earcrate.floor.model import FloorError, floor_read_json
from earcrate.floor.release import (
    floor_adapt_source_only_recurrence_receipt,
    floor_build_release_gate,
    floor_release_profile_capability,
    floor_release_review_template,
    floor_seal_audio_edit_plan,
    floor_seal_human_musical_review,
    floor_seal_release_candidate,
    floor_seal_signal_evaluation,
    floor_verify_release_object,
)
from earcrate.floor.schema import floor_schema_bundle


def _legacy_receipt() -> dict:
    sample_rate = 1_000
    prefix = (0.0, 8.0)
    target = (8.0, 12.0)
    donor = (20.0, 24.0)
    crossfade_frames = 35
    output_frames = 8_000 + 4_000 - crossfade_frames
    return {
        "schema_version": 1,
        "kind": "earcrate_source_only_recurrence_release_receipt",
        "specimen_id": "synthetic_release_fixture",
        "title": "Synthetic source-only recurrence candidate",
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
            "prefix_seconds": list(prefix),
            "target_replaced_seconds": list(target),
            "donor_seconds": list(donor),
            "prefix_bars": 8,
            "donor_bars": 4,
            "meter": "4/4",
            "crossfade_ms": 35.0,
            "crossfade_curve": "equal_power",
            "declared_operations": ["source_seek", "source_copy", "gain", "equal_power_crossfade"],
            "prohibited_operations": [
                "synthesis", "midi_overlay", "stem_layering", "filtered_intro", "beat_chopping", "silent_preroll"
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
            "decoded_stereo_f32le_sha256": "3" * 64,
            "wav_sha256": "4" * 64,
            "wav_size_bytes": 9_000,
            "mp3_sha256": "5" * 64,
            "mp3_size_bytes": 2_000,
            "mp3_30s_sha256": "6" * 64,
            "mp3_30s_size_bytes": 1_900,
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


def _adapt() -> dict:
    return floor_adapt_source_only_recurrence_receipt(
        _legacy_receipt(),
        builder={
            "identity_id": "org.test.builder",
            "identity_type": "provider",
            "version": "1.0.0",
        },
        signal_evaluator={
            "identity_id": "org.test.signal",
            "identity_type": "evaluator",
            "version": "1.0.0",
        },
    )


def _human(candidate: dict, verdict: str, reviewer_id: str = "human.reviewer") -> dict:
    return floor_seal_human_musical_review(
        {
            "candidate_sha256": candidate["candidate_sha256"],
            "reviewer": {
                "reviewer_id": reviewer_id,
                "reviewer_type": "human",
                "display_name": "Fixture reviewer",
            },
            "verdict": verdict,
            "dimensions": {"seam": 0.9, "groove": 0.8, "phrase": 0.9},
            "notes": ["fixture review"] if verdict == "revise" else [],
            "review_patch_refs": ["review_patch_fixture"] if verdict == "revise" else [],
            "listening_context": {"blind": True},
            "machine_generated": False,
        },
        candidate,
    )


def test_release_profile_adapts_recurrence_into_blocked_review_candidate() -> None:
    adapted = _adapt()
    candidate = adapted["release_candidate"]
    gate = adapted["release_gate"]
    assert candidate["builder_may_not_approve_music"] is True
    assert candidate["audio_edit_plan"]["source_only"] is True
    assert candidate["audio_edit_plan"]["transitions"][0]["overlap_frames"] == 35
    assert [row["mode"] for row in candidate["time_map"]["segments"]] == ["continuous", "jump"]
    assert adapted["signal_evaluation"]["evaluator"]["identity_id"] != candidate["builder"]["identity_id"]
    assert gate["release_allowed"] is False
    assert gate["status"]["summary"] == "signal_sane_human_review_pending"
    assert gate["status"]["signal_sanity"] == "passed"
    assert gate["status"]["musical_acceptance"] == "pending"
    assert "human musical acceptance is pending" in gate["blockers"]


def test_audio_edit_plan_refuses_gaps_undeclared_overlap_and_prohibited_operations() -> None:
    plan = deepcopy(_adapt()["audio_edit_plan"])
    for field in ("edit_plan_sha256", "edit_plan_id"):
        plan.pop(field, None)
    plan["segments"][1]["output_start_frame"] += 100
    try:
        floor_seal_audio_edit_plan(plan)
    except FloorError as exc:
        assert "gap" in str(exc) or "overlap disagrees" in str(exc)
    else:
        raise AssertionError("AudioEditPlan accepted an uncovered output gap")

    plan = deepcopy(_adapt()["audio_edit_plan"])
    for field in ("edit_plan_sha256", "edit_plan_id"):
        plan.pop(field, None)
    plan["transitions"] = []
    try:
        floor_seal_audio_edit_plan(plan)
    except FloorError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("AudioEditPlan accepted undeclared sample overlap")

    plan = deepcopy(_adapt()["audio_edit_plan"])
    for field in ("edit_plan_sha256", "edit_plan_id"):
        plan.pop(field, None)
    plan["segments"][0]["operation"] = "synthesis"
    plan["declared_operations"].append("synthesis")
    try:
        floor_seal_audio_edit_plan(plan)
    except FloorError as exc:
        assert "prohibit" in str(exc) or "source-only" in str(exc)
    else:
        raise AssertionError("source-only AudioEditPlan accepted synthesis")


def test_candidate_builder_cannot_approve_music_or_open_release() -> None:
    candidate = deepcopy(_adapt()["release_candidate"])
    candidate.pop("candidate_sha256", None)
    candidate["status"]["musical_acceptance"] = "accept"
    try:
        floor_seal_release_candidate(candidate)
    except FloorError as exc:
        assert "builder" in str(exc) and "acceptance" in str(exc)
    else:
        raise AssertionError("candidate builder self-approved musical acceptance")

    candidate = deepcopy(_adapt()["release_candidate"])
    candidate.pop("candidate_sha256", None)
    candidate["status"]["release_status"] = "approved"
    try:
        floor_seal_release_candidate(candidate)
    except FloorError as exc:
        assert "release gate" in str(exc)
    else:
        raise AssertionError("candidate builder opened its own release gate")


def test_signal_and_human_evaluation_identities_are_independent() -> None:
    adapted = _adapt()
    candidate = adapted["release_candidate"]
    signal = deepcopy(adapted["signal_evaluation"])
    signal.pop("signal_evaluation_sha256", None)
    signal["evaluator"]["identity_id"] = candidate["builder"]["identity_id"]
    try:
        floor_seal_signal_evaluation(signal, candidate)
    except FloorError as exc:
        assert "independent signal evaluator" in str(exc)
    else:
        raise AssertionError("candidate builder evaluated its own signal")

    template = floor_release_review_template(candidate, reviewer_id="human.reviewer")
    assert template["verdict"] == "pending" and template["machine_generated"] is False

    machine = deepcopy(template)
    machine.pop("human_review_sha256", None)
    machine["machine_generated"] = True
    try:
        floor_seal_human_musical_review(machine, candidate)
    except FloorError as exc:
        assert "machine-generated" in str(exc)
    else:
        raise AssertionError("machine output masqueraded as human review")

    self_review = deepcopy(template)
    self_review.pop("human_review_sha256", None)
    self_review["reviewer"]["reviewer_id"] = candidate["builder"]["identity_id"]
    self_review["verdict"] = "accept"
    try:
        floor_seal_human_musical_review(self_review, candidate)
    except FloorError as exc:
        assert "self-approve" in str(exc)
    else:
        raise AssertionError("candidate builder self-approved as human reviewer")


def test_release_gate_requires_custody_repro_signal_human_and_rights() -> None:
    adapted = _adapt()
    candidate = adapted["release_candidate"]
    signal = adapted["signal_evaluation"]
    accepted = _human(candidate, "accept")

    rights_pending = floor_build_release_gate(
        candidate,
        signal_evaluations=[signal],
        human_reviews=[accepted],
        custody={"status": "passed"},
        reproducibility={"status": "passed"},
        rights={"status": "not_evaluated"},
    )
    assert rights_pending["release_allowed"] is False
    assert rights_pending["status"]["summary"] == "rights_review_pending"

    approved = floor_build_release_gate(
        candidate,
        signal_evaluations=[signal],
        human_reviews=[accepted],
        custody={"status": "passed"},
        reproducibility={"status": "passed"},
        rights={
            "status": "accepted_by_policy",
            "policy_id": "fixture_policy",
            "decided_by": "human.rights.reviewer",
            "legal_determination": False,
        },
    )
    assert approved["release_allowed"] is True
    assert approved["status"]["release_status"] == "approved"
    assert approved["status"]["summary"] == "release_approved"
    assert approved["whole_organism_passed"] is False

    try:
        floor_build_release_gate(
            candidate,
            signal_evaluations=[signal],
            human_reviews=[accepted],
            custody={"status": "passed"},
            reproducibility={"status": "passed"},
            rights={
                "status": "accepted_by_policy",
                "policy_id": "fixture_policy",
                "decided_by": "human.rights.reviewer",
                "legal_determination": True,
            },
        )
    except FloorError as exc:
        assert "legal determination" in str(exc)
    else:
        raise AssertionError("release gate laundered a rights assertion into legal clearance")


def test_human_revision_and_rejection_remain_distinct() -> None:
    adapted = _adapt()
    candidate = adapted["release_candidate"]
    signal = adapted["signal_evaluation"]
    common = {
        "signal_evaluations": [signal],
        "custody": {"status": "passed"},
        "reproducibility": {"status": "passed"},
        "rights": {
            "status": "accepted_by_policy",
            "policy_id": "fixture_policy",
            "decided_by": "human.rights.reviewer",
        },
    }
    revise = floor_build_release_gate(candidate, human_reviews=[_human(candidate, "revise")], **common)
    reject = floor_build_release_gate(candidate, human_reviews=[_human(candidate, "reject")], **common)
    assert revise["release_allowed"] is False
    assert revise["status"]["summary"] == "human_revision_requested"
    assert revise["status"]["release_status"] == "blocked"
    assert reject["status"]["summary"] == "human_rejected"
    assert reject["status"]["release_status"] == "rejected"


def test_release_schemas_and_fixture_are_committed_without_source_media() -> None:
    bundle = floor_schema_bundle()
    expected = {
        "earcrate_floor_audio_edit_plan_v1.schema.json",
        "earcrate_floor_release_candidate_v1.schema.json",
        "earcrate_floor_signal_evaluation_v1.schema.json",
        "earcrate_floor_human_musical_review_v1.schema.json",
        "earcrate_floor_release_gate_receipt_v1.schema.json",
    }
    assert expected.issubset(bundle)
    root = Path(__file__).resolve().parent.parent
    for name in expected:
        assert (root / "schemas" / name).is_file()

    fixture = root / "proofs" / "specimens" / "pretty_lights_empire_release_candidate_v1"
    proof = floor_read_json(fixture / "proof.json")
    candidate = floor_verify_release_object(floor_read_json(fixture / "release_candidate.json"))
    signal = floor_verify_release_object(floor_read_json(fixture / "signal_evaluation.json"))
    gate = floor_verify_release_object(floor_read_json(fixture / "release_gate.pending.json"))
    assert proof["source_media_committed"] is False
    assert proof["candidate_sha256"] == candidate["candidate_sha256"]
    assert proof["signal_evaluation_sha256"] == signal["signal_evaluation_sha256"]
    assert proof["release_gate_sha256"] == gate["release_gate_sha256"]
    assert gate["status"]["summary"] == "signal_sane_human_review_pending"
    assert gate["release_allowed"] is False
    assert not any(path.suffix.lower() in {".wav", ".mp3", ".flac"} for path in fixture.iterdir())


def test_release_profile_package_and_single_file_surfaces(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    fixture = root / "proofs" / "specimens" / "pretty_lights_empire_release_candidate_v1"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    package = subprocess.run(
        [sys.executable, "-m", "earcrate", "floor", "release-capability"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    package_payload = json.loads(package.stdout)
    assert package_payload["human_musical_review_required"] is True

    adapted_dir = tmp_path / "adapted"
    adapted = subprocess.run(
        [
            sys.executable, "-m", "earcrate", "floor", "release-adapt-recurrence",
            str(fixture / "receipt.json"), str(adapted_dir),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert adapted.returncode == 0, adapted.stdout + adapted.stderr
    adapted_payload = json.loads(adapted.stdout)
    assert adapted_payload["status"]["summary"] == "signal_sane_human_review_pending"

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    single = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "floor", "release-capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert single.returncode == 0, single.stdout + single.stderr
    single_payload = json.loads(single.stdout)
    assert package_payload["capability_sha256"] == single_payload["capability_sha256"]


def test_release_gate_verifier_refuses_hash_valid_authority_laundering() -> None:
    adapted = _adapt()
    gate = deepcopy(adapted["release_gate"])
    gate.pop("release_gate_sha256", None)
    gate["release_allowed"] = True
    gate["status"]["release_status"] = "approved"
    gate["status"]["summary"] = "release_approved"
    gate["blockers"] = []
    try:
        floor_verify_release_object(gate)
    except FloorError as exc:
        assert "mandatory promotion conditions" in str(exc)
    else:
        raise AssertionError("ReleaseGateReceipt accepted approval without human and rights evidence")


def test_release_capability_refuses_signal_as_artistic_authority() -> None:
    capability = floor_release_profile_capability()
    assert capability["candidate_builder_may_approve_music"] is False
    assert capability["signal_evaluation_is_musical_acceptance"] is False
    assert capability["human_musical_review_required"] is True
    assert capability["rights_policy_acceptance_required"] is True
    assert capability["whole_organism_passage_implied"] is False
