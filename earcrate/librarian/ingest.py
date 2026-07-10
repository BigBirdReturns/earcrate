from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.core.util import *
from earcrate.core.config import *

_TRACKNO = re.compile(r"^(\d{1,3})")
_LEADNUM = re.compile(r"^\s*\d{1,3}\s*[-. _]\s*")
_YEAR_SUFFIX = re.compile(r"\s*[\[(](19|20)\d{2}[\])]\s*$")
_JUNK_TITLE = re.compile(r"^(track|audiotrack|pista|titel|piste)\s*\d{1,3}$", re.I)
_VARIOUS = {"various artists", "various", "va", "v.a.", "verschiedene interpreten", "compilation", "varios artistas"}


def _fix_case(s: str) -> str:
    """Fix ALLCAPS / all-lowercase tags (decades of rips); leave mixed case alone."""
    t = s.strip()
    if len(t) > 2 and (t.isupper() or t.islower()):
        return " ".join(w if (len(w) <= 3 and t.islower() and w in {"the", "and", "of", "a", "an", "in", "on", "for"}) else w.capitalize() for w in t.split())
    return t


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\x00", "").strip())


def _first(tags: Dict[str, str], *keys: str) -> str:
    for k in keys:
        v = _clean_text(tags.get(k) or "")
        if v:
            return v
    return ""


_GENERIC_DIR = re.compile(r"^(new folder(\s*\(\d+\))?|music|my music|mp3s?|songs?|tracks?|audio|downloads?|"
                          r"ingested|unsorted|misc|stuff|files|media|temp|dump\w*|backups?|old\s.*|to sort|"
                          r"ssd|hdd|usb|external|flash|drive [a-z]|various.*|va)$", re.I)
_BATCH_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-", re.I)  # master/ingested/<batch>/ stamps


def _folder_identity(path: Path, root: Optional[Path]) -> Tuple[str, str]:
    """Artist/Album fallback from the near-universal folder convention
    .../Artist/Album/track.ext or .../Artist/track.ext. Only the two nearest
    meaningful parents are considered; generic dump/batch folder names are
    ignored. This is a FALLBACK — embedded tags and parseable filenames win."""
    try:
        parts = list((path.relative_to(root) if root else path).parent.parts)
    except Exception:
        parts = [path.parent.name]
    if len(parts) >= 2 and parts[0].lower() == "ingested" and _BATCH_DIR.match(parts[1]):
        parts = parts[2:]  # copies live under ingested/<batch>/<source-folder>/...
    dirs = [d for d in parts if d and not _GENERIC_DIR.match(d) and not _BATCH_DIR.match(d)][-2:]
    if len(dirs) == 2:
        return dirs[0], dirs[1]
    if len(dirs) == 1:
        return dirs[0], ""
    return "", ""


def _derive_identity(path: Path, tags: Dict[str, str], root: Optional[Path] = None) -> Dict[str, Any]:
    """Deterministic normalization for decades of mp3-dump mess (spec §6.2):
    scene underscores, Track-NN junk titles, ALLCAPS, year-suffixed albums,
    'NN - Artist - Title' filenames, folder-convention fallback, 'Title by
    Artist' suffix strip, feat. canonicalization, compilations."""
    stem = path.stem
    if "_" in stem and " " not in stem:
        stem = stem.replace("_", " ")
    artist = _first(tags, "albumartist", "album artist", "album_artist", "band", "artist")
    title = _first(tags, "title")
    if _JUNK_TITLE.match(title):
        title = ""  # 'Track 01' from CDDB-less rips is not a title
    m_lead = re.match(r"^\s*(\d{1,3})\s*[-. _]\s*", stem)
    stem_no_num = _LEADNUM.sub("", stem)
    if not title or not artist:
        parts = [x.strip().replace("_", " ").strip() for x in re.split(r"\s+-\s+", stem_no_num) if x.strip()]
        if len(parts) >= 2:
            artist = artist or parts[0]
            title = title or " - ".join(parts[1:])
        else:
            title = title or (parts[0] if parts else stem)
    if _JUNK_TITLE.match(re.sub(r"[\s_]+", " ", _clean_text(title))):
        title = ""
    folder_artist, folder_album = _folder_identity(path, root)
    if not artist and folder_artist:
        artist = folder_artist
    # 'Title by Artist.mp3': strip the suffix ONLY when it names a known folder
    # identity ('Stand by Me' must survive). If it names the INNER folder, that
    # folder is the artist, not an album (dump/Artist/ layouts).
    if title:
        m_by = re.search(r"\s+by\s+(?:the\s+)?(?P<a>.+)$", title, re.I)
        if m_by:
            said = m_by.group("a").strip().lower()
            def _variants(a: str) -> set:
                a = a.lower()
                return {a, a[4:] if a.startswith("the ") else a}
            if artist and said in _variants(artist):
                title = title[:m_by.start()].strip() or title
            elif folder_album and said in _variants(folder_album):
                artist, folder_album = folder_album, ""
                title = title[:m_by.start()].strip() or title
    album = _first(tags, "album") or folder_album
    year = None
    m = _YEAR_SUFFIX.search(album)
    if m:
        year = m.group(0).strip(" []()")
        album = _YEAR_SUFFIX.sub("", album)
    album = _fix_case(album) or "Unknown Album"
    track_artist = _fix_case(_first(tags, "artist") or artist) or "Unknown Artist"
    artist = _fix_case(artist) or "Unknown Artist"
    for pat in (" Feat. ", " feat ", " Ft. ", " ft. ", " FEAT. ", " Featuring "):
        artist = artist.replace(pat, " feat. ")
        track_artist = track_artist.replace(pat, " feat. ")
    aa = _first(tags, "albumartist", "album artist", "album_artist", "band").lower()
    compilation = (aa in _VARIOUS) or (tags.get("compilation", "").strip() in {"1", "true"}) or (artist.lower() in _VARIOUS)
    m = _TRACKNO.match(_first(tags, "tracknumber"))
    track = int(m.group(1)) if m else (int(m_lead.group(1)) if m_lead else None)
    title = _fix_case(_clean_text(title))
    if not title:
        title = f"Track {track:02d}" if track else _clean_text(stem_no_num) or stem
    return {"artist": artist, "track_artist": track_artist, "album": album,
            "title": title, "track": track, "year": year, "compilation": compilation}


def ingest_sources(self, data: Dict[str, Any]) -> Dict[str, Any]:
    """Copy audio from N external folders into master/ingested/<batch>/, deduped by
    content hash, manifest-gated (dry-run default), journaled, rollback-able. Sources
    are NEVER modified (INV-1 extended: additions only, existing files untouched)."""
    c = self.ensure_config()
    apply = bool(data.get("apply"))
    sources = [Path(str(s)).expanduser() for s in (data.get("sources") or []) if str(s).strip()]
    if not sources:
        return {"ok": False, "error": "no source folders given"}
    protected = [c.working_root, c.agent_root, c.playlists_root, c.master_root / "ingested"]
    checked = []
    for s in sources:
        if not s.is_dir():
            return {"ok": False, "error": f"not a folder: {s}"}
        rs = s.resolve()
        for p in protected:
            try:
                rs.relative_to(p.resolve())
                return {"ok": False, "error": f"refusing to ingest from managed root: {s}"}
            except ValueError:
                pass
        checked.append(rs)
    db = self.conn()
    known = {r[0] for r in db.execute("SELECT sha256 FROM files WHERE sha256 IS NOT NULL").fetchall()}
    known_sizes = {int(r[0]) for r in db.execute("SELECT size_bytes FROM files").fetchall()}
    batch = time.strftime("%Y-%m-%d-%H%M%S") + "-" + ulidish()[-6:]
    ops, skipped_dupe, skipped_exists, batch_hashes = [], [], [], set()
    hashed_count = 0
    # Size-ladder dedupe (the trick every dedupe tool uses): a file whose byte size
    # exists nowhere in the library and nowhere else in this batch CANNOT be a duplicate,
    # so we skip hashing it here entirely and verify its hash at copy time instead.
    # For "gigs and gigs" over USB this turns a full-library read into a tiny one.
    all_files = []
    for root in checked:
        for f in root.rglob("*"):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                all_files.append((root, f, f.stat().st_size))
    from collections import Counter
    batch_size_counts = Counter(sz for _, _, sz in all_files)
    self.set_status(f"ingest: planning {len(all_files)} files (size-ladder dedupe)", 0, True, None)
    for i, (root, f, size) in enumerate(all_files):
        size_collides = (size in known_sizes) or (batch_size_counts[size] > 1)
        sha = None
        if size_collides:
            sha = sha256_file(f)
            hashed_count += 1
            if sha in known or sha in batch_hashes:
                skipped_dupe.append(str(f))
                continue
            batch_hashes.add(sha)
        rel = f.relative_to(root)
        dst = c.master_root / "ingested" / batch / safe_name(root.name) / rel
        if dst.exists():
            skipped_exists.append(str(dst))
            continue
        args = {"src": str(f), "dst": str(dst)}
        if sha:
            args["sha256"] = sha
        ops.append({"op_id": ulidish(), "type": "ingest_copy", "args": args,
                    "preconditions": {"dst_absent": True}})
        if i % 50 == 0:
            self.set_status(f"ingest: planned {i+1}/{len(all_files)} (hashed only {hashed_count})", None, True)
    if not ops:
        self.set_status("ingest: nothing new to copy", 1, False)
        return {"ok": True, "batch": batch, "planned": 0, "skipped_duplicates": len(skipped_dupe),
                "skipped_existing": len(skipped_exists), "message": "all files already in library"}
    manifest = self.write_manifest("librarian", c.seed, f"Ingest {len(ops)} files from {len(checked)} folder(s) [{batch}]", ops)
    result = self.execute_manifest(manifest, apply=apply)
    out = {"ok": True, "batch": batch, "planned": len(ops), "hashed_for_dedupe": hashed_count, "skipped_duplicates": len(skipped_dupe),
           "skipped_existing": len(skipped_exists), "manifest": manifest,
           "dry_run": not apply, "result": result}
    if apply:
        out["scan"] = self.scan()
        # Backfill content hashes for the copies (scan hashes lazily); without this,
        # re-ingesting the same folder would miss the dedupe check and copy dupes.
        db2 = self.conn()
        for item in (result.get("done") or []):
            if item.get("type") == "ingest_copy" and item.get("sha256"):
                db2.execute("UPDATE files SET sha256=? WHERE path=?", (item["sha256"], item["path"]))
        db2.commit()
        self.set_status(f"ingest complete: {len(ops)} copied, {len(skipped_dupe)} dupes skipped", 1, False)
    return out


def execute_ingest_copy(self, op: Dict[str, Any]) -> Dict[str, Any]:
    args = op["args"]
    src, dst = Path(args["src"]).resolve(), Path(args["dst"]).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    got = sha256_file(dst)
    if args.get("sha256") and got != args["sha256"]:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"copy hash mismatch for {src}")
    return {"type": "ingest_copy", "path": str(dst), "src": str(src), "sha256": got}


def organize_and_retag(self, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build working/organized/Artist/Album/NN Title.ext copies with amended tags.
    Master files stay verbatim (spec: copy-then-edit); manifest-gated, dry-run default."""
    c = self.ensure_config()
    apply = bool(data.get("apply"))
    limit = int(data.get("limit") or 0)
    db = self.conn()
    rows = db.execute("SELECT id, path FROM files WHERE root='master' ORDER BY path" + (f" LIMIT {limit}" if limit > 0 else "")).fetchall()
    if not rows:
        return {"ok": False, "error": "library is empty; scan or ingest first"}
    ops, planned_tree, taken = [], {}, set()
    # Pass 1: derive identities, then album-level compilation clustering — the
    # heuristic every serious tagger converged on: if one album name carries 2+
    # distinct track artists, it is a compilation even when albumartist tags are
    # missing (TXXX-frame casualties are everywhere in decades-old rips).
    derived = []
    for fid, fpath in rows:
        p = Path(fpath)
        if not p.exists():
            continue
        tags = {str(k).lower(): (v or "") for k, v in db.execute("SELECT key, value FROM tags WHERE file_id=?", (fid,)).fetchall()}
        derived.append((p, tags, _derive_identity(p, tags, c.master_root)))
    album_artists: Dict[str, set] = {}
    for _, _, ident in derived:
        if ident["album"].lower() != "unknown album":
            album_artists.setdefault(ident["album"].lower(), set()).add(ident["track_artist"].lower())
    comp_albums = {a for a, artists in album_artists.items() if len(artists) >= 2}
    for p, tags, ident in derived:
        if ident["album"].lower() in comp_albums:
            ident["compilation"] = True
        if ident["compilation"]:
            # Compilations stay together: Various Artists/Album/NN Track Artist - Title
            fn = (f"{ident['track']:02d} " if ident["track"] else "") + safe_name(ident["track_artist"]) + " - " + safe_name(ident["title"]) + p.suffix.lower()
            dst = c.working_root / "organized" / "Various Artists" / safe_name(ident["album"]) / fn
        else:
            fn = (f"{ident['track']:02d} " if ident["track"] else "") + safe_name(ident["title"]) + p.suffix.lower()
            dst = c.working_root / "organized" / safe_name(ident["artist"]) / safe_name(ident["album"]) / fn
        n = 2
        while str(dst) in taken or dst.exists():
            dst = dst.with_name(dst.stem + f" ({n})" + dst.suffix)
            n += 1
        taken.add(str(dst))
        amend = {"artist": ident["track_artist"], "albumartist": ("Various Artists" if ident["compilation"] else ident["artist"]), "album": ident["album"], "title": ident["title"]}
        if ident["track"]:
            amend["tracknumber"] = str(ident["track"])
        if ident.get("year") and not tags.get("date"):
            amend["date"] = ident["year"]
        ops.append({"op_id": ulidish(), "type": "organize_copy",
                    "args": {"src": str(p), "dst": str(dst), "tags": amend},
                    "preconditions": {"dst_absent": True}})
        routed = "Various Artists" if ident["compilation"] else ident["artist"]
        planned_tree.setdefault(routed, {}).setdefault(ident["album"], 0)
        planned_tree[routed][ident["album"]] += 1
    manifest = self.write_manifest("librarian", c.seed, f"Organize+retag {len(ops)} tracks into Artist/Album tree", ops)
    result = self.execute_manifest(manifest, apply=apply)
    return {"ok": True, "planned": len(ops), "artists": len(planned_tree),
            "albums": sum(len(v) for v in planned_tree.values()),
            "tree_preview": {a: v for a, v in list(planned_tree.items())[:12]},
            "manifest": manifest, "dry_run": not apply, "result": result}


def execute_organize_copy(self, op: Dict[str, Any]) -> Dict[str, Any]:
    args = op["args"]
    src, dst = Path(args["src"]).resolve(), Path(args["dst"]).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    amended = []
    try:
        mf = MutagenFile(str(dst), easy=True)
        if mf is not None:
            for k, v in (args.get("tags") or {}).items():
                try:
                    mf[k] = [v]
                    amended.append(k)
                except Exception:
                    pass
            mf.save()
    except Exception as exc:
        return {"type": "organize_copy", "path": str(dst), "tags_amended": [], "tag_error": str(exc)[:200]}
    return {"type": "organize_copy", "path": str(dst), "tags_amended": amended}
