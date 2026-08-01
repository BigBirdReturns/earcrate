from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from earcrate.estate.homelab import (
    audit_homelab,
    capture_homelab_node,
    decide_homelab_target,
    homelab_catalog,
    homelab_validate_seal,
    propose_homelab_campaign,
    record_homelab_audition,
    record_homelab_stage,
)
from earcrate.estate.model import estate_seal, write_estate_json

ROOT = Path(__file__).resolve().parent.parent


def _rig(tmp_path: Path) -> dict:
    executable = Path(sys.executable).resolve()
    return estate_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_rig_capability_receipt",
            "captured_at": "2026-08-01T00:00:00Z",
            "host": {
                "system": "TestOS",
                "machine": "x86_64",
                "hostname_sha256": "1" * 64,
                "python_executable": str(executable),
            },
            "roots": [{"path": str(tmp_path), "exists": True}],
            "nvidia": {"available": True, "gpus": [{"name": "RTX test", "uuid": "GPU-test"}]},
            "python_packages": {
                "numpy": "2.0",
                "scipy": "1.14",
                "librosa": "0.11",
                "soundfile": "0.13",
                "mido": "1.3",
                "pyloudnorm": "0.1",
                "basic-pitch": None,
                "allin1": None,
            },
            "executables": [
                {"name": "python", "available": True, "path": str(executable), "version": "Python test"},
                {"name": "ffmpeg", "available": True, "path": str(executable), "version": "ffmpeg test"},
                {"name": "ffprobe", "available": True, "path": str(executable), "version": "ffprobe test"},
                {"name": "git", "available": True, "path": str(executable), "version": "git test"},
                {"name": "fpcalc", "available": False, "path": None, "version": None},
                {"name": "rubberband", "available": False, "path": None, "version": None},
            ],
            "audio_devices": {"requested": True, "available": True, "devices": [{"index": 0, "name": "Test Output"}]},
            "environment_declarations": {"names_present": [], "values_recorded": False},
            "summary": {},
            "boundary": {
                "no_heavy_model_inference_run": True,
                "no_source_audio_decoded": True,
                "no_network_probe": True,
                "audio_devices_queried": True,
                "capability_is_not_quality_acceptance": True,
            },
        }
    )


def _inventory(tmp_path: Path, extra_objects: list[dict] | None = None) -> dict:
    items = [
        {
            "item_id": "item_source",
            "root_id": "root_music",
            "relative_path": "music/source.mp3",
            "absolute_path": str(tmp_path / "source.mp3"),
            "file_type": "file",
            "bytes": 12,
            "mtime_ns": 1,
            "extension": ".mp3",
            "classification": "source_audio",
            "disposition": "external_source_reference",
            "reasons": ["test source"],
            "hash_status": "not_requested",
            "raw_sha256": None,
            "metadata": {},
        },
        {
            "item_id": "item_workspace",
            "root_id": "root_workspace",
            "relative_path": "agent/config.json",
            "absolute_path": str(tmp_path / "config.json"),
            "file_type": "file",
            "bytes": 2,
            "mtime_ns": 1,
            "extension": ".json",
            "classification": "workspace_config",
            "disposition": "authority",
            "reasons": ["test workspace"],
            "hash_status": "not_requested",
            "raw_sha256": None,
            "metadata": {},
        },
        {
            "item_id": "item_project",
            "root_id": "root_workspace",
            "relative_path": "projects/demo/project.json",
            "absolute_path": str(tmp_path / "project.json"),
            "file_type": "file",
            "bytes": 2,
            "mtime_ns": 1,
            "extension": ".json",
            "classification": "project_index",
            "disposition": "authority",
            "reasons": ["test project"],
            "hash_status": "not_requested",
            "raw_sha256": None,
            "metadata": {"project_id": "demo"},
        },
        {
            "item_id": "item_revision",
            "root_id": "root_workspace",
            "relative_path": "projects/demo/revisions/a.json",
            "absolute_path": str(tmp_path / "revision.json"),
            "file_type": "file",
            "bytes": 2,
            "mtime_ns": 1,
            "extension": ".json",
            "classification": "project_revision",
            "disposition": "authority",
            "reasons": ["test revision"],
            "hash_status": "not_requested",
            "raw_sha256": None,
            "metadata": {"project_id": "demo"},
        },
    ]
    for index, value in enumerate(extra_objects or []):
        path = tmp_path / f"homelab-object-{index:02d}.json"
        write_estate_json(path, value)
        identity = next(value[field] for field in ("receipt_sha256", "ledger_sha256", "decision_sha256") if value.get(field))
        items.append(
            {
                "item_id": f"item_object_{index:02d}",
                "root_id": "root_evidence",
                "relative_path": path.name,
                "absolute_path": str(path),
                "file_type": "file",
                "bytes": int(path.stat().st_size),
                "mtime_ns": int(path.stat().st_mtime_ns),
                "extension": ".json",
                "classification": "run_receipt",
                "disposition": "durable_evidence",
                "reasons": ["homelab evidence"],
                "hash_status": "strong",
                "raw_sha256": identity,
                "metadata": {"kind": value["kind"]},
            }
        )
    return estate_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_estate_inventory",
            "created_at": "2026-08-01T00:00:00Z",
            "policy_sha256": "2" * 64,
            "hash_mode": "evidence",
            "roots": [
                {"root_id": "root_music", "path": str(tmp_path), "role": "source_library", "exists": True},
                {"root_id": "root_workspace", "path": str(tmp_path), "role": "workspace", "exists": True},
                {"root_id": "root_evidence", "path": str(tmp_path), "role": "unclassified", "exists": True},
            ],
            "items": items,
            "duplicates": [],
            "issues": [],
            "canon": None,
            "summary": {"files": len(items)},
        }
    )


def test_homelab_catalog_is_complete_and_preserves_flim_withholding() -> None:
    catalog = homelab_catalog()
    homelab_validate_seal(catalog)
    assert catalog["summary"]["targets"] == 87
    assert catalog["summary"]["fixtures"] == 10
    targets = {row["target_id"]: row for row in catalog["targets"]}
    for target_id in ("allin1", "music2midi", "pop2piano", "demucs", "mert", "rubberband", "mixxx", "jams"):
        assert target_id in targets
    fixtures = {row["fixture_id"]: row for row in catalog["fixtures"]}
    assert fixtures["fixture.flim.community_pack"]["expected_sha256"] == (
        "a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52"
    )
    assert fixtures["fixture.flim.target_recording"]["expected_sha256"] is None
    assert "withheld" in fixtures["fixture.flim.target_recording"]["note"].lower()
    assert all(row["required_stages"][-1].endswith("decision") for row in catalog["targets"])


def test_homelab_audit_never_promotes_feasibility_to_execution(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    inventory = _inventory(tmp_path)
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    audit = audit_homelab(inventory, [node], catalog=catalog)
    homelab_validate_seal(audit)
    by_target = {row["target_id"]: row for row in audit["targets"]}
    assert by_target["ffmpeg"]["feasibility"] == "ready"
    assert by_target["ffmpeg"]["completed_stages"] == []
    assert by_target["ffmpeg"]["terminal_decision"] is None
    assert by_target["allin1"]["feasibility"] == "blocked"
    assert any("allin1" in blocker.lower() for blocker in by_target["allin1"]["blockers"])
    assert audit["boundary"]["provider_processes_executed"] is False
    assert audit["boundary"]["feasibility_alone_can_complete_campaign"] is False
    campaign = propose_homelab_campaign(audit, catalog=catalog)
    homelab_validate_seal(campaign)
    assert campaign["completion_gate"]["passed"] is False
    assert campaign["summary"]["unresolved_targets"] == 87


def test_homelab_stage_audition_and_acceptance_are_receipt_gated(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    inventory = _inventory(tmp_path)
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    node_sha = node["node_sha256"]
    artifact = "a" * 64
    fixtures = ["fixture.synthetic.regression", "fixture.private_library.real"]

    try:
        record_homelab_stage(
            catalog,
            target_id="ffmpeg",
            stage="real_fixture",
            node_sha256=node_sha,
            status="passed",
            fixture_ids=fixtures,
        )
    except ValueError as exc:
        assert "artifact" in str(exc).lower()
    else:
        raise AssertionError("artifact-free stage unexpectedly passed")

    identity = record_homelab_stage(
        catalog,
        target_id="ffmpeg",
        stage="local_identity_audit",
        node_sha256=node_sha,
        status="passed",
        artifact_sha256s=[artifact],
    )
    fixture = record_homelab_stage(
        catalog,
        target_id="ffmpeg",
        stage="real_fixture",
        node_sha256=node_sha,
        status="passed",
        fixture_ids=fixtures,
        artifact_sha256s=["b" * 64],
        measurements={"decode_ok": True},
    )
    audition = record_homelab_audition(
        catalog,
        target_id="ffmpeg",
        node_sha256=node_sha,
        reviewer_id="reviewer:test",
        candidate_sha256="c" * 64,
        control_sha256="d" * 64,
        verdict="accept",
        blinded=False,
        randomized=True,
        playback_chain={"device": "test", "level": "matched"},
        dimensions={"decode_fidelity": 5, "workflow": 5},
        fixture_ids=fixtures,
    )
    inventory_with_receipts = _inventory(tmp_path, [identity, fixture, audition])
    audit = audit_homelab(inventory_with_receipts, [node], catalog=catalog)
    ffmpeg = next(row for row in audit["targets"] if row["target_id"] == "ffmpeg")
    assert set(ffmpeg["completed_stages"]) == {"local_identity_audit", "real_fixture", "regression_audition"}
    assert ffmpeg["missing_stages"] == ["retain_or_replace_decision"]
    assert ffmpeg["audition_acceptance_present"] is True

    evidence = sorted(audit["evidence_index"])
    decision = decide_homelab_target(
        audit,
        target_id="ffmpeg",
        decision="accepted",
        decided_by="authority:test",
        reason="current node and fixtures passed with accepting audition",
        supporting_receipt_sha256s=evidence,
    )
    homelab_validate_seal(decision)
    assert decision["decision"] == "accepted"
    assert decision["boundary"]["decision_is_not_whole_buffalo_passage"] is True


def test_homelab_blind_audition_and_cli_are_fail_closed(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    node = capture_homelab_node(_rig(tmp_path), catalog=catalog)
    required = ["fixture.pretty_lights.source_audio", "fixture.private_library.real"]
    try:
        record_homelab_audition(
            catalog,
            target_id="demucs",
            node_sha256=node["node_sha256"],
            reviewer_id="reviewer:test",
            candidate_sha256="e" * 64,
            control_sha256="f" * 64,
            verdict="accept",
            blinded=False,
            randomized=True,
            playback_chain={"device": "test"},
            dimensions={"bleed": 5},
            fixture_ids=required,
        )
    except ValueError as exc:
        assert "blinding" in str(exc).lower()
    else:
        raise AssertionError("unblinded blind audition unexpectedly sealed")

    process = subprocess.run(
        [sys.executable, "-m", "earcrate", "homelab", "catalog", "--output", str(tmp_path / "catalog.json")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    summary = json.loads(process.stdout)
    assert summary["ok"] is True
    emitted = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    homelab_validate_seal(emitted)
    assert emitted["summary"]["targets"] == 87
    schema = json.loads((ROOT / "schemas" / "earcrate_homelab_v1.schema.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["catalog"]["allOf"][1]["properties"]["targets"]["minItems"] == 87
