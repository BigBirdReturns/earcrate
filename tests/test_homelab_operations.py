from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

from earcrate.estate.homelab_catalog import homelab_catalog
from earcrate.estate.homelab_common import HOMELAB_SCHEMA_VERSION, homelab_seal, homelab_validate_seal
from earcrate.estate.homelab_ops import backup_homelab_store, export_public_store, render_homelab_dashboard, restore_homelab_backup
from earcrate.estate.homelab_review import adjudicate_review, prepare_blind_review, record_review_submission
from earcrate.estate.homelab_store import HomelabStore
from earcrate.estate.model import estate_sha256_file

ROOT = Path(__file__).resolve().parent.parent


def _stage_receipt(catalog: dict, *, target_id: str = "ffmpeg", stage: str = "local_identity_audit") -> dict:
    target = next(row for row in catalog["targets"] if row["target_id"] == target_id)
    return homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_stage_receipt",
            "recorded_at": "2026-08-01T00:00:00Z",
            "catalog_sha256": catalog["catalog_sha256"],
            "target_id": target_id,
            "target_manifest_sha256": target["target_manifest_sha256"],
            "stage": stage,
            "node_sha256": "1" * 64,
            "status": "passed",
            "fixture_ids": [],
            "artifact_sha256s": ["2" * 64],
            "measurements": {},
            "notes": [],
            "boundary": {},
        }
    )


def _campaign(catalog: dict) -> dict:
    return homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_campaign",
            "created_at": "2026-08-01T00:00:00Z",
            "audit_sha256": "3" * 64,
            "catalog_sha256": catalog["catalog_sha256"],
            "tasks": [
                {
                    "task_id": "ffmpeg.stage.identity",
                    "target_id": "ffmpeg",
                    "task_type": "stage",
                    "stage": "local_identity_audit",
                    "status": "ready",
                    "assigned_node_sha256": "1" * 64,
                    "resource": "cpu",
                    "reason": "test",
                    "depends_on": [],
                    "required_output_kinds": ["earcrate_homelab_stage_receipt"],
                },
                {
                    "task_id": "ffmpeg.stage.fixture",
                    "target_id": "ffmpeg",
                    "task_type": "stage",
                    "stage": "real_fixture",
                    "status": "ready",
                    "assigned_node_sha256": "1" * 64,
                    "resource": "gpu-exclusive",
                    "reason": "test",
                    "depends_on": ["ffmpeg.stage.identity"],
                    "required_output_kinds": ["earcrate_homelab_stage_receipt"],
                },
            ],
            "summary": {"tasks": 2, "unresolved_targets": 1},
            "completion_gate": {"passed": False},
        }
    )


def _audit_and_campaign(catalog: dict) -> tuple[dict, dict]:
    target = next(row for row in catalog["targets"] if row["target_id"] == "ffmpeg")
    audit = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_audit",
            "audited_at": "2026-08-01T00:00:00Z",
            "catalog_sha256": catalog["catalog_sha256"],
            "inventory_sha256": "4" * 64,
            "node_sha256s": ["1" * 64],
            "fixture_status": {},
            "targets": [
                {
                    "target_id": "ffmpeg",
                    "display_name": "FFmpeg",
                    "target_class": "adopted_core",
                    "target_manifest_sha256": target["target_manifest_sha256"],
                    "assigned_node_id": "node-test",
                    "assigned_node_sha256": "1" * 64,
                    "feasibility": "ready",
                    "blockers": [],
                    "warnings": [],
                    "feasibility_evidence": {},
                    "completed_stages": [],
                    "failed_stages": [],
                    "refused_stages": [],
                    "missing_stages": list(target["required_stages"]),
                    "stage_evidence": {},
                    "audition_required": True,
                    "audition_acceptance_present": False,
                    "terminal_decision": None,
                    "decision_sha256": None,
                    "lifecycle": "awaiting_local_identity_audit",
                }
            ],
            "evidence_index": {},
            "object_warnings": [],
            "summary": {"targets": 1, "unresolved_targets": 1},
            "boundary": {"provider_processes_executed": False},
        }
    )
    campaign = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_campaign",
            "created_at": "2026-08-01T00:00:00Z",
            "audit_sha256": audit["audit_sha256"],
            "catalog_sha256": catalog["catalog_sha256"],
            "tasks": [],
            "summary": {"tasks": 0, "unresolved_targets": 1},
            "completion_gate": {"passed": False},
        }
    )
    return audit, campaign


def test_homelab_store_is_idempotent_chained_and_concurrency_safe(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    value = _stage_receipt(catalog)
    with HomelabStore(tmp_path / "store") as store:
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _index: store.ingest_object(value), range(8)))
        assert sum(1 for row in rows if row["created"]) == 1
        assert sum(1 for row in rows if not row["created"]) == 7
        doctor = store.doctor()
        assert doctor["ok"] is True, doctor
        assert doctor["object_count"] == 1
        assert doctor["event_count"] == 1
        assert store.load_object(value["receipt_sha256"]) == value


def test_homelab_scheduler_enforces_dependencies_leases_and_evidence(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    campaign = _campaign(catalog)
    evidence_a = _stage_receipt(catalog)
    evidence_b = _stage_receipt(catalog, stage="real_fixture")
    with HomelabStore(tmp_path / "store") as store:
        store.ingest_object(evidence_a)
        store.ingest_object(evidence_b)
        registered = store.register_campaign(campaign)
        assert registered["created"] is True
        lease_a = store.lease_next(worker_id="worker-a", resources=["cpu"], now=1000.0)
        assert lease_a and lease_a["task_id"] == "ffmpeg.stage.identity"
        assert store.lease_next(worker_id="worker-b", resources=["gpu-exclusive"], now=1000.0) is None
        heartbeat = store.heartbeat(
            campaign["campaign_sha256"], lease_a["task_id"], lease_a["lease_token"], extend_seconds=60, now=1001.0
        )
        assert heartbeat["lease_expires_at"] == 1061.0
        completed = store.complete_task(
            campaign["campaign_sha256"],
            lease_a["task_id"],
            lease_a["lease_token"],
            outcome="completed",
            evidence_sha256=evidence_a["receipt_sha256"],
            now=1002.0,
        )
        assert completed["status"] == "completed"
        lease_b = store.lease_next(worker_id="worker-b", resources=["gpu-exclusive"], now=1003.0)
        assert lease_b and lease_b["task_id"] == "ffmpeg.stage.fixture"
        retry = store.complete_task(
            campaign["campaign_sha256"],
            lease_b["task_id"],
            lease_b["lease_token"],
            outcome="failed",
            error="transient test failure",
            now=1004.0,
        )
        assert retry["status"] == "queued"
        assert store.lease_next(worker_id="worker-b", resources=["gpu-exclusive"], now=1005.0) is None
        lease_b2 = store.lease_next(worker_id="worker-b", resources=["gpu-exclusive"], now=1035.0)
        assert lease_b2 and lease_b2["attempt"] == 2
        store.complete_task(
            campaign["campaign_sha256"],
            lease_b2["task_id"],
            lease_b2["lease_token"],
            outcome="completed",
            evidence_sha256=evidence_b["receipt_sha256"],
            now=1036.0,
        )
        doctor = store.doctor()
        assert doctor["ok"] is True, doctor
        assert store.snapshot()["campaigns"]["completed"] == 1


def test_blind_review_separates_public_assignment_and_private_authority(tmp_path: Path) -> None:
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
    homelab_validate_seal(assignment)
    homelab_validate_seal(authority)
    assert "option_map" not in assignment
    assert assignment["private_authority_sha256"] == authority["authority_sha256"]
    assert (tmp_path / "public" / "option-A.wav").is_file()
    assert (tmp_path / "public" / "option-B.wav").is_file()
    candidate_option = next(option for option, role in authority["option_map"].items() if role == "candidate")
    submission = record_review_submission(
        assignment,
        reviewer_id="reviewer:test",
        review_token=prepared["review_token"],
        choice=candidate_option,
        dimensions={"bleed": 5, "transients": 4, "role_usefulness": 5},
    )
    ledger = adjudicate_review(catalog, assignment, authority, submission)
    homelab_validate_seal(ledger)
    assert ledger["verdict"] == "accept"
    assert ledger["blinded"] is True
    assert ledger["randomized"] is True
    assert ledger["assignment_sha256"] == assignment["assignment_sha256"]


def test_homelab_store_doctor_detects_object_tampering(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    value = _stage_receipt(catalog)
    store_root = tmp_path / "store"
    with HomelabStore(store_root) as store:
        row = store.ingest_object(value)
        target = store_root / row["relative_path"]
        target.write_text(json.dumps({"tampered": True}), encoding="utf-8")
        doctor = store.doctor()
        assert doctor["ok"] is False
        assert any(problem["check"] == "object" for problem in doctor["problems"])


def test_homelab_public_export_backup_restore_and_dashboard(tmp_path: Path) -> None:
    catalog = homelab_catalog()
    public_receipt = _stage_receipt(catalog)
    private_authority = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_private_assignment_authority",
            "created_at": "2026-08-01T00:00:00Z",
            "catalog_sha256": catalog["catalog_sha256"],
            "target_id": "demucs",
            "target_manifest_sha256": next(row["target_manifest_sha256"] for row in catalog["targets"] if row["target_id"] == "demucs"),
            "node_sha256": "1" * 64,
            "reviewer_id": "reviewer:test",
            "fixture_ids": ["fixture.pretty_lights.source_audio", "fixture.private_library.real"],
            "nonce": "secret-nonce",
            "option_map": {"A": "candidate", "B": "control"},
            "source_artifacts": {
                "candidate": {"sha256": "5" * 64, "bytes": 5},
                "control": {"sha256": "6" * 64, "bytes": 6},
            },
            "boundary": {"private_object": True, "must_not_enter_public_export": True},
        }
    )
    store_root = tmp_path / "store"
    with HomelabStore(store_root) as store:
        store.ingest_object(public_receipt, visibility="public")
        store.ingest_object(private_authority, visibility="private")
    export = export_public_store(store_root, tmp_path / "public-export")
    assert export["source_media_exported"] is False
    exported_text = (tmp_path / "public-export" / "manifest.json").read_text(encoding="utf-8")
    assert public_receipt["receipt_sha256"] in exported_text
    assert private_authority["authority_sha256"] not in exported_text

    try:
        backup_homelab_store(store_root, tmp_path / "refused.zip")
    except ValueError as exc:
        assert "private" in str(exc).lower()
    else:
        raise AssertionError("private Homelab backup unexpectedly required no acknowledgement")
    backup = backup_homelab_store(store_root, tmp_path / "homelab-backup.zip", acknowledge_private_state=True)
    assert backup["contains_private_state"] is True
    restored = restore_homelab_backup(
        backup["output"],
        tmp_path / "restored-store",
        approve_sha256=backup["raw_sha256"],
    )
    homelab_validate_seal(restored)
    assert restored["doctor"]["ok"] is True
    with HomelabStore(tmp_path / "restored-store") as store:
        assert store.load_object(public_receipt["receipt_sha256"]) == public_receipt
        assert store.load_object(private_authority["authority_sha256"], allow_private=True) == private_authority

    audit, campaign = _audit_and_campaign(catalog)
    dashboard = render_homelab_dashboard(audit, campaign, tmp_path / "dashboard.html")
    assert dashboard["bytes"] > 0
    dashboard_text = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "EarCrate Homelab Provider Arcade" in dashboard_text
    assert "source.mp3" not in dashboard_text


def test_homelab_zipapp_is_deterministic_and_operational(tmp_path: Path) -> None:
    first = tmp_path / "homelab-a.pyz"
    second = tmp_path / "homelab-b.pyz"
    for output in (first, second):
        process = subprocess.run(
            [sys.executable, str(ROOT / "build" / "make_homelab_zipapp.py"), "--output", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout)["ok"] is True
    assert first.read_bytes() == second.read_bytes()

    catalog_path = tmp_path / "catalog.json"
    catalog_process = subprocess.run(
        [sys.executable, str(first), "catalog", "--output", str(catalog_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert catalog_process.returncode == 0, catalog_process.stderr
    emitted = json.loads(catalog_path.read_text(encoding="utf-8"))
    homelab_validate_seal(emitted)
    assert emitted["summary"]["targets"] == 87
    store_process = subprocess.run(
        [sys.executable, str(first), "store-init", str(tmp_path / "zipapp-store")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert store_process.returncode == 0, store_process.stderr
    assert json.loads(store_process.stdout)["ok"] is True
    assert estate_sha256_file(first) == estate_sha256_file(second)
