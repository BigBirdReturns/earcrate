from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from earcrate.reader.nervous_system import reader_read_song
from earcrate.reader.personas import reader_persona_prettylights


def reader_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate reader",
        description="Read a recording through distributed waveform-indexed arms and compile an executable SongGenome.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities", help="report the local song-reader authority surface")
    read = sub.add_parser("read", help="compile a waveform into observations, recurrence events, and a diagnostic SongGenome")
    read.add_argument("audio")
    read.add_argument("--output", required=True)
    read.add_argument("--persona", default="remix_prettylights_reader_v2")
    read.add_argument("--start", type=float, default=0.0)
    read.add_argument("--seconds", type=float, default=30.0)
    read.add_argument("--sample-rate", type=int, default=22_050)
    read.add_argument("--no-unique-residual", action="store_true")
    read.add_argument("--overwrite", action="store_true")
    return parser


def reader_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "authority": "waveform-indexed observations, canonical events, recurrence instances, and explicit residuals",
        "arms": ["pulse", "layers", "recurrence", "residual"],
        "persona": reader_persona_prettylights(),
        "offline_after_compile": True,
        "publication_requires_eligible_sources": True,
    }


def reader_main(argv: list[str] | None = None) -> int:
    args = reader_build_parser().parse_args(argv)
    if args.command == "capabilities":
        print(json.dumps(reader_capabilities(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    receipt = reader_read_song(
        Path(args.audio),
        Path(args.output),
        persona=args.persona,
        start_seconds=float(args.start),
        duration_seconds=float(args.seconds),
        sample_rate=int(args.sample_rate),
        include_unique_residual=not bool(args.no_unique_residual),
        overwrite=bool(args.overwrite),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt.get("thesis_ok") else 3


__all__ = ["reader_build_parser", "reader_capabilities", "reader_main"]
