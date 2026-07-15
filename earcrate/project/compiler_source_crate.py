from __future__ import annotations

from pathlib import Path
from typing import Any

from . import buffalo
from .compiler_source_common import EAR_TO_RENDER
from .model import CLIP_ROLES
from .util import ValidationError, clamp, stable_id

def prepare_crate_sources(profile_id: str, *, sample_rate: int = 44100) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    pool, crate_receipt = buffalo.load_crate_sources(profile_id)
    if not pool:
        raise ValidationError(f"approved EarCrate pool is empty for {profile_id}")
    sources: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    identity_receipts: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    for item in pool:
        path = str(Path(str(item.get("path") or "")).expanduser().resolve())
        if not path:
            continue
        if path not in by_path:
            identity, receipts = buffalo.source_identity(path, sample_rate)
            source_id = stable_id("source", {"pcm": identity["pcm_sha256"], "kind": "library"})
            analysis = {
                "id": str(item.get("file_id") or source_id),
                "bpm": float(item.get("bpm") or 120.0),
                "bpm_confidence": float(item.get("bpm_confidence") or 0.5),
                "key_root": int(item.get("key_root") or 0),
                "key_mode": int(item.get("key_mode") or 1),
                "key_confidence": float(item.get("key_confidence") or 0.5),
                "energy": float(item.get("energy") or 0.0),
                "vocal_likelihood": float(item.get("vocal_likelihood") or 0.0),
                "beats": [],
                "downbeats": [],
                "sections": [],
                "beat_state": {},
                "duration_s": identity["duration_s"],
            }
            asset = {
                "source_id": source_id,
                "kind": "library",
                "label": str(item.get("title") or Path(path).stem),
                "path": path,
                "byte_sha256": identity["byte_sha256"],
                "pcm_sha256": identity["pcm_sha256"],
                "sample_rate": sample_rate,
                "duration_samples": identity["duration_samples"],
                "duration_s": identity["duration_s"],
                "stat": identity["stat"],
                "stems": {"mix": path},
                "stem_identities": {"mix": {**identity, "path": path}},
                "capabilities": {"seekable": True, "loopable": True, "head_context_samples": identity["duration_samples"], "tail_context_samples": identity["duration_samples"], "stems": ["mix"]},
                "analysis": analysis,
                "metadata": {"artist": item.get("artist"), "album": item.get("album"), "title": item.get("title"), "file_id": item.get("file_id")},
                "locked": False,
            }
            by_path[path] = asset
            sources[source_id] = asset
            identity_receipts.extend(receipts)
        asset = by_path[path]
        start_s = float(item.get("start_s") or 0.0)
        end_s = float(item.get("end_s") or min(float(asset["duration_s"]), start_s + 8.0))
        if end_s <= start_s:
            continue
        ear_role = str(item.get("ear_role") or "TEXTURE")
        rail, role = EAR_TO_RENDER.get(ear_role, ("spark", str(item.get("role") or item.get("render_role") or "texture")))
        metrics = {
            "low_share": float(item.get("low_share") or item.get("dry_low200_share") or 0.0),
            "mid_share": float(item.get("mid_share") or 0.0),
            "high_share": float(item.get("high_share") or item.get("dry_high3000_share") or 0.0),
            "rms": float(item.get("rms") or 0.08),
            "transient_density": float(item.get("transient_density") or 0.0),
            "loopability": float(item.get("loopability") or 0.5),
            "intelligibility": float(item.get("intelligibility") or 0.0),
        }
        candidates.append({
            "candidate_id": str(item.get("atom_id") or item.get("id") or stable_id("candidate", {"source": asset["source_id"], "start": start_s, "end": end_s, "role": ear_role})),
            "source_id": asset["source_id"],
            "source_start_sample": int(round(start_s * sample_rate)),
            "source_end_sample": min(int(asset["duration_samples"]), int(round(end_s * sample_rate))),
            "duration_samples": max(1, int(round((end_s - start_s) * sample_rate))),
            "duration_s": end_s - start_s,
            "rail": rail,
            "role": role if role in CLIP_ROLES else "texture",
            "ear_role": ear_role,
            "score": clamp(float(item.get("score") or item.get("atom_score") or 0.0), 0.0, 1.0),
            "metrics": metrics,
            "analysis": asset["analysis"],
            "region": {"start_s": start_s, "end_s": end_s, "bars": int(item.get("bars") or 0), "kind": "ear_atom"},
            "stem": str(item.get("stem") or "mix"),
            "locked": False,
            "ordinal": len(candidates),
        })
    return sources, candidates, {
        "source_count": len(sources),
        "candidate_count": len(candidates),
        "sample_rate": sample_rate,
        "crate_receipt": crate_receipt,
        "component_receipts": identity_receipts,
        "buffalo": buffalo.capabilities(),
    }

