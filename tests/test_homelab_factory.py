from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.estate.homelab_factory import (
    adjudicate_factory_review,
    bootstrap_workspace,
    build_circulation_packet,
    build_quality_archive,
    compile_factory_campaign,
    compile_factory_manifest,
    compile_preference_update,
    load_adapter_policy,
    prepare_factory_review,
    submit_factory_review,
    run_factory,
    verify_workspace,
)
from earcrate.estate.homelab_specimens import bind_specimen_source, load_json, seal


CONFIG = ROOT / "configs" / "homelab_factory"
EXAMPLES = ROOT / "tests" / "fixtures" / "homelab_factory"


def _load_inputs(tmp_path: Path):
    suite = load_json(CONFIG / "specimen-suite.v1.json")
    policy = load_json(CONFIG / "provider-role-policy.v1.json")
    catalog = load_json(EXAMPLES / "synthetic.catalog.json")
    audit = load_json(EXAMPLES / "synthetic.audit.json")
    source_a = tmp_path / "four-seasons.wav"
    source_b = tmp_path / "maneskin.wav"
    source_a.write_bytes(b"RIFF-four-seasons")
    source_b.write_bytes(b"RIFF-maneskin")
    case_id = "beggin-four-seasons-x-maneskin-handoff"
    bindings = [
        bind_specimen_source(
            suite,
            case_id=case_id,
            source_id="four_seasons_beggin",
            artifact_path=source_a,
            bound_by="test",
            reason="test fixture",
        ),
        bind_specimen_source(
            suite,
            case_id=case_id,
            source_id="maneskin_beggin",
            artifact_path=source_b,
            bound_by="test",
            reason="test fixture",
        ),
    ]
    return suite, policy, catalog, audit, bindings, case_id


def test_factory_compiles_provider_graph_and_covering_recipes(tmp_path: Path) -> None:
    suite, policy, catalog, audit, bindings, case_id = _load_inputs(tmp_path)
    manifest = compile_factory_manifest(
        suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=policy,
        profile="core",
        case_ids=[case_id],
        max_recipes_per_case=8,
    )
    assert manifest["kind"] == "earcrate_homelab_factory_manifest"
    assert manifest["selected_case_ids"] == [case_id]
    assert manifest["recipes"]
    assert len(manifest["recipes"]) <= 8
    assert manifest["search_policy"]["cartesian_product_forbidden"] is True
    campaign = compile_factory_campaign(manifest)
    task_types = {row["task_type"] for row in campaign["tasks"]}
    assert {"specimen_trial", "factory_recipe", "factory_archive", "factory_review", "factory_preference", "factory_circulation"}.issubset(task_types)
    assert campaign["summary"]["recipes"] == len(manifest["recipes"])


def test_quality_archive_and_review_keep_mapping_private(tmp_path: Path) -> None:
    suite, policy, catalog, audit, bindings, case_id = _load_inputs(tmp_path)
    manifest = compile_factory_manifest(
        suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=policy,
        profile="smoke",
        case_ids=[case_id],
        max_recipes_per_case=3,
    )
    runs = []
    run_paths = {}
    for index, recipe in enumerate(manifest["recipes"][:3]):
        audio = tmp_path / f"candidate-{index}.wav"
        audio.write_bytes(b"RIFF" + bytes([index]) * 64)
        run = seal(
            {
                "schema_version": 1,
                "kind": "earcrate_homelab_factory_run",
                "recorded_at": "2026-08-09T00:00:00Z",
                "factory_manifest_sha256": manifest["manifest_sha256"],
                "recipe_sha256": recipe["recipe_sha256"],
                "case_id": case_id,
                "task_id": f"run-{index}",
                "worker_id": "test",
                "gpu": None,
                "outcome": "passed",
                "provider_receipt_identities": [],
                "source_binding_sha256s": [row["binding_sha256"] for row in bindings],
                "artifacts": [{"name": audio.name, "sha256": __import__("hashlib").sha256(audio.read_bytes()).hexdigest(), "bytes": audio.stat().st_size, "media_kind": "audio/wav"}],
                "measurements": {"signal": {"impact": 0.4 + index * 0.1, "timing": 0.8, "bleed": 0.2, "room_continuity": 0.6, "recognizability": 0.9, "vocal_authority": 0.9}},
                "notes": [],
                "authority": {"canonical_musical_write": False, "human_acceptance": False, "provider_adoption": False, "release_decision": False},
            }
        )
        runs.append(run)
        run_paths[run["run_sha256"]] = audio
    archive = build_quality_archive(manifest=manifest, case_id=case_id, runs=runs, frontier_size=3)
    prepared = prepare_factory_review(
        archive,
        run_paths=run_paths,
        public_directory=tmp_path / "public",
        private_directory=tmp_path / "private",
        reviewer_id="operator:owner",
        seed=7,
    )
    assignment = prepared["assignment"]
    assert "option_map" not in assignment
    assert "source_artifacts" not in assignment
    assert assignment["public_metrics"]["candidate_specific_signal_metrics_withheld_until_submission"] is True
    choice = sorted(assignment["options"])[0]
    submission = submit_factory_review(
        assignment,
        reviewer_id="operator:owner",
        review_token=prepared["review_token"],
        choice=choice,
        dimensions={"vocal authority": 5, "phrase placement": 4},
        notes=["test"],
    )
    ledger = adjudicate_factory_review(assignment, prepared["private_authority"], submission)
    assert ledger["winner_run_sha256"]
    update = compile_preference_update(ledger, archive=archive, manifest=manifest)
    assert update["scope"] == "fixture_and_review_dimensions_only"
    assert update["review_patch"]["unrelated_organs_bit_identical_required"] is True


def test_circulation_redacts_paths_and_private_review_fields(tmp_path: Path) -> None:
    suite, policy, catalog, audit, bindings, case_id = _load_inputs(tmp_path)
    manifest = compile_factory_manifest(
        suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=policy,
        profile="smoke",
        case_ids=[case_id],
        max_recipes_per_case=2,
    )
    campaign = compile_factory_campaign(manifest)
    private = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_private_assignment_authority",
            "created_at": "2026-08-09T00:00:00Z",
            "assignment_sha256": "a" * 64,
            "archive_sha256": "b" * 64,
            "case_id": case_id,
            "reviewer_id": "owner",
            "option_map": {"A": "c" * 64},
            "source_artifacts": {"c": {"path": "Z:\\redaction-fixture\\candidate.wav"}},
            "authority_seed": "secret",
            "authority_commitment": "d" * 64,
            "review_token": "token",
            "review_token_sha256": "e" * 64,
        }
    )
    packet = build_circulation_packet(
        manifest=manifest,
        campaign=campaign,
        objects=[private, bindings[0]],
        output_directory=tmp_path / "circulation",
    )
    assert packet["boundary"]["private_paths_exported"] is False
    text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "circulation").glob("*.json"))
    assert "Z:\\redaction-fixture" not in text
    assert '"option_map": "redacted"' in text
    assert '"review_token": "redacted"' in text


def test_bootstrap_workspace_is_verifiable(tmp_path: Path) -> None:
    suite, policy, catalog, audit, bindings, case_id = _load_inputs(tmp_path)
    workspace = tmp_path / "workspace"
    result = bootstrap_workspace(
        workspace,
        suite=suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=policy,
        profile="smoke",
        case_ids=[case_id],
        max_recipes_per_case=2,
    )
    assert result["manifest"]["manifest_sha256"]
    verification = verify_workspace(workspace)
    assert verification["ok"] is True
    assert verification["failures"] == []


def test_default_adapter_policy_is_explicit_and_nonmagical() -> None:
    policy = load_adapter_policy()
    assert policy["adapters"]["demucs"]["handler"] == "demucs"
    assert policy["adapters"]["audio-separator"]["handler"] == "audio_separator"
    assert "model_filename" not in policy["adapters"]["audio-separator"]



def test_factory_runner_executes_full_fake_graph_to_human_boundary(tmp_path: Path) -> None:
    suite, policy, catalog, audit, bindings, case_id = _load_inputs(tmp_path)
    workspace = tmp_path / "factory-run"
    bootstrap_workspace(
        workspace,
        suite=suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=policy,
        profile="core",
        case_ids=[case_id],
        max_recipes_per_case=3,
    )
    writer = (
        "from pathlib import Path; import sys; "
        "Path(sys.argv[1]).write_bytes(b'RIFF'+sys.argv[2].encode('utf-8'))"
    )
    adapter_policy = {
        "adapters": {
            "*": {
                "handler": "command",
                "argv": [sys.executable, "-c", writer, "{output_dir}/artifact.wav", "{case_id}"],
                "timeout_seconds": 60,
            }
        },
        "recipe_plugins": {
            "same_composition_different_era": {
                "argv": [sys.executable, "-c", writer, "{output_dir}/candidate.wav", "{recipe_sha256}"],
                "timeout_seconds": 60,
            }
        },
    }
    result = run_factory(workspace, adapter_policy=adapter_policy, max_parallel_cpu=4)
    assert result["human_review_queue"]
    assert result["summary"].get("human_pending") == 1
    public = Path(result["human_review_queue"][0]["public_directory"])
    assert (public / "assignment.json").is_file()
    assert len(list(public.glob("*.wav"))) >= 2
    assert verify_workspace(workspace)["ok"] is True
