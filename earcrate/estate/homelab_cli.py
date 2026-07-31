from __future__ import annotations

"""CLI for the MAME-like EarCrate homelab provider arcade."""

import argparse
import json
import sys
from typing import Any, Mapping, Sequence

from earcrate.estate.homelab import (
    audit_homelab,
    capture_homelab_node,
    decide_homelab_target,
    homelab_catalog,
    homelab_sweep,
    homelab_validate_seal,
    propose_homelab_campaign,
    record_homelab_audition,
    record_homelab_stage,
)
from earcrate.estate.model import load_estate_json, write_estate_json


def _print(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True))


def _load(path: str) -> dict[str, Any]:
    return load_estate_json(path)


def _json_arg(text: str | None, *, label: str) -> dict[str, Any]:
    if not text:
        return {}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must decode to a JSON object")
    return value


def _output(payload: Mapping[str, Any], path: str | None) -> None:
    if path:
        write_estate_json(path, payload)
        identity = next((payload.get(name) for name in ("catalog_sha256", "node_sha256", "audit_sha256", "campaign_sha256", "receipt_sha256", "ledger_sha256", "decision_sha256") if payload.get(name)), None)
        _print({"ok": True, "kind": payload.get("kind"), "identity": identity, "output": path})
    else:
        _print(payload)


def homelab_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate homelab",
        description="Catalog every swept commodity and require real load, fixture, benchmark, audition, and disposition receipts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("catalog", help="emit the complete provider/host/service/standard catalog")
    p.add_argument("--output")

    p = sub.add_parser("node", help="convert one estate rig receipt into a non-executing homelab node audit")
    p.add_argument("rig")
    p.add_argument("--catalog")
    p.add_argument("--output")

    p = sub.add_parser("audit", help="check feasibility and existing receipts without running any target")
    p.add_argument("inventory")
    p.add_argument("nodes", nargs="+")
    p.add_argument("--catalog")
    p.add_argument("--fail-on-blocked", action="store_true")
    p.add_argument("--output")

    p = sub.add_parser("campaign", help="turn the audit into explicit local remediation and audition tasks")
    p.add_argument("audit")
    p.add_argument("--catalog")
    p.add_argument("--fail-on-unresolved", action="store_true")
    p.add_argument("--output")

    p = sub.add_parser("record-stage", help="seal a non-audition stage result")
    p.add_argument("catalog")
    p.add_argument("target_id")
    p.add_argument("stage")
    p.add_argument("node_sha256")
    p.add_argument("status", choices=["passed", "failed", "refused"])
    p.add_argument("--fixture", action="append", default=[])
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--measurements-json")
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("record-audition", help="seal the human reality check for an audio/workflow target")
    p.add_argument("catalog")
    p.add_argument("target_id")
    p.add_argument("node_sha256")
    p.add_argument("reviewer_id")
    p.add_argument("candidate_sha256")
    p.add_argument("control_sha256")
    p.add_argument("verdict", choices=["accept", "reject", "revise", "abstain"])
    p.add_argument("--blinded", action="store_true")
    p.add_argument("--randomized", action="store_true")
    p.add_argument("--playback-json", required=True)
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("decide", help="accept, reject, defer, or retain one target as reference")
    p.add_argument("audit")
    p.add_argument("target_id")
    p.add_argument("decision", choices=["accepted", "rejected", "deferred", "reference_only"])
    p.add_argument("decided_by")
    p.add_argument("reason")
    p.add_argument("--receipt", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("verify", help="verify a catalog, node, audit, campaign, receipt, audition, or decision seal")
    p.add_argument("object")

    p = sub.add_parser("sweep", help="inventory the estate and emit the complete homelab audit/campaign without running providers")
    p.add_argument("--root", action="append", required=True)
    p.add_argument("--estate-root", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--canon")
    p.add_argument("--hash-mode", choices=["none", "evidence", "duplicates", "all"], default="evidence")
    p.add_argument("--audio-devices", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "catalog":
            _output(homelab_catalog(), args.output)
            return 0
        if args.command == "node":
            catalog = _load(args.catalog) if args.catalog else homelab_catalog()
            _output(capture_homelab_node(_load(args.rig), catalog=catalog), args.output)
            return 0
        if args.command == "audit":
            catalog = _load(args.catalog) if args.catalog else homelab_catalog()
            payload = audit_homelab(_load(args.inventory), [_load(path) for path in args.nodes], catalog=catalog)
            _output(payload, args.output)
            return 1 if args.fail_on_blocked and payload["summary"]["blocked_feasibility"] else 0
        if args.command == "campaign":
            catalog = _load(args.catalog) if args.catalog else homelab_catalog()
            payload = propose_homelab_campaign(_load(args.audit), catalog=catalog)
            _output(payload, args.output)
            return 1 if args.fail_on_unresolved and payload["summary"]["unresolved_targets"] else 0
        if args.command == "record-stage":
            payload = record_homelab_stage(
                _load(args.catalog), target_id=args.target_id, stage=args.stage,
                node_sha256=args.node_sha256, status=args.status,
                fixture_ids=args.fixture, artifact_sha256s=args.artifact,
                measurements=_json_arg(args.measurements_json, label="measurements-json"), notes=args.note,
            )
            _output(payload, args.output)
            return 0
        if args.command == "record-audition":
            payload = record_homelab_audition(
                _load(args.catalog), target_id=args.target_id, node_sha256=args.node_sha256,
                reviewer_id=args.reviewer_id, candidate_sha256=args.candidate_sha256,
                control_sha256=args.control_sha256, verdict=args.verdict,
                blinded=args.blinded, randomized=args.randomized,
                playback_chain=_json_arg(args.playback_json, label="playback-json"),
                dimensions=_json_arg(args.dimensions_json, label="dimensions-json"), notes=args.note,
            )
            _output(payload, args.output)
            return 0
        if args.command == "decide":
            payload = decide_homelab_target(
                _load(args.audit), target_id=args.target_id, decision=args.decision,
                decided_by=args.decided_by, reason=args.reason,
                supporting_receipt_sha256s=args.receipt,
            )
            _output(payload, args.output)
            return 0
        if args.command == "verify":
            payload = _load(args.object)
            homelab_validate_seal(payload)
            _print({"ok": True, "kind": payload["kind"], "seal_valid": True})
            return 0
        if args.command == "sweep":
            _print(homelab_sweep(
                args.root, estate_root=args.estate_root, output_dir=args.output_dir,
                canon_ledger_path=args.canon, hash_mode=args.hash_mode,
                include_audio_devices=args.audio_devices,
            ))
            return 0
        raise ValueError(f"unhandled homelab command: {args.command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(homelab_cli_main())


__all__ = ["homelab_cli_main"]
