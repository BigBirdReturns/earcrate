"""Build a clean Artist/Album/NN Title archive from a scanned library.

Invariants (the same ones EarCrate enforces, implemented standalone):
  - Sources are NEVER modified. organize only ever COPIES, then edits tags on
    the copy (copy-then-edit).
  - Idempotent. A destination that already exists means the track was organized
    on a previous run and is skipped — re-running never duplicates the tree, and
    numeric collision suffixes derive from the base name so ' (2) (3)' can't
    accrete.
  - Dry-run by default. Nothing is written unless apply=True.
  - Journaled + rollback-able. Every copy appends to a JSONL journal; because the
    tool only ever adds files, rollback is simply deleting what the journal
    records (no in-place mutation to invert).
  - Duplicates are skipped (the canonical copy is organized, its dupes are not).
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .identity import safe_name

try:
    from mutagen import File as _MutagenFile
except Exception:  # pragma: no cover
    _MutagenFile = None

NAME_PATTERNS = {
    "nn-title": "{nn}{title}",
    "artist-title": "{artist} - {title}",
    "nn-artist-title": "{nn}{artist} - {title}",
    "title": "{title}",
}


def _filename(ident: Dict[str, Any], pattern: str, compilation: bool, suffix: str) -> str:
    nn = f"{ident['track']:02d} " if ident.get("track") else ""
    if compilation:
        return f"{nn}{safe_name(ident['track_artist'])} - {safe_name(ident['title'])}{suffix}"
    tpl = NAME_PATTERNS.get(pattern, NAME_PATTERNS["nn-title"])
    return tpl.format(nn=nn, artist=safe_name(ident["track_artist"]), title=safe_name(ident["title"])) + suffix


def plan_organize(library: Dict[str, Any], dest: str, pattern: str = "nn-title") -> List[Dict[str, Any]]:
    """Compute the copy plan (no writes). One op per non-duplicate track."""
    dest_root = Path(dest).expanduser()
    ops: List[Dict[str, Any]] = []
    taken: set = set()
    for t in library.get("tracks", []):
        if t.get("duplicate_of"):
            continue
        ident = dict(t.get("identity") or {})
        src = Path(t["path"])
        comp = bool(ident.get("compilation"))
        fn = _filename(ident, pattern, comp, src.suffix.lower())
        if comp:
            dst = dest_root / "Various Artists" / safe_name(ident.get("album") or "Unknown Album") / fn
        else:
            dst = dest_root / safe_name(ident.get("artist") or "Unknown Artist") / safe_name(ident.get("album") or "Unknown Album") / fn
        base = dst
        n = 2
        while str(dst) in taken:
            dst = base.with_name(f"{base.stem} ({n}){base.suffix}")
            n += 1
        taken.add(str(dst))
        amend = {"artist": ident.get("track_artist"),
                 "albumartist": ("Various Artists" if comp else ident.get("artist")),
                 "album": ident.get("album"), "title": ident.get("title")}
        if ident.get("track"):
            amend["tracknumber"] = str(ident["track"])
        if ident.get("year") and not (t.get("tags") or {}).get("date"):
            amend["date"] = ident["year"]
        ops.append({"src": str(src), "dst": str(dst), "tags": amend})
    return ops


def _amend_tags(dst: Path, tags: Dict[str, str]) -> List[str]:
    if _MutagenFile is None:
        return []
    amended: List[str] = []
    try:
        mf = _MutagenFile(str(dst), easy=True)
        if mf is not None:
            for k, v in tags.items():
                if v is None:
                    continue
                try:
                    mf[k] = [str(v)]
                    amended.append(k)
                except Exception:
                    pass
            mf.save()
    except Exception:
        pass
    return amended


def organize(library: Dict[str, Any], dest: str, pattern: str = "nn-title",
             apply: bool = False, journal: str = "", progress=None) -> Dict[str, Any]:
    """Execute (or dry-run) the organize plan. Returns a receipt: planned,
    already-organized (skipped), copied, plus the tree shape and where it lives."""
    ops = plan_organize(library, dest, pattern)
    dest_root = str(Path(dest).expanduser())
    jpath = Path(journal) if journal else Path(dest).expanduser() / ".crate-librarian-journal.jsonl"
    tree: Dict[str, Dict[str, int]] = {}
    copied = 0
    already = 0
    samples: List[Dict[str, str]] = []
    todo = []
    for op in ops:
        dst = Path(op["dst"])
        if dst.exists():
            already += 1
            continue
        todo.append(op)
        artist_key = dst.parent.parent.name
        album_key = dst.parent.name
        tree.setdefault(artist_key, {}).setdefault(album_key, 0)
        tree[artist_key][album_key] += 1
        if len(samples) < 8:
            samples.append({"from": Path(op["src"]).name, "to": str(dst).replace(dest_root, "").lstrip("/\\")})

    if apply and todo:
        jpath.parent.mkdir(parents=True, exist_ok=True)
        with jpath.open("a", encoding="utf-8") as jf:
            for i, op in enumerate(todo):
                src, dst = Path(op["src"]), Path(op["dst"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                amended = _amend_tags(dst, op["tags"])
                rec = {"dst": str(dst), "src": str(src), "tags_amended": amended}
                jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                jf.flush()
                copied += 1
                if progress and i % 25 == 0:
                    progress("organize", i + 1, len(todo))

    return {
        "ok": True, "dry_run": not apply, "pattern": pattern,
        "output_root": dest_root, "planned": len(todo), "already_organized": already,
        "copied": copied if apply else 0,
        "artists": len(tree), "albums": sum(len(v) for v in tree.values()),
        "tree_preview": {a: v for a, v in list(tree.items())[:12]},
        "samples": samples, "journal": str(jpath),
        "message": (f"{already} track(s) already organized; nothing to do" if not todo
                    else f"{'copied' if apply else 'would copy'} {len(todo)} track(s) into {dest_root}"),
    }


def rollback(journal: str, apply: bool = False) -> Dict[str, Any]:
    """Undo an organize run: delete exactly the files the journal recorded (the
    tool only ever added them). Dry-run by default."""
    jpath = Path(journal)
    if not jpath.exists():
        return {"ok": False, "error": f"journal not found: {journal}"}
    removed = 0
    planned = 0
    for line in jpath.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        dst = Path(rec.get("dst") or "")
        if dst.exists():
            planned += 1
            if apply:
                try:
                    dst.unlink()
                    removed += 1
                except Exception:
                    pass
    if apply:
        jpath.rename(jpath.with_suffix(jpath.suffix + ".done"))
    return {"ok": True, "dry_run": not apply, "planned": planned, "removed": removed, "journal": journal}
