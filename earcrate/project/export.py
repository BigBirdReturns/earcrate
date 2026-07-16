from earcrate.core.deps import *
from earcrate.core.util import now_utc, safe_name
from earcrate.project.model import ScoreRevision
from earcrate.project.store import _atomic_json


def _atomic_text(path: Path, text: str) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return path


def export_project_edl(revision: ScoreRevision, path: Path) -> Path:
    revision.validate()
    payload = {
        "schema_version": 1,
        "format": "earcrate_project_edl",
        "project_id": revision.project_id,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
        "taste_profile": revision.intent["taste_profile"],
        "bpm": float(revision.arrangement["bpm"]),
        "tracks": revision.tracks,
        "transitions": revision.transitions,
        "master_actions": revision.master_actions,
        "source_registry": revision.source_registry,
        "exported_at": now_utc(),
    }
    return _atomic_json(path, payload)


def _q(text: str) -> str:
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'


def export_project_rpp(revision: ScoreRevision, path: Path) -> Path:
    revision.validate()
    bpm = float(revision.arrangement["bpm"])
    lines = [
        '<REAPER_PROJECT 0.1 "7.0" 17179869184',
        "  RIPPLE 0",
        "  AUTOXFADE 1",
        f"  TEMPO {bpm:.9f} 4 4",
        "  <EXTSTATE EARCRATE",
        f"    PROJECT_ID {_q(revision.project_id)}",
        f"    REVISION_SHA {_q(revision.revision_sha)}",
        f"    SCORE_SHA {_q(revision.score_sha)}",
        f"    TASTE_PROFILE {_q(str(revision.intent['taste_profile']['id']))}",
        "  >",
    ]
    for track in revision.tracks:
        lines.extend([
            "  <TRACK",
            f"    NAME {_q(str(track['track_id']) + ' // ' + str(track['role']))}",
            f"    VOLPAN {10.0 ** (float(track.get('gain_db') or 0.0) / 20.0):.12f} 0 -1 -1 1",
            f"    MUTESOLO {1 if track.get('muted') else 0} {1 if track.get('solo') else 0} 0",
        ])
        for clip in track.get("clips") or []:
            source = revision.source_registry[clip["source_ref_id"]]
            start = float(clip["timeline_start_beat"]) * 60.0 / bpm
            length = float(clip["duration_beats"]) * 60.0 / bpm
            source_offset = float(clip["source_start_s"])
            speed_ratio = float((clip.get("transform") or {}).get("speed_ratio") or 1.0)
            lines.extend([
                "    <ITEM",
                f"      POSITION {start:.12f}",
                f"      LENGTH {length:.12f}",
                "      LOOP 1",
                f"      MUTE {1 if clip.get('muted') else 0} 0",
                f"      NAME {_q(str(clip.get('ear_role') or clip.get('role')) + ' // ' + str(clip['clip_id']))}",
                f"      VOLPAN {10.0 ** (float(clip.get('gain_db') or 0.0) / 20.0):.12f} {float(clip.get('pan') or 0.0):.9f} 1 -1",
                f"      SOFFS {source_offset:.12f}",
                f"      PLAYRATE {speed_ratio:.12f} 1 0 -1 0 0.0025",
                "      <SOURCE WAVE",
                f"        FILE {_q(source['path'])}",
                "      >",
                "    >",
            ])
        lines.append("  >")
    for index, transition in enumerate(revision.transitions):
        seconds = float(transition["boundary_beat"]) * 60.0 / bpm
        lines.append(
            f"  MARKER {1000 + index} {seconds:.12f} "
            + _q(f"{transition['technique']} // {transition['transition_id']}")
            + " 0 0 1 B 0 0 1"
        )
    lines.extend(["  <NOTES 0 2", "    |EarCrate master actions:"])
    for action in revision.master_actions:
        lines.append("    |" + json.dumps(action, ensure_ascii=False, sort_keys=True))
    lines.extend(["  >", ">"])
    return _atomic_text(path, "\n".join(lines) + "\n")


def export_project_sheet(revision: ScoreRevision, path: Path) -> Path:
    revision.validate()
    lines = [
        "# EarCrate live score",
        "",
        f"Project: `{revision.project_id}`  ",
        f"Revision: `{revision.revision_sha}`  ",
        f"Score: `{revision.score_sha}`  ",
        f"Persona: `{revision.intent['taste_profile']['id']}`  ",
        f"BPM: `{float(revision.arrangement['bpm']):.4f}`",
        "",
        "## Clips",
        "",
        "| Beat | Rail | Ear role | Source | Gain | Stem | Muted | Locked |",
        "|---:|---|---|---|---:|---|---|---|",
    ]
    locked = {(str(x.get("target_type")), str(x.get("target_id"))) for x in revision.locks}
    for track in revision.tracks:
        for clip in track.get("clips") or []:
            source = revision.source_registry[clip["source_ref_id"]]
            lines.append(
                f"| {float(clip['timeline_start_beat']):.2f} | {track['role']} | "
                f"{clip.get('ear_role') or clip.get('role')} | `{safe_name(Path(source['path']).name)}` | "
                f"{float(clip.get('gain_db') or 0.0):.2f} dB | {(clip.get('stem_choice') or {}).get('choice')} | "
                f"{'yes' if clip.get('muted') else 'no'} | {'yes' if ('clip', str(clip['clip_id'])) in locked else 'no'} |"
            )
    lines.extend(["", "## Transitions", ""])
    for transition in revision.transitions:
        lines.append(
            f"- Beat {float(transition['boundary_beat']):.2f}: **{transition['technique']}**, "
            f"{float(transition['duration_beats']):.2f} beats, renderable={bool(transition['renderable'])}, "
            f"`{transition['transition_id']}`"
        )
    lines.extend(["", "## Mastering actions", ""])
    if revision.master_actions:
        for action in revision.master_actions:
            lines.append(f"- **{action.get('kind')}** `{json.dumps(action.get('parameters') or {}, sort_keys=True)}`")
    else:
        lines.append("- Premaster revision: mastering has not been resolved yet.")
    lines.extend(["", "## Decision receipts", ""])
    for decision in revision.decisions:
        lines.append(
            f"- `{decision.get('decision_id')}`: {decision.get('kind')} selected "
            f"`{json.dumps(decision.get('selected'), ensure_ascii=False, sort_keys=True)}`; "
            f"renderable={decision.get('renderable')}"
        )
    return _atomic_text(path, "\n".join(lines) + "\n")


def export_project_bundle(revision: ScoreRevision, destination: Path) -> Dict[str, Any]:
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    base = f"{revision.revision_sha}"
    edl = export_project_edl(revision, destination / f"{base}.edl.json")
    rpp = export_project_rpp(revision, destination / f"{base}.rpp")
    sheet = export_project_sheet(revision, destination / f"{base}.sheet.md")
    receipt = {
        "ok": True,
        "project_id": revision.project_id,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
        "edl": str(edl),
        "rpp": str(rpp),
        "sheet": str(sheet),
        "exported_at": now_utc(),
    }
    _atomic_json(destination / f"{base}.exports.json", receipt)
    return receipt
