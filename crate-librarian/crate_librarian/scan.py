"""Scan music roots into structured track records. mutagen for tags/duration,
hashlib for content identity. No audio decoding, no analysis — this is the
library layer, deliberately lean and reusable."""
from __future__ import annotations
import concurrent.futures
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .identity import derive_identity

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wv"}

try:
    from mutagen import File as _MutagenFile
except Exception:  # pragma: no cover
    _MutagenFile = None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_tags(path: Path) -> Dict[str, str]:
    """Normalized lowercase tag dict. mutagen easy-mode keys plus the raw
    albumartist/compilation frames the identity logic looks for."""
    if _MutagenFile is None:
        return {}
    out: Dict[str, str] = {}
    try:
        mf = _MutagenFile(str(path), easy=True)
        if mf and mf.tags:
            for k, v in mf.tags.items():
                val = v[0] if isinstance(v, list) and v else v
                out[str(k).lower()] = str(val)
    except Exception:
        pass
    # raw pass for frames easy-mode drops (TPE2 albumartist, TCMP compilation)
    try:
        raw = _MutagenFile(str(path))
        if raw and raw.tags:
            for k in ("TPE2", "aART", "TCMP", "cpil"):
                if k in raw.tags:
                    fr = raw.tags[k]
                    txt = getattr(fr, "text", fr)
                    txt = txt[0] if isinstance(txt, list) and txt else txt
                    out.setdefault("albumartist" if k in ("TPE2", "aART") else "compilation", str(txt))
    except Exception:
        pass
    return out


def _probe(path: Path) -> Dict[str, Any]:
    duration = None
    codec = path.suffix.lower().lstrip(".")
    if _MutagenFile is not None:
        try:
            mf = _MutagenFile(str(path))
            if mf is not None and getattr(mf, "info", None) is not None:
                duration = round(float(getattr(mf.info, "length", 0.0)), 3) or None
        except Exception:
            pass
    return {"duration_s": duration, "codec": codec}


def iter_audio(roots: List[Path]) -> List[Path]:
    files: List[Path] = []
    for root in roots:
        root = Path(root)
        if root.is_file() and root.suffix.lower() in AUDIO_EXTS:
            files.append(root)
            continue
        for f in root.rglob("*"):
            try:
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                    files.append(f)
            except OSError:
                continue
    return files


def scan_roots(roots: List[str], do_hash: bool = True, jobs: int = 0,
               progress=None) -> List[Dict[str, Any]]:
    """Return one record per audio file. Records carry source path (read-only),
    size, duration, codec, raw tags, derived identity, and (optionally) a content
    hash. Hashing is I/O-bound and parallelized; pass do_hash=False for a fast
    metadata-only pass."""
    root_paths = [Path(r).expanduser() for r in roots]
    files = iter_audio(root_paths)
    # nearest containing root for the folder-convention fallback
    resolved_roots = [r.resolve() for r in root_paths]

    def root_of(p: Path) -> Optional[Path]:
        rp = p.resolve()
        for r in resolved_roots:
            try:
                rp.relative_to(r)
                return r
            except ValueError:
                continue
        return None

    records: List[Dict[str, Any]] = []
    for i, f in enumerate(files):
        try:
            st = f.stat()
            tags = read_tags(f)
            probe = _probe(f)
            ident = derive_identity(f, tags, root_of(f))
            records.append({
                "path": str(f), "size_bytes": st.st_size, "mtime": int(st.st_mtime),
                "duration_s": probe["duration_s"], "codec": probe["codec"],
                "tags": tags, "identity": ident, "sha256": None,
            })
        except Exception as exc:
            records.append({"path": str(f), "error": str(exc)[:200]})
        if progress and i % 50 == 0:
            progress("scan", i + 1, len(files))

    if do_hash:
        # Size ladder: a file whose byte size is unique in the whole set cannot be a
        # duplicate, so it need not be hashed for dedup — but the contract wants a
        # content id for every track, so hash all, just parallelized.
        workers = jobs if jobs and jobs > 0 else min(16, (os.cpu_count() or 4))
        hashable = [r for r in records if "error" not in r]
        done = [0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(sha256_file, r["path"]): r for r in hashable}
            for fut in concurrent.futures.as_completed(futs):
                r = futs[fut]
                try:
                    r["sha256"] = fut.result()
                except Exception as exc:
                    r["hash_error"] = str(exc)[:200]
                done[0] += 1
                if progress and done[0] % 50 == 0:
                    progress("hash", done[0], len(hashable))
    return records
