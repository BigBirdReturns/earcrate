# EarCrate — earning 0.9.997

The version label 0.9.997 is on the header today; this document is what makes it
true. Same control question as always: **can each milestone prove, on the real
machine and the real library, that EarCrate produces better audio, a smoother
listening experience, or meaningful creative control over a musical decision?**

State at time of writing (post v0.9 integration, all verified on this tree):
194/194 gates; immutable projects are the musical authority; the full 0.8.x
engine (analysis, personas, external remix, librarian, GPU queue) is intact and
renders through `render_mashup`; the CLI project surface is complete; the static
UI predates all of it and references none of `/api/projects`; nothing from
`docs/OSS_INTEGRATION_AUDIT.md` has been adopted yet; there is no autonomous
composing loop; judgment is measured DSP (`GT_SPECTRAL_PROFILE`), with the
`EmbeddingProvider`/`VectorIndex`/`CandidateRetriever` seams declared and empty.

Supersedes the remaining open items of `MILESTONES.md` (its §3 editable-project
slice shipped as v0.9). Rules carried over unchanged:

- Every milestone lands behind a seam with the current behavior as fallback.
  No gate is lowered. No milestone claims done without a receipt produced on
  the real box, committed like `docs/DESKTOP_VERIFICATION_RESULTS.md`.
- Heavy deps stay opt-in with capability probes surfaced in doctor, exactly
  like the demucs pattern. A no-GPU, no-binary box must degrade to today's
  behavior, provably (a gate per milestone pins this).
- One milestone per PR. `ENGINE_DISPLAY_VERSION` bumps every shipped batch.
  `ENGINE_VERSION` bumps ONLY when rendered bytes change (M2 is the first
  legitimate bump since `earcrate_v0900`).

---

## M0 — land it and prove it on the rig  (blocks everything)

The integrated release exists on `claude/earcrate-v0.9.0-complete-wrz7lw` and
has only ever passed its gates in cloud containers.

- Merge the branch to `main` (PR when the owner says go).
- Close PR #29 (`agent/project-score-cli-rebuild`) and PR #30
  (`agent/integrated-score-cutover`) as superseded — their content is either
  discarded-by-design (the parallel engine) or landed via this branch.
- Fix the handoff paper cuts: **[done]** `earcrate doctor` is now a real
  subcommand (exposes the existing `doctor()` report, no render, works
  pre-config, non-zero exit on failure); `Install-Dependencies.cmd` checks for
  both ffmpeg.exe and ffprobe.exe and points at `earcrate doctor` to verify.
- **The rig receipt (the only proof that counts):** on the Windows box, against
  the real library — `tests/run_gates.py` (194/194), `earcrate project
  acceptance --destination <scratch>`, one real `project compile --render`
  with an audible keep, one `project edit` → render → `undo` → byte-identity
  check. Paste paths, timings, hashes into the receipt doc.

Definition of done: the receipt is committed and `main` contains v0.9.

## M1 — perception: real beats, downbeats, sections  **[seam built, opt-in]**

Adopt **allin1** (one model: beats + downbeats + tempo + functional sections)
as the beat backend. The librosa path stays the default, selected by capability
probe like `stem_provider`.

- **[done]** `earcrate/providers/beats.py`: `beat_capability()` (honest allin1
  probe), `resolve_beat_provider()` (env `EARCRATE_BEATS` > config > librosa,
  with allin1-unavailable degrading to librosa), and `detect_beats()` — an
  allin1 adapter written to its documented API (result.beats / .downbeats /
  .segments / .bpm), mapping onto EarCrate's beats/downbeats/sections shape.
- **[done]** Wired into `compute_pcm_features`: opt-in override of the librosa
  grid, so `beat_state`, MaterialRegions and the transition planner consume the
  real grid when enabled. Default (env unset) is byte-identical — verified —
  and the file records its `beat_backend`. The `beats` work-queue kind and
  `doctor` both report the real capability.
- **[done]** Gate `test_beat_provider_seam_default_stable_and_allin1_override`
  pins: probe/selection/fallback, default analysis unchanged, and — via a stub
  matching allin1's documented API — that the adapter's output-mapping routes
  end-to-end through `compute_pcm_features`.
- **Remaining (rig — the payoff): `pip install allin1`** on the box, set
  `EARCRATE_BEATS=allin1`, `analyze --force` the real library (switching the
  beat backend is an analysis-identity change — force re-analyze, or bump
  `ANALYZER_VERSION`), then measure before/after downbeat-confidence
  distribution and whether a previously-refused transition is now executable.
  The real allin1 model is unverified-until-the-box (demucs pattern); the stub
  de-risks the adapter shape. `mir_eval` F-measure on a real annotated set is
  the rig-side quality gate.

## M2 — render fidelity: Rubber Band time-stretch  **[seam built, opt-in]**

The single biggest render-quality lever left. **[done]** `pyrubberband` behind a
`TransformProvider` seam (`earcrate/providers/transform.py`), wired into the
render hot path (`render_mashup`); the phase vocoder (librosa) is the untouched
default. Requires the `rubberband` CLI binary + pyrubberband, both probed and
surfaced in `earcrate doctor` (transform_capability).

- **[done]** Opt-in via `EARCRATE_TRANSFORM=rubberband`; a box without the
  binary (or a bad value) resolves to `phase_vocoder`, honestly, never a crash.
  The transform cache key carries the effective provider so a Rubber Band clip
  and a phase-vocoder clip never collide.
- **[done]** Default render is textually unchanged, so **no ENGINE_VERSION bump
  yet** — banked renders stay valid. Verified: HF preservation on a 1.5×
  stretch (phase vocoder drops top-octave share 0.67→0.34; Rubber Band holds
  0.63), default render still green, import-safe without pyrubberband.
  Gate `test_transform_provider_seam_default_stable_and_rubberband_higher_fidelity`.
- **Remaining (rig, the ENGINE_VERSION bump):** flip the default to Rubber Band
  and bump `ENGINE_VERSION` → `earcrate_v0910` ONLY after an ears verdict on the
  box (same arrangement rendered pre/post + the owner's ears). That is a
  deliberate, receipt-gated step — the seam is ready for it, the flip is not
  taken unverified.

## M3 — the techno persona + the Beatles proof

The external-remix path (`propose_external_remix` / `remix_anchor`) already
does "drop a foreign vocal, rebuild a bed under it in a persona's style." What
is missing is the persona.

- `profiles/remix_techno_v1.json` (and optionally a second flavor — e.g.
  four-on-floor 125–132 vs harder 135+) built from documented producer
  breakdowns, same derivation discipline as the existing 22.
- Gates: schema; persona-differentiation (its arrangements are measurably
  distinct from the nearest electronic persona, not just thresholds);
  external-remix feasibility with the new persona.
- **Definition of done is audible, not statistical:** an out-of-library vocal
  (yes, that one) over a library-built techno bed, through a project revision,
  passing the gate, kept by a human. The A/B receipt goes in the persona doc
  like the bake-off logs.

## M4 — taste: rank candidates from the owner's own judgments

The judgments table is append-only training data the owner generates by using
the product. Use it. **Proposer only — the measured judge still disposes.**

- A small learn-to-rank model (logistic/GBDT over existing L1 features — NOT a
  neural fine-tune, nothing to host) trained offline by a script that emits a
  content-addressed model artifact + training receipt (rows used, seed,
  metrics).
- Plugs into the `CandidateRetriever` seam; changes candidate ORDER within the
  bounded search, never gate outcomes, never policy bounds.
- Gates: ranker-off is byte-identical to today; ranker-on changes ordering
  only; training is reproducible from the receipt; a poisoned/empty judgments
  table degrades to ranker-off loudly.
- This is the flywheel with M6: every morning-triage keep/reject becomes
  tomorrow's ranking signal.

## M5 — the player piano  (the night shift)  **[built]**

Now safe because v0.9 made it safe: immutable revisions, verification-gated
publication, refused source mutation, exact undo.

- **[done]** `earcrate project piano --personas a,b,c --iterations N
  [--keeps K] [--seconds S] [--run-id ID]`: unattended compile → render →
  keep/discard loop entirely through immutable project revisions
  (`project_piano` in `earcrate/project/runtime.py`).
- **[done]** Bounded (max_iterations, optional max_keeps / max_seconds) and
  kill-safe: the run receipt is rewritten atomically after every iteration and
  re-running the same run_id RESUMES from where it stopped. A gate-refused set
  is DISCARDED (never a corrupt WAV); a precondition failure is recorded and
  the loop stays alive. Every attempt is a durable project revision.
- **[done]** Gate `test_project_piano_is_bounded_killsafe_and_keeps_real_renders`
  pins: bounded+complete, kept sets are real WAVs on durable projects,
  receipt persisted, resume preserves prior iterations, max_keeps early-stop.
- **Remaining (rig):** draw work through the queue's warm lane so an
  interactive request always preempts the night shift; then one real overnight
  run on the library and the morning-after triage list. The loop itself is
  done and green; only the real-library run and the warm-lane wiring are left.

## M6 — the UI rebuild, last, on the frozen contract

Deliberately last, exactly as `docs/PROJECT_API.md` intended. LATTICE (or its
replacement — it may be thinner than what exists) consumes `/api/projects`
only: active revision display, typed command dispatch, history/undo timeline,
and the piano-roll triage view for M5's overnight keeps (whose keep/reject
clicks feed M4).

- Rule: no loose-arrangement path survives in the UI. Every button is a typed
  command against a revision.
- Gates: headless Playwright drive of the full lifecycle (compile → edit →
  undo → render → export) with zero console errors, same discipline as the
  v0.8.8 fresh-download verification.

---

## Order and why

M0 is hygiene and blocks all. M1 before M2 because better downbeats improve
every persona and transition decision, while M2 only improves how chosen audio
sounds. M3 rides on M1's confidence signals but can start any time (persona
authoring is independent). M4 needs judgment volume — it can wait for M5/M6 to
generate it, but the seam work is small enough to land early with a tiny
dataset and honest metrics. M5 needs nothing but what already shipped; it is
scheduled after the perception/fidelity work so the night shift produces keeps
worth waking up to. M6 is last because a UI built against a moving backend is
how the first LATTICE became archaeology.

Completing M0–M6, each with its rig receipt, is what 0.9.997 claims to be.
What follows it is 1.0.
