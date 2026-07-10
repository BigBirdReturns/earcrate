# Ship-righting plan: deterministic taste compiler

## Current facts from inspection
- Package layout is modular (`earcrate/core`, `analyze`, `deck`, `ear`, `judge`, `librarian`, `ui`) with a single-file builder preserved.
- Runtime ledgers currently write under `agent/perf/`, while render reports write beside render WAVs and rejected renders move under `agent/rejected_renders/`. This is useful but does not yet satisfy the required `agent/runs/<run_id>/` bundle shape for every attempted run.
- The HTTP UI exposes scan, analyze, loop extraction, EarAtom build, readiness, graph, mashup propose/render, manifest execution, rollback, judge, and status APIs.
- Candidate disappearance risks found: loop extraction skips low-score segments; EarAtom build marks low-score atoms rejected; `compose_taste_arrangement` can fall back from preferred picks to indexed floors; `render_section_deck` records drops and continues for missing metadata, short clips, transform vetoes, transform errors, bar offsets, and unexpected render errors. Those render-time continues contradict the invariant that selected events must not silently disappear.
- Taste constants are partly centralized in `ear/readiness.py` and `TASTE_PROFILES`, but Girl Talk thresholds are not yet represented as a complete declarative, versioned profile with a stable hash.

## Current data-flow diagram
```text
UI/API request
  -> configure / scan master_root
  -> files + tracks tables
  -> analyze_file_worker ProcessPool + analysis npz cache
  -> features table
  -> extract_loops from downbeats/sections
  -> loops table
  -> build_ear_crate metrics + role classifier
  -> ear_atoms table + optional previews
  -> taste_readiness / choose_taste_deck transform feasibility
  -> build_compatibility_graph typed edges
  -> compose_taste_arrangement sections/layers
  -> arrangement_preflight_gate + taste_arrangement_gate
  -> mashups row + guarded render manifest
  -> render_mashup DSP
  -> render report beside WAV or rejected_renders quarantine
  -> judge_audio_file / status/perf ledger
```

## Source-of-truth hierarchy
1. Executable acceptance tests.
2. `JUKEBREAKER_SPEC_v2_CONSOLIDATED.md`.
3. Versioned TasteSpec profiles.
4. Architecture and rebuild plans.
5. `CHANGELOG.md`.

`BUILD_SPEC` and old addenda are historical inputs, not parallel constitutions.

## Failure map
| Area | Current risk | Required correction |
|---|---|---|
| Receipts | Split between `agent/perf`, manifests, render reports, and rejected render folders. | Create `agent/runs/<run_id>/` immediately and update artifacts throughout success and failure. |
| TasteSpec | Profile exists as Python constants plus persona docs. | Load versioned JSON profile, record version/hash in plans and render reports. |
| Curation | EarAtoms are listable/rankable, but durable judgment APIs are missing. | Persist approve/reject/relabel/favorite/lock without automated rescoring erasing judgments. |
| Pairing | Graph can be built, but pair review is not a first-class surface. | List pair reasons and persist pair judgments by profile version. |
| Composer | Deterministic but still section/layer based; not fully canonical plan rails. | Emit three-rail event model with transform feasibility receipts and alternatives. |
| Renderer | Some selected layers are dropped and rendering continues. | Treat selected-event nonexecution as an invariant failure with receipt. |
| Acceptance corpus | Existing tests are synthetic unit gates only. | Add generated fixture tooling and private manifest schema/template/evaluator. |

## Ownership by module
- `earcrate/tastespec`: declarative profile loading, schema hashing, provenance.
- `earcrate/app.py`: durable state, curation/pair judgments, constructive plan, exact render enforcement.
- `earcrate/ui/server.py`: thin API routes for curation, pairs, timeline, run visibility.
- `earcrate/ui/static/index.html`: Compose surface for atoms, pairs, timeline, run status.
- `tests/`: executable authority for gates, receipts, deterministic plan/render behavior.
- `profiles/`: versioned TasteSpec profiles and schema.
- `docs/`: architecture and migration receipts.

## Milestone sequence
1. Governance cleanup and `AGENTS.md`.
2. Versioned TasteSpec schema/profile and hash receipts.
3. Durable run bundle writer wired around compile/render attempts.
4. Curation and pair judgment APIs with local persistence.
5. Constructive three-rail canonical plan and exact saved timeline.
6. Renderer invariant: fail on selected event drop.
7. Synthetic and private acceptance-corpus tooling.
8. UI consolidation into one Compose surface and screenshots.

## Acceptance matrix
| Acceptance | Test/API exercise |
|---|---|
| TasteSpec version/hash recorded | `tests/test_tastespec_vertical.py::test_profile_hash_and_plan_receipt` |
| Human atom judgment persists | API/unit test writes `atom_judgments` and verifies atom status/relabel. |
| Pair judgment persists by profile | API/unit test writes `pair_judgments` for an edge. |
| Plan save/load canonical hash | Save and load the same plan and compare hash. |
| Existing gates preserved | `pytest -q tests/test_gates.py` and `python VERIFY_PACKAGE.py`. |
| UI/API exercised | Start loopback server and call profile/default/status routes with token. |

## First vertical slice scope in this branch
This branch establishes the enforceable constitution, versioned `girl_talk_v1` TasteSpec data, profile hashes in plans/render reports, local curation and pair judgment persistence, canonical plan save/load APIs, and tests that exercise the API/database path. The full renderer invariant and complete `agent/runs/<run_id>/` bundle remain follow-on work rather than being falsely claimed complete.
