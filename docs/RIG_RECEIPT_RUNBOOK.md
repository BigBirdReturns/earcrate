# Rig-receipt runbook

The rig receipt is the one command that turns *"engineered on the branch / green
in cloud CI"* into *"validated on the Windows box against the real library."* It
is **verification tooling only** — it changes nothing about the engine, compiler,
renderer, personas, UI behavior, defaults, or feature flags. It reads, runs
subprocesses, and writes under a scratch directory (plus two receipt files you
choose to commit).

Control question it answers: **can the owner run one command, interrupt and
resume it safely, and get a receipt that makes it impossible to confuse code
presence, cloud validation, rig execution, and a human musical verdict?**

## Run it

```
Run-Rig-Receipt.cmd ^
  --workspace "D:\EarCrate" ^
  --scratch "D:\EarCrate-Rig-Receipt\2026-07-16" ^
  --profile remix_prettylights_v1 ^
  --real-seconds 120 ^
  --piano-iterations 3
```

(`python scripts/run_rig_receipt.py --workspace … --scratch …` on any platform.)

- `--workspace` — your configured EarCrate workspace/home. Treated as a
  **read-only source** of the real library + durable state. The harness clones
  the analysis DB into scratch and runs the stateful stages there, so your
  production workspace is never polluted with receipt projects and your **music
  library is never written**.
- `--scratch` — an explicit directory, **outside the music library**, for every
  output (logs, cloned workspace, renders, screenshots, receipts). Required.
- `--profile`, `--real-seconds`, `--piano-iterations` — the real-library
  compile persona, its length, and the piano iteration cap.
- Manual verdicts (optional; omit them and the stage records `pending_manual`):
  `--verdict-real-render keep|reject`, `--verdict-rubberband default|rubberband|tie`,
  `--verdict-techno keep|reject`, and `--external-vocal <path>` for the techno proof.
- `--chromium <path>` or `EARCRATE_CHROMIUM` — explicit Chromium for the DOM
  stage (otherwise Playwright's own browser is used).

## Preflight (refuses rather than guesses)

- **Refuses a dirty git tree** by default (`--allow-dirty` records the dirt).
- Records branch, HEAD, upstream, Python (+ executable path), OS, CPU, RAM, GPU,
  CUDA, and — as **executable path + version string, not a bare boolean** —
  ffmpeg, ffprobe, Rubber Band, plus versions for allin1, pyrubberband, Demucs,
  Playwright, torch, numpy/scipy/librosa/soundfile/mutagen, and the configured
  provider env (`EARCRATE_BEATS/TRANSFORM/RANKER/STEMS`).
- **Resolves the configured `master_root` (the real music library) first, then
  runs the scratch-safety check against it.** If `master_root` cannot be
  resolved from `--workspace`, the run **refuses** (exit 1) — the safety check
  cannot run against an unknown music root, so no crate-dependent stage may
  proceed. Run `earcrate configure --music <folder>` in that workspace first.
- **Requires an explicit scratch outside the music library** (refuses scratch ==
  music, scratch ⊂ music, or music ⊂ scratch).
- The durable-state clone uses a **consistent SQLite backup** (`Connection.backup`
  + `PRAGMA integrity_check`), never a raw file copy of a possibly-live DB; the
  production workspace is never written and the real music stays read-only.
- Does **not** install packages, does **not** persist env vars or config, does
  **not** push/merge/close PRs, delete outputs, or alter source audio.
- Redacts user-home and token-bearing paths in the committable receipt.

## Stages (in order)

| # | stage | tier | required | notes |
|---|---|---|---|---|
| 1 | gate suite | rig mechanical | yes | discovers the real gate count (never hardcodes 194/201) |
| 2 | VERIFY_PACKAGE + build single-file | rig mechanical | yes | records the built `dist/earcrate.py` SHA-256 |
| 3 | Workbench DOM lifecycle (package + single-file) | rig mechanical | yes | zero console errors required, both modes |
| 4 | project acceptance (scratch) | rig mechanical | yes | self-contained scratch workspace |
| 5 | compile + render a real-library project | real library | yes | records project/revision/score/render/report/EDL/RPP/sheet + hashes |
| 6 | human keep/reject on the real render | human listening | yes | `pending_manual` until a verdict; gate success is NEVER inferred as a keep |
| 7 | edit → render → undo → PCM identity → redo → restart | real library | yes | proves undo restores prior decoded-PCM identity + edited head reopens |
| 8 | ranker training + off/on order | real library | yes | insufficient/one-class data → honest `skipped_insufficient_data`, not a pass |
| 9 | bounded piano session | real library | yes | records attempted/kept/discarded/errored/stop_reason within the iteration cap |
| 10 | allin1 before/after on real tracks | gpu/provider | no | probes the real model; if absent → incomplete + exact install/rerun command; never claims the stub as validation |
| 11 | Rubber Band A/B render + listening verdict | gpu/provider | no | child-process `EARCRATE_TRANSFORM` override only; never flips the default or bumps `ENGINE_VERSION` |
| 12 | techno external-vocal proof + verdict | human listening | no | needs `--external-vocal`; the copyrighted source is never copied into the repo/receipt |

## Status + exit-code semantics

- Every stage status is exactly one of `passed`, `failed`, `skipped`,
  `pending_manual`.
- Overall is `complete`, `failed`, or `incomplete`.
- **Exit 0** only when every stage is `passed` (all required mechanical stages
  passed **and** every required manual verdict recorded).
- **Exit 1** on any mechanical failure.
- **Exit 2** (distinct) when mechanically green but a stage is `skipped` or
  `pending_manual` — awaiting a dependency (e.g. allin1) or a human verdict.
- `skipped` and `pending_manual` are **never** converted into success.

A first pass on a box without allin1 and without listening verdicts is expected
to be **incomplete / exit 2** — that is correct, not a failure.

## Resume

State is checkpointed **atomically after every stage** (`<scratch>/receipt/<run_id>/state.json`,
written temp + `os.replace`), so Ctrl+C or power loss loses no completed work.
Re-run with the **same `--run-id`** to resume: only `passed` stages are skipped;
`failed` / `skipped` / `pending_manual` stages re-run so a resume picks up a
now-installed dependency (allin1, Rubber Band) or a now-provided verdict. The run
id defaults to `rig_<head12>_<scratchhash6>`; pass `--run-id` to pin it.

**HEAD guard:** the receipt records the exact git HEAD, and the harness refuses
to append results from a different HEAD to an existing run (use a fresh
`--run-id`). This keeps a receipt honest about which commit it validated.

## What to commit

Commit only:
- `<scratch>/receipt/<run_id>/receipt.json` — the redacted committable receipt
- `<scratch>/receipt/<run_id>/receipt.md` — the readable summary
- the log/hash ledger (inside `receipt.json`)
- any deliberately selected screenshots

Large audio and browser artifacts stay under scratch and are **not** committed.

## Evidence tiers (why the Markdown is worded the way it is)

The receipt keeps five kinds of evidence strictly separate so *code present* can
never read as *milestone complete*:

1. **cloud CI** — not in this receipt at all (the 194/201-style numbers were a
   cloud-CI figure).
2. **rig mechanical** — stages 1–4.
3. **real library** — stages 5, 7, 8, 9.
4. **gpu/provider** — stages 10, 11.
5. **human listening** — stages 6, 11, 12.

A green mechanical run does **not** assert a musical verdict, and an outstanding
allin1/Rubber Band/listening stage keeps the overall status `incomplete`. Only
after a genuine `complete` (or a deliberate owner sign-off on the outstanding
tiers) does v0.9 go to `main`.
