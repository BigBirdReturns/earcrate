# Library cleanup session — 2026-07-11

Record of a Claude Code (local CLI) session that ran earcrate's headless cleanup
CLI against a real library (5,407 files / ~336.8 hours of audio), plus what was
discussed but not yet built. Written so a separate Claude session (web or
otherwise) can pick up the context by reading this file. Local file paths and
machine identifiers have been generalized/redacted below — this repo is public.

## What actually ran, in order

1. **`configure --music <music-folder>`** — workspace created as a sibling
   folder named `<music-folder-name> — EarCrate` (note: **em dash**, not a
   hyphen or en dash — see "Mistake made and fixed" below).
2. **`scan`** — 5,382 audio files indexed at the time (grew to 5,407 after
   reorganize touched some paths); 1 file failed to decode.
3. **`deepclean`** (assessment only, nothing moved) —
   - 5,381 real songs, 1 junk (corrupt, undecodable MP3)
   - 0 empty folders
   - 158 art-only folders (leaf folders with files but zero audio anywhere below —
     verified by hand: every one of the 158 contained only jpg/png/pdf/nfo/txt/
     m3u/cue/torrent/accurip/wpl/sfv/log/ini/db/url files, no audio in any
     unsupported extension either — confirmed safe before deleting)
   - 954 loose image files, 169 sidecar files (counted, not touched)
4. **`reorganize`** dry-run → 168 files planned to move in-place into
   `Artist\Album\NN-Title`, 5,216 already conforming, 0 quarantined to
   `_unsorted/`. Signature `0f1b522d89d9bef2c46dea4ccd243030d8e12cabd7c21cc70c3b989041c36688`.
5. **`reorganize --apply`** with that signature — 168 moved, 0 errors. Journal
   saved under `agent\reorg_journal\` (undo via `reorganize-rollback <journal>`).
6. **Manual deletion** (not an earcrate feature — done via the Windows Recycle
   Bin / `SHFileOperationW` with `FOF_ALLOWUNDO`, so it's recoverable) of the
   158 art-only folders and the 1 corrupt file identified by deepclean. Logged
   in full (every path) at `agent\cleanup_log.txt` on the local machine.

Net result: the library is deduped/tag-organized in place, originals never
touched except the explicit reorganize move + the recycle-bin deletions above,
everything reversible except the recycle-bin step (which is itself recoverable
from the Recycle Bin, just not through earcrate's own journal).

## Pre-existing data found in the workspace (NOT generated this session)

The freshly-created workspace database already contained:

- **288** rows in `features` (BPM/key/loudness/energy/vocal_likelihood/section
  labels), `analyzer_version = "gt-v0.6.1-earcrate-feasibility"`,
  `analyzed_at` between `2026-07-11T05:40:58Z` and `2026-07-11T06:49:23Z`.
- **3,453** rows in `loops` (all `status = candidate`, none approved), role
  breakdown: vocal 2400, bass 518, texture 282, full 150, harmony 57,
  drum_anchor 46. Bar-length breakdown: 1-bar 303, 2-bar 511, 4-bar 2571,
  8-bar 68. Average ~12 loops/track — matches the (at-the-time) hardcoded
  per-track cap exactly.

Those timestamps land right around the `v0.8.12` merge
(`936c3c7`, merged `2026-07-11T07:05:50Z`) — this looks like validation/
feasibility data from building or testing that release against a real
library, not something this session produced. Flagging clearly so it isn't
mistaken for output of the steps above. It does mean chorus/section labeling
(`earcrate/analyze/features.py::_estimate_sections`, labels sections including
`"chorus"` by energy) already exists in the codebase — relevant to the
"hook detection" discussion below.

## Discussed, decided, or planned — not yet implemented

- **Per-track loop cap.** `extract_loops_one` in `earcrate/app.py` hard-caps
  selected loops at `12` (three literal occurrences, ~lines 1462/1478/1484).
  Plan discussed: remove the cap (or raise it) and add a repetition/
  self-similarity scoring pass on top of the existing `vocal_likelihood` +
  loopability scoring, to bias toward chorus/hook material rather than pure
  loopability. **Not yet coded.**
- **Vocal/instrumental stem separation.** Does not exist in this codebase —
  no Demucs/Spleeter/PyTorch dependency anywhere; `vocal_likelihood` is a
  heuristic scalar used only for loop-role tagging, not real source
  separation. A UI warning string ("turn on stem separation") references a
  feature that was never built.
- **Compute-location clarification.** The user initially assumed more Claude
  usage/tokens could substitute for local CPU/GPU compute (e.g., run Demucs
  "on Claude" instead of the local machine). Clarified: this session's tool
  calls execute on the local machine (a 14-core/18-thread CPU with integrated
  graphics only — no discrete/CUDA GPU). Real Demucs separation across a
  ~336.8-hour library would be a genuinely multi-day CPU-bound job locally,
  or would need a rented cloud GPU instance to run in hours instead of days.
  **No decision made yet on whether to pursue this** (options left open:
  skip stems / run on local CPU over days / rent a cloud GPU).
- **Ingest pipeline.** The mashup-engine `ingest` command was considered but
  correctly identified as unsuitable here: `master_root` was configured as
  the live library itself (not a separate managed archive), so running
  `ingest` on it would have copied ~5,200 files into a nested duplicate
  inside itself. Not run. If real ingest is wanted later, either point it at
  a genuinely external/unmanaged source, or reconfigure with a separate
  `master_root` first.

## Mistake made and fixed, for the record

While writing an earlier version of the cleanup log, a hand-typed path used
an **en dash** (`–`, U+2013) instead of the **em dash** (`—`, U+2014) that
`configure_workspace` actually used for the default workspace folder name.
This silently created a second, near-identical, mostly-empty folder alongside
the real workspace folder. Caught because the user noticed two
similarly-named folders and asked about it. Fixed: log file moved into the
correct (em dash) workspace, stray folder removed. Take-away: don't hand-type
paths containing non-ASCII punctuation — read the real path back from a
config/API response instead of retyping it.

## Where things actually live (local machine only, not in this repo)

- The music library itself (~336.8 hours — far too large and not appropriate
  for source control) stays entirely local, never committed.
- The workspace database, journals, and cleanup log also stay local — outside
  the repo tree entirely.
- The repo clone used for this session was a fresh clone of
  `BigBirdReturns/earcrate` at `936c3c7`, working tree otherwise clean.

A separate Claude session (web or another machine) will **not** see the
music library or the workspace database — those never leave the local
machine, by design. It will see whatever is committed to this repo,
including this file.
