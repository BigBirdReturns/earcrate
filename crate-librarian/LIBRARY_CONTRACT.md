# library.json — the contract

This is the stable seam other projects consume. It is a plain JSON document; a
consumer needs no crate-librarian code to read it. Versioned by
`contract_version` (bumped only on a breaking change; additive fields are not
breaking).

## Top level

```json
{
  "contract_version": "1.0",
  "generated_at": "2026-07-10T12:00:00",   // caller-stamped local time, or ""
  "roots": ["E:\\Music", "F:\\more"],       // what was scanned (read-only sources)
  "count": 1882,                             // tracks scanned (incl. duplicates)
  "unique_count": 1740,                      // distinct by content hash
  "duplicate_count": 142,
  "error_count": 3,
  "errors": [ { "path": "...", "error": "..." } ],
  "tracks": [ Track, ... ]
}
```

## Track

```json
{
  "path": "E:\\Music\\...\\song.mp3",   // SOURCE path — never modified by this tool
  "sha256": "…64 hex…",                  // content id (null if scanned --no-hash)
  "size_bytes": 8123456,
  "duration_s": 210.5,                    // null if unreadable
  "codec": "mp3",
  "mtime": 1717000000,
  "identity": {
    "artist": "Daft Punk",               // album/primary artist (normalized)
    "track_artist": "Daft Punk",         // per-track artist (differs on comps)
    "album": "Discovery",
    "title": "One More Time",
    "track": 1,                          // int or null
    "year": "2001",                      // string or null
    "compilation": false,                // incl. album-level clustering
    "identity_source": "tags"            // tags | folder | filename | unknown
  },
  "tags": { "artist": "...", "album": "...", ... },   // raw embedded tags as read
  "archive_path": null,                  // set once organized into the clean tree
  "duplicate_of": null,                  // source path of the canonical copy, or null
  "quality": {
    "identity_source": "tags",
    "untagged": false,                   // identity came from folder/filename/nothing
    "unknown_artist": false,
    "unknown_album": false,
    "is_duplicate": false,
    "hash_error": null
  }
}
```

## Guarantees a consumer can rely on

- **`path` is read-only.** crate-librarian never modifies, moves, or deletes a
  source. Organized copies are separate files under a chosen dest.
- **`sha256` is the identity key.** Same bytes → same id, across roots and runs.
  `duplicate_of` points at the first-seen copy of identical bytes.
- **`identity` is deterministic.** Same inputs → same output, no clock or network.
- **`identity_source` is honest.** `tags` means embedded metadata; `folder`/
  `filename` mean it was inferred (lower confidence — `quality.untagged`).
- **Additive evolution.** New fields may appear at the same `contract_version`;
  removals or meaning changes bump it.

## Consuming it

```python
import json
lib = json.load(open("library.json", encoding="utf-8"))
playable = [t for t in lib["tracks"] if not t["duplicate_of"] and t["duration_s"]]
by_artist = {}
for t in playable:
    by_artist.setdefault(t["identity"]["artist"], []).append(t)
```

That is the whole integration surface. EarCrate is consumer #1; a future project
is consumer #2 — neither reaches around this file.
