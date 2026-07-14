# EarCrate — Debug Handoff (2026-07-13)

Context dump for a fresh (cloud) session so we can debug the **UI / "Book a set" flow**
without replaying discovery. Everything below was established live on the real box.

---

## TL;DR
- **Demucs stem path is VERIFIED working on the GPU** (real 48 MB stems, provenance, cache-hit). Enabled via `config.stem_provider="demucs"`.
- **"Book a set" fails with TasteSpec errors because the library is SCANNED but NOT ANALYZED** — `features/ear_atoms/loops/judgments` are all 0, so `approved_atom_pool` is empty and `taste_readiness` rejects with `"<role> atoms short: have 0, need N"`.
- The real workspace/config is **correct** (`D:\BonkyJones Backups\Music Library`). Temp/"old-laptop" paths seen in CLI runs are a **config-resolution red herring** from gate-test leftovers (see §5).
- **Uncommitted repo change:** `tests/test_gates.py` (+31/−3) — made the stem gate capability-aware + added a real-receipt branch. Gates: **54/54**. Review in `git diff`; do NOT commit without owner OK.

---

## 1. Environment (this box: `BAM-Desktop`, Win11)
- **GPU:** RTX 4060 8 GB, CUDA on. `torch 2.6.0+cu124`, `torchaudio`, **`demucs 4.1.0`**, `librosa 0.11`, `mutagen`. System **Python 3.13** (NOT a venv — `.cmd` launchers call bare `python`). `ffmpeg`/`ffprobe` on PATH.
- **Workspace (the real one):** `C:\Users\BAM-Desktop\EarCrate-Workspace`
  - pointer: `repos/earcrate/earcrate_workspace.json` → `EarCrate-Workspace\agent\config.json`
  - `master_root = D:\BonkyJones Backups\Music Library` (15,122 audio files, artist-first tree), `stem_provider = "demucs"`
  - DB: `EarCrate-Workspace\agent\earcrate.sqlite`
- **UI currently running:** `python dist\earcrate.py` (single-file build). Launch via desktop `EarCrate` shortcut → `Launch-EarCrate.cmd`.
- **`ArtifactStore` (L3) root:** `EARCRATE_L3_ROOT` env, set by `core.configure` to `<agent_root>/cache/L3`.

## 2. Changes made this session
1. `config.stem_provider` `noop → "demucs"` (workspace config.json, NOT committed to repo).
2. Removed a stray **Windows USER env var `EARCRATE_STEMS=demucs`** that was overriding `config.stem_provider` for *every* render (selection order is `env > config > default`). It broke the noop-fallback gate.
3. `tests/test_gates.py` `test_stem_path_producible`: replaced hardcoded `assert stem_capability()["ready"] is False` (a "no-torch box" assumption) with a capability-consistent assertion + a real-receipt branch that runs Demucs when `ready`. **Uncommitted.**

## 3. Demucs verification (the "4060 receipt")
`DemucsStemProvider.separate(pcm_sha, audio, roles)` → real run produced 4 × ~48 MB WAV stems into L3 with provenance (`provider=demucs, version=htdemucs, tier=ephemeral`), and a 2nd call returned `cached=True`. `stem_capability()` reports `{'torch':True,'demucs':True,'cuda':True,'ready':True}`.
- **GOTCHA:** `ArtifactStore.get(key)` returns a **dict `{"data": bytes, "meta": {...}}`**, NOT raw bytes. (A verify script that did `len(store.get(k))` printed "2" — the dict's key count — and looked like empty 2-byte stems. They were fine on disk.)

## 4. "Book a set" failure — root cause + evidence
**Flow:** UI "Book a set" → `EarcrateCore.one_click_taste_mix` (`app.py` ~3387) → `propose_taste_mashup` (~3010) → `taste_readiness` (~2815).
- `taste_readiness` builds `approved_atom_pool(profile)`; counts atoms by ear-role; compares `have` vs `need`; returns `failures = ["<role> atoms short: have H, need N", ...]`.
- `propose_taste_mashup` raises `RuntimeError("TasteSpec crate is not ready: " + "; ".join(failures))` when `not ready`.
- `one_click_taste_mix` catches that and enters an **auto-harvest loop** (~3392–3405): `analyze(limit=batch)` → `extract_loops` → `build_ear_crate` → retry `propose_taste_mashup`. **It re-raises if a harvest pass analyzes 0 files** (line ~3404: `if int(step.get("analyzed") or 0) == 0: raise last_exc`).

**DB state (live workspace) proving the cause:**
```
files 15122 | tracks 15122 | tags 287336
features 0 | ear_atoms 0 | loops 0 | atom_judgments 0 | compatibility_edges 0 | pair_judgments 0 | mashups 0
```
→ Nothing analyzed → empty atom pool → readiness fails on every role. The set can't compose because there's nothing to compose *from*.

**Cost reality:** `analyze` is ~**60 s/file** (`analysis_seconds` default 180; reads up to 180 s/track). Full-library analyze ≈ 15k × 60 s ≈ **250 h** — infeasible. Intended workflow is to analyze a **curated subset** (a "work-mix"), not the whole library. So "Book a set" auto-harvest on a 15k lib is either very slow or gives up.

## 5. Config-resolution gotcha (why CLI runs saw temp / "old-laptop" paths)
- `load_config_if_present` (`app.py` ~440) scans multiple pointer candidates; `_valid_pointer` skips a config whose `master_root` doesn't exist.
- **Running the gate suite creates dozens/hundreds of throwaway workspaces** at `%LOCALAPPDATA%\Temp\earcrate-gate-*\` (each with its own pointer + `agent/config.json` + `earcrate.sqlite` full of synthetic `music/s0.wav` rows). A stray `python -m earcrate analyze` resolved to one of these and reported `source missing/unreadable ... \Temp\tmp...\music\s0.wav` — **NOT** the real library.
- **There is NO old-laptop (`BAM-Gram`) config anywhere** — searched every pointer on the machine (0 hits). The real config is correct. If the user still sees "full send → old laptop," it is NOT in earcrate code/configs (only an unrelated Gentle/**full-send** render-intensity toggle in CHANGELOG). Need the exact screen/string where they saw it.
- Cleanup done this session: killed a **stray `stem_run.py` GPU process** (leftover bulk-stemmer hogging VRAM, would fight demucs renders) and removed ~30 `earcrate-gate-*` temp dirs (some locked, skipped).

## 6. Open questions — instrument these with logging
1. **Does the auto-harvest `analyze` actually run + succeed on real D: files?** Unverified end-to-end (my CLI test hit the stale temp workspace, §5). Log: which workspace/DB `analyze` resolved, how many rows selected, per-file decode/analyze timings, and the `failed[]` reasons.
2. **What exact `failures[]` does `taste_readiness` return in the UI run?** Surface the full list to the UI + logs (have/need per role), not just "a bunch of errors."
3. **Per-role atom yield after N analyzed files** — how many files must be analyzed before `readiness.ready` flips true for the default profile (`girl_talk_v1`, target_seconds)? Log `readiness_need(...)` vs running `have`.
4. **Batch size + ETA** in the harvest loop — surface progress (count + ETA) so a slow first-set doesn't look like a hang.
5. **"full send"** path source — capture where any non-`D:` output path is written.

## 7. Key code locations (earcrate/app.py)
- `one_click_taste_mix` (Book a set orchestration): ~3340–3439; auto-harvest ~3392–3405.
- `propose_taste_mashup`: ~3010–3055 (raises the TasteSpec errors).
- `taste_readiness`: ~2815–2860 (builds `failures`).
- `analyze`: ~1669; `analyze_one`: ~1851; `ear_crate_file_worker`: module top ~12.
- `preflight` ("No approved loops. Analyze, extract, then approve…"): ~3441.
- `load_config_if_present` / `_valid_pointer`: ~440–480.
- Stem seam: `earcrate/providers/stems.py` (`stem_capability`, `NoopStemProvider`, `DemucsStemProvider`, `_run_demucs`); L3 `earcrate/providers/artifacts.py` (`ArtifactStore.get/put`).

## 8. Broader session context (library the analysis runs on)
`D:\BonkyJones Backups\Music Library` was reorganized this session: 15,148 → artist-first + size-split, comps exploded, 812 dupes quarantined (`_Dupes_Quarantine`), titles cleaned, 63 "NOW Classic Rock" unknowns relabeled from the verified tracklist. Per-track DNA (embeddings/tempo/key/fp) in `D:\BonkyJones Backups\Music DNA\dna.sqlite`. Scripts + fpcalc in `Music DNA\_relabel_tools\`. This is the `master_root` earcrate scans/analyzes.
