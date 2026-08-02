from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

from earcrate.estate.homelab import (
    audit_homelab,
    bind_homelab_fixture,
    capture_homelab_node,
    decide_homelab_target,
    record_homelab_audition,
    record_homelab_stage,
)
from earcrate.estate.homelab_catalog import homelab_catalog
from earcrate.estate.homelab_common import homelab_seal, homelab_validate_seal
from earcrate.estate.homelab_redact import project_public_object
from earcrate.estate.homelab_review import adjudicate_review, prepare_blind_review, record_review_submission
from earcrate.estate.model import estate_seal, estate_sha256_file, write_estate_json


def _rig(tmp_path: Path) -> dict:
    return estate_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_rig_capability_receipt",
            "captured_at": "2026-08-01T00:00:00Z",
            "host": {
                "system": "TestOS",
                "machine": "x86_64",
                "hostname_sha256": "1" * 64,
                "python_executable": "python",
            },
            "roots": [{"path": str(tmp_path), "exists": True}],
            "nvidia": {"available": False, "gpus": []},
            "python_packages": {},
            "executables": [],
            "audio_devices": {"requested": False, "available": False, "devices": []},
            "environment_declarations": {"names_present": [], "values_recorded": False},
            "summary": {},
            "boundary": {
                "no_heavy_model_inference_run": True,
                "no_source_audio_decoded": True,
                "no_network_probe": True,
                "audio_devices_queried": False,
                "capability_is_not_quality_acceptance": True,
            },
        }
    )


def _item(path: Path, *, item_id: str, classification: str, metadata: dict, strong: bool = True) -> dict:
    return {
        "item_id": item_id,
        "root_id": "root_test",
        "relative_path": path.name,
        "absolute_path": str(path),
        "file_type": "file",
        "bytes": int(path.stat().st_size),
        "mtime_ns": int(path.stat().st_mtime_ns),
        "extension": path.suffix.lower(),
        "classification": classification,
        "disposition": "durable_evidence" if classification != "source_audio" else "external_source_reference",
        "reasons": ["integrity test"],
        "hash_status": "strong" if strong else "not_requested",
        "raw_sha256": estate_sha256_file(path) if strong else None,
        "metadata": metadata,
    }


def _inventory(tmp_path: Path, items: list[dict]) -> dict:
    return estate_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_estate_inventory",
            "created_at": "2026-08-01T00:00:00Z",
            "policy_sha256": "2" * 64,
            "hash_mode": "evidence",
            "roots": [{"root_id": "root_test", "path": str(tmp_path), "role": "unclassified", "exists": True}],
            "items": items,
            "duplicates": [],
            "issues": [],
            "canon": None,
            "summary": {"files": len(items)},
        }
    )


def test_declared_hash_never_substitutes_for_fixture_bytes(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    expected = next(
        row["expected_sha256"]
        for row in catalog["fixtures"]
        if row["fixture_id"] == "fixture.pretty_lights.source_audio"
    )
    declaration = tmp_path / "receipt.json"
    declaration.write_text(json.dumps({"source_sha256": expected}) + "\n", encoding="utf-8")
    inventory = _inventory(
        tmp_path,
        [
            _item(
                declaration,
                item_id="declared-only",
                classification="proof_receipt",
                metadata={"declared_sha256": [{"field": "source_sha256", "sha256": expected}]},
            )
        ],
    )
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    audit = audit_homelab(inventory, [node], catalog=catalog)
    status = audit["fixture_status"]["fixture.pretty_lights.source_audio"]
    assert status["available"] is False
    assert "only declared" in status["reason"]
    assert status["declared_identity_item_ids"] == ["declared-only"]


def test_fixture_binding_verifies_current_local_bytes_and_detects_mutation(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    audio = tmp_path / "flim-target.wav"
    audio.write_bytes(b"RIFF-exact-flim-test")
    binding = bind_homelab_fixture(
        catalog,
        fixture_id="fixture.flim.target_recording",
        artifact_path=audio,
        bound_by="owner:test",
        reason="bind the exact blind-control recording",
    )
    homelab_validate_seal(binding)
    binding_path = write_estate_json(tmp_path / "flim.fixture-binding.json", binding)
    inventory = _inventory(
        tmp_path,
        [
            _item(audio, item_id="audio", classification="source_audio", metadata={}),
            _item(
                binding_path,
                item_id="binding",
                classification="proof_manifest",
                metadata={"kind": "earcrate_homelab_fixture_binding"},
            ),
        ],
    )
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    audit = audit_homelab(inventory, [node], catalog=catalog)
    status = audit["fixture_status"]["fixture.flim.target_recording"]
    assert status["available"] is True
    assert status["binding_sha256s"] == [binding["binding_sha256"]]
    assert status["evidence_item_ids"] == ["audio"]

    audio.write_bytes(b"RIFF-mutated-flim-test")
    changed = audit_homelab(inventory, [node], catalog=catalog)
    changed_status = changed["fixture_status"]["fixture.flim.target_recording"]
    assert changed_status["available"] is False
    assert any("changed" in reason for reason in changed_status["invalid_binding_reasons"])


def test_blind_review_binds_public_bytes_and_requires_token_proof(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    candidate = tmp_path / "candidate.wav"
    control = tmp_path / "control.wav"
    candidate.write_bytes(b"RIFF-candidate")
    control.write_bytes(b"RIFF-control")
    prepared = prepare_blind_review(
        catalog,
        target_id="demucs",
        node_sha256="1" * 64,
        reviewer_id="reviewer:test",
        candidate_path=candidate,
        control_path=control,
        fixture_ids=["fixture.pretty_lights.source_audio", "fixture.private_library.real"],
        playback_chain={"device": "test", "sample_rate": 48000, "level": "matched"},
        public_directory=tmp_path / "public",
        private_directory=tmp_path / "private",
    )
    assignment = prepared["assignment"]
    authority = prepared["private_authority"]
    assert "review_token" not in assignment
    assert authority["review_token"] == prepared["review_token"]
    assert Path(prepared["review_token_file"]).is_file()
    assert not (tmp_path / "public" / "review-token.txt").exists()
    projected = project_public_object(authority)
    assert projected["payload"]["review_token"] == "redacted"

    candidate_option = next(option for option, role in authority["option_map"].items() if role == "candidate")
    submission = record_review_submission(
        assignment,
        reviewer_id="reviewer:test",
        review_token=prepared["review_token"],
        choice=candidate_option,
        dimensions={"bleed": 5, "transients": 4, "role_usefulness": 5},
    )
    ledger = adjudicate_review(catalog, assignment, authority, submission)
    assert ledger["verdict"] == "accept"
    assert ledger["submission_proof_hmac_sha256"] == submission["submission_proof_hmac_sha256"]

    forged = deepcopy(submission)
    forged.pop("submission_sha256")
    forged["choice"] = "B" if candidate_option == "A" else "A"
    forged = homelab_seal(forged)
    try:
        adjudicate_review(catalog, assignment, authority, forged)
    except PermissionError as exc:
        assert "token" in str(exc).lower()
    else:
        raise AssertionError("forged review submission unexpectedly adjudicated")

    mismatched_assignment = deepcopy(assignment)
    mismatched_assignment.pop("assignment_sha256")
    mismatched_assignment["options"]["A"]["sha256"] = "f" * 64
    mismatched_assignment = homelab_seal(mismatched_assignment)
    mismatched_submission = record_review_submission(
        mismatched_assignment,
        reviewer_id="reviewer:test",
        review_token=prepared["review_token"],
        choice="A",
        dimensions={"bleed": 5},
    )
    try:
        adjudicate_review(catalog, mismatched_assignment, authority, mismatched_submission)
    except ValueError as exc:
        assert "public option" in str(exc).lower()
    else:
        raise AssertionError("review over mismatched public bytes unexpectedly adjudicated")


def test_blind_audition_requires_complete_source_chain_in_audit(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    node_sha = node["node_sha256"]
    candidate = tmp_path / "candidate.wav"
    control = tmp_path / "control.wav"
    candidate.write_bytes(b"RIFF-candidate")
    control.write_bytes(b"RIFF-control")
    required = ["fixture.pretty_lights.source_audio", "fixture.private_library.real"]
    try:
        record_homelab_audition(
            catalog,
            target_id="demucs",
            node_sha256=node_sha,
            reviewer_id="reviewer:test",
            candidate_sha256=estate_sha256_file(candidate),
            control_sha256=estate_sha256_file(control),
            verdict="accept",
            blinded=True,
            randomized=True,
            playback_chain={"device": "test"},
            dimensions={"bleed": 5},
            fixture_ids=required,
        )
    except ValueError as exc:
        assert "assignment" in str(exc).lower()
    else:
        raise AssertionError("direct blind-audition ledger unexpectedly sealed")

    prepared = prepare_blind_review(
        catalog,
        target_id="demucs",
        node_sha256=node_sha,
        reviewer_id="reviewer:test",
        candidate_path=candidate,
        control_path=control,
        fixture_ids=required,
        playback_chain={"device": "test", "level": "matched"},
        public_directory=tmp_path / "public",
        private_directory=tmp_path / "private",
    )
    candidate_option = next(
        option for option, role in prepared["private_authority"]["option_map"].items() if role == "candidate"
    )
    submission = record_review_submission(
        prepared["assignment"],
        reviewer_id="reviewer:test",
        review_token=prepared["review_token"],
        choice=candidate_option,
        dimensions={"bleed": 5},
    )
    ledger = adjudicate_review(catalog, prepared["assignment"], prepared["private_authority"], submission)

    object_rows: list[dict] = []
    for index, value in enumerate(
        (prepared["assignment"], prepared["private_authority"], submission, ledger)
    ):
        path = write_estate_json(tmp_path / f"review-object-{index}.json", value)
        object_rows.append(
            _item(
                path,
                item_id=f"review-{index}",
                classification="human_review",
                metadata={"kind": value["kind"]},
            )
        )
    inventory = _inventory(tmp_path, object_rows)
    audit = audit_homelab(inventory, [node], catalog=catalog)
    demucs = next(row for row in audit["targets"] if row["target_id"] == "demucs")
    assert "blind_audition" in demucs["completed_stages"]

    incomplete = _inventory(
        tmp_path,
        [row for row in object_rows if row["metadata"]["kind"] != "earcrate_homelab_private_assignment_authority"],
    )
    incomplete_audit = audit_homelab(incomplete, [node], catalog=catalog)
    incomplete_demucs = next(row for row in incomplete_audit["targets"] if row["target_id"] == "demucs")
    assert "blind_audition" not in incomplete_demucs["completed_stages"]
    assert any("source objects are missing" in warning for warning in incomplete_demucs["warnings"])


def test_homelab_schema_requires_fixture_and_review_integrity_fields() -> None:
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "earcrate_homelab_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert {"$ref": "#/$defs/fixtureBinding"} in schema["oneOf"]
    private_required = set(schema["$defs"]["privateAssignmentAuthority"]["allOf"][1]["required"])
    assert {"review_token", "review_token_sha256"}.issubset(private_required)
    submission_required = set(schema["$defs"]["reviewSubmission"]["allOf"][1]["required"])
    assert {"fixture_ids", "review_token_sha256", "submission_proof_hmac_sha256"}.issubset(submission_required)
    blind_then = schema["$defs"]["auditionLedger"]["allOf"][1]["allOf"][0]["then"]["required"]
    assert {"assignment_sha256", "private_authority_sha256", "submission_sha256"}.issubset(blind_then)


def test_adoption_decision_is_current_node_and_stage_evidence_scoped(tmp_path: Path) -> None:
    catalog = homelab_catalog()

    def ready_rig(marker: str) -> dict:
        value = deepcopy(_rig(tmp_path))
        value.pop("rig_sha256")
        value["host"]["hostname_sha256"] = marker * 64
        executable = str(Path(sys.executable).resolve())
        value["host"]["python_executable"] = executable
        value["executables"] = [
            {"name": "ffmpeg", "available": True, "path": executable, "version": "test"},
            {"name": "ffprobe", "available": True, "path": executable, "version": "test"},
        ]
        return estate_seal(value)

    node_a = capture_homelab_node(ready_rig("a"), catalog=catalog)
    fixtures = ["fixture.synthetic.regression", "fixture.private_library.real"]
    identity = record_homelab_stage(
        catalog,
        target_id="ffmpeg",
        stage="local_identity_audit",
        node_sha256=node_a["node_sha256"],
        status="passed",
        artifact_sha256s=["1" * 64],
    )
    fixture = record_homelab_stage(
        catalog,
        target_id="ffmpeg",
        stage="real_fixture",
        node_sha256=node_a["node_sha256"],
        status="passed",
        fixture_ids=fixtures,
        artifact_sha256s=["2" * 64],
    )
    audition = record_homelab_audition(
        catalog,
        target_id="ffmpeg",
        node_sha256=node_a["node_sha256"],
        reviewer_id="reviewer:test",
        candidate_sha256="3" * 64,
        control_sha256="4" * 64,
        verdict="accept",
        blinded=False,
        randomized=True,
        playback_chain={"device": "test", "level": "matched"},
        dimensions={"decode_fidelity": 5, "workflow": 5},
        fixture_ids=fixtures,
    )

    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF-private-library")
    workspace = tmp_path / "config.json"
    workspace.write_text("{}\n", encoding="utf-8")
    rows = [
        _item(source, item_id="source", classification="source_audio", metadata={}),
        _item(workspace, item_id="workspace", classification="workspace_config", metadata={}),
    ]
    for index, value in enumerate((identity, fixture, audition)):
        path = write_estate_json(tmp_path / f"evidence-{index}.json", value)
        rows.append(
            _item(
                path,
                item_id=f"evidence-{index}",
                classification="run_receipt" if value["kind"] != "earcrate_homelab_audition_ledger" else "human_review",
                metadata={"kind": value["kind"]},
            )
        )

    first_audit = audit_homelab(_inventory(tmp_path, rows), [node_a], catalog=catalog)
    ffmpeg = next(row for row in first_audit["targets"] if row["target_id"] == "ffmpeg")
    assert ffmpeg["feasibility"] == "ready"
    assert set(ffmpeg["completed_stages"]) == {
        "local_identity_audit",
        "real_fixture",
        "regression_audition",
    }
    current_receipts = sorted(ffmpeg["stage_evidence"].values())

    try:
        decide_homelab_target(
            first_audit,
            target_id="ffmpeg",
            decision="accepted",
            decided_by="authority:test",
            reason="intentionally incomplete evidence set",
            supporting_receipt_sha256s=current_receipts[:1],
        )
    except ValueError as exc:
        assert "every current stage receipt" in str(exc)
    else:
        raise AssertionError("accepted decision unexpectedly omitted current stage evidence")

    decision = decide_homelab_target(
        first_audit,
        target_id="ffmpeg",
        decision="accepted",
        decided_by="authority:test",
        reason="all current node-scoped stage evidence passed",
        supporting_receipt_sha256s=current_receipts,
    )
    decision_path = write_estate_json(tmp_path / "decision.json", decision)
    rows_with_decision = [
        *rows,
        _item(
            decision_path,
            item_id="decision",
            classification="proof_receipt",
            metadata={"kind": decision["kind"]},
        ),
    ]
    accepted_audit = audit_homelab(_inventory(tmp_path, rows_with_decision), [node_a], catalog=catalog)
    accepted = next(row for row in accepted_audit["targets"] if row["target_id"] == "ffmpeg")
    assert accepted["terminal_decision"] == "accepted"

    node_b = capture_homelab_node(ready_rig("b"), catalog=catalog)
    other_node_audit = audit_homelab(
        _inventory(tmp_path, rows_with_decision),
        [node_b],
        catalog=catalog,
    )
    other_node = next(row for row in other_node_audit["targets"] if row["target_id"] == "ffmpeg")
    assert other_node["terminal_decision"] is None
    assert "retain_or_replace_decision" in other_node["missing_stages"]
