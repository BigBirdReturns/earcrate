from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from earcrate.midi.render import midi_render_ledger
from earcrate.study.reference_bundle import (
    ReferenceBundleError,
    reference_compile_bundle,
    reference_write_bundle,
)
from earcrate.study.reference_grid import (
    reference_accept_grid,
    reference_propose_drum_observation_from_audio,
    reference_propose_grid_from_audio,
    reference_validate_drum_observation,
    reference_validate_grid,
    reference_validate_note_observation,
)


def _reference_cli_load(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReferenceBundleError(f"JSON object required: {source}")
    return value


def _reference_cli_write(path: str | Path, value: Mapping[str, Any], *, overwrite: bool) -> None:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite JSON output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reference_cli_observation(spec_root: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    inline = row.get("observation")
    if isinstance(inline, Mapping):
        value = dict(inline)
    else:
        raw = str(row.get("observation_path") or "")
        if not raw:
            raise ReferenceBundleError("reference track requires observation or observation_path")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = spec_root / path
        value = _reference_cli_load(path)
    reference_validate_note_observation(value)
    return value


def _reference_cli_compile_spec(spec_path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    source = Path(spec_path).expanduser().resolve()
    spec = _reference_cli_load(source)
    tracks = []
    for row in spec.get("tracks") or []:
        if not isinstance(row, Mapping):
            raise ReferenceBundleError("reference spec tracks must be objects")
        tracks.append(
            {
                "track_id": str(row.get("track_id") or ""),
                "name": str(row.get("name") or row.get("track_id") or ""),
                "role": str(row.get("role") or "other"),
                "program": int(row.get("program") or 0),
                "pitch_bend_units_per_semitone": row.get("pitch_bend_units_per_semitone"),
                "observation": _reference_cli_observation(source.parent, row),
            }
        )
    drum = None
    inline_drum = spec.get("drum_observation")
    raw_drum = str(spec.get("drum_observation_path") or "")
    if isinstance(inline_drum, Mapping):
        drum = dict(inline_drum)
    elif raw_drum:
        path = Path(raw_drum).expanduser()
        if not path.is_absolute():
            path = source.parent / path
        drum = _reference_cli_load(path)
    if drum is not None:
        reference_validate_drum_observation(drum)
    return tracks, drum


def reference_cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m earcrate.study.reference_cli",
        description="Build local reference evidence: propose and accept beat grids, measure drum triggers, and compile exact MIDI reconstruction bundles.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    grid = sub.add_parser("grid-propose", help="propose a deterministic local beat/downbeat grid")
    grid.add_argument("audio")
    grid.add_argument("output")
    grid.add_argument("--meter", type=int, default=4)
    grid.add_argument("--denominator", type=int, default=4)
    grid.add_argument("--sample-rate", type=int, default=22_050)
    grid.add_argument("--duration", type=float, default=0.0)
    grid.add_argument("--overwrite", action="store_true")

    accept = sub.add_parser("grid-accept", help="turn a proposed grid into an explicitly accepted revision")
    accept.add_argument("input")
    accept.add_argument("output")
    accept.add_argument("--actor", required=True)
    accept.add_argument("--reason", required=True)
    accept.add_argument("--overwrite", action="store_true")

    drums = sub.add_parser("drums-propose", help="propose deterministic drum triggers from an isolated drum stem")
    drums.add_argument("audio")
    drums.add_argument("output")
    drums.add_argument("--source-identity", default="")
    drums.add_argument("--sample-rate", type=int, default=22_050)
    drums.add_argument("--duration", type=float, default=0.0)
    drums.add_argument("--overwrite", action="store_true")

    compile_parser = sub.add_parser("compile", help="compile accepted observations into exact MIDI and a reference bundle")
    compile_parser.add_argument("audio")
    compile_parser.add_argument("grid")
    compile_parser.add_argument("spec")
    compile_parser.add_argument("bundle")
    compile_parser.add_argument("midi")
    compile_parser.add_argument("--ppq", type=int, default=480)
    compile_parser.add_argument("--subdivisions", type=int, default=4)
    compile_parser.add_argument("--maximum-error", type=float, default=0.080)
    compile_parser.add_argument("--sample-rate", type=int, default=44_100)
    compile_parser.add_argument("--pitch-bend-range", type=float, default=2.0)
    compile_parser.add_argument("--allow-incomplete", action="store_true")
    compile_parser.add_argument("--neutral-render", default="")
    compile_parser.add_argument("--stems-dir", default="")
    compile_parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "grid-propose":
            value = reference_propose_grid_from_audio(
                args.audio,
                meter_numerator=args.meter,
                meter_denominator=args.denominator,
                sample_rate=args.sample_rate,
                duration_seconds=args.duration,
            )
            _reference_cli_write(args.output, value, overwrite=bool(args.overwrite))
            result = {"ok": True, "status": value["status"], "path": str(Path(args.output).expanduser().resolve()), "grid_sha256": value["grid_sha256"], "beat_count": len(value["beats"]), "confidence": value["confidence"]}
        elif args.command == "grid-accept":
            proposed = _reference_cli_load(args.input)
            reference_validate_grid(proposed, require_accepted=False)
            value = reference_accept_grid(proposed, actor=args.actor, reason=args.reason)
            _reference_cli_write(args.output, value, overwrite=bool(args.overwrite))
            result = {"ok": True, "status": value["status"], "path": str(Path(args.output).expanduser().resolve()), "grid_sha256": value["grid_sha256"], "parent_grid_sha256": value["acceptance"]["parent_grid_sha256"]}
        elif args.command == "drums-propose":
            value = reference_propose_drum_observation_from_audio(
                args.audio,
                source_identity=args.source_identity,
                sample_rate=args.sample_rate,
                duration_seconds=args.duration,
            )
            _reference_cli_write(args.output, value, overwrite=bool(args.overwrite))
            result = {"ok": True, "path": str(Path(args.output).expanduser().resolve()), "observation_sha256": value["observation_sha256"], "event_count": value["event_count"]}
        else:
            grid_value = _reference_cli_load(args.grid)
            tracks, drum = _reference_cli_compile_spec(args.spec)
            bundle = reference_compile_bundle(
                args.audio,
                grid_value,
                tracks,
                drum_observation=drum,
                ppq=args.ppq,
                quantization_subdivisions=args.subdivisions,
                maximum_quantization_error_seconds=args.maximum_error,
                sample_rate=args.sample_rate,
                pitch_bend_range_semitones=args.pitch_bend_range,
            )
            receipt = reference_write_bundle(
                bundle,
                args.bundle,
                args.midi,
                overwrite=bool(args.overwrite),
                allow_incomplete=bool(args.allow_incomplete),
            )
            render = None
            if args.neutral_render:
                render = midi_render_ledger(
                    bundle["midi_ledger"],
                    args.neutral_render,
                    stems_dir=args.stems_dir or None,
                    sample_rate=args.sample_rate,
                    overwrite=bool(args.overwrite),
                )
            result = {**receipt, "neutral_render": render}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ReferenceBundleError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(reference_cli_main())
