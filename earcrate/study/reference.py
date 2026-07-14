"""Deterministic reference study: documented Girl Talk data -> engine ground truth.

Four pure functions, no I/O beyond one JSON read in ``load_reference``:

* ``load_reference``      validate/normalize a dataset (path or dict) to the
                          shared schema.
* ``reference_fingerprint`` measure the persona targets that the hand-tuned
                          profile only *guessed* — density, source run length,
                          simultaneous layer count, per-track counts — from the
                          REAL data. Absent timing yields ``None`` (marked
                          unavailable), never a fabricated number.
* ``reference_edges``     the Girl-Talk-PROVEN pairings: every pair of samples
                          that OVERLAP inside one track is a labeled positive
                          compatibility edge.
* ``calibrate_profile``   return a NEW profile whose source_turnover /
                          density_model numbers are REPLACED by the measured
                          ones, plus a diff of exactly what changed. The shipped
                          JSON is never mutated in place.

Determinism contract: same input dict -> byte-identical output. No clock, no
randomness, stable iteration order, fixed rounding. Floats are rounded to
``_NDIGITS`` decimals so accumulation order can never leak FP noise into a
persona target or a plan hash.

Shared dataset schema (both study agents use exactly this shape)::

    {"album": str, "artist": str, "sources": [str, ...],
     "tracks": [
        {"index": int, "title": str, "duration_s": float|None,
         "samples": [
            {"source_artist": str, "source_title": str,
             "start_s": float|None, "end_s": float|None,
             "role": str|None}]}]}

Timestamps are SECONDS from the track start. start_s/end_s may be null when only
a source LIST (no timing) is available — still valuable: per-track pairings need
no timing and density needs only the track duration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Every measured number rounds to this many decimals before it leaves a public
# function, so the sweep/accumulation order inside layer integration can never
# produce a persona target that differs in the 12th decimal between runs.
_NDIGITS = 6


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), _NDIGITS)


# --------------------------------------------------------------------------- #
# 1) load_reference                                                           #
# --------------------------------------------------------------------------- #
def _as_opt_float(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise ValueError(f"{where}: expected a number or null, got a bool")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{where}: expected a number or null, got {type(value).__name__}")


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-empty string")
    return value


def _normalize_sample(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: sample must be an object")
    start = _as_opt_float(raw.get("start_s"), f"{where}.start_s")
    end = _as_opt_float(raw.get("end_s"), f"{where}.end_s")
    if (start is None) != (end is None):
        raise ValueError(f"{where}: start_s and end_s must both be set or both be null")
    if start is not None and end is not None and end < start:
        raise ValueError(f"{where}: end_s ({end}) is before start_s ({start})")
    role = raw.get("role")
    if role is not None and not isinstance(role, str):
        raise ValueError(f"{where}.role: expected a string or null")
    return {
        "source_artist": _as_str(raw.get("source_artist"), f"{where}.source_artist"),
        "source_title": _as_str(raw.get("source_title"), f"{where}.source_title"),
        "start_s": start,
        "end_s": end,
        "role": role if role else None,
    }


def _normalize_track(raw: Any, ordinal: int) -> Dict[str, Any]:
    where = f"tracks[{ordinal}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: track must be an object")
    index = raw.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError(f"{where}.index: expected an integer")
    samples_raw = raw.get("samples")
    if not isinstance(samples_raw, list):
        raise ValueError(f"{where}.samples: expected a list")
    samples = [_normalize_sample(s, f"{where}.samples[{i}]") for i, s in enumerate(samples_raw)]
    return {
        "index": index,
        "title": _as_str(raw.get("title"), f"{where}.title"),
        "duration_s": _as_opt_float(raw.get("duration_s"), f"{where}.duration_s"),
        "samples": samples,
    }


def load_reference(path_or_dict: Any) -> Dict[str, Any]:
    """Validate a reference dataset (a filesystem path or an in-memory dict) and
    return a normalized copy conforming to the shared schema.

    Accepts a ``str``/``Path`` (read as UTF-8 JSON) or an already-parsed ``dict``.
    Raises ``ValueError`` with a field path on any schema violation. The returned
    dict is a fresh normalized structure — mutating it never touches the input.
    """
    if isinstance(path_or_dict, (str, Path)):
        data = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
    elif isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        raise TypeError("load_reference expects a path, or a dataset dict")
    if not isinstance(data, dict):
        raise ValueError("reference dataset must be a JSON object")
    tracks_raw = data.get("tracks")
    if not isinstance(tracks_raw, list) or not tracks_raw:
        raise ValueError("tracks: expected a non-empty list")
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise ValueError("sources: expected a list")
    return {
        "album": _as_str(data.get("album"), "album"),
        "artist": _as_str(data.get("artist"), "artist"),
        "sources": [_as_str(s, f"sources[{i}]") for i, s in enumerate(sources)],
        "tracks": [_normalize_track(t, i) for i, t in enumerate(tracks_raw)],
    }


# --------------------------------------------------------------------------- #
# helpers shared by fingerprint + edges                                       #
# --------------------------------------------------------------------------- #
def _timed(sample: Dict[str, Any]) -> bool:
    return sample.get("start_s") is not None and sample.get("end_s") is not None


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _track_layer_integral(samples: List[Dict[str, Any]]) -> Tuple[float, float]:
    """Time-weighted layer integral for one track.

    Returns ``(integral, active_seconds)`` where ``integral`` is the sum over the
    timeline of (simultaneously-active sample count * segment length) and
    ``active_seconds`` is the total time at least one sample is playing. A
    boundary-swept sum of +1 at each start / -1 at each end makes the result
    independent of sample order. Both are 0 when no timed sample exists.
    """
    events: List[Tuple[float, int]] = []
    for s in samples:
        if _timed(s):
            events.append((float(s["start_s"]), 1))
            events.append((float(s["end_s"]), -1))
    if not events:
        return 0.0, 0.0
    events.sort()
    integral = 0.0
    active_seconds = 0.0
    depth = 0
    prev = events[0][0]
    for point, delta in events:
        span = point - prev
        if span > 0 and depth > 0:
            integral += depth * span
            active_seconds += span
        depth += delta
        prev = point
    return integral, active_seconds


def reference_fingerprint(dataset: Any) -> Dict[str, Any]:
    """Measure the persona targets from the REAL data.

    Returns a dict of measured numbers plus an ``availability`` map naming which
    targets the data could and could not supply:

    * ``samples_per_minute`` — total samples / total timed-duration minutes
      (needs at least one track with a known ``duration_s``).
    * ``source_seconds`` / ``median_source_run_s`` / ``max_source_run_s`` — mean,
      median, max of ``end_s - start_s`` over timed samples.
    * ``mean_layers`` — time-weighted mean simultaneous sample count across the
      spans where samples actually play (from overlaps).
    * ``per_track_sample_counts`` — one entry per track, in dataset order.

    Every value that the data cannot support is ``None`` and flagged unavailable
    rather than fabricated.
    """
    data = load_reference(dataset)
    tracks = data["tracks"]

    total_samples = 0
    total_duration_s = 0.0
    any_duration = False
    run_lengths: List[float] = []
    layer_integral = 0.0
    layer_active_s = 0.0
    per_track: List[Dict[str, Any]] = []

    for t in tracks:
        samples = t["samples"]
        total_samples += len(samples)
        if t["duration_s"] is not None:
            any_duration = True
            total_duration_s += float(t["duration_s"])
        for s in samples:
            if _timed(s):
                run_lengths.append(float(s["end_s"]) - float(s["start_s"]))
        integ, active = _track_layer_integral(samples)
        layer_integral += integ
        layer_active_s += active
        per_track.append({"track": t["index"], "title": t["title"], "samples": len(samples)})

    have_timing = bool(run_lengths)
    have_density = any_duration and total_duration_s > 0.0
    have_layers = layer_active_s > 0.0

    samples_per_minute = (total_samples / (total_duration_s / 60.0)) if have_density else None
    source_seconds = (sum(run_lengths) / len(run_lengths)) if have_timing else None
    median_run = _median(run_lengths) if have_timing else None
    max_run = max(run_lengths) if have_timing else None
    mean_layers = (layer_integral / layer_active_s) if have_layers else None

    return {
        "album": data["album"],
        "artist": data["artist"],
        "samples_per_minute": _round(samples_per_minute),
        "source_seconds": _round(source_seconds),
        "median_source_run_s": _round(median_run),
        "max_source_run_s": _round(max_run),
        "mean_layers": _round(mean_layers),
        "per_track_sample_counts": per_track,
        "totals": {
            "tracks": len(tracks),
            "samples": total_samples,
            "timed_samples": len(run_lengths),
            "duration_s": _round(total_duration_s) if any_duration else None,
        },
        "availability": {
            "samples_per_minute": have_density,
            "source_seconds": have_timing,
            "max_source_run_s": have_timing,
            "mean_layers": have_layers,
        },
    }


# --------------------------------------------------------------------------- #
# 3) reference_edges                                                          #
# --------------------------------------------------------------------------- #
def reference_edges(dataset: Any) -> List[Dict[str, Any]]:
    """The Girl-Talk-PROVEN pairings as labeled positive compatibility edges.

    For each track, every pair of TIMED samples whose ``[start_s, end_s]``
    intervals OVERLAP (share positive time) becomes one edge::

        {"a": {"artist", "title"}, "b": {"artist", "title"},
         "track": <track index>, "overlap_s": <shared seconds>}

    Pairs are emitted in track order then ascending ``(i, j)`` sample-index order,
    so the returned list is deterministic. Zero-length "touch at a boundary"
    contacts (overlap_s == 0) are NOT edges — nothing is layered there.
    """
    data = load_reference(dataset)
    edges: List[Dict[str, Any]] = []
    for t in data["tracks"]:
        samples = t["samples"]
        for i in range(len(samples)):
            a = samples[i]
            if not _timed(a):
                continue
            for j in range(i + 1, len(samples)):
                b = samples[j]
                if not _timed(b):
                    continue
                overlap = min(float(a["end_s"]), float(b["end_s"])) - max(
                    float(a["start_s"]), float(b["start_s"]))
                if overlap > 0.0:
                    edges.append({
                        "a": {"artist": a["source_artist"], "title": a["source_title"]},
                        "b": {"artist": b["source_artist"], "title": b["source_title"]},
                        "track": t["index"],
                        "overlap_s": _round(overlap),
                    })
    return edges


# --------------------------------------------------------------------------- #
# 3b) reference_recall — the answer-key benchmark                              #
# --------------------------------------------------------------------------- #
def source_key(artist: Any, title: Any) -> str:
    """Normalized identity for matching a reference source to a library track:
    lowercased, alphanumerics only, feat./remix noise dropped. Deterministic."""
    def _n(s: Any) -> str:
        s = str(s or "").lower()
        for cut in (" feat", " ft.", " ft ", " featuring", " (feat", " prod"):
            i = s.find(cut)
            if i != -1:
                s = s[:i]
        return "".join(ch for ch in s if ch.isalnum())
    return _n(artist) + "|" + _n(title)


def reference_source_keys(dataset: Any) -> List[str]:
    """Every distinct source track the reference draws on (normalized keys), in
    first-seen order."""
    data = load_reference(dataset)
    seen: Dict[str, None] = {}
    for t in data["tracks"]:
        for s in t["samples"]:
            seen.setdefault(source_key(s["source_artist"], s["source_title"]), None)
    return list(seen.keys())


def reference_cooccurrence_edges(dataset: Any) -> List[Dict[str, Any]]:
    """Same-track CO-USE pairings for UNTIMED datasets (most producer sample maps
    have no per-sample timing). The producer combined these sources into ONE
    track, so every unordered pair of DISTINCT sources within a track is a co-use
    edge. Deduped across the album (first-seen track kept). overlap_s is None."""
    data = load_reference(dataset)
    edges: List[Dict[str, Any]] = []
    seen: set = set()
    for t in data["tracks"]:
        srcs = [(s["source_artist"], s["source_title"]) for s in t["samples"]]
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                a, b = srcs[i], srcs[j]
                ka, kb = source_key(*a), source_key(*b)
                if ka == kb:
                    continue
                pair = frozenset((ka, kb))
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append({"a": {"artist": a[0], "title": a[1]},
                              "b": {"artist": b[0], "title": b[1]},
                              "track": t["index"], "overlap_s": None})
    return edges


def reference_pairings(dataset: Any) -> Tuple[List[Dict[str, Any]], str]:
    """The proven pairings + which notion produced them: TIMED overlap edges when
    the dataset carries per-sample timing (Girl Talk), else same-track
    CO-OCCURRENCE (Donuts-style producer maps). Auto-selected so recall works for
    both kinds of answer key."""
    timed = reference_edges(dataset)
    if timed:
        return timed, "timed_overlap"
    return reference_cooccurrence_edges(dataset), "same_track_cooccurrence"


def recall_report(dataset: Any, present_source_keys: Any,
                  recovered_pair_keys: Any) -> Dict[str, Any]:
    """The answer-key benchmark: of the PROVEN pairings the masters actually used,
    how many does OUR engine independently recover from OUR library?

    * ``present_source_keys``  — normalized source keys (source_key()) that exist
      in the library (what we even HAVE to work with).
    * ``recovered_pair_keys``  — set of frozenset({keyA, keyB}) pairs the engine
      independently linked (compatibility edge / positive score) between atoms of
      those two sources.

    A proven edge is RECOVERABLE only when BOTH its sources are present (you can't
    rediscover a pairing whose material you don't own); of those, RECOVERED when
    the engine linked them. ``missed`` lists the recoverable-but-not-recovered
    pairings — exactly what the system SHOULD have discovered on its own but
    didn't, ranked by the masters' overlap time (strongest evidence first)."""
    present = set(present_source_keys or [])
    recovered = set(recovered_pair_keys or [])
    edges, pairing_mode = reference_pairings(dataset)
    all_sources = reference_source_keys(dataset)
    recoverable: List[Dict[str, Any]] = []
    recovered_edges: List[Dict[str, Any]] = []
    missed: List[Dict[str, Any]] = []
    seen_pairs: set = set()
    for e in edges:
        ka = source_key(e["a"]["artist"], e["a"]["title"])
        kb = source_key(e["b"]["artist"], e["b"]["title"])
        pair = frozenset((ka, kb))
        if ka == kb or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        if ka in present and kb in present:
            recoverable.append(e)
            if pair in recovered:
                recovered_edges.append(e)
            else:
                missed.append(e)
    n_recov = len(recoverable)
    missed.sort(key=lambda e: -float(e.get("overlap_s") or 0.0))
    return {
        "album": load_reference(dataset)["album"],
        "pairing_mode": pairing_mode,
        "sources_total": len(all_sources),
        "sources_in_library": sum(1 for k in all_sources if k in present),
        "proven_pairs_total": len(seen_pairs),
        "recoverable": n_recov,
        "recovered": len(recovered_edges),
        "recall": _round(len(recovered_edges) / n_recov) if n_recov else None,
        "missed": [{"a": e["a"], "b": e["b"], "overlap_s": e["overlap_s"]} for e in missed[:25]],
    }


# --------------------------------------------------------------------------- #
# 4) calibrate_profile                                                        #
# --------------------------------------------------------------------------- #
# Each entry maps a measured fingerprint value onto the profile field it should
# REPLACE. A value function computes the target from the fingerprint (some fields
# derive, e.g. seconds_per_event = 60 / samples_per_minute). Order fixes the diff
# order. A measured value of None leaves the shipped number untouched.
_CALIBRATION = (
    ("source_turnover", "source_seconds",
     lambda fp: fp["source_seconds"]),
    ("source_turnover", "max_source_run_s",
     lambda fp: fp["max_source_run_s"]),
    ("density_model", "seconds_per_event",
     lambda fp: (60.0 / fp["samples_per_minute"]) if fp.get("samples_per_minute") else None),
    ("density_model", "sources_per_minute",
     lambda fp: fp["samples_per_minute"]),
)


def calibrate_profile(fingerprint: Dict[str, Any],
                      base_profile_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Return a NEW profile whose source_turnover/density_model numbers are the
    measured ones, plus a diff of exactly what changed.

    ``base_profile_dict`` (e.g. the hand-tuned ``girl_talk_v1``) is deep-copied,
    never mutated in place. Only fields the fingerprint can support are replaced;
    an unavailable measurement leaves the shipped guess as-is and produces no diff
    entry. Any pre-existing ``hash`` on the base is dropped from the copy because
    the numbers underneath it changed — the caller re-hashes the calibrated
    profile through the normal TasteSpec path.

    Returns ``{"profile": <calibrated copy>, "diff": [ {section, field, from, to} ]}``.
    The diff is deterministic (fixed field order) and empty when nothing changed.
    """
    calibrated = json.loads(json.dumps(base_profile_dict))  # deep copy, JSON-safe
    calibrated.pop("hash", None)
    diff: List[Dict[str, Any]] = []
    for section, field, value_fn in _CALIBRATION:
        measured = value_fn(fingerprint)
        if measured is None:
            continue
        measured = _round(measured)
        block = calibrated.get(section)
        if not isinstance(block, dict):
            block = {}
            calibrated[section] = block
        old = block.get(field)
        old_num = round(float(old), _NDIGITS) if isinstance(old, (int, float)) and not isinstance(old, bool) else old
        block[field] = measured
        if old_num != measured:
            diff.append({"section": section, "field": field, "from": old, "to": measured})
    calibrated["provenance_calibration"] = {
        "basis": "reference_fingerprint",
        "album": fingerprint.get("album"),
        "artist": fingerprint.get("artist"),
        "fields_calibrated": [f"{d['section']}.{d['field']}" for d in diff],
    }
    return {"profile": calibrated, "diff": diff}
