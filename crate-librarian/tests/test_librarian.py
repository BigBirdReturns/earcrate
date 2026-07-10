"""crate-librarian acceptance corpus. Runs standalone (mutagen + ffmpeg for
fixture creation). Proves: identity on the nasty cases, scan->library.json,
dedup, idempotent journaled organize, rollback, and the CLI."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crate_librarian import (derive_identity, scan_roots, build_library, write_library,
                             read_library, organize, rollback)


def _mk(path: Path, freq: int, **meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2"]
    for k, v in meta.items():
        args += ["-metadata", f"{k}={v}"]
    args.append(str(path))
    subprocess.run(args, check=True)


def test_identity_nasty_cases():
    root = Path("/lib")
    i = derive_identity(Path("/lib/The Front Bottoms/Au Revoir (Adios) by the Front Bottoms.mp3"), {}, root)
    assert i["artist"] == "The Front Bottoms" and i["title"] == "Au Revoir (Adios)" and i["identity_source"] == "folder"
    i = derive_identity(Path("/lib/Radiohead/OK Computer/02 Paranoid Android.mp3"), {}, root)
    assert i["artist"] == "Radiohead" and i["album"] == "OK Computer" and i["track"] == 2
    i = derive_identity(Path("/lib/Ben E. King/Stand by Me.mp3"), {}, root)
    assert i["title"] == "Stand by Me" and i["artist"] == "Ben E. King"
    i = derive_identity(Path("/lib/New folder/mystery.mp3"), {}, root)
    assert i["artist"] == "Unknown Artist" and i["identity_source"] == "unknown"
    i = derive_identity(Path("/x/song.mp3"), {"artist": "Portishead", "title": "Glory Box"}, root)
    assert i["artist"] == "Portishead" and i["identity_source"] == "tags"
    i = derive_identity(Path("/x/DAFT PUNK - ONE MORE TIME.mp3"), {"album": "Discovery (2001)"}, root)
    assert i["artist"] == "Daft Punk" and i["title"] == "One More Time" and i["album"] == "Discovery" and i["year"] == "2001"


def test_full_pipeline(tmp_path):
    ssd = tmp_path / "ssd"
    # scene-named, zero tags
    _mk(ssd / "dump1" / "02_-_daft_punk_-_harder_better_faster_stronger.mp3", 523)
    # ALLCAPS + year album
    _mk(ssd / "dump1" / "dp1.mp3", 440, artist="DAFT PUNK", album="Discovery (2001)", title="ONE MORE TIME", track="1")
    # albumartist-less compilation: same album, two artists
    _mk(ssd / "dump2" / "a.mp3", 262, artist="Outkast", album="Now 44", title="Hey Ya", track="4")
    _mk(ssd / "dump2" / "b.mp3", 294, artist="Kelis", album="Now 44", title="Milkshake", track="5")
    # byte-identical duplicate under another name (dump2 now exists)
    import shutil
    shutil.copy2(ssd / "dump1" / "dp1.mp3", ssd / "dump2" / "copy_of_dp1.mp3")
    # folder-convention, untagged
    _mk(ssd / "The Front Bottoms" / "Maps.mp3", 349)

    recs = scan_roots([str(ssd)], do_hash=True)
    lib = build_library(recs, [str(ssd)], generated_at="test")
    assert lib["count"] == 6 and lib["duplicate_count"] == 1, lib
    # the compilation clustered even with no albumartist tag
    now = [t for t in lib["tracks"] if (t["identity"]["album"] or "").lower() == "now 44"]
    assert now and all(t["identity"]["compilation"] for t in now), "2-artist album must be a compilation"
    # folder identity recovered the untagged artist
    fb = [t for t in lib["tracks"] if "Front Bottoms" in t["path"]][0]
    assert fb["identity"]["artist"] == "The Front Bottoms" and fb["quality"]["untagged"]

    libpath = write_library(lib, str(tmp_path / "library.json"))
    lib2 = read_library(libpath)
    assert lib2["contract_version"] == "1.0"

    dest = tmp_path / "archive"
    # dry-run writes nothing
    dry = organize(lib2, str(dest), apply=False)
    assert dry["dry_run"] and dry["planned"] == 5 and not dest.exists()
    # apply builds the tree; the duplicate is NOT copied (5 uniques)
    ap = organize(lib2, str(dest), apply=True)
    assert ap["copied"] == 5
    files = list(dest.rglob("*.mp3"))
    assert len(files) == 5
    assert (dest / "Daft Punk" / "Discovery" / "01 One More Time.mp3").exists()
    assert (dest / "Various Artists" / "Now 44").exists()
    assert (dest / "The Front Bottoms" / "Unknown Album" / "Maps.mp3").exists()

    # idempotent: second apply copies nothing
    ap2 = organize(lib2, str(dest), apply=True)
    assert ap2["copied"] == 0 and ap2["already_organized"] == 5

    # amended tags actually written to the copy
    from mutagen import File as MF
    mf = MF(str(dest / "Daft Punk" / "Discovery" / "01 One More Time.mp3"), easy=True)
    assert mf["artist"][0] == "Daft Punk" and mf["date"][0] == "2001"

    # rollback removes exactly what it made
    rb = rollback(ap["journal"], apply=True)
    assert rb["removed"] == 5 and not list(dest.rglob("*.mp3"))


def test_cli(tmp_path):
    ssd = tmp_path / "m"
    _mk(ssd / "Radiohead" / "OK Computer" / "02 Paranoid Android.mp3", 440)
    env_root = Path(__file__).resolve().parent.parent
    libp = tmp_path / "lib.json"
    r = subprocess.run([sys.executable, "-m", "crate_librarian.cli", "scan", str(ssd), "--out", str(libp)],
                       cwd=str(env_root), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lib = json.loads(libp.read_text())
    assert lib["count"] == 1
    r = subprocess.run([sys.executable, "-m", "crate_librarian.cli", "organize", str(libp), "--dest", str(tmp_path / "arch"), "--apply"],
                       cwd=str(env_root), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "arch" / "Radiohead" / "OK Computer" / "02 Paranoid Android.mp3").exists()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
