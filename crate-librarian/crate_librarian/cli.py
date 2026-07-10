"""crate-librarian CLI — the thin operator seam.

  crate-librarian scan <roots...> [--out library.json] [--no-hash] [--jobs N]
  crate-librarian report <library.json>
  crate-librarian organize <library.json> --dest <dir> [--apply] [--pattern P]
  crate-librarian rollback <journal.jsonl> [--apply]

Everything writes only under --dest; sources are never touched; organize is
dry-run unless --apply.
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import sys
from typing import List, Optional

from .scan import scan_roots
from .library import build_library, write_library, read_library
from .organize import organize, rollback


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _progress(stage: str, i: int, n: int) -> None:
    pct = (i / n * 100) if n else 0
    print(f"\r  {stage}: {i}/{n} ({pct:4.1f}%)", end="", file=sys.stderr, flush=True)
    if i >= n:
        print("", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="crate-librarian", description="Turn a folder of music into a clean library + library.json contract.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="scan roots into library.json")
    p.add_argument("roots", nargs="+")
    p.add_argument("--out", default="library.json")
    p.add_argument("--no-hash", action="store_true", help="skip content hashing (fast, no dedup ids)")
    p.add_argument("--jobs", type=int, default=0)

    p = sub.add_parser("report", help="human summary of a library.json")
    p.add_argument("library")

    p = sub.add_parser("organize", help="build Artist/Album/NN Title archive (dry-run by default)")
    p.add_argument("library")
    p.add_argument("--dest", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--pattern", default="nn-title", choices=["nn-title", "artist-title", "nn-artist-title", "title"])

    p = sub.add_parser("rollback", help="undo an organize run from its journal")
    p.add_argument("journal")
    p.add_argument("--apply", action="store_true")

    ns = ap.parse_args(argv)

    if ns.cmd == "scan":
        recs = scan_roots(ns.roots, do_hash=not ns.no_hash, jobs=ns.jobs, progress=_progress)
        lib = build_library(recs, ns.roots, generated_at=_now())
        write_library(lib, ns.out)
        print(json.dumps({"ok": True, "out": ns.out, "count": lib["count"],
                          "unique": lib["unique_count"], "duplicates": lib["duplicate_count"],
                          "errors": lib["error_count"]}, indent=2))
        return 0

    if ns.cmd == "report":
        lib = read_library(ns.library)
        tracks = lib.get("tracks", [])
        untagged = sum(1 for t in tracks if (t.get("quality") or {}).get("untagged"))
        unknown = sum(1 for t in tracks if (t.get("quality") or {}).get("unknown_artist"))
        by_source: dict = {}
        for t in tracks:
            s = (t.get("quality") or {}).get("identity_source") or "?"
            by_source[s] = by_source.get(s, 0) + 1
        print(json.dumps({
            "contract_version": lib.get("contract_version"), "generated_at": lib.get("generated_at"),
            "roots": lib.get("roots"), "count": lib.get("count"), "unique": lib.get("unique_count"),
            "duplicates": lib.get("duplicate_count"), "errors": lib.get("error_count"),
            "identity_source": by_source, "untagged": untagged, "unknown_artist": unknown,
        }, indent=2))
        return 0

    if ns.cmd == "organize":
        lib = read_library(ns.library)
        if ns.apply:
            print("Copying (sources are never modified)…", file=sys.stderr)
        r = organize(lib, ns.dest, pattern=ns.pattern, apply=ns.apply, progress=_progress)
        print(json.dumps(r, indent=2))
        return 0

    if ns.cmd == "rollback":
        print(json.dumps(rollback(ns.journal, apply=ns.apply), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
