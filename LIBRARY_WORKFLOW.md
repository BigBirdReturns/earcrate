# LIBRARY WORKFLOW — external drive → archive → ear crate

The exact sequence for the real job: an external SSD with thousands of songs that
need to be scanned, deduplicated, relabeled, retagged, and laid out as (a) a
listening archive and (b) source material for the mashup engine. Every mutating
step is **dry-run by default**, journaled, and rollback-able. **Source folders are
never modified** — the engine only ever copies.

Verified end-to-end (v0.7.3): ALLCAPS/lowercase tag repair, scene-name filename
recovery (`02_-_artist_-_title.mp3` with zero tags), junk `Track NN` title
rejection, `(1998)` album-year extraction into the date tag, `feat.`
canonicalization, albumartist-less compilation clustering, byte-identical dupe
rejection with size-ladder hashing (only colliding sizes get read), idempotent
re-ingest (second run plans 0 copies).

## 0. One-time setup

```
pip install -r requirements.txt        # needs ffmpeg/ffprobe on PATH too
python build\make_singlefile.py        # or just double-click START_HERE.cmd
python dist\earcrate.py                # opens the local UI (127.0.0.1 only)
```

Pick the workspace in the setup panel — or let the engine score candidates for
you first:

```
python dist\earcrate.py workspace-candidates --music "D:\"
```

Hard rules it enforces: workspace and music folder must not contain each other;
sync-managed folders (OneDrive/Dropbox/…) are flagged because they fight the
fsync journals and SQLite.

## 1. Ingest the SSD (copy in, dedupe, never touch the source)

Dry-run first — this is the default and it prints the full plan as a manifest:

```
python dist\earcrate.py ingest "E:\old_dump" "E:\backup2019" "E:\misc_music"
```

Read the plan (`planned`, `skipped_duplicates`, `hashed_for_dedupe`). Then apply:

```
python dist\earcrate.py ingest "E:\old_dump" "E:\backup2019" "E:\misc_music" --apply
```

What happens: audio lands under `master/ingested/<batch>/<source-folder>/…`,
content-hash-deduped against the entire existing library *and* within the batch.
The size ladder means a 500 GB drive is not read twice: a file whose byte size
collides with nothing is copied and verified at copy time, not pre-hashed.
Re-running the same command later is safe — already-ingested files plan to 0.

Scan and analysis kick in automatically after apply (parallel ffprobe across
cores; ~50k files is hours, not days).

## 2. Organize + retag (the listening archive)

```
python dist\earcrate.py organize            # dry-run: prints the Artist/Album tree preview
python dist\earcrate.py organize --apply
```

Builds `working/organized/Artist/Album/NN Title.ext` **copies** with amended tags
(artist / albumartist / album / title / tracknumber / date). Masters stay
verbatim — copy-then-edit, per spec. Compilations are detected two ways
(albumartist says VA, *or* 2+ distinct track artists share one album name) and
routed to `Various Artists/Album/NN Artist - Title` with albumartist amended on
the copies. That `organized/` tree is the archive: browseable, correctly tagged,
ready for any player or for syncing back out to the SSD.

Undo at any time: `python dist\earcrate.py rollback` (dry-run) then
`--apply` — generated outputs are archived, never deleted.

## 3. Derive compilation folders / crates for the engine

Two supported paths today:

- **Playlists** (query-driven, written under `working/playlists`): propose from
  the UI Library tab or API — e.g. by BPM window, key, energy — then the playlist
  is itself a compilation receipt you can copy from.
- **Re-ingest a subset**: any folder you assemble (from the organized tree or
  elsewhere) can be ingested as its own batch and ear-crated separately.

Then the mashup pipeline consumes the library in this exact order (the compiler
enforces it):

```
scan → analyze → extract loops → build ear crate → compatibility graph
     → compose rails → pre-render gates → guarded render → post-render gates
```

```
python dist\earcrate.py taste-readiness            # can this crate do girl_talk_v1?
python dist\earcrate.py ear-crate --previews       # audition WAVs for the atoms
python dist\earcrate.py taste-graph                # deterministic edge receipts
```

`taste-readiness` now includes the **endless receipt** (see
`PERSONAS/GIRL_TALK_V1.md` §8): how many seconds the crate sustains at Girl Talk
density before a source must recur, which resource is the bottleneck, and exactly
how many more deck-safe sources you need. Rule of thumb from the persona math:
**~83 deck-safe distinct songs = honestly endless**; a few hundred well-tagged
songs off the SSD clears it comfortably.

## Scale notes for "thousands of songs"

- Dedupe cost scales with *collisions*, not library size (size-ladder).
- Scan/probe is parallel across cores; analysis caches per-file (`.npz`) so
  re-runs are incremental.
- Everything mutating goes through the guarded executor: whole-manifest
  prevalidation, rollback inverse recorded *before* each write, fsync journals.
  Power loss mid-ingest leaves receipts, not corruption.

## Agent-driven cleanup (Claude Code local, CLI, dry-run by default)

Run these from a machine that can see your drives. Every destructive step is
dry-run unless you pass `--apply`, and JSON is printed for the agent to read.

```
python dist\earcrate.py configure --music "E:\Music"      # once; persists. workspace defaults to a visible sibling
python dist\earcrate.py scan                                # index the drive into the DB
python dist\earcrate.py deepclean                           # LISTEN to each file: real songs vs silence/static/corrupt; find empty + art-only folders (assessment only)
python dist\earcrate.py reorganize                          # dry-run: exact in-place Artist/Album/NN-Title move plan (+ signature)
python dist\earcrate.py reorganize --apply --signature <SIG># execute the approved plan; journaled + reversible
python dist\earcrate.py reorganize-rollback <journal-path>  # undo it
```

`deepclean` is genre-blind: spoken word, classical, and lo-fi all pass; only
silence, broadband static, non-decodable/corrupt files, and sub-1s fragments are
flagged. `reorganize` never deletes — unidentifiable files go to `_unsorted/`.
Online music identity (AcoustID/MusicBrainz) is a separate opt-in that needs
network + `fpcalc` + a free key on the machine.

### Online identity (opt-in: AcoustID/MusicBrainz)

Fixes lying tags and playlist-name-as-artist by identifying the recording from
the audio itself. Prereqs: `fpcalc` (Chromaprint) on PATH, and a **free** client
key from https://acoustid.org/new-application.

```
set EARCRATE_ACOUSTID_KEY=your_key_here      # or pass --key
python dist\earcrate.py identify --limit 50  # dry-run: proposes artist/title/album/mbid per file
```

`identify` is dry-run only — it proposes identities (nothing written). Rate-limited
to ~3 req/sec. Feed the confident matches into a reorganize/retag pass to correct
metadata on disk.
