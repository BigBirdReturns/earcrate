"""MusicBrainz enrichment — the license-clean, scalable path to check the WHOLE
library against known samples/remixes/covers.

WhoSampled (the comprehensive sample DB) is closed: no open API, ToS forbids bulk
querying, and it blocks scrapers. MusicBrainz is OPEN (CC0 data + a public API) and
carries `samples material`, `remix`, and `cover` relationships. Coverage is thinner
than WhoSampled for samples, but it's the legitimate way to annotate all ~14k
library tracks at scale.

Etiquette baked in: a descriptive User-Agent, a >=1 req/sec rate limit (MB's rule),
and a local JSON cache so re-runs and resumes never re-query. This is a BOX job over
the title-level manifest (`export_library_manifest`) — a few hours of polite,
sequential querying — NOT a parallel scrape (MB rate-limits per IP; parallelism just
earns 503s).

The network call is injected (`fetch`), so the parsing/aggregation is deterministic
and gate-tested here without touching the network.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_MB = "https://musicbrainz.org/ws/2"
_UA = "EarCrate/0.1 (local-first mashup research; sample-graph)"
_RELEVANT = {"samples material", "remix", "cover"}


def _default_fetch(url: str) -> Dict[str, Any]:  # pragma: no cover (network)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def extract_relationships(recording_json: Dict[str, Any]) -> Dict[str, List[str]]:
    """PURE: pull sample/remix/cover relationships out of a MusicBrainz recording
    JSON (fetched with inc=recording-rels+work-rels). Direction 'forward' means THIS
    recording samples/remixes the target; 'backward' means the target does. Returns
    lists of human labels per relationship kind."""
    out: Dict[str, List[str]] = {"sample_of": [], "sampled_by": [],
                                 "remix_of": [], "remixed_by": [], "covers": []}
    for rel in recording_json.get("relations", []) or []:
        typ = rel.get("type")
        if typ not in _RELEVANT:
            continue
        direction = rel.get("direction")
        target = rel.get("recording") or rel.get("work") or rel.get("artist") or {}
        label = target.get("title") or target.get("name") or ""
        if not label:
            continue
        if typ == "samples material":
            (out["sample_of"] if direction == "forward" else out["sampled_by"]).append(label)
        elif typ == "remix":
            (out["remix_of"] if direction == "forward" else out["remixed_by"]).append(label)
        elif typ == "cover":
            out["covers"].append(label)
    return out


def enrich_track(artist: str, title: str,
                 fetch: Optional[Callable[[str], Dict[str, Any]]] = None,
                 sleep: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """Enrich ONE track: search MusicBrainz for the recording, then pull its
    sample/remix/cover relationships. Returns {matched, mbid?, artist, title, ...
    relationship lists}. Two requests (search + relationships); ``sleep`` is called
    between them so the caller can enforce the rate limit."""
    fetch = fetch or _default_fetch
    q = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"')
    res = fetch(f"{_MB}/recording?query={q}&fmt=json&limit=1")
    recs = res.get("recordings") or []
    if not recs:
        return {"matched": False, "artist": artist, "title": title}
    mbid = recs[0].get("id")
    if sleep:
        sleep()
    rel = fetch(f"{_MB}/recording/{mbid}?inc=recording-rels+work-rels&fmt=json")
    result = {"matched": True, "mbid": mbid, "artist": artist, "title": title}
    result.update(extract_relationships(rel))
    return result


def has_any_relationship(enriched: Dict[str, Any]) -> bool:
    """True if the track has ANY sample/remix/cover relationship (worth keeping in
    the graph; most tracks won't)."""
    return any(enriched.get(k) for k in ("sample_of", "sampled_by", "remix_of", "remixed_by", "covers"))


def enrich_library(manifest_path: str, out_path: str, cache_path: str = "",
                   limit: int = 0, min_interval_s: float = 1.1,
                   fetch: Optional[Callable[[str], Dict[str, Any]]] = None,
                   progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:  # pragma: no cover (box job)
    """Batch-enrich the whole library manifest (from export_library_manifest) into a
    relationship graph. RESUMABLE via ``cache_path`` (an "artist\\ttitle" -> result
    JSON map); rate-limited to >=``min_interval_s`` per request. Writes only the
    tracks that HAVE a relationship to ``out_path``. This is the polite box pass."""
    fetch = fetch or _default_fetch
    tracks = json.loads(Path(manifest_path).read_text(encoding="utf-8")).get("tracks") or []
    if limit:
        tracks = tracks[:limit]
    cache: Dict[str, Any] = {}
    cp = Path(cache_path) if cache_path else None
    if cp and cp.exists():
        cache = json.loads(cp.read_text(encoding="utf-8"))
    last = [0.0]

    def _sleep() -> None:
        dt = min_interval_s - (time.monotonic() - last[0])
        if dt > 0:
            time.sleep(dt)
        last[0] = time.monotonic()

    graph: List[Dict[str, Any]] = []
    for i, t in enumerate(tracks):
        artist, title = str(t.get("artist") or ""), str(t.get("title") or "")
        if not artist or not title:
            continue
        key = artist + "\t" + title
        if key in cache:
            enriched = cache[key]
        else:
            _sleep()
            try:
                enriched = enrich_track(artist, title, fetch=fetch, sleep=_sleep)
            except Exception as exc:
                enriched = {"matched": False, "artist": artist, "title": title, "error": str(exc)[:120]}
            cache[key] = enriched
            if cp and (i % 50 == 0):
                cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        if has_any_relationship(enriched):
            graph.append(enriched)
        if progress:
            progress(i + 1, len(tracks))
    if cp:
        cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    Path(out_path).write_text(json.dumps(
        {"source": "musicbrainz", "tracks_checked": len(tracks),
         "with_relationships": len(graph), "graph": graph}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "tracks_checked": len(tracks), "with_relationships": len(graph), "out": out_path}
