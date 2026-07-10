# EARCRATE REBUILD PLAN v2 — "fully fully," lessons encoded

**Constitution:** executable acceptance tests → `JUKEBREAKER_SPEC_v2_CONSOLIDATED.md`
→ versioned TasteSpec profiles (`profiles/*.json`) → this plan. AGENTS.md rules
are unchanged and non-negotiable. This plan supersedes REBUILD_PLAN_v1's layout
sections; it does not change the invariants.

## 0. Why a v2 rebuild

v0.7.x proved the product (residents, curation loop, receipts, one design
system) by *renovating a monolith mid-flight*. Every defect we hit is now a
gate, but the shape that caused them is still there. v2 rebuilds to the shape
that would not have produced them — and cuts the library engine loose as a
standalone asset.

## 1. Lessons ledger (each one shaped v2; each already has a gate)

| # | What happened | Root shape-flaw | v2 answer |
|---|---|---|---|
| 1 | renders 4× target length | arithmetic buried in a 2,000-line method | pure `plan/` module: every formula is a small function with a unit gate |
| 2 | scorer blind to vocals | two vocabularies (`world` vs `role`) for one concept | ONE layer model, typed; no legacy twins |
| 3 | organize duplicated trees with ` (2) (3)` | mutation without identity | every derived artifact has a deterministic identity; re-runs are upserts |
| 4 | Browse called an endpoint that never existed | UI and API drift apart silently | API schema is generated from one route table; UI calls only named routes; e2e clicks every button in CI |
| 5 | personas erased each other's crates | `UNIQUE(loop_id)` — identity missing a dimension | identity keys carry ALL their dimensions from day one: `(loop, profile)`, `(edge: profile,left,right,relation)` |
| 6 | 2-hour second audition | measurement conflated with judgment | measurements (persona-independent DSP) stored ONCE per loop in their own table; personas store only judgments referencing them |
| 7 | judgments erased by regraphs/rebuilds | derived tables deleted-and-recreated with random ids | human judgment tables are append-only and keyed by deterministic identities; derived data may churn, judgment never |
| 8 | serial DSP on one core | compute paths grown ad hoc | every per-file compute goes through ONE parallel harness (decode-once, ProcessPool, ETA, receipts) |
| 9 | "is it stuck?" | status strings instead of structured progress | progress is structured (`stage, i, n, eta_s`), rendered anywhere, never parsed from prose |
| 10 | hidden AppData outputs, unreadable JSON receipts | machine-first surfaces | every receipt has a human sentence + WHERE + open-folder; paths are visible by default |
| 11 | version never changed / "did the update land?" | provenance as an afterthought | version + content-hash stamped at build in all three places (page, header, dist), checked by a gate |
| 12 | two persona sources of truth diverging | constants in code AND docs AND JSON | JSON is the only source; projections + drift gates (already law; v2 keeps it) |

## 2. The cut: two packages, one product

```
crate-librarian/          ← THE REUSABLE BUFFALO (its own repo when ready)
  scan/       parallel probe: tags, duration, codec, content-hash ladder
  identify/   folder-convention + filename heuristics; opt-in AcoustID/
              MusicBrainz online identify (the ONLY network-touching module,
              off by default, per the approved plan)
  organize/   Artist/Album/NN Title archive builder — idempotent, journaled,
              rollback-able, copy-then-edit, compilation clustering
  dedupe/     size-ladder + content-hash
  api/        library.json receipts + a thin CLI: `crate-librarian ingest|
              organize|identify|report`
  NO audio-analysis, NO personas, NO UI — consumable by ANY project
  (EarCrate is consumer #1; your next project is consumer #2)

earcrate/                 ← the instrument (depends on crate-librarian)
  measure/    decode-once parallel DSP → measurements table (persona-free)
  personas/   TasteSpec JSONs + loaders (unchanged law)
  judge/      human judgments: atoms, pairs, locks — append-only
  plan/       pure composition math (bars, rails, turnover, endless)
  render/     exact renderer: a saved plan renders byte-identically; a
              selected layer that cannot render is an invariant FAILURE
  station/    receipts, bias, activity (structured progress)
  ui/         Residents / Crate / Sessions / Activity / Workbench (AXM flavor)
```

**Library contract** (the seam your next project consumes): a `library.json`
receipt per root — every track with identity, tags, content-hash, quality
flags, and where its archive copy lives. Stable, versioned, documented. The
mashup engine reads the same contract; nothing reaches around it.

## 3. Attachment surfaces (prepared, not promised)

Explicit seams with registered no-op defaults, so future work plugs in
without surgery:

- **stems**: `measure.stem_provider` — input track, output stem WAVs +
  provenance; unlocks Notorious readiness, vocal variety, lyric work, parody.
- **identify**: `librarian.identify_provider` — offline heuristics default;
  AcoustID/MusicBrainz opt-in provider (approved earlier, network stays out
  of core).
- **progression**: `measure.chord_provider` — chord-progression extraction;
  unlocks the Troubadour's real selection key (I–V–vi–IV family matching).
- **transcribe**: `measure.lyric_provider` — stems-gated; unlocks lyric-aware
  splices and parody scaffolding.
- **persona learning**: `judge.twin_builder` — station receipts + judgments →
  a learned resident ("My Twin"), beliefs stored as receipts, deletable.
- **export**: `station.export` — plans/sets to M3U/DAW markers.

## 4. Migration (nothing the user made is lost)

1. crate-librarian extracted verbatim from `earcrate/librarian` + scan/dedupe
   (tests move with it; torture suite becomes its acceptance corpus).
2. Workspace adoption: existing DB migrates — files/tracks/features stay;
   loop measurements backfill the measurements table from `metrics_json`
   (already persona-independent as of v0.7.8); judgments carry over verbatim.
3. Renders, plans, journals: untouched on disk.
4. The v0.7.x app keeps working until v2 passes the SAME gate suite plus the
   new ones; no cutover before green.

## 5. Order of work

1. **Extract crate-librarian** ✅ DONE (v0.7.9) — standalone `crate-librarian/`
   package (mutagen-only), `library.json` contract + `LIBRARY_CONTRACT.md`, CLI
   (scan/report/organize/rollback), own acceptance corpus, cross-agreement gate
   with earcrate. *Point it at the SSD now; your next project reads library.json.*
2. Measurements table + adopt/parallel harness as the only compute path.
3. `plan/` purification (composition math out of EarcrateCore, unit-gated).
4. Exact renderer + selected-layer-drop = failure (closes SHIP_RIGHTING's
   renderer invariant).
5. Route-table API + UI e2e that clicks every control in CI.
6. Attachment seams registered with no-op defaults + docs.
7. Cutover gate: full suite + browser e2e + a real-library soak.

## 6. What v2 does NOT do

No streaming-DJ pivot (the collage/medley compiler is the product); no
network in core (identify stays opt-in, isolated in the librarian); no new
personas beyond data files; no rewrite of DSP that already passes gates —
working buffalo moves, it doesn't get re-shot.
