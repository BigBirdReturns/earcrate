from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .children import (
    CHILDREN_SPECIMEN_ID,
    children_compile_score_branch,
    children_load_bindings,
    children_load_builtin,
)
from .continuation_dense import children_compose_adjacent_move
from .gate import specimen_build_buffalo_gate
from .model import (
    SpecimenError,
    specimen_default_convergence_policy,
    specimen_read_json,
    specimen_sha256_json,
    specimen_write_json_atomic,
)


def specimen_capability() -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "kind": "earcrate_buffalo_gate_capability",
        "ready": True,
        "specimen_ids": [CHILDREN_SPECIMEN_ID],
        "commands": ["capability", "children-bindings", "children-score", "children-continuation", "gate"],
        "branch_isolation": {
            "score": ["score"],
            "audio": ["audio"],
            "convergence": ["score", "audio", "convergence"],
        },
        "score_branch": {
            "custody": True,
            "exact_midi": True,
            "form_graph": True,
            "performance_path": True,
            "music_events": True,
            "harmony_frames": True,
            "mixscore_evidence": True,
            "proof_carrying_adjacent_move": True,
            "rhythmic_identity_obligation": True,
            "pitch_and_harmony_novelty": True,
            "illegal_negative_control": True,
            "continuation_midi_lowering": True,
        },
        "full_gate_requires": [
            "independent audio ObservationLedger",
            "cross-modal convergence",
            "sealed specimen-specific adjacent-move receipt",
            "sealed-rack realization",
            "review-patch selective recomputation",
            "campaign evidence that changes a later decision",
        ],
        "requires_network": False,
        "requires_cloud": False,
        "authority": "Specimen identities, evidence lineage, form, answer keys, convergence reports, and Buffalo Gate receipts remain EarCrate data",
    }
    value["capability_sha256"] = specimen_sha256_json(value)
    return value


def _json(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def _manifest_annotations(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.manifest:
        manifest = specimen_read_json(args.manifest)
        if not args.annotations:
            raise SpecimenError("--annotations is required when --manifest is supplied")
        annotations = specimen_read_json(args.annotations)
        return manifest, annotations
    return children_load_builtin(CHILDREN_SPECIMEN_ID)


def _bindings_template(path: str | Path) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "kind": "earcrate_specimen_bindings",
        "specimen_id": CHILDREN_SPECIMEN_ID,
        "bindings": {
            "score_pdf": "",
            "score_extraction": "",
            "score_reconstruction_midi": "",
            "score_proof_receipt": "",
            "mix_score": "",
            "mix_execution_ledger": "",
            "reference_recording": "",
            "approved_private_library": "",
        },
        "note": "score_annotations is repository-managed; package and standalone modes materialize the exact embedded bytes when needed",
    }
    specimen_write_json_atomic(path, value)
    return {"ok": True, "path": str(Path(path).expanduser().resolve()), "specimen_id": CHILDREN_SPECIMEN_ID}


def specimen_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EarCrate cross-organ Buffalo Gate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capability", help="describe the executable specimen boundary")

    bindings_parser = subparsers.add_parser("children-bindings", help="write a Children external-path template")
    bindings_parser.add_argument("output")

    score_parser = subparsers.add_parser("children-score", help="compile the isolated Children score branch")
    score_parser.add_argument("bindings")
    score_parser.add_argument("output_dir")
    score_parser.add_argument("--manifest")
    score_parser.add_argument("--annotations")
    score_parser.add_argument("--overwrite", action="store_true")

    continuation_parser = subparsers.add_parser(
        "children-continuation",
        help="compose and prove a rhythm-legible Children-adjacent continuation from the sealed score answer key",
    )
    continuation_parser.add_argument("score_dir", help="directory emitted by children-score")
    continuation_parser.add_argument("output_dir")
    continuation_parser.add_argument("--bars", type=int, default=8)
    continuation_parser.add_argument("--sample-rate", type=int, default=8000)
    continuation_parser.add_argument("--overwrite", action="store_true")

    gate_parser = subparsers.add_parser("gate", help="assemble the current Buffalo Gate receipt")
    gate_parser.add_argument("score_dir")
    gate_parser.add_argument("output")
    gate_parser.add_argument("--manifest")
    gate_parser.add_argument("--annotations")
    gate_parser.add_argument("--audio-ledger")
    gate_parser.add_argument("--policy")
    gate_parser.add_argument("--continuation-receipt")
    gate_parser.add_argument("--rack-receipt")
    gate_parser.add_argument("--review-receipt")
    gate_parser.add_argument("--evolution-receipt")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "capability":
            _json(specimen_capability())
            return 0
        if args.command == "children-bindings":
            _json(_bindings_template(args.output))
            return 0
        if args.command == "children-score":
            manifest, annotations = _manifest_annotations(args)
            bindings = children_load_bindings(args.bindings)
            result = children_compile_score_branch(
                manifest=manifest,
                annotations=annotations,
                bindings=bindings,
                output_dir=args.output_dir,
                overwrite=bool(args.overwrite),
            )
            _json(result)
            return 0
        if args.command == "children-continuation":
            score_root = Path(args.score_dir).expanduser().resolve()
            result = children_compose_adjacent_move(
                score_root / "score.answer-key.json",
                args.output_dir,
                bars=int(args.bars),
                sample_rate=int(args.sample_rate),
                overwrite=bool(args.overwrite),
            )
            _json(result)
            return 0
        if args.command == "gate":
            manifest, _annotations = _manifest_annotations(args)
            root = Path(args.score_dir).expanduser().resolve()
            score_ledger = specimen_read_json(root / "score.observation-ledger.json")
            score_receipt = specimen_read_json(root / "score.branch.receipt.json")
            audio = None if not args.audio_ledger else specimen_read_json(args.audio_ledger)
            policy = specimen_default_convergence_policy() if not args.policy else specimen_read_json(args.policy)
            optional = {
                "continuation_receipt": None if not args.continuation_receipt else specimen_read_json(args.continuation_receipt),
                "rack_receipt": None if not args.rack_receipt else specimen_read_json(args.rack_receipt),
                "review_receipt": None if not args.review_receipt else specimen_read_json(args.review_receipt),
                "evolution_receipt": None if not args.evolution_receipt else specimen_read_json(args.evolution_receipt),
            }
            result = specimen_build_buffalo_gate(
                manifest=manifest,
                score_ledger=score_ledger,
                score_branch_receipt=score_receipt,
                output_path=args.output,
                audio_ledger=audio,
                convergence_policy=policy,
                **optional,
            )
            _json(result)
            return 0 if result["overall_status"] != "failed" else 1
        raise SpecimenError(f"unsupported Buffalo Gate command: {args.command}")
    except Exception as exc:
        _json({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(specimen_cli_main())


__all__ = ["specimen_capability", "specimen_cli_main"]
