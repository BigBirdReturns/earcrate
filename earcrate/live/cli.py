from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from earcrate.live.crate import (
    live_compile_crate_atlas,
    live_load_crate_atlas,
    live_run_crate_session,
    live_write_crate_session,
)
from earcrate.live.model import LiveError, live_persona_names
from earcrate.live.planner import live_atlas_from_midi, live_runtime_capability
from earcrate.live.runtime_fix import live_build_session
from earcrate.midi.codec import midi_read, midi_write
from earcrate.midi.render import midi_render_ledger


def _live_cli_json(path: str | Path) -> Any:
    source = Path(path).expanduser().resolve()
    return json.loads(source.read_text(encoding="utf-8"))


def _live_cli_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite live output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(destination), "bytes": destination.stat().st_size}


def _live_cli_controls(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    value = _live_cli_json(path)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise LiveError("live controls file must be a JSON array of objects")
    return [dict(row) for row in value]


def _live_cli_atoms(path: str | Path) -> list[dict[str, Any]]:
    value = _live_cli_json(path)
    if isinstance(value, Mapping):
        value = value.get("items", value.get("atoms"))
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise LiveError("live crate atoms file must be a JSON array or an object with items/atoms")
    return [dict(row) for row in value]


def _add_live_session_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=64)
    parser.add_argument("--persona", choices=live_persona_names(), default="club")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--controls", default="", help="JSON array of timed control commands")
    parser.add_argument("--energy", type=float)
    parser.add_argument("--density", type=float)
    parser.add_argument("--risk", type=float)
    parser.add_argument("--maximum-layers", type=int)
    parser.add_argument("--phrase-bars", type=int, default=0)
    parser.add_argument("--horizon-bars", type=int, default=0)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--candidate-limit", type=int, default=12)
    parser.add_argument("--target-bpm", type=float, default=0.0)


def live_cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate live",
        description="Compile musical knowledge offline, then plan persona-switchable DJ sets and execute them locally through exact MIDI or precompiled sample racks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capability", help="report the no-cloud live runtime contract")

    atlas_parser = sub.add_parser("atlas", help="compile a MIDI performance into a reusable live material atlas")
    atlas_parser.add_argument("input")
    atlas_parser.add_argument("output")
    atlas_parser.add_argument("--overwrite", action="store_true")

    session_parser = sub.add_parser("session", help="plan and execute a deterministic receding-horizon live set")
    session_parser.add_argument("input")
    session_parser.add_argument("output_root")
    _add_live_session_arguments(session_parser)
    session_parser.add_argument("--neutral-render", action="store_true")
    session_parser.add_argument("--sample-rate", type=int, default=8_000)
    session_parser.add_argument("--stems", action="store_true")
    session_parser.add_argument("--overwrite", action="store_true")

    crate_parser = sub.add_parser("crate-compile", help="search approved material once and seal reusable sample racks for live CPU execution")
    crate_parser.add_argument("input", help="source MIDI performance")
    crate_parser.add_argument("atoms", help="approved atom JSON array or object with items/atoms")
    crate_parser.add_argument("output_root")
    crate_parser.add_argument("--profile", default="")
    crate_parser.add_argument("--top-k", type=int, default=8)
    crate_parser.add_argument("--max-transpose", type=float, default=18.0)
    crate_parser.add_argument("--loopability-threshold", type=float, default=0.58)
    crate_parser.add_argument("--max-zones", type=int, default=8)
    crate_parser.add_argument("--combination-beam", type=int, default=64)
    crate_parser.add_argument("--sample-rate", type=int, default=44_100)
    crate_parser.add_argument("--no-sfz", action="store_true")
    crate_parser.add_argument("--overwrite", action="store_true")

    crate_session_parser = sub.add_parser("crate-session", help="plan, bind, and optionally render from a precompiled live crate without scanning the library")
    crate_session_parser.add_argument("crate_atlas")
    crate_session_parser.add_argument("output_root")
    _add_live_session_arguments(crate_session_parser)
    crate_session_parser.add_argument("--render", action="store_true")
    crate_session_parser.add_argument("--stems", action="store_true")
    crate_session_parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "capability":
            result = {"ok": True, **live_runtime_capability()}
        elif args.command == "atlas":
            atlas = live_atlas_from_midi(midi_read(args.input))
            receipt = _live_cli_atomic_json(args.output, atlas, overwrite=bool(args.overwrite))
            result = {
                "ok": True,
                "atlas_sha256": atlas["atlas_sha256"],
                "declared_pattern_count": atlas["declared_pattern_count"],
                "declared_material_count": atlas["declared_material_count"],
                **receipt,
            }
        elif args.command == "crate-compile":
            compiled = live_compile_crate_atlas(
                midi_read(args.input),
                _live_cli_atoms(args.atoms),
                args.output_root,
                taste_profile=args.profile,
                top_k=args.top_k,
                maximum_transpose_semitones=args.max_transpose,
                loopability_threshold=args.loopability_threshold,
                max_zones_per_slot=args.max_zones,
                combination_beam_width=args.combination_beam,
                sample_rate=args.sample_rate,
                compile_sfz=not args.no_sfz,
                overwrite=bool(args.overwrite),
            )
            atlas = compiled["atlas"]
            result = {
                "ok": True,
                "complete": True,
                "crate_atlas_sha256": atlas["crate_atlas_sha256"],
                "live_atlas_sha256": atlas["live_atlas_sha256"],
                "rack_build_sha256": atlas["rack_build_sha256"],
                "rack_count": len(atlas["rack_revisions"]),
                "path": compiled["write"]["path"],
            }
        elif args.command == "crate-session":
            root = Path(args.output_root).expanduser().resolve()
            planned_paths = [
                root / "live-session.json",
                root / "live-session.mid",
                root / "live-session.binding.json",
                root / "live-session.cpu-program.json",
                root / "live-session.cpu-execution.json",
            ]
            render_path = root / "live-session.wav" if args.render else None
            if render_path is not None:
                planned_paths.extend(
                    [
                        render_path,
                        render_path.with_suffix(render_path.suffix + ".render.json"),
                        render_path.with_suffix(render_path.suffix + ".program.json"),
                        render_path.with_suffix(render_path.suffix + ".execution.json"),
                    ]
                )
            if not args.overwrite:
                conflicts = [str(path) for path in planned_paths if path.exists()]
                if conflicts:
                    raise FileExistsError("refusing partial live crate session because output path(s) already exist: " + ", ".join(conflicts))
            atlas = live_load_crate_atlas(args.crate_atlas, verify_sources=True)
            session = live_run_crate_session(
                atlas,
                target_bars=args.bars,
                persona=args.persona,
                seed=args.seed,
                controls=_live_cli_controls(args.controls),
                target_energy=args.energy,
                density=args.density,
                risk=args.risk,
                maximum_layers=args.maximum_layers,
                horizon_bars=args.horizon_bars,
                phrase_bars=args.phrase_bars,
                beam_width=args.beam_width,
                candidate_limit=args.candidate_limit,
                target_bpm=args.target_bpm,
                render_path=render_path,
                stems_dir=(root / "live-session-stems") if args.stems and render_path is not None else None,
                overwrite=bool(args.overwrite),
            )
            writes = live_write_crate_session(session, root, overwrite=bool(args.overwrite))
            result = {
                "ok": True,
                "complete": True,
                "crate_atlas_sha256": atlas["crate_atlas_sha256"],
                "crate_session_sha256": session["session"]["crate_session_sha256"],
                "generated_event_count": session["session"]["generated_event_count"],
                "bound_event_count": session["session"]["bound_event_count"],
                "library_materials_scanned_during_execution": session["session"]["library_materials_scanned_during_execution"],
                "render": session["render"],
                "writes": writes,
            }
        else:
            root = Path(args.output_root).expanduser().resolve()
            planned_paths = [
                root / "atlas.json",
                root / "session.plan.json",
                root / "final.state.json",
                root / "midi.lowering.json",
                root / "cpu.program.json",
                root / "cpu.execution.json",
                root / "session.mid",
            ]
            if args.neutral_render:
                planned_paths.extend(
                    [
                        root / "neutral.wav",
                        root / "neutral.wav.render.json",
                        root / "neutral.wav.program.json",
                        root / "neutral.wav.execution.json",
                    ]
                )
            if not args.overwrite:
                conflicts = [str(path) for path in planned_paths if path.exists()]
                if conflicts:
                    raise FileExistsError("refusing partial live build because output path(s) already exist: " + ", ".join(conflicts))
            root.mkdir(parents=True, exist_ok=True)
            source = midi_read(args.input)
            build = live_build_session(
                source,
                target_bars=args.bars,
                persona=args.persona,
                seed=args.seed,
                controls=_live_cli_controls(args.controls),
                target_energy=args.energy,
                density=args.density,
                risk=args.risk,
                maximum_layers=args.maximum_layers,
                horizon_bars=args.horizon_bars,
                phrase_bars=args.phrase_bars,
                beam_width=args.beam_width,
                candidate_limit=args.candidate_limit,
                target_bpm=args.target_bpm,
            )
            _live_cli_atomic_json(root / "atlas.json", build["atlas"], overwrite=bool(args.overwrite))
            _live_cli_atomic_json(root / "session.plan.json", build["session"], overwrite=bool(args.overwrite))
            _live_cli_atomic_json(root / "final.state.json", build["final_state"], overwrite=bool(args.overwrite))
            _live_cli_atomic_json(root / "midi.lowering.json", build["midi_lowering"], overwrite=bool(args.overwrite))
            _live_cli_atomic_json(root / "cpu.program.json", build["cpu_program"], overwrite=bool(args.overwrite))
            _live_cli_atomic_json(root / "cpu.execution.json", build["cpu_execution"], overwrite=bool(args.overwrite))
            midi_receipt = midi_write(build["midi_ledger"], root / "session.mid", overwrite=bool(args.overwrite))
            neutral = None
            if args.neutral_render:
                neutral = midi_render_ledger(
                    build["midi_ledger"],
                    root / "neutral.wav",
                    stems_dir=(root / "neutral-stems") if args.stems else None,
                    sample_rate=args.sample_rate,
                    overwrite=bool(args.overwrite),
                )
            result = {
                "ok": True,
                "complete": True,
                "output_root": str(root),
                "atlas_sha256": build["atlas"]["atlas_sha256"],
                "session_sha256": build["session"]["session_sha256"],
                "lowering_sha256": build["midi_lowering"]["lowering_sha256"],
                "program_sha256": build["cpu_program"]["program_sha256"],
                "execution_sha256": build["cpu_execution"]["execution_sha256"],
                "target_bars": build["session"]["target_bars"],
                "generated_note_count": build["midi_lowering"]["output_statistics"]["note_on_count"],
                "peak_active_layers": build["cpu_execution"]["peak_active_layer_count"],
                "runtime_operation_count": build["cpu_execution"]["runtime_operation_count"],
                "materials_scanned_during_execution": build["cpu_execution"]["materials_scanned_during_execution"],
                "midi": midi_receipt,
                "neutral_render": neutral,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (LiveError, OSError, RuntimeError, ValueError) as exc:
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
