# EarCrate — what it is

> EarCrate is a local-first music system that turns a personal library into both a
> polished listening environment and an editable generative studio. Automatic
> arrangements are first-class projects, and every machine decision can be
> inspected, overridden, saved, and reproduced.

One private catalog supports two coupled experiences:

- **Listener** — polished library management, discovery, queueing, playback, radio,
  and continuity.
- **Creator** — open any generated set as a non-destructive **project** and change
  the musical decisions: audition, replace, trim, move, mute/solo, lock, re-stem,
  re-transition, undo/redo, save, reopen, re-render.

The catalog, analyzer, stem service, retriever, composer, renderer, and Workbench
are the actors connecting those two experiences. Deterministic receipts,
reversible operations, and human-over-machine precedence are means, not the end.
**Future work is judged by whether it advances that complete loop** — not by gate
count or by whether a method got called.

This file is the architecture-of-record for *intent*. The engine (`AGENTS.md`,
`EARCRATE_REBUILD_PLAN_v3.md`) is the architecture-of-record for *safety*. Where a
feature claim and this document disagree, this document names the target.

---

## Honest capability matrix

Statuses are **evidentiary**, not aspirational. A capability is only "functional"
if a user-visible path produces the result end to end. "Call site present" means
the wiring exists but the runtime is unproven — it is NOT "done".

| Capability | Status | Evidence / gap |
|---|---|---|
| Scan / analyze / extract / ear-crate pipeline | functional (gated) | drives real audio; graceful on corrupt/silence/empty |
| Deterministic segment identity + judgment survival | functional (gated) | `test_force_rebuild_preserves_judgments` |
| L0 sound identity (`pcm_sha`) deposited by scan | functional (gated) | `test_pcm_identity_feeds_stems` |
| Loop-megamix render (varispeed, multi-deck, quality gates) | functional | real WAV out; **not verified by ear on a real library** |
| Per-loop review + quota (human beats machine) | functional (gated) | `test_quota_preserves_human_loop_approval` |
| Reversible mutations (signature-gated) | functional (gated) | `test_destructive_mutations_require_signature` |
| Retrieval seam (`CandidateRetriever`, full-scan default) | functional (behavior-preserving) | call routes through the seam; not yet scalable (no query/limit) |
| **Stem separation (vocal-on-instrumental)** | **infrastructure only — OFF/unverified** | call site in render; `_run_demucs` is a stub; no provider selection; provider/renderer use different stores; no GPU receipt |
| LATTICE UI (7 modes, transport, skins) | functional | drives live data, 0 console errors headless |
| Immutable project + command model (CLI) | **functional (gated)** | automatic sets, external remixes and compatibility imports create revision-backed projects; typed edits, locks, undo/redo, preview, exports and explicit mastering are exercised end to end |
| Editable Workbench UI | **partial** | existing rail view and compatibility import remain; full clip inspector/drag editing is intentionally outside this CLI cutover |
| Listener polish (durable queue/playhead, fast search, gapless, install/updater) | **not started** | prototype `.cmd` installer; browser `<audio>` playback |
| Real-time preview/transport engine | **not started** | exact server-rendered project preview is functional; low-latency transport remains absent |
| Perceptual validation (A/B listening ledger) | **not started** | no musical-quality evidence yet |
| Autonomous engine ↔ edits reciprocity | **partial** | locks/vetoes/favorites exist; listener/creator behavior not yet fed back |
| §5.3 monolith table teardown | not started (intentionally last) | invariants gated; extraction should follow the project model, not precede it |

---

## Rules that hold across the whole product
1. **No claim without a receipt.** "Functional" requires a user-visible end-to-end
   result, not a green gate over a fake.
2. **Human decision beats machine convenience** (locks, vetoes, favorites,
   signatures), and that precedence is gated.
3. **Every generated set is a reproducible, editable project**, never a dead file.
4. **Local-first, private.** Source audio never leaves the machine; network stays
   opt-in behind a seam.
