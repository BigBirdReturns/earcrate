"""Command line for the A1-07 delivery master.

`plan` measures and solves without writing audio, so the refusal paths can be
exercised before anything is rendered. `master` runs the whole close-out: refuse a
clipped source, solve the gain, render twice, verify the ceiling and the section
invariance, then seal a private manifest and a body-free public receipt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from ..a1_07_full_form.contract import contract_path, load_contract
from ..a1_07_gold_v8 import common as c
from . import chain
from .provenance import master_tree_digest
from .receipt import MasterReceiptError, build_manifest, build_public_projection, \
    load_monitoring_verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTED_CANDIDATE_ID = "full-form-v1-native-pocket"


def _sections(contract: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {str(row["section_id"]): (float(row["start_seconds"]), float(row["end_seconds"]))
            for row in contract["form"]["sections"]}


def _ffmpeg_version(ffmpeg: str) -> str:
    result = c.run([ffmpeg, "-version"], timeout=120)
    return result.stdout.splitlines()[0] if result.stdout else ""


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="earcrate-a1-07-master",
        description="Master the owner-ratified A1-07 production render")
    sub = ap.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="measure and solve the gain without rendering")
    plan.add_argument("--source", type=Path, required=True)
    plan.add_argument("--ceiling-dbtp", type=float, default=chain.CEILING_DBTP)
    plan.add_argument("--target-lufs", type=float, default=None,
                      help="optional loudness target; refuses if it would require limiting")
    plan.add_argument("--ffmpeg", default="ffmpeg")
    plan.add_argument("--ffprobe", default="ffprobe")

    run = sub.add_parser("master", help="render, verify and seal the delivery master")
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--frontier-manifest", type=Path, required=True)
    run.add_argument("--verdict", type=Path, required=True,
                     help="the private monitoring-room ratification receipt")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--public-out", type=Path, default=None,
                     help="where to also write the body-free public receipt")
    run.add_argument("--contract", type=Path, default=contract_path(REPO_ROOT))
    run.add_argument("--candidate-id", default=ACCEPTED_CANDIDATE_ID)
    run.add_argument("--ceiling-dbtp", type=float, default=chain.CEILING_DBTP)
    run.add_argument("--target-lufs", type=float, default=None)
    run.add_argument("--ffmpeg", default="ffmpeg")
    run.add_argument("--ffprobe", default="ffprobe")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        source = args.source.expanduser().absolute()

        if args.command == "plan":
            # Probe rather than assume: a plan run may be pointed at any candidate.
            info = c.ffprobe_info(source, args.ffprobe)
            conditions = chain.refuse_if_source_is_clipped(
                source, sample_rate=info["sample_rate"], channels=info["channels"],
                ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
            plan = chain.solve_gain(source, ceiling_dbtp=args.ceiling_dbtp,
                                    target_lufs=args.target_lufs, ffmpeg=args.ffmpeg)
            chain.refuse_if_limiting(plan)
            print(json.dumps({"plan": plan, "source_peak_conditions": conditions},
                             indent=2, sort_keys=True))
            return 0

        contract = load_contract(args.contract)
        output = args.output.expanduser().absolute()
        if output.exists():
            raise chain.MasteringError(f"output exists: {output}")

        frontier_path = args.frontier_manifest.expanduser().absolute()
        frontier = c.load_json(frontier_path)
        c.validate_seal(frontier, "manifest_sha256")
        if frontier.get("contract_sha256") != contract["contract_sha256"]:
            raise MasterReceiptError(
                "the frontier manifest was produced against a different contract")
        accepted = next((row for row in frontier["candidates"]
                         if row["candidate_id"] == args.candidate_id), None)
        if accepted is None:
            raise MasterReceiptError(f"the frontier carries no candidate {args.candidate_id}")

        rate = int(frontier["timeline"]["sample_rate"])
        channels = int(frontier["timeline"]["channels"])

        # The verdict must name this render before a sample is written.
        verdict_path = args.verdict.expanduser().absolute()
        verdict = load_monitoring_verdict(
            verdict_path, accepted_pcm_sha256=accepted["canonical_pcm_sha256"])
        ceiling = float(verdict.get("ceiling_dbtp", args.ceiling_dbtp))

        observed_pcm = c.canonical_pcm_sha256(source, sample_rate=rate, channels=channels,
                                              ffmpeg=args.ffmpeg)
        if observed_pcm != accepted["canonical_pcm_sha256"]:
            raise MasterReceiptError(
                f"the supplied source is not the accepted render: {observed_pcm[:12]} vs "
                f"{accepted['canonical_pcm_sha256'][:12]}")

        conditions = chain.refuse_if_source_is_clipped(
            source, sample_rate=rate, channels=channels,
            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        plan = chain.solve_gain(source, ceiling_dbtp=ceiling,
                                target_lufs=args.target_lufs, ffmpeg=args.ffmpeg)
        chain.refuse_if_limiting(plan)

        output.mkdir(parents=True)
        rendered = chain.render_master_pair(
            source, output, gain_db=float(plan["solved_gain_db"]),
            sample_rate=rate, channels=channels, ffmpeg=args.ffmpeg)
        verification = chain.verify_master(
            Path(rendered["executions"][0]["path"]), source, plan, _sections(contract),
            sample_rate=rate, channels=channels, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)

        if not verification["true_peak_within_ceiling"]:
            raise chain.MasteringError(
                f"the master missed its ceiling: {verification['true_peak_dbtp']} dBTP")
        if verification["hard_clipped"]:
            raise chain.MasteringError("the master is hard-clipped")
        if not verification["macro_dynamics_preserved"]:
            raise chain.MasteringError(
                f"section gain drifted by {verification['max_section_gain_drift_db']} dB; the "
                "transfer was not linear")

        manifest = build_manifest(
            source_render=source,
            frontier_manifest=frontier,
            frontier_manifest_path=frontier_path,
            verdict=verdict,
            verdict_path=verdict_path,
            plan=plan,
            rendered=rendered,
            verification=verification,
            source_conditions=conditions,
            master_tree=master_tree_digest(REPO_ROOT),
            renderer_identity={"ffmpeg_version": _ffmpeg_version(args.ffmpeg)},
            repo_root=REPO_ROOT,
            candidate_id=args.candidate_id,
            sample_rate=rate,
            channels=channels,
        )
        c.atomic_write_json(output / "MASTER_MANIFEST.json", manifest)

        public = build_public_projection(manifest)
        c.atomic_write_json(output / "PUBLIC_MASTER_RECEIPT.json", public)
        if args.public_out is not None:
            c.atomic_write_json(args.public_out.expanduser().absolute(), public)

        print(json.dumps({
            "workspace": str(output),
            "master_manifest_sha256": manifest["master_manifest_sha256"],
            "receipt_sha256": public["receipt_sha256"],
            "gain_db": plan["solved_gain_db"],
            "master_canonical_pcm_sha256": rendered["canonical_pcm_sha256"],
            "master_container_sha256": rendered["container_sha256"],
            "verification": verification,
        }, indent=2, sort_keys=True))
        return 0
    except (chain.MasteringError, MasterReceiptError, c.DescentError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
