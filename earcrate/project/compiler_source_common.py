from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from . import buffalo
from .model import CLIP_ROLES
from .util import ValidationError, clamp, deep_copy_json, sha256_json, stable_id


EAR_TO_RENDER = {
    "VOX_HOOK": ("foreground", "vocal"),
    "VOX_VERSE": ("foreground", "vocal"),
    "VOX_SHOUT": ("spark", "vocal"),
    "RIFF_ID": ("foreground", "harmony"),
    "BED_CHORD": ("floor", "harmony"),
    "DRUM_BREAK": ("floor", "drum_anchor"),
    "BASS_RIFF": ("floor", "bass"),
    "TEXTURE": ("spark", "texture"),
    "PICKUP_FILL": ("spark", "fx"),
    "DROP_HIT": ("spark", "fx"),
    "TRANSITION_TAIL": ("spark", "fx"),
}

HARD_TECHNIQUES = {"start", "hard_cut", "hard_cut_pickup", "hard_cut_to_air", "impact_drop", "double_drop"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _role_from_hint(hint: str, ear_role: str, metrics: Mapping[str, Any], duration_s: float) -> tuple[str, str, str]:
    if ear_role in EAR_TO_RENDER:
        rail, render_role = EAR_TO_RENDER[ear_role]
        return rail, render_role, ear_role
    raw = str(hint or "").strip().lower()
    hint_map = {
        "floor": ("floor", "harmony", "BED_CHORD"),
        "bed": ("floor", "harmony", "BED_CHORD"),
        "harmony": ("floor", "harmony", "BED_CHORD"),
        "drum": ("floor", "drum_anchor", "DRUM_BREAK"),
        "drums": ("floor", "drum_anchor", "DRUM_BREAK"),
        "drum_anchor": ("floor", "drum_anchor", "DRUM_BREAK"),
        "bass": ("floor", "bass", "BASS_RIFF"),
        "foreground": ("foreground", "vocal", "VOX_HOOK"),
        "vocal": ("foreground", "vocal", "VOX_HOOK"),
        "voice": ("foreground", "vocal", "VOX_HOOK"),
        "riff": ("foreground", "harmony", "RIFF_ID"),
        "spark": ("spark", "fx", "PICKUP_FILL"),
        "texture": ("spark", "texture", "TEXTURE"),
        "fx": ("spark", "fx", "PICKUP_FILL"),
    }
    if raw in hint_map:
        return hint_map[raw]
    vocal = float(metrics.get("vocal_likelihood") or metrics.get("intelligibility") or 0.0)
    low = float(metrics.get("low_share") or 0.0)
    transient = float(metrics.get("transient_density") or 0.0)
    high = float(metrics.get("high_share") or 0.0)
    if vocal >= 0.58:
        return "foreground", "vocal", "VOX_HOOK" if duration_s <= 18 else "VOX_VERSE"
    if low >= 0.38:
        return "floor", "bass", "BASS_RIFF"
    if transient >= 0.52:
        return "floor", "drum_anchor", "DRUM_BREAK"
    if high >= 0.24 and duration_s <= 8.0:
        return "spark", "fx", "PICKUP_FILL"
    return "floor", "harmony", "BED_CHORD"


def _candidate_score(rail: str, metrics: Mapping[str, Any], analysis: Mapping[str, Any], explicit_score: float | None = None) -> float:
    if explicit_score is not None:
        return clamp(float(explicit_score), 0.0, 1.0)
    rms = float(metrics.get("rms") or 0.0)
    energy = clamp(rms / 0.12, 0.0, 1.0)
    loopability = float(metrics.get("loopability") or 0.0)
    transient = float(metrics.get("transient_density") or 0.0)
    intelligibility = float(metrics.get("intelligibility") or 0.0)
    high = float(metrics.get("high_share") or 0.0)
    low = float(metrics.get("low_share") or 0.0)
    vocal = float(analysis.get("vocal_likelihood") or 0.0)
    if rail == "foreground":
        score = 0.32 * intelligibility + 0.22 * vocal + 0.18 * energy + 0.16 * high + 0.12 * loopability
    elif rail == "spark":
        score = 0.30 * transient + 0.24 * high + 0.18 * energy + 0.16 * (1.0 - loopability) + 0.12 * intelligibility
    else:
        score = 0.25 * loopability + 0.22 * energy + 0.20 * transient + 0.18 * (1.0 - min(1.0, float(metrics.get("mid_share") or 0.0) / 0.75)) + 0.15 * min(1.0, low / 0.34)
    return clamp(score, 0.0, 1.0)


def _source_asset(
    entry: Mapping[str, Any],
    *,
    sample_rate: int,
    analysis_seconds: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    path = Path(str(entry.get("path") or "")).expanduser().resolve()
    if not path.exists():
        raise ValidationError(f"source does not exist: {path}")
    identity, identity_receipts = buffalo.source_identity(path, sample_rate)
    analysis, analysis_receipt = buffalo.analyze_audio(path, sample_rate, analysis_seconds=analysis_seconds)
    overrides = dict(entry.get("analysis_overrides") or {})
    for field in ("bpm", "bpm_confidence", "key_root", "key_mode", "key_confidence", "energy", "vocal_likelihood"):
        if entry.get(field) is not None:
            overrides[field] = entry[field]
    for field, value in overrides.items():
        if field in {"bpm", "bpm_confidence", "key_root", "key_mode", "key_confidence", "energy", "vocal_likelihood"}:
            analysis[field] = value
    analysis["id"] = stable_id("analysis", {"pcm": identity["pcm_sha256"], "version": "project-score-v1", "overrides": overrides})
    analysis["duration_s"] = identity["duration_s"]
    analysis_receipt = {**analysis_receipt, "authored_overrides": overrides}
    regions, regions_receipt = buffalo.regions_for_analysis(analysis)
    source_id = str(entry.get("source_id") or stable_id("source", {"pcm": identity["pcm_sha256"], "kind": entry.get("kind") or "project"}))
    stems = {"mix": str(path)}
    stem_identities: dict[str, dict[str, Any]] = {"mix": {**identity, "path": str(path)}}
    stem_receipts: list[dict[str, Any]] = []
    for stem, stem_path in (entry.get("stems") or {}).items():
        stem_name = str(stem)
        sp = Path(str(stem_path)).expanduser().resolve()
        if not sp.exists():
            raise ValidationError(f"stem {stem_name!r} not found for {path}: {sp}")
        stems[stem_name] = str(sp)
        if sp == path:
            stem_identities[stem_name] = {**identity, "path": str(sp)}
        else:
            stem_identity, receipts = buffalo.source_identity(sp, sample_rate)
            stem_identities[stem_name] = {**stem_identity, "path": str(sp)}
            stem_receipts.extend({**receipt, "stem": stem_name, "source_path": str(sp)} for receipt in receipts)
    capability = {
        "seekable": True,
        "loopable": bool(entry.get("loopable", True)),
        "head_context_samples": int(identity["duration_samples"]),
        "tail_context_samples": int(identity["duration_samples"]),
        "stems": sorted(stems),
    }
    asset = {
        "source_id": source_id,
        "kind": str(entry.get("kind") or "project_scoped"),
        "label": str(entry.get("label") or path.stem),
        "path": str(path),
        "byte_sha256": identity["byte_sha256"],
        "pcm_sha256": identity["pcm_sha256"],
        "sample_rate": sample_rate,
        "duration_samples": identity["duration_samples"],
        "duration_s": identity["duration_s"],
        "stat": identity["stat"],
        "stems": stems,
        "stem_identities": stem_identities,
        "capabilities": capability,
        "analysis": _jsonable(analysis),
        "metadata": _jsonable(entry.get("metadata") or {}),
        "locked": bool(entry.get("locked", False)),
    }
    return asset, _jsonable(regions), identity_receipts + stem_receipts + [analysis_receipt, regions_receipt]



def prepare_source_asset(
    entry: Mapping[str, Any],
    *,
    sample_rate: int = 44100,
    analysis_seconds: float = 180.0,
) -> dict[str, Any]:
    """Seal one project-scoped source and every declared stem.

    This is the command-path counterpart to source-manifest compilation. The returned
    asset is already suitable for the immutable source registry; no renderer-side
    discovery or identity repair is allowed.
    """
    asset, proposed_regions, receipts = _source_asset(
        entry, sample_rate=int(sample_rate), analysis_seconds=float(analysis_seconds)
    )
    asset = deep_copy_json(asset)
    asset["metadata"] = {
        **dict(asset.get("metadata") or {}),
        "source_import_receipts": receipts,
        "proposed_regions": proposed_regions,
    }
    return asset

def _regions_from_entry(entry: Mapping[str, Any], proposed: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    explicit = entry.get("regions") or []
    if explicit:
        out = []
        for index, region in enumerate(explicit):
            raw_start = region.get("start_s")
            if raw_start is None:
                raw_start = region.get("start_time_s")
            if raw_start is None:
                raw_start = region.get("start")
            raw_end = region.get("end_s")
            if raw_end is None:
                raw_end = region.get("end_time_s")
            if raw_end is None:
                raw_end = region.get("end")
            start = max(0.0, float(raw_start or 0.0))
            end = min(duration_s, float(raw_end if raw_end is not None else duration_s))
            if end <= start:
                raise ValidationError(f"explicit region {index} has invalid range")
            out.append({**dict(region), "region_id": str(region.get("region_id") or f"explicit:{index}"), "start_s": start, "end_s": end})
        return out
    if proposed:
        return proposed
    return [{"region_id": "whole", "start_s": 0.0, "end_s": duration_s, "bars": 0, "kind": "whole", "confidence": 1.0}]

