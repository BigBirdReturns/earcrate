from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from earcrate.midi.codec import midi_read, midi_roundtrip, midi_write_ledger_json
from earcrate.midi.model import MidiLedgerError, midi_statistics
from earcrate.midi.render import MIDI_RENDER_WAVEFORMS, midi_render_file
from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.notes import BasicPitchNoteTranscriber, NoopNoteTranscriber


def _midi_print(value: Any, *, stream: Any = None) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream or sys.stdout)


def _midi_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate midi",
        description="Exact Standard MIDI File ledger, round-trip verifier, neutral player-piano renderer, and note-observation provider.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="parse MIDI and print a deterministic event/statistics receipt")
    inspect_parser.add_argument("input")
    inspect_parser.add_argument("--full", action="store_true", help="include the complete canonical event ledger")

    ledger_parser = sub.add_parser("ledger", help="write the canonical source-independent event ledger as JSON")
    ledger_parser.add_argument("input")
    ledger_parser.add_argument("output")
    ledger_parser.add_argument("--overwrite", action="store_true")

    roundtrip_parser = sub.add_parser("roundtrip", help="write MIDI and prove semantic event equality after reparsing")
    roundtrip_parser.add_argument("input")
    roundtrip_parser.add_argument("output")
    roundtrip_parser.add_argument("--overwrite", action="store_true")

    render_parser = sub.add_parser("render", help="render every note through deterministic neutral tones")
    render_parser.add_argument("input")
    render_parser.add_argument("output")
    render_parser.add_argument("--stems-dir", default="")
    render_parser.add_argument("--sample-rate", type=int, default=44_100)
    render_parser.add_argument("--waveform", choices=sorted(MIDI_RENDER_WAVEFORMS), default="sine")
    render_parser.add_argument("--pitch-bend-range", type=float, default=2.0, help="semitones represented by full-scale MIDI pitch bend")
    render_parser.add_argument("--max-seconds", type=float, default=0.0, help="truncate only for diagnostics; 0 renders the whole performance")
    render_parser.add_argument("--target-peak", type=float, default=0.92)
    render_parser.add_argument("--overwrite", action="store_true")

    transcribe_parser = sub.add_parser("transcribe", help="measure note observations from one isolated audio stem")
    transcribe_parser.add_argument("input")
    transcribe_parser.add_argument("output")
    transcribe_parser.add_argument("--provider", choices=["basic-pitch", "noop"], default="basic-pitch")
    transcribe_parser.add_argument("--source-identity", default="")
    transcribe_parser.add_argument("--artifact-root", default="")
    transcribe_parser.add_argument("--onset-threshold", type=float, default=0.5)
    transcribe_parser.add_argument("--frame-threshold", type=float, default=0.3)
    transcribe_parser.add_argument("--minimum-note-length-ms", type=float, default=127.7)
    transcribe_parser.add_argument("--minimum-frequency-hz", type=float)
    transcribe_parser.add_argument("--maximum-frequency-hz", type=float)
    transcribe_parser.add_argument("--multiple-pitch-bends", action="store_true")
    transcribe_parser.add_argument("--no-melodia", action="store_true")
    transcribe_parser.add_argument("--midi-tempo", type=float, default=120.0)
    transcribe_parser.add_argument("--model-path", default="")
    transcribe_parser.add_argument("--overwrite", action="store_true")

    return parser


def midi_main(argv: list[str] | None = None) -> int:
    parser = _midi_build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            ledger = midi_read(args.input)
            result: dict[str, Any] = {"ok": True, "statistics": midi_statistics(ledger), "source": ledger.get("source")}
            if args.full:
                result["ledger"] = ledger
            _midi_print(result)
            return 0
        if args.command == "ledger":
            _midi_print(midi_write_ledger_json(midi_read(args.input), args.output, overwrite=args.overwrite))
            return 0
        if args.command == "roundtrip":
            _midi_print(midi_roundtrip(args.input, args.output, overwrite=args.overwrite))
            return 0
        if args.command == "render":
            _midi_print(
                midi_render_file(
                    args.input,
                    args.output,
                    stems_dir=args.stems_dir or None,
                    sample_rate=args.sample_rate,
                    waveform=args.waveform,
                    pitch_bend_range_semitones=args.pitch_bend_range,
                    max_seconds=args.max_seconds,
                    target_peak=args.target_peak,
                    overwrite=args.overwrite,
                )
            )
            return 0
        if args.command == "transcribe":
            output = Path(args.output).expanduser().resolve()
            if output.exists() and not args.overwrite:
                raise FileExistsError(f"refusing to overwrite existing note observation: {output}")
            provider = BasicPitchNoteTranscriber() if args.provider == "basic-pitch" else NoopNoteTranscriber()
            config: dict[str, Any] = {
                "onset_threshold": args.onset_threshold,
                "frame_threshold": args.frame_threshold,
                "minimum_note_length_ms": args.minimum_note_length_ms,
                "minimum_frequency_hz": args.minimum_frequency_hz,
                "maximum_frequency_hz": args.maximum_frequency_hz,
                "multiple_pitch_bends": bool(args.multiple_pitch_bends),
                "melodia_trick": not args.no_melodia,
                "midi_tempo": args.midi_tempo,
            }
            if args.model_path:
                config["model_path"] = args.model_path
            store = ArtifactStore(Path(args.artifact_root).expanduser().resolve()) if args.artifact_root else None
            observation = provider.transcribe(
                args.input,
                source_identity=args.source_identity,
                config=config,
                artifact_store=store,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _midi_print({
                "ok": True,
                "path": str(output),
                "provider": observation.get("provider"),
                "model_sha256": observation.get("model_sha256"),
                "observation_sha256": observation.get("observation_sha256"),
                "note_count": observation.get("note_count"),
                "cache_status": observation.get("cache_status"),
            })
            return 0
        parser.error(f"unknown command: {args.command}")
        return 2
    except (MidiLedgerError, RuntimeError, OSError, ValueError) as exc:
        _midi_print({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 2
