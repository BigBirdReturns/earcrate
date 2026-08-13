from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping

from .common import (
    EXPECTED,
    DescentError,
    atomic_write_json,
    current_git_head,
    decode_s32,
    load_json,
    seal,
    sha256_file,
    validate_seal,
    write_s32_wav,
)
from .compound import build_plans, render_twice, validate_composites
from .custody import verify_inputs
from .review import make_blind_lane, seal_review


def build(
    v7_workspace: Path,
    output: Path,
    contract: Mapping[str, Any],
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    validate_seal(contract, "contract_sha256")
    final_root = output.expanduser().absolute()
    if final_root.exists():
        raise DescentError(f"output workspace exists: {final_root}")
    final_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{final_root.name}.", dir=final_root.parent)
    )
    try:
        result = _build_into(
            v7_workspace,
            staging,
            contract,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        os.replace(staging, final_root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    result["workspace"] = str(final_root)
    result["whole_arc_review"] = str(
        final_root / "review" / "whole-arc" / "public"
    )
    result["core_window_review"] = str(
        final_root / "review" / "core-window" / "public"
    )
    return result


def _build_into(
    v7_workspace: Path,
    root: Path,
    contract: Mapping[str, Any],
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    del ffprobe
    inputs = verify_inputs(v7_workspace, ffmpeg=ffmpeg)
    bundle = build_plans(
        inputs,
        join_ms=float(contract["render_policy"]["core_join_ms"]),
        handoff_fade_ms=float(contract["render_policy"]["handoff_fade_ms"]),
        ffmpeg=ffmpeg,
    )
    custody = root / "private-custody"
    custody.mkdir()
    shutil.copyfile(
        inputs["owner_receipt"],
        custody / "owner-review.receipt.json",
    )
    shutil.copyfile(
        inputs["parent_score"],
        custody / "gold-v6.performance-score.json",
    )
    for key, child in inputs["children"].items():
        shutil.copyfile(
            child["score"],
            custody / f"v7-{key}.performance-score.json",
        )
        shutil.copyfile(
            child["receipt_path"],
            custody / f"v7-{key}.machine-receipt.json",
        )

    plans_root = root / "plans"
    plans_root.mkdir()
    for key, plan in bundle["plans"].items():
        atomic_write_json(plans_root / f"{key}.score.json", plan)

    rendered: dict[str, Any] = {}
    renders_root = root / "renders"
    for key, plan in bundle["plans"].items():
        rendered[key] = render_twice(
            plan,
            bundle["bindings"],
            renders_root / key,
            ffmpeg=ffmpeg,
        )
    validation = validate_composites(bundle, rendered, ffmpeg=ffmpeg)
    head = current_git_head(Path(__file__).resolve().parents[2])
    atomic_write_json(
        root / "machine-validation.private.json",
        seal(
            {
                "schema_version": 1,
                "kind": "earcrate_a1_07_gold_v8_machine_validation",
                "contract_sha256": contract["contract_sha256"],
                "exact_branch_head": head,
                "parent_review_receipt_sha256": EXPECTED["owner_review"],
                "parent_score_sha256": EXPECTED["parent_score"],
                "parent_pcm_sha256": EXPECTED["parent_pcm"],
                "positive_arc_score_sha256": EXPECTED["arc_score"],
                "positive_arc_pcm_sha256": EXPECTED["arc_pcm"],
                "core_start_sample": bundle["core_start"],
                "core_end_sample": bundle["core_end"],
                "selected_handoff_mask": bundle["selected_mask"],
                "candidate_scores": {
                    key: bundle["plans"][key]["score_sha256"]
                    for key in bundle["plans"]
                },
                "candidate_pcm": {
                    key: rendered[key]["canonical_pcm_sha256"]
                    for key in rendered
                },
                "reproduction_pairs": {
                    key: rendered[key]["reproduction_pair_sha256"]
                    for key in rendered
                },
                "checks": validation["checks"],
                "authority": {
                    "machine_qualified": True,
                    "human_acceptance": False,
                    "album_master": False,
                    "recovery_open": False,
                },
            },
            "validation_sha256",
        ),
    )

    whole_lane = make_blind_lane(
        root / "review" / "whole-arc",
        {
            "arc-positive-control": rendered["arc-control"]["audio"],
            "arc-plus-production": rendered["arc-production"]["audio"],
            "arc-plus-production-plus-handoff": rendered["arc-handoff"]["audio"],
        },
        dimensions=(
            "intro tension",
            "crescendo payoff",
            "one-band coherence",
            "desire to hear the continuation",
        ),
        target_lufs=float(contract["review_policy"]["target_lufs"]),
        peak_ceiling=float(contract["review_policy"]["peak_ceiling_dbfs"]),
        ffmpeg=ffmpeg,
    )

    core_root = root / "core-windows"
    core_root.mkdir()
    parent_core = core_root / "gold-v6.wav"
    production_core = core_root / "production.wav"
    handoff_core = core_root / "handoff.wav"
    write_s32_wav(
        parent_core,
        bundle["parent_bytes"],
        sample_rate=bundle["sample_rate"],
        channels=bundle["channels"],
    )
    write_s32_wav(
        production_core,
        bundle["production_bytes"],
        sample_rate=bundle["sample_rate"],
        channels=bundle["channels"],
    )
    handoff_full = decode_s32(
        rendered["arc-handoff"]["audio"],
        sample_rate=bundle["sample_rate"],
        channels=bundle["channels"],
        ffmpeg=ffmpeg,
    )
    frame_bytes = bundle["channels"] * 4
    handoff_core_bytes = handoff_full[
        bundle["core_start"] * frame_bytes : bundle["core_end"] * frame_bytes
    ]
    write_s32_wav(
        handoff_core,
        handoff_core_bytes,
        sample_rate=bundle["sample_rate"],
        channels=bundle["channels"],
    )
    core_lane = make_blind_lane(
        root / "review" / "core-window",
        {
            "gold-v6-core": parent_core,
            "production-core": production_core,
            "production-plus-handoff-core": handoff_core,
        },
        dimensions=(
            "vocal integration",
            "groove",
            "handoff usefulness",
            "percussion impact",
            "one-band coherence",
        ),
        target_lufs=float(contract["review_policy"]["target_lufs"]),
        peak_ceiling=float(contract["review_policy"]["peak_ceiling_dbfs"]),
        ffmpeg=ffmpeg,
    )
    (root / "review" / "README.txt").write_text(
        "A1-07 GOLD-V8 REVIEW\n\n"
        "1. Listen to review/whole-arc/public for the quiet intro, crescendo, and payoff.\n"
        "2. Listen to review/core-window/public for production integration and the single handoff.\n"
        "3. Return a natural ranking for each lane. Diagnosis is optional.\n"
        "4. Do not open either private authority map before the ranking is sealed.\n",
        encoding="utf-8",
    )
    projection = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_a1_07_gold_v8_public_projection",
            "contract_sha256": contract["contract_sha256"],
            "exact_branch_head": head,
            "parent_owner_review_receipt_sha256": EXPECTED["owner_review"],
            "positive_feature": "quiet source-led intro into crescendo",
            "whole_arc_assignment_sha256": whole_lane["assignment"][
                "assignment_sha256"
            ],
            "core_window_assignment_sha256": core_lane["assignment"][
                "assignment_sha256"
            ],
            "candidate_scores": {
                key: bundle["plans"][key]["score_sha256"]
                for key in bundle["plans"]
            },
            "candidate_pcm": {
                key: rendered[key]["canonical_pcm_sha256"]
                for key in rendered
            },
            "owner_frontier_created": True,
            "private_material_exported": False,
            "album_master_count": 0,
            "recovery_open": False,
        },
        "projection_sha256",
    )
    atomic_write_json(root / "PUBLIC_PROJECTION.json", projection)
    return {
        "ok": True,
        "kind": "a1_07_gold_v8_review_ready",
        "workspace": str(root),
        "contract_sha256": contract["contract_sha256"],
        "whole_arc_review": str(whole_lane["public"]),
        "core_window_review": str(core_lane["public"]),
        "positive_arc_pcm_sha256": EXPECTED["arc_pcm"],
        "candidate_pcm": projection["candidate_pcm"],
        "selected_handoff_mask": bundle["selected_mask"],
        "public_projection_sha256": projection["projection_sha256"],
    }


def verify_workspace(
    workspace: Path,
    contract: Mapping[str, Any],
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    del ffmpeg
    root = workspace.expanduser().absolute()
    validation = load_json(root / "machine-validation.private.json")
    validate_seal(validation, "validation_sha256")
    if validation.get("contract_sha256") != contract["contract_sha256"]:
        raise DescentError("workspace belongs to another contract")
    projection = load_json(root / "PUBLIC_PROJECTION.json")
    validate_seal(projection, "projection_sha256")
    if validation.get("exact_branch_head") != projection.get("exact_branch_head"):
        raise DescentError("public and private branch-head identities differ")
    for lane in ("whole-arc", "core-window"):
        assignment = load_json(
            root / "review" / lane / "public" / "assignment.json"
        )
        authority = load_json(
            root / "review" / lane / "private" / "authority.json"
        )
        validate_seal(assignment, "assignment_sha256")
        validate_seal(authority, "authority_sha256")
        if authority.get("assignment_sha256") != assignment["assignment_sha256"]:
            raise DescentError(f"review authority mismatch: {lane}")
        for label, row in assignment["options"].items():
            path = root / "review" / lane / "public" / f"{label}.flac"
            if sha256_file(path) != row["sha256"]:
                raise DescentError(f"review option changed: {lane}/{label}")
    return {
        "ok": True,
        "kind": "a1_07_gold_v8_workspace_verification",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": projection["exact_branch_head"],
        "projection_sha256": projection["projection_sha256"],
        "whole_arc_assignment_sha256": projection["whole_arc_assignment_sha256"],
        "core_window_assignment_sha256": projection["core_window_assignment_sha256"],
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Build the A1-07 gold-v8 arc rungs")
    root.add_argument(
        "--contract",
        type=Path,
        default=Path(
            "configs/album_one/a1-07/gold-v8-arc-rungs.v1.json"
        ),
    )
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-contract")
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--v7-workspace", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--ffmpeg", default="ffmpeg")
    build_parser.add_argument("--ffprobe", default="ffprobe")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--workspace", type=Path, required=True)
    verify_parser.add_argument("--ffmpeg", default="ffmpeg")
    review_parser = sub.add_parser("review")
    review_parser.add_argument("--workspace", type=Path, required=True)
    review_parser.add_argument("--whole-ranking", required=True)
    review_parser.add_argument("--core-ranking", required=True)
    review_parser.add_argument("--note", default="")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        contract = load_json(args.contract)
        validate_seal(contract, "contract_sha256")
        if (
            contract.get("kind") != "earcrate_track_descent_contract"
            or contract.get("track_id") != "A1-07"
        ):
            raise DescentError("wrong A1-07 descent contract")
        if args.command == "verify-contract":
            result = {
                "ok": True,
                "contract_sha256": contract["contract_sha256"],
                "rungs": [row["candidate_id"] for row in contract["rungs"]],
            }
        elif args.command == "build":
            result = build(
                args.v7_workspace,
                args.output,
                contract,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
        elif args.command == "verify":
            result = verify_workspace(
                args.workspace,
                contract,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "review":
            result = seal_review(
                args.workspace,
                whole_ranking=args.whole_ranking,
                core_ranking=args.core_ranking,
                note=args.note,
            )
        else:
            raise DescentError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (
        DescentError,
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
