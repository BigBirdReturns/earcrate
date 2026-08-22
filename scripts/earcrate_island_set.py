#!/usr/bin/env python3
"""Compile or render one content-bound EarCrate island-set request."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from earcrate.app import EarcrateCore
from earcrate.plan.islands import source_pool_identity


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate_island_set",
        description="Compile an exact tempo/key island schedule through the existing EarCrate engine",
    )
    parser.add_argument("request", type=Path, help="private request JSON")
    parser.add_argument("--render", action="store_true", help="execute the emitted guarded manifest")
    parser.add_argument(
        "--print-pool-sha",
        action="store_true",
        help="print the current pool identity and exit without compiling",
    )
    args = parser.parse_args()

    request = json.loads(args.request.read_text(encoding="utf-8"))
    core = EarcrateCore()
    if args.print_pool_sha:
        profile = str(request.get("profile") or "girl_talk_v1")
        excludes = request.get("source_exclude_ids") or []
        print(json.dumps({
            "profile": profile,
            "source_pool_sha256": source_pool_identity(core.approved_atom_pool(profile), excludes),
        }, indent=2))
        return 0

    result = core.propose_island_set(request)
    output = dict(result)
    output.pop("arrangement", None)
    if args.render:
        output["execution"] = core.execute_manifest(result["manifest"], apply=True)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
