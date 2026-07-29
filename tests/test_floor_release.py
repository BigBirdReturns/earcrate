from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from earcrate.floor.model import (
    FloorError,
    floor_seal_conformance_report,
    floor_seal_evaluation_ledger,
    floor_seal_time_map,
    floor_write_json_atomic,
)
from earcrate.floor.release import (
    floor_build_release_gate,
    floor_human_review_request,
    floor_seal_human_musical_review,
    floor_seal_release_candidate,
    floor_seal_rights_review,
)
from earcrate.floor.schema import floor_schema_bundle

SHA = "1" * 64
MANIFEST_SHA = "2" * 64
REQUEST_SHA = "3" * 64
RESULT_SHA = "4" * 64
SEMANTIC_SHA = "5" * 64
RECEIPT_SHA = "6" * 64


def _conformance() -> dict:
    return floor_seal_conformance_report(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_conformance_report",
            "requested_runs": 2,
            "completed_runs": 2,
            "runs": [
                {"index": 0, "result_sha256": RESULT_SHA, "semantic_result_sha256": SEMANTIC_SHA},
                {"index": 1, "result_sha256": RESULT_SHA, "semantic_result_sha256": SEMANTIC_SHA},
            ],
            "failures": [],
            "checks": {
                "request_custody_verified": True,
                "result_schema_accepted": True,
                "output_artifacts_contained": True,
                "output_artifact_identities_verified": True,
                "repeatability_checked": True,
                "semantic_result_repeatable": True,
                "network_policy_declaration_checked": True,
                "os_network_sandbox_proved": False,
            },
            "complete": True,
            "quality_claimed": False,
            "selection_authority": False,
        }
    )


def _candidate() -> dict:
    conformance = _conformance()
    return floor_seal_release_candidate(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_release_candidate",
            "candidate_id": "fixture_release_candidate",
            "builder": {
                "provider_id": "org.test.builder",
                "provider_version": "1.0.0",
                "provider_manifest_sha256": MANIFEST_SHA,
                "request_sha256": REQUEST_SHA,
                "result_sha256": RESULT_SHA,
                "semantic_result_sha256": SEMANTIC_SHA,
                "invocation_receipt_sha256": RECEIPT_SHA,
                "conformance_sha256": conformance["conformance_sha256"],
            },
            "source_artifact_sha256s": [SHA],
            "artifacts": [
                {
                    "artifact_id": "candidate_pcm",
                    "sha256": "7" * 64,
                    "size_bytes": 4096,
                    "media_kind": "application/vnd.earcrate.stereo-f32le",
                    "role": "authoritative_candidate_pcm",
                    "musical_authority": True,
                }
            ],
            "time_map": {
                "schema_version": 1,
                "kind": "earcrate_floor_time_map",
                "time_unit": "seconds",
                "segments": [
                    {
                        "lane_id": "deck_a",
                        "target_start": 0,
                        "target_end": 8,
                        "source_artifact_id": "source",
                        "source_start": 64,
                        "source_end": 72,
                        "mode": "continuous",
                    },
                    {
                        "lane_id": "deck_b",
                        "target_start": "799/100",
                        "target_end": 12,
                        "source_artifact_id": "source",
                        "source_start": 158,
                        "source_end": "16201/100",
                        "mode": "jump",
                    },
                ],
            },
            "phrase_contract": {
                "schema_version": 1,
                "kind": "earcrate_floor_phrase_contract",
                "role": "hook_reprise",
                "start_beat": 32,
                "length_beats": 16,
                "meter": {"numerator": 4, "denominator": 4},
                "transforms": {"allowed_operations": ["source_seek", "equal_power_crossfade"]},
                "hard_constraints": {"source_only": True},
                "soft_objectives": [{"objective": "transition_transparency"}],
                "identity_obligations": [{"kind": "recognizability"}],
                "future_obligations": [{"kind": "human_review"}],
                "evidence_refs": ["recurrence_pair"],
                "rights": {
                    "assertion_status": "unknown",
                    "allowed_uses": ["private_review"],
                    "prohibited_uses": ["public_release"],
                },
            },
            "status_vector": {
                "custody": "passed",
                "build_reproducibility": "passed",
                "signal_sanity": "not_evaluated",
                "recurrence_identity": "not_evaluated",
                "transition_integrity": "not_evaluated",
                "human_musical_review": "pending",
                "rights_eligibility": "not_evaluated",
                "release_state": "blocked",
            },
            "boundary": {
                "source_only": True,
                "builder_may_approve_music": False,
                "human_review_required": True,
                "legal_clearance_claimed": False,
                "whole_organism_passed": False,
            },
            "metadata": {"declared_use": "private_review"},
        }
    )


def _evaluation() -> dict:
    return floor_seal_evaluation_ledger(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_evaluation_ledger",
            "provider_id": "org.test.builder",
            "provider_manifest_sha256": MANIFEST_SHA,
            "request_sha256": REQUEST_SHA,
            "result_sha256": RESULT_SHA,
            "evaluator": {
                "evaluator_id": "org.test.signal-evaluator",
                "version": "1.0.0",
                "manifest_sha256": "8" * 64,
            },
            "fixture_sha256": SHA,
            "metrics": {
                "automatic_signal_passed": 1.0,
                "chroma_frame_cosine_mean": 0.99,
                "transition_integrity_passed": 1.0,
                "integrated_loudness_lufs": -9.1,
            },
            "hard_gate_evidence": {"gates": {"starts_immediately": True}},
            "notes": ["Signal only; no musical verdict."],
            "metadata": {"evaluation_domain": "signal_sanity"},
        }
    )


def test_floor_time_map_supports_crossfade_lanes_but_refuses_same_lane_overlap() -> None:
    sealed = floor_seal_time_map(_candidate()["time_map"])
    assert [row["lane_id"] for row in sealed["segments"]] == ["deck_a", "deck_b"]
    bad = json.loads(json.dumps(sealed))
    bad.pop("time_map_sha256", None)
    bad["segments"][1]["lane_id"] = "deck_a"
    try:
        floor_seal_time_map(bad)
    except FloorError as exc:
        assert "within lane" in str(exc)
    else:
        raise AssertionError("Floor accepted overlapping source mappings in one lane")


def test_release_gate_stops_at_human_review_and_rights_boundaries() -> None:
    candidate = _candidate()
    conformance = _conformance()
    evaluation = _evaluation()
    pending = floor_build_release_gate(candidate, conformance=conformance, signal_evaluation=evaluation)
    assert pending["status_vector"]["release_state"] == "signal_sane_human_review_pending"
    assert pending["release_eligible"] is False
    assert pending["musical_acceptance_decided_by_builder"] is False

    review = floor_seal_human_musical_review(
        {
            "release_candidate_sha256": candidate["release_candidate_sha256"],
            "reviewer": {"reviewer_id": "human:musician-1", "role": "musician"},
            "verdict": "accepted",
            "blind_review": True,
            "ratings": {"continuity": 0.9, "recognizability": 0.85},
            "notes": ["Accepted for the declared private review use."],
        }
    )
    rights_pending = floor_build_release_gate(
        candidate,
        conformance=conformance,
        signal_evaluation=evaluation,
        human_review=review,
    )
    assert rights_pending["status_vector"]["release_state"] == "rights_review_pending"

    rights = floor_seal_rights_review(
        {
            "release_candidate_sha256": candidate["release_candidate_sha256"],
            "declared_use": "private_review",
            "status": "eligible_for_declared_use",
            "reviewer": {"reviewer_id": "policy:local-private-review"},
            "evidence_refs": ["user_supplied_source"],
            "conditions": ["do not distribute source or derivative publicly"],
            "legal_determination": False,
        }
    )
    accepted = floor_build_release_gate(
        candidate,
        conformance=conformance,
        signal_evaluation=evaluation,
        human_review=review,
        rights_review=rights,
    )
    assert accepted["status_vector"]["release_state"] == "release_eligible_for_declared_use"
    assert accepted["release_eligible"] is True
    assert accepted["whole_organism_passed"] is False


def test_release_candidate_and_reviews_refuse_self_approval() -> None:
    raw = json.loads(json.dumps(_candidate()))
    raw.pop("release_candidate_sha256", None)
    raw["boundary"]["builder_may_approve_music"] = True
    try:
        floor_seal_release_candidate(raw)
    except FloorError as exc:
        assert "may not approve" in str(exc)
    else:
        raise AssertionError("release builder approved its own music")

    candidate = _candidate()
    self_review = floor_seal_human_musical_review(
        {
            "release_candidate_sha256": candidate["release_candidate_sha256"],
            "reviewer": {"reviewer_id": "org.test.builder"},
            "verdict": "accepted",
            "ratings": {"continuity": 1.0},
        }
    )
    try:
        floor_build_release_gate(
            candidate,
            conformance=_conformance(),
            signal_evaluation=_evaluation(),
            human_review=self_review,
        )
    except FloorError as exc:
        assert "may not supply" in str(exc)
    else:
        raise AssertionError("release builder supplied the human verdict")


def test_release_review_request_and_schema_bundle() -> None:
    candidate = _candidate()
    request = floor_human_review_request(candidate)
    assert request["status"] == "pending"
    assert request["builder_may_answer"] is False
    schemas = floor_schema_bundle()
    for name in (
        "earcrate_floor_release_candidate_v1.schema.json",
        "earcrate_floor_human_musical_review_request_v1.schema.json",
        "earcrate_floor_human_musical_review_v1.schema.json",
        "earcrate_floor_rights_review_v1.schema.json",
        "earcrate_floor_release_gate_receipt_v1.schema.json",
    ):
        assert name in schemas


def test_release_candidate_package_and_single_file_cli(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    candidate = _candidate()
    candidate_path = floor_write_json_atomic(tmp_path / "candidate.json", candidate)
    review_path = tmp_path / "review-request.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    package = subprocess.run(
        [sys.executable, "-m", "earcrate", "floor", "review-request", str(candidate_path), str(review_path)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    package_payload = json.loads(package.stdout)
    assert package_payload["status"] == "pending"

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    single_path = tmp_path / "single-review-request.json"
    single = subprocess.run(
        [
            sys.executable,
            str(root / "dist" / "earcrate.py"),
            "floor",
            "review-request",
            str(candidate_path),
            str(single_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert single.returncode == 0, single.stdout + single.stderr
    single_payload = json.loads(single.stdout)
    assert package_payload["review_request_sha256"] == single_payload["review_request_sha256"]


def test_empire_state_release_fixture_stops_at_human_review() -> None:
    root = Path(__file__).resolve().parent.parent
    fixture = json.loads(
        (root / "proofs" / "floor" / "empire_state_recurrence_release_candidate_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["source"]["file_sha256"] == "af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979"
    assert fixture["builder"]["conformance_complete"] is True
    assert fixture["builder"]["semantic_result_repeatable"] is True
    assert fixture["signal_evaluator"]["independent_of_builder"] is True
    assert fixture["measurements"]["automatic_signal_passed"] is True
    assert fixture["status_vector"]["release_state"] == "signal_sane_human_review_pending"
    assert fixture["boundary"]["release_eligible"] is False
    assert fixture["boundary"]["whole_organism_passed"] is False
