"""The library.json contract — the stable, versioned seam other projects consume.

A Library is a plain, documented JSON structure (see LIBRARY_CONTRACT.md). It is
produced by scanning, enriched by organize, and read by anything downstream
(EarCrate is consumer #1). Nothing about it depends on how it was produced."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List

CONTRACT_VERSION = "1.0"


def build_library(records: List[Dict[str, Any]], roots: List[str], generated_at: str = "") -> Dict[str, Any]:
    """Assemble records into the contract, marking duplicates (by content hash;
    the first-seen copy is canonical). Album-level compilation clustering: any
    album whose tracks carry 2+ distinct artists is a compilation even without an
    albumartist tag."""
    ok = [r for r in records if "error" not in r]
    errs = [r for r in records if "error" in r]

    # duplicate detection by content hash, first seen wins
    seen_hash: Dict[str, str] = {}
    for r in ok:
        h = r.get("sha256")
        if not h:
            r["duplicate_of"] = None
            continue
        if h in seen_hash:
            r["duplicate_of"] = seen_hash[h]
        else:
            seen_hash[h] = r["path"]
            r["duplicate_of"] = None

    # album-level compilation clustering (albumartist-less comps)
    album_artists: Dict[str, set] = {}
    for r in ok:
        ident = r.get("identity") or {}
        alb = str(ident.get("album") or "").lower()
        if alb and alb != "unknown album":
            album_artists.setdefault(alb, set()).add(str(ident.get("track_artist") or "").lower())
    comp_albums = {a for a, arts in album_artists.items() if len(arts) >= 2}
    for r in ok:
        ident = r.get("identity") or {}
        if str(ident.get("album") or "").lower() in comp_albums:
            ident["compilation"] = True

    tracks = []
    for r in ok:
        ident = r.get("identity") or {}
        tracks.append({
            "path": r["path"], "sha256": r.get("sha256"),
            "size_bytes": r.get("size_bytes"), "duration_s": r.get("duration_s"),
            "codec": r.get("codec"), "mtime": r.get("mtime"),
            "identity": ident,
            "tags": r.get("tags") or {},
            "archive_path": r.get("archive_path"),
            "duplicate_of": r.get("duplicate_of"),
            "quality": {
                "identity_source": ident.get("identity_source"),
                "untagged": ident.get("identity_source") in ("filename", "folder", "unknown"),
                "unknown_artist": ident.get("artist") == "Unknown Artist",
                "unknown_album": ident.get("album") == "Unknown Album",
                "is_duplicate": bool(r.get("duplicate_of")),
                "hash_error": r.get("hash_error"),
            },
        })
    uniq = [t for t in tracks if not t["duplicate_of"]]
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "roots": [str(x) for x in roots],
        "count": len(tracks),
        "unique_count": len(uniq),
        "duplicate_count": len(tracks) - len(uniq),
        "error_count": len(errs),
        "errors": errs[:200],
        "tracks": tracks,
    }


def write_library(library: Dict[str, Any], path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def read_library(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
