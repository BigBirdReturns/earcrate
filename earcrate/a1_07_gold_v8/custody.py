from __future__ import annotations

from array import array
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .common import (
    AUDIO_SUFFIXES,
    EXPECTED,
    DescentError,
    bytes_to_samples,
    canonical_pcm_sha256,
    ffprobe_info,
    load_json,
    sha256_file,
    validate_seal,
)


def score_mask(
    mask: Mapping[str, Any],
    parent: array,
    interplay: array,
    *,
    channels: int,
) -> tuple[int, float, int]:
    text = str(mask.get("musical_function") or "").lower()
    keyword_weights = {
        "fill": 100,
        "launch": 95,
        "handoff": 90,
        "answer": 85,
        "response": 80,
        "release": 75,
        "punctuation": 70,
        "ownership": 65,
    }
    semantic = max(
        (weight for word, weight in keyword_weights.items() if word in text),
        default=0,
    )
    start = int(mask["start_sample"])
    end = int(mask["end_sample"])
    difference = 0.0
    count = max(1, (end - start) * channels)
    for index in range(start * channels, end * channels):
        difference += abs(int(parent[index]) - int(interplay[index]))
    return semantic, difference / count, -(end - start)


def choose_handoff_mask(
    masks: Sequence[Mapping[str, Any]],
    *,
    parent_pcm: bytes,
    interplay_pcm: bytes,
    channels: int,
) -> dict[str, Any]:
    if not masks:
        raise DescentError("interplay machine receipt contains no declared masks")
    parent = bytes_to_samples(parent_pcm)
    interplay = bytes_to_samples(interplay_pcm)
    candidates = [dict(mask) for mask in masks]
    for mask in candidates:
        start = int(mask.get("start_sample", -1))
        end = int(mask.get("end_sample", -1))
        if start < 0 or end <= start or end * channels > len(parent):
            raise DescentError("invalid interplay mask")
    candidates.sort(
        key=lambda row: score_mask(row, parent, interplay, channels=channels),
        reverse=True,
    )
    selected = candidates[0]
    selected_score = score_mask(selected, parent, interplay, channels=channels)
    selected["selection_reason"] = {
        "semantic_priority": selected_score[0],
        "mean_absolute_pcm_delta": selected_score[1],
        "deterministic_policy": (
            "prefer fill/launch/handoff/answer/response/release, then larger audible delta"
        ),
    }
    return selected


def find_score(root: Path, expected_sha: str) -> Path:
    matches: list[Path] = []
    for path in root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            isinstance(value, dict)
            and str(value.get("score_sha256") or "").lower() == expected_sha
        ):
            matches.append(path)
    if not matches:
        raise DescentError(f"score {expected_sha} not found under {root}")
    matches.sort(
        key=lambda path: (
            "performance-score" not in path.name,
            len(path.parts),
            str(path),
        )
    )
    return matches[0]


def one_audio(root: Path, name: str | None = None) -> Path:
    if name:
        path = root / name
        if path.is_file():
            return path
    files = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )
    if len(files) != 1:
        raise DescentError(f"expected one audio file under {root}, found {len(files)}")
    return files[0]


def load_machine(
    workspace: Path,
    child: str,
    expected_score: str,
    expected_pcm: str,
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    machine_root = workspace / child / "machine"
    receipt_path = machine_root / "machine-receipt.json"
    receipt = load_json(receipt_path)
    if (
        receipt.get("kind") != "a1_07_gold_v7_machine_receipt"
        or receipt.get("candidate_id") != child
    ):
        raise DescentError(f"wrong v7 machine receipt for {child}")
    validate_seal(receipt, "machine_receipt_sha256")
    if (
        receipt.get("candidate_score_sha256") != expected_score
        or receipt.get("candidate_pcm_sha256") != expected_pcm
    ):
        raise DescentError(f"v7 machine receipt identity mismatch for {child}")
    audio = one_audio(machine_root, str(receipt.get("qualified_audio_name") or ""))
    score = find_score(workspace / child, expected_score)
    info = ffprobe_info(audio)
    observed_pcm = canonical_pcm_sha256(
        audio,
        sample_rate=info["sample_rate"],
        channels=info["channels"],
        ffmpeg=ffmpeg,
    )
    if observed_pcm != expected_pcm:
        raise DescentError(f"v7 audio PCM mismatch for {child}: {observed_pcm}")
    return {
        "receipt": receipt,
        "receipt_path": receipt_path,
        "audio": audio,
        "score": score,
        "info": info,
    }


def verify_inputs(v7_workspace: Path, *, ffmpeg: str) -> dict[str, Any]:
    root = v7_workspace.expanduser().absolute()
    if not root.is_dir():
        raise DescentError(f"v7 workspace missing: {root}")
    owner_receipt = root / "incumbent" / "owner-review.receipt.json"
    if sha256_file(owner_receipt) != EXPECTED["owner_review"]:
        raise DescentError("wrong gold-v6 owner-review receipt")
    parent_score = root / "incumbent" / "performance-score.json"
    parent_score_value = load_json(parent_score)
    if str(parent_score_value.get("score_sha256") or "") != EXPECTED["parent_score"]:
        raise DescentError("wrong protected gold-v6 score")
    parent_audio = one_audio(root / "incumbent")
    info = ffprobe_info(parent_audio)
    parent_pcm = canonical_pcm_sha256(
        parent_audio,
        sample_rate=info["sample_rate"],
        channels=info["channels"],
        ffmpeg=ffmpeg,
    )
    if parent_pcm != EXPECTED["parent_pcm"]:
        raise DescentError(f"wrong protected gold-v6 PCM: {parent_pcm}")
    children = {
        "production": load_machine(
            root,
            "gold-v7-production",
            EXPECTED["production_score"],
            EXPECTED["production_pcm"],
            ffmpeg=ffmpeg,
        ),
        "interplay": load_machine(
            root,
            "gold-v7-interplay",
            EXPECTED["interplay_score"],
            EXPECTED["interplay_pcm"],
            ffmpeg=ffmpeg,
        ),
        "arc": load_machine(
            root,
            "gold-v7-arc",
            EXPECTED["arc_score"],
            EXPECTED["arc_pcm"],
            ffmpeg=ffmpeg,
        ),
    }
    for label, child in children.items():
        if child["info"] != info:
            raise DescentError(f"audio format mismatch for {label}")
    return {
        "root": root,
        "owner_receipt": owner_receipt,
        "parent_score": parent_score,
        "parent_score_value": parent_score_value,
        "parent_audio": parent_audio,
        "info": info,
        "children": children,
    }


def source_row(
    source_id: str,
    role: str,
    path: Path,
    pcm_sha: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "role": role,
        "container_sha256": sha256_file(path),
        "canonical_pcm_sha256": pcm_sha,
        "bytes": path.stat().st_size,
    }
