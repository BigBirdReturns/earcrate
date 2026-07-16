# EarCrate — order of work

> **Superseded by `EARCRATE_0997_PLAN.md`** (post-v0.9 roadmap). §3's
> editable-project slice shipped as the v0.9 immutable-project cutover; the
> remaining open themes here (perceptual validation, consumer polish, the §5.3
> teardown) are re-sequenced there. Kept for lineage.

The control question for any change: **can it prove, on the real machine and the
real library, that EarCrate produces better audio, a smoother listening
experience, or meaningful creative control over a musical decision?** Gate count
and "a method was called" do not answer that question.

§5.3 (the generic monolith table teardown) is **LAST**, not next: a table
teardown before the project model exists risks clean modules around the wrong
abstraction.

---

## 1. Make the current vertical path truthful  ← immediate, blocks PR #25 merge
PR #25 is an architecture-and-safety increment, not a completed stem feature. Its
claims must become accurate before it merges. Either **complete** the stem path or
keep it **split-and-labelled as infrastructure with the feature OFF** (done in
v0.8.25).

**Completion criteria (the "stem feature works" definition of done):**
- A real `_run_demucs` invocation (Demucs on torch+CUDA), guarded.
- Optional `torch`/`demucs` dependency + a documented install path.
- Explicit provider selection through workspace config (choose `demucs`), not the
  hard-coded `noop` default.
- A user-visible **capability probe** (can torch+demucs+CUDA load?) surfaced in
  Setup/doctor — honest on a no-GPU box.
- **One workspace-scoped persistent `ArtifactStore`** injected into BOTH the
  provider and the renderer (kill the two-temp-dirs bug).
- A **cache lookup before separation** (don't re-run Demucs for a known stem).
- **Surfaced** provider/artifact errors (not a silent `except: return None`).
- Keep the existing no-op byte-identity test.

**The 4060 receipt (the only proof that counts — must be produced on the GPU box):**
1. clean install → `pip install torch demucs` (or the documented extra)
2. configure the workspace to select the `demucs` provider
3. separate ONE real track → a `vocals` artifact persists in the workspace store
4. render a set → a vocal layer reports `stem_source="vocals"` (not `"mix"`)
5. render again → the second render reuses the cache and does NOT run Demucs
6. paste the receipt (paths + timings + the two render reports) into the PR.

**Also in this milestone — real-library defects already observed on `main` (these
outrank a refactor because they came from the actual library, not a fixture):**
- AcoustID identify produced ~0 useful identities out of 585 attempts — investigate
  the request/parse/rate-limit path.
- Stale DB state between identity application and reorganization.
- Workspace-pointer mismatch between package (`python -m earcrate`) and CLI
  invocation.

## 2. Encode the product contract
`PRODUCT.md` (done, v0.8.25) + a versioned capability matrix with **user-visible
acceptance tests** per capability. Never label a capability "wired" when the
evidence only proves a method was called.

## 3. The editable-project slice (first real unification of listener + creator)
Make Workbench genuinely editable. A generated arrangement opens as a **versioned
project**; the user can audition, replace, trim, move, mute/solo, lock; adjust
gain/fades; choose a stem; change a transition; undo/redo; save; reopen; re-render.

Project schema (composer emits it, Workbench edits it, renderer consumes it
without inventing decisions): rails/tracks, source refs, clips, stem selection,
start/end, loop bounds, transform params, gain/pan, fades, transition data,
mute/solo, locks, automation envelopes, append-only command history.

**Acceptance (on a real library):** pick a resident → generate a 2-min set → open
in Workbench → replace/trim a vocal → lower one layer → change a transition →
undo/redo → save → restart EarCrate → reopen → produce a *different verified*
render.

## 4. Perceptual validation
Library cleanup has real evidence (~5,400 files, 336+ hours). Musical OUTPUT does
not. Keep a private local test crate + an A/B listening ledger for vocal bleed,
beat alignment, harmonic clashes, low-end masking, transition continuity,
loudness, repetition, recognizability, overall preference. Automated gates catch
regressions; human listening decides if it's getting musically better.

## 5. Consumer polish as a system property
Signed installer + updater (retire the `.cmd`); progressive scanning; durable
queue/playhead/volume/project/job state across restarts; artwork + robust metadata
review; fast global search; keyboard nav + accessibility; every background job has
progress/cancel/recover/comprehensible-failure. (The single-file build can stay a
distribution target but should stop dictating the source architecture; a real UI
state/accessibility surface will outgrow one inlined HTML blob.)

## 6. Decompose the monolith along product boundaries (§5.3, LAST)
Extract catalog/library, analysis/derived artifacts, retrieval, composition,
projects+command history, rendering, background jobs, and provider runtime — as
the editable-project work requires them, so the modules form around the real
abstractions instead of a generic table split.
