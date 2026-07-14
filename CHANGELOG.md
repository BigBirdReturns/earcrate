# EarCrate — CHANGELOG

## v0.8.30 — the performance chapter + the GPU multi-tool seam + the treble fix
- Rebuilds the perf campaign from the hot-path audit's verified findings (the
  original implementation died unpushed with its session container):
  - analyze runs ONE full-track decode, hashing the decoder's own byte stream
    (digest proven byte-identical to the old separate ffmpeg hash pass, so every
    banked pcm_sha and L3 stem key stays valid — zero cache invalidation) while
    keeping only the bounded feature window in RAM.
  - local_harmony builds one whole-track chromagram and slices windows instead
    of ~59 per-window chroma_cqt builds (measured 36% of per-track analyze
    cost); onset_strength is computed once and shared instead of 3x; the crate
    pass reads harmonic/percussive ratios off the spectrogram it already has
    instead of a second STFT + two iSTFTs per clip.
  - plan_varispeed_transform is memoized (pure; ~4.4M repeated calls per
    propose, previously computed twice); ArtifactStore.has() answers existence
    without reading ~48MB blobs; renders decode each stem ONCE per
    (pcm_sha, role) instead of per-layer-per-section; /api/status polls read
    through a second read-only WAL connection so UI input never queues behind a
    background writer.
- Fixes the treble-dead render chain (the box-named sole blocker for audible
  external-remix output): the finishing EQ is now MEASURED and target-directed
  (reads low200/high3000 with the gate's own spectral ruler, solves the shelf
  gains to reach the real-GT targets, iterates, bounded to +/-14 dB) and runs on
  EVERY gated render — the external_remix mode previously received no tonal
  correction at all. Dynamics are untouched by construction.
- GPU work queue seam (earcrate/providers/workqueue.py): declared job kinds
  with honest capability probes (stems live; beats/embed/transcribe declared,
  reporting exactly what an install would unlock), interactive lane over warm
  lane, batch-by-kind draining (one model resident — the 8GB VRAM policy),
  content-addressed dedup and done-once skip via each kind's has-probe. The
  demucs stem-warmer is tenant #1: it now draws its work through the queue and
  its existing gates pass unmodified — that is the proof of the seam. doctor()
  surfaces the per-kind report.
- remix_anchor records fold provenance (bpm_fold_tested / bpm_fold_choice /
  bed_median_bpm) so the box can tell a deliberate fold-to-crate outcome from a
  fold that never ran (v0.8.29 re-verification request).
- Gates: 181/181 (new workqueue suite + presence-restore gate + streaming-digest
  equality pinned inside the pcm-identity gate).

## v0.8.29 — PR-27 review fixes: anti-aliased resampler, external-vocal integrity, dist import guard
- FIX **render brightness at the source**: `resample_or_fit` was `np.interp` —
  linear interpolation, a first-order-hold low-pass sitting in the render hot
  path (every varispeed clip), shaving the top octave off EVERY layer before
  mixing. Measured: ~6% relative high-band loss per pass at a typical 6%
  varispeed — a CONTRIBUTING (not sole) cause of the "presence-dark" renders;
  it compounded across every layer and no downstream EQ can restore aliased
  content. Now polyphase FIR (`scipy.signal.resample_poly`), deterministic,
  exact-length. The dominant presence fix remains the finishing chain (see
  PR-27 review: pedalboard EQ/bus + matchering reference-master).
- FIX **external vocal tail loop-tiling (blocker)**: a dropped take's final
  window (shorter than its section) was loop-tiled to fill the slot, replaying
  the last words as a stutter-echo on essentially every external remix. New
  pure `fit_external_clip`: trim when long, keep short when short — the bed
  carries the rest of the section instrumental, per the stamped contract.
- FIX **14 ms fade dips inside the held vocal**: `fade_out` was unconditional,
  so the continuous take dipped to zero at every 4-bar section seam. New pure
  `external_edge_fades`: fade in only at the take's first window (or mid-section
  entry), fade out only in the window that truly reaches the vocal's end.
- FIX **dist-only ModuleNotFoundError (3 endpoints)**: function-level
  `from earcrate.` imports survive the column-0 strip and crash the standalone
  single file at call time (`/api/materials/regions`, `reference_recall`,
  `plan_reference_extraction`). Imports hoisted to module top; the build now
  REFUSES to build any indented earcrate import so the class is extinct.
- FIX **album flow counted rejected renders as made tracks**: `render_album`
  discarded the manifest-execution result, so a gate-rejected render appeared
  on the tracklist as a phantom WAV. It now lands in `skipped[]` with the gate
  reason (rides the v0.8.28 F3 non-ok contract).
- External remix integrity + honesty: the render now re-verifies the dropped
  file's PCM identity against the propose-time receipt (a swapped file drops
  with a clear reason instead of shipping under the old identity); feasibility
  demands bed-only turnover (needed//3, floor 2) instead of re-imposing the
  full-set source contract the composer explicitly waives for external mode.
- Gate-suite sandbox is now UNCONDITIONAL (was setdefault): a user who exports
  EARCRATE_HOME at their real workspace still gets the test sandbox.
- MusicBrainz batch: transient fetch errors are no longer cached forever —
  resumes retry them.

## v0.8.28 — turnover freshness pass + gate-suite pointer sandbox
- FIX **stagnant crate through the one-click path**: the fail-fast harvest
  gates ENTRY (stop analyzing the moment readiness is satisfied) but nothing
  gated GROWTH — tracks added after the crate first became ready were scanned
  yet never analyzed by one-click, so every set recycled the same sources
  while readiness itself reported `bottleneck: sources`. One-click now runs a
  bounded freshness pass after readiness: at most ONE `harvest_batch` of
  never-analyzed tracks per run (analyze → extract → re-crate → recompute
  readiness), accounted in the harvest log and runtime ledger, opt-out via
  `skip_freshness`. Zero work on a stable library; loops enter as candidates
  (quota/human review still owns approval); no gate or threshold changed.
  Observed live over the API: tracks stuck at `bpm=None, loops=0` under the
  old path were analyzed and looped by the one-click run itself
  (`freshness_*` stages in the perf ledger). Gates:
  `test_freshness_harvest_folds_new_tracks_into_ready_crate`,
  `test_freshness_harvest_costs_nothing_on_stable_library`,
  `test_one_click_runs_freshness_pass_between_readiness_and_graph`.
- FIX **the shipped test suite hijacked the real workspace pointer**: gates
  constructing `EarcrateCore()` wrote the app-global pointer at the repo root,
  so `python tests/run_gates.py` silently re-pointed a configured workspace at
  a deleted temp fixture (observed live: a fresh server booted into
  `/tmp/tmp…/` instead of the user library). The runner and `test_gates.py`
  now sandbox `EARCRATE_HOME` for the whole suite; gate
  `test_gate_suite_sandboxes_the_workspace_pointer` pins the contract.

## v0.8.27 — release consolidation: truthful gates, fail-hard renders, durable receipts
- Consolidates the v0.8.16–v0.8.25 architecture and seam work with the parallel
  v0.8.26 real-library fixes under one package-visible release version.
- A dedicated gate runner discovers and executes the complete acceptance suite;
  CI and package verification can no longer report green after a mid-file runner
  silently skips later gates or the TasteSpec suite.
- Rendering is fail-hard for every selected layer: feasibility or resolution
  failures reject the arrangement before a WAV is written, rather than silently
  discarding material and presenting an incomplete mix. UI/API runs leave one
  canonical, durable receipt for the outcome.
- Restores native worker environment limits while preserving analysis
  multiprocessing, addressing the known BLAS/ProcessPool exit-139 failure without
  introducing a serial or degraded fallback.
- Full-track stem identities now hash the complete canonical PCM instead of the
  bounded feature-analysis prefix; same-prefix/different-tail tracks cannot share
  the wrong cached stem. Legacy prefix-identity caches are rebuilt automatically.
  Same-path replacements advance a source generation only after the complete PCM
  is proven different, invalidate content-bound caches, and preserve old judged
  loops as inactive history rather than applying their feasibility or judgments
  to different audio. Metadata-only retags keep the existing generation.
- Successful scans retain missing files as inactive ledger history, and stat
  changes invalidate source trust before metadata probing. Analyze, harvest,
  curation, crate-building, and rendering all exclude inactive/stale identities;
  render preflight also verifies the exact analyzed file bytes before any WAV can
  be written.
- Canonical decode and full-track hashing now both select audio stream `0:a:0`,
  preventing multi-stream containers from binding features and renders to a
  different stream than the cached source identity.
- The opt-in Demucs provider requests the released `htdemucs` model identifier,
  and workspace-pointer discovery skips malformed/stale candidates until it finds
  a valid configuration instead of stopping at the first existing file.
- The Windows first-run path now goes through one guarded launcher and checks for
  both FFmpeg and ffprobe before building or starting the UI.
- Still not claimed: the opt-in Demucs/CUDA path remains off by default and needs
  a real GPU receipt; synthetic gates do not substitute for listening to renders
  from the live library.

## v0.8.26 — milestone 1: stem-path CPU completion (still OFF/UNVERIFIED) + 3 real-library defects
- The stem path's CPU half is now correct end to end, so a 4060 receipt becomes
  PRODUCIBLE — but the feature stays OFF by default and the real Demucs run is
  UNVERIFIED. No "works on a GPU" claim until the receipt exists.
  - ONE workspace-scoped shared L3 `ArtifactStore` (`EARCRATE_L3_ROOT`, set by
    configure to `<agent_root>/cache/L3`): the provider materializes and the
    renderer resolves through the SAME store — the two-temp-dirs bug is gone.
  - Provider SELECTION: config `stem_provider` (default `noop`) + env
    `EARCRATE_STEMS`; render selects it. Default stays no-op.
  - Honest capability PROBE `stem_capability()` → {torch,demucs,cuda,ready},
    surfaced in `doctor()` — reports not-ready here.
  - CACHE-before-separate: a known stem is reused without re-running the producer.
  - SURFACED fallback: render records `stem_reason` instead of a silent swallow.
  - Real (guarded) `_run_demucs` implemented — but UNEXECUTED here.
  - `requirements.txt` documents the OPTIONAL torch/demucs install; torch is NOT a
    shipped dependency.
  - Gate `test_stem_path_producible` proves select→materialize→resolve→cache with a
    FAKE demucs writing to the shared store (byte-identical under the no-op default).
    Its own docstring states it does NOT run Demucs. The detector keeps `stems`
    honestly deferred: "call path complete + gated with a fake; real Demucs run
    UNVERIFIED pending a 4060 receipt."
- Real-library defects (from `main`; fixed at the code level, gated on synthetic
  fixtures — the live 585-file run is still the true test):
  - **AcoustID identify**: the request `meta` was malformed (a literal `+` in
    `recordings+releasegroups+compress` collapsed under URL-encoding), so lookups
    came back thin — plausibly the 0/585. Fixed the encoding; hardened the parse
    gate with realistic multi-result shapes.
  - **identify → reorganize stale DB**: `apply_identities` now resolves the library
    `file_id` by path and writes the corrected identity where `reorganize` reads
    it, so a re-tag flows into the destination. Gate
    `test_identify_then_reorganize_uses_new_identity`.
  - **workspace-pointer package-vs-CLI mismatch**: `visible_app_dir()` anchored to
    `__main__.__file__` (different under `-m` vs the console script) and fell back
    to cwd — so the two entry points wrote different pointers. Now anchored to the
    `earcrate` package location, deterministic across entry points (and the
    single-file build). Gate `test_workspace_pointer_stable_across_entrypoints`.
- The AcoustID gate was moved BEFORE the `__main__` runner so it actually executes
  in CI (it previously sat in the never-run tail).
- 36/36 gates green; single-file builds + self-test.
- Honest remainders (from each agent's `honest_unverified`): the real Demucs/GPU
  separation (needs a 4060 receipt), the live keyed AcoustID run on the real
  library, and Windows-specific pointer paths — none exercised here.

## v0.8.25 — correction: the stem "feature" is infrastructure, not a working feature
- HONEST CORRECTION of v0.8.24's overclaim (caught in external review of PR #25).
  v0.8.24 said stems "reach render" and implied a 4060 with Demucs would produce
  vocal-on-instrumental. That is NOT true as committed. Four real blockers:
  1. `DemucsStemProvider._run_demucs()` is a stub that always raises — the real
     separation is unimplemented.
  2. Nothing selects demucs: `get("stems")` returns the registered DEFAULT, which
     is `NoopStemProvider`. A GPU box still gets the no-op → falls back to mix.
  3. The provider builds its own `ArtifactStore()` (temp dir) and the renderer
     resolves via a *different* `get("artifacts")` store — a produced key would
     never resolve.
  4. No torch/demucs in requirements, no capability probe, no install path; render
     silently swallows stem errors.
  So v0.8.24 proved a CALL GRAPH with a fake provider, not a feature. Relabeled:
  the stem work is INFRASTRUCTURE that prepares stem integration; the feature is
  OFF and UNVERIFIED pending a real GPU receipt.
- The unfed-handoff detector's DEFERRED_SEAMS now says so: `stems`/`artifacts`
  carry honest "call site present, runtime unimplemented — feature OFF" reasons
  instead of being silently promoted to "wired". ("Wired" = a call site exists,
  NOT proven functional.)
- What v0.8.24 DID legitimately land (unchanged, still true): retrieval routes
  through `CandidateRetriever` (behavior-preserving), per-loop review is real, and
  the no-op fallback is genuinely byte-identical (gated).
- NEW `PRODUCT.md` (the product definition + an honest capability matrix with
  evidentiary statuses) and `MILESTONES.md` (the order of work: make the vertical
  path truthful → product contract → editable Workbench → perceptual validation →
  consumer polish → §5.3 teardown LAST). §5.3 is explicitly NOT next.
- Gates unchanged (32); single-file builds. No engine behavior changed this bump.

## v0.8.24 — wire the seams: stems reach render, retrieval + per-loop review go live   ⚠ stem claim CORRECTED in v0.8.25 (infrastructure, not a working feature)
- The CPU-doable feature wiring that was mis-filed as "needs a GPU." Only Demucs
  RUNNING needs a GPU; the plumbing that CALLS it is CPU code, and it's now done —
  behavior-identical on this box (the no-op/default providers), real on a GPU box.
- §5.2 STEMS IN RENDER: `render_mashup` now CONSULTS the StemProvider seam for a
  vocal layer — looks up the file's `pcm_sha` (files.audio_sha256) and calls
  `get("stems").separate(pcm_sha, path, ["vocals"])` before touching the full mix.
  If a real vocals stem comes back (a GPU box with Demucs), it's decoded and sliced
  at the same loop window and layered; otherwise (the no-op default here, or no
  pcm_sha) it FALLS BACK to the byte-identical full-mix decode. Each layer's report
  records `stem_source: "vocals" | "mix"`, and the transform cache key includes it
  so a mix render can never mask a later stem render. Gate
  `test_render_consults_stem_seam` (red-first): a fake provider proves render
  consults the seam with the right pcm_sha for vocals ONLY, the returned stem
  changes the audio, and the no-op fallback is byte-identical.
- §5.4 RETRIEVAL: the composer's candidate pool now routes through the
  `CandidateRetriever` seam (default `FullScanRetriever` reproduces today's output
  exactly). Gate `test_composer_uses_retriever_seam`.
- UI: the Library "Loop review" card gains real PER-LOOP human review — a candidate
  list with Approve (locks the loop so quota can't demote it) / Reject / Lock, plus
  "Reject all candidates" (bulk approve stays disabled by design). Gate
  `test_loop_status_endpoints_contract`.
- The unfed-handoff detector now shows `stems`, `artifacts`, `retriever` as WIRED
  (removed from DEFERRED); only `embedding` and `vector_index` remain deferred (no
  data to consume yet — ANN is §5.4-later). So the detector reflects the real wiring.
- 32/32 gates green; single-file builds + self-test; UI drives headless with zero
  console errors.
- Still GPU-gated (honestly): the actual Demucs separation run. Still not done:
  the §5.3 monolith table teardown (deliberately not fanned out — destructive
  refactor, invariants already gated, wants careful driven work).

## v0.8.23 — the unfed-handoff detector: silent orphans can't ship anymore
- Generalized the audio_sha256 bug (v0.8.22) into a DETECTOR for its whole class.
  The "unfed handoff": a producer→consumer contract where the consumer is declared
  but the producer never runs — a schema identity/link COLUMN nobody writes, or a
  registered provider SEAM nobody calls. It's silent (null / no-op, never an
  error), so "built the column/seam" reads as "wired the feature" when it isn't.
  Distinct from Lesson #4 (calls a missing endpoint — errors loudly) and #2/#12
  (two sources of truth — duplication drift); this is a source with ZERO producers.
- NEW gate `test_no_unfed_handoffs`: introspects the real (post-migration) schema
  and the seam registry, and requires every identity/link column to be WRITTEN and
  every registered seam to have a LIVE CALLER — or to sit in a DEFERRED map with a
  written reason. A new orphan with no receipt fails the gate. Self-checks that the
  fed-heuristic isn't a tautology; red-proven on both arms (drop a seam from
  DEFERRED, or add an unfed identity column).
- What it found (all now accounted for): every identity/link column is fed
  (audio_sha256 was the last, fixed in v0.8.22); the five registered seams
  (stems, artifacts, retriever, embedding, vector_index) have no live caller and
  are now DEFERRED with explicit reasons — so "Stems/ANN are deferred on purpose"
  is written in code, not a silent gap. This gate would have caught audio_sha256
  the day the column was added.
- 29/29 gates green; single-file builds + self-test.

## v0.8.22 — one stomach: the cheap scan now deposits the identity GPU stems eat
- Closed a silent LAYER-HANDOFF gap: `files.audio_sha256` (the L0 pcm_sha, the
  decoded-canonical SOUND identity) was declared in the schema and consumed by the
  StemProvider seam (its L3 artifact key = `SHA256(pcm_sha ‖ "demucs" ‖ model ‖
  role)`) — but NOTHING wrote it. Verified: after scan+analyze, `audio_sha256` was
  `None`. So the cheap laptop pass and the expensive GPU pass were two stomachs,
  not one.
- Fix: the analyze pass now computes `pcm_sha256(y)` from the canonical PCM it
  already decodes (zero extra decode) and deposits it on `files.audio_sha256`,
  across all paths — parallel worker, serial, and cache-hit (the `.npz` now carries
  `pcm_sha`; older caches repopulate on re-analyze). NEW `pcm_sha256` helper in
  core/util. So the same sound in two files gets the SAME id (separate once, dedup
  duplicates), and L1 now hands off to L3 by construction.
- Gate `test_pcm_identity_feeds_stems` (red-first: on the old code `audio_sha256`
  is null and the gate fails "cheap scan feeds the GPU nothing"): analyze deposits
  a 64-hex id, duplicate sounds share it, and the StemProvider carries/keys on it.
- 28/28 gates green; single-file builds + self-test.
- Note: this feeds the KEY the stem pass needs; the stem pass itself still isn't
  CALLED from render (§5.2 wiring) and Demucs still runs only on a GPU box.

## v0.8.21 — code the dicta: four conflicts resolved, each pinned by a gate
- Audited the session's surface for things that step on each other's toes, decided
  who wins, and wrote the ruling into an executable gate. 27/27 gates green.
- **Two real footguns closed (missing-signature bypass).** `reorganize_source`
  (moves originals) and `apply_identities` (rewrites tags) used the weak guard
  `if approved and approved != sig` — an EMPTY signature short-circuits false, so
  they executed unsigned. Reproduced: `reorganize_source({apply:true})` moved 3
  real files with no approval. Now both refuse a missing OR stale signature (same
  strong guard as `apply_workspace_migration`). RULING: any op that moves/deletes/
  rewrites files on disk must have a valid plan signature to apply; copy-only
  `ingest`/`organize` keep the flag-only path (source is never touched). Gate
  `test_destructive_mutations_require_signature`.
- **Human beats machine on loop curation.** `auto_approve_quota` reset EVERY
  approved loop to candidate before re-approving its own picks — wiping a human's
  manual loop approvals. NEW `loops.locked` column (additive migration);
  `set_loop_status` locks a human call; quota now demotes only machine-approved
  (`locked=0`) loops and preserves locked ones. Gate
  `test_quota_preserves_human_loop_approval`.
- **Steering precedence made law.** pair-veto / atom-lock-out > favorite > rank >
  station bias: the composer now drops human-rejected/locked-out atoms from the
  pool before any rail is built, so no favorite or station nudge can resurrect a
  vetoed choice. Gate `test_steering_precedence_order` (extends
  `test_curation_steers_composer`).
- **One source of truth for the math.** Collapsed five inlined `11.5`
  source-seconds fallbacks to `plan.math.DEFAULT_SOURCE_SECONDS`; the composition
  formulas live only in `plan/math.py`. Gate `test_no_shadow_sources_of_truth`
  (grep-style, red if a formula is re-inlined in app.py).
- Each ruling proven RED-first (the gate fails when the wrong thing wins) and
  re-verified in the main tree; resolved via a 4-way parallel fan-out.

## v0.8.20 — restore the buffalo the LATTICE reskin dropped
- The v0.8.17 reskin was a prettier but THINNER front door — it silently dropped
  working handles the old console had (the engine kept them; the new UI just
  stopped exposing them). Restored, wired to the existing endpoints:
  - **Reorganize ROLLBACK** — the reskin kept Preview+Apply but dropped the undo,
    leaving a destructive in-place move with no UI reversal. Now a real
    Preview → Apply (signature-gated) → Rollback (dry-run confirm then undo) flow.
  - **Loop review // quota approval** — candidate/approved/rejected counts +
    bounded, role-balanced quota promotion (`/api/loops`, `auto_approve_quota`).
  - **Playlist builder** — query → m3u8 (`/api/playlist/propose`).
  - **Plan save / load** in Workbench — save a composed plan, reload it later
    (`/api/timeline/save|load|list`); plus **Preflight**.
  - **Judge render** + **Open folder** on Sessions rows, and a **Manifests**
    panel (reversible operation journals: preview / execute).
  - **Scout drives** + **Open workspace** in Setup (`/api/workspace_candidates`,
    `/api/open_folder`).
- Verified: all seven screens drive live data headless with ZERO console errors;
  single-file builds + self-test; 23/23 gates green.
- Still not surfaced (honest): per-loop status toggles and bulk loop status
  (quota approval is the sanctioned promotion path) and `mashup/propose`
  (redundant with one_click) — say so rather than pretend. And the reskin's
  original omission is the lesson: a rebuild that drops working surface is
  shooting the buffalo and leaving the meat.

## v0.8.19 — v3 phases 2–5: provider seams, plan/ purification, layer + render gates
- The v3 rebuild's architecture-of-record lands as SEAMS and INVARIANTS (not a
  monolith rewrite). 23/23 gates green on the built single-file.
- §5.2/§5.3/§5.4 — NEW `earcrate/providers/` package: a pure registry
  (`register`/`get`, default-per-kind) plus four capability seams, each with a
  correct registered DEFAULT so core reaches capability THROUGH the seam:
  - `StemProvider` — `NoopStemProvider` default (reports unavailable, never
    crashes, no heavy deps). `DemucsStemProvider` guards its torch/demucs import
    and materializes to L3 with provenance `(pcm_sha, "demucs", model_version)` +
    a retention tier. **Honest limit: the actual GPU separation is not run in CI
    (no torch/CUDA here) — the seam, no-op default, guarded-import safety, and L3
    provenance shape are verified; the real Demucs pass executes on the user's
    RTX 4060, and is not claimed to run.**
  - `CandidateRetriever` (`FullScanRetriever` default), `EmbeddingProvider`
    (`NoopEmbeddingProvider` = returns None, never fabricates vectors),
    `VectorIndex` (`LinearScanIndex` = brute-force true nearest). Linear-scan and
    full-scan run and are gated.
  - `ArtifactStore` (L3) — local tiered dir, `put/get/evict` with retention tiers
    ephemeral|warm|pinned; eviction sheds ephemeral before warm and NEVER pinned;
    provenance `(source_identity, provider, version)`.
  - Gate `test_provider_seams` pins all of the above (red-first on evict-pinned +
    non-noop-default).
- §5.3 / Lesson #1 — NEW `earcrate/plan/` package: composition MATH extracted to
  PURE functions (`sources_needed`, `readiness_need`, `target_bars`), and app.py
  now DELEGATES to them in 7 spots so there is ONE source of the arithmetic.
  Behavior byte-identical (all prior gates unchanged). Gate
  `test_plan_math_pins_composition_arithmetic` (red-first: a wrong constant fails).
- §5.5 + §5.3 layer invariants — NEW gates: `test_saved_plan_renders_identically`
  (a saved plan re-derives with an identical `arrangement_sha` — reproduce or it's
  an invariant failure), `test_measurements_persona_free` (L1 measured once per
  (file, analyzer_version), shared across personas), and
  `test_judgments_append_only_deterministic` (L2 keyed by content identity, upsert
  not duplicate).
- New modules added to the single-file builder ORDER (before app.py) and verified
  present in `dist/earcrate.py`; built-artifact self-test + `test_singlefile_cli_smoke`
  both green. Built via a 3-way parallel fan-out, integrated and re-verified in the
  main tree.
- Still open (honestly): core is not yet WIRED to call `StemProvider` inside the
  render path (the seam exists + is gated; vocal-on-instrumental layering is the
  next feature step), the on-GPU Demucs run (above), and the destructive teardown
  of the monolith's shared table space (the L1/L2 invariants are now GATED, i.e.
  pinned, not yet re-shaped).

## v0.8.18 — close the §5.1 debt: rows 13–16 gated (and two live footguns fixed)
- The entire v3 §5.1 debt front is now closed. With v0.8.16's Lesson #7 gate,
  ledger rows 7, 13, 14, 15, 16 all have executable gates that go RED the instant
  the failure class returns. 18/18 gates green on the BUILT single-file.
- ROW 15 CAUGHT TWO LIVE FOOTGUNS (fixed, red-first): (1) `apply_workspace_migration`
  executed the migration when called with NO signature — the guard was
  `if approved and approved != sig`, and an empty signature is falsy, so it fell
  straight through to mkdir + move. Now it refuses (`dry_run:true,
  requires_signature:true`) without a signature. (2) `rollback_identities`
  rewrote tags on disk immediately with no `--apply` and no dry-run branch (the
  CLI `identify-rollback` verb too). Now dry-run-default: it previews
  (`would_restore`, sample of files) and only writes with `apply:true`; the CLI
  gains `--apply` to match `reorganize-rollback`.
- NEW gates (rebuild plan §5.1):
  - `test_singlefile_cli_smoke` (row 13): builds `dist/earcrate.py` and drives the
    BUILT artifact as a subprocess through `--self-test` + `configure` + `deepclean`
    (the assess_track_audio → decode_audio path that once crashed with "earcrate is
    not a package"), asserting no import/package tracebacks. The builder only strips
    column-0 package imports, so this pins that no INDENTED in-function
    `from earcrate.<pkg> import` survives into a shipped path.
  - `test_fresh_clone_has_no_runtime_state` (row 14): `git ls-files` carries zero
    runtime pointers/DBs/caches (`*_workspace.json`, `config.json`, `*.sqlite`,
    `*.npz`, `.deps_installed`, `cache|workspace|agent|dist`), and `.gitignore`
    covers them.
  - `test_all_mutations_dry_run_default` (row 15): every mutating op
    (reorganize/rollback/migrate/apply-identities/ingest/organize/execute-manifest…)
    returns dry-run or refuses without `apply`, and touches nothing.
  - `test_done_requires_receipt` (row 16): a completion leaves a receipt artifact on
    disk the gate reads back to confirm the claimed outcome.
- Rows 13, 14, 16 were already-green — the gates PIN the invariants. Row 15 needed
  the two real fixes above. Closed via a 4-way parallel fan-out, each gate proven
  red-first in isolation, then integrated and re-verified in the main tree.

## v0.8.17 — LATTICE UI: the new console, wired to the live backend
- NEW single-file front end (`earcrate/ui/static/index.html`) rebuilt to the
  LATTICE design: a command strip (query + skin switch + crate-fit meter), a
  live system readout, a seven-mode rail (Play · Library · Crate · Workbench ·
  Sessions · Activity · Setup), and a transport bar. Four skins
  (lattice/signal/terminal/paper) as pure CSS-var swaps, persisted to
  localStorage. System fonts only — no Google Fonts link — so it stays a
  no-network local app.
- Every screen is wired to the EXISTING API (no mock data): Play ← `/api/residents`
  + `/api/rank` rails; Crate ← `/api/rank`, `/api/taste/pairs`, `/api/taste/readiness`
  with favorite/lock/reject → `/api/ear_atoms/judgment` and pair veto →
  `/api/taste/pair_judgment`; Workbench ← `/api/timeline/propose` +
  `/api/taste/graph`; Sessions ← `/api/sessions`; Activity ← `/api/status` +
  `/api/perf`; Library ← `/api/tracks` + ingest/organize/reorganize/identify/
  deepclean + the scan→analyze→extract→build pipeline; Setup ← `/api/defaults`,
  `/api/config`, `/api/config_workspace`, `/api/browse_dir`, `/api/doctor`.
  Endless radio, demo seeding, station HOTTER/COOLER/CUT, and the render
  transport are carried over from the previous UI under the new chrome.
- `residents()` now also reports `sources` (deck-safe source count) and
  `pool_size` so the Play hero can show real numbers without a second call.
- Verified end-to-end: all seven screens drive live data in a headless browser
  (seeded 5-track workspace) with ZERO console errors; single-file builds +
  SELF_TEST_OK; 14/14 gates pass.

## v0.8.16 — deterministic segment identity: force-rebuild preserves judgments
- LESSON #7 CLOSED (the front of the v3 debt queue). `extract_loops(force=True)`
  used to run `DELETE FROM loops WHERE file_id=?` and reinsert with fresh random
  ids. That delete cascaded through `ear_atoms` into `atom_judgments` and
  silently destroyed human judgment — the locked keeps and favorites that only
  real listening produces. A convenience flag was quietly deleting the most
  expensive data in the system.
- NEW `segment_id(source, analyzer, start_sample, end_sample, role, stem)`
  (v3 §2 keystone): a loop's id is now the SHA256 of its own content/recipe, not
  a ulid. Same sound + analyzer + window + role always yields the same id, so a
  re-extract is an UPSERT of the same row — id stable, only the churnable
  measurements (score, role_confidence) refresh. The atom id already derived
  deterministically from the loop id, so atoms and their judgments survive by
  construction. `force` is now RE-MEASURE IN PLACE; it never deletes a loop.
- Migration `migrate_loops_segment_identity`: additive and idempotent. Adds the
  `segment_id` column, backfills it for existing loops from their own recipe
  fields (touching no audio), and enforces uniqueness. Old ulid loop ids are
  LEFT ALONE — the column is what future rebuilds match on, so re-extracting an
  old loop upserts that same row rather than orphaning its atoms/judgments.
  Source identity here is the file id; sound-level dedup across duplicate files
  is L1 work (rebuild plan §5.3), deliberately not folded into this keystone.
- Gate `test_force_rebuild_preserves_judgments` (rebuild plan §5.1): shipped RED
  on the delete+reinsert code (proved `1 -> 0` judgments), GREEN on the upsert
  code. It locks+favorites+relabels an atom, forces a full loop rebuild, and
  asserts the judgment and its atom survive with stable ids. Placed before the
  runner block so it actually executes under `python tests/test_gates.py`.
- Verified: 14/14 runner gates pass; single-file builds + SELF_TEST_OK via
  `VERIFY_PACKAGE.py`; migration exercised on a pre-existing ulid-keyed loop
  (id preserved, segment_id backfilled, locked judgment intact).
## v0.8.26 — parallel real-library fixes (integrated into v0.8.27)
Built on main/v0.8.15, parallel to the v0.8.16–v0.8.25 seam work on PR #25.
Every fix here came from the 2026-07-11/12 real-library sessions
(`docs/SESSION_FINDINGS_2026-07-12.md` on main), not from a fixture.

- FIX **AcoustID identify 0/585**: `_acoustid_lookup` requested
  `meta=recordings+releasegroups+compress`, which the API answers with bare
  `{id, score}` results — no `recordings` key even on 0.93+ matches — so every
  lookup parsed to `match: None`. Now requests `meta=recordings` alone (the
  value verified live against the API); artist/title resolve reliably, album
  is left to tags/folders. Gate `test_acoustid_requests_recordings_meta_only`
  pins the request body so the combined value can't creep back.
- FIX **stale DB after apply-identities**: proposals without a `file_id`
  (e.g. from an external driver script) corrected the on-disk tags but left
  the DB tag cache stale, so a following `reorganize` silently planned
  against pre-retag identities. `apply_identities` now resolves a missing
  `file_id` by path and backfills the DB; if any file still can't be resolved
  the result says so and tells you to `scan` before `reorganize`. Gate:
  `test_apply_identities_backfills_db_without_file_id`.
- FIX **workspace-pointer mismatch between entry points**: the pointer was
  read from exactly one location anchored on `__main__`, so a driver script
  importing the package looked next to ITSELF, missed, and silently adopted a
  stale legacy AppData pointer — landing every write in an old JukebreakerGT
  workspace. Reading now scans the legitimate locations in priority order
  (EARCRATE_HOME override > next to the running entry point > cwd > the code's
  own directory > user profile); the legacy hidden pointer is adopted only
  when NO visible pointer exists anywhere. An explicit EARCRATE_HOME remains
  the ONLY location searched (sandbox contract). The core records which
  pointer it resolved (`pointer_resolved_from`).
- FIX **five gates never ran**: `tests/test_gates.py` had its runner block in
  the MIDDLE of the file, so every gate defined after it (workspace migration,
  in-place reorganize, deep clean, identify parsing, apply-identities) was
  silently skipped by `python tests/test_gates.py` — green CI, dead tests.
  Runner moved to the end; all 21 gates now execute, and all pass.
- CHANGE **demo warm-up self-retires**: `seed_demo_renders` refuses once any
  real (non-demo) render exists in the workspace, so synthesized first-run
  material can never sit next to real output pretending to be product.
  Demo-only workspaces still re-seed freely. Gate:
  `test_pointer_visible_beats_legacy_and_demo_seed_retires`.
- NEW `docs/HERITAGE_MAP.md`: the v0.5–v0.6 mastered mechanisms (varispeed
  lattice, role transform budgets, keyless percussion, multideck tail overlay,
  six-transition DJ grammar, turnover contract…) mapped to where each lives in
  the rebuild, which gate covers it, and which two were deliberately cut
  (two-world pairing, floor-safe rescue) — so no reskin can drop a buffalo
  silently again.

## v0.8.15 — apply identities: close the identify -> fix loop (reversible)
- NEW `apply_identities` + `rollback_identities` (CLI `apply-identities`,
  `identify-rollback`; routes `/api/identify/apply|rollback`): takes identify's
  AcoustID proposals and rewrites artist/title/album/albumartist on the files
  AND in the DB, so a following `reorganize` files them by the corrected tags.
  Simulate -> approve -> execute: dry-run by default, signature-gated apply,
  confidence-gated (--min-score, default 0.85), and every old tag is backed up
  to a journal so `identify-rollback` restores it. `identify` now saves its
  proposals to agent/identify_proposals.json for the apply step to read.
- The identify -> fix loop is now closed: identify (get truth) -> apply-identities
  (retag, reversible) -> reorganize (re-file). Each step dry-run-first.
- Gate `test_apply_identities_retags_and_reverses`: dry-run changes nothing,
  --apply rewrites disk+DB, stale signature refuses, sub-threshold matches are
  skipped, rollback restores. CLI verified through the single-file.
- Verified: 18/18 gates pass in isolation this run; single-file builds +
  SELF_TEST_OK. (The personas_coexist native DSP segfault seen earlier is an
  intermittent container-degradation issue, not code.)

## v0.8.14 — online music identity (AcoustID/MusicBrainz), opt-in
- NEW `identify_tracks` + `_fingerprint_file` + `_acoustid_lookup` (CLI
  `identify`, route `/api/identify`): fingerprints each file with fpcalc
  (Chromaprint) and looks up the real recording from AcoustID -> MusicBrainz,
  proposing artist/title/album/MBID from the AUDIO, not the (lying) tags or
  playlist folder. Dry-run only: it proposes, nothing is written. Rate-limited
  to ~3 req/s. Needs fpcalc on PATH + a free AcoustID key (--key or
  EARCRATE_ACOUSTID_KEY) -- the key + network stay opt-in, off by default.
- This is the fix for the untagged-classical / playlist-name-as-artist cases
  from the real-library run: feed confident matches into a reorganize/retag pass.
- Gate `test_identify_parses_acoustid_and_guards_key`: best-score selection +
  artist/title/album/mbid extraction, empty/error handling, real fpcalc
  fingerprinting (when present), and the no-key refusal. CLI verified through
  the single-file.
- Verified: 16/17 gates pass in isolation (the 1 non-pass is the pre-existing
  native DSP segfault in personas_coexist, reproduced on main -- container
  degradation, not code); single-file builds + SELF_TEST_OK.

## v0.8.13 — reorganize-rollback is dry-run by default (safety fix)
- REAL FOOTGUN CLOSED: `reorganize-rollback` executed IMMEDIATELY with no
  preview or `--apply` gate, unlike everything else in the tool. Someone
  (or an agent) running it "just to look" would have silently undone their
  whole reorganize. Now it's DRY-RUN by default (shows how many files it would
  move back) and only undoes for real with `--apply` — across the core method,
  the CLI, and the web UI (whose confirm-dialog button now passes apply=true).
  Verified: dry-run moves nothing; --apply restores the original layout.
- Hardened the workspace-migration gate to isolate its workspace pointer via
  EARCRATE_HOME, so a sibling test's pointer written to the repo dir can't make
  it see a phantom legacy source (flaky-gate fix, not a product change).
- Verification note: single-file builds + SELF_TEST_OK; 15/16 gates pass in
  isolation. The 1 non-pass is a pre-existing NATIVE DSP segfault in
  `personas_coexist` that reproduces on untouched main — a degraded state in
  this build container's numba/librosa stack after a restart, not a code issue
  and not on the Windows path users run.

## v0.8.12 — CLI you can drive headlessly + fix a single-file deep-clean crash
- NEW CLI COMMANDS so an agent (or you) can run the whole cleanup from the
  command line, no web UI: `configure` (set music+workspace, persists),
  `scan`, `analyze`, `deepclean` (audio-graph assessment), `reorganize`
  (dry-run plan; `--apply --signature` to execute), `reorganize-rollback`.
  Dry-run by default; JSON out. Verified end-to-end as separate processes
  through the single-file: configure -> scan -> deepclean -> reorganize plan
  -> apply (moved, journaled) -> stale-signature refusal.
- BUGFIX (would have shipped broken): `assess_track_audio` imported
  `decode_audio` INSIDE the function, which works in package mode but crashes
  in the single-file build users actually run ("earcrate is not a package").
  Hoisted to a top-level import the builder strips; deep-clean now works via
  `dist/earcrate.py`. Caught by testing the real artifact, not just the package.
- LIBRARY_WORKFLOW.md: added the agent-driven cleanup recipe.
- Verified: 16/16 gates + singlefile SELF_TEST_OK.

## v0.8.11 — deep clean: hear real songs vs static junk by the audio graph
- NEW `assess_track_audio` + `deep_clean_scan` (route `/api/deepclean/scan`):
  decodes each file and judges it by the SOUND, not tags or genre. Flags only
  silence, broadband static/noise, non-decodable/corrupt files (a JPEG renamed
  .mp3 dies at decode), and sub-1s fragments. Real music, classical, lo-fi, and
  SPOKEN WORD all pass — the classifier is deliberately genre-blind, so a
  comedian's timing is kept, not thrown away. Also finds empty folders and
  art-only folders, and counts loose images/sidecars. Dry-run assessment only;
  nothing is moved.
- Gate `test_deep_clean_hears_junk_but_keeps_voice` proves music AND a
  voice-like signal pass while silence/static/corrupt are flagged, and that
  empty + art-only folders are detected.
- This is the first piece of the "point it at the drive and find the real
  songs" cleanup. Still to come (honest): a UI card, the execute pass that
  quarantines junk to _junk/ (reversible), and online music ID
  (AcoustID/MusicBrainz) — which needs network + fpcalc + a key on your
  machine and can't be fingerprinted from the build box.
- Verified: 16/16 gates + singlefile SELF_TEST_OK.

## v0.8.10 — trim the buffalo: kill the dead two-world UI + a nav divider
- The two-world arranger backend was deleted in v0.8.0, but the UI still
  OFFERED it: "Album Collision" and "Notorious Mode" presets in BOTH the Jam and
  legacy-compose dropdowns — and "Album Collision" was the DEFAULT selected, so a
  fresh compile targeted a preset that no longer exists and silently fell back.
  The client-side presets map still carried both (and used album_collision as its
  fallback), plus a dead `mix_mode` line branching on them. All removed; the
  default is now a real preset (party_cutup).
- Killed the "Re-extract with current scoring" dev-cruft button label (the very
  first thing flagged this project) -> honest "Re-extract loops (force rebuild)".
- Added a "Setup & workbench" divider in the sidebar so the product path
  (Residents / Crate / Sessions / Activity / Jam) reads as distinct from the
  mechanical/legacy surfaces (Setup / Library / Analyze / Loops / Compose /
  Manifests).
- Verified headless (Playwright): no Album Collision / Notorious Mode / dead
  label anywhere, Jam defaults to party_cutup, divider present,
  updateOutcomePreview runs clean, no page errors. 15/15 gates + SELF_TEST_OK.

## v0.8.9 — add the Install-Dependencies.cmd the error message promised
- The import-error banner told users to "Run Install-Dependencies.cmd" but that
  file did not exist in the repo. Added it (Python check + `pip install -r
  requirements.txt`), so a dependency failure points at a real tool instead of a
  ghost. Windows helper; user-verified on Windows.

## v0.8.8 — fresh-download hygiene: stop shipping a runtime pointer
- BUG (would confuse a fresh install): three `earcrate_workspace.json` runtime
  pointer files had been swept into the repo by `git add -A` during dev runs —
  one pointed at a `/tmp/...` path from a test machine. A fresh download would
  boot trying to load a workspace that doesn't exist. Removed all three and
  gitignored `earcrate_workspace.json` / `.deps_installed` / `config.json` so
  per-machine runtime state can never be committed again. (Boot already degraded
  gracefully to the setup screen on a dangling pointer, but the repo should be
  clean.)

## v0.8.7 — reorganize gets a UI card (preview -> apply -> rollback)
- The in-place source reorganize is now a Library-tab card, not just an API:
  PREVIEW REORGANIZE (dry-run, shows the exact move plan and the _unsorted/
  count, moves nothing, enables Apply only if there is something to do),
  APPLY (confirm dialog; moves in place; synchronous since same-volume moves are
  fast; returns the journal), and ROLLBACK LAST (restores the original layout).
- Verified with a headless-Chromium button-click run: Apply stays disabled until
  Preview; Preview renders the plan with files untouched; Apply reports "Moved N
  file(s)" and reveals Rollback; no page errors. 15/15 gates + SELF_TEST_OK.

## v0.8.6 — in-place source reorganize + a real desktop icon
- IN-PLACE LIBRARY REORGANIZE (item 5), simulate -> approve -> execute.
  `reorganize_source` MOVES files within your chosen music root into
  Artist/Album/NN-Title (routes `/api/reorganize/plan|apply|rollback`). The
  preview shows the exact move plan and touches nothing; a signed plan refuses
  if the library changed; apply is journaled and DB paths follow the move;
  unidentifiable files are quarantined to `_unsorted/` (never lost); rollback
  fully restores the original layout. Nothing is deleted. Unlike
  `organize_and_retag` (which copies into working/organized), this reorganizes
  the source itself, as requested. New gate
  `test_reorganize_source_in_place_previews_and_reverses` locks it.
- DESKTOP ICON: `Create-Desktop-Shortcut.cmd` drops a clean "EarCrate" icon on
  the Desktop (and removes any leftover "Jukebreaker" shortcut). It points at
  the new `Launch-EarCrate.cmd` fast launcher (checks for Python, installs deps
  only once, rebuilds the single-file, starts the server + opens the browser).
  Windows-only scripts, authored to convention but verified by the user on
  Windows (not click-testable from the build environment).
- Verified: 15/15 gates + singlefile SELF_TEST_OK.

## v0.8.5 — finish the rename: the app no longer calls itself Jukebreaker
- HONEST CORRECTION: earlier releases claimed the rebrand was done. It wasn't —
  the app still named your work "Jukebreaker" in five user-facing places. Fixed
  now: the import-error banner, the default set names ("Jukebreaker Sketch" ->
  "EarCrate Sketch", "Jukebreaker TasteSpec" -> "EarCrate Set"), the
  "...is already busy" error, the module header, and the CHANGELOG title all
  read EarCrate.
- KEPT ON PURPOSE (these find and migrate your OLD install, so renaming them
  would break adoption): the \\Jukebreaker drive scout, jukebreaker.sqlite
  adoption, APP_NAME/legacy hidden-dir key, and the historical CHANGELOG entries
  + README lineage note (rewriting shipped history would be its own lie).
- Verified: 14/14 gates + singlefile SELF_TEST_OK.

## v0.8.4 — first-run "press play": an instant demo warm-up set
- YOU CAN PRESS PLAY ON A FRESH INSTALL. Endless plays renders, and a brand-new
  library has none until you compile — so a "Play demo" button (core
  `seed_demo_renders`, route `/api/demo/seed`) synthesizes a handful of
  listenable chord+kick loops locally (no real music, clearly a demo) and plays
  them endlessly on the spot, while you Book a set to compile YOUR library in
  the background. The "no renders yet" path now points here instead of dead-ending.
- VERIFIED headless (Playwright/Chromium) on a FRESH empty workspace: 0 renders
  → click Play demo → 8 renders seeded → Endless on, actually playing (not
  paused), no page errors. Backend: seed → list_renders shows current-engine
  passing renders. 14/14 gates + singlefile SELF_TEST_OK.

## v0.8.3 — the continuous player: a resident actually plays endless
- THE 45-MINUTE CLAIM IS NOW REAL. An "Endless" transport plays the crate
  continuously: it builds a shuffled queue of passing current-engine renders,
  auto-advances on each track's `ended`, and reshuffles when the queue is
  exhausted (avoiding an immediate repeat) — genuinely endless, not a single
  sketch. `?endless=1` on the URL auto-starts it so one link just plays.
- VERIFIED headless (Playwright/Chromium): from a seeded 10-render crate the
  player started a full queue, auto-advanced through 13 track-endings, wrapped
  past the queue and kept going (still endless), played multiple distinct
  tracks, zero page errors. Autoplay in a real browser still needs one click
  (browser policy); the queue is armed on load either way.
- Verified: 14/14 gates + singlefile `SELF_TEST_OK`.

## v0.8.2 — honest player transport + Cast to TV
- STOP ADVERTISING A PLAYER THAT DID NOT EXIST. The station `▶` used to call
  `oneClickJam()` — it started a long background compile, not playback — while
  the residents card advertised "Can play 45:00 endless off your crate." There
  was no endless player behind that number (it is `endless_sustain` CAPACITY,
  the max render is a 5-min sketch). Fixed the lie: `▶` is now a real
  PLAY/PAUSE transport for the loaded render, a separate clearly-labelled
  "Book a set" button does the compile, and the copy now reads honestly as
  "crate depth: ~MM:SS of non-repeating material" (a continuous player is the
  next slice, explicitly marked "coming").
- CAST TO TV (Remote Playback API): a "Cast" button (`audio.remote.prompt()`)
  appears when a cast target is available (Chrome/Edge → Chromecast/DLNA), with
  a live "▶ Playing on your TV" state and graceful fallback messaging where the
  browser does not expose it. No external SDK, no CSP dependencies.
- VERIFIED: 14/14 gates + singlefile `SELF_TEST_OK`, plus a headless-Chromium
  (Playwright) pass — transport + `audio.remote` wiring present, no page
  errors, Play-with-no-render toasts instead of crashing. The physical
  Chromecast handshake is the one thing only your browser + TV can confirm.

## v0.8.1 — visible-by-default layout + a one-time workspace migration
- NO HIDDEN NESTS, NO ROOT CLUTTER. The workspace now defaults to a VISIBLE
  sibling next to your music — point at `.../The Sample Factory` and it derives
  `.../The Sample Factory — EarCrate` (name derived, never hardcoded). Killed the
  `~/.local/share/JukebreakerGT` / `%LOCALAPPDATA%\JukebreakerGT` fallback in
  `configure_workspace`, and stopped the workspace scout from ever suggesting a
  drive-root or home-root folder. The one app-global breadcrumb (the workspace
  pointer) moved from the hidden AppData nest to a VISIBLE portable file
  (`visible_app_dir()`), and an old hidden pointer is adopted on first launch so
  nothing breaks.
- ONE-TIME MIGRATION TOOL (`plan_workspace_migration` / `apply_workspace_migration`,
  routes `/api/migrate/plan` + `/api/migrate/apply`): SIMULATE → APPROVE →
  EXECUTE. The preview shows exactly what will happen and touches nothing; a
  plan carries a signature so a stale apply refuses. On approval it moves
  reusable buffalo to their NEW homes (library DB with your judgments, analysis
  cache kept by name so NO re-scan is forced, renders, manifests), QUARANTINES
  anything non-conforming under `legacy/`, and scrubs dead breadcrumbs into
  `legacy/_scrubbed/`. Nothing is ever deleted; every move is journaled and
  reversible; the music library is read-only and never touched. New gate
  `test_workspace_migration_previews_then_executes` locks the contract.
  (This is a personal, this-iteration cleanup; a later version folds it into
  the library engine.)
- VERIFIED: 14/14 gates, singlefile builds + `SELF_TEST_OK`.

## v0.8.0 — the v2 cut: one composer, the legacy two-world arranger removed
- THE DEAD BUFFALO IS BURIED, not hidden. The old two-world/album-collision
  arranger — a SECOND full composer living beside the TasteSpec engine — is
  deleted outright, not merely UI-hidden: `arrange`, `propose_continuum`, the
  legacy `propose_mashup`/`one_click_mix` branches, and their legacy-only
  helpers (`build_energy_plan`, `plan_harmonic_route`, `score_key_for_pool`,
  `pick_loop`, plus dead `choose_target_key`/`compatible_era_keys`). Net −840
  lines. This closes rebuild-plan lesson #2 ("two vocabularies for one
  concept") — there is now ONE layer model and ONE composer.
- SCORER DE-TWINNED: `score_arrangement` no longer branches on the two-world
  `mix_mode` vocabulary (`two_world`/`album_collision`/`notorious_mode`). The
  voice/bed reward, the role-leak veto, and the `voice_missing` veto that only
  ever fired for two-world are gone; the TasteSpec path (the only path) keeps
  its intent-match, coverage, transform, and structural vetoes. `test_intent_
  flips_winner` still passes — behavior on the surviving path is unchanged.
- PRESETS/KNOBS PRUNED: `album_collision`/`notorious_mode` presets and the
  `voice_world_query`/`bed_world_query` world-routing knobs removed from
  `outcome_params`. Orphaned deck helpers (`world_query_match`,
  `role_world_guess`, `drydeck_role_leak`, dead `item_text_blob`) and the
  `/api/continuum/compile` route removed. `propose_mashup` remains as a thin
  back-compat adapter that routes to the TasteSpec composer.
- VERIFIED: 13/13 gates green, singlefile builds + `SELF_TEST_OK`, vertical
  suite 2/2, package compiles. No new failures vs baseline.
- STILL OPEN (honest): `APP_NAME`/state-dir (`JukebreakerGT`) and
  `ANALYZER_VERSION` are live cache/migration keys — renaming them orphans
  existing workspaces and analysis caches, so they need a real migration, not
  a rename. That is the next v2 slice, tracked separately.

## v0.7.9 — crate-librarian extracted (rebuild plan v2, phase 1)
- NEW STANDALONE TOOL `crate-librarian/`: the library engine cut loose as a
  reusable, mutagen-only package (no audio analysis, no personas, no UI, no
  network). scan → identify (the tested decades-of-dumps heuristics) → dedup →
  idempotent journaled organize into Artist/Album/NN Title, all emitting a
  versioned `library.json` contract (LIBRARY_CONTRACT.md) any project can read.
  CLI: `crate-librarian scan|report|organize|rollback`. Point it at the SSD to
  turn it into a usable, deduped, tagged archive whose manifest your NEXT
  project consumes without any mashup machinery.
- Own acceptance corpus (identity nasty-cases, full scan→dedup→organize→
  rollback pipeline, CLI) + a cross-agreement gate in earcrate
  (test_librarian_identity_agrees_with_earcrate) so the standalone identity and
  earcrate's inline identity cannot drift before the planned cutover.
- EarCrate runtime unchanged (single-file build + selftest still green);
  crate-librarian is a sibling package, extractable to its own repo per the
  plan. Next: phase B (the RTX 4060 stems provider seam).

## v0.7.8 — House flavor (one design system, under law)
- SCHEMA FIX (the big one): ear_atoms had UNIQUE(loop_id) — one atom per loop
  GLOBALLY — so personas were mutually destructive: building Troubadour's
  crate destroyed Girl Talk's (via loop-rebuild cascade), which is why it
  "required a full rebuild" and took hours. Now UNIQUE(loop_id,taste_profile)
  with an in-place migration for existing workspaces.
- ADOPT, DON'T RE-MEASURE: segment measurements are persona-independent, so a
  new resident now ADOPTS existing measurements and only re-judges them —
  measured in the gate: second resident's audition adopted 12/12 atoms in
  0.01s vs 3.0s of DSP for the first. Your two-hour Troubadour audition
  becomes seconds next time.
- PARALLEL FIRST AUDITION: the one time DSP must run, it decodes each file
  once and fans across cores (same ProcessPool discipline as analyze), with
  per-file ETA. force now re-measures IN PLACE (never deletes), and a locked
  human judgment survives force rebuilds. Gate:
  test_personas_coexist_and_adopt (includes the schema migration).
- PLAN: EARCRATE_REBUILD_PLAN_v2.md — the "fully fully" rebuild: 12-lesson
  ledger (each already gated), crate-librarian extracted as a standalone
  reusable package with a stable library.json contract (the buffalo for the
  next project), prepared attachment seams (stems, identify, progression,
  transcribe, twin, export), migration that loses nothing, cutover only on
  green.
- ACTIVITY TAB: a real view of what the engine is doing — current task, RUNNING
  pill, progress %, a live ETA COUNTDOWN (parsed from measured-throughput ETAs,
  ticking each second between polls), elapsed time, last error in ember, and
  the per-stage ledger of the run. The sidebar dot glows amber while busy.
- PINNED SHELL: the app is viewport-locked — sidebar and station bar always
  visible, only the main column scrolls (verified: bar bottom == viewport).
- TOASTS: moved inside the themed shell and restyled to the family (they were
  outside #kapp, stuck in the old console look).
- CLARITY: the topbar gauge is relabeled "CRATE FIT" with a tooltip — it is a
  MEASUREMENT of how much of the selected resident's contract your crate
  satisfies, not a progress bar (live task progress is the bottom bar). The
  ear-crating stage now shows count + ETA like analyze does (it decodes
  thousands of clips and previously read as hung). A resident with zero atoms
  now says it "hasn't auditioned your library yet — Book a set ear-crates it
  automatically" instead of listing generic shortages.
- FIX: the station bar was nested inside the flex row, crushing the main
  column to 57px — the "squeezed card in an empty page" bug. Structure
  corrected; main column now fills the shell (verified by measured layout).
- FLAVOR: EarCrate is now a registered AXM property (axm-tools
  identity/axm/PROPERTY-FLAVORS.md, branch earcrate-flavor): Dark Ecosystem
  default (void #0d0c09 — the station at night) with Cream Editorial as the
  light toggle; signature accent is the Technics pitch-LED amber; EMBER
  #C24B2C is reserved for refusals, gate failures, and thin rails only.
  Type law: Barlow Condensed (display) + IBM Plex Mono (evidence) + IBM
  Plex Sans (text), named in stacks with ZERO webfont loads (local-first,
  the acceptance-page precedent).
- ONE SYSTEM: the legacy workbench palette is bridged to the family tokens
  (the old :root variables are redefined under the shell), so Jam/Setup/
  Library/Analyze/Loops/Compose/Manifests follow the active family instead
  of floating as dark console cards on a light page. Hardcoded card/hero
  gradients removed.
- Verified by LOOKING: headless screenshots of Residents (both families),
  Jam, and Library reviewed; layout boxes measured; full shell e2e green;
  zero page errors.

## v0.7.7 — Residents (the front end becomes the product)
- SHELL: new interface built to the product mock — left sidebar (Residents /
  Crate / Sessions + the full Workbench preserved beneath), warm light theme
  with dark toggle, persistent station bar. No external fonts (local-first).
- RESIDENTS: each persona is a card with a LIVE readiness gauge, the endless
  receipt ("can play M:SS endless before a source must recur"), and exactly
  what it's missing. "Book a set" compiles with that resident; the readiness
  widget in the top bar tracks the selected one. New: GET /api/residents.
- TWO NEW RESIDENTS, both pure JSON (proving personas are data):
  · troubadour_v1 — the Pat & Sean medley contract: one persistent harmonic
    bed, sequential recognizable hooks spliced at phrase boundaries, constant
    key (capo logic), ~2.7 sources/min, 1–2 layers, intelligible vocals
    mandatory. Derivation: PERSONAS/TROUBADOUR_V1.md (22-songs-in-1:34 stunt
    ceiling vs standard-medley band). Honest gap named: real chord-progression
    matching is the next analyzer rung.
  · notorious_v1 — one voice over another era's beds (bootleg-album form):
    verse-length runs, beds rotate, voice recurs by design.
  Profile discovery is automatic (profiles/*.json + embedded in single-file);
  the drift gate now checks EVERY persona against enforced deck limits.
- CRATE VIEW: rails as columns with live counts, thin-rail alerts, the role
  bar, endless + bottleneck in the legend, click-to-audition cells, fix cards
  from real readiness failures, and topbar search (bpm:104-118 role:vocal).
- SESSIONS VIEW: every render AND every refusal with receipts (rejected
  renders' gate failures surfaced). New: GET /api/sessions.
- STATION: crowd controls with real consequences — 🔥/🧊 write durable taste
  receipts (kv + fsync journal) and bias the NEXT compile's chaos/vocal/drama
  (clamped, recorded in params); ⏭ logs. Gentle/full-send toggle actually
  reconfigures analysis workers. New: POST /api/station/feedback.
- LICENSE: PolyForm Noncommercial 1.0.0 — free for personal use, commercial
  use requires a written license; note included that music rights are separate
  from software rights.
- Verified end-to-end in a real headless browser: residents cards (3 personas
  + honest teaser), crate rails + search filter, sessions, station receipts
  persisted with correct bias math, theme toggle, and the old workbench fully
  functional inside the new shell. Zero page errors; 13 tests green.

## v0.7.6 — Compose (the rungs assembled)
- MERGED PR #8 (Codex vertical slice): governance (AGENTS.md), versioned TasteSpec
  JSON + schema + stable hash, atom/pair judgment tables, plan save/load, profile
  provenance in arrangements and render reports.
- ONE SOURCE OF TRUTH: profiles/girl_talk_v1.json (v1.1.0) is now canonical and
  DRIVES the engine — TASTE_PROFILES is a projection of it (flat_profile), the
  readiness aliases and ranking weights derive from it, and the single-file build
  embeds it. v1.0.0's aspirational numbers (drum stretch 12% vs enforced 8%,
  edge floor 0.42 vs enforced 0.54) corrected to tested reality; gate
  test_persona_single_source forbids any future drift between JSON, projection,
  and enforced deck limits.
- CURATION LOOP CLOSED: the composer now OBEYS human judgments — a rejected
  pairing is a veto (beats even a favorite), approved pairings get boosted,
  favorited atoms get pulled forward. Gate test_curation_steers_composer proves
  favorite flips the pick and veto beats favorite.
- DURABLE JUDGMENTS: compatibility edge ids are now deterministic hashes of
  (profile,left,right,relation), so rebuilding the pair graph updates scores in
  place and pair judgments SURVIVE every regraph (the merged code deleted all
  edges with random ids — every rebuild silently erased judgments).
- COMPOSE SURFACE replaces the Mashup tab: (1) the crate ranked by the persona
  with per-atom why-bars, audition, ★ favorite / ✗ reject; (2) pair explorer
  with edge receipts and ✓/✗ judgments; (3) timeline — propose a plan WITHOUT
  rendering, see sections/layers as colored rails, save/load named plans (hash +
  tastespec provenance). Old Mashup controls preserved under an advanced fold.
  New: POST /api/timeline/propose, GET /api/timeline/list, propose_plan(),
  list_plans(); /api/audio now serves read-only source/preview audio (Range
  supported) so atoms can be auditioned. Verified end-to-end in a real headless
  browser: rank → pairs → judge → propose → gate PASSES → save → load, judgments
  persisted in sqlite, zero page errors.

## v0.7.5 — Persona Codex + craft ranking
- UI DECLUTTER: removed the confusing 'Legacy two-world labels' control blocks from
  the Jam and Mashup pages (voice/bed world queries, mix-mode, aux decks) — dead
  weight from the pre-TasteSpec engine. `val()` is now null-safe so their removal
  can't break the param builder. Verified in a headless browser: no JS errors.
- OPEN FOLDER: new 'open folder' button by the render player and 'OPEN ARCHIVE
  FOLDER' by the ingest/organize receipt, plus POST /api/open_folder (reveals a
  path in the OS file manager, constrained to the configured workspace/library).
  No more hunting through hidden AppData nests.
- SANER DEFAULT: new setups default the workspace to a VISIBLE ~/EarCrate folder
  instead of a hidden AppData nest.
- VERSION: bumped so the header visibly changes on update (it stayed "v0.7.4"
  across every fix, which made "did the update land?" unanswerable). Going
  forward the version moves every shipped batch; the `· build <hash>` stamp
  still disambiguates within a version.
- RANKING: the persona now ranks raw material the way the artist reaches for it,
  not just gates it. `rank_material` (five weighted priorities: recognizability
  0.34, role clarity 0.24, danceability 0.18, deck feasibility 0.14, contrast
  0.10) grounds each on a metric the analyzer already computes, and every ranked
  entry carries its five sub-scores as a receipt — the curation surface. Deck
  feasibility is a hard reality (an unbeatmatchable loop sinks regardless of
  contrast). Surfaced via `rank_crate`, CLI `earcrate rank`, `POST /api/rank`;
  documented in PERSONAS/GIRL_TALK_V1.md §11; gate test_girl_talk_ranking.
- FIX (duration): every TasteSpec render came out ~4x its target length (a
  2-min pick became ~8 min). total_bars computed beats/4 (already bars) then
  multiplied by 4 again; now rounds to the nearest whole 4-bar phrase. A 120s
  target renders ~1.95 min.
- FIX (vocals invisible to the scorer): score_arrangement counted voice/bed via
  the legacy two-world 'world' tag, but the TasteSpec composer tags every layer
  world='taste' and marks vocals by role/ear_role — so voice_layers/realized_vocal
  read 0 on every render even when dozens of vocal layers were placed. This
  blinded the vocal_density intent-match and the voice-missing veto and made
  render reports lie. Now counts by role/ear_role too. (Vocals were being placed
  and rendered; the report was wrong. Vocal VARIETY is still capped by the
  single-key deck — see backlog.) Gate: test_taste_duration_and_vocal_count.
- JANITOR: launch-time cleanup of old-version leftovers, automatic. Purges caches
  keyed to dead analyzer/engine versions, archives ' (N)' accretion duplicates in
  the organized tree, finds legacy Jukebreaker/earcrate workspaces (AppData,
  profile, drive roots), re-ingests their songs (deduped) and rescues their
  renders to renders/rescued/, then marks each husk safe to delete. Receipt at
  agent/janitor_last.json + doctor line; deleting the husk stays a human act.
- BUILD STAMP: one content-hash stamped in three places that must match — the
  Pages download button ("currently ships v0.7.4 · build abc1234"), the package
  header, and the single-file header. Update = re-run installer until they match.
- FIX: organize is now idempotent — an existing destination means the track was
  organized on a previous run and is skipped (receipt reports the count);
  re-running can never duplicate the tree, and numeric collision suffixes derive
  from the base name so ' (2) (3)' accretion is impossible. Naming stays the
  canonical convention (Artist/Album/NN Title; compilations NN Artist - Title);
  an API-level pattern hook exists for scripts, no UI knob.
- PROOF OF WORK: ingest/organize now end with a human receipt — what happened,
  WHERE the files landed (open-in-Explorer path), and before→after samples —
  instead of a raw JSON wall; toasts on completion. Analysis status now shows
  a live ETA ("analyzing 42/96 ×16 cores · ~3m10s left").
- FIX: Library-tab Browse buttons called /api/choose_dir which never existed
  (errors were silently swallowed); now /api/browse_dir with visible errors.
- LIBRARIAN: folder-convention identity fallback — untagged files inherit
  Artist/Album from `.../Artist/Album/track` or `.../Artist/track` parents
  (generic dump/batch/drive folder names ignored; ingested/<batch>/<source>
  scaffolding skipped). 'Title by Artist' filename suffixes strip only when
  they name a known folder identity ('Stand by Me' survives), and a 'by X'
  title naming the inner folder promotes it from album to artist. Gate:
  test_identity_from_folders.
- UI: Browse buttons fixed (native Windows FolderBrowserDialog via PowerShell
  with a TopMost owner; Tk fallback; picker errors surface as a toast where
  clicked). Header rebranded EARCRATE with the version injected from
  ENGINE_DISPLAY_VERSION at serve time — it can no longer drift.
- PERSONA: `PERSONAS/GIRL_TALK_V1.md` — the complete quantitative reference for the
  first TasteSpec persona: documented sample densities, rails contract, varispeed and
  harmony math, typed-edge and acceptance-gate thresholds, and a code map for every
  number. Persona constants are now single-sourced in `TASTE_PROFILES["girl_talk_v1"]`
  (density model + endless contract included); `ear/readiness.py` derives its GT_*
  aliases from the profile instead of redefining them.
- NEW: endless-set math (`endless_sustain`): no-repeat runtime
  T = min(60·S/r, E·seconds_per_event); endless iff T ≥ min_recycle_gap_s (900 s),
  ⇒ 83 deck-safe sources unlock an honestly endless crate. Reported as an `endless`
  receipt by both `crate_readiness_audit` and `taste_readiness`, gated by
  `test_endless_math_is_exact`.
- DOCS: `LIBRARY_WORKFLOW.md` — the exact external-drive → archive → ear-crate
  sequence (ingest/organize verified end-to-end against a torture library: scene
  names, junk titles, ALLCAPS, year suffixes, feat. forms, albumartist-less
  compilations, byte dupes, idempotent re-ingest).

## v0.7.1 — Buffalo Grade (scale + decades-of-dumps hardening)
- SCALE: scan() parallelized (stat-filter -> threaded ffprobe/tag probes -> serial DB
  writes). Measured 234ms/probe single-threaded = ~3h for 50k files; now /N cores.
- SCALE: ingest uses size-ladder dedupe (size prefilter, hash only colliders, verify
  at copy time) — "gigs and gigs" over USB no longer means reading every byte twice.
  Torture run: 6 files, 0 hashed up front when no sizes collide.
- BUFFALO: albumartist key variants (TPE2/TXXX/vorbis chaos), ALLCAPS/lowercase case
  repair, album "(1998)" year extraction into date tag, "Track NN" junk-title
  rejection with honest fallback, scene "NN - Artist - Title" + underscore parsing
  with track capture, feat. canonicalization.
- BUFFALO: compilation handling — albumartist/VA detection PLUS album-level
  clustering (2+ distinct track artists on one album = compilation even with no
  albumartist tag). Comps route to Various Artists/Album/NN Artist - Title and get
  albumartist amended on the copies. No more shattered NOW-That's-Music folders.


## v0.7.0 — Library Forge (this build)
- RESTRUCTURE: the 5,728-line monolith is now the `jukebreaker/` package per
  JUKEBREAKER_REBUILD_PLAN v1 (modules: core, analyze, deck, ear, judge, librarian, ui).
  Single-file distribution preserved: `build/make_singlefile.py` emits `dist/jukebreaker_gt.py`
  deterministically; VERIFY_PACKAGE checks it. The 56KB embedded HTML string is now a real
  file at `jukebreaker/ui/static/index.html`.
- NEW: multi-folder ingest. Select any number of source folders (external SSD etc.);
  audio is copied into `master/ingested/<batch>/` — content-hash deduped against the whole
  library, manifest-gated (dry-run default), journaled, rollback-able, sources never touched.
  UI panel on the Library tab; CLI: `ingest <folders...> [--apply]`.
- NEW: organize + retag. Builds `working/organized/Artist/Album/NN Title.ext` copies with
  amended tags (artist/albumartist/album/title/track normalized deterministically). Masters
  stay verbatim per spec copy-then-edit. UI buttons; CLI: `organize [--apply] [--limit N]`.
- New executor op types `ingest_copy` / `organize_copy` with full prevalidation
  (path-root checks, dst_absent, copy-hash verification) and archive_move inverses.
- DEPLOY HYGIENE: 22 PATCH_NOTES files consolidated into this one CHANGELOG.md.

---
# Historical patch notes (v0.2 → v0.6.3), newest first


## v0.6.3

Jukebreaker GT v0.6.3 - Workspace Scout

Purpose
- Stop making the user guess where the workspace should live. The engine
  knows its own constraints; setup should apply them.

Changes
- Added workspace_candidates(): enumerates candidate locations (existing
  configured workspace, per-drive roots on fixed drives, user-profile
  locations) and scores each against the constraints the engine itself
  imposes. Receipts per candidate: free headroom (analysis .npz cache,
  renders, previews, rollback archives all accumulate), drive kind
  (fixed / removable / network via GetDriveTypeW on Windows), sync-client
  detection (OneDrive/Dropbox/Google Drive/iCloud path markers plus
  OneDrive env roots; sync clients fight the fsync JSONL journals and
  SQLite locks), and a live fsync probe (3x256KB fsynced temp write,
  reported in ms). Hard rejects: candidate inside the music folder, or
  music folder inside the candidate; the executor's path-containment
  invariant (INV-1) requires the separation, so setup enforces it up front.
- Existing workspaces are detected and score a bonus: adopting one
  preserves the database and analysis cache.
- Setup UI: a Suggest button next to Browse fills the workspace field with
  the top candidate and renders the ranked list with reasons; click any
  non-rejected candidate to select it.
- API: POST /api/workspace_candidates {music_folder}.
- CLI: python jukebreaker_gt.py workspace-candidates --music PATH.
- Nothing is created on disk by the scout; it is read-only apart from the
  self-deleting fsync probe file.

Correction carried in this patch
- ANALYZER_VERSION is reverted to gt-v0.6.1-earcrate-feasibility. The
  v0.6.2 runtime-ledger patch bumped it to gt-v0.6.2-runtime-ledger while
  analyze_file_worker was byte-identical to v0.6.1. Because the features
  query selects on analyzer_version and the disk cache is keyed
  {sha}-{ANALYZER_VERSION}.npz, that bump orphaned every cached analysis
  and forced a full library re-analysis to add instrumentation. The pin
  moves only when the DSP actually changes.

Lineage note
- Built on the v0.6.2 Runtime Ledger branch. Does NOT include the
  fail-fast batched harvest from the parallel v0.6.2 branch; that merge
  is still pending as v0.6.4 if desired.
- Engine marker: gt_tastespec_v0603.

Validation
- python -m py_compile jukebreaker_gt.py VERIFY_PACKAGE.py
- python jukebreaker_gt.py --self-test
- python VERIFY_PACKAGE.py
- workspace-candidates CLI run against a container filesystem: ranked
  output with fsync timings; inside-music candidates hard-rejected;
  existing workspace adopted as top recommendation.

## v0.5.1

Jukebreaker GT v0.5.1

This package fixes the handoff failure in v0.5.0. The source-only zip did not include a local .venv and did not include a bootstrap launcher, so a fresh folder could not start from the command the assistant kept repeating.

Added:
- START_HERE.cmd: creates .venv, installs requirements, launches the app.
- RESET_LOCAL_ENV_AND_START.cmd: rebuilds only the local Python environment.
- README_FIRST.txt: explains the two-folder setup model.

The application UI still uses Music folder + Jukebreaker workspace and Browse buttons.

## v0.5.16

Jukebreaker GT v0.5.16 - audible rescue lattice

Intent:
- Keep fast failure for bad expressive candidates.
- Stop leaving the user with a silent/product-purity outcome when a conservative audible deck can be made.

Changes:
- Clears stale Last error banners at the start of a new run and after successful completion.
- Keeps expressive candidate preflight fast: failed expressive plans still skip full WAV render.
- Makes the repaired varispeed rescue less brittle: non-structural dry-quality preflight failures are allowed to proceed to the post-render gate, because stable_presence_restore runs only during render.
- Adds a final floor-safe audible rescue after expressive and repaired candidates fail.
- Floor-safe rescue abandons two-world ambition before abandoning audio output: single-crate, one auxiliary deck, low chaos, low stretch, zero residual pitch budget, high key strictness, short 75-120 second proof mix.
- Floor-safe rescue still writes only under working_root/renders and still requires the post-render quality gate before loading the player.
- Failure copy now says expressive, repaired, and floor-safe candidates failed, rather than implying a previous render should be trusted.

Analyzer note:
- The analyzer cache version remains gt-v0.5.15-librosa-varispeed-lattice-dna because v0.5.16 changes execution policy, not analysis features.

## v0.5.17

Jukebreaker GT v0.5.17 — Audible Truth Gate

Problem fixed:
- v0.5.15/v0.5.16 could bless a correctly sized WAV that contained microscopic noise for most of the timeline and a small audible tail near the end. The old post-render silence metric was relative to the median frame RMS, so near-zero noise could count as non-silence.

Changes:
- Post-render gate now measures absolute audible coverage: active_coverage_ratio, audible_seconds, first_audible_s, last_audible_s, largest_silence_gap_s, global_rms, and audible_rms_floor.
- Long renders fail if audible coverage is too low, first audible material starts too late, or a dead gap is too long.
- Arrangement preflight now rejects structurally empty plans before rendering: too few layer events, low covered_bar_ratio, late first layer, too many empty music sections, and missing vocal identity when hooky two-world mode asks for vocals.
- Arrangement scoring now rewards covered musical timeline and layer depth, and it vetoes one-layer-tail plans before they can waste a render.
- Judge silence gate direction corrected.

Fast fail remains fast. The change is that a passing render now has to be audible as a song body, not merely a WAV-shaped receipt.

## v0.6.1

Jukebreaker GT v0.6.1 — TasteSpec feasibility compiler

This patch fixes the root exposed by v0.6.0: the ear crate had inventory, but the composer was allowed to select atoms that could not actually play at the chosen BPM/key. add_layer() then silently dropped them, producing a mostly empty pre-render plan that the new gate correctly rejected.

Changes:
- Adds tempo-octave folding before varispeed planning, so half-time and double-time analyzer disagreements no longer destroy valid DJ tempo islands.
- Treats the BPM input as a taste hint in the one-click TasteSpec path rather than a hard pin. The compiler now chooses the BPM/key deck with the strongest playable foreground, floor, bass, spark, and source-turnover feasibility.
- Filters the approved ear crate into a transform-feasible pool before composition. The composer no longer discovers illegal atoms after the plan has already been built.
- Makes source rotation part of deterministic composition so one source cannot become the whole floor rail by accident.
- Forces the first phrase to carry a recognizable foreground when foreground atoms exist.
- Adds adaptive harvest expansion: if a bounded track budget is too short for Girl Talk density, one-click expands to all scanned tracks before refusing.
- Updates UI defaults: the BPM box is blank by default, and the track budget defaults to all scanned tracks because Girl Talk-style source turnover is source-hungry.

This is not a rescue fallback. It is a feasibility correction: choose a playable deck from the material before composing, then render only if the TasteSpec rail contract passes.

## v0.5.12

Jukebreaker GT v0.5.12 — Full Buffalo Deck

Purpose
- This is not a rollback. It keeps the continuum/two-world/multideck feature stack and fixes the failure loop that made bad ideas cost full WAV render time.

Durable changes
- Engine marker: gt_fullbuffalo_v0512.
- Adds approved-loop dry quality preflight before arrangement selection.
- Adds arrangement_preflight_gate so doomed candidates can be rejected before a full WAV render.
- Candidate search still compiles up to 64 arrangements, but only a preflight-approved candidate reaches the renderer.
- One-click renders one expressive candidate and, if needed, one repaired full-buffalo rescue candidate. It does not spend twenty minutes rendering four known-bad full WAVs.
- Tightens transform budgets by role: drum/bass/floor material must stay near native tempo and key; vocals remain readable.
- Dry deck rendering uses deterministic varispeed/resample for small approved corrections instead of phase-vocoder time-stretch in stable/dry mode.
- The post-render quality gate separates catastrophic failures from warnings, so the engine still blocks degraded audio but does not turn every imperfect sketch into a dead-end failure.
- Reports include candidate preflight receipts, dry loop quality receipts, transform policy, cache stats, deck receipts, and quality gate warnings/failures.

Preserved capabilities
- Two-world continuum controls.
- Album collision / Notorious mode.
- Multideck tail overlay.
- Role-locked voice/bed worlds.
- Named DJ transitions.
- Transform cache.
- Source/workspace separation.
- Quota approval, not landfill bulk approval.
- Current-engine render filtering.

Validation
- python -m py_compile jukebreaker_gt.py
- python jukebreaker_gt.py --self-test
- python VERIFY_PACKAGE.py
- node --check extracted_ui_v0512.js

## v0.5.13.1

Jukebreaker GT v0.5.13.1  — Lattice completion + readiness dashboard + console UI

WHY: v0.5.13 shipped the varispeed transform math but the "lattice" it was named
after did not exist in code — render_bpm was still a single median/pinned value.
Two receipt bugs and a dead UI knob were also present.

ENGINE
- NEW  build_bpm_lattice / score_bpm_lattice: scores candidate deck speeds (native
       BPM clusters + a symmetric lattice around the target) by total clean-transform
       cost over the approved pool. Pure, render-free, deterministic.
- NEW  crate_readiness_audit: per-role usable counts, native-BPM window histogram,
       transform-tier histogram, source-dominance warnings, recommended BPM.
- WIRED arrange() now picks render_bpm from the lattice when BPM is blank (was: raw
       median); honours a user pin but records the lattice cost either way. Arrangement
       and render report now carry a bpm_lattice receipt.
- FIX  budget no-op: allowed_varispeed = min(lim, max(lim, user)) always collapsed to
       lim, so the stretch-budget knob did nothing. User budget now actually constrains.
- FIX  incoming_downbeat_error_ms was hardcoded 0.0; now measured from actual placement.
       outgoing_energy_zero_before_boundary now measured from the tail, not asserted False.

API
- NEW  POST /api/preflight -> readiness audit for the current outcome params.

UI (no-network, system fonts only)
- Full restyle to a "mixing console" identity: graphite chassis, amber tempo-readout
  LED accent, cyan cue accent, coral warnings.
- SIGNATURE: the lattice rendered as a pitch-fader ladder — bar height = usable loops,
  amber = recommended speed, dashed = your target.
- Deck-readiness panel on Jam: role counts (short roles flagged coral), clean-vs-synthetic
  transform tier bar, and plain-language warnings — so a thin pool fails in seconds with a
  reason instead of after a long render.
- One-click jam auto-runs preflight and surfaces the verdict before you commit.

VALIDATION: py_compile OK, --self-test SELF_TEST_OK, VERIFY_PACKAGE ok:true.

## v0.5.14

Jukebreaker GT v0.5.14 — Settings that actually steer + grounded readiness

WHY: two renders felt identical despite "drastic" setting changes. Measured: the two
files were bit-identical audio (correlation 1.0000). Root causes, all fixed:

1. ALIASED MODE. album_collision / two_world_continuum / notorious_mode were one code
   path — toggling changed only the output hash. Mix-mode dropdown collapsed to two
   honest choices (two-world vs single-crate); creative character lives in the preset.
2. FIXED-IDEAL SCORER. score_arrangement rewarded a constant ideal (always more
   diversity/edits), so same pool+seed always won regardless of sliders. Replaced with
   an INTENT-TARGETING scorer: rewards realized-vs-requested chaos, drama, genre
   whiplash, and vocal density; keeps only true failures (transform violations, role
   leaks, dead-air, over-reuse) as hard vetoes. Verified: HIGH chaos/drama now selects
   a choppy dynamic plan, LOW selects a calm one — the winner flips with the sliders.
3. HARD CAPS. pitch_budget=min(2) and stretch_budget=min(8.5) silently ignored the
   knobs above the cap. Removed; user budgets honored up to role-tier ceilings.

READINESS, NOW GROUNDED IN GIRL TALK DENSITY (not invented minimums):
   Feed the Animals ~300+ samples/53min, All Day ~372/71min => ~5.5 samples/min,
   a new element every ~11s, 2-4 layers, ~15-25 sources per 4-5 min stretch.
   The audit reports have-vs-need sample-events, distinct sources, bed riders, and
   foreground for the requested track length, and names the real bottleneck. For 40
   random songs that is almost always clean drums + isolatable vocals -> recommends
   stems. The old "pool thin" verdict fired on healthy pools; it had no basis.

DOCS: JUKEBREAKER_SPEC_v2_CONSOLIDATED.md supersedes BUILD_SPEC v1.0 + ADDENDUM A v1.1,
which described an architecture the code never implemented. v2 describes the real code,
grounds thresholds in Girl Talk numbers, and defines runnable acceptance gates.

VALIDATION: py_compile OK; --self-test SELF_TEST_OK; VERIFY_PACKAGE ok:true; intent
scorer flips winner with sliders; grounded readiness READY on balanced 40-song pool.

## v0.5.8

Jukebreaker GT v0.5.8 — Dry Deck Stable Build

This build is a rollback-hardening pass, not a feature expansion.

Durable fixes:
- Engine marker: gt_drydeck_v058.
- Defaults now favor dry-deck playback over cave/wash artifacts.
- Strict transform budgets are enforced before arrangement and again during render.
- Vocals: max ±2 semitones and <=5% stretch.
- Drum anchors: max ±1 semitone and <=6% stretch.
- Bass/harmony/texture/full roles have conservative dry budgets.
- Two-world mode now locks roles: voice world supplies vocals; bed world supplies drums, bass, harmony, texture, and full beds.
- Candidate search penalizes or vetoes transform violations, role leaks, false bass swaps, same-source overuse, and excessive tail density.
- Multideck tails are pruned by transition type. Bass swaps carry only low + rhythm by default; hook blends carry the dry floor, not the whole previous section.
- Tiny timing corrections avoid the heavy phase-vocoder path when possible.
- Post-render dry-deck quality metrics are written to the render report. For renders >=60 seconds, failed gates raise an error instead of silently presenting degraded audio as a success.

This should stop the specific degraded failure mode heard in v0.5.7: cave tone, phase smear, over-transformed vocals/drums, role leakage, and too many wet outgoing tails.

## v0.5.10

Jukebreaker GT v0.5.10 — Floor Safe Deck

Purpose
- Fix the v0.5.9 behavior where all stable-deck attempts could be rejected and the UI still showed an old v0.5.8 render, creating a false success state.
- Keep degraded audio blocked while adding a conservative floor-safe rescue pass that is built to be dry, continuous, and transform-light.

Changes
- Engine marker: gt_floorsafe_v0510.
- Analyzer marker: gt-v0.5.10-librosa-floor-safe-dna.
- Added final floor-safe rescue after expressive stable-deck retries fail.
- Rescue mode uses low chaos, low drama, strict key safety, max ±1 pitch shift, max 4.5% stretch, one auxiliary deck, no true-air cuts, and continuous build/sustain sections.
- Candidate scorer now penalizes same-source overuse, low source diversity, hard-air transitions, excess predicted silence, and excess tail density more aggressively.
- Presence repair is still dry and conservative, but stronger against the specific cave/muffle failure.
- Render list now marks current-engine and gate status from render reports.
- UI hides old/stale renders by default when there is no passing current-engine render.
- UI can clear the audio player instead of leaving the last old render loaded after a failed run.
- HTTP server now suppresses benign browser-aborted socket writes instead of printing scary ConnectionAbortedError traces.

Non-goals
- Does not bypass the quality gate.
- Does not load rejected renders.
- Does not remove continuum, multideck, two-world, transform cache, or transition receipts.

## v0.5.9

Jukebreaker GT v0.5.9 — Stable Deck Quality-Retry Build

Purpose:
- Turn the v0.5.8 dry-deck gate from a blunt user-facing stop into a durable autopilot.
- Bad audio is still blocked, but Jam Now now retries safer candidate plans before reporting failure.

Changes:
- Engine marker: gt_stabledeck_v059.
- Failed quality-gate renders are quarantined under agent/rejected_renders instead of being loaded into the player.
- Jam Now performs up to four quality attempts, reducing dead-air/drama/tail density on retries.
- Candidate search now penalizes predicted silence, excess hard-air cuts, and over-dynamic plans.
- Stable-deck planning limits true air sections before render.
- Added mild deterministic presence restoration before the dry-deck quality gate.
- Kept v0.5.8 guardrails: transform budgets, strict two-world roles, pruned multideck tails, transform cache, manifest/rollback records.

## v0.5.5

Jukebreaker GT v0.5.5 — Multideck Tail Overlay build

This build fixes the structural DJ bug in v0.5.4: a crossfade cannot be built by splicing the incoming head early into the already-summed past. DJing requires live decks. This renderer now treats sections as decks with outgoing tails.

Changes:
- Engine marker: gt_multideck_v055.
- Replaced single-timeline pre-boundary splice with multi-deck tail overlay.
- Incoming section downbeats stay on the planned grid; transition window starts at the section boundary.
- Outgoing sections render an overhang tail when the next transition is a blend type.
- Layer fade-out is suppressed when the layer participates in an outgoing tail; the transition curve is the fade-out.
- Transition reports now include deck_model, overlap_side, tail_deck_count, source_tail_sections, incoming_downbeat_error_ms, transition window samples, and outgoing_energy_zero_before_boundary.
- Supports up to four live tail decks in the mixer path, with the current arrangement usually using the main outgoing deck plus the incoming deck.
- Kept v0.5.4 DJ primitives: beatmatch_blend, bass_swap, acapella_bridge, impact_drop, hard_cut_pickup, hard_cut_to_air.
- Kept v0.5.3 guardrails: source immutability posture, quota approval, no landfill bulk approval, verifier, loopback token, and source-only packaging.

Validation:
- python -m py_compile jukebreaker_gt.py
- python jukebreaker_gt.py --self-test
- python VERIFY_PACKAGE.py

## v0.5.3

Jukebreaker GT v0.5.3 Right Build

This package is the cleaned source package built from the v0.5.2 fresh-start upload plus the earlier GT fixes and shortcuts.

Fixed:
- Internal engine/version marker now matches the package: gt_right_build_v053.
- Browse buttons now call a real local Tk folder picker. If Tk is unavailable, the UI receives a recoverable error and the user can paste paths.
- Default workspace no longer sits inside the Music folder, which prevented first-run setup from satisfying source immutability.
- Loop extraction no longer defaults to direct bulk approval.
- Loop Review now exposes quota approval as the main shortcut and removes the two landfill buttons.
- API-level bulk approval is blocked, so stale UI or direct calls cannot approve the whole candidate pool by accident.
- Self-test now exercises quota approval before proposing and rendering the synthetic mashup.

Still intentionally source-only:
- No .venv is bundled. START_HERE.cmd creates it locally.
- No user music, cache, database, renders, or generated workspace files are bundled.
- No network behavior is added to the core application. Dependency installation uses pip only during setup.

## v0.5.15

Jukebreaker GT v0.5.15 — Spec-authority realignment for manifest execution

WHY: the previous pass let implementation reality rewrite the safety spec. This patch drags the executor back to the spec boundary while keeping the useful musical work from v0.5.14 inside that boundary. The varispeed lattice, intent scoring, and Girl Talk density audit remain features; they do not replace the safety model.

CHANGES:
- Manifest execution is dry-run by default. API callers must pass apply=true, and CLI callers must pass --apply, before outputs are written.
- Whole-manifest prevalidation is now factored into prevalidate_manifest(), which returns the same execution plan used by dry-run and apply-mode execution.
- The browser manifest table now separates DRY RUN from APPLY NOW and APPLY BG so the default button is non-mutating.
- Added rollback_outputs(), a real rollback executor for generated artifacts recorded in rollback.jsonl. It archives generated renders, playlists, and render-report sidecars under agent/archive/rollback instead of deleting them.
- Added guarded CLI twins: jukebreaker-gt manifest <path> [--apply] and jukebreaker-gt rollback [--manifest-id ID] [--limit N] [--apply]. Both are dry-run by default.
- Added rollback source validation so rollback can only touch generated-output roots, never the master music library.
- Operation journals now record apply-mode execution explicitly, and rollback application writes rollback_applied.jsonl receipts.

VALIDATION PERFORMED HERE:
- python -m py_compile jukebreaker_gt.py VERIFY_PACKAGE.py passed.
- Synthetic manifest self-test passed: dry-run created no playlist, apply created the playlist, rollback dry-run moved nothing, rollback --apply archived the generated playlist, and an unknown operation type was rejected before mutation.

## v0.5.2

Jukebreaker GT v0.5.2

Fixes the startup UI JavaScript syntax error in v0.5.1 that left the page showing only the header/status/player and no navigation or setup tabs. The broken string was in Judge Render UI code.

Fresh start:
1. Unzip.
2. Double-click START_HERE.cmd.
3. Use Setup with Music folder and Jukebreaker workspace.

## v0.5.6

Jukebreaker GT v0.5.6 — Continuum Compiler

This build turns v0.5.5 from a multideck batch renderer into the first continuum-oriented compiler.

Changes:
- Engine marker: gt_continuum_v056.
- Adds candidate arrangement search before audio render, so the engine can reject weak plans before paying WAV cost.
- Adds two-world / album-collision crate logic: voice world supplies hooks and lead identity, bed world supplies drums, bass, harmony, and texture.
- Adds world receipts per layer: world, source_track_key, native_key, and source_bpm.
- Upgrades multideck tail overlay into role-group aux decks: low, rhythm, voice, texture, and mixed tails.
- Adds transform cache under agent/cache/transforms/<engine>, keyed by loop, target length, pitch shift, sample rate, and engine version.
- Adds transform cache hit/miss receipts in render reports.
- Adds hook_blend_over_bed so the engine does not falsely report a bass handoff when the floor owner is intentionally preserved.
- Adds continuum lookahead compile API (/api/continuum/compile), which writes a local plan JSON without requiring immediate full render.
- Keeps the right-build guardrails: source/workspace separation, quota approval, no landfill bulk approval, manifest-gated execution, and package verification.

Known scope:
- This is still source-only and local-first.
- Live audio device output is not bundled; Continuum is compiled lookahead plus stream-ready planning, not a cross-platform audio driver.

## v0.5.13.2

Jukebreaker GT v0.5.13.2  — Performance: parallel analysis, JIT warmup, sane cap

WHY: analysis felt slow. Measured cause was structural, not algorithmic — every
individual op is fast in steady state. The three real offenders:
  1. analyze() was single-threaded (used 1 of N cores). Spec called for a process
     pool; it was never built.
  2. librosa's numba JIT compiled on the FIRST analyze/render call (~5-10s), on the
     request path, with no progress — looked like a freeze.
  3. MAX_ANALYSIS_SECONDS was 12 minutes, so every file was decoded + beat-tracked
     up to 12 min deep. Fine for songs, catastrophic for any long mix/set/podcast.
NOT a cause (verified): the 64-candidate arrangement search. arrange() is pure
planning + dict-math preflight, no decode/DSP. Do not "optimize" it by lowering
candidate_count; that is not where time goes.

CHANGES
- PARALLEL analyze(): decode + DSP now run across cores via ProcessPoolExecutor.
  * fork context on Unix (workers inherit the imported module, no re-import, no risk
    of a child re-running server startup); spawn on Windows; freeze_support() added.
  * Worker count = config.workers, or auto = max(1, cpu_count - 2).
  * Cache-hit fast path: already-analyzed files load from npz in-process (no pool).
  * All DB writes stay in the parent process (workers return plain feature dicts).
  * Robust serial fallback: any pool/spawn failure degrades to single-core, never
    breaks analysis. Result reports parallel=true/false and worker count.
- WARMUP: warmup_dsp() pays the numba JIT cost once at server start, in a daemon
  thread off the request path. First real analyze/render no longer stalls.
- CONFIGURABLE analysis depth: new analysis_seconds (default 180, hard ceiling 720)
  replaces the fixed 12-minute cap. Exposed in Setup > Performance, persisted to
  config.json and config.toml.
- SETUP UI: Performance section adds "Analysis depth per track" and "Analysis
  workers" (0 = auto). Populated on load, saved with the workspace.

REFACTor (no behavior change): the three self-free DSP helpers (estimate_downbeats,
vocal_likelihood, estimate_sections) are now module-level functions so a worker
process can call them; the instance methods delegate to them.

VALIDATION: py_compile OK; --self-test SELF_TEST_OK; VERIFY_PACKAGE ok:true;
end-to-end 7-file analyze with pool (parallel=true, 0 failures), warm-cache re-run
0.00s, Setup round-trips analysis_seconds=90/workers=3.

## v0.6.2

Jukebreaker GT v0.6.2 - Runtime Ledger

Purpose
- Add wall-clock runtime instrumentation to the TasteSpec one-click path so performance work is evidence-driven.
- Preserve the v0.6.1 TasteSpec feasibility architecture and analysis worker path.

Changes
- Added a durable runtime ledger for one-click TasteSpec runs.
- The ledger records named stage durations for doctor, scan, bounded analysis, loop extraction, ear-crate build, readiness, graph build, composition/gating, expanded harvest passes, and render execution.
- Analysis now returns internal phase timings: row selection, cache load, compute, DB write, total, cache hits, compute jobs, workers, and parallel mode.
- The UI now shows a Runtime Ledger card with elapsed time, stage count, top stages by wall-clock time, and the path to the JSON ledger.
- The latest runtime ledger is written to agent/perf/last_run.runtime_ledger.json and each run also gets a stable run_id.runtime_ledger.json file.
- Added /api/perf for inspecting the last ledger from the local UI/API.

Validation
- python -m py_compile jukebreaker_gt.py VERIFY_PACKAGE.py
- python jukebreaker_gt.py --self-test
- python VERIFY_PACKAGE.py

## v0.5.13

Jukebreaker GT v0.5.13 - Varispeed Lattice Deck

Purpose
- Fix the deck-theory category error: tempo movement and pitch movement are not always separate DSP operations.
- Model turntable/DJ varispeed first: changing deck speed changes BPM and pitch together cleanly by resampling.
- Use synthetic pitch shifting only for the small residual after varispeed.

Core changes
- Added tempo-key lattice planning:
  speed_ratio = target_bpm / source_bpm
  natural_pitch_shift = 12 * log2(speed_ratio)
  residual_pitch_shift = desired_key_shift - natural_pitch_shift
- Candidate selection now rewards clean varispeed solutions and penalizes synthetic residual correction.
- Arrangement layers now carry transform receipts: transform_mode, speed_ratio, varispeed_pct, natural_pitch_shift, desired_key_shift, residual_pitch_shift, artifact_risk.
- Render cache keys include the residual pitch plan and engine version.
- Stable/dry render mode uses varispeed resample first, then small residual pitch correction.
- Dry-deck transform budgets now separate clean varispeed percentage from synthetic residual pitch budget.
- Self-test is shortened so package verification does not become a long render loop.

Non-goals
- This does not bypass dry-deck quality gates.
- This does not revert continuum, two-world crates, multideck tails, role locks, transform cache, or render quarantine behavior.

## v0.5.4

Jukebreaker GT v0.5.4 — DJ Compiler build

This is the first source package that treats the mashup engine as a DJ/mix compiler rather than a loop placer.

Changes:
- Engine marker: gt_dj_compiler_v054.
- Added phrase-aware transition metadata per section.
- Added named transition primitives: beatmatch_blend, bass_swap, acapella_bridge, impact_drop, hard_cut_pickup, hard_cut_to_air.
- Added equal-power and S-curve fade curves.
- Added low/high split transition blending so bass swaps keep one low-end owner.
- Added harmonic route planner to prevent two-minute sketches from collapsing into one or two pitch centers.
- Added beat_dna and chord_dna summaries into arrangement sections.
- Fixed energy-plan repair so mandatory cuts/breakdowns do not shorten the requested render length.
- Render reports now include transitions with fade type, phrase boundary, harmonic relation, bass owner before/after, xfade beats, and xfade samples.
- Kept v0.5.3 guardrails: source immutability posture, quota approval, no landfill bulk approval, verifier, loopback token, and source-only packaging.

Validation:
- python -m py_compile jukebreaker_gt.py
- python jukebreaker_gt.py --self-test
- python VERIFY_PACKAGE.py

## v0.6.0

Jukebreaker GT v0.6.0 — TasteSpec Ear Crate

Purpose
- Stop treating a render gate as the product.
- Encode taste as deterministic, inspectable rules before the arranger is allowed to compose.
- Use Girl Talk as the first acceptance profile, not as a fallback or degraded mode.

New architecture
- Added TasteSpec profile support with girl_talk_v1 as the first contract.
- Added EarAtom role taxonomy: VOX_HOOK, VOX_VERSE, VOX_SHOUT, DRUM_BREAK, BASS_RIFF, BED_CHORD, RIFF_ID, TEXTURE, PICKUP_FILL, DROP_HIT, TRANSITION_TAIL.
- Added ear_atoms SQLite table. A song slice is not arrangement material until it becomes an approved EarAtom.
- Added compatibility_edges SQLite table. The compiler now stores typed relations such as vocal_over_bed, bass_over_drums, and spark_into_phrase.
- Added deterministic floor rail, foreground rail, and spark rail composer.

Rules now enforced before render
- Approved crate must contain enough foreground, floor, bass, spark, and source-identity material for the requested duration.
- First recognizable foreground must arrive early.
- Floor coverage and foreground coverage are measured as timeline obligations.
- Source identity turnover is part of the style contract.
- A plan cannot render if the crate or style contract is structurally empty.

New callable paths
- UI one-click now defaults to TasteSpec girl_talk_v1.
- Track budget default raised to 240 because this profile needs source turnover.
- Added API endpoints:
  - GET /api/ear_atoms
  - POST /api/ear_crate/build
  - POST /api/taste/readiness
  - POST /api/taste/graph
- Added CLI twins:
  - python jukebreaker_gt.py ear-crate --force --previews
  - python jukebreaker_gt.py taste-readiness --seconds 120
  - python jukebreaker_gt.py taste-graph --seconds 120

Validation
- python -m py_compile jukebreaker_gt.py passed.
- python jukebreaker_gt.py --self-test passed.
- python VERIFY_PACKAGE.py passed.

Known validation note
- The local container segfaults inside the inherited librosa analysis path when running full PCM feature extraction. The same crash reproduces in v0.5.17, so it is not introduced by TasteSpec. Static verification and guarded-executor self-test pass here; full audio validation should be run on the target Windows environment where the prior versions already analyzed the user's library.

## v0.5.7

Jukebreaker GT v0.5.7 — Continuum Control Surface Fix

This build fixes the v0.5.6 UI exposure gap. The continuum compiler existed in the backend, but Jam Now still showed the older v0.5.5/v0.5.6 batch controls.

Changes:
- Engine marker: gt_continuum_v057.
- Header now displays Jukebreaker GT v0.5.7 Continuum.
- Jam Now exposes Album Collision / Notorious Mode.
- Jam Now exposes two-world controls: mix mode, voice world query, bed world query, candidate arrangement count, lookahead seconds, max auxiliary decks, and transition priority.
- Defaults match the current test doctrine: 2 minute sketch, track budget 80, 126.05 BPM, 64 candidate plans, voice world "2017 top 40", bed world "2014 indie".
- Adds a COMPILE CONTINUUM PLAN button wired to /api/continuum/compile.
- Advanced Outcome Controls mirror the same continuum fields.
- Keeps v0.5.6 continuum compiler, transform cache, multideck aux tails, source/workspace separation, quota approval, and package verification.

## v0.7.2 - Organ Transplant (2026-07-09)

The v0.7.x rebuild forked from the v0.6.2 runtime-ledger branch and lost
four battle-tested fixes from the v0.6.4/v0.6.5 line. This release ports
them into the modular layout. No architecture changes.

- Keyless percussion (v0.6.5): KEYLESS_ROLES = {drum_anchor, fx} in
  deck/transform.py. Percussive material is no longer key-gated on
  analyzer key noise. Measured on the reference 64-track library: the
  refused deck (129.2 BPM, key 9) goes from 9/11 to 28 distinct sources.
- Turnover contract (v0.6.4): deck selection restricted to decks that
  keep needed_sources when any exist; pre-compose refusal with the deck
  named when none can; hard source rotation in the composer's pick().
- Fail-fast batched harvest (v0.6.4): per-batch readiness checks under
  the runtime ledger (harvest_bN_* stages), early refusal with per-axis
  yield projection, batched gate-miss recovery (missN_* stages).
- Honest veto reporting (v0.6.4): arrangement-score vetoes name only the
  violated conditions with values; voice_missing exposed in the score.
- Ear-crate memory fix (v0.6.4): loops processed grouped by source file
  with a single-entry decode cache instead of holding every track's PCM.
- dj_compiler stamp corrected from the stale v0.6.2 to v0.7.2.
- ENGINE_VERSION: gt_library_forge_v072. ANALYZER_VERSION unchanged.

### v0.7.2 addendum: key discipline was silently disabled in v0.7.0/v0.7.1

deck/transform.py never imported pitch_distance (the module cycle
harmony -> judge -> lattice -> transform -> harmony made the direct
import impossible), and the bare `except Exception` in
nearest_harmonic_shift converted the resulting NameError into raw=0.0.
Effect: every loop was treated as already in the target key, for every
role including vocals. The entire dry-deck harmonic doctrine was off,
and the gates could not catch it because they consult the same planner.

Fixes:
- pitch_distance moved to deck/dsp.py (cycle-free, pure function);
  harmony and transform both import it from there.
- The except in nearest_harmonic_shift narrowed to (TypeError,
  ValueError): missing key metadata degrades gracefully, infrastructure
  failures die loud.
- New regression gate test_percussion_is_keyless_but_vocals_are_not:
  a tritone vocal at equal tempo must violate; a drum break must not.
- test_budget_knob_bites repaired to be key-neutral; its previous key
  pair only passed because key discipline was dead.

## v0.7.3 - Incremental Crate (2026-07-09)

Diagnosed from a 5,255s / 22-stage run on an enlarged library. The eight
slowest stages (~4,700s, 90% of the run) were all missN_ear_crate and
missN_extract_loops across four gate-miss recovery passes. Root cause was
two-part:

1. Real work, badly amortized. Each miss pass analyzed a fresh ~48-track
   batch; ~15s/track of librosa analysis on full-length pop is genuinely
   ~700s/batch, and the deck-feasibility gate demanded four passes.
2. Full-table re-walks between passes. extract_loops and build_ear_crate
   guarded correctness with per-row skip probes but still SELECTed the
   entire files/loops tables every pass, re-touching every already
   processed row.

Fixes:
- extract_loops: when not forcing, excludes files that already have loops
  at the SQL layer (WHERE f.id NOT IN ...). A fully-extracted library is
  now a no-op select, not a full walk. Per-row skip retained for safety.
- build_ear_crate: when not forcing, excludes loops that already have an
  atom for the profile at the SQL layer. Path-grouped + top-N-by-score
  ordering preserved.
- Default harvest batch 48 -> 96, so large libraries converge in fewer,
  larger analysis passes with less per-pass graph-rebuild and table-walk
  overhead. Override with data.harvest_batch.

Honest note: the analyzer is inherently the floor. On a large library the
first full harvest is still bounded below by ~15s/track. These changes
remove the redundant re-walks and halve the pass count; they do not and
cannot make librosa analysis of new audio free. The cached-rerun path,
however, is now near-instant.

Also confirmed healthy from the same run: the merged organs all fired.
veto:false, quality_gate passed, 142 layers over 23 source tracks, 0
transform_violations, 0 role_leaks, voice_missing:false, 2% silence. The
render succeeded; only its cost was wrong.

ENGINE_VERSION: gt_library_forge_v073. ANALYZER_VERSION unchanged;
existing analysis and atom caches remain valid.

## v0.7.3 rename: earcrate

Jukebreaker GT is renamed earcrate, after the mechanism that defines the
system: only auditioned material exists to the composer. Package dir is
now earcrate/, entry point `python -m earcrate`, single file
dist/earcrate.py, ENGINE_VERSION earcrate_v073. Sketch filenames are
prefixed Earcrate_Sketch. Existing workspaces survive: a legacy
jukebreaker.sqlite is adopted in place, and ANALYZER_VERSION is unchanged
so all analysis caches remain valid. Historical references in this
changelog are left as written.
