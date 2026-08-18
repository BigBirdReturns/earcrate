"""`earcrate-a1-02-custody` -- capture a delivered candidate without promoting it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from ..album import commission as cm
from ..evidence.identity import validate_seal
from .custody import CustodyError, capture

REPO_ROOT = Path(__file__).resolve().parents[2]


def _declaration(repo_root: Path) -> dict:
    manifest = json.loads(
        (repo_root / "configs/album_one/manifest.v1.json").read_text(encoding="utf-8"))
    validate_seal(manifest, "manifest_sha256")
    cm.from_ledger(manifest, "A1-02")
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    declaration = row.get("edition_declaration")
    if not declaration:
        raise CustodyError("A1-02 has no declared edition; declare it before acquiring")
    return declaration


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="earcrate-a1-02-custody",
        description="Capture a delivered A1-02 audio candidate as a declared candidate")
    ap.add_argument("--file", type=Path, required=True,
                    help="the delivered file, unchanged: no rename, no transcode")
    ap.add_argument("--out", type=Path, required=True,
                    help="where to write the private capture receipt")
    ap.add_argument("--source", default="official Robert Miles Bandcamp download",
                    help="acquisition provenance")
    ap.add_argument("--downloaded-at", default="", help="download timestamp, event context")
    ap.add_argument("--release-page", default="https://robertmiles.bandcamp.com/album/dreamland")
    ap.add_argument("--displayed-version", default="")
    ap.add_argument("--track-number", type=int, default=1)
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        declaration = _declaration(args.repo_root)
        receipt = capture(
            args.file.expanduser().absolute(),
            declaration=declaration,
            provenance={
                "source": args.source,
                "downloaded_at": args.downloaded_at,
                "official_release_page": args.release_page,
                "displayed_version_name": args.displayed_version,
                "track_number": args.track_number,
                "delivered_file_modified": False,
            },
            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)

        out = args.out.expanduser().absolute()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")

        summary = {
            "status": receipt["status"],
            "capture_sha256": receipt["capture_sha256"],
            "container_sha256": receipt["observed"]["container_sha256"],
            "canonical_pcm_sha256": receipt["observed"]["canonical_pcm_sha256"],
            "duration_seconds": receipt["observed"]["duration_seconds"],
            "codec": receipt["observed"]["codec_name"],
            "bit_depth": receipt["observed"]["bit_depth"],
            "sample_rate": receipt["observed"]["sample_rate"],
            "leading_silence_seconds": receipt["observed"]["leading_silence_seconds"],
            "trailing_silence_seconds": receipt["observed"]["trailing_silence_seconds"],
            "obvious_mismatches": receipt["declared_versus_observed"]["obvious_mismatches"],
            "next": receipt["promotion_requires"],
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if not summary["obvious_mismatches"] else 3
    except CustodyError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
