from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .lower import TempoMap, lower_revision
from .store import ProjectStore
from .util import ValidationError, atomic_write_json, atomic_write_text, now_utc, sha256_file


def _seconds(samples: int, sample_rate: int) -> float:
    return float(samples) / float(sample_rate)


def export_edl(store: ProjectStore, project_id: str, *, revision_sha: str | None = None, path: str | Path | None = None) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    program = lower_revision(revision)
    sr = int(program["sample_rate"])
    events = []
    for event in program["events"]:
        source = revision["sources"][event["source_id"]]
        events.append({
            "event_id": event["event_id"],
            "clip_id": event["clip_id"],
            "track_id": event["track_id"],
            "track_role": event["track_role"],
            "source_id": event["source_id"],
            "source_path": source["stems"][event["stem"]],
            "stem": event["stem"],
            "timeline_start_sample": event["timeline_start_sample"],
            "timeline_start_s": _seconds(event["timeline_start_sample"], sr),
            "active_samples": event["active_samples"],
            "active_s": _seconds(event["active_samples"], sr),
            "render_samples": event["render_samples"],
            "source_start_sample": event["source_start_sample"],
            "source_end_sample": event["source_end_sample"],
            "source_start_s": _seconds(event["source_start_sample"], sr),
            "source_end_s": _seconds(event["source_end_sample"], sr),
            "gain_db": event["gain_db"],
            "pan": event["pan"],
            "transform": event["transform"],
            "envelope": event["envelope"],
            "decision_id": event.get("decision_id"),
        })
    edl = {
        "schema_version": 1,
        "kind": "earcrate_project_edl",
        "project_id": project_id,
        "revision_sha": revision["revision_sha"],
        "program_sha": program["program_sha"],
        "exported_at": now_utc(),
        "sample_rate": sr,
        "tempo_map": program["tempo_map"],
        "events": events,
        "transitions": program["transitions"],
        "master_actions": program["master_actions"],
        "source_identities": program["source_identities"],
    }
    destination = Path(path).expanduser().resolve() if path else store.exports_dir(project_id) / f"{revision['revision_sha']}.edl.json"
    atomic_write_json(destination, edl)
    receipt = {
        "ok": True,
        "format": "edl.json",
        "project_id": project_id,
        "revision_sha": revision["revision_sha"],
        "program_sha": program["program_sha"],
        "path": str(destination),
        "sha256": sha256_file(destination),
        "event_count": len(events),
        "transition_count": len(program["transitions"]),
    }
    store.append_event(project_id, {"event": "export", "at": now_utc(), **receipt})
    return receipt


def _rpp_quote(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def export_rpp(store: ProjectStore, project_id: str, *, revision_sha: str | None = None, path: str | Path | None = None) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    program = lower_revision(revision)
    sr = int(program["sample_rate"])
    bpm = float(program["tempo_map"][0]["bpm"])
    by_track: dict[str, list[Mapping[str, Any]]] = {}
    for event in program["events"]:
        by_track.setdefault(str(event["track_id"]), []).append(event)
    lines = [
        '<REAPER_PROJECT 0.1 "7.0/x64" 1700000000',
        f"  // EARCRATE_PROJECT_ID {project_id}",
        f"  // EARCRATE_REVISION_SHA {revision['revision_sha']}",
        f"  // EARCRATE_PROGRAM_SHA {program['program_sha']}",
        f"  RIPPLE 0",
        f"  GROUPOVERRIDE 0 0 0",
        f"  AUTOXFADE 1",
        f"  TEMPO {bpm:.9f} 4 4",
        f"  SAMPLERATE {sr} 0 0",
    ]
    track_map = {str(track["track_id"]): track for track in revision["tracks"]}
    for track_id in sorted(by_track):
        track = track_map.get(track_id) or {"name": track_id, "role": "aux"}
        lines.extend([
            "  <TRACK",
            f"    NAME {_rpp_quote(str(track.get('name') or track_id))}",
            f"    // EARCRATE_TRACK_ROLE {track.get('role')}",
            "    VOLPAN 1 0 -1 -1 1",
        ])
        for event in sorted(by_track[track_id], key=lambda item: (int(item["timeline_start_sample"]), str(item["event_id"]))):
            source = revision["sources"][event["source_id"]]
            source_path = str(source["stems"][event["stem"]])
            position = _seconds(int(event["timeline_start_sample"]), sr)
            length = _seconds(int(event["active_samples"]), sr)
            source_offset = _seconds(int(event["source_start_sample"]), sr)
            volume = 10.0 ** (float(event["gain_db"]) / 20.0)
            pan = float(event["pan"])
            lines.extend([
                "    <ITEM",
                f"      POSITION {position:.12f}",
                f"      LENGTH {length:.12f}",
                f"      SOFFS {source_offset:.12f}",
                f"      VOLPAN {volume:.12f} {pan:.12f} 1 -1",
                f"      NAME {_rpp_quote(str(event['clip_id']))}",
                f"      NOTES {_rpp_quote('revision=' + revision['revision_sha'] + ' event=' + event['event_id'] + ' decision=' + str(event.get('decision_id') or ''))}",
                "      <SOURCE WAVE",
                f"        FILE {_rpp_quote(source_path)}",
                "      >",
                "    >",
            ])
        lines.append("  >")
    lines.append(">")
    destination = Path(path).expanduser().resolve() if path else store.exports_dir(project_id) / f"{revision['revision_sha']}.rpp"
    atomic_write_text(destination, "\n".join(lines) + "\n")
    receipt = {
        "ok": True,
        "format": "rpp",
        "project_id": project_id,
        "revision_sha": revision["revision_sha"],
        "program_sha": program["program_sha"],
        "path": str(destination),
        "sha256": sha256_file(destination),
        "event_count": len(program["events"]),
        "transition_count": len(program["transitions"]),
    }
    store.append_event(project_id, {"event": "export", "at": now_utc(), **receipt})
    return receipt


def export_project(store: ProjectStore, project_id: str, fmt: str, *, revision_sha: str | None = None, path: str | Path | None = None) -> dict[str, Any]:
    fmt = fmt.lower().strip()
    if fmt in {"edl", "edl.json", "json"}:
        return export_edl(store, project_id, revision_sha=revision_sha, path=path)
    if fmt in {"rpp", "reaper"}:
        return export_rpp(store, project_id, revision_sha=revision_sha, path=path)
    raise ValidationError(f"unsupported export format: {fmt}")
