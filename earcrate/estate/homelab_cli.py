from __future__ import annotations

"""CLI for the MAME-like EarCrate homelab provider arcade."""

import argparse
import json
import os
from pathlib import Path
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
from earcrate.estate.homelab_ops import (
    backup_homelab_store,
    export_public_store,
    render_homelab_dashboard,
    restore_homelab_backup,
)
from earcrate.estate.homelab_review import adjudicate_review, prepare_blind_review, record_review_submission
from earcrate.estate.homelab_store import HomelabStore
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
        identity = next(
            (
                payload.get(name)
                for name in (
                    "catalog_sha256",
                    "node_sha256",
                    "audit_sha256",
                    "campaign_sha256",
                    "receipt_sha256",
                    "ledger_sha256",
                    "decision_sha256",
                    "assignment_sha256",
                    "authority_sha256",
                    "submission_sha256",
                    "snapshot_sha256",
                    "manifest_sha256",
                )
                if payload.get(name)
            ),
            None,
        )
        _print({"ok": True, "kind": payload.get("kind"), "identity": identity, "output": path})
    else:
        _print(payload)


def _write_secret(path: str | Path, value: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with __import__("contextlib").suppress(Exception):
        target.chmod(0o600)
    return target


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

    p = sub.add_parser("record-audition", help="seal a previously adjudicated human reality check")
    p.add_argument("catalog")
    p.add_argument("target_id")
    p.add_argument("node_sha256")
    p.add_argument("reviewer_id")
    p.add_argument("candidate_sha256")
    p.add_argument("control_sha256")
    p.add_argument("verdict", choices=["accept", "reject", "revise", "abstain"])
    p.add_argument("--fixture", action="append", default=[])
    p.add_argument("--blinded", action="store_true")
    p.add_argument("--randomized", action="store_true")
    p.add_argument("--playback-json", required=True)
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("review-prepare", help="prepare committed randomized A/B files and a separate private authority")
    p.add_argument("catalog")
    p.add_argument("target_id")
    p.add_argument("node_sha256")
    p.add_argument("reviewer_id")
    p.add_argument("candidate")
    p.add_argument("control")
    p.add_argument("--fixture", action="append", default=[])
    p.add_argument("--playback-json", required=True)
    p.add_argument("--public-dir", required=True)
    p.add_argument("--private-dir", required=True)
    p.add_argument("--token-output", help="private token file; defaults beneath --private-dir")

    p = sub.add_parser("review-submit", help="record an A/B choice without exposing the private option map")
    p.add_argument("assignment")
    p.add_argument("reviewer_id")
    p.add_argument("review_token_file")
    p.add_argument("choice", choices=["A", "B", "tie", "abstain"])
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--auth-receipt")
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("review-adjudicate", help="combine public assignment, private authority, and submission into an audition ledger")
    p.add_argument("catalog")
    p.add_argument("assignment")
    p.add_argument("private_authority")
    p.add_argument("submission")
    p.add_argument("--output")

    p = sub.add_parser("decide", help="accept, reject, defer, or retain one target as reference")
    p.add_argument("audit")
    p.add_argument("target_id")
    p.add_argument("decision", choices=["accepted", "rejected", "deferred", "reference_only"])
    p.add_argument("decided_by")
    p.add_argument("reason")
    p.add_argument("--receipt", action="append", default=[])
    p.add_argument("--output")

    p = sub.add_parser("verify", help="verify a Homelab object seal")
    p.add_argument("object")

    p = sub.add_parser("sweep", help="inventory the estate and emit the complete homelab audit/campaign without running providers")
    p.add_argument("--root", action="append", required=True)
    p.add_argument("--estate-root", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--canon")
    p.add_argument("--hash-mode", choices=["none", "evidence", "duplicates", "all"], default="evidence")
    p.add_argument("--audio-devices", action="store_true")

    p = sub.add_parser("store-init", help="initialize and verify a durable Homelab store")
    p.add_argument("store")

    p = sub.add_parser("store-ingest", help="ingest one sealed object into the durable store")
    p.add_argument("store")
    p.add_argument("object")
    p.add_argument("--visibility", choices=["public", "private", "sensitive"], default="public")

    p = sub.add_parser("store-doctor", help="verify SQLite, event chain, objects, tasks, and leases")
    p.add_argument("store")
    p.add_argument("--skip-object-verification", action="store_true")

    p = sub.add_parser("store-snapshot", help="emit a sealed source-free store summary")
    p.add_argument("store")
    p.add_argument("--include-private-counts", action="store_true")
    p.add_argument("--output")

    p = sub.add_parser("campaign-register", help="persist a Homelab campaign and its dependency graph")
    p.add_argument("store")
    p.add_argument("campaign")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=3)

    p = sub.add_parser("task-lease", help="lease the next dependency-ready task")
    p.add_argument("store")
    p.add_argument("worker_id")
    p.add_argument("--resource", action="append", default=[])
    p.add_argument("--campaign")
    p.add_argument("--lease-seconds", type=int, default=900)
    p.add_argument("--token-output", help="write the lease token to a private file instead of stdout")

    p = sub.add_parser("task-heartbeat", help="extend a live task lease")
    p.add_argument("store")
    p.add_argument("campaign_sha256")
    p.add_argument("task_id")
    p.add_argument("lease_token_file")
    p.add_argument("--extend-seconds", type=int, default=900)

    p = sub.add_parser("task-complete", help="complete, fail, refuse, or cancel a leased task")
    p.add_argument("store")
    p.add_argument("campaign_sha256")
    p.add_argument("task_id")
    p.add_argument("lease_token_file")
    p.add_argument("outcome", choices=["completed", "failed", "refused", "cancelled"])
    p.add_argument("--evidence")
    p.add_argument("--error")

    p = sub.add_parser("campaign-cancel", help="cancel an active campaign and release its leases")
    p.add_argument("store")
    p.add_argument("campaign_sha256")
    p.add_argument("reason")

    p = sub.add_parser("public-export", help="export public objects and a source-free store snapshot")
    p.add_argument("store")
    p.add_argument("destination")

    p = sub.add_parser("backup", help="create a verified private store backup")
    p.add_argument("store")
    p.add_argument("output_zip")
    p.add_argument("--acknowledge-private-state", action="store_true")

    p = sub.add_parser("restore", help="restore a verified backup into a new destination")
    p.add_argument("backup_zip")
    p.add_argument("destination")
    p.add_argument("--approve", required=True)
    p.add_argument("--output")

    p = sub.add_parser("dashboard", help="write a static source-free audit/campaign dashboard")
    p.add_argument("audit")
    p.add_argument("campaign")
    p.add_argument("output_html")

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
            _output(
                record_homelab_stage(
                    _load(args.catalog),
                    target_id=args.target_id,
                    stage=args.stage,
                    node_sha256=args.node_sha256,
                    status=args.status,
                    fixture_ids=args.fixture,
                    artifact_sha256s=args.artifact,
                    measurements=_json_arg(args.measurements_json, label="measurements-json"),
                    notes=args.note,
                ),
                args.output,
            )
            return 0
        if args.command == "record-audition":
            _output(
                record_homelab_audition(
                    _load(args.catalog),
                    target_id=args.target_id,
                    node_sha256=args.node_sha256,
                    reviewer_id=args.reviewer_id,
                    candidate_sha256=args.candidate_sha256,
                    control_sha256=args.control_sha256,
                    verdict=args.verdict,
                    blinded=args.blinded,
                    randomized=args.randomized,
                    playback_chain=_json_arg(args.playback_json, label="playback-json"),
                    dimensions=_json_arg(args.dimensions_json, label="dimensions-json"),
                    fixture_ids=args.fixture,
                    notes=args.note,
                ),
                args.output,
            )
            return 0
        if args.command == "review-prepare":
            result = prepare_blind_review(
                _load(args.catalog),
                target_id=args.target_id,
                node_sha256=args.node_sha256,
                reviewer_id=args.reviewer_id,
                candidate_path=args.candidate,
                control_path=args.control,
                fixture_ids=args.fixture,
                playback_chain=_json_arg(args.playback_json, label="playback-json"),
                public_directory=args.public_dir,
                private_directory=args.private_dir,
            )
            token_output = Path(args.token_output).expanduser() if args.token_output else Path(result["private_directory"]) / "review-token.txt"
            _write_secret(token_output, result["review_token"])
            _print(
                {
                    "ok": True,
                    "assignment_sha256": result["assignment"]["assignment_sha256"],
                    "private_authority_sha256": result["private_authority"]["authority_sha256"],
                    "public_directory": result["public_directory"],
                    "private_directory": result["private_directory"],
                    "review_token_file": str(token_output),
                    "review_token_exposed_on_stdout": False,
                    "boundary": result["boundary"],
                }
            )
            return 0
        if args.command == "review-submit":
            _output(
                record_review_submission(
                    _load(args.assignment),
                    reviewer_id=args.reviewer_id,
                    review_token=Path(args.review_token_file).read_text(encoding="utf-8").strip(),
                    choice=args.choice,
                    dimensions=_json_arg(args.dimensions_json, label="dimensions-json"),
                    notes=args.note,
                    authentication_receipt_sha256=args.auth_receipt,
                ),
                args.output,
            )
            return 0
        if args.command == "review-adjudicate":
            _output(
                adjudicate_review(
                    _load(args.catalog),
                    _load(args.assignment),
                    _load(args.private_authority),
                    _load(args.submission),
                ),
                args.output,
            )
            return 0
        if args.command == "decide":
            _output(
                decide_homelab_target(
                    _load(args.audit),
                    target_id=args.target_id,
                    decision=args.decision,
                    decided_by=args.decided_by,
                    reason=args.reason,
                    supporting_receipt_sha256s=args.receipt,
                ),
                args.output,
            )
            return 0
        if args.command == "verify":
            payload = _load(args.object)
            homelab_validate_seal(payload)
            _print({"ok": True, "kind": payload["kind"], "seal_valid": True})
            return 0
        if args.command == "sweep":
            _print(
                homelab_sweep(
                    args.root,
                    estate_root=args.estate_root,
                    output_dir=args.output_dir,
                    canon_ledger_path=args.canon,
                    hash_mode=args.hash_mode,
                    include_audio_devices=args.audio_devices,
                )
            )
            return 0
        if args.command == "store-init":
            with HomelabStore(args.store) as store:
                result = store.doctor()
            _print(result)
            return 0 if result["ok"] else 1
        if args.command == "store-ingest":
            with HomelabStore(args.store) as store:
                result = store.ingest_object(_load(args.object), visibility=args.visibility)
            _print(result)
            return 0
        if args.command == "store-doctor":
            with HomelabStore(args.store) as store:
                result = store.doctor(verify_objects=not args.skip_object_verification)
            _print(result)
            return 0 if result["ok"] else 1
        if args.command == "store-snapshot":
            with HomelabStore(args.store) as store:
                result = store.snapshot(include_private_counts=args.include_private_counts)
            _output(result, args.output)
            return 0
        if args.command == "campaign-register":
            with HomelabStore(args.store) as store:
                result = store.register_campaign(_load(args.campaign), priority=args.priority, max_attempts=args.max_attempts)
            _print(result)
            return 0
        if args.command == "task-lease":
            with HomelabStore(args.store) as store:
                result = store.lease_next(
                    worker_id=args.worker_id,
                    resources=args.resource,
                    lease_seconds=args.lease_seconds,
                    campaign_sha256=args.campaign,
                )
            if result is None:
                _print({"ok": False, "lease": None})
                return 1
            token = str(result.pop("lease_token"))
            if args.token_output:
                token_path = _write_secret(args.token_output, token)
                result["lease_token_file"] = str(token_path)
                result["lease_token_exposed_on_stdout"] = False
            else:
                result["lease_token"] = token
                result["lease_token_exposed_on_stdout"] = True
            _print({"ok": True, "lease": result})
            return 0
        if args.command == "task-heartbeat":
            token = Path(args.lease_token_file).read_text(encoding="utf-8").strip()
            with HomelabStore(args.store) as store:
                result = store.heartbeat(
                    args.campaign_sha256,
                    args.task_id,
                    token,
                    extend_seconds=args.extend_seconds,
                )
            _print(result)
            return 0
        if args.command == "task-complete":
            token = Path(args.lease_token_file).read_text(encoding="utf-8").strip()
            with HomelabStore(args.store) as store:
                result = store.complete_task(
                    args.campaign_sha256,
                    args.task_id,
                    token,
                    outcome=args.outcome,
                    evidence_sha256=args.evidence,
                    error=args.error,
                )
            _print(result)
            return 0
        if args.command == "campaign-cancel":
            with HomelabStore(args.store) as store:
                result = store.cancel_campaign(args.campaign_sha256, reason=args.reason)
            _print(result)
            return 0
        if args.command == "public-export":
            _print(export_public_store(args.store, args.destination))
            return 0
        if args.command == "backup":
            _print(
                backup_homelab_store(
                    args.store,
                    args.output_zip,
                    acknowledge_private_state=args.acknowledge_private_state,
                )
            )
            return 0
        if args.command == "restore":
            result = restore_homelab_backup(
                args.backup_zip,
                args.destination,
                approve_sha256=args.approve,
                receipt_path=args.output,
            )
            _print(result)
            return 0
        if args.command == "dashboard":
            _print(render_homelab_dashboard(_load(args.audit), _load(args.campaign), args.output_html))
            return 0
        raise ValueError(f"unhandled homelab command: {args.command}")
    except Exception as exc:
        print(
            json.dumps({"ok": False, "type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(homelab_cli_main())


__all__ = ["homelab_cli_main"]
