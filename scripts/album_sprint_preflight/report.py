from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .common import base_result, blocker, seal, write_json
from .primary import children, flim, pretty_lights
from .secondary import beggin, gesture, homelab


def inspect_track(track_id: str, spec: Mapping[str, Any], campaign_track: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    probe = str(spec.get("repo_probe") or "")
    if probe == "pretty_lights_release_candidate":
        result = pretty_lights(track_id, spec, bindings)
    elif probe == "children_score_compiler":
        result = children(track_id, spec, bindings)
    elif probe == "flim_symbolic_report":
        result = flim(track_id, spec)
    elif probe == "homelab_factory":
        result = homelab(track_id, spec, campaign_track, bindings)
    elif probe == "gesture_adapter":
        result = gesture(track_id, spec, bindings)
    elif probe == "gold_v9_contract":
        result = beggin(track_id, spec, bindings)
    else:
        result = base_result(track_id, str(spec.get("adapter") or ""))
        result["blockers"].append(blocker(
            "blocked_adapter_implementation", "adapter_contract", f"unknown repo probe: {probe}"
        ))
    result["blockers"] = sorted(result["blockers"], key=lambda row: (row["stage"], row["kind"], row["detail"]))
    result["music_producing_path_ready"] = bool(
        result["tool_contract_ready"] and result["binding_contract_ready"]
        and result["representative_invocation_ready"]
        and result["performance_realization_ready"] and not result["blockers"]
    )
    if result["performance_realization_ready"]:
        result["state"] = "performance_realization_ready"
    elif result["symbolic_evidence_ready"]:
        result["state"] = "symbolic_evidence_ready"
    elif result["tool_contract_ready"]:
        result["state"] = "tool_contract_ready"
    return result


def build_report(campaign: Mapping[str, Any], preflight: Mapping[str, Any], bindings: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    campaign_tracks = {str(row["track_id"]): row for row in campaign.get("tracks") or []}
    tracks: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for track_id, spec in (preflight.get("tracks") or {}).items():
        result = inspect_track(track_id, spec, campaign_tracks[track_id], bindings.get(track_id) or {})
        tracks[track_id] = result
        counts[result["state"]] = counts.get(result["state"], 0) + 1
    lane_count = sum(bool(row["music_producing_path_ready"]) for row in tracks.values())
    return seal({
        "schema_version": 1, "kind": "earcrate_album_sprint_executable_preflight_report",
        "sprint_id": campaign["sprint_id"], "campaign_sha256": campaign["contract_sha256"],
        "preflight_contract_sha256": preflight["preflight_contract_sha256"],
        "tracks": tracks, "state_counts": counts,
        "music_producing_lane_count": lane_count,
        "performance_realization_ready_count": sum(bool(row["performance_realization_ready"]) for row in tracks.values()),
        "estate_execution_authorized": lane_count > 0,
        "authorized_track_ids": [track_id for track_id, row in tracks.items() if row["music_producing_path_ready"]],
        "private_paths_included": False, "source_audio_exported": False,
    }, "report_sha256")


def apply_workspace(report: Mapping[str, Any], workspace: Path) -> list[str]:
    root = workspace.expanduser().absolute()
    if not root.is_dir():
        raise RuntimeError(f"workspace does not exist: {root}")
    removed: list[str] = []
    authorized = set(report.get("authorized_track_ids") or [])
    for path in root.glob("tracks/A1-*/NEXT_COMMAND.ps1"):
        if path.parent.name not in authorized:
            path.unlink(missing_ok=True)
            removed.append(str(path))
    write_json(root / "PREFLIGHT.json", report)
    return removed
