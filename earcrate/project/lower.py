from __future__ import annotations

import bisect
import math
from typing import Any, Mapping

from .model import clip_index, iter_clips, seal_render_program, validate_revision
from .util import ValidationError, deep_copy_json, stable_id


class TempoMap:
    def __init__(self, rows: list[Mapping[str, Any]], sample_rate: int):
        if not rows:
            raise ValidationError("tempo map is empty")
        self.rows = sorted((float(row["beat"]), float(row["bpm"]), list(row.get("meter") or [4, 4])) for row in rows)
        self.sample_rate = int(sample_rate)
        self.beats = [row[0] for row in self.rows]
        self.prefix_samples = [0]
        for index in range(1, len(self.rows)):
            prev_beat, prev_bpm, _ = self.rows[index - 1]
            beat, _, _ = self.rows[index]
            samples = int(round((beat - prev_beat) * 60.0 / prev_bpm * self.sample_rate))
            self.prefix_samples.append(self.prefix_samples[-1] + samples)

    def beat_to_sample(self, beat: float) -> int:
        beat = float(beat)
        if beat < 0:
            raise ValidationError("negative beat position")
        index = max(0, bisect.bisect_right(self.beats, beat) - 1)
        row_beat, bpm, _ = self.rows[index]
        return int(self.prefix_samples[index] + round((beat - row_beat) * 60.0 / bpm * self.sample_rate))

    def duration_samples(self, start_beat: float, duration_beats: float) -> int:
        return self.beat_to_sample(start_beat + duration_beats) - self.beat_to_sample(start_beat)


ALGORITHM_BY_TECHNIQUE = {
    "start": "start",
    "hard_cut": "hard_cut",
    "hard_cut_pickup": "hard_cut",
    "hard_cut_to_air": "hard_cut_to_air",
    "impact_drop": "impact_drop",
    "double_drop": "impact_drop",
    "beatmatch_blend": "equal_power_overlap",
    "long_blend": "equal_power_overlap",
    "hook_blend_over_bed": "equal_power_overlap",
    "echo_out": "echo_out",
    "acapella_bridge": "low_stripped_overlap",
    "bass_swap": "bass_swap",
    "bed_ride": "bed_ride",
}


def _event_envelope(active_samples: int, fade_in_samples: int, fade_out_samples: int) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if fade_in_samples > 0:
        segments.append({"start_sample": 0, "end_sample": min(active_samples, fade_in_samples), "start_gain": 0.0, "end_gain": 1.0, "curve": "equal_power_in"})
    if fade_out_samples > 0:
        start = max(0, active_samples - fade_out_samples)
        segments.append({"start_sample": start, "end_sample": active_samples, "start_gain": 1.0, "end_gain": 0.0, "curve": "equal_power_out"})
    return segments



def _clip_automation_envelope(
    revision: Mapping[str, Any],
    clip: Mapping[str, Any],
    tempo: TempoMap,
    active_samples: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    segments: list[dict[str, Any]] = []
    ids: list[str] = []
    duration_beats = float(clip["timeline_duration_beats"])
    for automation in revision.get("automation") or []:
        if str(automation.get("clip_id") or "") != str(clip["clip_id"]):
            continue
        if str(automation.get("parameter") or "") != "gain_db":
            continue
        points = list(automation.get("points") or [])
        if not points:
            continue
        normalized = [{"beat_offset": 0.0, "value_db": 0.0}]
        for point in points:
            if float(point["beat_offset"]) == 0.0:
                normalized[0] = dict(point)
            else:
                normalized.append(dict(point))
        if float(normalized[-1]["beat_offset"]) < duration_beats:
            normalized.append({"beat_offset": duration_beats, "value_db": float(normalized[-1]["value_db"])})
        for left, right in zip(normalized, normalized[1:]):
            start_beat = float(left["beat_offset"])
            end_beat = float(right["beat_offset"])
            start_sample = tempo.duration_samples(float(clip["timeline_start_beat"]), start_beat)
            end_sample = tempo.duration_samples(float(clip["timeline_start_beat"]), end_beat)
            start_sample = max(0, min(active_samples, start_sample))
            end_sample = max(start_sample, min(active_samples, end_sample))
            if end_sample <= start_sample:
                continue
            segments.append({
                "start_sample": start_sample,
                "end_sample": end_sample,
                "start_gain": 10.0 ** (float(left["value_db"]) / 20.0),
                "end_gain": 10.0 ** (float(right["value_db"]) / 20.0),
                "curve": "linear",
                "automation_id": automation["automation_id"],
                "parameter": "gain_db",
            })
        ids.append(str(automation["automation_id"]))
    return segments, ids

def lower_revision(revision: Mapping[str, Any], *, sample_rate: int | None = None) -> dict[str, Any]:
    validate_revision(revision, require_sealed=True)
    sources = revision.get("sources") or {}
    source_rates = {int(source["sample_rate"]) for source in sources.values()}
    if sample_rate is None:
        sample_rate = min(source_rates) if source_rates else 44100
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValidationError("sample_rate must be positive")
    tempo = TempoMap(list(revision.get("tempo_map") or []), sample_rate)
    clips = clip_index(revision)
    solo_tracks = {str(track["track_id"]) for track in revision.get("tracks") or [] if any(bool(clip.get("solo")) for clip in track.get("clips") or [])}

    events: list[dict[str, Any]] = []
    event_by_clip: dict[str, dict[str, Any]] = {}
    for track, clip in iter_clips(revision):
        if clip.get("muted"):
            continue
        if solo_tracks and str(track["track_id"]) not in solo_tracks and not clip.get("solo"):
            continue
        source = sources[str(clip["source_id"])]
        stem = str(clip.get("stem") or "mix")
        if stem not in (source.get("stems") or {}):
            raise ValidationError(f"clip {clip['clip_id']} selects unavailable stem {stem}")
        start_sample = tempo.beat_to_sample(float(clip["timeline_start_beat"]))
        active_samples = tempo.duration_samples(float(clip["timeline_start_beat"]), float(clip["timeline_duration_beats"]))
        if active_samples <= 0:
            raise ValidationError(f"clip {clip['clip_id']} lowers to zero samples")
        fade_in_samples = tempo.duration_samples(float(clip["timeline_start_beat"]), min(float(clip["timeline_duration_beats"]), float((clip.get("fades") or {}).get("in_beats") or 0.0)))
        fade_out_beats = min(float(clip["timeline_duration_beats"]), float((clip.get("fades") or {}).get("out_beats") or 0.0))
        fade_out_samples = tempo.duration_samples(float(clip["timeline_start_beat"]), fade_out_beats)
        event_id = stable_id("event", {"clip": clip["clip_id"], "track": track["track_id"]})
        automation_envelope, automation_ids = _clip_automation_envelope(revision, clip, tempo, active_samples)
        event = {
            "event_id": event_id,
            "clip_id": clip["clip_id"],
            "track_id": track["track_id"],
            "track_role": track["role"],
            "source_id": clip["source_id"],
            "stem": stem,
            "role": clip["role"],
            "ear_role": clip["ear_role"],
            "timeline_start_sample": start_sample,
            "active_samples": active_samples,
            "render_samples": active_samples,
            "post_roll_samples": 0,
            "source_start_sample": int(clip["source_start_sample"]),
            "source_end_sample": int(clip["source_end_sample"]),
            "loop": deep_copy_json(clip.get("loop") or {"enabled": False, "crossfade_samples": 512}),
            "gain_db": float(clip["gain_db"]),
            "pan": float(clip["pan"]),
            "envelope": _event_envelope(active_samples, fade_in_samples, fade_out_samples) + automation_envelope,
            "automation_ids": automation_ids,
            "transform": deep_copy_json(clip["transform"]),
            "source_context": deep_copy_json(clip.get("source_context") or {}),
            "decision_id": clip.get("decision_id"),
            "locked_fields": list(clip.get("locked_fields") or []),
        }
        events.append(event)
        event_by_clip[str(clip["clip_id"])] = event

    selected_clip_ids = {str(clip["clip_id"]) for _, clip in iter_clips(revision) if not clip.get("muted")}
    lowered_clip_ids = set(event_by_clip)
    omitted = sorted(selected_clip_ids - lowered_clip_ids)
    if omitted:
        raise ValidationError(f"selected clips did not lower: {omitted}")

    transition_programs: list[dict[str, Any]] = []
    for transition in revision.get("transitions") or []:
        technique = str(transition["technique"])
        boundary_sample = tempo.beat_to_sample(float(transition["boundary_beat"]))
        duration_samples = tempo.duration_samples(float(transition["boundary_beat"]), float(transition["duration_beats"])) if float(transition["duration_beats"]) > 0 else 0
        outgoing_events = [event_by_clip[cid] for cid in transition.get("outgoing_clip_ids") or [] if cid in event_by_clip]
        incoming_events = [event_by_clip[cid] for cid in transition.get("incoming_clip_ids") or [] if cid in event_by_clip]
        contract = transition.get("render_contract") or {}
        if bool(contract.get("requires_outgoing_tail")) != (duration_samples > 0):
            raise ValidationError(f"transition {transition['transition_id']} contract/duration disagree")
        required_tail = int(contract.get("required_tail_samples") or duration_samples)
        if duration_samples > 0 and required_tail != duration_samples:
            # Source and timeline sample rates are the same in v1; exact equality is the contract.
            raise ValidationError(f"transition {transition['transition_id']} required tail does not match lowered duration")
        if duration_samples > 0 and (not outgoing_events or not incoming_events):
            raise ValidationError(f"transition {transition['transition_id']} cannot overlap without both sides")
        for event in outgoing_events:
            source = sources[event["source_id"]]
            available = int(source["duration_samples"]) - int(event["source_end_sample"])
            looping = bool((event.get("loop") or {}).get("enabled"))
            if available < duration_samples and not looping:
                raise ValidationError(
                    f"transition {transition['transition_id']} requires {duration_samples} tail samples from {event['clip_id']}, only {available} are available"
                )
            event["post_roll_samples"] = max(int(event["post_roll_samples"]), duration_samples)
            event["render_samples"] = int(event["active_samples"]) + int(event["post_roll_samples"])
            if duration_samples > 0:
                event["envelope"].append({
                    "start_sample": int(event["active_samples"]),
                    "end_sample": int(event["active_samples"] + duration_samples),
                    "start_gain": 1.0,
                    "end_gain": 0.0,
                    "curve": "equal_power_out",
                    "transition_id": transition["transition_id"],
                })
        for event in incoming_events:
            if duration_samples > 0:
                event["envelope"].append({
                    "start_sample": 0,
                    "end_sample": min(duration_samples, int(event["active_samples"])),
                    "start_gain": 0.0,
                    "end_gain": 1.0,
                    "curve": "equal_power_in",
                    "transition_id": transition["transition_id"],
                })
        algorithm = ALGORITHM_BY_TECHNIQUE.get(technique)
        if not algorithm:
            raise ValidationError(f"no render algorithm for transition technique {technique}")
        transition_programs.append({
            "transition_id": transition["transition_id"],
            "technique": technique,
            "boundary_sample": boundary_sample,
            "duration_samples": duration_samples,
            "curve": transition.get("curve"),
            "algorithm": algorithm,
            "bass_policy": transition.get("bass_policy"),
            "outgoing_event_ids": [event["event_id"] for event in outgoing_events],
            "incoming_event_ids": [event["event_id"] for event in incoming_events],
            "parameters": {"required_stems": list(contract.get("requires_stems") or []), "automation": transition.get("automation") or []},
            "executed_contract": {
                "fallback_forbidden": True,
                "source_context_validated": True,
                "algorithm_resolved": True,
                "zero_overlap_is_execution": duration_samples == 0,
            },
            "decision_id": transition.get("decision_id"),
        })

    total_samples = max((int(event["timeline_start_sample"]) + int(event["render_samples"]) for event in events), default=0)
    if total_samples <= 0:
        raise ValidationError("render program would be empty")
    source_identities = {
        source_id: {
            "path": source["path"],
            "byte_sha256": source["byte_sha256"],
            "pcm_sha256": source["pcm_sha256"],
            "duration_samples": source["duration_samples"],
            "stems": deep_copy_json(source["stem_identities"]),
        }
        for source_id, source in sources.items()
    }
    program = {
        "schema_version": 1,
        "project_id": revision["project_id"],
        "revision_sha": revision["revision_sha"],
        "sample_rate": sample_rate,
        "total_samples": total_samples,
        "tempo_map": deep_copy_json(revision["tempo_map"]),
        "events": sorted(events, key=lambda event: (int(event["timeline_start_sample"]), str(event["event_id"]))),
        "transitions": sorted(transition_programs, key=lambda row: (int(row["boundary_sample"]), str(row["transition_id"]))),
        "master_actions": deep_copy_json(((revision.get("mastering") or {}).get("actions") or [])),
        "source_identities": source_identities,
        "compiler_receipt": deep_copy_json(revision.get("compiler_receipt") or {}),
        "static_gate_receipt": deep_copy_json(revision.get("static_gate_receipt") or {}),
    }
    return seal_render_program(program)


def renderability_receipt(revision: Mapping[str, Any], *, sample_rate: int | None = None) -> dict[str, Any]:
    try:
        program = lower_revision(revision, sample_rate=sample_rate)
        return {
            "passed": True,
            "revision_sha": revision.get("revision_sha"),
            "program_sha": program["program_sha"],
            "event_count": len(program["events"]),
            "transition_count": len(program["transitions"]),
            "total_samples": program["total_samples"],
            "failures": [],
        }
    except Exception as exc:
        return {
            "passed": False,
            "revision_sha": revision.get("revision_sha"),
            "program_sha": None,
            "event_count": 0,
            "transition_count": 0,
            "total_samples": 0,
            "failures": [str(exc)],
        }
