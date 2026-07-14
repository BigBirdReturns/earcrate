#!/usr/bin/env python3
"""Cross-reference the library manifest against every producer answer key to see
which masters' source material we actually own -- the CEILING on what the engine
could rediscover. Writes docs/MATERIAL_COVERAGE.md.

Artist-level (from docs/LIBRARY_ARTISTS.json). Owning the artist != owning the
exact sampled track, so this OVER-estimates; the box's title-level reference_recall
is the precise measure. But it is the honest first cut for what crates are even
viable in this library.

Run: python scripts/cross_reference.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.study.reference import artist_key, answer_key_material_coverage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "earcrate" / "reference"
MANIFEST = ROOT / "docs" / "LIBRARY_ARTISTS.json"


def main():
    if not MANIFEST.exists():
        print("no docs/LIBRARY_ARTISTS.json -- run export_library_manifest on the box and commit it")
        return 1
    artists = json.loads(MANIFEST.read_text(encoding="utf-8")).get("artists") or {}
    owned = {artist_key(a) for a in artists}
    keys = sorted(REF.glob("*_samples.json"))
    reports = []
    for k in keys:
        try:
            reports.append((k.name, answer_key_material_coverage(str(k), owned)))
        except Exception as exc:
            print(f"skip {k.name}: {exc}")
    reports.sort(key=lambda r: -(r[1]["artist_coverage"] or 0))
    lines = ["# Material coverage — which masters' sources our library owns", "",
             f"Library artists indexed: **{len(owned)}**. Artist-level (owning the artist ",
             "over-estimates vs. owning the exact sampled track — the box's title-level ",
             "`reference_recall` is precise). This is the CEILING on rediscovery: you can't ",
             "recover a flip whose source records you don't have.", "",
             "| answer key | source artists | owned | coverage |", "|---|--:|--:|--:|"]
    for name, r in reports:
        cov = r["artist_coverage"]
        lines.append(f"| {r['artist']} — {r['album']} | {r['source_artists_total']} | "
                     f"{r['source_artists_owned']} | {round(100*cov) if cov is not None else 0}% |")
    lines += ["", "## Detail", ""]
    for name, r in reports:
        lines.append(f"### {r['artist']} — {r['album']}  ({r['source_artists_owned']}/{r['source_artists_total']})")
        lines.append(f"- **owned:** {', '.join(r['owned']) if r['owned'] else '(none — this crate cannot feed this persona)'}")
        miss = r["missing"]
        lines.append(f"- **missing ({len(miss)}):** {', '.join(miss[:40])}{' …' if len(miss) > 40 else ''}")
        lines.append("")
    out = ROOT / "docs" / "MATERIAL_COVERAGE.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", out)
    for name, r in reports:
        print(f"  {r['artist']:<16} {r['album']:<20} {r['source_artists_owned']:>3}/{r['source_artists_total']:<3}  "
              f"{round(100*(r['artist_coverage'] or 0))}%")


if __name__ == "__main__":
    raise SystemExit(main())
