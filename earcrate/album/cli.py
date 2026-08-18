"""`earcrate album` -- the supported path for changing album authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .transitions import EVENTS, LedgerTransitionError, apply_transition, \
    plan_transition, verify

REPO_ROOT = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="earcrate-album",
        description="Change or verify Album One authority through its ledger")
    sub = ap.add_subparsers(dest="command", required=True)

    check = sub.add_parser("verify", help="check that the ledger still says what it claims")
    check.add_argument("--repo-root", type=Path, default=REPO_ROOT)

    for name, help_text in (("plan", "compute a transition without writing"),
                            ("transition", "apply a transition")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--track", required=True)
        cmd.add_argument("--event", required=True, choices=sorted(EVENTS))
        cmd.add_argument("--receipt", type=Path, required=True)
        cmd.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "verify":
            problems = verify(args.repo_root)
            print(json.dumps({"ok": not problems, "problems": problems},
                             indent=2, sort_keys=True))
            return 0 if not problems else 2

        if args.command == "plan":
            plan = plan_transition(args.repo_root, track_id=args.track,
                                   event_name=args.event, receipt_path=args.receipt)
            print(json.dumps({
                "track_id": plan.track_id,
                "event": plan.event.name,
                "from_state": plan.current_state,
                "to_state": plan.next_state,
                "idempotent_replay": plan.idempotent,
                "documents_to_update": sorted(plan.documents),
                "accepted_album_masters": plan.manifest["completed_album_master_count"],
                "completed_system_references":
                    plan.manifest["completed_system_reference_count"],
                "manifest_sha256": plan.manifest["manifest_sha256"],
            }, indent=2, sort_keys=True))
            return 0

        result = apply_transition(args.repo_root, track_id=args.track,
                                  event_name=args.event, receipt_path=args.receipt)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except LedgerTransitionError as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
