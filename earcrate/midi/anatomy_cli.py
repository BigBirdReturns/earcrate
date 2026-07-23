from __future__ import annotations

import argparse
import json
import sys

from earcrate.midi.anatomy import AnatomyError, midi_write_arrangement_anatomy
from earcrate.midi.codec import midi_read
from earcrate.midi.model import MidiLedgerError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m earcrate.midi.anatomy_cli",
        description="Compile bar, layer, section, transition, motif, and event anatomy from an exact MIDI performance.",
    )
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--minimum-section-bars", type=int, default=2)
    parser.add_argument("--maximum-section-bars", type=int, default=16)
    parser.add_argument("--section-penalty", type=float, default=0.22)
    parser.add_argument("--boundary-reward", type=float, default=0.32)
    parser.add_argument("--motif-subdivisions", type=int, default=16)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = midi_write_arrangement_anatomy(
            midi_read(args.input),
            args.output,
            overwrite=args.overwrite,
            minimum_section_bars=args.minimum_section_bars,
            maximum_section_bars=args.maximum_section_bars,
            section_penalty=args.section_penalty,
            boundary_reward=args.boundary_reward,
            motif_subdivisions=args.motif_subdivisions,
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (AnatomyError, MidiLedgerError, OSError, ValueError) as exc:
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
    raise SystemExit(main())
