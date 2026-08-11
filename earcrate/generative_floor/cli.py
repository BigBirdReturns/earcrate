from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import Sequence

from .catalog import (
    build_generation_request,
    compile_generation_campaign,
    probe_provider,
    provider_map,
    validate_provider_catalog,
)
from .core import load_json, write_json
from .execution import (
    build_generation_frontier,
    build_public_projection,
    execute_generation_request,
    generated_material_from_receipt,
)


def cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="earcrate-generative-floor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-catalog")
    p.add_argument("--catalog", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--catalog", required=True)
    p.add_argument("--provider", required=True)
    p.add_argument("--override")
    p.add_argument("--output", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--catalog", required=True)
    p.add_argument("--campaign", required=True)
    p.add_argument("--probe", action="append", default=[])
    p.add_argument("--output", required=True)

    p = sub.add_parser("request")
    p.add_argument("--provider", required=True)
    p.add_argument("--task-mode", required=True)
    p.add_argument("--model-repository", required=True)
    p.add_argument("--model-revision", required=True)
    p.add_argument("--asset", action="append", required=True, help="name:sha256:bytes")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--prompt-json", required=True)
    p.add_argument("--conditioning-json", default="[]")
    p.add_argument("--output", required=True)

    p = sub.add_parser("run")
    p.add_argument("--catalog", required=True)
    p.add_argument("--provider", required=True)
    p.add_argument("--probe", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--source-bindings", required=True, help="private JSON mapping source_id to artifact_path")
    p.add_argument("--node-json", required=True)
    p.add_argument("--gpu-json")
    p.add_argument("--output", required=True)

    p = sub.add_parser("materialize")
    p.add_argument("--receipt", required=True)
    p.add_argument("--artifact-sha256", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--musical-function", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--output", required=True)

    p = sub.add_parser("frontier")
    p.add_argument("--material", action="append", default=[])
    p.add_argument("--incumbent-json")
    p.add_argument("--maximum-options", type=int, default=4)
    p.add_argument("--output", required=True)

    p = sub.add_parser("public-project")
    p.add_argument("--object", action="append", required=True)
    p.add_argument("--output", required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "validate-catalog":
            value = load_json(args.catalog)
            print(json.dumps({"ok": True, "catalog_sha256": validate_provider_catalog(value)}, indent=2))
            return 0
        if args.command == "probe":
            catalog = load_json(args.catalog)
            provider = provider_map(catalog)[args.provider]
            override = load_json(args.override) if args.override else {}
            probe = probe_provider(provider, local_override=override)
            write_json(args.output, probe, exclusive=True)
            print(json.dumps({"ok": True, "probe_sha256": probe["probe_sha256"], "ready": probe["ready"], "blockers": probe["blockers"]}, indent=2))
            return 0
        if args.command == "plan":
            catalog = load_json(args.catalog)
            campaign = load_json(args.campaign)
            probes = [load_json(path) for path in args.probe]
            plan = compile_generation_campaign(catalog=catalog, campaign_spec=campaign, provider_probes=probes)
            write_json(args.output, plan, exclusive=True)
            print(json.dumps({"ok": True, "campaign_sha256": plan["campaign_sha256"], "summary": plan["summary"]}, indent=2))
            return 0
        if args.command == "request":
            assets = []
            for raw in args.asset:
                name, digest, byte_count = raw.split(":", 2)
                assets.append({"name": name, "sha256": digest, "bytes": int(byte_count)})
            request_value = build_generation_request(
                provider_id=args.provider,
                task_mode=args.task_mode,
                model_repository=args.model_repository,
                model_revision=args.model_revision,
                model_assets=assets,
                seed=args.seed,
                prompt=json.loads(args.prompt_json),
                conditioning=json.loads(args.conditioning_json),
            )
            write_json(args.output, request_value, exclusive=True)
            print(json.dumps({"ok": True, "request_sha256": request_value["request_sha256"]}, indent=2))
            return 0
        if args.command == "run":
            catalog = load_json(args.catalog)
            provider = provider_map(catalog)[args.provider]
            probe = load_json(args.probe)
            request_value = load_json(args.request)
            adapter_value = load_json(args.adapter)
            adapter = dict(adapter_value.get("adapter") or adapter_value)
            source_bindings = load_json(args.source_bindings)
            paths = dict(source_bindings.get("sources") or source_bindings.get("bindings") or source_bindings)
            if any(isinstance(value, Mapping) for value in paths.values()):
                paths = {key: value.get("artifact_path") for key, value in paths.items()}
            receipt = execute_generation_request(
                request_value,
                provider=provider,
                probe=probe,
                local_adapter=adapter,
                private_source_paths=paths,
                output_directory=args.output,
                node_identity=load_json(args.node_json),
                gpu_identity=load_json(args.gpu_json) if args.gpu_json else None,
            )
            print(json.dumps({"ok": receipt.get("outcome") == "observed", "receipt_sha256": receipt["receipt_sha256"], "outcome": receipt["outcome"], "artifacts": receipt["artifacts"]}, indent=2))
            return 0 if receipt.get("outcome") == "observed" else 3
        if args.command == "materialize":
            material = generated_material_from_receipt(
                load_json(args.receipt),
                artifact_sha256=args.artifact_sha256,
                role=args.role,
                musical_function=args.musical_function,
                generation_strategy=args.strategy,
            )
            write_json(args.output, material, exclusive=True)
            print(json.dumps({"ok": True, "material_sha256": material["material_sha256"]}, indent=2))
            return 0
        if args.command == "frontier":
            materials = [load_json(path) for path in args.material]
            incumbent = json.loads(args.incumbent_json) if args.incumbent_json else None
            frontier = build_generation_frontier(materials, incumbent=incumbent, maximum_options=args.maximum_options)
            write_json(args.output, frontier, exclusive=True)
            print(json.dumps({"ok": True, "frontier_sha256": frontier["frontier_sha256"], "entries": len(frontier["entries"])}, indent=2))
            return 0
        if args.command == "public-project":
            projection = build_public_projection([load_json(path) for path in args.object])
            write_json(args.output, projection, exclusive=True)
            print(json.dumps({"ok": True, "projection_sha256": projection["projection_sha256"], "entries": len(projection["entries"])}, indent=2))
            return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_main())
