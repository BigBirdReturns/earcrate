from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest import mock
import zipfile


from earcrate.estate.discover import redact_estate_inventory, scan_estate
from earcrate.estate.model import (
    default_estate_policy,
    estate_architecture,
    estate_sha256_file,
    estate_validate_seal,
    write_estate_json,
)
from earcrate.estate.plan import (
    apply_estate_plan,
    propose_estate_plan,
    rollback_estate_apply,
    verify_estate_apply,
)
from earcrate.estate.rig import capture_rig_capabilities, propose_local_acceptance_campaign

ROOT = Path(__file__).resolve().parent.parent


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _seed_estate(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo-snapshot"
    (repo / "earcrate").mkdir(parents=True)
    (repo / "build").mkdir()
    (repo / "AGENTS.md").write_text("authority\n", encoding="utf-8")
    (repo / "earcrate" / "module.py").write_text("ENGINE_DISPLAY_VERSION = 'v0.8.30'\n", encoding="utf-8")
    (repo / "accidental-source.mp3").write_bytes(b"ID3" + b"source" * 50)

    workspace = tmp_path / "Legacy EarCrate"
    agent = workspace / "agent"
    work = workspace / "work"
    agent.mkdir(parents=True)
    work.mkdir()
    config = _write_json(
        agent / "config.json",
        {
            "master_root": str(tmp_path / "Music"),
            "working_root": str(work),
            "agent_root": str(agent),
            "stems_root": str(workspace / "stems"),
            "playlists_root": str(workspace / "playlists"),
        },
    )
    connection = sqlite3.connect(agent / "earcrate.sqlite")
    connection.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE atom_judgments (id INTEGER PRIMARY KEY, verdict TEXT)")
    connection.commit()
    connection.close()

    project = workspace / "projects" / "project_demo"
    _write_json(
        project / "project.json",
        {
            "project_id": "project_demo",
            "active_revision_sha": "a" * 64,
            "lineage": ["a" * 64],
            "cursor": 0,
        },
    )
    _write_json(
        project / "revisions" / ("a" * 64 + ".json"),
        {
            "kind": "earcrate_project_revision",
            "schema_version": 1,
            "project_id": "project_demo",
            "revision_sha": "a" * 64,
        },
    )
    (project / "commands.jsonl").write_text('{"event":"project_created"}\n', encoding="utf-8")

    l3 = agent / "cache" / "L3"
    l3.mkdir(parents=True)
    (l3 / "paired.bin").write_bytes(b"stem-data")
    _write_json(l3 / "paired.meta.json", {"key": "paired", "tier": "warm", "source_identity": "pcm"})
    (l3 / "orphan.bin").write_bytes(b"orphan")

    evidence = tmp_path / "proofs"
    evidence.mkdir()
    _write_json(
        evidence / "candidate.json",
        {
            "kind": "earcrate_floor_release_candidate",
            "schema_version": 1,
            "candidate_sha256": "b" * 64,
            "status": "pending",
        },
    )
    _write_json(
        evidence / "receipt.json",
        {
            "kind": "earcrate_mix_render_receipt",
            "schema_version": 1,
            "receipt_sha256": "c" * 64,
        },
    )
    (evidence / "candidate-listen.mp3").write_bytes(b"ID3" + b"candidate" * 60)
    for name in ("proof-a.zip", "proof-b.zip"):
        with zipfile.ZipFile(evidence / name, "w") as archive:
            archive.writestr("receipt.json", '{"ok":true}\n')

    pointer_dir = tmp_path / "pointer"
    pointer_dir.mkdir()
    _write_json(pointer_dir / "earcrate_workspace.json", {"config_json": str(config)})
    stale_dir = tmp_path / "stale-pointer"
    stale_dir.mkdir()
    _write_json(stale_dir / "earcrate_workspace.json", {"config_json": str(tmp_path / "missing" / "config.json")})

    music = tmp_path / "Music"
    music.mkdir()
    (music / "Reference Song.mp3").write_bytes(b"ID3" + b"music" * 80)

    return {
        "repo": repo,
        "workspace": workspace,
        "evidence": evidence,
        "pointer": pointer_dir,
        "stale": stale_dir,
        "music": music,
    }


def test_estate_inventory_ingests_versions_workspaces_artifacts_and_conflicts(tmp_path: Path) -> None:
    roots = _seed_estate(tmp_path)
    inventory = scan_estate(roots.values(), hash_mode="duplicates")
    estate_validate_seal(inventory)

    roles = {root["role"] for root in inventory["roots"]}
    assert {"repository", "workspace", "source_library"}.issubset(roles)
    classes = inventory["summary"]["classifications"]
    assert classes["database"] == 1
    assert classes["project_index"] == 1
    assert classes["project_revision"] == 1
    assert classes["orphan_artifact"] == 1
    assert classes["release_candidate"] == 1
    assert classes["audition_audio"] == 1
    assert classes["source_audio"] >= 1
    assert inventory["duplicates"], "the two proof archives must be proven exact duplicates"

    issue_names = {issue["issue"] for issue in inventory["issues"]}
    assert "stale_workspace_pointer" in issue_names
    assert "conflicting_workspace_pointers" in issue_names
    assert "orphan_artifact_blob" in issue_names
    assert "media_inside_repository" in issue_names

    sqlite_item = next(item for item in inventory["items"] if item["classification"] == "database")
    assert sqlite_item["metadata"]["sqlite_status"] == "parsed"
    assert {row["name"] for row in sqlite_item["metadata"]["sqlite_objects"]} >= {"projects", "atom_judgments"}

    repo_module = next(item for item in inventory["items"] if item["relative_path"] == "earcrate/module.py")
    assert repo_module["classification"] == "repository"
    assert repo_module["metadata"]["declared_versions"] == ["v0.8.30"]

    redacted = redact_estate_inventory(inventory)
    estate_validate_seal(redacted)
    assert all("path" not in root or root.get("path") is None for root in redacted["roots"])
    assert all("absolute_path" not in item for item in redacted["items"])


def test_estate_plan_apply_verify_and_rollback_are_copy_only_and_signature_gated(tmp_path: Path) -> None:
    roots = _seed_estate(tmp_path)
    policy = default_estate_policy()
    inventory = scan_estate([roots["evidence"], roots["music"]], policy=policy, hash_mode="all")
    estate_root = tmp_path / "Managed Estate"
    plan = propose_estate_plan(inventory, estate_root, policy=policy)
    estate_validate_seal(plan)

    assert plan["summary"]["source_files_deleted"] == 0
    assert plan["summary"]["databases_merged"] == 0
    source_operation = next(op for op in plan["operations"] if op["classification"] == "source_audio")
    assert source_operation["action"] == "reference"
    copy_operations = [op for op in plan["operations"] if op["action"] == "copy"]
    assert copy_operations and all(op["expected_sha256"] for op in copy_operations)

    try:
        apply_estate_plan(plan, approve_sha256="0" * 64)
    except ValueError as exc:
        assert "approval mismatch" in str(exc)
    else:
        raise AssertionError("wrong approval unexpectedly applied the estate plan")

    source_before = {
        op["source_path"]: estate_sha256_file(op["source_path"])
        for op in copy_operations
    }
    receipt = apply_estate_plan(plan, approve_sha256=plan["plan_sha256"])
    estate_validate_seal(receipt)
    assert receipt["source_files_deleted"] == 0
    assert receipt["databases_merged"] == 0
    assert receipt["created"]
    assert verify_estate_apply(receipt)["ok"] is True
    assert source_before == {path: estate_sha256_file(path) for path in source_before}

    rollback = rollback_estate_apply(receipt, approve_sha256=receipt["receipt_sha256"])
    estate_validate_seal(rollback)
    assert rollback["source_files_affected"] == 0
    assert rollback["removed"]
    assert all(not (estate_root / row["target_relative_path"]).exists() for row in rollback["removed"])
    assert source_before == {path: estate_sha256_file(path) for path in source_before}


def test_estate_rig_and_campaign_expose_local_gpu_cpu_library_and_audition_work(tmp_path: Path) -> None:
    roots = _seed_estate(tmp_path)
    inventory = scan_estate(roots.values(), hash_mode="evidence")

    def fake_runner(argv, timeout):
        if Path(argv[0]).name == "nvidia-smi" and "--query-gpu=name,uuid,memory.total,driver_version" in argv:
            return {"returncode": 0, "stdout": "NVIDIA GeForce RTX 4060, GPU-test, 8192, 999.1\n", "stderr": "", "timed_out": False}
        if Path(argv[0]).name == "nvidia-smi":
            return {"returncode": 0, "stdout": "CUDA Version: 12.8\n", "stderr": "", "timed_out": False}
        return {"returncode": 0, "stdout": f"{Path(argv[0]).name} test-version\n", "stderr": "", "timed_out": False}

    fake_packages = {
        "numpy": "2.0",
        "scipy": "1.14",
        "librosa": "0.11",
        "soundfile": "0.13",
        "mido": "1.3",
        "torch": "2.8",
        "torchaudio": "2.8",
        "demucs": "4.0",
        "basic-pitch": "0.4",
        "allin1": "1.1",
        "pyrubberband": "0.4",
        "sounddevice": None,
        "onnxruntime": None,
        "onnxruntime-gpu": None,
        "transformers": None,
        "faiss-cpu": None,
        "faiss-gpu": None,
    }
    with mock.patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), mock.patch(
        "earcrate.estate.hardware._estate_package_versions", return_value=fake_packages
    ):
        rig = capture_rig_capabilities(roots=[tmp_path], command_runner=fake_runner)
    estate_validate_seal(rig)
    assert rig["summary"]["nvidia_gpu_count"] == 1
    assert rig["nvidia"]["gpus"][0]["name"] == "NVIDIA GeForce RTX 4060"
    assert rig["boundary"]["no_heavy_model_inference_run"] is True

    campaign = propose_local_acceptance_campaign(inventory, rig)
    estate_validate_seal(campaign)
    tasks = {task["task_id"]: task for task in campaign["tasks"]}
    assert tasks["local.gpu.demucs_stems"]["status"] == "ready"
    assert tasks["local.provider.allin1"]["status"] == "ready"
    assert tasks["local.provider.rubberband_ab"]["status"] == "ready"
    assert tasks["local.human.audition_queue"]["status"] == "needs_human"
    assert tasks["local.campaign.review_changes_future_choice"]["status"] == "blocked"
    assert campaign["audition_queue"]
    assert campaign["boundary"]["cloud_gates_are_not_local_acceptance"] is True


def test_estate_schemas_and_package_cli_sweep(tmp_path: Path) -> None:
    schema_kinds = {
        "earcrate_estate_architecture_v1.schema.json": "earcrate_estate_architecture",
        "earcrate_estate_policy_v1.schema.json": "earcrate_estate_policy",
        "earcrate_estate_inventory_v1.schema.json": "earcrate_estate_inventory",
        "earcrate_estate_plan_v1.schema.json": "earcrate_estate_plan",
        "earcrate_estate_apply_receipt_v1.schema.json": "earcrate_estate_apply_receipt",
        "earcrate_estate_rollback_receipt_v1.schema.json": "earcrate_estate_rollback_receipt",
        "earcrate_rig_capability_receipt_v1.schema.json": "earcrate_rig_capability_receipt",
        "earcrate_local_acceptance_campaign_v1.schema.json": "earcrate_local_acceptance_campaign",
    }
    for filename, kind in schema_kinds.items():
        schema = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        assert schema["properties"]["kind"]["const"] == kind
        assert schema["properties"]["schema_version"]["const"] == 1

    roots = _seed_estate(tmp_path)
    output = tmp_path / "sweep"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "earcrate",
            "estate",
            "sweep",
            "--root",
            str(roots["workspace"]),
            "--root",
            str(roots["evidence"]),
            "--estate-root",
            str(tmp_path / "estate"),
            "--output-dir",
            str(output),
            "--hash-mode",
            "evidence",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    summary = json.loads(process.stdout)
    assert summary["ok"] is True
    assert summary["mutation"] == "report files only; scanned roots unchanged"
    for filename in (
        "estate.architecture.json",
        "estate.policy.json",
        "estate.rig.json",
        "estate.inventory.json",
        "estate.inventory.redacted.json",
        "estate.plan.json",
        "estate.campaign.json",
    ):
        assert (output / filename).is_file()
