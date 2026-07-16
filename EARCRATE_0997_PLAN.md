# EarCrate — earning 0.9.997

The version label 0.9.997 is on the header today; this document is what makes it
true. Same control question as always: **can each milestone prove, on the real
machine and the real library, that EarCrate produces better audio, a smoother
listening experience, or meaningful creative control over a musical decision?**

## Status ledger — what "done" means per milestone (read this first)

Every milestone below has its ENGINEERING on this branch (201/201 hermetic gates
in the cloud CI env), but engineering-on-branch is NOT the roadmap's definition
of done. Done requires the on-box receipt against the real library. Honest state:

| Milestone | Code on branch | Verified in cloud CI | Rig receipt (the actual bar) |
|---|---|---|---|
| M0 doctor + hygiene | ✅ | ✅ gate | ❌ **the M0 receipt (full gate suite + acceptance + audible compile/keep/undo-identity) has NOT run on the box, and v0.9 has NOT reached `main`** |
| M1 allin1 beats | seam + adapter + stub gate | ✅ default-unchanged | ❌ real allin1 not installed/run; no downbeat-confidence or transition measurement |
| M2 Rubber Band | seam, opt-in | ✅ HF spectral A/B | ❌ default NOT flipped, `ENGINE_VERSION` NOT bumped, no ears verdict |
| M3 techno persona | ✅ persona + gate | ✅ distinct+valid | ❌ audible external-vocal-over-techno proof NOT produced |
| M4 taste ranker | ✅ seam + train + gate | ✅ deterministic | ❌ no real judgments trained/enabled/compared |
| M5 player piano | ✅ loop + gate | ✅ bounded/kill-safe | ❌ warm-lane queue integration + one real overnight run outstanding |
| M6 Workbench | ✅ functional | ✅ package DOM, hermetic gates | ⚠️ single-file DOM drive + rig lifecycle outstanding; design pass deferred |

The whole program is **"engineered, not yet validated."** The 201/201 figure is a
cloud-CI number, not an independent rig receipt. Nothing here is "done" in the
roadmap's sense until item-by-item on the box (see "The rig receipt" at the end).

Prior state (pre-M0, for lineage): 194/194 gates; immutable projects the musical
authority; the full 0.8.x engine intact; CLI project surface complete; the static
UI referenced no `/api/projects`; nothing from `docs/OSS_INTEGRATION_AUDIT.md`
adopted; no autonomous composing loop; judgment was measured DSP only.

Process note: the roadmap said "one milestone per PR"; in practice M0–M6 were
built on this single branch. That was expedient for a stop-when-blocked cloud
session, but it means the on-box validation is one consolidated receipt, not
seven, and the branch is not `main`-merged per-milestone.

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

## M3 — the techno persona + the Beatles proof  **[persona built, audible proof outstanding]**

The external-remix path (`propose_external_remix` / `remix_anchor`) already
does "drop a foreign vocal, rebuild a bed under it in a persona's style."

- **[done]** `profiles/remix_techno_v1.json` — hypnotic four-on-floor 128–134,
  long held loops, kick-owned low end, darker+steadier top; same derivation
  discipline as the existing personas. Loads, projects, and compiles into a
  policy like the others.
- **[done]** Gate `test_techno_persona_is_hypnotic_and_distinct` pins schema/
  identity, that it changes the arrangement from the same pool (holds a
  foreground source strictly longer than girl_talk), and that it passes its own
  taste-coverage gate — distinct AND valid.
- **Remaining — the definition of done is AUDIBLE, not statistical:** an
  out-of-library vocal (yes, that one) over a library-built techno bed, through
  a project revision, passing the gate, kept by a human. That A/B proof needs
  the real library + ears on the box; it has NOT happened. A second flavor
  (harder 135+) is optional and unbuilt.

## M4 — taste: rank candidates from the owner's own judgments  **[built, opt-in]**

The judgments table is append-only training data the owner generates by using
the product. Use it. **Proposer only — the measured judge still disposes.**

- **[done]** `earcrate/ear/taste_ranker.py`: a dependency-free L2-regularized
  logistic regression over the existing atom features, trained by deterministic
  zero-init gradient descent (no sklearn, no RNG — same judgments always yield
  the same model). Content-addressed JSON artifact + receipt. `train_taste_ranker`
  reads `atom_judgments`; CLI `earcrate train-ranker --profile X`. Refuses a
  one-class / too-small set instead of emitting a degenerate model.
- **[done]** Plugs in at `approved_atom_pool` (right after the
  `CandidateRetriever` seam): `rank_pool` is a stable, membership-preserving
  permutation of the FULL pool — changes candidate ORDER only, never membership,
  gate outcomes, or policy bounds. OFF by default (`EARCRATE_RANKER=on` to
  enable); a feature-drifted or missing artifact is ignored, not mis-scored.
- **[done]** Gate `test_taste_ranker_trains_reproducibly_and_reorders_opt_in`
  pins: reproducible training, one-class refusal, membership-preserving reorder,
  artifact round-trip + drift rejection, and end-to-end that `approved_atom_pool`
  is identity-when-off and a same-membership reorder when opted in.
- **Remaining (rig — the payoff): the model is only as good as real judgments.**
  Judge atoms in the Library loop (and via the M5 morning triage), then
  `earcrate train-ranker`, set `EARCRATE_RANKER=on`, and compare selections.
  This is the flywheel with M5/M6: every keep/reject becomes tomorrow's signal.

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

## M6 — the project Workbench, on the frozen contract  **[functional pass done]**

Rebuilt on `/api/projects` exclusively, inside the preserved LATTICE shell (no
redesign — that's the on-box aesthetic pass). See `docs/M6_WORKBENCH.md`.

- **[done]** Project list + compile/import, active header (revision/persona/
  seed/BPM/duration/gate/head-currency), three-rail timeline showing every clip,
  clip inspector with backend-policy ranges + typed commands, transition
  inspector, undo/redo/recompile with full refresh, revision-bound preview/
  render/export with receipts, history (ancestry) + runs views, and the M5
  morning-triage view whose keep/reject feeds M4 through the atom-judgment path.
- **[done]** No loose-arrangement path survives in the UI; project errors map to
  4xx; no framework/network/second-state-model; single-file build byte-identical.
- **[done]** `tests/manual/verify_workbench_dom.py` drives the full lifecycle
  (compile/import → edit → undo → redo → preview → render → export → reopen after
  restart) with ZERO console errors, package-mode green; single-file serves the
  identical Workbench. Hermetic gates extend the HTTP contract + piano triage
  (201/201). Screenshots at desktop + narrow widths captured.
- **Remaining (on-box, deliberate): the aesthetic pass** — timeline zoom/scroll +
  waveforms, rail colour semantics, inspector docking, `replace_clip` atom-picker
  UX, refusal presentation, narrow-width header priority (the deferred list in
  `docs/M6_WORKBENCH.md`). Left for the owner's eye on real compiled output.

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

---

## The rig receipt — the single consolidated validation that closes the program

Feature work on this branch is DONE. The next work product is ONE Windows-rig
receipt, run against the real library, committed to the branch. Only after that
exact head is green does v0.9 go to `main`. The receipt must:

1. Run the full repository gate suite (`python tests/run_gates.py`).
2. Run `tests/manual/verify_workbench_dom.py` against BOTH package mode and
   `dist/earcrate.py`, with zero console errors (settles the single-file DOM
   drive the cloud env could not complete).
3. Run `earcrate project acceptance --destination <empty scratch>`.
4. Compile AND render a real-library project (not a synthetic fixture).
5. Perform a real edit → undo → redo → restart → preview → render → export
   through the browser.
6. Prove undo restoration by the prior render's file hash (byte identity).
7. `pip install allin1`, set `EARCRATE_BEATS=allin1`, re-analyze the real
   library with `--force`, and record the measured downbeat-confidence / section
   effect and any transition change (or honestly: no change).
8. Render the same project with the default transform and with
   `EARCRATE_TRANSFORM=rubberband` for the owner's listening verdict. Only after
   that verdict flip the default and bump `ENGINE_VERSION` → `earcrate_v0910`.
9. Produce the techno external-remix proof (a foreign vocal over a library-built
   techno bed, kept by a human) — M3's audible definition of done.
10. Run one real ranker experiment (`train-ranker` on real judgments →
    `EARCRATE_RANKER=on` → compare selections) and one bounded `project piano`
    session on the real library.
11. Update THIS file's status ledger so the markers match the receipts.
12. Open the PR to `main` only after that exact head is green.

Items 1–6 and 10 are mechanical and scriptable on the box; 7–9 need the GPU /
real audio / ears. After the receipt lands, the front-end designer takes the
branch for the deferred M6 aesthetic layer (zoom + waveforms, rail semantics,
inspector docking, replacement-atom audition, refusal UX, responsive hierarchy,
transport integration) — backend wiring is complete; their remit is design.
