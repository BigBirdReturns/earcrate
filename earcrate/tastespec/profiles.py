from __future__ import annotations
import base64
import json, hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

try:
    ROOT = Path(__file__).resolve().parents[2]
except Exception:  # single-file build: __file__ has no package depth
    ROOT = Path(".")
PROFILE_DIR = ROOT / "profiles"

# Single-file builds cannot ship a profiles/ directory next to dist/earcrate.py,
# so make_singlefile.py replaces this dict with {profile_id: b64(json)} — the same
# trick used for the embedded UI HTML. Package mode reads the real files.
EMBEDDED_PROFILES: Dict[str, str] = {}


@lru_cache(maxsize=16)
def load_tastespec(profile_id: str = "girl_talk_v1") -> Dict[str, Any]:
    path = PROFILE_DIR / f"{profile_id}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    elif profile_id in EMBEDDED_PROFILES:
        data = json.loads(base64.b64decode(EMBEDDED_PROFILES[profile_id]).decode("utf-8"))
    else:
        raise FileNotFoundError(f"TasteSpec profile not found: {profile_id} (looked in {PROFILE_DIR})")
    data["hash"] = tastespec_hash(data)
    return data


def tastespec_hash(profile: Dict[str, Any]) -> str:
    clean = {k: v for k, v in profile.items() if k != "hash"}
    payload = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def profile_summary(profile_id: str = "girl_talk_v1") -> Dict[str, Any]:
    p = load_tastespec(profile_id)
    return {"id": p["id"], "version": p["version"], "hash": p["hash"], "permitted_roles": p["permitted_roles"]}


def flat_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Project the structured TasteSpec JSON onto the flat legacy TASTE_PROFILES
    shape the engine consumes. The JSON is the single source of truth; this
    mapping is the only bridge — no number may be defined in both places."""
    cov = profile.get("coverage_obligations") or {}
    turn = profile.get("source_turnover") or {}
    dens = profile.get("density_model") or {}
    endl = profile.get("endless_contract") or {}
    return {
        "name": profile.get("name") or profile.get("id") or "unnamed",
        "contract": profile.get("contract") or "",
        "source_seconds": float(turn.get("source_seconds") or 11.5),
        "first_foreground_s": float(cov.get("first_foreground_s") or 8.0),
        "max_source_run_s": float(turn.get("max_source_run_s") or 16.0),
        "min_feasible_sources": int(turn.get("min_feasible_sources") or 11),
        "floor_coverage": float(cov.get("floor_coverage") or 0.70),
        "foreground_coverage": float(cov.get("foreground_coverage") or 0.50),
        "max_silent_gap_s": float(cov.get("max_silent_gap_s") or 2.0),
        "min_edge_score": float(profile.get("min_edge_score") or 0.54),
        "seconds_per_event": float(dens.get("seconds_per_event") or 11.0),
        "sources_per_minute": float(dens.get("sources_per_minute") or 5.5),
        "min_layers": int(dens.get("min_layers") or 2),
        "max_layers": int(dens.get("max_layers") or 4),
        "max_source_share": float(turn.get("foreground_max_share") or 0.20),
        "min_recycle_gap_s": float(endl.get("min_recycle_gap_s") or 900.0),
        "objective_weights": dict(profile.get("objective_weights") or {}),
        "tastespec_id": profile.get("id"),
        "tastespec_version": profile.get("version"),
        "tastespec_hash": profile.get("hash"),
    }
