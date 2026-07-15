from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from .util import (
    ValidationError,
    canonical_json,
    deep_copy_json,
    finite_number,
    now_utc,
    require_keys,
    sha256_json,
    sorted_unique,
    stable_id,
)

PROJECT_SCHEMA_VERSION = 1
REVISION_SCHEMA_VERSION = 1
RENDER_PROGRAM_SCHEMA_VERSION = 1

TRACK_ROLES = {"floor", "foreground", "spark", "aux", "master"}
CLIP_ROLES = {"drum_anchor", "bass", "harmony", "vocal", "texture", "fx", "full"}
TRANSITION_TECHNIQUES = {
    "start",
    "hard_cut",
    "hard_cut_pickup",
    "hard_cut_to_air",
    "impact_drop",
    "beatmatch_blend",
    "long_blend",
    "hook_blend_over_bed",
    "echo_out",
    "acapella_bridge",
    "bass_swap",
    "double_drop",
    "bed_ride",
}
MASTER_ACTION_TYPES = {"low_shelf", "presence_shelf", "loudness_normalize", "true_peak_limit"}


def revision_payload(revision: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable content payload whose hash identifies a revision.

    Human-readable timestamps and the self-referential ``revision_sha`` are excluded.
    Everything that can change audible output, provenance, policy, or authorship stays in.
    """
    payload = deep_copy_json(dict(revision))
    payload.pop("revision_sha", None)
    payload.pop("created_at", None)
    return payload


def compute_revision_sha(revision: Mapping[str, Any]) -> str:
    return sha256_json(revision_payload(revision))


def seal_revision(revision: Mapping[str, Any]) -> dict[str, Any]:
    out = deep_copy_json(dict(revision))
    out.setdefault("schema_version", REVISION_SCHEMA_VERSION)
    out.setdefault("created_at", now_utc())
    out["revision_sha"] = compute_revision_sha(out)
    validate_revision(out, require_sealed=True)
    return out


def render_program_payload(program: Mapping[str, Any]) -> dict[str, Any]:
    out = deep_copy_json(dict(program))
    out.pop("program_sha", None)
    out.pop("created_at", None)
    return out


def seal_render_program(program: Mapping[str, Any]) -> dict[str, Any]:
    out = deep_copy_json(dict(program))
    out.setdefault("schema_version", RENDER_PROGRAM_SCHEMA_VERSION)
    out.setdefault("created_at", now_utc())
    out["program_sha"] = sha256_json(render_program_payload(out))
    validate_render_program(out)
    return out


def default_tracks() -> list[dict[str, Any]]:
    return [
        {"track_id": "floor", "role": "floor", "name": "Floor", "clips": []},
        {"track_id": "foreground", "role": "foreground", "name": "Foreground", "clips": []},
        {"track_id": "spark", "role": "spark", "name": "Spark", "clips": []},
        {"track_id": "aux", "role": "aux", "name": "Aux", "clips": []},
    ]


def new_revision(
    *,
    project_id: str,
    parent_revision_sha: str | None,
    created_by: Mapping[str, Any],
    intent: Mapping[str, Any],
    sources: Mapping[str, Any],
    tracks: Iterable[Mapping[str, Any]],
    transitions: Iterable[Mapping[str, Any]] = (),
    automation: Iterable[Mapping[str, Any]] = (),
    master_actions: Iterable[Mapping[str, Any]] = (),
    decisions: Iterable[Mapping[str, Any]] = (),
    locks: Iterable[Mapping[str, Any]] = (),
    static_gate_receipt: Mapping[str, Any] | None = None,
    compiler_receipt: Mapping[str, Any] | None = None,
    tempo_map: Iterable[Mapping[str, Any]] | None = None,
    compile_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rev = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "project_id": str(project_id),
        "parent_revision_sha": parent_revision_sha,
        "created_at": now_utc(),
        "created_by": dict(created_by),
        "intent": dict(intent),
        "sources": deep_copy_json(dict(sources)),
        "tempo_map": deep_copy_json(list(tempo_map or [{"beat": 0.0, "bpm": 120.0, "meter": [4, 4]}])),
        "tracks": deep_copy_json(list(tracks)),
        "transitions": deep_copy_json(list(transitions)),
        "automation": deep_copy_json(list(automation)),
        "mastering": {"state": "unresolved" if not list(master_actions) else "finalized", "actions": deep_copy_json(list(master_actions))},
        "decisions": deep_copy_json(list(decisions)),
        "locks": deep_copy_json(list(locks)),
        "static_gate_receipt": deep_copy_json(dict(static_gate_receipt or {})),
        "compiler_receipt": deep_copy_json(dict(compiler_receipt or {})),
        "compile_request": deep_copy_json(dict(compile_request or {})),
    }
    return seal_revision(rev)


def new_project_index(project_id: str, name: str, revision_sha: str, profile_id: str = "") -> dict[str, Any]:
    now = now_utc()
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": str(project_id),
        "name": str(name),
        "created_at": now,
        "updated_at": now,
        "profile_id": str(profile_id),
        "active_revision_sha": str(revision_sha),
        "lineage": [str(revision_sha)],
        "cursor": 0,
        "branches": [],
        "last_render": None,
    }


def iter_clips(revision: Mapping[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for track in revision.get("tracks") or []:
        for clip in track.get("clips") or []:
            yield track, clip


def clip_index(revision: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    out: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for track, clip in iter_clips(revision):
        cid = str(clip.get("clip_id") or "")
        if cid in out:
            raise ValidationError(f"duplicate clip_id: {cid}")
        out[cid] = (track, clip)
    return out


def transition_index(revision: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for transition in revision.get("transitions") or []:
        tid = str(transition.get("transition_id") or "")
        if tid in out:
            raise ValidationError(f"duplicate transition_id: {tid}")
        out[tid] = transition
    return out


def make_clip_id(source_id: str, start_beat: float, role: str, ordinal: int = 0) -> str:
    return stable_id("clip", {"source_id": source_id, "start_beat": round(float(start_beat), 9), "role": role, "ordinal": ordinal})


def make_transition_id(boundary_beat: float, outgoing: Iterable[str], incoming: Iterable[str]) -> str:
    return stable_id(
        "transition",
        {
            "boundary_beat": round(float(boundary_beat), 9),
            "outgoing": sorted_unique(outgoing),
            "incoming": sorted_unique(incoming),
        },
    )


def validate_source(source_id: str, source: Mapping[str, Any]) -> None:
    require_keys(source, ["source_id", "kind", "label", "path", "byte_sha256", "pcm_sha256", "sample_rate", "duration_samples", "stems", "stem_identities", "capabilities"], f"source {source_id}")
    if str(source.get("source_id")) != source_id:
        raise ValidationError(f"source key {source_id} does not match embedded source_id {source.get('source_id')}")
    if not str(source.get("path") or ""):
        raise ValidationError(f"source {source_id} has no path")
    if int(source.get("sample_rate") or 0) <= 0:
        raise ValidationError(f"source {source_id} sample_rate must be positive")
    if int(source.get("duration_samples") or 0) <= 0:
        raise ValidationError(f"source {source_id} duration_samples must be positive")
    caps = source.get("capabilities") or {}
    require_keys(caps, ["seekable", "loopable", "head_context_samples", "tail_context_samples"], f"source {source_id}.capabilities")
    stems = source.get("stems") or {}
    if "mix" not in stems:
        raise ValidationError(f"source {source_id} must expose a mix stem")
    identities = source.get("stem_identities") or {}
    if set(stems) != set(identities):
        raise ValidationError(f"source {source_id} stem identities do not match exposed stems")
    for stem, stem_path in stems.items():
        ident = identities.get(stem) or {}
        require_keys(ident, ["path", "byte_sha256", "pcm_sha256", "sample_rate", "duration_samples"], f"source {source_id}.stem_identities.{stem}")
        if str(ident.get("path") or "") != str(stem_path):
            raise ValidationError(f"source {source_id} stem {stem} path does not match its identity receipt")
        if int(ident.get("sample_rate") or 0) != int(source.get("sample_rate") or 0):
            raise ValidationError(f"source {source_id} stem {stem} sample rate does not match the project rate")
        if int(ident.get("duration_samples") or 0) <= 0:
            raise ValidationError(f"source {source_id} stem {stem} has no decoded audio")


def validate_clip(clip: Mapping[str, Any], sources: Mapping[str, Any], track_id: str) -> None:
    require_keys(
        clip,
        [
            "clip_id",
            "source_id",
            "stem",
            "role",
            "ear_role",
            "timeline_start_beat",
            "timeline_duration_beats",
            "source_start_sample",
            "source_end_sample",
            "gain_db",
            "pan",
            "fades",
            "transform",
            "muted",
            "solo",
            "locked_fields",
        ],
        f"clip on track {track_id}",
    )
    source_id = str(clip.get("source_id") or "")
    if source_id not in sources:
        raise ValidationError(f"clip {clip.get('clip_id')} references unknown source {source_id}")
    role = str(clip.get("role") or "")
    if role not in CLIP_ROLES:
        raise ValidationError(f"clip {clip.get('clip_id')} has unsupported role {role}")
    start = finite_number(clip.get("timeline_start_beat"), f"clip {clip.get('clip_id')}.timeline_start_beat")
    duration = finite_number(clip.get("timeline_duration_beats"), f"clip {clip.get('clip_id')}.timeline_duration_beats")
    if start < 0 or duration <= 0:
        raise ValidationError(f"clip {clip.get('clip_id')} has invalid timeline range")
    source_start = int(clip.get("source_start_sample"))
    source_end = int(clip.get("source_end_sample"))
    source_duration = int(sources[source_id].get("duration_samples") or 0)
    if source_start < 0 or source_end <= source_start or source_end > source_duration:
        raise ValidationError(f"clip {clip.get('clip_id')} has invalid source range {source_start}:{source_end}/{source_duration}")
    finite_number(clip.get("gain_db"), f"clip {clip.get('clip_id')}.gain_db")
    pan = finite_number(clip.get("pan"), f"clip {clip.get('clip_id')}.pan")
    if pan < -1 or pan > 1:
        raise ValidationError(f"clip {clip.get('clip_id')}.pan must be in [-1,1]")
    transform = clip.get("transform") or {}
    require_keys(transform, ["rate", "pitch_semitones", "mode", "artifact_risk"], f"clip {clip.get('clip_id')}.transform")
    if finite_number(transform.get("rate"), "transform.rate") <= 0:
        raise ValidationError("transform.rate must be positive")
    fades = clip.get("fades") or {}
    require_keys(fades, ["in_beats", "out_beats", "curve"], f"clip {clip.get('clip_id')}.fades")
    if finite_number(fades.get("in_beats"), "fade in") < 0 or finite_number(fades.get("out_beats"), "fade out") < 0:
        raise ValidationError("clip fades must be nonnegative")


def validate_transition(transition: Mapping[str, Any], clips: Mapping[str, Any]) -> None:
    require_keys(
        transition,
        [
            "transition_id",
            "boundary_beat",
            "technique",
            "duration_beats",
            "curve",
            "outgoing_clip_ids",
            "incoming_clip_ids",
            "bass_policy",
            "render_contract",
        ],
        "transition",
    )
    technique = str(transition.get("technique") or "")
    if technique not in TRANSITION_TECHNIQUES:
        raise ValidationError(f"unsupported transition technique {technique}")
    if finite_number(transition.get("boundary_beat"), "transition.boundary_beat") < 0:
        raise ValidationError("transition boundary must be nonnegative")
    duration = finite_number(transition.get("duration_beats"), "transition.duration_beats")
    if duration < 0:
        raise ValidationError("transition duration must be nonnegative")
    outgoing = [str(x) for x in transition.get("outgoing_clip_ids") or []]
    incoming = [str(x) for x in transition.get("incoming_clip_ids") or []]
    missing = [cid for cid in outgoing + incoming if cid not in clips]
    if missing:
        raise ValidationError(f"transition references missing clips: {', '.join(missing)}")
    hard = technique in {"start", "hard_cut", "hard_cut_pickup", "hard_cut_to_air", "impact_drop"}
    if hard and duration != 0:
        raise ValidationError(f"{technique} must have zero overlap duration in the executable score")
    if not hard and duration <= 0:
        raise ValidationError(f"{technique} requires positive overlap duration")
    contract = transition.get("render_contract") or {}
    require_keys(contract, ["requires_outgoing_tail", "requires_stems", "fallback_forbidden"], "transition.render_contract")
    if contract.get("fallback_forbidden") is not True:
        raise ValidationError("every transition must forbid render-time fallback")



def validate_automation(automation: Mapping[str, Any], clips: Mapping[str, Any]) -> None:
    require_keys(automation, ["automation_id", "clip_id", "parameter", "mode", "points"], "automation")
    clip_id = str(automation.get("clip_id") or "")
    if clip_id not in clips:
        raise ValidationError(f"automation references missing clip: {clip_id}")
    if str(automation.get("parameter") or "") != "gain_db":
        raise ValidationError("v1 automation supports gain_db only")
    if str(automation.get("mode") or "") != "relative_db":
        raise ValidationError("v1 gain automation must use relative_db mode")
    points = automation.get("points") or []
    if not isinstance(points, list) or not points:
        raise ValidationError("automation must contain points")
    clip = clips[clip_id][1]
    duration = float(clip.get("timeline_duration_beats") or 0.0)
    last = -1.0
    for point in points:
        require_keys(point, ["beat_offset", "value_db"], "automation point")
        beat = finite_number(point.get("beat_offset"), "automation beat_offset")
        value = finite_number(point.get("value_db"), "automation value_db")
        if beat < 0 or beat < last or beat > duration + 1e-9:
            raise ValidationError("automation points must be ordered inside the clip")
        if value < -48.0 or value > 24.0:
            raise ValidationError("automation value_db must be in [-48,24]")
        last = beat

def validate_master_action(action: Mapping[str, Any]) -> None:
    require_keys(action, ["action_id", "type", "parameters", "decision"], "master action")
    typ = str(action.get("type") or "")
    if typ not in MASTER_ACTION_TYPES:
        raise ValidationError(f"unsupported master action {typ}")


def validate_revision(revision: Mapping[str, Any], require_sealed: bool = True) -> None:
    require_keys(
        revision,
        [
            "schema_version",
            "project_id",
            "parent_revision_sha",
            "created_by",
            "intent",
            "sources",
            "tempo_map",
            "tracks",
            "transitions",
            "automation",
            "mastering",
            "decisions",
            "locks",
            "static_gate_receipt",
            "compiler_receipt",
            "compile_request",
        ],
        "revision",
    )
    if int(revision.get("schema_version") or 0) != REVISION_SCHEMA_VERSION:
        raise ValidationError(f"unsupported revision schema {revision.get('schema_version')}")
    if not str(revision.get("project_id") or ""):
        raise ValidationError("revision.project_id is required")
    created_by = revision.get("created_by") or {}
    require_keys(created_by, ["actor", "reason"], "revision.created_by")
    intent = revision.get("intent") or {}
    require_keys(intent, ["taste_profile", "seed", "target_seconds", "mode", "compiled_policy", "compiled_policy_sha"], "revision.intent")
    profile = intent.get("taste_profile") or {}
    require_keys(profile, ["id", "version", "hash"], "revision.intent.taste_profile")
    if finite_number(intent.get("target_seconds"), "intent.target_seconds") <= 0:
        raise ValidationError("intent.target_seconds must be positive")
    policy = intent.get("compiled_policy") or {}
    if sha256_json(policy) != str(intent.get("compiled_policy_sha") or ""):
        raise ValidationError("compiled_policy_sha does not match the compiled policy")
    sources = revision.get("sources") or {}
    if not isinstance(sources, Mapping) or not sources:
        raise ValidationError("revision must contain at least one source")
    for source_id, source in sources.items():
        validate_source(str(source_id), source)
    tempo_map = revision.get("tempo_map") or []
    if not tempo_map:
        raise ValidationError("revision must contain a tempo map")
    last_beat = -1.0
    for row in tempo_map:
        require_keys(row, ["beat", "bpm", "meter"], "tempo map row")
        beat = finite_number(row.get("beat"), "tempo beat")
        bpm = finite_number(row.get("bpm"), "tempo bpm")
        if beat < 0 or beat < last_beat or bpm <= 0:
            raise ValidationError("tempo map must be ordered and positive")
        last_beat = beat
    tracks = revision.get("tracks") or []
    if not tracks:
        raise ValidationError("revision must contain tracks")
    track_ids: set[str] = set()
    clip_ids: set[str] = set()
    for track in tracks:
        require_keys(track, ["track_id", "role", "clips"], "track")
        tid = str(track.get("track_id") or "")
        if not tid or tid in track_ids:
            raise ValidationError(f"duplicate or empty track_id: {tid}")
        track_ids.add(tid)
        if str(track.get("role") or "") not in TRACK_ROLES:
            raise ValidationError(f"track {tid} has unsupported role {track.get('role')}")
        for clip in track.get("clips") or []:
            cid = str(clip.get("clip_id") or "")
            if not cid or cid in clip_ids:
                raise ValidationError(f"duplicate or empty clip_id: {cid}")
            clip_ids.add(cid)
            validate_clip(clip, sources, tid)
    if not clip_ids:
        raise ValidationError("revision must contain at least one clip")
    clips = clip_index(revision)
    transition_ids: set[str] = set()
    for transition in revision.get("transitions") or []:
        tid = str(transition.get("transition_id") or "")
        if not tid or tid in transition_ids:
            raise ValidationError(f"duplicate or empty transition_id: {tid}")
        transition_ids.add(tid)
        validate_transition(transition, clips)
    automation_ids: set[str] = set()
    automation_targets: set[tuple[str, str]] = set()
    for automation in revision.get("automation") or []:
        aid = str(automation.get("automation_id") or "")
        if not aid or aid in automation_ids:
            raise ValidationError(f"duplicate or empty automation_id: {aid}")
        automation_ids.add(aid)
        target = (str(automation.get("clip_id") or ""), str(automation.get("parameter") or ""))
        if target in automation_targets:
            raise ValidationError(f"duplicate automation target: {target}")
        automation_targets.add(target)
        validate_automation(automation, clips)
    mastering = revision.get("mastering") or {}
    require_keys(mastering, ["state", "actions"], "revision.mastering")
    if mastering.get("state") not in {"unresolved", "finalized"}:
        raise ValidationError("mastering.state must be unresolved or finalized")
    for action in mastering.get("actions") or []:
        validate_master_action(action)
    if mastering.get("state") == "finalized" and not mastering.get("actions"):
        raise ValidationError("finalized mastering must contain actions")
    locked_paths = [str(lock.get("path") or "") for lock in revision.get("locks") or []]
    if len(locked_paths) != len(set(locked_paths)):
        raise ValidationError("duplicate lock paths are not allowed")
    gate = revision.get("static_gate_receipt") or {}
    if gate and gate.get("passed") is False:
        raise ValidationError("a sealed active revision cannot fail its static gate")
    if require_sealed:
        expected = compute_revision_sha(revision)
        if str(revision.get("revision_sha") or "") != expected:
            raise ValidationError("revision_sha does not match revision contents")


def validate_render_program(program: Mapping[str, Any]) -> None:
    require_keys(program, ["schema_version", "revision_sha", "sample_rate", "total_samples", "events", "transitions", "master_actions", "source_identities", "program_sha"], "render program")
    if int(program.get("schema_version") or 0) != RENDER_PROGRAM_SCHEMA_VERSION:
        raise ValidationError("unsupported render program schema")
    if int(program.get("sample_rate") or 0) <= 0 or int(program.get("total_samples") or 0) <= 0:
        raise ValidationError("render program sample dimensions must be positive")
    event_ids: set[str] = set()
    for event in program.get("events") or []:
        require_keys(event, ["event_id", "clip_id", "source_id", "timeline_start_sample", "active_samples", "render_samples", "source_start_sample", "source_end_sample", "gain_db", "envelope", "transform"], "render event")
        eid = str(event.get("event_id") or "")
        if not eid or eid in event_ids:
            raise ValidationError(f"duplicate render event {eid}")
        event_ids.add(eid)
        if int(event.get("timeline_start_sample")) < 0 or int(event.get("active_samples")) <= 0 or int(event.get("render_samples")) < int(event.get("active_samples")):
            raise ValidationError(f"render event {eid} has invalid timeline dimensions")
    if not event_ids:
        raise ValidationError("render program contains no events")
    for transition in program.get("transitions") or []:
        require_keys(transition, ["transition_id", "technique", "boundary_sample", "duration_samples", "algorithm", "outgoing_event_ids", "incoming_event_ids", "executed_contract"], "render transition")
        missing = [eid for eid in list(transition.get("outgoing_event_ids") or []) + list(transition.get("incoming_event_ids") or []) if eid not in event_ids]
        if missing:
            raise ValidationError(f"render transition references missing events: {missing}")
    for action in program.get("master_actions") or []:
        validate_master_action(action)
    expected = sha256_json(render_program_payload(program))
    if str(program.get("program_sha") or "") != expected:
        raise ValidationError("program_sha does not match render program contents")


def summarize_revision(revision: Mapping[str, Any]) -> dict[str, Any]:
    clips = [clip for _, clip in iter_clips(revision)]
    sources = revision.get("sources") or {}
    roles = Counter(str(clip.get("role") or "") for clip in clips)
    end_beat = max((float(clip.get("timeline_start_beat") or 0.0) + float(clip.get("timeline_duration_beats") or 0.0) for clip in clips), default=0.0)
    return {
        "project_id": revision.get("project_id"),
        "revision_sha": revision.get("revision_sha"),
        "parent_revision_sha": revision.get("parent_revision_sha"),
        "profile": ((revision.get("intent") or {}).get("taste_profile") or {}).get("id"),
        "seed": (revision.get("intent") or {}).get("seed"),
        "source_count": len(sources),
        "clip_count": len(clips),
        "transition_count": len(revision.get("transitions") or []),
        "master_action_count": len(((revision.get("mastering") or {}).get("actions") or [])),
        "roles": dict(sorted(roles.items())),
        "end_beat": round(end_beat, 6),
        "created_by": revision.get("created_by"),
        "static_gate": revision.get("static_gate_receipt"),
    }


def canonical_diff(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    """Small deterministic JSON diff used by CLI inspect and tests."""
    if type(left) is not type(right):
        return [{"path": path, "left": left, "right": right}]
    if isinstance(left, Mapping):
        out: list[dict[str, Any]] = []
        keys = sorted(set(left) | set(right))
        for key in keys:
            p = f"{path}.{key}"
            if key not in left:
                out.append({"path": p, "left": None, "right": right[key]})
            elif key not in right:
                out.append({"path": p, "left": left[key], "right": None})
            else:
                out.extend(canonical_diff(left[key], right[key], p))
        return out
    if isinstance(left, list):
        out = []
        for i in range(max(len(left), len(right))):
            p = f"{path}[{i}]"
            if i >= len(left):
                out.append({"path": p, "left": None, "right": right[i]})
            elif i >= len(right):
                out.append({"path": p, "left": left[i], "right": None})
            else:
                out.extend(canonical_diff(left[i], right[i], p))
        return out
    return [] if canonical_json(left) == canonical_json(right) else [{"path": path, "left": left, "right": right}]
