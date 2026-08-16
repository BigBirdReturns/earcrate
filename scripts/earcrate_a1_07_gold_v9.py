#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate import reference_zero as rz
from earcrate.a1_07_gold_v8 import common as c
from earcrate.a1_07_gold_v8 import custody as v8custody
from earcrate.a1_07_gold_v8.review import level_match

CONTRACT = ROOT / "configs" / "album_one" / "a1-07" / "gold-v9-timing-laws.v1.json"
LAW_ORDER = ("gold-v9-single-speed", "gold-v9-native-pocket", "gold-v9-phrase-reset")
REVIEW_LABELS = {
    "gold-v9-single-speed": "A",
    "gold-v9-native-pocket": "B",
    "gold-v9-phrase-reset": "C",
}


class TimingLawError(RuntimeError):
    pass


def load_contract(path: Path = CONTRACT) -> dict[str, Any]:
    value = c.load_json(path)
    if value.get("kind") != "earcrate_track_descent_contract":
        raise TimingLawError("wrong contract kind")
    if value.get("track_id") != "A1-07":
        raise TimingLawError("v9 runner is restricted to A1-07")
    c.validate_seal(value, "contract_sha256")
    ids = tuple(str(row.get("candidate_id") or "") for row in value.get("timing_laws") or [])
    if ids != LAW_ORDER:
        raise TimingLawError("timing law order does not match the v9 contract")
    if bool((value.get("review_policy") or {}).get("roles_withheld", True)):
        raise TimingLawError("v9 review must disclose cut differences")
    return value


def is_frankie_track(track: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> bool:
    header = " ".join(str(track.get(key) or "") for key in ("track_id", "role", "ownership")).lower()
    if "frankie" in header or "lead_vocal" in header or "lead-vocal" in header:
        return True
    for clip in track.get("clips") or []:
        source = sources.get(str(clip.get("source_id") or ""), {})
        text = f"{source.get('source_id', '')} {source.get('role', '')}".lower()
        if "four_seasons" in text and "vocal" in text:
            return True
    return False


def transformed_duration(clip: Mapping[str, Any], scale: float | None = None) -> int:
    source_duration = int(clip["source_end_sample"]) - int(clip["source_start_sample"])
    tempo = float(scale if scale is not None else clip.get("tempo_scale", 1.0))
    if tempo <= 0:
        raise TimingLawError("tempo scale must be positive")
    return max(1, round(source_duration / tempo))


def parent_partition(score: Mapping[str, Any]) -> dict[str, Any]:
    rz.validate_performance_score(score)
    sources = {str(row["source_id"]): dict(row) for row in score.get("sources") or []}
    frankie: list[dict[str, Any]] = []
    band: list[dict[str, Any]] = []
    for track in score.get("tracks") or []:
        row = deepcopy(dict(track))
        if is_frankie_track(row, sources):
            frankie.append(row)
        else:
            band.append(row)
    if not frankie or not band:
        raise TimingLawError("parent score must contain Frankie and donor-band tracks")
    return {"sources": sources, "frankie": frankie, "band": band}


def derive_slots(score: Mapping[str, Any]) -> dict[str, Any]:
    partition = parent_partition(score)
    slot_sets: list[tuple[int, ...]] = []
    by_track: dict[str, dict[int, dict[str, Any]]] = {}
    for track in partition["band"]:
        clips = [deepcopy(dict(row)) for row in track.get("clips") or []]
        if not clips:
            raise TimingLawError(f"band track has no clips: {track.get('track_id')}")
        mapping: dict[int, dict[str, Any]] = {}
        for clip in clips:
            target = int(clip["target_start_sample"])
            if target in mapping:
                raise TimingLawError(f"band track has multiple clips at target {target}: {track.get('track_id')}")
            mapping[target] = clip
        slots = tuple(sorted(mapping))
        slot_sets.append(slots)
        by_track[str(track["track_id"])] = mapping
    if len(set(slot_sets)) != 1:
        raise TimingLawError("donor-band tracks do not share the same target slot set")
    slots = slot_sets[0]
    if len(slots) < 2:
        raise TimingLawError("at least two donor-band slots are required")

    reference_track = partition["band"][0]
    reference = by_track[str(reference_track["track_id"])]
    source_durations: dict[int, int] = {}
    original_ends: dict[int, int] = {}
    for slot in slots:
        representative = reference[slot]
        source_duration = int(representative["source_end_sample"]) - int(representative["source_start_sample"])
        if source_duration <= 0:
            raise TimingLawError("non-positive donor source duration")
        source_durations[slot] = source_duration
        original_ends[slot] = slot + transformed_duration(representative)
        for track_id, mapping in by_track.items():
            candidate_duration = int(mapping[slot]["source_end_sample"]) - int(mapping[slot]["source_start_sample"])
            if candidate_duration != source_duration:
                raise TimingLawError(f"donor-band source durations disagree at slot {slot}: {track_id}")

    first = slots[0]
    final_end = max(original_ends.values())
    span = final_end - first
    if span <= 0:
        raise TimingLawError("invalid parent donor-band span")
    return {
        **partition,
        "slots": slots,
        "by_track": by_track,
        "source_durations": source_durations,
        "first": first,
        "final_end": final_end,
        "span": span,
    }


def _reference_clip(info: Mapping[str, Any], slot: int) -> Mapping[str, Any]:
    first_track = next(iter(info["by_track"]))
    return info["by_track"][first_track][slot]


def single_speed_schedule(info: Mapping[str, Any]) -> tuple[dict[int, tuple[int, float]], dict[str, Any]]:
    slots = list(info["slots"])
    total_source = sum(int(info["source_durations"][slot]) for slot in slots)
    scale = total_source / int(info["span"])
    target = int(info["first"])
    schedule: dict[int, tuple[int, float]] = {}
    for slot in slots:
        schedule[slot] = (target, scale)
        target += transformed_duration(_reference_clip(info, slot), scale)
    return schedule, {
        "tempo_scales": [scale],
        "phase_resets": [],
        "final_band_end_sample": target,
        "law": "single-speed",
    }


def native_schedule(info: Mapping[str, Any]) -> tuple[dict[int, tuple[int, float]], dict[str, Any]]:
    target = int(info["first"])
    schedule: dict[int, tuple[int, float]] = {}
    for slot in info["slots"]:
        schedule[int(slot)] = (target, 1.0)
        target += int(info["source_durations"][slot])
    return schedule, {
        "tempo_scales": [1.0],
        "phase_resets": [],
        "final_band_end_sample": target,
        "drift_vs_parent_end_samples": target - int(info["final_end"]),
        "law": "native-pocket",
    }


def phrase_schedule(info: Mapping[str, Any], phrase_slots: int = 4) -> tuple[dict[int, tuple[int, float]], dict[str, Any]]:
    slots = list(info["slots"])
    schedule: dict[int, tuple[int, float]] = {}
    scales: list[float] = []
    resets: list[int] = []
    for index in range(0, len(slots), phrase_slots):
        phrase = slots[index : index + phrase_slots]
        phrase_start = int(phrase[0])
        phrase_end = int(slots[index + phrase_slots]) if index + phrase_slots < len(slots) else int(info["final_end"])
        target_span = phrase_end - phrase_start
        total_source = sum(int(info["source_durations"][slot]) for slot in phrase)
        if target_span <= 0 or total_source <= 0:
            raise TimingLawError("invalid phrase span")
        scale = total_source / target_span
        scales.append(scale)
        if index:
            resets.append(phrase_start)
        target = phrase_start
        for slot in phrase:
            schedule[int(slot)] = (target, scale)
            target += transformed_duration(_reference_clip(info, slot), scale)
    return schedule, {
        "tempo_scales": scales,
        "phase_resets": resets,
        "phrase_slots": phrase_slots,
        "law": "phrase-reset",
    }


def schedule_for_law(law_id: str, info: Mapping[str, Any]) -> tuple[dict[int, tuple[int, float]], dict[str, Any]]:
    if law_id == "gold-v9-single-speed":
        return single_speed_schedule(info)
    if law_id == "gold-v9-native-pocket":
        return native_schedule(info)
    if law_id == "gold-v9-phrase-reset":
        return phrase_schedule(info)
    raise TimingLawError(f"unsupported timing law: {law_id}")


def build_child_score(parent_score: Mapping[str, Any], *, law_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    info = derive_slots(parent_score)
    schedule, facts = schedule_for_law(law_id, info)
    child = deepcopy(dict(parent_score))
    child.pop("score_sha256", None)
    sources = {str(row["source_id"]): dict(row) for row in child["sources"]}

    parent_frankie = [deepcopy(dict(track)) for track in parent_score["tracks"] if is_frankie_track(track, sources)]
    for track in child["tracks"]:
        if is_frankie_track(track, sources):
            continue
        for clip in track.get("clips") or []:
            original_target = int(clip["target_start_sample"])
            if original_target not in schedule:
                raise TimingLawError(f"band clip target is absent from shared schedule: {original_target}")
            new_target, new_scale = schedule[original_target]
            clip["target_start_sample"] = int(new_target)
            clip["tempo_scale"] = float(new_scale)

    child_frankie = [deepcopy(dict(track)) for track in child["tracks"] if is_frankie_track(track, sources)]
    if c.canonical_json_bytes(parent_frankie) != c.canonical_json_bytes(child_frankie):
        raise TimingLawError("Frankie track rows changed while deriving a timing law")

    max_end = int(child["timeline"]["duration_samples"])
    for track in child["tracks"]:
        for clip in track.get("clips") or []:
            max_end = max(max_end, int(clip["target_start_sample"]) + transformed_duration(clip))
    child["timeline"]["duration_samples"] = max_end
    child["score_id"] = f"{parent_score.get('score_id', 'gold-v6')}-{law_id}"
    child["title"] = f"{parent_score.get('title', 'A1-07')} {law_id}"
    authority = dict(child.get("authority") or {})
    authority.update({
        "parent_score_sha256": parent_score["score_sha256"],
        "timing_law": law_id,
        "musical_acceptance": False,
        "renderer_invented_decisions": False,
        "frankie_timing_changed": False,
    })
    child["authority"] = authority
    history = [deepcopy(dict(row)) for row in child.get("command_history") or []]
    next_sequence = max((int(row.get("sequence") or 0) for row in history), default=0) + 1
    history.append({
        "sequence": next_sequence,
        "command_id": f"{law_id}-derive",
        "actor": "earcrate:a1-07-gold-v9",
        "operation": "replace_band_timing_law",
        "target": "donor-band",
        "parameters_sha256": c.sha256_bytes(c.canonical_json_bytes({
            "law_id": law_id,
            "schedule": {
                str(slot): {"target_start_sample": target, "tempo_scale": scale}
                for slot, (target, scale) in sorted(schedule.items())
            },
        })),
    })
    child["command_history"] = history
    child = rz.seal(child)
    rz.validate_performance_score(child)
    return child, facts


def child_bindings(parent_bindings: Mapping[str, Any], child_score: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(parent_bindings))
    value.pop("bindings_sha256", None)
    value["score_sha256"] = child_score["score_sha256"]
    result = rz.seal(value)
    rz.validate_source_bindings(result, child_score)
    return result


def render_core_twice(
    score: Mapping[str, Any],
    bindings: Mapping[str, Any],
    output: Path,
    *,
    parent_frames: int,
    sample_rate: int,
    channels: int,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    output.mkdir(parents=True)
    receipts: list[dict[str, Any]] = []
    audios: list[Path] = []
    for suffix in ("a", "b"):
        audio = output / f"render-{suffix}.wav"
        receipt_path = output / f"render-{suffix}.receipt.json"
        receipt = rz.render_performance_score(
            score,
            bindings,
            output_path=audio,
            receipt_path=receipt_path,
            verify_source_pcm=True,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        receipts.append(receipt)
        audios.append(audio)
    pcm_ids = [row["output"]["canonical_pcm_sha256"] for row in receipts]
    if len(set(pcm_ids)) != 1:
        raise TimingLawError("independent timing-law renders disagree on canonical PCM")
    decoded = c.decode_s32(audios[0], sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
    frame_bytes = channels * 4
    needed = parent_frames * frame_bytes
    if len(decoded) < needed:
        decoded += b"\x00" * (needed - len(decoded))
    core = decoded[:needed]
    core_path = output / "qualified-core.wav"
    c.write_s32_wav(core_path, core, sample_rate=sample_rate, channels=channels)
    return {
        "audio": core_path,
        "pcm": core,
        "canonical_pcm_sha256": c.sha256_bytes(core),
        "score_sha256": score["score_sha256"],
        "render_receipt_sha256": [row["receipt_sha256"] for row in receipts],
    }


def splice_into_arc(
    arc_pcm: bytes,
    core_pcm: bytes,
    *,
    core_start: int,
    core_frames: int,
    channels: int,
    fade_frames: int,
) -> bytes:
    base = c.bytes_to_samples(arc_pcm)
    replacement = c.bytes_to_samples(core_pcm)
    c.blend_regions(
        base,
        replacement,
        base_start_frame=core_start,
        replacement_start_frame=0,
        frames=core_frames,
        channels=channels,
        fade_in_frames=fade_frames,
        fade_out_frames=fade_frames,
    )
    return c.samples_to_bytes(base)


def write_cut_notes(destination: Path, rows: Sequence[Mapping[str, Any]], *, common_note: str) -> None:
    lines = [
        "# A1-07 GOLD-V9 CUT NOTES",
        "",
        "This review is intentionally transparent. You are not being asked to guess what changed.",
        "",
        common_note,
        "",
    ]
    for row in rows:
        lines.extend([
            f"## {row['filename']}",
            "",
            f"**Timing law:** {row['label']}",
            "",
            f"**What changed:** {row['mechanism']}",
            "",
            f"**Why it exists:** {row['why']}",
            "",
            f"**Observed timing facts:** `{json.dumps(row['facts'], sort_keys=True)}`",
            "",
            "**What stayed the same:** exact Frankie score rows; exact source selections, pitch choices, gains, pans, and fades; exact quiet v7 arc intro and tail outside the bounded core joins.",
            "",
        ])
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(
    v7_workspace: Path,
    output: Path,
    contract: Mapping[str, Any],
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    c.validate_seal(contract, "contract_sha256")
    final_root = output.expanduser().absolute()
    if final_root.exists():
        raise TimingLawError(f"output exists: {final_root}")
    final_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{final_root.name}.", dir=final_root.parent))
    try:
        result = _build_into(v7_workspace, staging, contract, ffmpeg=ffmpeg, ffprobe=ffprobe)
        os.replace(staging, final_root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    result["workspace"] = str(final_root)
    result["review"] = str(final_root / "review")
    return result


def _build_into(
    v7_workspace: Path,
    root: Path,
    contract: Mapping[str, Any],
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    inputs = v8custody.verify_inputs(v7_workspace, ffmpeg=ffmpeg)
    parent_score = c.load_json(inputs["parent_score"])
    if parent_score.get("score_sha256") != contract["parent"]["gold_v6_score_sha256"]:
        raise TimingLawError("wrong gold-v6 score for v9")
    parent_bindings_path = inputs["root"] / "incumbent" / "source-bindings.private.json"
    parent_bindings = c.load_json(parent_bindings_path)
    rz.validate_source_bindings(parent_bindings, parent_score)

    sample_rate = int(inputs["info"]["sample_rate"])
    channels = int(inputs["info"]["channels"])
    parent_pcm = c.decode_s32(inputs["parent_audio"], sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
    parent_frames = c.frame_count(parent_pcm, channels)

    arc_audio = inputs["children"]["arc"]["audio"]
    arc_pcm = c.decode_s32(arc_audio, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
    frame_bytes = channels * 4
    core_start = c.find_exact_subsequence(arc_pcm, parent_pcm, frame_bytes=frame_bytes)
    core_end = core_start + parent_frames
    fade_frames = max(1, round(float(contract["construction"]["core_join_ms"]) * sample_rate / 1000.0))

    private = root / "private"
    private.mkdir()
    shutil.copyfile(inputs["parent_score"], private / "gold-v6.performance-score.json")
    shutil.copyfile(parent_bindings_path, private / "source-bindings.private.json")
    shutil.copyfile(arc_audio, private / f"gold-v7-arc{arc_audio.suffix.lower()}")

    review = root / "review"
    review.mkdir()
    cut_rows: list[dict[str, Any]] = []
    candidate_pcm_ids: dict[str, str] = {}
    machine_rows: dict[str, Any] = {}
    qualified = 0
    laws_by_id = {str(row["candidate_id"]): dict(row) for row in contract.get("timing_laws") or []}

    for law_id in LAW_ORDER:
        law = laws_by_id[law_id]
        law_root = root / "candidates" / law_id
        law_root.mkdir(parents=True)
        try:
            score, facts = build_child_score(parent_score, law_id=law_id)
            bindings = child_bindings(parent_bindings, score)
            c.atomic_write_json(law_root / "performance-score.json", score)
            c.atomic_write_json(law_root / "source-bindings.private.json", bindings)
            core = render_core_twice(
                score,
                bindings,
                law_root / "reproduction",
                parent_frames=parent_frames,
                sample_rate=sample_rate,
                channels=channels,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
            whole_pcm = splice_into_arc(
                arc_pcm,
                core["pcm"],
                core_start=core_start,
                core_frames=parent_frames,
                channels=channels,
                fade_frames=fade_frames,
            )
            whole_path = law_root / "whole-arc.wav"
            c.write_s32_wav(whole_path, whole_pcm, sample_rate=sample_rate, channels=channels)

            if whole_pcm[: core_start * frame_bytes] != arc_pcm[: core_start * frame_bytes]:
                raise TimingLawError("arc intro changed outside the core")
            if whole_pcm[core_end * frame_bytes :] != arc_pcm[core_end * frame_bytes :]:
                raise TimingLawError("arc tail changed outside the core")

            label = REVIEW_LABELS[law_id]
            semantic = str(law["label"])
            review_name = f"{label}_{semantic}.flac"
            metrics = level_match(
                whole_path,
                review / review_name,
                target_lufs=float(contract["construction"]["review_target_lufs"]),
                peak_ceiling=float(contract["construction"]["review_peak_ceiling_dbfs"]),
                ffmpeg=ffmpeg,
            )
            candidate_pcm_ids[law_id] = core["canonical_pcm_sha256"]
            machine_rows[law_id] = {
                "state": "qualified",
                "score_sha256": score["score_sha256"],
                "core_pcm_sha256": core["canonical_pcm_sha256"],
                "whole_arc_pcm_sha256": c.sha256_bytes(whole_pcm),
                "render_receipt_sha256": core["render_receipt_sha256"],
                "timing_facts": facts,
                "review_sha256": metrics["sha256"],
            }
            qualified += 1
            cut_rows.append({
                "filename": review_name,
                "label": semantic,
                "mechanism": law["mechanism"],
                "why": law["why"],
                "facts": facts,
            })
        except Exception as exc:
            machine_rows[law_id] = {"state": "failed", "reason": str(exc)}

    if qualified < 2:
        raise TimingLawError(f"v9 requires at least two qualified timing laws, got {qualified}")
    if len(candidate_pcm_ids) != len(set(candidate_pcm_ids.values())):
        raise TimingLawError("qualified timing laws produced duplicate core PCM")

    write_cut_notes(
        review / "CUT_NOTES.md",
        cut_rows,
        common_note=(
            "All cuts preserve the same exact Frankie score rows and the same quiet-to-crescendo v7 arc shell. "
            "The only intentional musical variable is how the donor band handles time."
        ),
    )
    assignment = c.seal({
        "schema_version": 1,
        "kind": "earcrate_a1_07_gold_v9_transparent_review",
        "roles_withheld": False,
        "options": {
            row["filename"]: {
                "timing_law": row["label"],
                "mechanism": row["mechanism"],
                "why": row["why"],
                "timing_facts": row["facts"],
                "sha256": c.sha256_file(review / row["filename"]),
            }
            for row in cut_rows
        },
        "choices": [*[row["filename"] for row in cut_rows], "tie", "reject_all"],
        "owner_diagnosis_required": False,
    }, "assignment_sha256")
    c.atomic_write_json(review / "assignment.json", assignment)

    head = c.current_git_head(ROOT)
    machine = c.seal({
        "schema_version": 1,
        "kind": "earcrate_a1_07_gold_v9_machine_validation",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": head,
        "parent_score_sha256": parent_score["score_sha256"],
        "parent_pcm_sha256": contract["parent"]["gold_v6_pcm_sha256"],
        "positive_arc_pcm_sha256": contract["parent"]["gold_v7_arc_pcm_sha256"],
        "core_start_sample": core_start,
        "core_end_sample": core_end,
        "candidate_states": machine_rows,
        "qualified_count": qualified,
        "review_assignment_sha256": assignment["assignment_sha256"],
        "authority": {"human_acceptance": False, "album_master": False, "recovery_open": False},
    }, "validation_sha256")
    c.atomic_write_json(root / "machine-validation.private.json", machine)
    projection = c.seal({
        "schema_version": 1,
        "kind": "earcrate_a1_07_gold_v9_public_projection",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": head,
        "owner_signal": "v8 timing law dominated the audible differences",
        "qualified_timing_laws": [law_id for law_id, row in machine_rows.items() if row["state"] == "qualified"],
        "review_assignment_sha256": assignment["assignment_sha256"],
        "cut_notes_public": True,
        "roles_withheld": False,
        "private_material_exported": False,
        "album_master_count": 0,
        "recovery_open": False,
    }, "projection_sha256")
    c.atomic_write_json(root / "PUBLIC_PROJECTION.json", projection)
    return {
        "ok": True,
        "kind": "a1_07_gold_v9_review_ready",
        "contract_sha256": contract["contract_sha256"],
        "qualified_timing_laws": projection["qualified_timing_laws"],
        "review_assignment_sha256": assignment["assignment_sha256"],
        "projection_sha256": projection["projection_sha256"],
    }


def verify(workspace: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    root = workspace.expanduser().absolute()
    machine = c.load_json(root / "machine-validation.private.json")
    projection = c.load_json(root / "PUBLIC_PROJECTION.json")
    assignment = c.load_json(root / "review" / "assignment.json")
    c.validate_seal(machine, "validation_sha256")
    c.validate_seal(projection, "projection_sha256")
    c.validate_seal(assignment, "assignment_sha256")
    if machine["contract_sha256"] != contract["contract_sha256"]:
        raise TimingLawError("workspace belongs to another v9 contract")
    if projection["review_assignment_sha256"] != assignment["assignment_sha256"]:
        raise TimingLawError("projection and review assignment disagree")
    if bool(assignment.get("roles_withheld")):
        raise TimingLawError("v9 review unexpectedly withholds cut roles")
    if not (root / "review" / "CUT_NOTES.md").is_file():
        raise TimingLawError("transparent cut notes are missing")
    for filename, row in assignment["options"].items():
        path = root / "review" / filename
        if c.sha256_file(path) != row["sha256"]:
            raise TimingLawError(f"review audio changed: {filename}")
    return {
        "ok": True,
        "kind": "a1_07_gold_v9_workspace_verification",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": projection["exact_branch_head"],
        "projection_sha256": projection["projection_sha256"],
        "review_assignment_sha256": assignment["assignment_sha256"],
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Build transparent A1-07 v9 timing-law alternatives")
    root.add_argument("--contract", type=Path, default=CONTRACT)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-contract")
    p = sub.add_parser("build")
    p.add_argument("--v7-workspace", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    p = sub.add_parser("verify")
    p.add_argument("--workspace", type=Path, required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.command == "verify-contract":
            result = {
                "ok": True,
                "contract_sha256": contract["contract_sha256"],
                "timing_laws": [row["candidate_id"] for row in contract["timing_laws"]],
                "roles_withheld": contract["review_policy"]["roles_withheld"],
            }
        elif args.command == "build":
            result = build(args.v7_workspace, args.output, contract, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        elif args.command == "verify":
            result = verify(args.workspace, contract)
        else:
            raise TimingLawError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (
        TimingLawError,
        c.DescentError,
        rz.ReferenceZeroError,
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
