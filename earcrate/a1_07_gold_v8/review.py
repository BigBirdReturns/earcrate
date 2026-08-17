from __future__ import annotations

from pathlib import Path
import random
import re
import shutil
from typing import Any, Mapping, Sequence

from .common import (
    DescentError,
    atomic_write_json,
    load_json,
    run,
    seal,
    sha256_file,
    validate_seal,
)


def measure_loudness(path: Path, *, ffmpeg: str) -> tuple[float, float]:
    result = run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        timeout=1800,
    )
    text = result.stderr
    # Read the trailing Summary block, never the per-frame trace. ebur128 prints a
    # running `I:` on every frame, and a track that opens quietly reports the -70
    # LUFS floor for its first frames. Pairing that floor with the summary's `Peak:`
    # -- which an unanchored `I: ... .*? Peak:` match does -- reported near-silence
    # for any quiet intro, and every A1-07 arc opens quiet. level_match then clamped
    # on peak instead of loudness, so cuts reached the owner peak-normalized rather
    # than level-matched.
    tail = text.rsplit("Summary:", 1)[-1] if "Summary:" in text else ""
    integrated = re.search(
        r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", tail, flags=re.S)
    peak = re.search(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", tail, flags=re.S)
    if integrated:
        return float(integrated.group(1)), float(peak.group(1)) if peak else -99.0
    raise DescentError(f"could not measure loudness: {path}")


def level_match(
    source: Path,
    destination: Path,
    *,
    target_lufs: float,
    peak_ceiling: float,
    ffmpeg: str,
) -> dict[str, Any]:
    lufs, peak = measure_loudness(source, ffmpeg=ffmpeg)
    gain = target_lufs - lufs
    if peak + gain > peak_ceiling:
        gain = peak_ceiling - peak
    result = run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"volume={gain:.12g}dB",
            "-map_metadata",
            "-1",
            "-c:a",
            "flac",
            "-compression_level",
            "8",
            str(destination),
        ],
        timeout=1800,
    )
    if result.returncode != 0 or not destination.is_file():
        raise DescentError(f"level match failed: {result.stderr[-2000:]}")
    post_lufs, post_peak = measure_loudness(destination, ffmpeg=ffmpeg)
    return {
        "source_lufs": lufs,
        "source_peak_dbfs": peak,
        "gain_db": gain,
        "output_lufs": post_lufs,
        "output_peak_dbfs": post_peak,
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
    }


def make_blind_lane(
    lane_root: Path,
    sources: Mapping[str, Path],
    *,
    dimensions: Sequence[str],
    target_lufs: float,
    peak_ceiling: float,
    ffmpeg: str,
) -> dict[str, Any]:
    public = lane_root / "public"
    private = lane_root / "private"
    public.mkdir(parents=True)
    private.mkdir()
    matched: dict[str, Path] = {}
    metrics: dict[str, Any] = {}
    for semantic, source in sorted(sources.items()):
        destination = private / f"{semantic}.flac"
        metrics[semantic] = level_match(
            source,
            destination,
            target_lufs=target_lufs,
            peak_ceiling=peak_ceiling,
            ffmpeg=ffmpeg,
        )
        matched[semantic] = destination
    semantics = list(sorted(matched))
    random.SystemRandom().shuffle(semantics)
    option_map: dict[str, str] = {}
    options: dict[str, Any] = {}
    for index, semantic in enumerate(semantics):
        label = chr(ord("A") + index)
        destination = public / f"{label}.flac"
        shutil.copyfile(matched[semantic], destination)
        option_map[label] = semantic
        options[label] = {
            "sha256": sha256_file(destination),
            "bytes": destination.stat().st_size,
        }
    assignment = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_a1_07_gold_v8_review_assignment",
            "options": options,
            "choices": [*sorted(options), "tie", "reject_all", "abstain"],
            "dimensions": list(dimensions),
            "roles_withheld": True,
            "target_lufs": target_lufs,
            "peak_ceiling_dbfs": peak_ceiling,
        },
        "assignment_sha256",
    )
    authority = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_a1_07_gold_v8_review_authority",
            "assignment_sha256": assignment["assignment_sha256"],
            "option_map": option_map,
            "private_metrics": metrics,
        },
        "authority_sha256",
    )
    atomic_write_json(public / "assignment.json", assignment)
    atomic_write_json(private / "authority.json", authority)
    return {
        "assignment": assignment,
        "authority": authority,
        "public": public,
        "private": private,
    }


def seal_review(
    workspace: Path,
    *,
    whole_ranking: str,
    core_ranking: str,
    note: str,
) -> dict[str, Any]:
    root = workspace.expanduser().absolute()
    results: dict[str, Any] = {}
    for lane, ranking in (
        ("whole-arc", whole_ranking),
        ("core-window", core_ranking),
    ):
        assignment = load_json(root / "review" / lane / "public" / "assignment.json")
        authority = load_json(root / "review" / lane / "private" / "authority.json")
        validate_seal(assignment, "assignment_sha256")
        validate_seal(authority, "authority_sha256")
        labels = set(assignment["options"])
        tokens = [
            token.strip().upper()
            for token in re.split(r"[>,= ]+", ranking)
            if token.strip()
        ]
        terminal = ranking.strip().lower() in {"tie", "reject_all", "abstain"}
        if not terminal and (
            not tokens or any(token not in labels for token in tokens)
        ):
            raise DescentError(f"invalid {lane} ranking")
        semantic = [authority["option_map"].get(token) for token in tokens]
        results[lane] = {
            "ranking": ranking,
            "semantic_ranking": semantic,
            "assignment_sha256": assignment["assignment_sha256"],
        }
    receipt = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_a1_07_gold_v8_owner_review",
            "whole_arc": results["whole-arc"],
            "core_window": results["core-window"],
            "note": note,
            "authority": {
                "relative_preference_only": True,
                "album_acceptance": False,
                "recovery_open": False,
            },
        },
        "review_receipt_sha256",
    )
    destination = root / "review" / "private" / "owner-review.receipt.json"
    atomic_write_json(destination, receipt)
    projection = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_a1_07_gold_v8_owner_review_projection",
            "review_receipt_sha256": receipt["review_receipt_sha256"],
            "whole_arc_semantic_ranking": results["whole-arc"]["semantic_ranking"],
            "core_window_semantic_ranking": results["core-window"]["semantic_ranking"],
            "album_acceptance": False,
            "recovery_open": False,
        },
        "projection_sha256",
    )
    atomic_write_json(root / "OWNER_REVIEW_PROJECTION.json", projection)
    return {
        "ok": True,
        "review_receipt_sha256": receipt["review_receipt_sha256"],
        "projection_sha256": projection["projection_sha256"],
        **results,
    }
