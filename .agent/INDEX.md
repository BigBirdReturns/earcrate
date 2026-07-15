# EarCrate — Dewey index

One-screen catalog of where things live, so hands don't re-scan the tree every
loop iteration. This is a map, not a duplicate — read the linked file for content.

## Constitution (binding, in priority order)
1. Executable acceptance tests (`tests/`)
2. `JUKEBREAKER_SPEC_v2_CONSOLIDATED.md` — what the code actually is, grounded
3. Versioned TasteSpec profiles (`profiles/*.json`) + their schema
4. `EARCRATE_REBUILD_PLAN_v3.md` — binding architecture plan, supersedes v2
5. `AGENTS.md` — nonnegotiable rules (no rescue/degraded/fallback behavior, no
   silent gate-lowering, determinism, path containment, etc.)
6. `CHANGELOG.md` — what actually shipped, in order

`BUILD_SPEC`, `JUKEBREAKER_REBUILD_PLAN_v1.md`, `ADDENDUM`-era docs, and
`EARCRATE_REBUILD_PLAN_v2.md` are historical inputs only — do not treat as current.

## Product intent
- `PRODUCT.md` — what EarCrate is, one paragraph
- `MILESTONES.md` — long-view roadmap (perceptual validation, consumer polish,
  monolith decomposition — read the tail for "what's next")
- `PERSONAS/*.md` — acceptance personas (Girl Talk is first, not the whole product)

## Current-state snapshots (regenerated, not hand-maintained — read, don't edit by hand)
- `docs/AGENT_HANDOFF.json` — living QA ledger: root-cause analysis, readiness
  ledger, roadmap steps, and a long `qa_findings[]` array of confirmed/unverified
  bugs with file:line evidence. THE place to check "is this already known broken."
- `docs/SHIP_RIGHTING_PLAN.md`, `docs/QA_ROADMAP.md` — remediation sequencing
- `docs/PERF_CAMPAIGN.md` — perf work, v0.8.30 chapter
- `SESSION_HANDOFF_2026-07-13.md` — most recent debug context dump

## Library/ingest domain
- `LIBRARY_WORKFLOW.md` — external drive → archive → ear-crate pipeline (source of
  truth for the ingest job)
- `docs/LIBRARY_MANIFEST.md` / `.json`, `docs/LIBRARY_ARTISTS.json`,
  `docs/HERITAGE_MAP.md`, `docs/LIBRARY_FLIP_MAP.md`, `docs/LIBRARY_SAMPLE_GRAPH.md`
  — library-state artifacts, mostly generated

## Reconstruction lab (separate sub-project — see memory: earcrate-reconstruction-contract)
- `.tmp/empire_reconstruction/RECONSTRUCTION_CONTRACT.md` — every heard failure
  codified as a checked invariant; violations refuse to render
- `.tmp/empire_reconstruction/ref_tracking.py`, `fidelity_v2.py` — verification gates

## Code
- `earcrate/` — the app (app.py = core engine/routes, `ui/` = server + static
  frontend, `judge/` = render verification gates)
- `crate-librarian/` — library tooling
- `scripts/` — one-off/perf scripts (`perf_baseline.py` etc.)
- `tests/` — acceptance tests, highest-priority source of truth

## This harness
- `.agent/PLANNER_LOOP.md` — the read → ask-planner → act → verify → log protocol
- `.agent/journal/` — one append-only log per loop session, dated
