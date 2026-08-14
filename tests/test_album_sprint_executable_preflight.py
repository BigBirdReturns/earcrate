from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "earcrate_album_sprint_preflight.py"
CAMPAIGN = ROOT / "configs" / "album_one" / "sprint-01" / "campaign.v1.json"
PREFLIGHT = ROOT / "configs" / "album_one" / "sprint-01" / "executable-preflight.v1.json"
RUNNER = ROOT / "scripts" / "RUN_ALBUM_ONE_SPRINT_01.ps1"

_spec = importlib.util.spec_from_file_location("earcrate_album_sprint_preflight", SCRIPT)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def report_with_no_bindings():
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    bindings = module.load_bindings(None, campaign, verify_bytes=False)
    return campaign, preflight, module.build_report(campaign, preflight, bindings)


def test_preflight_contract_is_sealed_and_bound_to_campaign() -> None:
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    assert campaign["contract_sha256"] == "85f3b31106dd6c5ee9e9687edce3ac13d8b2c69fb2763544e455e5faa3dd4516"
    assert preflight["base_campaign_sha256"] == campaign["contract_sha256"]
    assert preflight["preflight_contract_sha256"] == "11c992ddfcf11a4822879ae09babbd9726baf9b07e67d5c93baad1abe6312a5f"
    assert preflight["boundary"]["estate_execution_default"] == "closed"


def test_repo_only_retry_refuses_all_seven_lanes_for_exact_reasons() -> None:
    _campaign, _preflight, report = report_with_no_bindings()
    assert report["music_producing_lane_count"] == 0
    assert report["performance_realization_ready_count"] == 0
    assert report["estate_execution_authorized"] is False
    assert report["authorized_track_ids"] == []
    tracks = report["tracks"]

    a101 = tracks["A1-01"]
    assert a101["observations"]["retained_candidate_duration_seconds"] < 60.0
    assert {row["kind"] for row in a101["blockers"]} >= {
        "blocked_adapter_implementation", "blocked_full_form_adapter"
    }

    a102 = tracks["A1-02"]
    pack = next(row for row in a102["blockers"] if row["kind"] == "blocked_exact_artifact_pack")
    assert pack["missing_artifact_ids"] == [
        "score_pdf", "score_extraction", "score_reconstruction_midi",
        "score_proof_receipt", "mix_score", "mix_execution_ledger",
    ]
    assert any(row["kind"] == "blocked_performance_adapter" for row in a102["blockers"])

    a103 = tracks["A1-03"]
    assert a103["symbolic_evidence_ready"] is True
    assert a103["observations"]["decoded_float_pcm_max_abs"] == 0.0
    assert a103["observations"]["witness_duration_seconds"] < 120.0
    assert a103["observations"]["executable_note_events_present"] is False

    for track_id in ("A1-04", "A1-05"):
        row = tracks[track_id]
        assert any(item["kind"] == "blocked_full_form_adapter" for item in row["blockers"])
        switches = set(row["observations"]["campaign_template_switches"])
        assert not module.HOMELAB_REQUIRED_SWITCHES.issubset(switches)
        assert switches & module.HOMELAB_FORBIDDEN_SWITCHES
        assert any(item["kind"] == "blocked_adapter_implementation" for item in row["blockers"])

    assert any(row["kind"] == "blocked_adapter_implementation" for row in tracks["A1-06"]["blockers"])
    assert any(row["kind"] == "blocked_full_form_adapter" for row in tracks["A1-07"]["blockers"])
    assert tracks["A1-07"]["observations"]["positive_arc_reapplication_deferred"] is True


def test_complete_fake_bindings_do_not_launder_missing_music_adapters() -> None:
    campaign, preflight = module.campaign_and_contract(CAMPAIGN, PREFLIGHT)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw = {
            "kind": "earcrate_album_sprint_private_bindings",
            "contract_sha256": campaign["contract_sha256"],
            "tracks": {},
        }
        for track_id, spec in preflight["tracks"].items():
            rows = []
            for binding_id in spec["required_bindings"]:
                artifact = root / f"{track_id}-{binding_id}.bin"
                artifact.write_bytes(b"fixture")
                rows.append({"binding_id": binding_id, "artifact_path": str(artifact)})
            raw["tracks"][track_id] = {"bindings": rows}
        path = root / "bindings.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        bindings = module.load_bindings(path, campaign, verify_bytes=True)
        report = module.build_report(campaign, preflight, bindings)
        assert report["music_producing_lane_count"] == 0
        assert report["estate_execution_authorized"] is False
        assert report["authorized_track_ids"] == []


def test_workspace_application_removes_unauthorized_commands() -> None:
    _campaign, _preflight, report = report_with_no_bindings()
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        for track_id in report["tracks"]:
            command = workspace / "tracks" / track_id / "NEXT_COMMAND.ps1"
            command.parent.mkdir(parents=True, exist_ok=True)
            command.write_text("Write-Host should-not-run\n", encoding="utf-8")
        removed = module.apply_workspace(report, workspace)
        assert len(removed) == 7
        assert not list(workspace.rglob("NEXT_COMMAND.ps1"))
        stored = json.loads((workspace / "PREFLIGHT.json").read_text(encoding="utf-8"))
        assert stored["report_sha256"] == report["report_sha256"]


def test_powershell_uses_preflight_authority_before_execution() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "earcrate_album_sprint_preflight.py" in text
    assert "PreflightOnly" in text
    assert "estate_execution_authorized" in text
    assert "authorized_track_ids" in text
    assert "No complete music-producing Album adapter passed preflight" in text
    assert "Get-ChildItem" not in text or "NEXT_COMMAND.ps1 -Recurse" not in text
