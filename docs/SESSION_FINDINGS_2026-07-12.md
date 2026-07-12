# Session findings — 2026-07-12

Technical findings from continued use of the headless cleanup + identify CLI (v0.8.12–v0.8.15)
against a real ~5,400-track library, plus a legacy-workspace cleanup. Filed as a follow-up to
`LIBRARY_CLEANUP_SESSION_2026-07-11.md`. Personal library details omitted; this covers bugs and
behavior worth fixing or documenting upstream.

## Bug: `_acoustid_lookup`'s combined `meta` parameter silently drops all metadata

`earcrate/app.py`'s `_acoustid_lookup` requests `meta=recordings+releasegroups+compress` on every
call. In practice this returns a bare `{"id": ..., "score": ...}` with **no** `recordings` key at
all — even for high-confidence matches (0.93+) against well-known, well-cataloged tracks. Since
`_parse_acoustid` filters results to `if r.get("recordings")`, every single lookup came back with
`match: None`, i.e. `identify` reported 0/585 matches despite the underlying fingerprint matching
correctly.

Isolated by testing the same fingerprint with different `meta` values directly against the API:

- `meta=recordings` → full artist/title/recording data, reliably.
- `meta=recordings+releasegroups` → metadata disappears entirely.
- `meta=recordings+releasegroups+compress` (current default) → same, metadata disappears.

Root cause not fully isolated (plausibly an interaction between the combined meta value and
`urllib.parse.urlencode`'s escaping of the literal `+` separators, or an AcoustID-side quirk with
combined meta requests) — but the fix that actually works is to request `meta=recordings` alone.
The tradeoff is losing `releasegroups` (album title) in the response; artist/title still resolve
correctly. Given `identify`'s current 0% real-world hit rate on this call, requesting less metadata
reliably beats requesting more metadata unreliably. Worth a real fix (or at least the narrower
`meta` value) in `_acoustid_lookup` itself rather than every caller needing to route around it.

## Known failure mode: confident-looking false matches on classical recordings

Two independent false positives surfaced in this session, both at scores that read as confident in
isolation (0.936–0.986 typically look fine; these were not low-confidence edge cases):

- A Wagner "Ride of the Valkyries" file matched to a result whose `recordings[0].artists` field
  was empty/placeholder, causing `reorganize`'s filename-derived-identity fallback to invent an
  artist name from the German section-label text in the filename itself.
- A Bach cantata movement (identifiable from its original filename as BWV 147) matched to a
  completely different piece ("Agnus Dei") performed by an unrelated choir/orchestra credit.

Neither was catchable programmatically from the score alone — both looked like ordinary confident
matches. They were only caught because the resulting tag/folder text was legible enough to look
obviously wrong on manual review. Any bulk `identify` + `apply-identities` pass over a
classical-heavy library should budget time for a manual spot-check of the classical subset
specifically; AcoustID's fingerprint-to-metadata linking appears meaningfully less reliable there
than for popular/rock/electronic material.

## Gotcha: `apply-identities` → `reorganize` needs an intervening `scan`

`apply_identities` writes corrected tags to the audio files directly (via mutagen) but only
back-fills earcrate's SQLite tag cache when a `file_id` is present in the proposal. If `file_id` is
missing (see next item), the on-disk files are correct but the database still reflects pre-retag
tags. Running `reorganize` immediately afterward will silently plan against the stale cached data.
A `scan` between `apply-identities --apply` and `reorganize` is required, but nothing in either
command's output signals this — it's easy to get a `reorganize` dry-run that looks complete but
is actually working from stale identity data.

## Gotcha: driving `EarcrateCore()` from a standalone script resolves a different workspace pointer than the CLI

Building a small driver script that does `sys.path.insert(...); import earcrate; core =
earcrate.EarcrateCore()` to call `identify_tracks`/`_fingerprint_file` directly (e.g. to scope
`identify` to a subset of files, since the CLI's `identify` has no path-filtering option) resolved
a *different* config pointer than invoking `python dist/earcrate.py identify` from the same
directory. The proposals output landed in a legacy `AppData\Local\JukebreakerGT` workspace instead
of the actively-configured one, and every `file_id` in the output came back `None` — which is what
caused the previous gotcha. Root cause not isolated (something in `load_config_if_present()`'s
pointer resolution appears sensitive to invocation context in a way that isn't obvious from the
code), but worth knowing: don't assume a standalone script importing the package resolves the same
workspace as the CLI entry point. Verify `core.config.master_root` before trusting output paths.

## Legacy `JukebreakerGT` AppData workspace — findings from a full audit

For anyone else with a pre-rename Jukebreaker workspace still in `AppData\Local\JukebreakerGT`:
in this case it held ~13GB, of which ~9.75GB was `agent/archive/` (rollback-archived, i.e.
already-discarded, render output — safe to reclaim by definition) and ~2.9GB was `work/organized/`
(copy-with-amended-tags output that had become fully redundant once the live library was properly
retagged in place). The genuinely valuable content was 7 real mashup renders in `work/renders/`
(the version history immediately preceding the active workspace's later renders) — worth copying
forward before clearing the rest. The legacy workspace's database also held proportionally more
loop/atom/mashup analysis than a freshly-adopted workspace ends up with, but tied to a smaller/
different file set whose paths won't match after any subsequent reorganizing — not safely
mergeable; a fresh `scan → analyze → ear-crate` pass on the current library is the cleaner path to
recovering that richness if wanted.

## Status of prior open items (from 2026-07-11 log)

- Per-track loop cap (`extract_loops_one`, hardcoded `12`) and repetition/hook scoring: still not
  implemented in this pass either — understand this is being picked up separately.
- Demucs stem separation: still undecided (local CPU vs. cloud GPU vs. skip) — same, being handled
  elsewhere.
