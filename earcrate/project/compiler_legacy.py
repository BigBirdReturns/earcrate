from __future__ import annotations

from typing import Any, Mapping

from .compiler_gate import static_gate
from .model import default_tracks, make_clip_id, new_revision
from .policy import compile_policy
from .store import ProjectStore
from .util import ValidationError, random_id, sha256_json, stable_id


def import_legacy_arrangement(
    store: ProjectStore,
    *,
    name: str,
    arrangement: Mapping[str, Any],
    profile: str | Mapping[str, Any],
    sample_rate: int = 44_100,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Import the historical audio-clip arrangement without reinterpretation.

    This migration path intentionally performs no candidate search. Every legacy
    layer becomes a locked clip and every missing source identity is a refusal.
    """
    policy_bundle = compile_policy(profile)
    policy = policy_bundle["compiled_policy"]
    profile_row = policy["profile"]
    policy_receipt = policy_bundle["receipt"]
    raw_sources = arrangement.get("sources") or {}
    if not isinstance(raw_sources, Mapping) or not raw_sources:
        raise ValidationError("legacy arrangement must contain source identities")
    sources = {str(key): dict(value) for key, value in raw_sources.items()}
    tracks = default_tracks()
    track_by_role = {str(track["role"]): track for track in tracks}
    layers = arrangement.get("layers") or arrangement.get("clips") or []
    if not isinstance(layers, list) or not layers:
        raise ValidationError("legacy arrangement contains no layers")
    decisions = []
    bpm = float(arrangement.get("bpm") or 120.0)
    maximum_end = 0.0
    for ordinal, layer in enumerate(layers):
        source_id = str(layer.get("source_id") or "")
        if source_id not in sources:
            raise ValidationError(f"legacy layer references unknown source {source_id}")
        rail = str(layer.get("rail") or layer.get("track_role") or "aux")
        if rail not in track_by_role:
            rail = "aux"
        role = str(layer.get("role") or "full")
        start = float(layer.get("timeline_start_beat") if layer.get("timeline_start_beat") is not None else layer.get("start_beat") or 0.0)
        duration = float(layer.get("timeline_duration_beats") if layer.get("timeline_duration_beats") is not None else layer.get("duration_beats") or 0.0)
        if duration <= 0.0:
            raise ValidationError("legacy layer duration must be positive")
        source = sources[source_id]
        source_end = int(layer.get("source_end_sample") or source.get("duration_samples") or 0)
        clip_id = str(layer.get("clip_id") or make_clip_id(source_id, start, role, ordinal))
        clip = {
            "clip_id": clip_id,
            "source_id": source_id,
            "stem": str(layer.get("stem") or "mix"),
            "role": role,
            "ear_role": str(layer.get("ear_role") or "TEXTURE"),
            "timeline_start_beat": start,
            "timeline_duration_beats": duration,
            "source_start_sample": int(layer.get("source_start_sample") or 0),
            "source_end_sample": source_end,
            "loop": dict(layer.get("loop") or {"enabled": False, "crossfade_samples": 0}),
            "gain_db": float(layer.get("gain_db") or 0.0),
            "normalization_gain_db": float(layer.get("normalization_gain_db") or 0.0),
            "pan": float(layer.get("pan") or 0.0),
            "fades": dict(layer.get("fades") or {"in_beats": 0.0, "out_beats": 0.0, "curve": "equal_power"}),
            "transform": dict(layer.get("transform") or {"rate": 1.0, "pitch_semitones": 0.0, "mode": "identity", "artifact_risk": 0.0, "receipt": {}}),
            "muted": bool(layer.get("muted", False)),
            "solo": bool(layer.get("solo", False)),
            "locked_fields": ["source_id", "source_range", "timeline", "transform"],
            "decision_id": stable_id("decision", {"legacy": clip_id}),
            "source_context": dict(layer.get("source_context") or {"available_head_samples": int(layer.get("source_start_sample") or 0), "available_tail_samples": max(0, int(source.get("duration_samples") or 0) - source_end)}),
        }
        track_by_role[rail]["clips"].append(clip)
        decisions.append({
            "decision_id": clip["decision_id"],
            "kind": "legacy_import",
            "selected": clip_id,
            "human_lock": True,
            "legacy_payload_sha256": sha256_json(layer),
        })
        maximum_end = max(maximum_end, start + duration)
    total_bars = max(1, int(round(maximum_end / 4.0)))
    gate = static_gate(tracks=tracks, transitions=[], sources=sources, policy=policy, bpm=bpm, total_bars=total_bars)
    if not gate["passed"]:
        raise ValidationError("legacy arrangement fails the selected TasteSpec: " + ", ".join(gate["failures"]))
    pid = str(project_id or random_id("project"))
    revision = new_revision(
        project_id=pid,
        parent_revision_sha=None,
        created_by={"actor": "migration", "reason": "import_legacy_arrangement"},
        intent={
            "taste_profile": {"id": profile_row["id"], "version": profile_row["version"], "hash": profile_row["hash"]},
            "seed": int(arrangement.get("seed") or 0),
            "target_seconds": max(0.001, maximum_end * 60.0 / bpm),
            "mode": "legacy_import",
            "compiled_policy": policy,
            "compiled_policy_sha": sha256_json(policy),
        },
        sources=sources,
        tracks=tracks,
        decisions=decisions,
        locks=[{"path": "performance", "reason": "historical import", "actor": "migration"}],
        static_gate_receipt=gate,
        compiler_receipt={"schema": "earcrate/legacy-import-receipt@1", "arrangement_sha256": sha256_json(arrangement), "policy_receipt": policy_receipt},
        tempo_map=[{"beat": 0.0, "bpm": bpm, "meter": [4, 4]}],
        compile_request={"kind": "legacy_import", "arrangement_sha256": sha256_json(arrangement)},
    )
    created = store.create_project(name, revision, project_id=pid)
    return {"ok": True, **created, "static_gate": gate}
