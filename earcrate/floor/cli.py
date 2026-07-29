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
    floor_build_release_gate,
    floor_human_review_request,
    floor_seal_human_review_request,
    floor_seal_human_musical_review,
    floor_seal_release_candidate,
    floor_seal_release_gate_receipt,
    floor_seal_rights_review,
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
            "review-request",
            "release-gate",
            "verify",
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
            "ReleaseCandidate",
            "HumanMusicalReview",
            "RightsReview",
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

    review_request = subparsers.add_parser("review-request", help="write a human musical review request for a sealed release candidate")
    review_request.add_argument("candidate")
    review_request.add_argument("output")

    release_gate = subparsers.add_parser("release-gate", help="compose conformance, signal, human, and rights evidence without self-approval")
    release_gate.add_argument("candidate")
    release_gate.add_argument("output")
    release_gate.add_argument("--conformance")
    release_gate.add_argument("--signal-evaluation")
    release_gate.add_argument("--human-review")
    release_gate.add_argument("--rights-review")

    verify = subparsers.add_parser("verify", help="validate and reseal one normative Floor object")
    verify.add_argument("path")

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
        if args.command == "review-request":
            candidate = floor_seal_release_candidate(floor_read_json(args.candidate))
            result = floor_human_review_request(candidate)
            floor_write_json_atomic(args.output, result)
            _floor_cli_json(result)
            return 0
        if args.command == "release-gate":
            result = floor_build_release_gate(
                floor_read_json(args.candidate),
                conformance=None if not args.conformance else floor_read_json(args.conformance),
                signal_evaluation=None if not args.signal_evaluation else floor_read_json(args.signal_evaluation),
                human_review=None if not args.human_review else floor_read_json(args.human_review),
                rights_review=None if not args.rights_review else floor_read_json(args.rights_review),
            )
            floor_write_json_atomic(args.output, result)
            _floor_cli_json(result)
            return 0 if result["status_vector"]["release_state"] != "failed" else 3
        if args.command == "verify":
            value = floor_read_json(args.path)
            kind = str(value.get("kind") or "")
            if kind == "earcrate_floor_release_candidate":
                sealed = floor_seal_release_candidate(value)
            elif kind == "earcrate_floor_human_musical_review_request":
                sealed = floor_seal_human_review_request(value)
            elif kind == "earcrate_floor_human_musical_review":
                sealed = floor_seal_human_musical_review(value)
            elif kind == "earcrate_floor_rights_review":
                sealed = floor_seal_rights_review(value)
            elif kind == "earcrate_floor_release_gate_receipt":
                sealed = floor_seal_release_gate_receipt(value)
            else:
                sealed = floor_verify_object(value)
            _floor_cli_json(sealed)
            return 0
        raise FloorError(f"unsupported Floor command: {args.command}")
    except Exception as exc:
        _floor_cli_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(floor_cli_main())


__all__ = ["floor_capability", "floor_cli_main"]
