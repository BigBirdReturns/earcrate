from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from earcrate.mix.model import (
    MixScoreError,
    mixscore_capability,
    mixscore_seal,
    mixscore_write_json,
)
from earcrate.mix.render import mixscore_build_demo, mixscore_render_to_files


def _mixscore_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


def _mixscore_scaffold(
    source_a: str,
    source_b: str,
    output: str,
    *,
    master_bpm: float,
    source_a_bpm: float,
    source_b_bpm: float,
    bars: int,
    sample_rate: int,
) -> dict[str, Any]:
    if bars < 4:
        raise MixScoreError("mix scaffold requires at least four bars")
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_a_path = Path(source_a).expanduser().resolve()
    source_b_path = Path(source_b).expanduser().resolve()
    if not source_a_path.is_file() or not source_b_path.is_file():
        raise MixScoreError("mix scaffold sources must both exist")
    beats_per_bar = 4
    end_beat = float(bars * beats_per_bar)
    entry_beat = float(beats_per_bar)
    blend_end = float(min(end_beat, beats_per_bar * 3))
    score = mixscore_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_mix_score",
            "title": f"{source_a_path.stem} x {source_b_path.stem}",
            "clock": {
                "bpm": float(master_bpm),
                "beats_per_bar": beats_per_bar,
                "sample_rate": int(sample_rate),
            },
            "end_beat": end_beat,
            "peak_ceiling": 0.92,
            "master_gain_db": -2.0,
            "assets": [
                {
                    "asset_id": "source-a",
                    "path": str(source_a_path),
                    "source_bpm": float(source_a_bpm),
                    "downbeat_seconds": 0.0,
                    "cues": {"start": 0.0},
                },
                {
                    "asset_id": "source-b",
                    "path": str(source_b_path),
                    "source_bpm": float(source_b_bpm),
                    "downbeat_seconds": 0.0,
                    "cues": {"start": 0.0},
                },
            ],
            "decks": [
                {"deck_id": "A", "crossfader_side": "A", "gain_db": -2.0, "pan": 0.0},
                {"deck_id": "B", "crossfader_side": "B", "gain_db": -2.0, "pan": 0.0},
            ],
            "events": [
                {"at_beat": 0.0, "deck_id": "A", "op": "load", "asset_id": "source-a"},
                {"at_beat": 0.0, "deck_id": "A", "op": "play", "cue": "start", "sync": True},
                {"at_beat": 0.0, "deck_id": "B", "op": "load", "asset_id": "source-b"},
                {"at_beat": 0.0, "op": "set_crossfader", "position": -1.0},
                {"at_beat": entry_beat, "deck_id": "B", "op": "play", "cue": "start", "sync": True},
                {
                    "from_beat": entry_beat,
                    "to_beat": blend_end,
                    "deck_id": "B",
                    "op": "fade",
                    "from_db": -120.0,
                    "to_db": -2.0,
                    "curve": "equal_power",
                },
                {
                    "from_beat": entry_beat,
                    "to_beat": blend_end,
                    "op": "crossfade",
                    "from_position": -1.0,
                    "to_position": 0.0,
                    "curve": "s_curve",
                },
            ],
            "metadata": {
                "generated_by": "earcrate mix scaffold",
                "next_edits": [
                    "set downbeat_seconds and cue source beats",
                    "add jump, loop, cut, and additional fade events",
                    "bind expected_file_sha256 by rendering once",
                ],
            },
        }
    )
    mixscore_write_json(output_path, score)
    return {
        "ok": True,
        "score_path": str(output_path),
        "score_sha256": str(score["score_sha256"]),
        "end_beat": end_beat,
        "events": len(score["events"]),
    }


def mixscore_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate mix",
        description="Render independent source transports from an EarCrate MixScore",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capability", help="print the MixScore execution contract")

    render_parser = subparsers.add_parser("render", help="render a MixScore to master WAV, stems, and receipts")
    render_parser.add_argument("score", help="MixScore JSON path")
    render_parser.add_argument("output", help="output master WAV path")
    render_parser.add_argument("--stems-dir", default=None, help="optional output directory for per-deck stems")

    demo_parser = subparsers.add_parser("demo", help="generate and render a self-contained two-deck proof")
    demo_parser.add_argument("output_dir", help="directory for synthetic sources, score, audio, stems, and receipts")
    demo_parser.add_argument("--sample-rate", type=int, default=24_000)

    scaffold_parser = subparsers.add_parser("scaffold", help="write a two-source MixScore starting point")
    scaffold_parser.add_argument("source_a")
    scaffold_parser.add_argument("source_b")
    scaffold_parser.add_argument("output")
    scaffold_parser.add_argument("--bpm", type=float, required=True, dest="master_bpm")
    scaffold_parser.add_argument("--source-a-bpm", type=float, required=True)
    scaffold_parser.add_argument("--source-b-bpm", type=float, required=True)
    scaffold_parser.add_argument("--bars", type=int, default=16)
    scaffold_parser.add_argument("--sample-rate", type=int, default=48_000)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "capability":
            _mixscore_print({"ok": True, **mixscore_capability()})
        elif args.command == "render":
            _mixscore_print(
                mixscore_render_to_files(
                    args.score,
                    args.output,
                    stems_dir=args.stems_dir,
                )
            )
        elif args.command == "demo":
            _mixscore_print(mixscore_build_demo(args.output_dir, sample_rate=int(args.sample_rate)))
        elif args.command == "scaffold":
            _mixscore_print(
                _mixscore_scaffold(
                    args.source_a,
                    args.source_b,
                    args.output,
                    master_bpm=float(args.master_bpm),
                    source_a_bpm=float(args.source_a_bpm),
                    source_b_bpm=float(args.source_b_bpm),
                    bars=int(args.bars),
                    sample_rate=int(args.sample_rate),
                )
            )
        else:
            raise MixScoreError(f"unsupported mix command: {args.command}")
        return 0
    except Exception as exc:
        _mixscore_print({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(mixscore_cli_main())
