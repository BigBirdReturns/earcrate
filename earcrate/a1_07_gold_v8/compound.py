from __future__ import annotations

from array import array
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .common import (
    EXPECTED,
    DescentError,
    atomic_write_json,
    blend_regions,
    bytes_to_samples,
    canonical_json_bytes,
    compare_exact_region,
    compare_region_offsets,
    decode_s32,
    find_exact_subsequence,
    frame_count,
    samples_to_bytes,
    seal,
    sha256_bytes,
    sha256_file,
    validate_seal,
    write_s32_wav,
)
from .custody import choose_handoff_mask, source_row


def make_plan(
    *,
    plan_id: str,
    title: str,
    sample_rate: int,
    channels: int,
    duration_frames: int,
    sources: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_compound_performance_score",
            "score_id": plan_id,
            "title": title,
            "timeline": {
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_samples": duration_frames,
            },
            "sources": [dict(row) for row in sources],
            "operations": [dict(row) for row in operations],
            "authority": dict(authority),
        },
        "score_sha256",
    )


def render_plan(
    plan: Mapping[str, Any],
    bindings: Mapping[str, Path],
    *,
    ffmpeg: str,
) -> bytes:
    validate_seal(plan, "score_sha256")
    timeline = plan["timeline"]
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    duration = int(timeline["duration_samples"])
    source_meta = {row["source_id"]: row for row in plan["sources"]}
    decoded: dict[str, bytes] = {}
    for source_id, row in source_meta.items():
        path = bindings.get(source_id)
        if path is None or not path.is_file():
            raise DescentError(f"missing binding for {source_id}")
        if sha256_file(path) != row["container_sha256"]:
            raise DescentError(f"container mutation for {source_id}")
        data = decode_s32(
            path,
            sample_rate=sample_rate,
            channels=channels,
            ffmpeg=ffmpeg,
        )
        if sha256_bytes(data) != row["canonical_pcm_sha256"]:
            raise DescentError(f"PCM mutation for {source_id}")
        decoded[source_id] = data

    output = array("i", [0]) * (duration * channels)
    if sys.byteorder != "little":
        output.byteswap()
    for operation in plan["operations"]:
        if operation.get("op") != "replace_with_crossfade":
            raise DescentError(f"unsupported operation {operation.get('op')}")
        source_id = str(operation["source_id"])
        source = bytes_to_samples(decoded[source_id])
        target_start = int(operation["target_start_sample"])
        source_start = int(operation["source_start_sample"])
        frames = int(operation["duration_samples"])
        if target_start < 0 or source_start < 0 or frames <= 0:
            raise DescentError("invalid compound operation")
        if (
            (target_start + frames) * channels > len(output)
            or (source_start + frames) * channels > len(source)
        ):
            raise DescentError("compound operation out of bounds")
        blend_regions(
            output,
            source,
            base_start_frame=target_start,
            replacement_start_frame=source_start,
            frames=frames,
            channels=channels,
            fade_in_frames=int(operation.get("fade_in_samples") or 0),
            fade_out_frames=int(operation.get("fade_out_samples") or 0),
        )
    return samples_to_bytes(output)


def build_plans(
    inputs: Mapping[str, Any],
    *,
    join_ms: float,
    handoff_fade_ms: float,
    ffmpeg: str,
) -> dict[str, Any]:
    info = inputs["info"]
    sample_rate = info["sample_rate"]
    channels = info["channels"]
    parent_path = inputs["parent_audio"]
    production_path = inputs["children"]["production"]["audio"]
    interplay_path = inputs["children"]["interplay"]["audio"]
    arc_path = inputs["children"]["arc"]["audio"]
    parent_bytes = decode_s32(
        parent_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    production_bytes = decode_s32(
        production_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    interplay_bytes = decode_s32(
        interplay_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    arc_bytes = decode_s32(
        arc_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    parent_frames = frame_count(parent_bytes, channels)
    if (
        frame_count(production_bytes, channels) != parent_frames
        or frame_count(interplay_bytes, channels) != parent_frames
    ):
        raise DescentError("v7 core descendants do not match protected core duration")
    arc_frames = frame_count(arc_bytes, channels)
    core_start = find_exact_subsequence(
        arc_bytes,
        parent_bytes,
        frame_bytes=channels * 4,
    )
    core_end = core_start + parent_frames
    if core_end > arc_frames:
        raise DescentError("embedded core exceeds arc")
    available_tail = arc_frames - core_end if arc_frames > core_end else parent_frames // 4
    join = max(
        1,
        min(
            round(sample_rate * join_ms / 1000.0),
            core_start,
            parent_frames // 4,
            available_tail,
        ),
    )
    masks = inputs["children"]["interplay"]["receipt"].get("declared_masks") or []
    selected = choose_handoff_mask(
        masks,
        parent_pcm=parent_bytes,
        interplay_pcm=interplay_bytes,
        channels=channels,
    )
    mask_start = int(selected["start_sample"])
    mask_end = int(selected["end_sample"])
    handoff_fade = max(
        1,
        min(
            round(sample_rate * handoff_fade_ms / 1000.0),
            (mask_end - mask_start) // 3,
        ),
    )

    sources = [
        source_row(
            "v7_arc",
            "locked_quiet_to_crescendo_arc",
            arc_path,
            EXPECTED["arc_pcm"],
        ),
        source_row(
            "v7_production",
            "production_integrated_gold_v6_core",
            production_path,
            EXPECTED["production_pcm"],
        ),
        source_row(
            "v7_interplay",
            "bounded_cross_era_interplay_core",
            interplay_path,
            EXPECTED["interplay_pcm"],
        ),
    ]
    common_authority = {
        "track_id": "A1-07",
        "parent_owner_review_receipt_sha256": EXPECTED["owner_review"],
        "parent_score_sha256": EXPECTED["parent_score"],
        "parent_pcm_sha256": EXPECTED["parent_pcm"],
        "positive_module_score_sha256": EXPECTED["arc_score"],
        "positive_module_pcm_sha256": EXPECTED["arc_pcm"],
        "human_acceptance": False,
        "recovery_open": False,
        "renderer_invented_decisions": False,
    }
    arc_control = make_plan(
        plan_id="a1-07-gold-v8-arc-control",
        title="A1-07 v7 arc positive module control",
        sample_rate=sample_rate,
        channels=channels,
        duration_frames=arc_frames,
        sources=sources,
        operations=[
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_arc",
                "source_start_sample": 0,
                "target_start_sample": 0,
                "duration_samples": arc_frames,
                "fade_in_samples": 0,
                "fade_out_samples": 0,
                "musical_function": "locked_quiet_intro_crescendo_and_gold_v6_core",
            }
        ],
        authority={
            **common_authority,
            "candidate_id": "gold-v8-arc-control",
            "incumbent_control": True,
        },
    )
    production_plan = make_plan(
        plan_id="a1-07-gold-v8-arc-production",
        title="A1-07 v8 locked arc plus production core",
        sample_rate=sample_rate,
        channels=channels,
        duration_frames=arc_frames,
        sources=sources,
        operations=[
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_arc",
                "source_start_sample": 0,
                "target_start_sample": 0,
                "duration_samples": arc_frames,
                "fade_in_samples": 0,
                "fade_out_samples": 0,
                "musical_function": "locked_arc_base",
            },
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_production",
                "source_start_sample": 0,
                "target_start_sample": core_start,
                "duration_samples": parent_frames,
                "fade_in_samples": join,
                "fade_out_samples": join,
                "musical_function": "production_integrated_protected_core",
            },
        ],
        authority={
            **common_authority,
            "candidate_id": "gold-v8-arc-production",
            "intro_exact_before_sample": core_start,
            "core_join_samples": join,
        },
    )
    handoff_plan = make_plan(
        plan_id="a1-07-gold-v8-arc-handoff",
        title="A1-07 v8 locked arc plus production core and one handoff",
        sample_rate=sample_rate,
        channels=channels,
        duration_frames=arc_frames,
        sources=sources,
        operations=[
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_arc",
                "source_start_sample": 0,
                "target_start_sample": 0,
                "duration_samples": arc_frames,
                "fade_in_samples": 0,
                "fade_out_samples": 0,
                "musical_function": "locked_arc_base",
            },
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_production",
                "source_start_sample": 0,
                "target_start_sample": core_start,
                "duration_samples": parent_frames,
                "fade_in_samples": join,
                "fade_out_samples": join,
                "musical_function": "production_integrated_protected_core",
            },
            {
                "op": "replace_with_crossfade",
                "source_id": "v7_interplay",
                "source_start_sample": mask_start,
                "target_start_sample": core_start + mask_start,
                "duration_samples": mask_end - mask_start,
                "fade_in_samples": handoff_fade,
                "fade_out_samples": handoff_fade,
                "musical_function": selected.get("musical_function")
                or "one_bounded_cross_era_handoff",
            },
        ],
        authority={
            **common_authority,
            "candidate_id": "gold-v8-arc-handoff",
            "intro_exact_before_sample": core_start,
            "core_join_samples": join,
            "selected_handoff_mask": selected,
            "handoff_fade_samples": handoff_fade,
        },
    )
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "parent_frames": parent_frames,
        "arc_frames": arc_frames,
        "core_start": core_start,
        "core_end": core_end,
        "join": join,
        "selected_mask": selected,
        "handoff_fade": handoff_fade,
        "parent_bytes": parent_bytes,
        "production_bytes": production_bytes,
        "interplay_bytes": interplay_bytes,
        "arc_bytes": arc_bytes,
        "plans": {
            "arc-control": arc_control,
            "arc-production": production_plan,
            "arc-handoff": handoff_plan,
        },
        "bindings": {
            "v7_arc": arc_path,
            "v7_production": production_path,
            "v7_interplay": interplay_path,
        },
    }


def render_twice(
    plan: Mapping[str, Any],
    bindings: Mapping[str, Path],
    output_dir: Path,
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    root = output_dir.expanduser().absolute()
    if root.exists():
        raise DescentError(f"render directory exists: {root}")
    root.mkdir(parents=True)
    timeline = plan["timeline"]
    results: list[dict[str, Any]] = []
    for label in ("a", "b"):
        pcm = render_plan(plan, bindings, ffmpeg=ffmpeg)
        output = root / f"render-{label}.wav"
        write_s32_wav(
            output,
            pcm,
            sample_rate=int(timeline["sample_rate"]),
            channels=int(timeline["channels"]),
        )
        receipt = seal(
            {
                "schema_version": 1,
                "kind": "earcrate_compound_render_receipt",
                "score_sha256": plan["score_sha256"],
                "output": {
                    "name": output.name,
                    "container_sha256": sha256_file(output),
                    "canonical_pcm_sha256": sha256_bytes(pcm),
                    "bytes": output.stat().st_size,
                    "sample_rate": timeline["sample_rate"],
                    "channels": timeline["channels"],
                    "duration_samples": timeline["duration_samples"],
                },
                "authority": {
                    "human_acceptance": False,
                    "renderer_invented_decisions": False,
                },
            },
            "receipt_sha256",
        )
        atomic_write_json(root / f"render-{label}.receipt.json", receipt)
        results.append(receipt)
    for field in (
        "container_sha256",
        "canonical_pcm_sha256",
        "bytes",
        "sample_rate",
        "channels",
        "duration_samples",
    ):
        if results[0]["output"][field] != results[1]["output"][field]:
            raise DescentError(f"independent compound renders differ: {field}")
    return {
        "score_sha256": plan["score_sha256"],
        "canonical_pcm_sha256": results[0]["output"]["canonical_pcm_sha256"],
        "container_sha256": results[0]["output"]["container_sha256"],
        "reproduction_pair_sha256": sha256_bytes(
            canonical_json_bytes(
                [results[0]["receipt_sha256"], results[1]["receipt_sha256"]]
            )
        ),
        "audio": root / "render-a.wav",
        "receipt_a": root / "render-a.receipt.json",
        "receipt_b": root / "render-b.receipt.json",
    }


def validate_composites(
    bundle: Mapping[str, Any],
    rendered: Mapping[str, Any],
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    sr = bundle["sample_rate"]
    ch = bundle["channels"]
    core_start = bundle["core_start"]
    core_end = bundle["core_end"]
    join = bundle["join"]
    arc = bundle["arc_bytes"]
    production = bundle["production_bytes"]
    selected = bundle["selected_mask"]
    mask_start = int(selected["start_sample"])
    mask_end = int(selected["end_sample"])
    handoff_fade = bundle["handoff_fade"]
    production_out = decode_s32(
        rendered["arc-production"]["audio"],
        sample_rate=sr,
        channels=ch,
        ffmpeg=ffmpeg,
    )
    handoff_out = decode_s32(
        rendered["arc-handoff"]["audio"],
        sample_rate=sr,
        channels=ch,
        ffmpeg=ffmpeg,
    )
    checks = {
        "production_intro_exact": compare_exact_region(
            production_out,
            arc,
            start_frame=0,
            end_frame=max(0, core_start),
            channels=ch,
        ),
        "production_tail_exact": compare_exact_region(
            production_out,
            arc,
            start_frame=core_end,
            end_frame=bundle["arc_frames"],
            channels=ch,
        ),
        "production_core_interior_exact": compare_region_offsets(
            production_out,
            production,
            a_start_frame=core_start + join,
            b_start_frame=join,
            frames=max(0, bundle["parent_frames"] - 2 * join),
            channels=ch,
        ),
        "handoff_intro_exact": compare_exact_region(
            handoff_out,
            arc,
            start_frame=0,
            end_frame=core_start,
            channels=ch,
        ),
        "handoff_tail_exact": compare_exact_region(
            handoff_out,
            arc,
            start_frame=core_end,
            end_frame=bundle["arc_frames"],
            channels=ch,
        ),
    }
    expanded_start = core_start + max(0, mask_start)
    expanded_end = core_start + min(bundle["parent_frames"], mask_end)
    checks["handoff_before_mask_exact"] = compare_exact_region(
        handoff_out,
        production_out,
        start_frame=0,
        end_frame=expanded_start,
        channels=ch,
    )
    checks["handoff_after_mask_exact"] = compare_exact_region(
        handoff_out,
        production_out,
        start_frame=expanded_end,
        end_frame=bundle["arc_frames"],
        channels=ch,
    )
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise DescentError("compound validation failed: " + ", ".join(failed))
    if rendered["arc-control"]["canonical_pcm_sha256"] != EXPECTED["arc_pcm"]:
        raise DescentError("arc control does not reproduce the qualified v7 arc PCM")
    if rendered["arc-production"]["canonical_pcm_sha256"] == EXPECTED["arc_pcm"]:
        raise DescentError("arc-production is PCM-identical to v7 arc")
    if rendered["arc-handoff"]["canonical_pcm_sha256"] in {
        EXPECTED["arc_pcm"],
        rendered["arc-production"]["canonical_pcm_sha256"],
    }:
        raise DescentError("arc-handoff is not a distinct rung")
    return {
        "checks": checks,
        "selected_mask": selected,
        "join_samples": join,
        "handoff_fade_samples": handoff_fade,
    }
