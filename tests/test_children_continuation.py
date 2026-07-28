from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from earcrate.specimen.continuation_dense import children_compose_adjacent_move
from earcrate.specimen.gate import specimen_build_buffalo_gate
from earcrate.specimen.model import specimen_read_json
from test_buffalo_specimen import _compile


def test_children_adjacent_move_is_legal_novel_deterministic_and_refuses_negative_control(tmp_path: Path) -> None:
    score = _compile(tmp_path, "adjacent")
    score_root = Path(score["output_dir"])
    first = children_compose_adjacent_move(
        score_root / "score.answer-key.json",
        tmp_path / "continuation-first",
        sample_rate=8000,
    )
    second = children_compose_adjacent_move(
        score_root / "score.answer-key.json",
        tmp_path / "continuation-second",
        sample_rate=8000,
    )
    receipt = first["receipt"]
    assert first["ok"] is True and first["complete"] is True
    assert receipt["legal"] is True
    assert receipt["negative_control_refused"] is True
    assert receipt["negative_control"]["commit_refused"] is True
    assert receipt["negative_control"]["proof"]["legal"] is False
    assert receipt["rhythmic_identity_passed"] is True
    assert receipt["rhythmic_obligation"]["rhythmic_identity_passed"] is True
    assert receipt["rhythmic_obligation"]["duration_multiset_preserved"] is True
    assert receipt["novelty"]["literal_copy_detected"] is False
    assert receipt["novelty"]["pitch_sequence_changed"] is True
    assert receipt["novelty"]["harmony_sequence_changed"] is True
    assert receipt["open_obligation_count"] == 0
    assert receipt["committed_event_count"] > 0
    assert receipt["midi"]["selected_event_count"] == receipt["committed_event_count"]
    assert receipt["midi"]["executed_event_count"] == receipt["committed_event_count"]
    assert receipt["midi"]["refused_event_count"] == 0
    assert Path(first["midi_path"]).is_file()
    assert Path(first["neutral_path"]).is_file()
    assert receipt["receipt_sha256"] == second["receipt"]["receipt_sha256"]
    assert receipt["composition_sha256"] == second["receipt"]["composition_sha256"]
    assert receipt["midi"]["semantic_sha256"] == second["receipt"]["midi"]["semantic_sha256"]
    assert receipt["midi"]["neutral_pcm_f32le_sha256"] == second["receipt"]["midi"]["neutral_pcm_f32le_sha256"]

    gate = specimen_build_buffalo_gate(
        manifest=specimen_read_json(score_root / "specimen.manifest.bound.json"),
        score_ledger=specimen_read_json(score_root / "score.observation-ledger.json"),
        score_branch_receipt=specimen_read_json(score_root / "score.branch.receipt.json"),
        continuation_receipt=receipt,
    )
    statuses = {row["organ_id"]: row["status"] for row in gate["receipt"]["organs"]}
    assert statuses["proof_carrying_adjacent_move"] == "passed"
    assert statuses["sealed_rack_realization"] == "blocked"
    assert gate["buffalo_gate_passed"] is False


def test_children_adjacent_move_schema_matches_runtime(tmp_path: Path) -> None:
    score = _compile(tmp_path, "continuation-schema")
    result = children_compose_adjacent_move(
        Path(score["output_dir"]) / "score.answer-key.json",
        tmp_path / "continuation-schema-output",
        sample_rate=8000,
    )
    root = Path(__file__).resolve().parent.parent
    schema = specimen_read_json(root / "schemas" / "earcrate_children_adjacent_move_receipt_v1.schema.json")
    receipt = result["receipt"]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == receipt["schema_version"] == 1
    assert schema["properties"]["kind"]["const"] == receipt["kind"]
    assert schema["properties"]["legal"]["const"] is True
    assert schema["properties"]["negative_control_refused"]["const"] is True
    assert schema["properties"]["rhythmic_identity_passed"]["const"] is True
    assert schema["properties"]["novelty"]["properties"]["pitch_sequence_changed"]["const"] is True
    assert schema["properties"]["novelty"]["properties"]["harmony_sequence_changed"]["const"] is True


def test_children_adjacent_move_package_cli_and_single_file_execute_the_same_authority(tmp_path: Path) -> None:
    score = _compile(tmp_path, "continuation-cli")
    root = Path(__file__).resolve().parent.parent
    score_root = Path(score["output_dir"])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    package_output = tmp_path / "package-continuation"
    package = subprocess.run(
        [
            sys.executable,
            "-m",
            "earcrate.specimen",
            "children-continuation",
            str(score_root),
            str(package_output),
            "--sample-rate",
            "8000",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    package_payload = json.loads(package.stdout)
    assert package_payload["legal"] is True
    assert package_payload["negative_control_refused"] is True
    assert package_payload["rhythmic_identity_passed"] is True

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    single_output = tmp_path / "single-continuation"
    single = subprocess.run(
        [
            sys.executable,
            str(root / "dist" / "earcrate.py"),
            "buffalo",
            "children-continuation",
            str(score_root),
            str(single_output),
            "--sample-rate",
            "8000",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert single.returncode == 0, single.stdout + single.stderr
    single_payload = json.loads(single.stdout)
    assert single_payload["legal"] is True
    assert single_payload["negative_control_refused"] is True
    assert single_payload["rhythmic_identity_passed"] is True
    assert package_payload["receipt"]["receipt_sha256"] == single_payload["receipt"]["receipt_sha256"]
    assert package_payload["receipt"]["midi"]["semantic_sha256"] == single_payload["receipt"]["midi"]["semantic_sha256"]
