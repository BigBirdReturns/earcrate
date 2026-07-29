from __future__ import annotations

"""CLI for the EarCrate Open Music Evidence Floor."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .adapters import floor_earcrate_provider_manifests
from .catalog import floor_discover_provider_catalog
from .gaps import floor_gap_register
from .interop import floor_export_crate
from .model import (
    FloorError,
    FloorProtocolError,
    floor_read_json,
    floor_sha256_json,
    floor_verify_object,
    floor_write_json_atomic,
)
from .protocol import floor_conformance_run, floor_invoke_provider
from .reference import floor_write_reference_provider
from .release import (
    floor_adapt_source_only_recurrence_receipt,
    floor_build_release_gate,
    floor_release_profile_capability,
    floor_release_review_template,
    floor_verify_release_object,
)
from .schema import floor_schema_bundle, floor_write_schema_bundle
from .tournament import floor_run_tournament


def floor_capability() -> dict[str, Any]:
    gaps = floor_gap_register()
    value = {
        "schema_version": 1,
        "kind": "earcrate_open_music_evidence_floor_capability",
        "ready": True,
        "protocol": {
            "name": "earcrate-floor-stdio-json",
            "version": 1,
            "wire": {
                "stdin": "one sealed ProviderRequest JSON object",
                "stdout": "one ProviderResult JSON object and no log text",
                "stderr": "diagnostics",
                "artifacts": "files beneath FLOOR_ARTIFACT_DIR",
            },
        },
        "commands": [
            "capability",
            "gaps",
            "schemas",
            "scaffold",
            "catalog",
            "invoke",
            "conformance",
            "tournament",
            "crate",
            "verify",
            "release-capability",
            "release-adapt-recurrence",
            "release-review-template",
            "release-gate",
        ],
        "normative_objects": [
            "ProviderManifest",
            "ProviderRequest",
            "ProviderResult",
            "TimeMap",
            "PhraseContract",
            "RightsEnvelope",
            "ReviewPatch",
            "InvocationReceipt",
            "EvaluationPolicy",
            "EvaluationLedger",
            "TournamentReport",
            "FloorCrate",
            "AudioEditPlan",
            "ReleaseCandidate",
            "SignalEvaluation",
            "HumanMusicalReview",
            "ReleaseGateReceipt",
        ],
        "provider_may_emit": [
            "observation",
            "candidate",
            "measurement",
            "refusal",
            "derived_artifact",
            "unapplied review patch",
        ],
        "provider_may_not_claim": [
            "canonical musical state",
            "applied review patch",
            "legal determination",
            "whole-organism passage",
            "tournament winner as truth",
        ],
        "security_boundary": {
            "input_hashes_verified": True,
            "output_hashes_verified": True,
            "artifact_paths_contained": True,
            "symlinks_refused": True,
            "shell_used": False,
            "network_policy_declared": True,
            "os_network_sandbox_proved": False,
            "os_resource_sandbox_proved": False,
        },
        "conformance_is_quality": False,
        "catalog_is_selection": False,
        "tournament_winner_is_canonical": False,
        "source_media_copied_by_crate_default": False,
        "release_candidate_profile": floor_release_profile_capability(),
        "existing_earcrate_provider_adapter_count": len(floor_earcrate_provider_manifests()),
        "gap_status_counts": gaps["counts"],
        "requires_network": False,
        "requires_cloud": False,
    }
    value["capability_sha256"] = floor_sha256_json(value)
    return value


def _floor_cli_json(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def _floor_cli_empty_dir(path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise FloorError(f"refusing nonempty output directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def floor_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate floor",
        description="EarCrate Open Music Evidence Floor: portable provider custody, authority, evaluation, and crates",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capability", help="describe the protocol and authority boundary")
    subparsers.add_parser("gaps", help="emit the executable interoperability gap register")

    schemas = subparsers.add_parser("schemas", help="write the committed Floor JSON Schema bundle")
    schemas.add_argument("output_dir")

    scaffold = subparsers.add_parser("scaffold", help="write a movable third-party reference provider")
    scaffold.add_argument("output_dir")

    catalog = subparsers.add_parser("catalog", help="discover provider manifests and optionally filter compatibility")
    catalog.add_argument("paths", nargs="+")
    catalog.add_argument("--request")
    catalog.add_argument("--no-earcrate-adapters", action="store_true")

    invoke = subparsers.add_parser("invoke", help="run one stdio JSON provider with custody receipts")
    invoke.add_argument("manifest")
    invoke.add_argument("request")
    invoke.add_argument("output_dir")
    invoke.add_argument("--timeout", type=int, default=0)

    conformance = subparsers.add_parser("conformance", help="run repeatable protocol conformance")
    conformance.add_argument("manifest")
    conformance.add_argument("request")
    conformance.add_argument("output_dir")
    conformance.add_argument("--repeat", type=int, default=2)

    tournament = subparsers.add_parser("tournament", help="rank independent evaluation ledgers under a sealed policy")
    tournament.add_argument("policy")
    tournament.add_argument("evaluations", nargs="+")
    tournament.add_argument("--output")

    crate = subparsers.add_parser("crate", help="export a checksummed Floor/JAMS/PROV/ODRL/RO-Crate bundle")
    crate.add_argument("manifest")
    crate.add_argument("request")
    crate.add_argument("result")
    crate.add_argument("receipt")
    crate.add_argument("output_dir")
    crate.add_argument("--artifact-root")
    crate.add_argument("--copy-derived", action="store_true")

    verify = subparsers.add_parser("verify", help="validate and reseal one normative Floor object")
    verify.add_argument("path")

    subparsers.add_parser("release-capability", help="describe the reviewed release-candidate profile")

    release_adapt = subparsers.add_parser(
        "release-adapt-recurrence",
        help="adapt one source-only recurrence receipt into candidate, signal, review, and pending gate objects",
    )
    release_adapt.add_argument("receipt")
    release_adapt.add_argument("output_dir")
    release_adapt.add_argument("--builder-id", default="org.earcrate.release.recurrence-builder-v1")
    release_adapt.add_argument("--builder-version", default="1.0.0")
    release_adapt.add_argument("--signal-evaluator-id", default="org.earcrate.release.signal-evaluator-v1")
    release_adapt.add_argument("--signal-evaluator-version", default="1.0.0")

    release_review = subparsers.add_parser(
        "release-review-template", help="write an unapplied human musical review template for a candidate"
    )
    release_review.add_argument("candidate")
    release_review.add_argument("output")
    release_review.add_argument("--reviewer-id", default="unassigned-human-reviewer")

    release_gate = subparsers.add_parser(
        "release-gate", help="assemble the current release gate from independent signal, human, custody, reproducibility, and rights evidence"
    )
    release_gate.add_argument("candidate")
    release_gate.add_argument("output")
    release_gate.add_argument("--signal-evaluation", action="append", default=[])
    release_gate.add_argument("--human-review", action="append", default=[])
    release_gate.add_argument("--custody", choices=["pending", "passed", "failed"], default="pending")
    release_gate.add_argument("--reproducibility", choices=["not_run", "passed", "failed"], default="not_run")
    release_gate.add_argument(
        "--rights-status", choices=["not_evaluated", "accepted_by_policy", "blocked", "expired"], default="not_evaluated"
    )
    release_gate.add_argument("--rights-policy", default="")
    release_gate.add_argument("--rights-decided-by", default="")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "capability":
            _floor_cli_json(floor_capability())
            return 0
        if args.command == "gaps":
            _floor_cli_json(floor_gap_register())
            return 0
        if args.command == "schemas":
            _floor_cli_json(floor_write_schema_bundle(args.output_dir))
            return 0
        if args.command == "scaffold":
            _floor_cli_json(floor_write_reference_provider(args.output_dir))
            return 0
        if args.command == "catalog":
            request = None if not args.request else floor_read_json(args.request)
            _floor_cli_json(
                floor_discover_provider_catalog(
                    args.paths,
                    request=request,
                    include_earcrate_adapters=not bool(args.no_earcrate_adapters),
                )
            )
            return 0
        if args.command == "invoke":
            output = _floor_cli_empty_dir(args.output_dir)
            request = floor_read_json(args.request)
            run = floor_invoke_provider(
                args.manifest,
                request,
                artifact_dir=output / "artifacts",
                timeout_seconds=None if int(args.timeout) <= 0 else int(args.timeout),
            )
            floor_write_json_atomic(output / "result.json", run["result"])
            floor_write_json_atomic(output / "invocation.receipt.json", run["receipt"])
            _floor_cli_json(
                {
                    "ok": True,
                    "output_dir": str(output),
                    "artifact_dir": run["artifact_dir"],
                    "result_path": str(output / "result.json"),
                    "receipt_path": str(output / "invocation.receipt.json"),
                    "result_sha256": run["result"]["result_sha256"],
                    "receipt_sha256": run["receipt"]["receipt_sha256"],
                }
            )
            return 0
        if args.command == "conformance":
            report = floor_conformance_run(
                args.manifest,
                floor_read_json(args.request),
                output_dir=args.output_dir,
                repeat=int(args.repeat),
            )
            _floor_cli_json(report)
            return 0 if report["complete"] else 3
        if args.command == "tournament":
            report = floor_run_tournament(
                floor_read_json(args.policy),
                [floor_read_json(path) for path in args.evaluations],
            )
            if args.output:
                floor_write_json_atomic(args.output, report)
            _floor_cli_json(report)
            return 0 if report["winner"] is not None else 3
        if args.command == "crate":
            result = floor_export_crate(
                manifest_value=floor_read_json(args.manifest),
                request_value=floor_read_json(args.request),
                result_value=floor_read_json(args.result),
                receipt_value=floor_read_json(args.receipt),
                output_dir=args.output_dir,
                artifact_root=args.artifact_root,
                copy_derived=bool(args.copy_derived),
            )
            _floor_cli_json(result)
            return 0
        if args.command == "release-capability":
            _floor_cli_json(floor_release_profile_capability())
            return 0
        if args.command == "release-adapt-recurrence":
            output = _floor_cli_empty_dir(args.output_dir)
            adapted = floor_adapt_source_only_recurrence_receipt(
                floor_read_json(args.receipt),
                builder={
                    "identity_id": args.builder_id,
                    "identity_type": "provider",
                    "version": args.builder_version,
                    "display_name": "EarCrate source-only recurrence builder",
                },
                signal_evaluator={
                    "identity_id": args.signal_evaluator_id,
                    "identity_type": "evaluator",
                    "version": args.signal_evaluator_version,
                    "display_name": "EarCrate release signal evaluator",
                },
            )
            names = {
                "audio_edit_plan": "audio_edit_plan.json",
                "time_map": "time_map.json",
                "phrase_contract": "phrase_contract.json",
                "release_candidate": "release_candidate.json",
                "signal_evaluation": "signal_evaluation.json",
                "human_review_template": "human_review.template.json",
                "release_gate": "release_gate.pending.json",
            }
            for key, name in names.items():
                floor_write_json_atomic(output / name, adapted[key])
            _floor_cli_json(
                {
                    "ok": True,
                    "output_dir": str(output),
                    "candidate_sha256": adapted["release_candidate"]["candidate_sha256"],
                    "signal_evaluation_sha256": adapted["signal_evaluation"]["signal_evaluation_sha256"],
                    "release_gate_sha256": adapted["release_gate"]["release_gate_sha256"],
                    "status": adapted["release_gate"]["status"],
                    "files": [str(output / name) for name in names.values()],
                }
            )
            return 0
        if args.command == "release-review-template":
            review = floor_release_review_template(floor_read_json(args.candidate), reviewer_id=args.reviewer_id)
            floor_write_json_atomic(args.output, review)
            _floor_cli_json({"ok": True, "output": str(Path(args.output).expanduser().resolve()), "review": review})
            return 0
        if args.command == "release-gate":
            gate = floor_build_release_gate(
                floor_read_json(args.candidate),
                signal_evaluations=[floor_read_json(path) for path in args.signal_evaluation],
                human_reviews=[floor_read_json(path) for path in args.human_review],
                custody={"status": args.custody},
                reproducibility={"status": args.reproducibility},
                rights={
                    "status": args.rights_status,
                    "policy_id": args.rights_policy,
                    "decided_by": args.rights_decided_by,
                    "legal_determination": False,
                },
            )
            floor_write_json_atomic(args.output, gate)
            _floor_cli_json(gate)
            return 0 if gate["release_allowed"] else 3
        if args.command == "verify":
            value = floor_read_json(args.path)
            try:
                sealed = floor_verify_object(value)
            except FloorError as exc:
                if "unsupported Floor object kind" not in str(exc):
                    raise
                sealed = floor_verify_release_object(value)
            _floor_cli_json(sealed)
            return 0
        raise FloorError(f"unsupported Floor command: {args.command}")
    except Exception as exc:
        _floor_cli_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(floor_cli_main())


__all__ = ["floor_capability", "floor_cli_main"]
