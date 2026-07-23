from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import buffalo
from .commands import apply_command
from .compiler import compile_project, import_legacy_arrangement
from .export import export_project
from .lower import renderability_receipt
from .model import canonical_diff, summarize_revision
from .render import preview_project, render_project, verify_render
from .store import ProjectStore
from .util import ProjectError, ValidationError


def _root(value: str | None) -> Path:
    raw = value or os.environ.get("EARCRATE_PROJECT_ROOT") or str(Path.cwd() / "EarCrateProjects")
    return Path(raw).expanduser().resolve()


def _json_arg(value: str) -> Any:
    path = Path(value).expanduser()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _add_common_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_id")
    parser.add_argument("--revision", default="", help="immutable revision SHA; default is active head")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate project",
        description="EarCrate project/score engine: compile immutable editable scores, execute exact render programs, and retain every decision.",
    )
    parser.add_argument("--root", default="", help="visible project store root (default: $EARCRATE_PROJECT_ROOT or ./EarCrateProjects)")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capabilities", help="show which existing EarCrate buffalo components are available")

    comp = sub.add_parser("compile", help="compile a new project from a source manifest or the approved EarCrate")
    comp.add_argument("--name", required=True)
    comp.add_argument("--profile", required=True, help="TasteSpec id or JSON path")
    source_group = comp.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--sources", help="source manifest JSON path")
    source_group.add_argument("--from-crate", action="store_true", help="consume the existing approved EarAtom pool")
    comp.add_argument("--seconds", type=float, default=120.0)
    comp.add_argument("--seed", type=int, default=1)
    comp.add_argument("--sample-rate", type=int, default=44100)
    comp.add_argument("--analysis-seconds", type=float, default=180.0)
    comp.add_argument("--constraints", default="{}", help="JSON object or JSON file")
    comp.add_argument("--mode", default="automatic")
    comp.add_argument("--beam-width", type=int, default=12)
    comp.add_argument("--project-id", default="")

    legacy = sub.add_parser("import-legacy", help="migrate an existing arrangement JSON into a fully locked canonical project")
    legacy.add_argument("--name", required=True)
    legacy.add_argument("--profile", required=True)
    legacy.add_argument("--arrangement", required=True)
    legacy.add_argument("--sample-rate", type=int, default=44100)
    legacy.add_argument("--project-id", default="")

    sub.add_parser("list", help="list projects")

    show = sub.add_parser("show", help="show a project revision summary or full score")
    _add_common_revision(show)
    show.add_argument("--full", action="store_true")

    sheet = sub.add_parser("sheet", help="print the live decision sheet for a revision")
    _add_common_revision(sheet)
    sheet.add_argument("--kind", default="", choices=["", "clip_selection", "transition", "human_override", "mastering_plan", "legacy_import"])

    validate = sub.add_parser("validate", help="validate immutable revisions and exact lowering")
    validate.add_argument("project_id")

    diff = sub.add_parser("diff", help="compare two immutable revisions")
    diff.add_argument("project_id")
    diff.add_argument("left")
    diff.add_argument("right")

    cmd = sub.add_parser("command", help="apply a typed edit and create a new immutable revision")
    cmd.add_argument("project_id")
    cmd.add_argument("operation", choices=["set_gain", "set_pan", "set_fades", "trim", "move", "mute", "unmute", "solo", "unsolo", "set_loop", "set_stem", "replace_source", "add_source", "remove_source", "set_automation", "remove_automation", "set_transition", "lock", "unlock"])
    cmd.add_argument("--clip-id", default="")
    cmd.add_argument("--gain-db", type=float)
    cmd.add_argument("--pan", type=float)
    cmd.add_argument("--fade-in-beats", type=float)
    cmd.add_argument("--fade-out-beats", type=float)
    cmd.add_argument("--source-start-sample", type=int)
    cmd.add_argument("--source-end-sample", type=int)
    cmd.add_argument("--timeline-start-beat", type=float)
    cmd.add_argument("--stem", default="")
    cmd.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    cmd.add_argument("--crossfade-samples", type=int)
    cmd.add_argument("--source-id", default="")
    cmd.add_argument("--transition-id", default="")
    cmd.add_argument("--technique", default="")
    cmd.add_argument("--duration-beats", type=float)
    cmd.add_argument("--curve", default="")
    cmd.add_argument("--path", default="")
    cmd.add_argument("--reason", default="")
    cmd.add_argument("--source", default="", help="source JSON object or JSON file for add_source")
    cmd.add_argument("--automation-id", default="")
    cmd.add_argument("--parameter", default="")
    cmd.add_argument("--points", default="", help="automation point array as JSON or JSON file")
    cmd.add_argument("--expected-head", default="")
    cmd.add_argument("--payload", default="", help="complete JSON payload; overrides individual flags")

    undo = sub.add_parser("undo", help="move the active head to the previous revision")
    undo.add_argument("project_id")
    undo.add_argument("--expected-head", default="")

    redo = sub.add_parser("redo", help="move the active head to the next revision")
    redo.add_argument("project_id")
    redo.add_argument("--expected-head", default="")

    render = sub.add_parser("render", help="lower and render the exact active revision; unresolved mastering becomes a visible child revision")
    _add_common_revision(render)
    render.add_argument("--output", default="")
    render.add_argument("--no-finalize-mastering", action="store_true")

    preview = sub.add_parser("preview", help="audition one clip or a beat range without changing project state")
    _add_common_revision(preview)
    preview.add_argument("--clip-id", default="")
    preview.add_argument("--start-beat", type=float)
    preview.add_argument("--end-beat", type=float)
    preview.add_argument("--output", required=True)

    verify = sub.add_parser("verify", help="judge a WAV against the exact project revision")
    _add_common_revision(verify)
    verify.add_argument("wav")

    export = sub.add_parser("export", help="export the exact revision to EDL JSON or Reaper RPP")
    _add_common_revision(export)
    export.add_argument("--format", required=True, choices=["edl", "rpp"])
    export.add_argument("--output", default="")

    template = sub.add_parser("source-template", help="print a source-manifest template")
    template.add_argument("--profile", default="remix_prettylights_v1")

    return parser


def _command_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        payload = _json_arg(args.payload)
        if not isinstance(payload, dict):
            raise ValidationError("--payload must be a JSON object")
        return payload
    operation = args.operation
    payload: dict[str, Any] = {}
    if args.clip_id:
        payload["clip_id"] = args.clip_id
    if args.gain_db is not None:
        payload["gain_db"] = args.gain_db
    if args.pan is not None:
        payload["pan"] = args.pan
    if args.fade_in_beats is not None:
        payload["in_beats"] = args.fade_in_beats
    if args.fade_out_beats is not None:
        payload["out_beats"] = args.fade_out_beats
    if args.source_start_sample is not None:
        payload["source_start_sample"] = args.source_start_sample
    if args.source_end_sample is not None:
        payload["source_end_sample"] = args.source_end_sample
    if args.timeline_start_beat is not None:
        payload["timeline_start_beat"] = args.timeline_start_beat
    if args.stem:
        payload["stem"] = args.stem
    if args.enabled is not None:
        payload["enabled"] = args.enabled
    if args.crossfade_samples is not None:
        payload["crossfade_samples"] = args.crossfade_samples
    if args.source_id:
        payload["source_id"] = args.source_id
    if args.transition_id:
        payload["transition_id"] = args.transition_id
    if args.technique:
        payload["technique"] = args.technique
    if args.duration_beats is not None:
        payload["duration_beats"] = args.duration_beats
    if args.curve:
        payload["curve"] = args.curve
    if args.path:
        payload["path"] = args.path
    if args.reason:
        payload["reason"] = args.reason
    if args.source:
        payload["source"] = _json_arg(args.source)
    if args.automation_id:
        payload["automation_id"] = args.automation_id
    if args.parameter:
        payload["parameter"] = args.parameter
    if args.points:
        payload["points"] = _json_arg(args.points)
    required = {
        "set_gain": ["clip_id", "gain_db"],
        "set_pan": ["clip_id", "pan"],
        "set_fades": ["clip_id"],
        "trim": ["clip_id"],
        "move": ["clip_id", "timeline_start_beat"],
        "mute": ["clip_id"],
        "unmute": ["clip_id"],
        "solo": ["clip_id"],
        "unsolo": ["clip_id"],
        "set_loop": ["clip_id", "enabled"],
        "set_stem": ["clip_id", "stem"],
        "replace_source": ["clip_id", "source_id"],
        "add_source": ["source"],
        "remove_source": ["source_id"],
        "set_automation": ["clip_id", "points"],
        "remove_automation": [],
        "set_transition": ["transition_id", "technique"],
        "lock": ["path"],
        "unlock": ["path"],
    }[operation]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValidationError(f"{operation} requires: {', '.join(missing)}")
    if operation == "set_fades" and "in_beats" not in payload and "out_beats" not in payload:
        raise ValidationError("set_fades requires --fade-in-beats or --fade-out-beats")
    if operation == "remove_automation" and not payload.get("automation_id") and not payload.get("clip_id"):
        raise ValidationError("remove_automation requires --automation-id or --clip-id")
    return payload


def _source_template(profile: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "My EarCrate Project",
        "profile": profile,
        "constraints": {"bpm": 96.0, "key_root": 0},
        "sources": [
            {
                "path": "/absolute/path/to/instrumental.wav",
                "label": "instrumental bed",
                "kind": "project_scoped",
                "role_hint": "floor",
                "ear_role": "BED_CHORD",
                "stem": "mix",
                "stems": {"no_vocals": "/absolute/path/to/instrumental.no_vocals.wav"},
                "loopable": True,
                "locked": False,
                "regions": [{"start_s": 0.0, "end_s": 32.0, "role_hint": "floor", "ear_role": "BED_CHORD"}],
            },
            {
                "path": "/absolute/path/to/vocal.wav",
                "label": "foreground vocal",
                "kind": "project_scoped",
                "role_hint": "vocal",
                "ear_role": "VOX_VERSE",
                "stem": "mix",
                "stems": {"vocals": "/absolute/path/to/vocal.vocals.wav"},
                "loopable": False,
                "locked": True,
                "regions": [{"start_s": 0.0, "end_s": 24.0, "role_hint": "vocal", "ear_role": "VOX_VERSE", "locked": True}],
            },
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ProjectStore(_root(args.root))
    try:
        if args.command == "capabilities":
            _print({"ok": True, "project_root": str(store.root), "buffalo": buffalo.capabilities()})
        elif args.command == "compile":
            constraints = _json_arg(args.constraints)
            if not isinstance(constraints, dict):
                raise ValidationError("--constraints must be a JSON object")
            result = compile_project(
                store,
                name=args.name,
                profile=args.profile,
                source_manifest=args.sources or None,
                from_crate=bool(args.from_crate),
                target_seconds=args.seconds,
                seed=args.seed,
                sample_rate=args.sample_rate,
                analysis_seconds=args.analysis_seconds,
                constraints=constraints,
                mode=args.mode,
                beam_width=args.beam_width,
                project_id=args.project_id or None,
            )
            _print(result)
        elif args.command == "import-legacy":
            arrangement = _json_arg(args.arrangement)
            result = import_legacy_arrangement(
                store,
                name=args.name,
                arrangement=arrangement,
                profile=args.profile,
                sample_rate=args.sample_rate,
                project_id=args.project_id or None,
            )
            _print(result)
        elif args.command == "list":
            _print({"ok": True, "root": str(store.root), "items": store.list_projects()})
        elif args.command == "show":
            revision = store.load_revision(args.project_id, args.revision or None)
            _print({"ok": True, "project": store.load_project(args.project_id), "revision": revision if args.full else summarize_revision(revision)})
        elif args.command == "sheet":
            revision = store.load_revision(args.project_id, args.revision or None)
            decisions = [item for item in revision.get("decisions") or [] if not args.kind or item.get("kind") == args.kind]
            _print({
                "ok": True,
                "project_id": args.project_id,
                "revision_sha": revision["revision_sha"],
                "profile": revision["intent"]["taste_profile"],
                "policy_receipt": revision.get("compiler_receipt", {}).get("policy_receipt"),
                "decisions": decisions,
                "locks": revision.get("locks") or [],
                "mastering": revision.get("mastering"),
                "static_gate": revision.get("static_gate_receipt"),
                "renderability": renderability_receipt(revision),
            })
        elif args.command == "validate":
            store_receipt = store.validate_store(args.project_id)
            revision = store.load_revision(args.project_id)
            _print({"ok": bool(store_receipt["ok"]), "store": store_receipt, "renderability": renderability_receipt(revision)})
        elif args.command == "diff":
            left = store.load_revision(args.project_id, args.left)
            right = store.load_revision(args.project_id, args.right)
            _print({"ok": True, "project_id": args.project_id, "left": args.left, "right": args.right, "changes": canonical_diff(left, right)})
        elif args.command == "command":
            payload = _command_payload(args)
            _print(apply_command(store, args.project_id, args.operation, payload, expected_head=args.expected_head or None))
        elif args.command == "undo":
            _print(store.undo(args.project_id, expected_head=args.expected_head or None))
        elif args.command == "redo":
            _print(store.redo(args.project_id, expected_head=args.expected_head or None))
        elif args.command == "render":
            _print(render_project(
                store,
                args.project_id,
                revision_sha=args.revision or None,
                output=args.output or None,
                finalize_mastering=not args.no_finalize_mastering,
            ))
        elif args.command == "preview":
            _print(preview_project(
                store,
                args.project_id,
                revision_sha=args.revision or None,
                clip_id=args.clip_id or None,
                start_beat=args.start_beat,
                end_beat=args.end_beat,
                output=args.output,
            ))
        elif args.command == "verify":
            _print(verify_render(store, args.project_id, args.wav, revision_sha=args.revision or None))
        elif args.command == "export":
            _print(export_project(store, args.project_id, args.format, revision_sha=args.revision or None, path=args.output or None))
        elif args.command == "source-template":
            _print(_source_template(args.profile))
        else:
            parser.error("unknown project command")
        return 0
    except (ProjectError, ValidationError, OSError, ValueError) as exc:
        _print({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
