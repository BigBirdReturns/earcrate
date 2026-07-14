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

---

## PERFORMANCE — before vs after the perf commits (`4ef1829` model-cache, `16a59ed` scaling+NVMe)
Measured render wall-clock on the real box (analysis banked; compose fast). Render is **GPU-separation-
bound**: each cache MISS = one ~6 s demucs separation; cache HITS (from S: NVMe) are near-free.

| render | commit era | cache | duration | misses | hits | disk_hits |
|---|---|---|---|---|---|---|
| `736bff5b` | pre (84f825d) | cold | 115 s | 21 | 54 | 18 |
| `d6001286` | pre (dda8bc0) | warm | 19 s | 5 | 45 | 20 |
| `e8f419e7` | pre (dda8bc0) | warm | 34 s | 1 | 48 | 23 |
| `c6299aaa` | **post** (893d613) | cold | 170 s | 25 | 49 | 0 |
| `28bd6c49` | **post** (893d613) | warm | 48 s | 4 | 71 | 25 |

**Honest read — the perf commits did NOT produce a measured render speedup:**
- Per-separation cost is ~5.5 s (pre) vs ~6.8 s (post) — same ballpark; a cold render is
  separation-bound (~6 s × N misses). `4ef1829` model-caching can't move a single render: if the model
  were truly reloaded per separation, the 21-sep cold render would've been ~300 s, not 115 s — so it
  wasn't the bottleneck.
- **The real lever is the content cache** (stems reused by pcm_sha), which pre-dates these commits:
  cold ~170 s (25 sep) → warm ~48 s (4 sep + 25 disk_hits) ≈ **3.5×**. The commits' actual contribution
  is *where* the cache lives — now **1.7 GB on S: NVMe** instead of C: — plus not littering C: and a
  marginal model-reload skip across renders in a long-lived process.
- **To speed up the FIRST (cold) render, target the demucs separation itself** (GPU batching, a smaller
  model, or 2-stem `--two-stems=vocals`), not caching — caching already handles repeat material.

## Workspace relocated to D: (manual — auto-relocate didn't fire on `-m`)
Moved `EarCrate-Workspace` off C: → `D:\BonkyJones Backups\EarCrate-Workspace` (DB/atoms/edges/features
+ runs + renders intact: 3 crates ×1152, 1260 edges, 96 features); C: workspace deleted; hot cache on
`S:\earcrate_cache` (1.7 GB). **Finding:** the auto-seed/relocate (`40da2de`/`5f59287`) did NOT fire via
`python -m earcrate --serve` even unconfigured with `EARCRATE_DEFAULTS` set — `machine_defaults.json` is
resolved via `visible_app_dir()`, which for `-m` isn't the repo root (same `-m`-vs-dist gap as the
config-pointer trap). So "git pull + run just works" holds for the **dist launcher** only; the `-m` path
needs the seed to key off `EARCRATE_DEFAULTS`/CWD, not `visible_app_dir()`.

---

## VERIFY `32aa81f` + `c711c52` on real box (clean `-m` launch, D: workspace)
- **Config-trap FIXED (`32aa81f`) + status-truth FIXED (`c711c52`):** a CLEAN `python -m earcrate --serve`
  (no `EARCRATE_DEFAULTS`, no manual `/api/config`) now auto-configures to the D: workspace and
  `/api/status` reports `configured: True`, `master_root: D:\…\Music Library`. "git pull + run just
  works" now holds on the `-m` path.
- **Persona coverage (`c711c52`) — partial:** after force-rebuilding crates+graphs with the new code,
  plan_only bake-off:
  | persona | gate | bed_layers | failure |
  |---|---|---|---|
  | girl_talk_v1 | ✅ PASS | 23 | — |
  | troubadour_v1 | ✗ | 8 | floor 0.50 + foreground 0.56 too low |
  | notorious_v1 | ✗ | 19 | foreground 0.61 too low |
  girl_talk passes; troubadour/notorious still miss coverage on the **96-file / 1152-loop pool**. Their
  sparse/voice-forward styles need more foreground+floor material than 96 files provide — **material-
  depth, confirmed** (analyze a bigger/broader slice). The coverage change helped but didn't clear them
  at this pool size.
- **Workflow finding:** a `git pull` that changes the composer requires **rebuilding crates+graphs**
  (`/api/ear_crate/build?force` + `/api/taste/graph`). On stale atoms, girl_talk falsely FAILED coverage
  (0.47); after rebuild it PASSED. Consider a stale-crate warning / auto-rebuild on engine-version bump.
- Not exercised here (plan_only = no render): the demucs speed knob and bake-off persistence from
  `c711c52` — need a real render / non-plan_only bake-off to verify.

---

## BIGGER POOL (896 files / 10,704 loops) + recurrence scoring (`aedb8ce`) — coverage still short
Ingested 800 more songs (CPU, 18 cores, ~26 min; GPU idle — analyze is librosa, not demucs),
extract_loops (1,152 → 10,704), force-rebuilt all 3 crates (**9,552 atoms each**) + graphs, plan_only
bake-off. Build-time note: **cold `ear_crate/build` on ~10.7k loops = ~847 s; warm rebuilds ~85–95 s.**

| persona | gate | total | bed | src tracks | foreground cov (prev → now) |
|---|---|---|---|---|---|
| girl_talk_v1 | ✅ PASS | 44.13 | 15 | 18 → **35** | passes |
| troubadour_v1 | ✗ FAIL | 41.33 | 7 | 17 | 0.56 → **0.68** (floor 0.47) |
| notorious_v1 | ✗ FAIL | 40.84 | 15 | 18 | 0.61 → **0.63** |

**Verdict: material-depth was PARTIALLY right, not the whole story.** 9× more material lifted troubadour
foreground coverage 0.56→0.68 and girl_talk source diversity 18→35 — real, measurable — but
troubadour/notorious STILL miss their gates. So it's ALSO **gate calibration**: troubadour's sparse
key-matched medley and notorious's one-voice-over-beds *structurally* carry more air than girl_talk's
dense collage, so a girl_talk-tuned coverage floor is likely wrong for them. Diminishing returns
(0.56→0.68 from 9× data, still short) suggest **persona-aware coverage thresholds** are the bigger lever
than yet more material. Recommendation to cloud: per-persona coverage floors (a medley ≠ a wall), or
far more taste-targeted ingest.

---

## GROUND TRUTH — real Girl Talk vs earcrate output (the gate is badly miscalibrated)
The library has **93 real Girl Talk tracks** (`Artists/Girl Talk/`). Measured earcrate's own gate metrics
(`rms_std_db`, `low200_share`, `high3000_share`, replicated in librosa) over 24 of them vs earcrate's
PASSING girl_talk render:

| metric | REAL Girl Talk (24 trk, mean [min–max]) | earcrate render (PASSES) | gate floor |
|---|---|---|---|
| rms_std_db (dynamics) | **5.31** [3.23–7.62] | 3.19 | ≥3.0 |
| low200_share (bass) | **0.20** [0.07–0.31] | **0.59** | *(none)* |
| high3000_share (presence) | **0.31** [0.19–0.53] | **0.031** | ≥0.030 |

**earcrate's "passing" render does NOT sound like Girl Talk — it clears floors set 3–10× too low.**
- **Presence:** real 0.31 vs earcrate 0.031 → **10× less treble/air**; earcrate passes only because the
  floor is 0.030. This is why the "presence repair recommended" warning fires yet the render still passes.
- **Bass:** real 0.20 vs earcrate 0.59 → earcrate is a **low-end mud wall**, and there is **no low200
  ceiling** in the gate to catch it.
- **Dynamics:** real 5.31 vs earcrate 3.19 → earcrate squeaks over the 3.0 floor.

**Actionable for cloud:** recalibrate the quality gate to the ground-truth Girl Talk distribution —
`rms_std_db` target ~5 (floor ~3.5), **add a `low200_share` CEILING ~0.30**, `high3000_share` target ~0.30
(floor ~0.15). And fix the mix itself (it's bass-heavy + treble-dead vs real GT): high-pass/low-shelf the
instrumental beds + a presence lift. The real albums are a ready-made validation set — a good render
should land inside the real-GT metric ranges, not just above today's floors.
(Caveat: metric formulas replicated in librosa; earcrate's exact defs may differ, but a 10× gap ≠ noise.)

### All-persona ground truth (real reference artists from the library)
Same metrics over each persona's REAL reference material (40 tracks/group):

| persona (real reference) | rms_std_db | low200_share | high3000_share |
|---|---|---|---|
| girl_talk (Girl Talk) | 5.21 [3.2–7.9] | 0.19 [0.02–0.32] | 0.31 [0.16–0.53] |
| troubadour (Bright Eyes, Elliott Smith, Iron & Wine, Sufjan, Sun Kil Moon) | 4.78 [2.9–8.3] | 0.21 [0.11–0.40] | 0.23 [0.06–0.35] |
| notorious (Wale, Kanye West, Wu-Tang) | 5.16 [3.3–7.0] | 0.18 [0.07–0.34] | 0.33 [0.17–0.55] |
| **earcrate render (PASSES)** | **3.19** | **0.59** | **0.031** |

**Findings:**
- **The three real personas share ~one quality box** (dynamics ~5, bass ~0.19, presence ~0.23–0.33) →
  the quality gate should be **uniform, not persona-specific**. Real singer-songwriter / hip-hop are as
  dynamic + present as Girl Talk; the persona difference is arrangement/coverage, not spectral quality.
  (troubadour presence 0.23 is slightly lower — acoustic warmth — but still ~7× earcrate's 0.031.)
- **earcrate's mix fails ALL THREE references universally** — 3× too bassy, ~10× too dull, under-dynamic.
  Not a per-persona problem: a bass-wall / no-treble MIX problem.
- **Recommendation (revised):** ONE real-calibrated quality gate for all personas — `rms_std_db` ≳4
  (real ~5), a `low200_share` **CEILING ~0.30** (real ~0.19; earcrate 0.59 must fail), `high3000_share`
  ≳0.20 (real ~0.25–0.30; earcrate 0.031 must fail). Fix the render mix: high-pass/low-shelf + presence
  lift so output lands INSIDE the real-reference box. The library's real artists are per-persona
  validation sets (Girl Talk / SS-writers / hip-hop).

### CORRECTION — "notorious" = *Notorious XX* (Wait What), a MASHUP, not raw hip-hop
The persona is named after the **Notorious XX** mixtape (`Artists/Wait What`, 11 trk — Biggie acapellas
over **The xx** instrumentals), NOT generic rap. Re-measured against the real thing:

| persona (corrected real ref) | rms_std_db | low200 (bass) | high3000 (presence) | character |
|---|---|---|---|---|
| girl_talk (Girl Talk) | 5.21 | 0.19 | **0.31** | brightest |
| troubadour (SS-writers) | 4.78 | 0.21 | 0.23 | mid |
| **notorious (Notorious XX / Wait What)** | 4.59 | **0.27** | **0.19** | **darkest** (The xx beds) |
| *(source: The xx)* | 5.64 | 0.34 | 0.14 | — |
| earcrate render (PASSES) | 3.19 | **0.59** | **0.031** | broken |

**Revised finding (supersedes the "one uniform box" claim above):** there IS a per-persona **spectral
gradient** — girl_talk bright (presence 0.31) → notorious/XX dark (0.19), because notorious rides
minimal warm The-xx beds. So the quality gate wants *some* per-persona spectral tolerance, not one
threshold. **BUT earcrate's render is outside EVERY real target:** its bass 0.59 exceeds even The xx
(0.34, the bassiest ref); its presence 0.031 is below even The xx (0.14, the dullest ref). So the
mix-bug conclusion is unchanged and stronger. Practical gate: bass **ceiling ~0.35**, presence **floor
~0.15**, dynamics ≳3.5 (catches earcrate on all three axes), optionally tightened per-persona toward
each reference's mean. (User also flagged **Branchez** as another mashup reference — not found as an
Artists folder; likely in Singles/comps, TODO to locate.)

### Authoritative persona definitions (from `profiles/*.json`) — corrects 2 earlier calls
The TasteSpecs define each persona as a distinct MASHUP AESTHETIC (not a genre):

| persona | contract | event/turnover | layers | coverage floor / fg | real sonic (measured) |
|---|---|---|---|---|---|
| **girl_talk** | recognizable foreground + stable floor + FAST turnover | ~11 s / 5.5 songs·min, no vox >20% | 2–4 | 0.70 / 0.50 | rms 5.2, bass 0.19, pres 0.31 (bright) |
| **troubadour** | long **key-matched MEDLEY**: one persistent bed, sequential hooks, minimal layering | ~22 s / 2.7·min, runs to 45 s | **1–2** | **0.95** / 0.75 | (ref TBD — a medley bootleg, NOT folk) |
| **notorious** | one voice over another era's beds (whole verses) | ~16 s / 1.5·min, runs to **60 s** | 2–3 | 0.90 / 0.80, intelligibility 0.6 | rms 4.6, bass 0.27, pres 0.19 (dark, The-xx beds) |

**CORRECTIONS to my earlier notes:**
1. **troubadour is a key-matched MEDLEY mashup, NOT singer-songwriter** — my earlier troubadour
   ground-truth (Bright Eyes/Elliott Smith/…) measured the WRONG reference material. Its real
   reference is a continuous one-bed medley bootleg (user to name it; I'll re-measure).
2. **Coverage floors are DELIBERATELY per-persona** (collage 0.70 < notorious 0.90 < medley 0.95),
   so my "coverage gate is miscalibrated / too strict" was WRONG. troubadour SHOULD demand ~total
   coverage (a medley has no gaps); it fails because the composer can't sustain a continuous bed from
   disjoint atoms — a **composition** gap, not a gate-number bug. The genuinely-broken gate is the
   **spectral quality** one (§ ground-truth: earcrate bass-heavy/treble-dead vs every reference).

### OWNER'S INTENT — the actual mashup GRAMMAR of each persona (definitive)
Per the owner, the personas are three distinct mashup *constructions*, not just density settings:
- **girl_talk** — a dense, fast-turnover COLLAGE: many recognizable hooks stacked and swapped on a grid.
- **troubadour** — a **chain of two-song PAIRINGS**. Each unit = two songs that *wouldn't* naturally go
  together but share an **earworm chord progression** (the universal I–V–vi–IV-type magic), mashed on
  that shared harmony; then pairings are strung one after another. "Hook-mashup-sync." The engine
  primitive is a **chord-progression-matched surprising PAIR**, sequenced — NOT a genre, NOT folk.
  (Implication: troubadour's compatibility graph should score edges on shared chord progression /
  earworm-hook match between *unlike* sources, and the arrangement is a sequence of such pair-units.)
- **notorious** — **exactly TWO FULL ALBUMS paired 1:1**: one artist's acapellas across one other
  artist's complete instrumental album, start to finish (Notorious B.I.G. × The xx = *Notorious XX*).
  Not "a voice over assorted beds" — a whole-album marriage. (Implication: notorious wants a single
  dominant vocal *source-album* riding a single instrumental *source-album* for the whole set, i.e.
  very low source turnover on BOTH rails — which matches its 40–60 s runs but is even stronger: it's
  album-locked, not just verse-locked.)
This reframes troubadour/notorious as **source-pairing constraints** the composer must honor, above the
per-atom scoring — a level the current graph (atom-pairwise) may not express.

### Gate-off preview renders (all 3 personas, real engine+demucs, big pool)
Rendered one labeled preview per persona (pre/post gates bypassed so failing personas produce audio;
NOT committed, NOT product renders). Real (pre-bypass) gate metrics:

| preview | real gate | rms_std_db | low200 | high3000 |
|---|---|---|---|---|
| girl_talk | PASS | 3.24 | 0.44 | 0.047 |
| troubadour | FAIL (coverage) | **5.00** | **0.17** | 0.051 |
| notorious | PASS | 2.85 | 0.57 | 0.049 |

Audible findings: (1) **presence ~0.05 across ALL three vs real ~0.20–0.31** — the treble-dead mix bug
is universal and clearly hearable. (2) **troubadour has the BEST spectral match to real mashups**
(rms 5.0, bass 0.17) yet FAILS purely on coverage — its minimal layering can't sustain the 0.95 medley
bed. So troubadour's gap is STRUCTURAL (can't build the chord-matched pairing chain), not mix — the
clearest evidence of the atom-engine-vs-source-pairing-grammar gap. WAVs local only
(work/previews/PREVIEW_<persona>.wav).

### WHY THE PREVIEWS SOUND ALIKE (owner's ears, confirmed in arrangement data)
Analyzed the 3 preview render reports by `section_index`:
1. **Identical intro:** section 0 uses the SAME 2 source tracks for all three personas (2/2 shared);
   sections 1+ share 0 sources. The composer picks a persona-agnostic opening anchor -> same first ~15-30s.
2. **girl_talk ≈ notorious are the SAME arrangement:** identical per-section density curve
   `[2,3,2,3,2,3,3,3,2,3,2,3,3,2,2]`, identical 38 layers, identical role mix (15 drum / 23 vocal).
   They differ ONLY in which source tracks were selected. notorious is SUPPOSED to be sparse (1.5
   songs/min, one voice over one bed for 60s) but composes girl_talk's dense fast-turnover collage.
   => the per-persona `density_model`/`source_turnover` params are NOT reaching the composer's STRUCTURE;
   personas differentiate at atom SELECTION (893d613) but not at arrangement STRUCTURE. **Composer bug.**
3. troubadour is the only structurally-distinct one (flat 2-layer density) -> reads as "different".
"Diverges later" = 0%-shared source picks accumulate audibly over time, but GT/notorious structure never
actually diverges (sample drift, not structural difference). Fix: make density/turnover/foreground-share
per-persona params drive the arrangement builder, and differentiate the opening anchor per persona.

---

## External-target remix (#4, cloud a2490ba) — box end-to-end verification (2026-07-14)

Test: dropped an OUT-of-library-style vocal (Bill Withers "Ain't No Sunshine" -> demucs htdemucs
`vocals.wav`, 5s on the 4060) into `propose_external_remix` + `execute_manifest(apply=True)`,
girl_talk_v1, target 60s. Called in-process (see finding 1). Verdict: **design is right, but it
produces no output today for two independent reasons.**

**GOOD — anchor-inversion is correctly implemented.** Arrangement placed **16 external vocal windows**
(`role=vocal`, `is_external=true`, no rate/pitch transform = identity) over **11 conforming bed atoms**
(drum_anchor x8, harmony x2, bass x1). Feasibility passed: "bed OK: 12 floor, 85 bass, 78 spark across
51 sources at 156.6 BPM key 7." The vocal is the boss and only the bed bends — exactly as designed.

**FINDING 1 (wiring): `POST /api/remix/external` is not registered.** `propose_external_remix` + the
`remix/external` helpers exist and the docstring promises the route, but no HTTP handler dispatches to it
— the endpoint 404s. Only callable in-process. Wire the handler.

**FINDING 2 (correctness, high value): the anchor is confidently WRONG on an acapella.** "Ain't No
Sunshine" is ~78 BPM / A-minor. The engine anchored the whole render to **156.6 BPM (a clean 2x octave
error) at bpm_confidence 0.93**, and **key_root 7 (G) at key_confidence 0.17** (essentially a guess,
key_mode 0). The bed then dutifully conforms to the wrong tempo AND wrong key. `remix_anchor`'s
garbage-guard only catches absent/extreme tempo, not a high-confidence octave error, and there is no
key-confidence floor. Acapellas — the canonical thing a user drops here — are the worst-case input for
tempo/key estimation (no percussion, sparse harmony). Fix: fold BPM into a sane band (test half/double
against a target range) and add a key-confidence floor (below ~0.3 don't hard-pin the key; keep the bed's
own compatible key or derive from vocal chroma more robustly). This is the highest-leverage fix for #4.

**FINDING 3 (contract): gate rejection is SILENT to the caller.** The render was produced (61.3s, 26
layers) then rejected by `post_render_quality_gate`. But `execute_manifest` returned `ok:true`, the op
logged `status:"done"`, and the only signal is `done[].type == "render_rejected"` (path=null,
presented=false). A caller — and the future HTTP route — gets success + no audio + no reason. Same class
as the earlier dinner-run "0 tracks, no error." Propagate rejection as a distinct non-ok status/error
carrying the failure so the UI can say "rejected: presence too dark," not silently succeed.

**CONFIRMED — the post-render gate calibration works.** It caught this render correctly:
`high3000_share 0.068` -> "catastrophically dark (real Girl Talk ~0.31); presence is dead" (FAIL);
`rms_std_db 2.26` vs target ~5.0 (WARN, dynamics too flat); low200_share 0.279. Those targets are the
real-Girl-Talk ground truth measured earlier — the gate is now honest. But the RENDERER is still
treble-dead + dynamically flat, so external remix inherits the same mix defect and will keep getting
rejected until the render EQ/dynamics are fixed. Net: #4 is structurally sound but yields zero output
today = wrong anchor (F2) x treble-dead render (F below the calibrated gate).
