from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters import floor_builtin_provider_snapshot
from .catalog import floor_discover_provider_catalog
from .interop import floor_export_crate
from .model import (
    K_MANIFEST,
    K_PHRASE,
    K_POLICY,
    K_RECEIPT,
    K_REQUEST,
    K_REVIEW,
    K_RIGHTS,
    K_TIME_MAP,
    FloorError,
    floor_capability,
    floor_read_json,
    floor_schema_bundle,
    floor_seal_evaluation_policy,
    floor_seal_invocation_receipt,
    floor_seal_phrase_contract,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_seal_review_patch,
    floor_seal_rights_envelope,
    floor_seal_time_map,
    floor_write_json_atomic,
)
from .protocol import floor_invoke_provider
from .reference import floor_run_reference_demo, floor_write_reference_provider
from .tournament import floor_default_evaluation_policy, floor_run_tournament


def _emit(value: Any, *, stream: Any = sys.stdout) -> None:
    # ASCII output remains safe on legacy Windows consoles while preserving a
    # lossless JSON wire representation.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False), file=stream)


def floor_export_schemas(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, schema in sorted(floor_schema_bundle().items()):
        path = root / name
        floor_write_json_atomic(path, schema)
        rows.append({"name": name, "path": str(path)})
    return {"ok": True, "complete": True, "output_dir": str(root), "schema_count": len(rows), "schemas": rows}


def _validate(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    raw = floor_read_json(source)
    kind = str(raw.get("kind") or "")
    if kind == K_MANIFEST:
        sealed = floor_seal_provider_manifest(raw)
    elif kind == K_REQUEST:
        sealed = floor_seal_provider_request(raw)
    elif kind == K_TIME_MAP:
        sealed = floor_seal_time_map(raw)
    elif kind == K_PHRASE:
        sealed = floor_seal_phrase_contract(raw)
    elif kind == K_RIGHTS:
        sealed = floor_seal_rights_envelope(raw)
    elif kind == K_REVIEW:
        sealed = floor_seal_review_patch(raw)
    elif kind == K_RECEIPT:
        sealed = floor_seal_invocation_receipt(raw)
    elif kind == K_POLICY:
        sealed = floor_seal_evaluation_policy(raw)
    else:
        raise FloorError(
            "validate supports standalone manifests, requests, time maps, phrase contracts, "
            "rights envelopes, review patches, receipts, and evaluation policies"
        )
    return {"ok": True, "complete": True, "path": str(source), "kind": kind, "sealed": sealed}


def _request_for_catalog(path: str | None) -> Mapping[str, Any] | None:
    return None if not path else floor_read_json(path)


def floor_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate floor",
        description="EarCrate Open Music Evidence Floor provider and conformance protocol",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capability", help="describe the normative protocol boundary")
    sub.add_parser("adapters", help="project existing EarCrate providers into Floor manifests")

    schemas = sub.add_parser("schemas", help="write the normative JSON Schema bundle")
    schemas.add_argument("output")

    scaffold = sub.add_parser("scaffold", help="write a movable standard-library reference provider")
    scaffold.add_argument("output")
    scaffold.add_argument("--overwrite", action="store_true")

    demo = sub.add_parser("demo", help="run the reference provider twice and export a crate")
    demo.add_argument("output")
    demo.add_argument("--overwrite", action="store_true")

    catalog = sub.add_parser("catalog", help="discover provider manifests without trusting or selecting them")
    catalog.add_argument("roots", nargs="+")
    catalog.add_argument("--request")

    invoke = sub.add_parser("invoke", help="run one stdio-json-v1 provider under exact custody")
    invoke.add_argument("manifest")
    invoke.add_argument("request")
    invoke.add_argument("output")
    invoke.add_argument("--repeat", type=int, default=1)
    invoke.add_argument("--timeout", type=int, default=None)
    invoke.add_argument("--require-repeatability", action="store_true")
    invoke.add_argument("--overwrite", action="store_true")

    crate = sub.add_parser("export-crate", help="export one verified invocation as a portable Floor crate")
    crate.add_argument("manifest")
    crate.add_argument("request")
    crate.add_argument("result")
    crate.add_argument("receipt")
    crate.add_argument("output")
    crate.add_argument("--artifact-root")
    crate.add_argument("--include-derived", action="store_true")
    crate.add_argument("--overwrite", action="store_true")

    tournament = sub.add_parser("tournament", help="rank independently evaluated candidates lexicographically")
    tournament.add_argument("candidates", help="JSON object with candidates, or a JSON array")
    tournament.add_argument("--policy")
    tournament.add_argument("--output")

    validate = sub.add_parser("validate", help="validate and reseal one standalone Floor object")
    validate.add_argument("path")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "capability":
            _emit(floor_capability())
            return 0
        if args.command == "adapters":
            _emit(floor_builtin_provider_snapshot())
            return 0
        if args.command == "schemas":
            _emit(floor_export_schemas(args.output))
            return 0
        if args.command == "scaffold":
            _emit(floor_write_reference_provider(args.output, overwrite=bool(args.overwrite)))
            return 0
        if args.command == "demo":
            _emit(floor_run_reference_demo(args.output, overwrite=bool(args.overwrite)))
            return 0
        if args.command == "catalog":
            _emit(floor_discover_provider_catalog(args.roots, request=_request_for_catalog(args.request)))
            return 0
        if args.command == "invoke":
            _emit(
                floor_invoke_provider(
                    args.manifest,
                    args.request,
                    args.output,
                    repeat=int(args.repeat),
                    require_repeatability=True if args.require_repeatability else None,
                    timeout_seconds=args.timeout,
                    overwrite=bool(args.overwrite),
                )
            )
            return 0
        if args.command == "export-crate":
            _emit(
                floor_export_crate(
                    manifest=args.manifest,
                    request=args.request,
                    result=args.result,
                    receipt=args.receipt,
                    output_dir=args.output,
                    artifact_root=args.artifact_root,
                    include_derived_artifacts=bool(args.include_derived),
                    overwrite=bool(args.overwrite),
                )
            )
            return 0
        if args.command == "tournament":
            payload = json.loads(Path(args.candidates).expanduser().read_text(encoding="utf-8"))
            candidates = payload.get("candidates") if isinstance(payload, dict) else payload
            if not isinstance(candidates, list):
                raise FloorError("tournament candidate file must be an array or contain a candidates array")
            policy = floor_default_evaluation_policy() if not args.policy else floor_read_json(args.policy)
            result = floor_run_tournament(policy=policy, candidates=candidates)
            if args.output:
                floor_write_json_atomic(args.output, result)
            _emit(result)
            return 0
        if args.command == "validate":
            _emit(_validate(args.path))
            return 0
        raise FloorError(f"unsupported Floor command: {args.command}")
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(floor_cli_main())


__all__ = ["floor_cli_main", "floor_export_schemas"]
