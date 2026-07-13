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
