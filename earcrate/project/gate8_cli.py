from __future__ import annotations

"""Canonical project command wrapper for Gate 8 custody, continuation, and execution.

Legacy project commands remain delegated to :mod:`earcrate.project.cli`. Gate 8
capability inspection is side-effect-free and runs before a store is constructed.
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .custody import (
    project_adopt_causal_semantics, project_import_causal_score,
    project_render_causal_score, project_verify_custody,
    project_verify_semantic_adoption,
)
from .continuation import project_extend_causal_score, project_verify_causal_continuation
from .gate8_store import Gate8ProjectStore
from .library import project_real_library_handshake
from .source_execution import project_execute_registered_source_phrase
from .util import ProjectError, ValidationError
from earcrate.rack.portable import rack_rebase_portable_bundle


def project_capabilities() -> dict[str, Any]:
    return {
        "schema": "earcrate/project-capabilities@1", "ok": True,
        "side_effect_free": True,
        "authority": {"audio_clip_score": True, "causal_score": True, "historical_custody": True, "semantic_adoption": True, "causal_continuation": True},
        "execution": {"source_phrase": True, "portable_rack_rebase": True, "real_library_handshake": True, "publication_requires_producer_verdict": True},
        "commands": ["import-causal-score", "verify-custody", "adopt-semantics", "verify-adoption", "render-causal-score", "library-handshake", "extend-causal-score", "verify-continuation", "rebase-portable-racks", "execute-source-phrase"],
    }


def _json_object(path: str | None, label: str) -> dict[str, Any] | None:
    if not path: return None
    source = Path(path).expanduser().resolve()
    try: value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc: raise ValidationError(f"invalid {label}: {source}: {exc}") from exc
    if not isinstance(value, dict): raise ValidationError(f"{label} must contain a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="earcrate project", add_help=True)
    parser.add_argument("--root", default="earcrate-projects", help="project store root")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("capabilities", help="report project capabilities without touching storage")
    imp = sub.add_parser("import-causal-score", help="import a selected causal score without recomposition")
    imp.add_argument("--name", required=True); imp.add_argument("--family-id", required=True)
    imp.add_argument("--midi", required=True); imp.add_argument("--score", required=True)
    imp.add_argument("--evidence"); imp.add_argument("--plan")
    imp.add_argument("--neutral-render", required=True); imp.add_argument("--producer-verdict", required=True)
    imp.add_argument("--profile", default="remix_prettylights_v1"); imp.add_argument("--producer-status")
    imp.add_argument("--actor", default="producer"); imp.add_argument("--reason", default="Gate 8.0 historical custody import")
    imp.add_argument("--supersedes", action="append", default=[]); imp.add_argument("--project-id")
    verify = sub.add_parser("verify-custody"); verify.add_argument("project_id"); verify.add_argument("--revision")
    adopt = sub.add_parser("adopt-semantics"); adopt.add_argument("project_id"); adopt.add_argument("--revision")
    adopt.add_argument("--annotations"); adopt.add_argument("--actor", default="producer"); adopt.add_argument("--reason", default="Gate 8.0 semantic adoption"); adopt.add_argument("--expected-head")
    va = sub.add_parser("verify-adoption"); va.add_argument("project_id"); va.add_argument("--revision")
    render = sub.add_parser("render-causal-score"); render.add_argument("project_id"); render.add_argument("output"); render.add_argument("--revision"); render.add_argument("--overwrite", action="store_true")
    lib = sub.add_parser("library-handshake"); lib.add_argument("project_id"); lib.add_argument("--workspace-config", required=True); lib.add_argument("--revision"); lib.add_argument("--profile"); lib.add_argument("--per-role-limit", type=int, default=2000); lib.add_argument("--max-transpose", type=float, default=18.0); lib.add_argument("--max-zones", type=int, default=8); lib.add_argument("--combination-beam", type=int, default=64)
    ext = sub.add_parser("extend-causal-score"); ext.add_argument("project_id"); ext.add_argument("--score", required=True); ext.add_argument("--midi", required=True); ext.add_argument("--evidence"); ext.add_argument("--annotations"); ext.add_argument("--execution-manifest"); ext.add_argument("--actor", default="producer"); ext.add_argument("--reason", default="extend causal score"); ext.add_argument("--expected-head")
    vc = sub.add_parser("verify-continuation"); vc.add_argument("project_id"); vc.add_argument("--revision")
    rebase = sub.add_parser("rebase-portable-racks"); rebase.add_argument("manifest"); rebase.add_argument("bundle_root"); rebase.add_argument("output_dir"); rebase.add_argument("--actor", default="earcrate"); rebase.add_argument("--reason", default="portable rack relocation"); rebase.add_argument("--overwrite", action="store_true")
    src = sub.add_parser("execute-source-phrase"); src.add_argument("project_id"); src.add_argument("--registration", required=True); src.add_argument("--comparison-reference", required=True); src.add_argument("--output-dir", required=True); src.add_argument("--floor-artifact-key", default="continuation_rack_floor"); src.add_argument("--parent-audition-artifact-key", default="producer_audition"); src.add_argument("--preserve-prefix-seconds", type=float, default=30.0); src.add_argument("--actor", default="producer"); src.add_argument("--reason", default="execute registered SourcePhrase"); src.add_argument("--expected-head"); src.add_argument("--overwrite", action="store_true")
    return parser


def _emit(value: Any) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)); return 0


def _find_command(argv: Sequence[str]) -> str | None:
    values = list(argv); index = 0
    while index < len(values):
        if values[index] == "--root": index += 2; continue
        if values[index].startswith("-"): index += 1; continue
        return values[index]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    command = _find_command(values)
    gate8_commands = set(project_capabilities()["commands"]) | {"capabilities"}
    if command not in gate8_commands:
        from .cli import main as legacy_main
        return int(legacy_main(values))
    args = _parser().parse_args(values)
    if args.command == "capabilities": return _emit(project_capabilities())
    try:
        if args.command == "rebase-portable-racks":
            return _emit(rack_rebase_portable_bundle(args.manifest, args.bundle_root, args.output_dir, overwrite=args.overwrite, actor=args.actor, reason=args.reason))
        store = Gate8ProjectStore(Path(args.root).expanduser().resolve())
        if args.command == "import-causal-score": result = project_import_causal_score(store, name=args.name, family_id=args.family_id, midi_path=args.midi, score_path=args.score, evidence_path=args.evidence, plan_path=args.plan, historical_neutral_render=args.neutral_render, producer_verdict_path=args.producer_verdict, profile=args.profile, producer_status=args.producer_status, actor=args.actor, reason=args.reason, supersedes=args.supersedes, project_id=args.project_id)
        elif args.command == "verify-custody": result = project_verify_custody(store, args.project_id, args.revision)
        elif args.command == "adopt-semantics": result = project_adopt_causal_semantics(store, args.project_id, revision_sha=args.revision, annotations=_json_object(args.annotations, "semantic annotations"), actor=args.actor, reason=args.reason, expected_head=args.expected_head)
        elif args.command == "verify-adoption": result = project_verify_semantic_adoption(store, args.project_id, args.revision)
        elif args.command == "render-causal-score": result = project_render_causal_score(store, args.project_id, args.output, revision_sha=args.revision, overwrite=args.overwrite)
        elif args.command == "library-handshake": result = project_real_library_handshake(store, args.project_id, workspace_config=args.workspace_config, revision_sha=args.revision, taste_profile=args.profile, per_role_limit=args.per_role_limit, maximum_transpose_semitones=args.max_transpose, max_zones_per_slot=args.max_zones, combination_beam_width=args.combination_beam)
        elif args.command == "extend-causal-score": result = project_extend_causal_score(store, args.project_id, score_path=args.score, midi_path=args.midi, evidence_path=args.evidence, semantic_annotations=_json_object(args.annotations, "continuation annotations"), execution_manifest_path=args.execution_manifest, actor=args.actor, reason=args.reason, expected_head=args.expected_head)
        elif args.command == "verify-continuation": result = project_verify_causal_continuation(store, args.project_id, args.revision)
        elif args.command == "execute-source-phrase": result = project_execute_registered_source_phrase(store, args.project_id, registration_path=args.registration, comparison_reference_path=args.comparison_reference, output_dir=args.output_dir, floor_artifact_key=args.floor_artifact_key, parent_audition_artifact_key=args.parent_audition_artifact_key, preserve_prefix_seconds=args.preserve_prefix_seconds, actor=args.actor, reason=args.reason, expected_head=args.expected_head, overwrite=args.overwrite)
        else: raise ValidationError(f"unsupported Gate 8 project command: {args.command}")
        return _emit(result)
    except (ProjectError, ValidationError, FileExistsError, ValueError) as exc:
        print(json.dumps({"ok": False, "command": args.command, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())

__all__ = ["project_capabilities", "main"]
