from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import buffalo
from .compiler_source_common import _candidate_score, _jsonable, _regions_from_entry, _role_from_hint, _source_asset
from .util import ValidationError, deep_copy_json, stable_id

def _candidate_from_region(
    asset: Mapping[str, Any],
    entry: Mapping[str, Any],
    region: Mapping[str, Any],
    *,
    sample_rate: int,
    ordinal: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
    start_s = max(0.0, float(raw_start or 0.0))
    end_s = min(float(asset["duration_s"]), float(raw_end if raw_end is not None else asset["duration_s"]))
    if end_s - start_s < 0.25:
        raise ValidationError(f"candidate region too short in {asset['label']}: {start_s:.3f}-{end_s:.3f}")
    clip_audio, decode_receipt = buffalo.decode_audio(asset["path"], sample_rate, start=start_s, duration=end_s - start_s)
    metrics = buffalo.spectral_metrics(clip_audio, sample_rate)
    analysis = dict(asset.get("analysis") or {})
    rail, role, ear_role = _role_from_hint(
        str(region.get("role_hint") or entry.get("role_hint") or ""),
        str(region.get("ear_role") or entry.get("ear_role") or ""),
        {**metrics, **analysis},
        end_s - start_s,
    )
    explicit_score = region.get("score") if region.get("score") is not None else entry.get("score")
    score = _candidate_score(rail, metrics, analysis, float(explicit_score) if explicit_score is not None else None)
    source_start = int(round(start_s * sample_rate))
    source_end = int(round(end_s * sample_rate))
    candidate_id = stable_id(
        "candidate",
        {
            "source_id": asset["source_id"],
            "start": source_start,
            "end": source_end,
            "rail": rail,
            "role": role,
            "ear_role": ear_role,
        },
    )
    return {
        "candidate_id": candidate_id,
        "source_id": asset["source_id"],
        "source_start_sample": source_start,
        "source_end_sample": source_end,
        "duration_samples": source_end - source_start,
        "duration_s": end_s - start_s,
        "rail": rail,
        "role": role,
        "ear_role": ear_role,
        "score": score,
        "metrics": metrics,
        "analysis": analysis,
        "region": _jsonable(dict(region)),
        "stem": str(region.get("stem") or entry.get("stem") or "mix"),
        "locked": bool(region.get("locked", entry.get("locked", False))),
        "ordinal": ordinal,
    }, [decode_receipt]


def load_source_manifest(path_or_data: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_data, Mapping):
        data = deep_copy_json(dict(path_or_data))
    else:
        path = Path(path_or_data).expanduser().resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("manifest_path", str(path))
    sources = data.get("sources") or []
    if not isinstance(sources, list) or not sources:
        raise ValidationError("source manifest must contain a non-empty sources array")
    for index, source in enumerate(sources):
        if not str(source.get("path") or ""):
            raise ValidationError(f"source manifest entry {index} has no path")
    return data


def prepare_manifest_sources(
    manifest: Mapping[str, Any],
    *,
    sample_rate: int = 44100,
    analysis_seconds: float = 180.0,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    sources: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for source_ordinal, entry in enumerate(manifest.get("sources") or []):
        asset, proposed_regions, source_receipts = _source_asset(entry, sample_rate=sample_rate, analysis_seconds=analysis_seconds)
        if asset["source_id"] in sources:
            raise ValidationError(f"duplicate source_id {asset['source_id']}")
        sources[asset["source_id"]] = asset
        receipts.extend(source_receipts)
        regions = _regions_from_entry(entry, proposed_regions, float(asset["duration_s"]))
        for region_ordinal, region in enumerate(regions):
            try:
                candidate, candidate_receipts = _candidate_from_region(
                    asset,
                    entry,
                    region,
                    sample_rate=sample_rate,
                    ordinal=source_ordinal * 10000 + region_ordinal,
                )
            except ValidationError:
                continue
            candidates.append(candidate)
            receipts.extend(candidate_receipts)
    if not candidates:
        raise ValidationError("source manifest produced no playable candidates")
    return sources, candidates, {
        "source_count": len(sources),
        "candidate_count": len(candidates),
        "sample_rate": sample_rate,
        "buffalo": buffalo.capabilities(),
        "component_receipts": receipts,
    }

