from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .util import clamp, sha256_json


def _intervals(tracks: Sequence[Mapping[str, Any]], role: str | None = None) -> list[tuple[float, float, Mapping[str, Any]]]:
    rows: list[tuple[float, float, Mapping[str, Any]]] = []
    for track in tracks:
        if role is not None and str(track.get("role") or "") != role:
            continue
        for clip in track.get("clips") or []:
            if bool(clip.get("muted")):
                continue
            start = float(clip.get("timeline_start_beat") or 0.0)
            end = start + max(0.0, float(clip.get("timeline_duration_beats") or 0.0))
            if end > start:
                rows.append((start, end, clip))
    rows.sort(key=lambda row: (row[0], row[1], str(row[2].get("clip_id") or "")))
    return rows


def _coverage(intervals: Sequence[tuple[float, float, Mapping[str, Any]]], end_beat: float) -> float:
    if end_beat <= 0.0 or not intervals:
        return 0.0
    merged: list[list[float]] = []
    for start, end, _clip in intervals:
        start = max(0.0, min(end_beat, start))
        end = max(start, min(end_beat, end))
        if end <= start:
            continue
        if not merged or start > merged[-1][1] + 1e-9:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return clamp(sum(end - start for start, end in merged) / end_beat, 0.0, 1.0)


def _largest_gap(intervals: Sequence[tuple[float, float, Mapping[str, Any]]], end_beat: float) -> float:
    if end_beat <= 0.0:
        return 0.0
    merged: list[list[float]] = []
    for start, end, _clip in intervals:
        start = max(0.0, min(end_beat, start))
        end = max(start, min(end_beat, end))
        if end <= start:
            continue

        if not merged or start > merged[-1][1] + 1e-9:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    cursor = 0.0
    gap = 0.0
    for start, end in merged:
        gap = max(gap, start - cursor)
        cursor = max(cursor, end)
    return max(gap, end_beat - cursor)


def static_gate(
    *,
    tracks: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Any],
    policy: Mapping[str, Any],
    bpm: float,
    total_bars: int,
) -> dict[str, Any]:
    """Fail-honest structural gate for the legacy clip-score compiler.

    This gate owns only the audio-clip score path. Causal-score custody has its
    own independent gate in :mod:`earcrate.project.custody` and is never made to
    look valid by manufacturing dummy clips.
    """
    end_beat = max(4.0, float(total_bars) * 4.0)
    all_intervals = _intervals(tracks)
    floor = _intervals(tracks, "floor")
    foreground = _intervals(tracks, "foreground")
    coverage = policy.get("coverage") or {}
    required_floor = float(coverage.get("floor_coverage") or 0.0)
    required_foreground = float(coverage.get("foreground_coverage") or 0.0)
    first_foreground_s = float(coverage.get("first_foreground_s") or 1e9)
    max_silent_gap_s = float(coverage.get("max_silent_gap_s") or 1e9)
    beats_per_second = max(1e-9, float(bpm) / 60.0)

    floor_coverage = _coverage(floor, end_beat)
    foreground_coverage = _coverage(foreground, end_beat)
    audible_coverage = _coverage(all_intervals, end_beat)
    first_fg_beat = min((row[0] for row in foreground), default=float("inf"))
    largest_gap_beats = _largest_gap(all_intervals, end_beat)

    failures: list[str] = []
    if not sources:
        failures.append("no_sources")
    if not all_intervals:
        failures.append("no_audible_clips")
    if floor_coverage + 1e-9 < required_floor:
        failures.append("floor_coverage")
    if foreground_coverage + 1e-9 < required_foreground:
        failures.append("foreground_coverage")
    if first_fg_beat / beats_per_second > first_foreground_s + 1e-9:
        failures.append("first_foreground")
    if largest_gap_beats / beats_per_second > max_silent_gap_s + 1e-9:
        failures.append("silent_gap")

    clip_ids = {
        str(clip.get("clip_id") or "")
        for track in tracks
        for clip in track.get("clips") or []
    }
    transition_failures = []
    for transition in transitions:
        outgoing = set(map(str, transition.get("outgoing_clip_ids") or []))
        incoming = set(map(str, transition.get("incoming_clip_ids") or []))
        missing = sorted((outgoing | incoming) - clip_ids)
        if missing:
            transition_failures.append({"transition_id": transition.get("transition_id"), "missing_clip_ids": missing})
    if transition_failures:
        failures.append("transition_references")

    source_use: Counter[str] = Counter(
        str(clip.get("source_id") or "")
        for track in tracks
        for clip in track.get("clips") or []
        if not bool(clip.get("muted"))
    )
    receipt = {
        "schema": "earcrate/project-static-gate@1",
        "passed": not failures,
        "failures": failures,
        "bpm": float(bpm),
        "total_bars": int(total_bars),
        "end_beat": end_beat,
        "clip_count": len(clip_ids),
        "source_count": len(sources),
        "source_use": dict(sorted(source_use.items())),
        "coverage": {
            "audible": round(audible_coverage, 9),
            "floor": round(floor_coverage, 9),
            "foreground": round(foreground_coverage, 9),
            "required_floor": required_floor,
            "required_foreground": required_foreground,
            "first_foreground_seconds": None if first_fg_beat == float("inf") else round(first_fg_beat / beats_per_second, 9),
            "maximum_first_foreground_seconds": first_foreground_s,
            "largest_silent_gap_seconds": round(largest_gap_beats / beats_per_second, 9),
            "maximum_silent_gap_seconds": max_silent_gap_s,
        },
        "transition_failures": transition_failures,
    }
    receipt["gate_sha256"] = sha256_json(receipt)
    return receipt
