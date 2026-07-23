from __future__ import annotations

from typing import Any, Mapping

from .compiler import EAR_TO_RENDER, HARD_TECHNIQUES, prepare_source_asset, static_gate
from .lower import renderability_receipt
from .model import clip_index, compute_revision_sha, make_transition_id, seal_revision, transition_index
from .policy import assert_value_in_policy_range
from .store import ProjectStore
from .util import ProjectError, ValidationError, deep_copy_json, now_utc, stable_id


def _locked(revision: Mapping[str, Any], path: str) -> bool:
    if any(str(lock.get("path") or "") == path for lock in revision.get("locks") or []):
        return True
    parts = path.split(".")
    if len(parts) >= 3 and parts[0] == "clips":
        clip_id, field = parts[1], parts[2]
        pair = clip_index(revision).get(clip_id)
        if pair and field in list(pair[1].get("locked_fields") or []):
            return True
    return False


def _require_unlocked(revision: Mapping[str, Any], path: str) -> None:
    if _locked(revision, path):
        raise ProjectError(f"field is locked: {path}")


def _track_for_clip(revision: Mapping[str, Any], clip_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    pair = clip_index(revision).get(clip_id)
    if pair is None:
        raise ValidationError(f"clip not found: {clip_id}")
    return pair


def _transition_for_id(revision: Mapping[str, Any], transition_id: str) -> dict[str, Any]:
    transition = transition_index(revision).get(transition_id)
    if transition is None:
        raise ValidationError(f"transition not found: {transition_id}")
    return transition


def _rebuild_gate(revision: dict[str, Any]) -> dict[str, Any]:
    bpm = float(revision["tempo_map"][0]["bpm"])
    end_beat = max(
        (
            float(clip["timeline_start_beat"]) + float(clip["timeline_duration_beats"])
            for track in revision["tracks"]
            for clip in track["clips"]
        ),
        default=0.0,
    )
    total_bars = max(1, int(round(end_beat / 4.0)))
    gate = static_gate(
        tracks=revision["tracks"],
        transitions=revision["transitions"],
        sources=revision["sources"],
        policy=revision["intent"]["compiled_policy"],
        bpm=bpm,
        total_bars=total_bars,
    )
    if not gate["passed"]:
        raise ValidationError(f"command would violate the active TasteSpec: {gate['failures']}")
    revision["static_gate_receipt"] = gate
    return gate


def _author_revision(base: Mapping[str, Any], operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    revision = deep_copy_json(dict(base))
    revision.pop("revision_sha", None)
    revision["parent_revision_sha"] = base["revision_sha"]
    revision["created_at"] = now_utc()
    revision["created_by"] = {"actor": "user", "reason": operation, "command": deep_copy_json(dict(payload))}
    revision.setdefault("decisions", []).append({
        "decision_id": stable_id("decision", {"base": base["revision_sha"], "operation": operation, "payload": payload}),
        "kind": "human_override",
        "operation": operation,
        "payload": deep_copy_json(dict(payload)),
        "base_revision_sha": base["revision_sha"],
        "human_precedence": True,
    })
    # Any score edit invalidates a previously-resolved master plan. The next render
    # will derive a visible machine-authored child revision from the new premaster.
    if operation not in {"lock", "unlock"}:
        revision["mastering"] = {"state": "unresolved", "actions": []}
    return revision


def apply_command(
    store: ProjectStore,
    project_id: str,
    operation: str,
    payload: Mapping[str, Any],
    *,
    expected_head: str | None = None,
) -> dict[str, Any]:
    base = store.load_revision(project_id)
    if expected_head is not None and expected_head != base["revision_sha"]:
        raise ProjectError(f"stale command head: expected {expected_head}, found {base['revision_sha']}")
    revision = _author_revision(base, operation, payload)
    policy = revision["intent"]["compiled_policy"]

    if operation == "set_gain":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.gain_db")
        value = float(payload["gain_db"])
        rail = "bass" if clip["role"] == "bass" else EAR_TO_RENDER.get(clip["ear_role"], ("aux", ""))[0]
        assert_value_in_policy_range(policy, rail, value, f"clip {clip_id}.gain_db")
        clip["gain_db"] = value
    elif operation == "set_pan":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.pan")
        value = float(payload["pan"])
        if value < -1.0 or value > 1.0:
            raise ValidationError("pan must be in [-1, 1]")
        clip["pan"] = value
    elif operation == "set_fades":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.fades")
        fade_in = float(payload.get("in_beats", (clip.get("fades") or {}).get("in_beats") or 0.0))
        fade_out = float(payload.get("out_beats", (clip.get("fades") or {}).get("out_beats") or 0.0))
        if fade_in < 0 or fade_out < 0 or fade_in + fade_out > float(clip["timeline_duration_beats"]) + 1e-9:
            raise ValidationError("fade lengths must be nonnegative and fit inside the clip")
        clip["fades"] = {"in_beats": fade_in, "out_beats": fade_out, "curve": str(payload.get("curve") or "equal_power")}
    elif operation == "trim":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.source_range")
        start = int(payload.get("source_start_sample", clip["source_start_sample"]))
        end = int(payload.get("source_end_sample", clip["source_end_sample"]))
        source = revision["sources"][clip["source_id"]]
        if start < 0 or end <= start or end > int(source["duration_samples"]):
            raise ValidationError("trim source range is invalid")
        clip["source_start_sample"] = start
        clip["source_end_sample"] = end
        clip["source_context"] = {"available_head_samples": start, "available_tail_samples": int(source["duration_samples"]) - end}
    elif operation == "move":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.timeline")
        start = float(payload["timeline_start_beat"])
        if start < 0:
            raise ValidationError("timeline_start_beat must be nonnegative")
        clip["timeline_start_beat"] = start
    elif operation in {"mute", "unmute", "solo", "unsolo"}:
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        field = "muted" if operation in {"mute", "unmute"} else "solo"
        _require_unlocked(revision, f"clips.{clip_id}.{field}")
        clip[field] = operation in {"mute", "solo"}
    elif operation == "set_loop":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.loop")
        enabled = bool(payload.get("enabled"))
        crossfade = int(payload.get("crossfade_samples", (clip.get("loop") or {}).get("crossfade_samples") or 512))
        if crossfade < 0:
            raise ValidationError("crossfade_samples must be nonnegative")
        clip["loop"] = {"enabled": enabled, "crossfade_samples": crossfade}
    elif operation == "set_stem":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.stem")
        stem = str(payload["stem"])
        if stem not in (revision["sources"][clip["source_id"]].get("stems") or {}):
            raise ValidationError(f"source does not expose stem {stem}")
        clip["stem"] = stem
    elif operation == "replace_source":
        clip_id = str(payload["clip_id"])
        _, clip = _track_for_clip(revision, clip_id)
        _require_unlocked(revision, f"clips.{clip_id}.source_id")
        source_id = str(payload["source_id"])
        if source_id not in revision["sources"]:
            raise ValidationError(f"source not found: {source_id}")
        source = revision["sources"][source_id]
        start = int(payload.get("source_start_sample", 0))
        end = int(payload.get("source_end_sample", min(int(source["duration_samples"]), start + (clip["source_end_sample"] - clip["source_start_sample"]))))
        if start < 0 or end <= start or end > int(source["duration_samples"]):
            raise ValidationError("replacement source range is invalid")
        clip["source_id"] = source_id
        clip["source_start_sample"] = start
        clip["source_end_sample"] = end
        clip["source_context"] = {"available_head_samples": start, "available_tail_samples": int(source["duration_samples"]) - end}
        if clip["stem"] not in (source.get("stems") or {}):
            clip["stem"] = "mix"
    elif operation == "add_source":
        entry = payload.get("source") or payload
        if not isinstance(entry, Mapping):
            raise ValidationError("add_source requires a source object")
        sr = int(next(iter(revision["sources"].values()))["sample_rate"])
        analysis_seconds = float((revision.get("compile_request") or {}).get("analysis_seconds") or 180.0)
        source = prepare_source_asset(entry, sample_rate=sr, analysis_seconds=analysis_seconds)
        source_id = str(source["source_id"])
        if source_id in revision["sources"]:
            if revision["sources"][source_id] != source:
                raise ValidationError(f"source identity collision: {source_id}")
            raise ProjectError(f"source is already in the project: {source_id}")
        revision["sources"][source_id] = source
    elif operation == "remove_source":
        source_id = str(payload["source_id"])
        if source_id not in revision["sources"]:
            raise ValidationError(f"source not found: {source_id}")
        used = [clip["clip_id"] for track in revision["tracks"] for clip in track["clips"] if str(clip["source_id"]) == source_id]
        if used:
            raise ValidationError(f"source is still used by clips: {used}")
        _require_unlocked(revision, f"sources.{source_id}")
        del revision["sources"][source_id]
    elif operation == "set_automation":
        clip_id = str(payload["clip_id"])
        _track_for_clip(revision, clip_id)
        parameter = str(payload.get("parameter") or "gain_db")
        if parameter != "gain_db":
            raise ValidationError("v1 automation supports gain_db only")
        _require_unlocked(revision, f"clips.{clip_id}.automation.{parameter}")
        points = payload.get("points") or []
        if not isinstance(points, list) or not points:
            raise ValidationError("set_automation requires a nonempty points array")
        normalized = []
        last = -1.0
        clip = _track_for_clip(revision, clip_id)[1]
        for point in points:
            beat = float(point.get("beat_offset"))
            value = float(point.get("value_db"))
            if beat < 0 or beat < last or beat > float(clip["timeline_duration_beats"]) + 1e-9:
                raise ValidationError("automation points must be ordered inside the clip")
            if value < -48.0 or value > 24.0:
                raise ValidationError("automation gain must be in [-48, 24] dB")
            normalized.append({"beat_offset": beat, "value_db": value})
            last = beat
        automation_id = str(payload.get("automation_id") or stable_id("automation", {"clip_id": clip_id, "parameter": parameter}))
        revision["automation"] = [row for row in revision.get("automation") or [] if not (str(row.get("clip_id")) == clip_id and str(row.get("parameter")) == parameter)]
        revision["automation"].append({
            "automation_id": automation_id,
            "clip_id": clip_id,
            "parameter": parameter,
            "mode": "relative_db",
            "points": normalized,
        })
    elif operation == "remove_automation":
        automation_id = str(payload.get("automation_id") or "")
        clip_id = str(payload.get("clip_id") or "")
        parameter = str(payload.get("parameter") or "gain_db")
        before = len(revision.get("automation") or [])
        revision["automation"] = [
            row for row in revision.get("automation") or []
            if not ((automation_id and str(row.get("automation_id")) == automation_id) or (clip_id and str(row.get("clip_id")) == clip_id and str(row.get("parameter")) == parameter))
        ]
        if len(revision["automation"]) == before:
            raise ValidationError("automation not found")
    elif operation == "set_transition":
        transition_id = str(payload["transition_id"])
        transition = _transition_for_id(revision, transition_id)
        _require_unlocked(revision, f"transitions.{transition_id}")
        technique = str(payload["technique"])
        allowed = set((policy.get("transition_policy") or {}).get("allowed") or []) | {"start"}
        if technique not in allowed:
            raise ValidationError(f"transition {technique} is not allowed by the active persona")
        transition["technique"] = technique
        if technique in HARD_TECHNIQUES:
            duration = 0.0
        else:
            duration = float(payload.get("duration_beats", ((policy.get("transition_policy") or {}).get("duration_beats") or {}).get(technique) or 4.0))
            if duration <= 0:
                raise ValidationError("overlap transition requires positive duration")
        transition["duration_beats"] = duration
        transition["curve"] = str(payload.get("curve") or ("equal_power" if duration > 0 else "none"))
        bpm = float(revision["tempo_map"][0]["bpm"])
        sr = int(next(iter(revision["sources"].values()))["sample_rate"])
        required = int(round(duration * 60.0 / bpm * sr))
        transition["render_contract"] = {
            "requires_outgoing_tail": duration > 0,
            "required_tail_samples": required,
            "requires_stems": ["bass", "no_bass"] if technique == "bass_swap" else [],
            "fallback_forbidden": True,
            "capability_validated": False,
        }
        for clip_id in transition.get("outgoing_clip_ids") or []:
            _, clip = _track_for_clip(revision, clip_id)
            source = revision["sources"][clip["source_id"]]
            available = int(source["duration_samples"]) - int(clip["source_end_sample"])
            if duration > 0 and available < required and not bool((clip.get("loop") or {}).get("enabled")):
                raise ValidationError(f"transition cannot render: {clip_id} has {available} tail samples, needs {required}")
        if technique == "bass_swap":
            for clip_id in list(transition.get("outgoing_clip_ids") or []) + list(transition.get("incoming_clip_ids") or []):
                _, clip = _track_for_clip(revision, clip_id)
                stems = set((revision["sources"][clip["source_id"]].get("stems") or {}).keys())
                if not {"bass", "no_bass"} <= stems:
                    raise ValidationError(f"bass_swap requires bass/no_bass stems for {clip_id}")
        transition["render_contract"]["capability_validated"] = True
    elif operation == "lock":
        path = str(payload["path"])
        if _locked(revision, path):
            raise ProjectError(f"path is already locked: {path}")
        revision.setdefault("locks", []).append({"path": path, "owner": "user", "reason": str(payload.get("reason") or "user lock")})
        if path.startswith("clips."):
            parts = path.split(".")
            if len(parts) >= 3:
                _, clip = _track_for_clip(revision, parts[1])
                if parts[2] not in clip["locked_fields"]:
                    clip["locked_fields"].append(parts[2])
    elif operation == "unlock":
        path = str(payload["path"])
        revision["locks"] = [lock for lock in revision.get("locks") or [] if str(lock.get("path") or "") != path]
        if path.startswith("clips."):
            parts = path.split(".")
            if len(parts) >= 3:
                _, clip = _track_for_clip(revision, parts[1])
                clip["locked_fields"] = [field for field in clip.get("locked_fields") or [] if field != parts[2]]
    else:
        raise ValidationError(f"unsupported project command: {operation}")

    gate = _rebuild_gate(revision)
    provisional = seal_revision(revision)
    renderability = renderability_receipt(provisional)
    if not renderability["passed"]:
        raise ValidationError(f"command creates an unrenderable score: {renderability['failures']}")
    revision["compiler_receipt"] = deep_copy_json(revision.get("compiler_receipt") or {})
    revision["compiler_receipt"]["last_human_command"] = {
        "operation": operation,
        "payload": deep_copy_json(dict(payload)),
        "static_gate": gate,
        "renderability": renderability,
    }
    sealed = seal_revision(revision)
    return store.commit_revision(
        project_id,
        sealed,
        expected_head=base["revision_sha"],
        event="project_command",
        event_payload={"operation": operation, "payload": dict(payload)},
    )
