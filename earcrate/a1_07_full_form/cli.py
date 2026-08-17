"""Command line for the A1-07 full-form descent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from ..a1_07_gold_v8 import common as c
from .build import ADAPTER_ID, ADAPTER_VERSION, build
from .contract import FullFormError, contract_path, load_contract

REPO_ROOT = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="earcrate-a1-07-full-form",
        description="Build the A1-07 full-form frontier from the qualified gold-v7 workspace")
    sub = ap.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show-contract", help="validate and print the descent contract")
    show.add_argument("--contract", type=Path, default=contract_path(REPO_ROOT))

    plan = sub.add_parser("plan", help="derive candidate scores without rendering")
    plan.add_argument("--contract", type=Path, default=contract_path(REPO_ROOT))
    plan.add_argument("--v7-workspace", type=Path, required=True)

    run = sub.add_parser("build", help="render the frontier and seal its evidence package")
    run.add_argument("--contract", type=Path, default=contract_path(REPO_ROOT))
    run.add_argument("--v7-workspace", type=Path, required=True)
    run.add_argument("--core-archive", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--ffmpeg", default="ffmpeg")
    run.add_argument("--ffprobe", default="ffprobe")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        contract = load_contract(args.contract)
        if args.command == "show-contract":
            print(json.dumps({
                "adapter_id": ADAPTER_ID,
                "adapter_version": ADAPTER_VERSION,
                "descent_id": contract["descent_id"],
                "contract_sha256": contract["contract_sha256"],
                "form": contract["form"],
                "timing_laws": [row["candidate_id"] for row in contract["timing_laws"]],
                "machine_gate": contract["machine_gate"],
            }, indent=2, sort_keys=True))
            return 0

        if args.command == "plan":
            from .score import build_full_form_score
            arc = c.load_json(args.v7_workspace / "gold-v7-arc" / "authoring" / "derived"
                              / "performance-score.json")
            rows = []
            for row in contract["timing_laws"]:
                child, facts = build_full_form_score(
                    arc, contract, candidate_id=str(row["candidate_id"]))
                rows.append({
                    "candidate_id": row["candidate_id"],
                    "label": row["label"],
                    "score_sha256": child["score_sha256"],
                    "timing_facts": facts,
                })
            print(json.dumps({"candidates": rows}, indent=2, sort_keys=True))
            return 0

        result = build(
            args.v7_workspace.expanduser().absolute(),
            args.core_archive.expanduser().absolute(),
            args.output.expanduser().absolute(),
            contract,
            REPO_ROOT,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
        )
        summary = {
            "workspace": result["workspace"],
            "manifest_sha256": result["manifest_sha256"],
            "projection_sha256": result["projection_sha256"],
            "machine_gate": result["machine_gate"],
            "review_pack": result["review_pack"],
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if result["machine_gate"]["frontier_admissible"] else 2
    except (FullFormError, c.DescentError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
