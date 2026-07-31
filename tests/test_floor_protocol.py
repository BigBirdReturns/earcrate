from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from earcrate.floor.catalog import floor_discover_provider_catalog
from earcrate.floor.cli import floor_capability
from earcrate.floor.gaps import floor_gap_register
from earcrate.floor.interop import floor_export_crate
from earcrate.floor.model import (
    FloorError,
    FloorProtocolError,
    floor_read_json,
    floor_seal_evaluation_ledger,
    floor_seal_evaluation_policy,
    floor_seal_phrase_contract,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_seal_provider_result,
    floor_seal_review_patch,
    floor_seal_time_map,
    floor_sha256_file,
    floor_write_json_atomic,
)
from earcrate.floor.protocol import floor_conformance_run, floor_invoke_provider
from earcrate.floor.reference import floor_write_reference_provider
from earcrate.floor.schema import floor_schema_bundle
from earcrate.floor.tournament import floor_run_tournament


def _reference(tmp_path: Path, name: str = "reference") -> tuple[Path, dict, dict]:
    root = tmp_path / name
    floor_write_reference_provider(root)
    return root, floor_read_json(root / "reference.floor-provider.json"), floor_read_json(root / "request.json")


def _write_provider(root: Path, body: str, *, provider_id: str, capability: str = "file.echo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    script = root / "provider.py"
    script.write_text(body, encoding="utf-8", newline="\n")
    manifest = floor_seal_provider_manifest(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_provider_manifest",
            "provider_id": provider_id,
            "provider_version": "1.0.0",
            "display_name": provider_id,
            "protocol": {"name": "earcrate-floor-stdio-json", "version": 1},
            "entrypoint": {
                "argv": ["${PYTHON}", "${FLOOR_MANIFEST_DIR}/provider.py"],
                "working_directory": "${FLOOR_MANIFEST_DIR}",
                "environment": {},
            },
            "capabilities": [
                {
                    "capability": capability,
                    "input_media_kinds": ["text/plain"],
                    "result_kinds": ["observation", "measurement", "derived_artifact", "refusal", "review_patch"],
                    "evidence_branches": ["symbolic"],
                    "evidence_tiers": ["community_symbolic_witness"],
                    "network_policy": "forbidden",
                    "determinism": "unknown",
                    "max_runtime_seconds": 30,
                    "max_output_bytes": 1 << 20,
                    "parameter_schema": {},
                    "metadata": {},
                }
            ],
            "authority": {
                "may_emit": ["observation", "measurement", "derived_artifact", "refusal", "review_patch"],
                "may_not_emit": [],
            },
            "supply_chain": {
                "license_expression": "CC0-1.0",
                "source_uri": "",
                "artifact_sha256": floor_sha256_file(script),
                "model_identities": [],
                "signatures": [],
            },
            "metadata": {},
        }
    )
    path = root / "provider.floor-provider.json"
    floor_write_json_atomic(path, manifest)
    return path


def test_floor_capability_and_gap_register_are_honest() -> None:
    capability = floor_capability()
    gaps = floor_gap_register()
    assert capability["ready"] is True
    assert capability["protocol"]["name"] == "earcrate-floor-stdio-json"
    assert capability["security_boundary"]["shell_used"] is False
    assert capability["security_boundary"]["os_network_sandbox_proved"] is False
    assert capability["conformance_is_quality"] is False
    assert capability["catalog_is_selection"] is False
    assert capability["tournament_winner_is_canonical"] is False
    assert gaps["counts"]["implemented"] >= 16
    statuses = {row["gap_id"]: row["status"] for row in gaps["gaps"]}
    assert statuses["network_declaration"] == "partial"
    assert statuses["normative_license"] == "owner_decision_required"


def test_floor_request_identity_ignores_machine_local_paths(tmp_path: Path) -> None:
    left = tmp_path / "machine-a" / "audio.txt"
    right = tmp_path / "machine-b" / "renamed.txt"
    left.parent.mkdir(parents=True)
    right.parent.mkdir(parents=True)
    left.write_text("same bytes\n", encoding="utf-8")
    right.write_bytes(left.read_bytes())

    def request(path: Path, uri: str) -> dict:
        return floor_seal_provider_request(
            {
                "schema_version": 1,
                "kind": "earcrate_floor_provider_request",
                "capability": "file.echo",
                "evidence_branch": "symbolic",
                "evidence_tier": "community_symbolic_witness",
                "inputs": [
                    {
                        "artifact_id": "source",
                        "sha256": floor_sha256_file(path),
                        "size_bytes": path.stat().st_size,
                        "media_kind": "text/plain",
                        "branch": "symbolic",
                        "ancestor_branches": ["symbolic"],
                        "path": str(path),
                        "uri": uri,
                    }
                ],
                "parameters": {"mode": "identity"},
                "allowed_result_kinds": ["measurement"],
                "network_policy": "forbidden",
            }
        )

    first = request(left, "file:///machine-a/audio.txt")
    second = request(right, "file:///machine-b/renamed.txt")
    assert first["request_sha256"] == second["request_sha256"]
    assert first["request_id"] == second["request_id"]


def test_floor_time_phrase_rights_and_review_contracts() -> None:
    time_map = floor_seal_time_map(
        {
            "segments": [
                {
                    "source_artifact_id": "deck-a",
                    "target_start": 0,
                    "target_end": 4,
                    "source_start": 8,
                    "source_end": 12,
                    "mode": "continuous",
                },
                {
                    "source_artifact_id": "deck-a",
                    "target_start": 4,
                    "target_end": 8,
                    "source_start": 20,
                    "source_end": 24,
                    "mode": "jump",
                },
            ]
        }
    )
    assert [row["mode"] for row in time_map["segments"]] == ["continuous", "jump"]
    contract = floor_seal_phrase_contract(
        {
            "role": "vocal_hook",
            "start_beat": 0,
            "length_beats": 8,
            "meter": {"numerator": 4, "denominator": 4},
            "transforms": {"allowed_operations": ["tempo", "transpose"]},
            "hard_constraints": {"clean_entry": True},
            "identity_obligations": [{"kind": "recognizability", "minimum": 0.8}],
            "future_obligations": [{"kind": "cadence", "by_beat": 8}],
            "rights": {"assertion_status": "asserted", "allowed_uses": ["research"]},
        }
    )
    assert contract["rights"]["provider_may_not_decide_legality"] is True
    assert contract["identity_obligations"]
    patch = floor_seal_review_patch(
        {
            "target_revision_sha256": "0" * 64,
            "target_object": "PerformanceScore",
            "operations": [{"op": "replace", "path": "/events/0/pitch", "value": 64}],
            "reason": "arrival is premature",
            "evidence_refs": ["human-review-1"],
            "invalidation_hints": ["midi", "rack", "mixscore"],
        }
    )
    assert patch["applied"] is False
    try:
        floor_seal_review_patch({**patch, "applied": True})
    except FloorError as exc:
        assert "unapplied" in str(exc)
    else:
        raise AssertionError("Floor accepted an already-applied provider ReviewPatch")


def test_reference_provider_conformance_custodies_inputs_outputs_and_repeatability(tmp_path: Path) -> None:
    root, manifest, request = _reference(tmp_path)
    report = floor_conformance_run(
        root / "reference.floor-provider.json",
        request,
        output_dir=tmp_path / "conformance",
        repeat=2,
    )
    assert report["complete"] is True
    assert report["checks"]["semantic_result_repeatable"] is True
    assert report["checks"]["os_network_sandbox_proved"] is False
    result = floor_read_json(tmp_path / "conformance" / "run-01" / "result.json")
    receipt = floor_read_json(tmp_path / "conformance" / "run-01" / "invocation.receipt.json")
    assert result["status"] == "success"
    assert result["artifacts"][0]["path"] == "echo.txt"
    assert receipt["process"]["shell"] is False
    assert receipt["network"]["host_enforcement"] == "declaration_only"
    assert receipt["input_custody"][0]["verified"] is True
    assert receipt["output_custody"][0]["verified"] is True
    assert manifest["authority"]["canonical_write_access"] is False


def test_floor_refuses_wrong_input_identity_path_traversal_and_authority_laundering(tmp_path: Path) -> None:
    root, manifest, request = _reference(tmp_path, "bad-input")
    changed = deepcopy(request)
    changed.pop("request_sha256", None)
    changed.pop("request_id", None)
    changed["inputs"][0]["sha256"] = "0" * 64
    changed = floor_seal_provider_request(changed)
    try:
        floor_invoke_provider(root / "reference.floor-provider.json", changed, artifact_dir=tmp_path / "bad-input-artifacts")
    except FloorProtocolError as exc:
        assert "identity changed" in str(exc)
    else:
        raise AssertionError("Floor accepted an input whose bytes did not match the request")

    traversal_body = '''import json, os, sys\nfrom pathlib import Path\nr=json.loads(sys.stdin.read())\nout=Path(os.environ["FLOOR_ARTIFACT_DIR"]).parent/"escape.txt"\nout.write_text("escape", encoding="utf-8")\nprint(json.dumps({"schema_version":1,"kind":"earcrate_floor_provider_result","request_sha256":r["request_sha256"],"provider_manifest_sha256":os.environ["FLOOR_PROVIDER_MANIFEST_SHA256"],"provider_id":"org.test.traversal","provider_version":"1.0.0","status":"success","emissions":[{"kind":"measurement","subject":"source","payload":{"x":1},"evidence_refs":["source"],"confidence":1.0}],"artifacts":[{"artifact_id":"escape","relative_path":"../escape.txt","sha256":"0"*64,"size_bytes":6,"media_kind":"text/plain"}],"refusals":[],"metrics":{},"metadata":{}}))\n'''
    traversal_manifest = _write_provider(tmp_path / "traversal", traversal_body, provider_id="org.test.traversal")
    traversal_request = deepcopy(request)
    traversal_request.pop("request_sha256", None)
    traversal_request.pop("request_id", None)
    traversal_request = floor_seal_provider_request(traversal_request)
    try:
        floor_invoke_provider(traversal_manifest, traversal_request, artifact_dir=tmp_path / "traversal-artifacts")
    except FloorProtocolError as exc:
        assert "unsafe" in str(exc) or "escapes" in str(exc)
    else:
        raise AssertionError("Floor accepted provider path traversal")

    laundering_body = '''import json, os, sys\nr=json.loads(sys.stdin.read())\nprint(json.dumps({"schema_version":1,"kind":"earcrate_floor_provider_result","request_sha256":r["request_sha256"],"provider_manifest_sha256":os.environ["FLOOR_PROVIDER_MANIFEST_SHA256"],"provider_id":"org.test.launder","provider_version":"1.0.0","status":"success","emissions":[{"kind":"observation","subject":"source","payload":{"SongGenome":{"canonical":True}},"evidence_refs":["source"],"confidence":1.0}],"artifacts":[],"refusals":[],"metrics":{},"metadata":{}}))\n'''
    laundering_manifest = _write_provider(tmp_path / "launder", laundering_body, provider_id="org.test.launder")
    laundering_request = deepcopy(request)
    laundering_request.pop("request_sha256", None)
    laundering_request.pop("request_id", None)
    laundering_request["allowed_result_kinds"] = ["observation"]
    laundering_request = floor_seal_provider_request(laundering_request)
    try:
        floor_invoke_provider(laundering_manifest, laundering_request, artifact_dir=tmp_path / "launder-artifacts")
    except FloorError as exc:
        assert "forbidden authority" in str(exc) or "authority-bearing" in str(exc)
    else:
        raise AssertionError("Floor accepted a provider claiming SongGenome authority")


def test_floor_conformance_detects_nondeterministic_semantics(tmp_path: Path) -> None:
    root, _manifest, request = _reference(tmp_path, "nondeterministic-fixture")
    body = '''import json, os, random, sys\nr=json.loads(sys.stdin.read())\nprint(json.dumps({"schema_version":1,"kind":"earcrate_floor_provider_result","request_sha256":r["request_sha256"],"provider_manifest_sha256":os.environ["FLOOR_PROVIDER_MANIFEST_SHA256"],"provider_id":"org.test.random","provider_version":"1.0.0","status":"success","emissions":[{"kind":"measurement","subject":"source","payload":{"random":random.random()},"evidence_refs":["source"],"confidence":1.0}],"artifacts":[],"refusals":[],"metrics":{},"metadata":{}}))\n'''
    manifest = _write_provider(tmp_path / "random-provider", body, provider_id="org.test.random")
    report = floor_conformance_run(manifest, request, output_dir=tmp_path / "random-conformance", repeat=2)
    assert report["complete"] is False
    assert report["checks"]["repeatability_checked"] is True
    assert report["checks"]["semantic_result_repeatable"] is False


def test_floor_catalog_refuses_conflicting_identity_and_filters_compatibility(tmp_path: Path) -> None:
    root, manifest, request = _reference(tmp_path, "catalog-fixture")
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    first = deepcopy(manifest)
    second = deepcopy(manifest)
    first.pop("manifest_sha256", None)
    second.pop("manifest_sha256", None)
    first["description"] = "first"
    second["description"] = "second"
    floor_write_json_atomic(catalog_root / "one.floor-provider.json", floor_seal_provider_manifest(first))
    floor_write_json_atomic(catalog_root / "two.floor-provider.json", floor_seal_provider_manifest(second))
    catalog = floor_discover_provider_catalog([catalog_root], request=request, include_earcrate_adapters=False)
    assert catalog["counts"]["accepted"] == 0
    assert any(row["code"] == "conflicting_provider_identity" for row in catalog["refusals"])

    incompatible_request = deepcopy(request)
    incompatible_request.pop("request_sha256", None)
    incompatible_request.pop("request_id", None)
    incompatible_request["evidence_branch"] = "audio"
    incompatible_request["evidence_tier"] = "blind_audio_inference"
    incompatible_request["inputs"][0]["branch"] = "audio"
    incompatible_request["inputs"][0]["ancestor_branches"] = ["audio"]
    incompatible_request = floor_seal_provider_request(incompatible_request)
    single = tmp_path / "single-catalog"
    single.mkdir()
    floor_write_json_atomic(single / "reference.floor-provider.json", manifest)
    catalog = floor_discover_provider_catalog([single], request=incompatible_request, include_earcrate_adapters=False)
    assert catalog["counts"]["incompatible"] == 1
    assert catalog["accepted"] == []


def test_floor_tournament_requires_independent_evaluation_and_never_claims_truth() -> None:
    policy = floor_seal_evaluation_policy(
        {
            "hard_gates": [{"metric": "complete", "operator": "true", "value": True}],
            "lexicographic_stages": [
                {"stage": "identity", "weights": {"f1": 1.0}},
                {"stage": "cost", "weights": {"latency": 1.0}},
            ],
            "higher_is_better": ["f1"],
            "lower_is_better": ["latency"],
        }
    )
    evaluations = []
    for provider_id, manifest_digit, result_digit, f1, latency in (
        ("provider-a", "1", "3", 0.91, 10.0),
        ("provider-b", "2", "4", 0.91, 8.0),
    ):
        evaluations.append(
            floor_seal_evaluation_ledger(
                {
                    "provider_id": provider_id,
                    "provider_manifest_sha256": manifest_digit * 64,
                    "request_sha256": "5" * 64,
                    "result_sha256": result_digit * 64,
                    "evaluator": {"evaluator_id": "independent-judge", "version": "1"},
                    "metrics": {"f1": f1, "latency": latency},
                    "hard_gate_evidence": {"complete": True},
                }
            )
        )
    report = floor_run_tournament(policy, evaluations)
    assert report["winner"]["provider_id"] == "provider-b"
    assert report["canonical_authority"] is False
    assert report["selection_requires_earcrate_adjudication"] is True

    bad = deepcopy(evaluations[0])
    bad.pop("evaluation_sha256", None)
    bad["evaluator"]["evaluator_id"] = bad["provider_id"]
    try:
        floor_seal_evaluation_ledger(bad)
    except FloorError as exc:
        assert "independent" in str(exc)
    else:
        raise AssertionError("Floor accepted provider self-evaluation")


def test_floor_crate_exports_mappings_checksums_and_no_source_media(tmp_path: Path) -> None:
    root, manifest, request = _reference(tmp_path, "crate-fixture")
    run = floor_invoke_provider(
        root / "reference.floor-provider.json",
        request,
        artifact_dir=tmp_path / "invoke" / "artifacts",
    )
    exported = floor_export_crate(
        manifest_value=manifest,
        request_value=request,
        result_value=run["result"],
        receipt_value=run["receipt"],
        output_dir=tmp_path / "crate",
        artifact_root=run["artifact_dir"],
        copy_derived=True,
    )
    crate = exported["crate"]
    assert crate["source_media_copied"] is False
    assert crate["derived_artifacts_copied"] == ["derived/echo.txt"]
    assert set(crate["standards_mappings"]) == {"JAMS", "W3C PROV", "ODRL", "RO-Crate"}
    assert (tmp_path / "crate" / "checksums.sha256").is_file()
    assert not (tmp_path / "crate" / "sample.txt").exists()
    assert (tmp_path / "crate" / "derived" / "echo.txt").read_bytes() == (root / "sample.txt").read_bytes()


def test_floor_committed_schemas_match_runtime_bundle() -> None:
    root = Path(__file__).resolve().parent.parent
    bundle = floor_schema_bundle()
    assert len(bundle) >= 8
    for name, expected in bundle.items():
        actual = json.loads((root / "schemas" / name).read_text(encoding="utf-8"))
        assert actual == expected
        assert actual["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_floor_package_and_single_file_execute_the_same_reference_protocol(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    package_capability = subprocess.run(
        [sys.executable, "-m", "earcrate", "floor", "capability"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert package_capability.returncode == 0, package_capability.stdout + package_capability.stderr
    package_value = json.loads(package_capability.stdout)

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    single_capability = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "floor", "capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert single_capability.returncode == 0, single_capability.stdout + single_capability.stderr
    single_value = json.loads(single_capability.stdout)
    assert package_value["capability_sha256"] == single_value["capability_sha256"]

    scaffold = tmp_path / "single-reference"
    scaffold_run = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "floor", "scaffold", str(scaffold)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert scaffold_run.returncode == 0, scaffold_run.stdout + scaffold_run.stderr
    conformance = subprocess.run(
        [
            sys.executable,
            str(root / "dist" / "earcrate.py"),
            "floor",
            "conformance",
            str(scaffold / "reference.floor-provider.json"),
            str(scaffold / "request.json"),
            str(tmp_path / "single-conformance"),
            "--repeat",
            "2",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert conformance.returncode == 0, conformance.stdout + conformance.stderr
    report = json.loads(conformance.stdout)
    assert report["complete"] is True
    assert report["checks"]["semantic_result_repeatable"] is True
