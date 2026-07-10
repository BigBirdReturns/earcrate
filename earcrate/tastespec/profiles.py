from __future__ import annotations
import json, hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "profiles"

@lru_cache(maxsize=16)
def load_tastespec(profile_id: str = "girl_talk_v1") -> Dict[str, Any]:
    path = PROFILE_DIR / f"{profile_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hash"] = tastespec_hash(data)
    return data

def tastespec_hash(profile: Dict[str, Any]) -> str:
    clean = {k: v for k, v in profile.items() if k != "hash"}
    payload = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def profile_summary(profile_id: str = "girl_talk_v1") -> Dict[str, Any]:
    p = load_tastespec(profile_id)
    return {"id": p["id"], "version": p["version"], "hash": p["hash"], "permitted_roles": p["permitted_roles"]}
