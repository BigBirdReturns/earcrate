# JUKEBREAKER REBUILD PLAN — v1.0
**Constitution: executable acceptance tests first, then JUKEBREAKER_SPEC_v2_CONSOLIDATED.md, then versioned TasteSpec profiles.**
BUILD_SPEC v1.0 and ADDENDUM A v1.1 are historical inputs only. This plan is subordinate to the consolidated spec and must not reintroduce rescue, degraded, floor-safe, single-crate, or old-render fallback behavior.

---

## 1. Why rebuild, stated as evidence

The monolith (v0.6.3: 5,728 lines, 168 functions, 3 classes, 15% of the file an
embedded JS app in a string, 22 patch notes serving as the de facto architecture) has a
specific failure signature. Every major defect found in audit was a **visibility failure
caused by topology**, not a hard problem:

| Defect (version found) | Class | Would module structure have caught it? |
|---|---|---|
| 3 mode strings, 1 code path (v0.5.13) | dead alias | yes — an enum with one consumer is visible in a 100-line module |
| `downbeat_error_ms: 0.0` hardcoded (v0.5.4→13) | self-attested receipt | yes — receipts module + test asserting measurement |
| `min(lim, max(lim, x)) ≡ lim` (v0.5.13) | dead knob | yes — unit test on the budget function |
| scorer ignored sliders → identical renders (v0.5.13) | intent bypass | yes — intent-sensitivity gate as a test |
| single-threaded analyze vs spec §2 (all versions) | spec drift | yes — spec's perf gate as CI |
| suppress(Exception) force-fit (v0.3.1) | silent failure | yes — Addendum A8.2 lint ban, enforceable per-module |

Forensic audit found all of these. Structure should have. That is the rebuild case.

## 2. The one deliberate deviation, resolved honestly

The single-file constraint was a **distribution** decision (copy one file, run). It became
a **development** constraint by accident. Resolution: develop as a package, ship as one
file. `build/make_singlefile.py` concatenates the package (and inlines `ui/` assets) into
`jukebreaker_gt.py` deterministically; `VERIFY_PACKAGE.py` checks the artifact hash against
the source tree. Users still get one file. Developers get modules. Both specs' invariants
apply to the built artifact.

## 3. Repo layout (spec section each module implements)

```
jukebreaker/
  core/
    config.py        # §3 layout, config.toml, workspace scout (v0.6.3, kept)
    db.py            # §4 schema — CURRENT DB IS ALREADY COMPLIANT + ear_atoms
    journal.py       # INV-6: fsync jsonl, ulid, manifest sha
    manifest.py      # §5.1-5.2 op types + pydantic schemas
    executor.py      # §5.3 contract: prevalidate-all, all-or-nothing, inverses,
                     #   dry-run DEFAULT, rollback REPLAYER (recorded-only today)
    guards.py        # INV-1 path allow-lists, realpath/symlink escape rejection
  analyze/
    decode.py        # §2: ffmpeg is the only decoder
    features.py      # §6.3: compute_pcm_features (salvage, near-verbatim)
    workers.py       # ProcessPool + warmup_dsp + npz cache (salvage v0.5.13.2)
  librarian/
    scan.py normalize.py duplicates.py playlists.py   # §6.1-6.5, propose-only
  ear/                                # v0.6.0 TasteSpec, slotted under spec §7.1-7.2
    atoms.py         # EarAtom taxonomy = the loops.role enum matured
    taste.py         # TasteSpec contracts; girl_talk_v1 carries the DENSITY MODEL
                     #   (~5.5 samples/min, element per ~11s, 2-4 layers,
                     #    ≤20% per-source foreground) as named constants w/ citations
    extract.py       # §7.2 loop extraction: caps ≤12/track, HPSS gate, downbeat-only
    readiness.py     # crate_readiness_audit + girl_talk_targets (salvage v0.5.14)
  deck/
    transform.py     # varispeed-first planner + nearest_harmonic_shift (salvage)
    lattice.py       # score_bpm_lattice / build_bpm_lattice (salvage)
    transitions.py   # §7 primitives: beatmatch_blend, bass_swap, acapella_bridge,
                     #   impact_drop, hard_cut_pickup, hard_cut_to_air
    render.py        # two-deck tail-overlay model (v0.5.4 unlock), fade curves,
                     #   band-split bass handoff, A4: no suppress, no force-fit
  arrange/
    planner.py       # A3 energy arc: build/sustain/drop/breakdown/cut, A2 key eras
    score.py         # intent-targeting scorer (v0.5.14) — realized vs requested
    search.py        # §7.4 candidate search, seed-threaded (INV-5)
  judge/
    audio.py         # jb judge: rms_std/silence/low200/pitch-centers (Addendum A0)
    gates.py         # every acceptance gate as a callable, used by tests AND UI
  ui/
    static/          # the 56KB string becomes real .html/.css/.js files
    server.py        # loopback + token (kept), routes thin over core
  cli.py             # §8: every UI action's typer twin (scan/analyze/propose/apply/
                     #   rollback/doctor/judge) — MISSING today, spec requires it
  assist/            # §9: import-isolated, off by default (unbuilt today; stub + guard)
  mcp/               # §9: propose_manifest-only server (unbuilt today; stub)
tests/               # THE GATES, WRITTEN FIRST — see §5
build/make_singlefile.py
```

## 4. Salvage manifest (verified present in v0.6.3, ports with unit tests attached)

Near-verbatim: `plan_varispeed_transform`, `nearest_harmonic_shift`, `score_bpm_lattice`,
`build_bpm_lattice`, `crate_readiness_audit`, `girl_talk_targets` + density constants,
`compute_pcm_features`, `analyze_file_worker`, `warmup_dsp`, `krumhansl_key`,
`dj_bass_swap_blend` + fade curves, intent-targeting `score_arrangement`, tail-overlay
transition application, `fsync_append_jsonl`, path guards, doctor, workspace scout,
`ear_atoms`/`compatibility_edges` schema. **The SQLite file migrates as-is** (schema is
already spec §4 + v0.6.0 tables); npz caches keyed by content-sha migrate as-is; renders
and reports are plain files. Rebuild loses nothing analyzed or approved.

Cut permanently: mode aliases, Jam/Mashup as duplicate products, safe-deck rescue, floor-safe rescue, single-crate fallback, old-render fallback, all `contextlib.suppress` in deck/arrange/executor paths (A8.2),
per-section limiting (A3.4), the embedded-HTML string, patch-notes-as-architecture.

## 5. Build order = spec P1→P8, with tests FIRST

The durability mechanism you asked for: each phase begins by porting its gate into
`tests/` — the gate scripts already exist from this project (judge metrics, intent
sensitivity, twin-render provenance, budget-knob unit test, readiness honesty on a
40-song pool, kill -9 journal replay). CI runs with network disabled (INV-2 structural).

P1 core+scan (gate: fixture scan, zero writes outside agent_root, incremental) →
P2 analyze (BPM ±2 on ≥90%, parallel speedup ≥ (cores-2)×0.7, cache determinism) →
P3 manifest/executor/ROLLBACK REPLAYER (property: apply→rollback = byte-identical tree;
   path-escape fuzzing; kill -9 replayable) →
P4 librarian proposals → P5 ear extraction + review (≤12/track, HPSS precision ≥0.8) →
P6 deck+arrange+render (judge gates: rms_std ≥4.5dB, silence ≥1%, low200 ≥0.48,
   ≥4 pitch centers; intent-sensitivity: slider extremes flip the winner; downbeat
   error ≤5ms MEASURED) →
P7 stems → P8 assist/MCP stubs behind guards.

**Definition of done for rebuild v1.0.0:** the built single file passes every gate the
monolith passes today, plus P3's rollback replayer and the CLI twins the spec always
required — the two real spec gaps v0.6.3 still has.

## 6. What stays true from the specs, verbatim

INV-1 through INV-6. The §5 op-type closed set (extended only by spec amendment, not
code drift). §7.2's caps. A2.4's "reject, never force-fit." A3's mandatory breathing.
The §10 gate discipline. The spec remains the document you hand to any agent — this
plan is just the route back to it.
