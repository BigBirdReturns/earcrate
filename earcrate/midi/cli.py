from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from earcrate.midi.codec import midi_read, midi_roundtrip, midi_write_ledger_json
from earcrate.midi.model import MidiLedgerError, midi_statistics
from earcrate.midi.render import MIDI_RENDER_WAVEFORMS, midi_render_file
from earcrate.providers import get
from earcrate.providers.artifacts import ArtifactStore
from earcrate.rack.binding import rack_compile_binding, rack_load_binding, rack_load_many
from earcrate.rack.demand import rack_compile_demands
from earcrate.rack.model import (
    RACK_MODES,
    RackError,
    rack_atomic_json,
    rack_load_revision,
    rack_seal_draft,
    rack_template,
)
from earcrate.rack.render import rack_render_ledger
from earcrate.rack.sfz import rack_compile_sfz


def _midi_print(value: Any, *, stream: Any = None) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream or sys.stdout)


def _json_arg(value: str) -> Any:
    path = Path(value).expanduser()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _midi_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate midi",
        description="Exact MIDI ledger, neutral proof renderer, sample-rack substitution bridge, and note-observation providers.",
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

    demand_parser = sub.add_parser("demand", help="compile exact substitution requirements from a MIDI performance")
    demand_parser.add_argument("input")
    demand_parser.add_argument("output")
    demand_parser.add_argument("--pitch-bend-range", type=float, default=2.0)
    demand_parser.add_argument("--overwrite", action="store_true")

    template_parser = sub.add_parser("rack-template", help="write a human-editable sample-rack draft")
    template_parser.add_argument("output")
    template_parser.add_argument("--mode", choices=sorted(RACK_MODES), default="pitched")
    template_parser.add_argument("--rack-id", default="my-rack")
    template_parser.add_argument("--name", default="My Rack")
    template_parser.add_argument("--overwrite", action="store_true")

    seal_parser = sub.add_parser("rack-seal", help="resolve sample identities and seal an immutable rack revision")
    seal_parser.add_argument("draft")
    seal_parser.add_argument("output")
    seal_parser.add_argument("--base-dir", default="", help="base for relative sample paths; default is the draft directory")
    seal_parser.add_argument("--overwrite", action="store_true")

    bind_parser = sub.add_parser("bind", help="bind every MIDI event to one exact compatible rack zone")
    bind_parser.add_argument("input")
    bind_parser.add_argument("output")
    bind_parser.add_argument("racks", nargs="+")
    bind_parser.add_argument("--assignments", default="{}", help="slot_id to rack_id/rack_sha JSON object or file")
    bind_parser.add_argument("--pitch-bend-range", type=float, default=2.0)
    bind_parser.add_argument("--overwrite", action="store_true")

    sfz_parser = sub.add_parser("sfz", help="compile a sealed rack revision to SFZ object code")
    sfz_parser.add_argument("rack")
    sfz_parser.add_argument("output")
    sfz_parser.add_argument("--overwrite", action="store_true")

    rack_render_parser = sub.add_parser("render-rack", help="execute a complete MIDI binding through exact sample slices")
    rack_render_parser.add_argument("input")
    rack_render_parser.add_argument("binding")
    rack_render_parser.add_argument("output")
    rack_render_parser.add_argument("--rack", dest="racks", action="append", required=True, help="sealed rack JSON; repeat for every referenced revision")
    rack_render_parser.add_argument("--stems-dir", default="")
    rack_render_parser.add_argument("--sample-rate", type=int, default=44_100)
    rack_render_parser.add_argument("--max-seconds", type=float, default=0.0)
    rack_render_parser.add_argument("--target-peak", type=float, default=0.92)
    rack_render_parser.add_argument("--overwrite", action="store_true")

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
            provider = get("notes", args.provider)
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
        if args.command == "demand":
            demand = rack_compile_demands(midi_read(args.input), pitch_bend_range_semitones=args.pitch_bend_range)
            receipt = rack_atomic_json(args.output, demand, overwrite=args.overwrite)
            _midi_print({**receipt, "demand_sha256": demand["demand_sha256"], "slot_count": demand["slot_count"], "selected_event_count": demand["selected_event_count"]})
            return 0
        if args.command == "rack-template":
            draft = rack_template(mode=args.mode, rack_id=args.rack_id, name=args.name)
            _midi_print(rack_atomic_json(args.output, draft, overwrite=args.overwrite))
            return 0
        if args.command == "rack-seal":
            draft_path = Path(args.draft).expanduser().resolve()
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            base_dir = Path(args.base_dir).expanduser().resolve() if args.base_dir else draft_path.parent
            rack = rack_seal_draft(draft, base_dir=base_dir)
            receipt = rack_atomic_json(args.output, rack, overwrite=args.overwrite)
            _midi_print({**receipt, "rack_id": rack["rack_id"], "rack_sha256": rack["rack_sha256"], "zone_count": len(rack["zones"])})
            return 0
        if args.command == "bind":
            assignments = _json_arg(args.assignments)
            if not isinstance(assignments, dict):
                raise RackError("--assignments must be a JSON object")
            plan = rack_compile_binding(
                midi_read(args.input),
                rack_load_many(args.racks),
                assignments=assignments,
                pitch_bend_range_semitones=args.pitch_bend_range,
            )
            receipt = rack_atomic_json(args.output, plan, overwrite=args.overwrite)
            _midi_print({
                **receipt,
                "binding_sha256": plan["binding_sha256"],
                "complete": plan["complete"],
                "selected_event_count": plan["selected_event_count"],
                "bound_event_count": plan["bound_event_count"],
                "unresolved": plan["unresolved"],
            })
            return 0 if plan["complete"] else 3
        if args.command == "sfz":
            _midi_print(rack_compile_sfz(rack_load_revision(args.rack), args.output, overwrite=args.overwrite))
            return 0
        if args.command == "render-rack":
            _midi_print(
                rack_render_ledger(
                    midi_read(args.input),
                    rack_load_binding(args.binding),
                    rack_load_many(args.racks),
                    args.output,
                    stems_dir=args.stems_dir or None,
                    sample_rate=args.sample_rate,
                    max_seconds=args.max_seconds,
                    target_peak=args.target_peak,
                    overwrite=args.overwrite,
                )
            )
            return 0
        parser.error(f"unknown command: {args.command}")
        return 2
    except (MidiLedgerError, RackError, RuntimeError, OSError, ValueError) as exc:
        _midi_print({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 2
