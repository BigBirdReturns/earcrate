# crate-librarian

Turn a folder (or a whole SSD) of chaotic music into a clean, identified,
deduplicated, organized library — plus a stable machine-readable manifest
(`library.json`) any other project can consume.

Standalone and reusable by design: **mutagen** for tags, Python stdlib for
everything else. No audio analysis, no mashup engine, no UI, no network. This is
the library layer, extracted from EarCrate so it can be used on its own.
(EarCrate is consumer #1; it's destined for its own repo.)

## What it does

- **scan** — walk roots, read tags (mutagen), probe duration/codec, content-hash
  every file, and derive a normalized identity for each track even when it's
  untagged (decades-of-dumps heuristics: scene names, `NN - Artist - Title`,
  ALLCAPS repair, `(2001)` album-year, `Title by Artist` suffixes, the
  `Artist/Album/track` folder convention, `feat.` forms).
- **dedup** — mark byte-identical files by content hash; the first-seen copy is
  canonical.
- **organize** — build an `Artist/Album/NN Title.ext` archive of **copies** with
  amended tags. Compilations (incl. albumartist-less, detected by 2+ artists on
  one album) route to `Various Artists/Album/NN Artist - Title`. **Idempotent**
  (re-runs skip what exists — no ` (2) (3)` accretion), **journaled**, and
  **rollback-able**. Sources are never modified.
- **library.json** — the versioned contract every consumer reads
  (see `LIBRARY_CONTRACT.md`).

## Use it (CLI)

```bash
crate-librarian scan  "E:\Music" "F:\more"  --out library.json
crate-librarian report library.json                     # human summary
crate-librarian organize library.json --dest "D:\Archive"          # dry-run
crate-librarian organize library.json --dest "D:\Archive" --apply  # write copies
crate-librarian rollback "D:\Archive\.crate-librarian-journal.jsonl" --apply
```

`scan --no-hash` skips content hashing for a fast metadata-only pass (no dedup
ids). `--jobs N` sets hashing parallelism (default: cores).

## Use it (library)

```python
from crate_librarian import scan_roots, build_library, write_library, organize
recs = scan_roots(["E:/Music"], do_hash=True)
lib = build_library(recs, ["E:/Music"], generated_at="…")
write_library(lib, "library.json")
organize(lib, "D:/Archive", apply=True)     # dry-run without apply
```

## Install

```
pip install mutagen        # the only dependency
```

## Invariants (the safety contract)

1. **Sources are never modified.** Copy-then-edit; originals untouched.
2. **Dry-run by default.** `organize` writes nothing without `--apply`.
3. **Idempotent.** Re-organizing never duplicates the tree.
4. **Journaled + reversible.** Every copy is recorded; `rollback` deletes exactly
   what a run added.
5. **Deterministic.** No clock, no randomness, no network in identity or planning.

## Tests

```
python -m pytest crate-librarian/tests -q     # needs mutagen + ffmpeg (fixtures)
```

The test suite is the acceptance corpus: the nasty identity cases, a full
scan→dedup→organize→rollback pipeline, and the CLI.
