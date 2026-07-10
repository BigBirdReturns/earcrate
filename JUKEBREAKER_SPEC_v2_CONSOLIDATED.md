# JUKEBREAKER — Consolidated Spec v2.0

**Supersedes** BUILD_SPEC v1.0 and ADDENDUM A v1.1. Those described an aspirational
manifest-gated architecture the code never implemented; they diverged from reality
and became fiction. This document describes what the code **actually is**, grounds
every threshold in **documented Girl Talk numbers**, and defines acceptance gates
that are **scripts you can run today** — not prose to hope gets followed. Durability
comes from executable gates, not aspirational architecture.

---

## 0. What this actually is

One Python file (`jukebreaker_gt.py`, ~3.7k lines) that grew organically v0.2→v0.5.13.
It is a local-first library analyzer + DJ/mashup compiler with an embedded loopback
web UI. It is **not** the multi-phase manifest system of BUILD_SPEC v1.0. The parts of
that spec worth keeping are the safety invariants below; the elaborate manifest phases
(P1–P8) were never built and this document stops pretending they were.

### Invariants that are real and enforced
- **Source immutability.** Master files are never modified; all output goes under the
  workspace. (Enforced by path checks in the executor + copy-then-edit.)
- **No network in core.** Zero outbound calls; UI binds `127.0.0.1` with a per-session token.
- **Determinism.** Same pool + seed + params → same arrangement (seed-threaded RNG).
- **Render provenance.** Engine version + arrangement SHA + seed stamped into filename
  and render report.

---


## 0A. Safety authority restored in v0.5.15

The spec is the canonical control surface for safety behavior. New musical mechanisms, including the varispeed lattice, intent-targeting scorer, and Girl Talk density audit, must fit inside these invariants rather than redefining them. The guarded executor is the enforcement boundary.

**INV-1: Source immutability and path containment.** Master files are never mutated. Manifest outputs are path-checked into the configured render or playlist roots before any operation runs. Rollback sources are likewise restricted to generated-output roots.

**INV-2: Local-only core.** The engine does not make outbound network calls. The browser UI binds loopback only and uses a per-session token.

**INV-3: Whole-manifest prevalidation.** Every operation type and destination is validated before the first write. Unknown operation types are rejected.

**INV-4: Inverse recording before mutation.** Apply-mode execution writes a rollback inverse before render or playlist mutation, so an interrupted run still leaves an undo receipt.

**INV-5: Dry-run default.** Manifest execution is non-mutating unless `apply=true` through the API or `--apply` through the CLI. The browser manifest table exposes DRY RUN separately from APPLY NOW and APPLY BG.

**INV-6: Fsync journals.** Operation, rollback, and applied-rollback records are appended through the fsync JSONL journal path.

**INV-7: Rollback executor.** Recorded generated outputs can be dry-run or applied through the same guarded path. Apply-mode rollback archives render, playlist, and render-report artifacts under `agent/archive/rollback` rather than deleting them.

**INV-8: UI/API/CLI twins.** The browser, HTTP API, and command line expose the same guarded execution semantics for `manifest` and `rollback`: dry-run by default, explicit apply for mutation.

## 1. The Girl Talk density model (the basis for EVERY threshold)

Readiness and arrangement targets are grounded in his catalogued albums, not invented:

| Album | Samples | Runtime | Density |
|---|---|---|---|
| Feed the Animals (2008) | ~300+ | ~53 min | ~5.7 / min |
| All Day (2010) | ~372 | ~71 min | ~5.2 / min |

**Derived constants** (in code as `GT_SECONDS_PER_EVENT`, `GT_SOURCES_PER_MINUTE`,
`GT_MIN_LAYERS`, `GT_MAX_LAYERS`):
- A new recognizable element roughly **every 11 seconds** (~5.5/min).
- **2–4** elements layered at once (a bed rider + 1–2 foreground).
- **~15–25 distinct source songs** per 4–5 min stretch (~5.5/min).
- Foreground hooks rotate fast; instrumental beds ride longer; any one source stays
  under **~20%** of a track as foreground.

**For a track of length T:** need ≈ `T/11` sample-events, ≥1 bed rider and ≥1 vocal/
harmony foreground available, and ≈ `5.5·(T/60)` distinct sources. For a 2-min sketch:
~11 events, ~11 sources, 2–4 layers.

**The "40 random songs" reality.** 40 songs yield ~80–120 raw loops — quantity is never
the problem. The bottleneck is **role balance**: random full-mix pop gives many `full`/
`harmony` loops but few **clean drum beds** and few **isolatable vocals** — exactly the
material Girl Talk hand-sourced and exactly what stem separation recovers. Readiness now
reports this honestly instead of asserting arbitrary role minimums.

---

## 2. What was CUT (and why) in this pass

1. **Invented readiness minimums** (`drum_anchor≥2, bass≥1`) — replaced by the density
   model above. The old "pool thin" verdict fired on healthy pools; it had no basis.
2. **Aliased mode controls.** `album_collision`, `two_world_continuum`, and `notorious_mode`
   were the same code path — toggling them changed nothing but the output hash. The
   mix-mode dropdown is now two honest choices: **two-world** vs **single-crate**. Creative
   character lives in the preset dropdown (which sets real slider values).
3. **Hard caps that killed sliders.** `pitch_budget = min(2,…)` and `stretch_budget =
   min(8.5,…)` silently ignored the knobs above the cap. Removed; user budgets are honored
   up to role-tier ceilings (the per-loop transform planner still enforces safety).
4. **The fixed-ideal scorer.** `score_arrangement` rewarded a constant ideal (always more
   diversity/pitch-centers/edits), so the same pool+seed always won regardless of intent.
   That was the root cause of "changed settings, got identical output." Replaced — see §3.

## 3. What was ADDED

1. **Intent-targeting scorer.** The arrangement score now rewards how closely the realized
   arrangement matches the requested sliders (chaos→edit density, drama→dynamic sections,
   genre_whiplash→source diversity, vocal_density→voice fraction), with true failures
   (transform violations, role leaks, dead-air, over-reuse) as the only hard vetoes.
   *Verified: high chaos/drama selects a choppy dynamic plan; low selects a calm one — the
   winner flips with the sliders.*
2. **Density-grounded readiness** (`crate_readiness_audit`, `girl_talk_targets`): reports
   have-vs-need sample-events, distinct sources, bed riders, and foreground, and names the
   real bottleneck (usually clean drums + vocals → recommends stems).
3. **Deck-quality varispeed already present** (v0.5.13) — tempo/pitch move together, residual
   synthetic pitch last. Retained.

---

## 4. Acceptance gates (run these; they are the spec)

All are scripts, not prose. CI should run them with the network disabled.

0. **Guarded executor realignment** — `jukebreaker-gt manifest <manifest>` returns a dry-run plan and writes no output; `jukebreaker-gt manifest <manifest> --apply` writes only under configured output roots and records rollback inverses; `jukebreaker-gt rollback` returns a dry-run archive plan; `jukebreaker-gt rollback --apply` archives generated outputs and sidecar render reports under `agent/archive/rollback`.
1. **Compile / self-test / package**
   `python -m py_compile jukebreaker_gt.py` · `python jukebreaker_gt.py --self-test` (expect
   `SELF_TEST_OK`) · `python VERIFY_PACKAGE.py` (expect `ok:true`).
2. **`jb judge <render.wav>`** — audio gates (from ADDENDUM A, still valid):
   rms_std ≥ 4.5 dB, silence_ratio ≥ 0.01, low200_share ≥ 0.48, distinct dominant
   pitch classes ≥ 4 over a 3-min render.
3. **Intent sensitivity** (new, the anti-homogenization gate): score the same pool under
   HIGH vs LOW chaos/drama; the selected arrangement's `realized_chaos`/`realized_drama`
   must move in the same direction as the request. If two renders with materially different
   sliders are bit-identical, this gate FAILS. (This is the exact bug that shipped in the
   1364 renders.)
4. **Readiness honesty** — on a role-balanced 40-song pool the audit reports `ready:true`
   with sample-events ≥ target; on a role-starved pool it reports the specific missing role
   with the Girl Talk basis, not a generic number.
5. **Provenance** — two default renders differ (seed auto-increments); `--seed N` twice is
   bit-identical.

---

## 5. Where to go next (honest backlog, not pretend-done)

- **Key eras in the audit path.** Readiness scores usability against native-pitch varispeed
  (correct — the arranger's harmonic router places loops near native key). If the router is
  ever tightened to a single key, readiness must move with it or it will over-report.
- **`chord_dna` is still a receipt, not analysis.** Loops are shifted to era keys rather than
  selected by measured chroma trajectory. Real harmonic matching is the next quality jump.
- **Varispeed DSP** is `np.interp` (no anti-alias on speed-ups). `resample_poly`/`soxr` is a
  one-line upgrade to true deck-quality resampling; audible only above ~8%.
- **Stems** remain the highest-leverage unlock for the "40 random songs" case: they convert
  the scarce roles (clean drums, isolated vocals) from rare to abundant.

## 0B. Audible rescue policy added in v0.5.16

The fast-fail contract is preserved for expressive candidates: structurally bad or preflight-failed expressive plans should be rejected before a full WAV render whenever possible. The product contract is also explicit: one-click jam must not treat silence as the only safe outcome if a conservative audible deck can be made.

When expressive and repaired candidates fail, the engine may compile a floor-safe rescue. This rescue is allowed to reduce ambition before reducing safety: it may abandon two-world separation, lower chaos and edit density, use a single-crate deck, limit auxiliary decks, and constrain tempo/key movement. It must still write only under the configured output roots and must still pass the post-render quality gate before it is loaded into the player.

The floor-safe rescue is therefore not a degraded bypass. It is a minimum viable musical output path that keeps receipts, preserves output-root safety, and lets the gate reject catastrophic audio while avoiding a user-hostile empty run.


## v0.5.17 Addendum: Audible Truth Gate

A render cannot pass because it is merely the requested duration. For any render of one minute or longer, the post-render gate must measure absolute audible coverage, first audible material, largest silent gap, and active timeline ratio. The arrangement preflight must reject structurally empty plans before full WAV rendering when planned layer coverage, layer event count, first-layer timing, or required vocal identity are insufficient.

## v0.6.0 TasteSpec Addendum

The canonical unit of arrangement is no longer a raw loop candidate. The canonical unit is an EarAtom: a downbeat-aligned phrase cell with an explicit ear role, source identity, transform cost, spectral profile, salience metrics, and auditionability. Rendering from unclassified raw audio is a violation of the TasteSpec contract.

The first TasteSpec profile is `girl_talk_v1`. This profile encodes a dense collage target through deterministic obligations rather than mood labels. The accepted render must be built from a floor rail, a foreground rail, and a spark rail. The floor rail maintains drums, bass, or harmonic bed. The foreground rail carries recognizable hooks, verses, shouts, or identity riffs. The spark rail carries short fills, hits, tails, and comic events. A plan must satisfy coverage and source-turnover rules before it can write a WAV.

The compiler must run in this order: scan, analyze, extract phrase candidates, build ear crate, build compatibility graph, compose rail plan, run pre-render gates, execute guarded manifest, run post-render audible gate. If any earlier stage cannot satisfy the profile, the system must refuse with missing material receipts. It must not load an old render, a degraded render, a floor-safe substitute, or a single-layer artifact as success.

The compatibility graph is typed. Vocal-over-bed, bass-over-drums, and spark-into-phrase are different relations with different cost functions. Edges must account for phrase alignment, harmonic relation, transform budget, low-end conflict, midrange masking, intelligibility, bed usefulness, and source contrast. The graph is a deterministic receipt of why a collision was allowed.

The Girl Talk profile is not the end state. It is the first acceptance test for the general TasteSpec system. Additional profiles may change weights and rules, but they must preserve the same contract form: named roles, measurable gates, deterministic edge scoring, and explicit failure receipts.


## v0.6.1 TasteSpec Feasibility Invariant

The TasteSpec compiler MUST NOT compose from the approved ear crate directly. Approval means the phrase atom is musically salient. It does not mean the atom is playable at the selected BPM/key. Before composition, the compiler must choose a tempo/key deck that maximizes feasible foreground, floor, bass, spark, and source-turnover coverage, including half-time and double-time tempo aliases. The composer may only select atoms from this transform-feasible pool. If the feasible pool cannot satisfy the profile, the engine must expand the harvest or refuse with missing-role diagnostics; it must not build an empty timeline and hope that render-time gates catch it.
