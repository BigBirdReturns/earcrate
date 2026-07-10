"""Deterministic music identity — the crown jewel, ported verbatim from the
EarCrate librarian and gated for agreement with it (earcrate/tests cross-check).

Pure: (path, tags, root) -> identity dict. No I/O, no dependencies beyond stdlib.
Handles decades of mp3-dump mess: scene underscores, Track-NN junk titles,
ALLCAPS/lowercase repair, year-suffixed albums, 'NN - Artist - Title' filenames,
Artist/Album folder convention, 'Title by Artist' suffixes, feat. forms, and
albumartist-less compilations (clustering is applied at the library level, see
organize.py).
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_TRACKNO = re.compile(r"^(\d{1,3})")
_LEADNUM = re.compile(r"^\s*\d{1,3}\s*[-. _]\s*")
_YEAR_SUFFIX = re.compile(r"\s*[\[(](19|20)\d{2}[\])]\s*$")
_JUNK_TITLE = re.compile(r"^(track|audiotrack|pista|titel|piste)\s*\d{1,3}$", re.I)
VARIOUS = {"various artists", "various", "va", "v.a.", "verschiedene interpreten", "compilation", "varios artistas"}
_GENERIC_DIR = re.compile(r"^(new folder(\s*\(\d+\))?|music|my music|mp3s?|songs?|tracks?|audio|downloads?|"
                          r"ingested|unsorted|misc|stuff|files|media|temp|dump\w*|backups?|old\s.*|to sort|"
                          r"ssd|hdd|usb|external|flash|drive [a-z]|various.*|va)$", re.I)
_BATCH_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-", re.I)


def fix_case(s: str) -> str:
    """Fix ALLCAPS / all-lowercase tags (decades of rips); leave mixed case alone."""
    t = s.strip()
    if len(t) > 2 and (t.isupper() or t.islower()):
        return " ".join(w if (len(w) <= 3 and t.islower() and w in {"the", "and", "of", "a", "an", "in", "on", "for"}) else w.capitalize() for w in t.split())
    return t


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\x00", "").strip())


def _first(tags: Dict[str, str], *keys: str) -> str:
    for k in keys:
        v = clean_text(tags.get(k) or "")
        if v:
            return v
    return ""


def folder_identity(path: Path, root: Optional[Path]) -> Tuple[str, str]:
    """Artist/Album fallback from the near-universal folder convention
    .../Artist/Album/track.ext or .../Artist/track.ext. Only the two nearest
    meaningful parents are considered; generic dump/batch folder names are
    ignored. This is a FALLBACK — embedded tags and parseable filenames win."""
    try:
        parts = list((path.relative_to(root) if root else path).parent.parts)
    except Exception:
        parts = [path.parent.name]
    if len(parts) >= 2 and parts[0].lower() == "ingested" and _BATCH_DIR.match(parts[1]):
        parts = parts[2:]
    dirs = [d for d in parts if d and not _GENERIC_DIR.match(d) and not _BATCH_DIR.match(d)][-2:]
    if len(dirs) == 2:
        return dirs[0], dirs[1]
    if len(dirs) == 1:
        return dirs[0], ""
    return "", ""


def derive_identity(path: Path, tags: Dict[str, str], root: Optional[Path] = None) -> Dict[str, Any]:
    """Deterministic normalization. Returns artist/track_artist/album/title/track/
    year/compilation plus `identity_source` (tags | folder | filename) so callers
    can flag low-confidence rows."""
    path = Path(path)
    tags = {str(k).lower(): (v or "") for k, v in (tags or {}).items()}
    stem = path.stem
    if "_" in stem and " " not in stem:
        stem = stem.replace("_", " ")
    tag_artist = _first(tags, "albumartist", "album artist", "album_artist", "band", "artist")
    tag_title = _first(tags, "title")
    artist = tag_artist
    title = tag_title
    source = "tags" if (tag_artist and tag_title and not _JUNK_TITLE.match(tag_title)) else "filename"
    if _JUNK_TITLE.match(title):
        title = ""
    m_lead = re.match(r"^\s*(\d{1,3})\s*[-. _]\s*", stem)
    stem_no_num = _LEADNUM.sub("", stem)
    if not title or not artist:
        parts = [x.strip().replace("_", " ").strip() for x in re.split(r"\s+-\s+", stem_no_num) if x.strip()]
        if len(parts) >= 2:
            artist = artist or parts[0]
            title = title or " - ".join(parts[1:])
        else:
            title = title or (parts[0] if parts else stem)
    if _JUNK_TITLE.match(re.sub(r"[\s_]+", " ", clean_text(title))):
        title = ""
    folder_artist, folder_album = folder_identity(path, root)
    if not artist and folder_artist:
        artist = folder_artist
        source = "folder"
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
                source = "folder"
                title = title[:m_by.start()].strip() or title
    album = _first(tags, "album") or folder_album
    year = None
    m = _YEAR_SUFFIX.search(album)
    if m:
        year = m.group(0).strip(" []()")
        album = _YEAR_SUFFIX.sub("", album)
    album = fix_case(album) or "Unknown Album"
    track_artist = fix_case(_first(tags, "artist") or artist) or "Unknown Artist"
    artist = fix_case(artist) or "Unknown Artist"
    for pat in (" Feat. ", " feat ", " Ft. ", " ft. ", " FEAT. ", " Featuring "):
        artist = artist.replace(pat, " feat. ")
        track_artist = track_artist.replace(pat, " feat. ")
    aa = _first(tags, "albumartist", "album artist", "album_artist", "band").lower()
    compilation = (aa in VARIOUS) or (tags.get("compilation", "").strip() in {"1", "true"}) or (artist.lower() in VARIOUS)
    m = _TRACKNO.match(_first(tags, "tracknumber"))
    track = int(m.group(1)) if m else (int(m_lead.group(1)) if m_lead else None)
    title = fix_case(clean_text(title))
    if not title:
        title = f"Track {track:02d}" if track else clean_text(stem_no_num) or stem
    if artist == "Unknown Artist":
        source = "unknown"
    return {"artist": artist, "track_artist": track_artist, "album": album,
            "title": title, "track": track, "year": year, "compilation": compilation,
            "identity_source": source}


def safe_name(s: str, fallback: str = "untitled") -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", s or "")
    s = re.sub(r"\s+", " ", s).strip(" ._")
    return s[:120] or fallback
