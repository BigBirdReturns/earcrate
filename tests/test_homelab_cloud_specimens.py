from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from earcrate.estate.homelab_common import HOMELAB_HASH_FIELDS, homelab_validate_seal
from earcrate.estate.homelab_redact import project_public_object
from earcrate.estate.homelab_store import HomelabStore
from earcrate.estate.homelab_specimens import (
    bind_specimen_source,
    build_specimen_suite,
    compile_specimen_campaign,
    record_specimen_trial,
    validate_seal,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(tmp_path: Path) -> Path:
    root = "case-v1"
    files = {
        "case.json": json.dumps(
            {
                "case_id": "synthetic-cloud-case",
                "title": "Synthetic cloud case",
                "recordings": [
                    {"source_id": "source_a", "artist": "A", "title": "One", "recording_role": "host"},
                    {"source_id": "source_b", "artist": "B", "title": "Two", "recording_role": "donor"},
                ],
            },
            sort_keys=True,
        ).encode(),
        "earcrate/provider_jobs.json": json.dumps(
            {
                "jobs": [
                    {"job_id": "P00", "capability": "source_identity_and_signal_scan", "provider": "ffmpeg"},
                    {"job_id": "P01", "capability": "source_separation", "provider": "Demucs"},
                ]
            },
            sort_keys=True,
        ).encode(),
        "auditions/audition_matrix.json": json.dumps(
            {"auditions": [{"id": "AUD01", "goal": "synthetic"}]}, sort_keys=True
        ).encode(),
    }
    checks = "".join(f"{_sha(data)}  {name}\n" for name, data in sorted(files.items())).encode()
    files["checksums.sha256"] = checks
    target = tmp_path / "case-v1.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(f"{root}/{name}", data)
    (tmp_path / "case-v1.zip.sha256.txt").write_text(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n")
    return target


def _policy() -> dict:
    return {
        "profiles": {
            "core": {"role_caps": {"custody": 1, "separation": 1}},
            "smoke": {"role_caps": {"custody": 1}},
            "full": {"role_caps": {"custody": 99, "separation": 99}},
        },
        "role_capability_terms": {
            "custody": ["decode", "media_probe"],
            "separation": ["source_separation", "stems"],
        },
        "provider_aliases": {
            "ffmpeg": ["ffmpeg", "ffprobe"],
            "demucs": ["demucs", "htdemucs"],
        },
    }


def _catalog() -> dict:
    from earcrate.estate.homelab_common import homelab_seal

    return homelab_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_catalog",
            "cataloged_at": "2026-08-08",
            "name": "synthetic",
            "targets": [
                {
                    "target_id": "ffmpeg",
                    "display_name": "FFmpeg / ffprobe",
                    "target_class": "adopted_core",
                    "target_manifest_sha256": "1" * 64,
                    "capabilities": ["decode", "media_probe"],
                    "requirements": {"gpu": "none", "network": "none", "manual_probe": False},
                },
                {
                    "target_id": "demucs",
                    "display_name": "Demucs",
                    "target_class": "oss_provider",
                    "target_manifest_sha256": "2" * 64,
                    "capabilities": ["source_separation", "stems"],
                    "requirements": {"gpu": "optional", "network": "none", "manual_probe": False},
                },
            ],
            "fixtures": [],
            "summary": {"targets": 2},
        }
    )


def _audit(catalog: dict) -> dict:
    from earcrate.estate.homelab_common import homelab_seal

    return homelab_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_audit",
            "audited_at": "2026-08-08T00:00:00Z",
            "catalog_sha256": catalog["catalog_sha256"],
            "inventory_sha256": "3" * 64,
            "node_sha256s": ["4" * 64],
            "fixture_status": {},
            "targets": [
                {"target_id": "ffmpeg", "feasibility": "ready"},
                {"target_id": "demucs", "feasibility": "ready"},
            ],
            "evidence_index": {},
            "object_warnings": [],
            "summary": {"targets": 2, "feasible": 2},
            "boundary": {},
        }
    )


def test_cloud_specimen_objects_are_registered_and_storeable(tmp_path: Path) -> None:
    expected = {
        "earcrate_homelab_specimen_suite",
        "earcrate_homelab_specimen_intake_receipt",
        "earcrate_homelab_specimen_source_binding",
        "earcrate_homelab_specimen_trial_receipt",
    }
    assert expected.issubset(HOMELAB_HASH_FIELDS)
    archive = _archive(tmp_path)
    suite = build_specimen_suite(
        [archive],
        sidecars={archive.name: archive.with_name(archive.name + ".sha256.txt")},
        role_policy=_policy(),
    )
    validate_seal(suite)
    homelab_validate_seal(suite)
    with HomelabStore(tmp_path / "store") as store:
        result = store.ingest_object(suite, visibility="public")
        assert result["identity"] == suite["suite_sha256"]
        assert store.doctor()["ok"] is True


def test_sensitive_source_binding_projects_without_absolute_path(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    suite = build_specimen_suite(
        [archive],
        sidecars={archive.name: archive.with_name(archive.name + ".sha256.txt")},
        role_policy=_policy(),
    )
    source = tmp_path / "source-a.wav"
    source.write_bytes(b"RIFF-synthetic")
    binding = bind_specimen_source(
        suite,
        case_id="synthetic-cloud-case",
        source_id="source_a",
        artifact_path=source,
        bound_by="operator:test",
        reason="synthetic test binding",
    )
    validate_seal(binding)
    projected = project_public_object(binding)
    assert projected["payload"]["artifact_path"].startswith("redacted:sha256:")
    assert str(source.resolve()) not in json.dumps(projected)


def test_campaign_requires_bindings_and_trial_receipt_does_not_claim_adoption(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    suite = build_specimen_suite(
        [archive],
        sidecars={archive.name: archive.with_name(archive.name + ".sha256.txt")},
        role_policy=_policy(),
    )
    catalog = _catalog()
    audit = _audit(catalog)
    blocked = compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=[],
        policy=_policy(),
        profile="core",
    )
    assert blocked["summary"]["source_bindings_missing"] == 2
    assert all(task["status"] == "blocked" for task in blocked["tasks"])

    bindings = []
    for source_id in ("source_a", "source_b"):
        source = tmp_path / f"{source_id}.wav"
        source.write_bytes(f"RIFF-{source_id}".encode())
        bindings.append(
            bind_specimen_source(
                suite,
                case_id="synthetic-cloud-case",
                source_id=source_id,
                artifact_path=source,
                bound_by="operator:test",
                reason="synthetic test binding",
            )
        )
    campaign = compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=bindings,
        policy=_policy(),
        profile="core",
    )
    assert campaign["summary"]["source_bindings_missing"] == 0
    trial_task = next(task for task in campaign["tasks"] if task["task_type"] == "specimen_trial")
    artifact = tmp_path / "observation.json"
    artifact.write_text('{"ok":true}\n')
    receipt = record_specimen_trial(
        suite,
        campaign,
        task_id=trial_task["task_id"],
        node_sha256="4" * 64,
        outcome="passed",
        actor_id="worker:test",
        actor_type="machine",
        artifacts=[artifact],
        source_bindings=bindings,
    )
    validate_seal(receipt)
    assert receipt["authority"]["provider_adoption_decision"] is False
    assert receipt["boundary"]["trial_receipt_is_not_provider_stage_receipt"] is True


def _fixture_blocked_audit(catalog: dict, *, hard_blocker: bool = False) -> dict:
    from earcrate.estate.homelab_common import homelab_seal

    rows = []
    for target_id in ("ffmpeg", "demucs"):
        blockers = ["missing fixture fixture.legacy.audio: exact bytes are not bound"]
        if hard_blocker and target_id == "demucs":
            blockers.append("missing Python distribution: demucs")
        rows.append({"target_id": target_id, "feasibility": "blocked", "blockers": blockers})
    return homelab_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_audit",
            "audited_at": "2026-08-08T00:00:00Z",
            "catalog_sha256": catalog["catalog_sha256"],
            "inventory_sha256": "5" * 64,
            "node_sha256s": ["4" * 64],
            "fixture_status": {},
            "targets": rows,
            "evidence_index": {},
            "object_warnings": [],
            "summary": {"targets": 2, "feasible": 0, "blocked_feasibility": 2},
            "boundary": {},
        }
    )


def test_fixture_only_audit_blockers_are_substitutable_for_specimen_trials(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    suite = build_specimen_suite(
        [archive],
        sidecars={archive.name: archive.with_name(archive.name + ".sha256.txt")},
        role_policy=_policy(),
    )
    catalog = _catalog()
    audit = _fixture_blocked_audit(catalog)
    campaign = compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=[],
        policy=_policy(),
        profile="core",
    )
    assert campaign["summary"]["audit_ready_targets"] == 0
    assert campaign["summary"]["specimen_fixture_substitutable_targets"] == 2
    assert campaign["summary"]["provider_trials"] == 2
    trials = [task for task in campaign["tasks"] if task["task_type"] == "specimen_trial"]
    assert all(task["status"] == "blocked" for task in trials)
    assert all(task["selection_evidence"]["trial_readiness_mode"] == "specimen_fixture_substitution" for task in trials)
    assert campaign["completion_gate"]["catalog_fixture_substitution_is_trial_scoped_only"] is True


def test_nonfixture_audit_blocker_remains_hard_for_specimen_trials(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    suite = build_specimen_suite(
        [archive],
        sidecars={archive.name: archive.with_name(archive.name + ".sha256.txt")},
        role_policy=_policy(),
    )
    catalog = _catalog()
    audit = _fixture_blocked_audit(catalog, hard_blocker=True)
    campaign = compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=[],
        policy=_policy(),
        profile="core",
    )
    selected_targets = {
        task["target_id"] for task in campaign["tasks"] if task["task_type"] == "specimen_trial"
    }
    assert "ffmpeg" in selected_targets
    assert "demucs" not in selected_targets
    assert campaign["summary"]["hard_blocked_targets"] == 1
