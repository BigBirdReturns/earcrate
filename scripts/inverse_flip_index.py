#!/usr/bin/env python3
"""Invert the answer-key corpus into a LIBRARY-CENTRIC flip map: which of the
library's own songs are documented ingredients in the classic mashups/beats we've
catalogued. Writes docs/LIBRARY_FLIP_MAP.md.

NOT comprehensive: covers only the flips in earcrate/reference/*_samples.json, not
all of WhoSampled (closed / no open API). The open path to check all ~14k tracks is
MusicBrainz/AcoustID (see docs/OSS_INTEGRATION_AUDIT.md).
Run: python scripts/inverse_flip_index.py
"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.study.reference import flip_index, artist_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    lib = json.loads((ROOT / "docs" / "LIBRARY_ARTISTS.json").read_text())["artists"]
    owned = {artist_key(a): a for a in lib}
    datasets = sorted(str(p) for p in (ROOT / "earcrate" / "reference").glob("*_samples.json"))
    idx = flip_index(datasets)
    hits = {ak: s for ak, s in idx.items() if ak in owned}
    lines = ["# Library flip map — your songs that are ingredients in known flips", "",
             f"Cross-referenced {len(owned)} library artists against {len(datasets)} catalogued ",
             "answer keys. NOT comprehensive — only the classic flips in our corpus, not all of ",
             "WhoSampled (closed/no-API). Comprehensive path = MusicBrainz/AcoustID.", "",
             f"**{len(hits)} of your artists are documented sources in these flips:**", ""]
    for ak, s in sorted(hits.items(), key=lambda kv: -len(kv[1]["flips"])):
        albs = sorted({f"{f['flip_artist']} — {f['flip_album']}" for f in s["flips"]})
        lines.append(f"- **{owned[ak]}** ({len(s['flips'])}) → {', '.join(albs)}")
    (ROOT / "docs" / "LIBRARY_FLIP_MAP.md").write_text("\n".join(lines) + "\n")
    print(f"{len(hits)} owned artists are ingredients in the catalogued flips -> docs/LIBRARY_FLIP_MAP.md")


if __name__ == "__main__":
    main()
