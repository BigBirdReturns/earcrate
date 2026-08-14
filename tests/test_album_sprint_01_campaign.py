from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "earcrate_album_sprint_01.py"
CONTRACT = ROOT / "configs" / "album_one" / "sprint-01" / "campaign.v1.json"
POWERSHELL = ROOT / "scripts" / "RUN_ALBUM_ONE_SPRINT_01.ps1"

_spec = importlib.util.spec_from_file_location("earcrate_album_sprint_01", SCRIPT)
assert _spec and _spec.loader
sprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sprint)


def test_campaign_is_sealed_parallel_full_form_and_fail_closed() -> None:
    campaign = sprint.contract(CONTRACT)
    assert campaign["contract_sha256"] == (
        "d6950f41246629762a717e66765a4b869afe4c500318cfccc46732c28bffcb2c"
    )
    assert [row["track_id"] for row in campaign["tracks"]] == list(sprint.TRACK_IDS)
    assert campaign["program_truth"]["proving_track_is_not_album_mutex"] is True
    assert campaign["owner_time_budget"]["frontiers_per_track_max"] == 1
    assert campaign["owner_time_budget"]["cuts_per_frontier_max"] == 4
    assert "machine_work_ready" not in campaign["terminal_states"]
    assert all(
        "payoff_or_release" in row["full_form"]["required_functions"]
        for row in campaign["tracks"]
    )
    assert all(row["machine_can_progress_without_frontier_bindings"] is False for row in campaign["tracks"])
    assert all((row.get("entrypoint") or {}).get("template") is None for row in campaign["tracks"])


def test_one_command_prepares_all_seven_dossiers() -> None:
    campaign = sprint.contract(CONTRACT)
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary) / "album-sprint"
        result = sprint.prepare(campaign, workspace)
        assert result["ok"] is True
        assert (workspace / "TASK_QUEUE.json").is_file()
        assert (workspace / "private" / "source-bindings.private.template.json").is_file()
        for track_id in sprint.TRACK_IDS:
            root = workspace / "tracks" / track_id
            assert (root / "TRACK_TASK.json").is_file()
            assert (root / "RUNBOOK.md").is_file()
            assert (root / "TRACK_RESULT.private.template.json").is_file()
        status = sprint.status(campaign, workspace)
        assert set(status["track_states"]) == set(sprint.TRACK_IDS)


def test_dossier_materialization_does_not_claim_adapter_readiness() -> None:
    campaign = sprint.contract(CONTRACT)
    tracks = sprint.by_track(campaign)
    template = sprint.bindings_template(campaign)
    resolved, missing = sprint.resolve_bindings(campaign, template, verify_bytes=False)

    children = sprint.task(campaign, tracks["A1-02"], resolved["A1-02"], missing["A1-02"])
    assert children["initial_state"] == "campaign_task_materialized"
    assert children["readiness"]["tool_contract_ready"] is False
    blocker = children["readiness"]["blockers"][0]
    assert blocker["kind"] == "blocked_exact_artifact_pack"
    assert blocker["missing_artifact_ids"] == list(sprint.CHILDREN_SCORE_ARTIFACTS)

    flim = sprint.task(campaign, tracks["A1-03"], resolved["A1-03"], missing["A1-03"])
    assert flim["initial_state"] == "campaign_task_materialized"
    assert flim["readiness"]["symbolic_evidence_ready"] is True
    assert flim["readiness"]["performance_realization_ready"] is False
    blocker = flim["readiness"]["blockers"][0]
    assert blocker["kind"] == "blocked_performance_realization"
    assert blocker["decoded_float_pcm_max_abs"] == 0.0
    assert blocker["observed_duration_seconds"] < blocker["minimum_duration_seconds"]


def test_frontier_admission_requires_full_form_disclosed_distinct_cuts() -> None:
    campaign = sprint.contract(CONTRACT)
    track = sprint.by_track(campaign)["A1-04"]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        audio = root / "Animal_into_Toxic_full_form.wav"
        audio.write_bytes(b"valid fixture bytes")
        notes = root / "CUT_NOTES.md"
        notes.write_text("Animal-derived body; Toxic identity fixed.\n", encoding="utf-8")
        frontier = {
            "cuts": [{
                "name": audio.name,
                "artifact_path": str(audio),
                "duration_seconds": 75.0,
                "musical_delta": "Animal-derived rhythmic body under Toxic",
                "reproduction_receipt_sha256": ["a" * 64, "b" * 64],
            }],
            "cut_notes_path": str(notes),
            "form_functions": ["setup", "body", "payoff_or_release"],
            "musical_delta_disclosed": True,
            "shared_dominant_defect": False,
        }
        normalized = sprint.validate_frontier(track, frontier)
        assert len(normalized["cuts"]) == 1
        broken = dict(frontier)
        broken["shared_dominant_defect"] = True
        try:
            sprint.validate_frontier(track, broken)
        except sprint.SprintError as exc:
            assert "non-discriminating" in str(exc)
        else:
            raise AssertionError("shared-defect frontier was admitted")


def test_exact_blocker_requires_runnable_contract() -> None:
    campaign = sprint.contract(CONTRACT)
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary) / "album-sprint"
        sprint.prepare(campaign, workspace)
        result_path = Path(temporary) / "result.json"
        result_path.write_text(json.dumps({
            "schema_version": 1,
            "kind": "earcrate_album_sprint_track_result",
            "sprint_id": campaign["sprint_id"],
            "contract_sha256": campaign["contract_sha256"],
            "track_id": "A1-06",
            "state": "blocked_exact_source",
            "detail": "",
            "frontier": None,
            "blocker": {
                "missing_binding_ids": [
                    "pinkpantheress_stateside_exact_edition",
                    "curated_bhangra_comparison_corpus",
                    "non_bhangra_negative_control_corpus",
                ],
                "detail": "Exact source and held-out corpora not yet bound.",
                "runnable_contract_ready": True,
            },
        }), encoding="utf-8")
        recorded = sprint.record(campaign, workspace, result_path)
        assert recorded["state"] == "blocked_exact_source"


def test_powershell_is_the_single_fail_closed_fanout_entrypoint() -> None:
    text = POWERSHELL.read_text(encoding="utf-8")
    assert "earcrate_album_sprint_01.py" in text
    assert "earcrate_album_sprint_preflight.py" in text
    assert "dispatch" in text
    assert "ExecuteReadyAdapters" in text
    assert "estate_execution_authorized" in text
    assert "TASK_QUEUE.json" in text
