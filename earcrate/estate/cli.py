from __future__ import annotations

"""Command-line surface for local estate inventory, planning, and acceptance."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from earcrate.estate.discover import redact_estate_inventory, scan_estate
from earcrate.estate.model import (
    default_estate_policy,
    estate_architecture,
    estate_validate_seal,
    load_estate_json,
    validate_estate_policy,
    write_estate_json,
)
from earcrate.estate.plan import (
    apply_estate_plan,
    propose_estate_plan,
    rollback_estate_apply,
    verify_estate_apply,
)
from earcrate.estate.rig import capture_rig_capabilities, propose_local_acceptance_campaign


def _estate_cli_print(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True))


def _estate_cli_load_policy(path: str | None) -> dict[str, Any]:
    return validate_estate_policy(load_estate_json(path)) if path else default_estate_policy()


def _estate_cli_output(payload: Mapping[str, Any], path: str | None) -> None:
    if path:
        write_estate_json(path, payload)
    else:
        _estate_cli_print(payload)


def _estate_cli_summary(kind: str, payload: Mapping[str, Any], output: str | None = None) -> dict[str, Any]:
    field = {
        "earcrate_estate_architecture": "architecture_sha256",
        "earcrate_estate_policy": "policy_sha256",
        "earcrate_estate_inventory": "inventory_sha256",
        "earcrate_estate_plan": "plan_sha256",
        "earcrate_estate_apply_receipt": "receipt_sha256",
        "earcrate_estate_rollback_receipt": "receipt_sha256",
        "earcrate_rig_capability_receipt": "rig_sha256",
        "earcrate_local_acceptance_campaign": "campaign_sha256",
    }[kind]
    return {
        "ok": True,
        "kind": kind,
        field: payload[field],
        "output": output,
        "summary": payload.get("summary"),
    }


def estate_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate estate",
        description="Inventory, reconcile, and locally accept the complete EarCrate estate",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    architecture_parser = sub.add_parser("architecture", help="emit the canonical local-estate architecture")
    architecture_parser.add_argument("--output")

    policy_parser = sub.add_parser("policy", help="emit the safe default estate policy")
    policy_parser.add_argument("--output")

    rig_parser = sub.add_parser("rig", help="capture CPU/GPU/storage/tool capabilities without heavy inference")
    rig_parser.add_argument("--root", action="append", default=[])
    rig_parser.add_argument("--audio-devices", action="store_true")
    rig_parser.add_argument("--output")

    inventory_parser = sub.add_parser("inventory", aliases=["ingest"], help="read-only ingest of explicit local roots into one estate inventory")
    inventory_parser.add_argument("roots", nargs="+")
    inventory_parser.add_argument("--policy")
    inventory_parser.add_argument("--hash-mode", choices=["none", "evidence", "duplicates", "all"])
    inventory_parser.add_argument("--canon")
    inventory_parser.add_argument("--redact", action="store_true")
    inventory_parser.add_argument("--output")

    plan_parser = sub.add_parser("plan", help="propose a non-destructive target architecture and cleanup dispositions")
    plan_parser.add_argument("inventory")
    plan_parser.add_argument("estate_root")
    plan_parser.add_argument("--policy")
    plan_parser.add_argument("--output")

    apply_parser = sub.add_parser("apply", help="copy only strongly identified objects into the managed estate")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--approve", required=True, help="exact plan_sha256")
    apply_parser.add_argument("--output")

    rollback_parser = sub.add_parser("rollback", help="remove only unchanged files created by one apply receipt")
    rollback_parser.add_argument("receipt")
    rollback_parser.add_argument("--approve", required=True, help="exact apply receipt_sha256")
    rollback_parser.add_argument("--output")

    verify_parser = sub.add_parser("verify", help="verify an estate object's seal or a materialized apply receipt")
    verify_parser.add_argument("object")
    verify_parser.add_argument("--materialized", action="store_true")

    campaign_parser = sub.add_parser("campaign", help="propose the local GPU/CPU/private-library/audition acceptance campaign")
    campaign_parser.add_argument("inventory")
    campaign_parser.add_argument("rig")
    campaign_parser.add_argument("--canon")
    campaign_parser.add_argument("--output")

    sweep_parser = sub.add_parser("sweep", help="run inventory, rig capture, architecture plan, and campaign without mutating inputs")
    sweep_parser.add_argument("--root", action="append", required=True)
    sweep_parser.add_argument("--estate-root", required=True)
    sweep_parser.add_argument("--output-dir", required=True)
    sweep_parser.add_argument("--policy")
    sweep_parser.add_argument("--canon")
    sweep_parser.add_argument("--hash-mode", choices=["none", "evidence", "duplicates", "all"])
    sweep_parser.add_argument("--audio-devices", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "architecture":
            payload = estate_architecture()
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "policy":
            payload = default_estate_policy()
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "rig":
            payload = capture_rig_capabilities(roots=args.root, include_audio_devices=args.audio_devices)
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command in {"inventory", "ingest"}:
            policy = _estate_cli_load_policy(args.policy)
            payload = scan_estate(
                args.roots,
                policy=policy,
                hash_mode=args.hash_mode,
                canon_ledger_path=args.canon,
            )
            if args.redact:
                payload = redact_estate_inventory(payload)
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "plan":
            inventory = load_estate_json(args.inventory)
            policy = _estate_cli_load_policy(args.policy)
            payload = propose_estate_plan(inventory, args.estate_root, policy=policy)
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "apply":
            plan = load_estate_json(args.plan)
            payload = apply_estate_plan(plan, approve_sha256=args.approve, receipt_path=args.output)
            _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "rollback":
            receipt = load_estate_json(args.receipt)
            payload = rollback_estate_apply(receipt, approve_sha256=args.approve, output_path=args.output)
            _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "verify":
            payload = load_estate_json(args.object)
            estate_validate_seal(payload)
            if args.materialized:
                result = verify_estate_apply(payload)
            else:
                result = {"ok": True, "kind": payload["kind"], "seal_valid": True}
            _estate_cli_print(result)
            return 0 if result.get("ok") else 1

        if args.command == "campaign":
            inventory = load_estate_json(args.inventory)
            rig = load_estate_json(args.rig)
            payload = propose_local_acceptance_campaign(inventory, rig, canon_ledger_path=args.canon)
            _estate_cli_output(payload, args.output)
            if args.output:
                _estate_cli_print(_estate_cli_summary(payload["kind"], payload, args.output))
            return 0

        if args.command == "sweep":
            policy = _estate_cli_load_policy(args.policy)
            architecture = estate_architecture()
            rig = capture_rig_capabilities(roots=args.root, include_audio_devices=args.audio_devices)
            inventory = scan_estate(
                args.root,
                policy=policy,
                hash_mode=args.hash_mode,
                canon_ledger_path=args.canon,
            )
            plan = propose_estate_plan(inventory, args.estate_root, policy=policy)
            campaign = propose_local_acceptance_campaign(inventory, rig, canon_ledger_path=args.canon)
            output_dir = Path(args.output_dir).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            outputs = {
                "architecture": str(write_estate_json(output_dir / "estate.architecture.json", architecture)),
                "policy": str(write_estate_json(output_dir / "estate.policy.json", policy)),
                "rig": str(write_estate_json(output_dir / "estate.rig.json", rig)),
                "inventory": str(write_estate_json(output_dir / "estate.inventory.json", inventory)),
                "inventory_redacted": str(write_estate_json(output_dir / "estate.inventory.redacted.json", redact_estate_inventory(inventory))),
                "plan": str(write_estate_json(output_dir / "estate.plan.json", plan)),
                "campaign": str(write_estate_json(output_dir / "estate.campaign.json", campaign)),
            }
            _estate_cli_print(
                {
                    "ok": True,
                    "kind": "earcrate_estate_sweep",
                    "output_dir": str(output_dir),
                    "outputs": outputs,
                    "inventory_sha256": inventory["inventory_sha256"],
                    "rig_sha256": rig["rig_sha256"],
                    "plan_sha256": plan["plan_sha256"],
                    "campaign_sha256": campaign["campaign_sha256"],
                    "mutation": "report files only; scanned roots unchanged",
                    "apply_command": [
                        sys.executable,
                        "-m",
                        "earcrate",
                        "estate",
                        "apply",
                        outputs["plan"],
                        "--approve",
                        plan["plan_sha256"],
                    ],
                }
            )
            return 0

        raise ValueError(f"unhandled estate command: {args.command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(estate_cli_main())


__all__ = ["estate_cli_main"]
