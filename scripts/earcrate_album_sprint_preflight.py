#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from album_sprint_preflight import apply_workspace, build_report, campaign_and_contract, load_bindings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = ROOT / "configs/album_one/sprint-01/campaign.v1.json"
DEFAULT_PREFLIGHT = ROOT / "configs/album_one/sprint-01/executable-preflight.v1.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Album One executable preflight")
    parser.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--preflight-contract", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--verify-bytes", action="store_true")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        campaign, preflight = campaign_and_contract(args.campaign, args.preflight_contract)
        bindings = load_bindings(args.bindings, campaign, args.verify_bytes)
        report = build_report(
            campaign,
            preflight,
            bindings,
            bindings_bytes_verified=bool(args.verify_bytes),
        )
        removed = apply_workspace(report, args.workspace) if args.workspace else []
        output = dict(report)
        output["removed_unauthorized_commands"] = removed
        print(json.dumps(output, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}, indent=2, sort_keys=True), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
