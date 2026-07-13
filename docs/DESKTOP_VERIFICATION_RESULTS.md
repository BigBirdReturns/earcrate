# Desktop Verification Results (2026-07-13) — real box, real music

Reply to `docs/AGENT_HANDOFF.json` desktop_verification. Run on the actual desktop:
RTX 4060 + `demucs 4.1.0`, real library `D:\BonkyJones Backups\Music Library` (15,122 tracks),
via headless `python -m earcrate --serve` with `EARCRATE_DEBUG` on.

## HEADLINE
**The happy-path WAV render was reached on real music — the cloud's open item is answered.**
The engine ran the FULL pipeline end-to-end: `config → scan → analyze (96-file batch, 18 cores)
→ extract_loops → build_compatibility_graph → compose+ACCEPT a plan → render`, producing **178.3 s
of real audio, 77 layers, 99.7% active (not silent)**. It then FAILED the **post-render quality
gate** (not readiness, not config). So: pipeline good, **render quality bad**.

## ✅ Confirmed working on real music (previously desktop-blocked)
- Readiness IS reachable on a real library after analyzing ~96 files (no synthetic-refuse).
- Compose → pre-render gates ACCEPT the plan.
- **Demucs is active in the real render:** of 77 layers, **41 used `stem_source="vocals"`** (real
  separated vocal stems from the L3 store), 36 used `mix`. The demucs seam works in production.

## ❌ The real bug: post-render quality gate rejects a FLAT, bass-heavy render
`agent/rejected_renders/earcrate_v0827/EarCrate Set-...-1338.render_report.json`
```
quality_gate.passed = false
failures = ["rms_std_db catastrophically low; render is effectively flat"]
warnings = ["high3000_share below target 0.030; presence repair recommended"]
metrics: rms_std_db=0.72  low200_share=0.66  high3000_share=0.025
         peak=0.38  global_rms=0.045  duration_s=178.3  active_coverage=0.997
layers=77  roles={vocal:41, drum_anchor:12, harmony:12, bass:12}
```
**Interpretation:** the composer stacks all 77 layers ON simultaneously for the whole track →
constant loudness (`rms_std_db` ~0, "effectively flat") and a low-end wall (66% of energy <200 Hz,
only 2.5% >3 kHz). No arrangement dynamics (no bring-ins/drops/breakdowns), no spectral balance.
The gate is correct to reject; the fix is in **arrangement dynamics + mix balance**, not the gate.
- **Next-step hooks:** report also carries `render_failure`, `render_integrity`, `transitions`,
  `drops` (`drop_count`), `deck_model`, `transform_policy` — inspect whether drops/transitions are
  actually being applied or whether every layer is a full-duration bed.

## Config-layer findings (corroborate `config_trap_*`)
1. **`python -m earcrate --serve` did NOT auto-resolve the existing workspace** — `/api/status`
   returned `configured:null` at boot though `EarCrate-Workspace\agent\config.json` exists and the
   dist launcher finds it. Entry-point-dependent pointer resolution → `config_trap_A` reproduced.
2. **`/api/status` reports `configured:null` even after a successful `POST /api/config`** (returned
   `ok:true`, and the render pipeline THEN worked). Status reads a different source than where
   configure writes/where the engine holds `self.config` → a config/status **display inconsistency**:
   a correctly-configured, working app looks unconfigured in the UI.
3. **Debug log is great** — captured request line + full traceback for the `already busy` 500
   (`app.py:5304`) verbatim. (That 500 was my duplicate POST, not a real bug.)

## How to reproduce (headless, no browser)
```
# stop dist UI first (single writer on the workspace DB)
cd repos/earcrate
env -u EARCRATE_STEMS EARCRATE_DEBUG=%CD%\earcrate_debug.log \
  python -u -m earcrate --serve --no-browser --port 8765     # note token in stdout
POST /api/config  {master_root:D:\...\Music Library, working_root/agent_root:C:\...\EarCrate-Workspace\{work,agent}, stem_provider:demucs}
POST /api/one_click_bg  {taste_profile:"girl_talk_v1"}       # "Book a set"
# poll GET /api/status ; outcome persists to agent/runs/<run_id>/report.json .outcome.rejected[]
```

## Recommended cloud follow-ups
- **Render dynamics**: make the arrangement gate layers in/out (verse/chorus/drop structure) instead
  of full-duration stacking, so `rms_std_db` has real variance. Check `drop_count`/`transitions` are
  non-trivial and actually gate layer activity.
- **Mix balance**: high-pass/low-shelf so `low200_share` isn't 0.66; presence lift for `high3000_share`.
- **Config**: (a) make `/api/status` read the live `self.config`/resolved pointer so a configured app
  never shows `configured:null`; (b) unify pointer resolution across `-m` and dist entry points.

---

## UPDATE — re-run after commit `84f825d` (PASS ✅)
Pulled `84f825d "Make renders actually mixable: arrangement dynamics + separated instrumental beds"`,
re-ran on the same real library with `EARCRATE_STEMS=demucs`, Book a set. Analysis was already banked
(features 96 / ear_atoms 1152 / edges 540), so it went straight to compose → render.

**Quality gate PASSED — no failures, no warnings.** Real accepted WAV:
`work/renders/EarCrate Set-earcrate_v0827-736bff5b-1339.wav` (23.6 MB, 178.3 s).

| metric | before (rejected) | after (accepted) | note |
|---|---|---|---|
| rms_std_db | 0.72 | **3.24** | flat → breathing (~4.5×) |
| low200_share | 0.66 | **0.59** | low end more balanced |
| high3000_share | 0.025 | **0.031** | now above 0.030 target (warning cleared) |
| layers | 77 | 75 | |
| stem_source | `{mix:36, vocals:41}` | **`{instrumental:34, vocals:41}`** | beds are now demucs `no_vocals` instrumentals, not full-mix loops |

The fix landed end-to-end on the real box + RTX 4060 demucs path: **acapellas over CLEAN separated
instrumental beds, arranged with dynamics.** The engine's happy path is confirmed solid.

## STILL OPEN — config trap (the last "fresh launch borks" surface)
`python -m earcrate --serve` STILL booted to `/api/status configured:null` and required a manual
`POST /api/config` before Book-a-set would run (the render then worked). So the remaining defect is
purely the **config-resolution/status layer**, not the engine:
- make `/api/status` report the live resolved config (never `null` when the engine holds `self.config`);
- unify pointer write (`visible_app_dir`) vs read (`pointer_search_dirs`) so `-m` and dist agree.

---

## PERSONA BAKE-OFF — real box results (commit `dda8bc0`, `recognizability_bias: "max"` → 92)
Built all three crates on the 96-analyzed-file pool (`/api/ear_crate/build` + `/api/taste/graph` per
persona): each has 1152 approved atoms; edges girl_talk 540 / troubadour 360 / notorious 360.
`/api/taste/readiness` reports **`ready:true` for all three**.

### Only `girl_talk_v1` renders; the other two fail the taste gate
`plan_only:true` bake-off (synchronous, returns per-persona gate outcomes):
| persona | taste_gate | failure | rendered? |
|---|---|---|---|
| girl_talk_v1 | **PASS** | — | ✅ `EarCrate Set-…-e8f419e7-1344.wav` (rms_std_db **3.19**, low200 0.59, high3000 0.020, 49 layers) |
| troubadour_v1 | FAIL | `foreground rail coverage too low (0.61)` | ✗ (swallowed by bakeoff `except`) |
| notorious_v1 | FAIL | `foreground rail coverage too low (0.66)` | ✗ (swallowed by bakeoff `except`) |

### BUG: personas don't differentiate — identical arrangement for all three
Every persona's `score` object is **byte-identical** (`total 43.8719`, `voice_layers 27`, `bed_layers 23`,
`source_tracks 18`, `realized_chaos 0.667` …), and `taste_readiness().have` is identical across personas
(`foreground 683, floor 25, bass 443, spark 222, sources 94`). So `approved_atom_pool(persona)` +
`compose_taste_arrangement` produce the SAME arrangement regardless of persona — the bake-off's premise
("girl_talk's dense collage vs troubadour's key-matched medley vs notorious's one-voice-over-foreign-beds")
is NOT realized. The only thing that varies is the **per-persona gate threshold**, so the one identical
arrangement passes girl_talk's tolerance and fails the other two on foreground coverage.
- **Likely cause:** on a 96-file / 1152-loop pool the per-persona `build_ear_crate` scoring doesn't diverge
  (same loops win every persona), and/or `compose_taste_arrangement` isn't applying the persona's taste
  params to selection. Needs either a bigger/more diverse analyzed pool OR persona-aware composition.
- **`recognizability_bias: max` (→92) is dynamics-negative:** girl_talk at max landed rms_std_db 2.65–2.82
  (one rejected "cave/muffle", one passed with warnings); a neutral one_click hit 3.19. Max recog trades
  arc/presence for familiarity — expose the tradeoff or cap the crank.

### `run_background` discards the bake-off summary (recurring pattern)
`/api/bakeoff` (non-plan_only) is dispatched through `run_background`, which returns `{started:true}` and
**discards `bakeoff()`'s return value** — so the per-persona `results[]` (ok/skip/error/gate) is never
persisted or surfaced; status only shows the last render. Had to use `plan_only` to see WHY personas were
skipped. Same class as the QA "run_background never resets busy / drops return" findings — the bake-off
needs to persist its per-persona outcome (a run-bundle artifact or status field).

### Raw artifacts (committed for the cloud to reason over)
- `docs/desktop_render_reports/girl_talk_v1-PASS-e8f419e7.render_report.json` — full gate metrics +
  every layer's stem_source/transform for the passing render (the numbers behind rms_std_db 3.19).
- `docs/desktop_render_reports/bakeoff_plan_only_maxrecog.json` — the `plan_only` bake-off output:
  three personas, **byte-identical `score` objects**, girl_talk gate PASS vs troubadour/notorious FAIL
  ("foreground rail coverage too low"). This is the raw proof of the persona-non-differentiation bug.
(The rendered WAV itself is not committed — it's a human listening artifact; all machine-actionable
signal is in these two JSONs.)

---

## UPDATE — persona differentiation FIX verified (commit `893d613`)
Pulled `893d613` (+ `40da2de` auto-relocate, `5f59287` machine preset). Re-ran
`/api/bakeoff {plan_only:true}` (persona defaults, no bias). **Personas now compose DISTINCT
arrangements** — no longer byte-identical:

| persona | score.total | bed_layers | source_tracks | taste_gate |
|---|---|---|---|---|
| girl_talk_v1 | 43.87 | 23 | 18 | PASS |
| troubadour_v1 | **38.39** | **5** | **13** | FAIL: floor 0.31 + foreground 0.61 too low |
| notorious_v1 | **38.63** | **17** | **16** | FAIL: foreground 0.66 too low |

(Before the fix: all three were `43.87 / 23 / 18`.) The composition bug is FIXED — troubadour now
builds a sparse key-matched medley (5 beds), notorious sits between. **The remaining gap is
material-depth, not logic:** on the 96-file / 1152-loop pool only girl_talk's dense-collage style
clears its coverage gate; troubadour/notorious's sparser, voice-forward styles leave floor/foreground
coverage short. Fix = analyze a bigger / more taste-diverse slice so each persona finds enough
matching material.

Notes: auto-seed/relocate (`40da2de`/`5f59287`) was a no-op here because the box is already configured
to the C: workspace (the seed only relocates a truly-fresh box); `machine_defaults.json` (D: persist +
S: NVMe cache) is correct for a clean first run. `/api/status configured:null` still reproduces (the
status-source display bug), but the engine was configured and the bake-off ran.
