from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from earcrate.midi.arranger import ArrangementError, midi_write_pattern_arrangement
from earcrate.midi.codec import midi_read
from earcrate.midi.model import MidiLedgerError
from earcrate.midi.render import midi_render_file


def arranger_cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m earcrate.midi.arranger_cli",
        description="Generate a deterministic multitrack MIDI arrangement from measured source-bar patterns and write every decision receipt.",
    )
    parser.add_argument("input", help="source MIDI performance used as the pattern corpus")
    parser.add_argument("output", help="generated MIDI path")
    parser.add_argument("--plan", default="", help="decision-ledger JSON; default is <output>.arrangement.json")
    parser.add_argument("--patterns", default="", help="pattern-bank JSON; default is <plan>.patterns.json")
    parser.add_argument("--target-bars", type=int, default=32)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--form", choices=["classic", "double_drop", "long_build"], default="classic")
    parser.add_argument("--bpm", type=float, default=0.0, help="target tempo; 0 preserves the source opening tempo")
    parser.add_argument("--density", type=float, default=1.0, help="layer-density multiplier in (0,2]")
    parser.add_argument("--maximum-layers", type=int, default=6)
    parser.add_argument("--neutral-render", default="", help="optional neutral-tone WAV proof")
    parser.add_argument("--stems-dir", default="", help="optional neutral-render track stems")
    parser.add_argument("--sample-rate", type=int, default=44_100)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    plan = Path(args.plan).expanduser().resolve() if args.plan else Path(str(output) + ".arrangement.json")
    patterns = Path(args.patterns).expanduser().resolve() if args.patterns else None
    try:
        receipt = midi_write_pattern_arrangement(
            midi_read(args.input),
            output,
            plan,
            patterns,
            overwrite=bool(args.overwrite),
            target_bars=args.target_bars,
            seed=args.seed,
            form_variant=args.form,
            target_bpm=args.bpm,
            density=args.density,
            maximum_layers=args.maximum_layers,
        )
        render = None
        if args.neutral_render:
            render = midi_render_file(
                output,
                Path(args.neutral_render),
                stems_dir=Path(args.stems_dir) if args.stems_dir else None,
                sample_rate=args.sample_rate,
                overwrite=bool(args.overwrite),
            )
        print(json.dumps({**receipt, "neutral_render": render}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ArrangementError, MidiLedgerError, OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "type": type(exc).__name__},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(arranger_cli_main())
