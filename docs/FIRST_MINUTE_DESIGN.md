# EarCrate — First-Minute Experience & Long-Term Deployment Design

*Grounded in the code as it exists on `main` (v0.8.27). Every claim cites `file:line`. Each item is tagged **[EXISTS]** (wire it up), **[GAP]** (nothing reports this yet), or **[NEW]** (net-new work).*

## 0. What the code actually does today (the ground truth)

Three facts reframe the whole design:

1. **"Book a set" is already the entire pipeline, not just a render.** `bookSet()` (`index.html:280`) → `POST /api/one_click_bg` (`server.py:301`) → `one_click_taste_mix` (`app.py:3300`) runs, in order: `doctor` → `scan` → a **fail-fast harvest loop** that repeatedly `analyze` → `extract_loops` → `build_ear_crate` until `taste_readiness().ready` is true (`app.py:3336-3363`) → `build_compatibility_graph` → `propose_taste_mashup` → `execute_manifest` render. The old "JAM button" the owner remembers is this. The engine for the vertical slice **already exists**.

2. **The first-run landing is a dead end.** `boot()` (`index.html:614`) defaults `S.mode="play"` (`index.html:146`) and calls `renderPlay()`. On a fresh install there is no config, so:
   - `residents()` (`app.py:5004`) calls `taste_readiness()` per persona, which calls `approved_atom_pool()` → `ensure_config()` → **raises `"earcrate is not configured yet"`** (`app.py:970-972`). It's caught per-persona (`app.py:5030`), so cards render with `entry["error"]` and no `readiness_pct` — the user sees personas that say "WANTS MATERIAL" (`verdictOf`, `index.html:196`) with no explanation.
   - The giant glowing **"Book a set"** button (`index.html:249`) has **no config guard**. Clicking it → `one_click_bg` → `run_background` → `one_click_taste_mix` → `ensure_config()` raises → `run_background` catches it into `status.last_error` (`app.py:5225-5227`). The user gets a toast "Booking…", lands on Activity, and sees a red `last_error: earcrate is not configured yet` (`renderStatus`, `index.html:469`). **That is the borked first minute.**

3. **Setup exists but is a flat panel, not a wizard.** `renderSetup()` (`index.html:549`) is reachable only as nav item #06. It shows music/workspace inputs, "Save workspace" (`configure_workspace`), "Scout drives" (`workspace_candidates`), "Run doctor". Nothing detects first-run, nothing validates inline, and `runDoctor()` (`index.html:582`) **itself 500s pre-config** because `doctor()` opens with `ensure_config()` (`app.py:1353`).

The product owner's instinct is exactly right: the app needs to *walk the user through setup, confirm everything is in place, then tell them what the system still needs* — and that "what's still needed" data **already exists** inside `taste_readiness().failures[]`, `doctor().checks[]`, and `preflight()`. It is simply never assembled into one honest panel, and the flow lets users hit the JAM path before any of it is true.

---

## 1. The Readiness Ledger

**One panel, one source of truth.** A vertical checklist of five stages from cold install to "ready to JAM." Each row shows **DONE / IN-PROGRESS / BLOCKED-because-X** and surfaces **exactly one** recommended next action. It replaces the scattered signals (`renderReadiness` bars in Crate `index.html:355`, doctor text in Setup, the refusal wall-of-text in `status.last_error`).

### Ledger rows → real state sources

| # | Ledger row | DONE when… | State source that reports it today | Recommended next action | Tag |
|---|---|---|---|---|---|
| 1 | **Environment OK** | ffmpeg/ffprobe present; roots writable; sqlite intact | `doctor().checks[]` (`app.py:1352-1389`): `{name,ok,detail}` per tool/root + `stem_capability` | "Install FFmpeg" / "Fix folder permissions" (from the failing `detail`) | **[EXISTS]** but see gap ↓ |
| 2 | **Workspace configured** | master/working/agent roots persisted and resolvable | `default_paths().configured` (`app.py:556`): `None` ⇒ not configured | "Choose your music folder" → opens wizard | **[EXISTS]** |
| 3 | **Library scanned** | tracks discovered under master_root | `scan()` result `{total}` (`app.py:1478`); `/api/tracks` count (`list_tracks`) | "Scan library" (`/api/scan_bg`) | **[GAP]** no endpoint returns "N tracks known / N analyzed" as a first-class number; today only inferable from `/api/tracks` length + `_trusted_analyzed_count()` (used privately at `app.py:3338`) |
| 4 | **Atoms extracted & approved** | approved-atom pool meets per-role `need{}` | `taste_readiness()` (`app.py:2815`): `have{foreground,floor,bass,spark,sources}`, `need{}`, `failures[]`, `pool_size` | "Harvest more of your library" / "Approve a hot pool" (`/api/loops/auto_approve_quota`) | **[EXISTS]** — `failures[]` is the row's blocked-reason verbatim |
| 5 | **Compatibility graph built** | typed edges exist over the approved pool | `build_compatibility_graph()` returns `{edges}` (`app.py:2876`); `/api/taste/graph` | "Build pair graph" (`/api/taste/graph`) | **[GAP]** graph is computed on demand and **not persisted as a "ready" flag**; nothing reports "graph is stale vs current pool" |
| 6 | **Ready to JAM** | rows 1-5 green | `taste_readiness().ready` (`app.py:2839`) **and** `preflight().ready` (`app.py:3441-3462`) | Enables the JAM button (see §4) | **[EXISTS]** but two separate calls, never unified |

### What must be built for the ledger

- **[NEW] `GET /api/readiness_ledger`** — one endpoint that assembles all rows without side effects and **without requiring config** (critical: rows 1-2 must render pre-config). It should:
  - Return row 2 from `default_paths()` (already config-optional, `app.py:543`).
  - Return row 1 by running the `doctor()` checks **guarded** — see the doctor fix below.
  - Return rows 3-6 as `state: "blocked", because: "<not configured>"` when `self.config is None`, instead of throwing.
  - For a configured workspace, fold in `taste_readiness()` `have/need/failures` (row 4), a cheap "graph present?" check (row 5), and `ready` (row 6).
- **[NEW] Make `doctor()` config-optional.** Today `doctor()` opens with `ensure_config()` (`app.py:1353`) so it cannot report "FFmpeg missing" before a workspace exists — the one moment you most want it. Split the tool checks (ffmpeg/ffprobe/stem_capability, no config needed) from the root checks (need config). Return the environment half always.
- **[NEW] A persisted "graph freshness" marker** (row 5): store the approved-pool hash the graph was built against so the ledger can say "graph is current" vs "rebuild — pool changed." The building block exists (`build_compatibility_graph` already iterates the approved pool); it just needs a receipt like the run-bundle receipts (`_write_run_artifact`, `app.py:188`).

### UI

Replace the Crate-only `renderReadiness()` (`index.html:355`) with a **persistent left-column ledger** visible on Play and Setup. Reuse its existing per-role bar rendering (`index.html:359-363`) for row 4's sub-meters. Poll it alongside `/api/status` in `pollStatus` (`index.html:603`). Each row: status dot (green/amber/red, reuse the `verdictOf` ink convention `index.html:192-197`), one-line reason, one button.

---

## 2. The First 60 Seconds (vertical slice)

**Goal:** fresh install → the user hears one mashup, with a visible next step at every instant and no dead ends.

The engine already does the heavy lifting (`one_click_taste_mix`, §0.1). The slice is about **gating the JAM path behind the ledger** and **turning refusals into guidance**.

### Storyboard

**Screen 0 — Launch (0-3s).** `serve()` opens the browser to the tokenized URL (`server.py:361,379`). `boot()` must first hit **`GET /api/readiness_ledger`**. If row 2 (configured) is not DONE → route to the **Setup Wizard** (§3), *not* Play. **[NEW]** first-run branch in `boot()` (`index.html:614`); today it always `go(S.mode="play")`.

**Screen 1 — "Where's your music?" (3-15s).** Wizard step 1 (§3). App auto-fills the music folder from `default_paths().music_folder` (`~/Music` or `~`, `app.py:544-547`) and a **scouted workspace** from `workspace_candidates()` (`app.py:607`, already returns a ranked `recommended` with human-readable `reasons`). The user confirms or re-picks via the native dialog (`/api/browse_dir`, `server.py:265`). Live validation runs `doctor()`'s environment half inline. **One button: "Set up my library."**

**Screen 2 — "Getting ready" (15-55s).** This is the harvest, already implemented. On confirm, call `POST /api/config_workspace` (`app.py:761`) then **`/api/one_click_bg`** — but now the user watches the **Activity** screen (`renderStatus`, `index.html:462`) which already renders `status.message` + progress from the harvest loop's granular updates ("harvesting batch 3 (240/585 tracks in)", `app.py:3339`). The Readiness Ledger sits beside it, flipping rows green as `scan → analyze → extract → ear_crate` complete.

**Screen 3 — "Here's your first set" (55-60s).** On success `one_click_taste_mix` sets `status.last_render_path` (`app.py:3418`). **[NEW]** the frontend should auto-load that path into the transport (`setPlayer`, `index.html:590`) and drop the user on Play with the render playing and `∞` endless armed (`toggleEndless`, `index.html:596`). Audio plays.

### Minimum stages that MUST succeed for the slice

From `readiness_need()` (`plan/math.py:49-64`) at the default 120s target, the harvest must reach: **foreground ≥4, floor ≥6, bass ≥3, spark ≥5, sources ≥5** (the floors; base counts scale with target). Below that, `taste_readiness().ready` is false and `one_click_taste_mix` **refuses to render** (`app.py:3364-3372`) — correctly, per README_FIRST "A render writes no WAV unless the complete selected arrangement passes its TasteSpec."

### Where today's flow dead-ends or borks

| Failure point | What happens now | Fix |
|---|---|---|
| No config, user lands on Play | Personas show "WANTS MATERIAL", JAM 500s into `status.last_error` (§0.2) | First-run routes to Wizard; JAM disabled-with-reason (§4) |
| Music folder unreachable at load | `_valid_pointer` requires `master_root` to exist (`app.py:470-471`) → config silently nulls → everything throws "not configured" | §3 self-heal: detect resolvable-config-but-missing-root and say so |
| Library too small to satisfy `need{}` | `one_click_taste_mix` returns a **wall-of-text refusal** in `status.last_error` (`app.py:3367`: "TasteSpec crate refused theater: foreground atoms short: have 2, need 4; …") | Ledger row 4 shows the same `failures[]` as a clean checklist **before** the user JAMs (§1, §4) |
| ffmpeg missing | `doctor` check fails deep inside `one_click_taste_mix` (`app.py:3316-3318`) after the user already committed | Ledger row 1 catches it pre-JAM |

The key structural change: **the ledger front-runs the JAM path so refusals become a checklist the user reads *before* pressing go, not a red error *after*.**

---

## 3. Guided Setup Wizard

Replace the flat `renderSetup()` (`index.html:549`) with a 3-step wizard triggered on first run (row 2 not DONE) and reachable anytime from Setup.

### Step 1 — Music folder (read-only source)
- Prefill from `default_paths().music_folder` (`app.py:544`). "Browse…" → `/api/browse_dir` (`server.py:265`, already wired via `browseInto` `index.html:578`).
- Live: as the user types/picks, validate the folder exists and is a directory (mirror `configure()`'s check `app.py:520`).

### Step 2 — Workspace (all output lives here)
- Auto-scout with `workspace_candidates()` (`app.py:607`) — **already** enforces INV-1 separation (hard-rejects any candidate inside/containing the music folder, `app.py:698-704`), scores fsync latency, free space, sync-managed (OneDrive/Dropbox) penalties, and returns human-readable `reasons[]`. Surface the top candidate + its reasons; let the user expand the ranked list. `scoutDrives()` (`index.html:579`) already calls this but only fills the top path silently — **[NEW]** show the reasons so the choice is legible.
- The default sibling name comes from `sibling_workspace()` (`util.py:117`): `".../<Music> — EarCrate"`.

### Step 3 — Confirm & validate
- On confirm: `POST /api/config_workspace` (`app.py:761`) → `configure()` (`app.py:514`). This derives all five roots via `derive_workspace_paths()` (`app.py:596`), enforces path separation (`app.py:522-531`), creates the layout (`ensure_layout` `app.py:975`), and persists (see trap below).
- Then run the environment doctor inline and show a green/red per-check list (reuse `runDoctor`'s renderer `index.html:582`, fed by the config-optional doctor from §1).
- **Self-heal / explain:** if a write-root check fails, show `detail` (which already includes the OS exception, `app.py:1367`) plus a "Open workspace folder" button (`open_folder`, `app.py:559`, already path-guarded). If ffmpeg is missing, link the bundled `Install-Dependencies.cmd`.

### The config-resolution trap — exact fix

The owner's suspicion is correct and has **two concrete mechanisms** in the code:

**Trap A — the write/read location mismatch.** `configure()` writes the pointer to `visible_app_dir()` (`app.py:539`, `pointer_path = state_dir/earcrate_workspace.json`, `app.py:148`). But `load_config_if_present()` **reads** by scanning `pointer_search_dirs()` (`app.py:448-449`), which depends on *how the process was started* — `__main__.__file__` dir, cwd, then the package dir (`util.py:90-106`). `visible_app_dir()` anchors to the package-holding dir (`util.py:56-59`). These usually agree, but MILESTONES.md:46-47 records the real-library defect: **"Workspace-pointer mismatch between package (`python -m earcrate`) and CLI invocation."** When they disagree, `configure()` succeeds, the pointer is written, but the *next launch via a different entry point* scans a different set of dirs and doesn't find it → `config` stays `None` → every downstream (`doctor`, `taste_readiness`, `one_click`) throws "not configured."

**Trap B — the master-root existence gate.** `_valid_pointer()` returns `None` if `master_root` doesn't exist on disk (`app.py:470-471`). A perfectly-persisted config **silently fails to load** if the music drive is unmounted or the folder was renamed. The UI then reports "not configured" — a lie; it *is* configured, the source just moved.

**What must be persisted and re-read (the contract):**
1. **Pointer:** `{config_json: <abs path>}` at `visible_app_dir()/earcrate_workspace.json` (`app.py:539`) — keep, but **[NEW]** *also* write a copy to every dir in `pointer_search_dirs()` on `configure()` (or, cleaner, make `visible_app_dir()` and the first entry of `pointer_search_dirs()` provably identical and assert it). This closes Trap A deterministically rather than relying on the read-side scan to paper over it.
2. **Config JSON:** `agent_root/config.json` holding `master_root/working_root/agent_root` (+ derived) via `Config.as_dict()` (`app.py:535-536`). Downstream resolution keys off these three (validated at `app.py:468`). Keep.
3. **[NEW] Distinguish "unconfigured" from "configured-but-source-missing."** Change `load_config_if_present()` so that when the pointer + config JSON validate but `master_root` doesn't exist (`app.py:470`), it loads the config into a **degraded state** (config present, flagged `source_missing=True`) instead of discarding it. Then `default_paths().configured` is non-null and the ledger row 2 can say **"Workspace configured — but your music folder isn't reachable: `<path>`. Reconnect the drive or re-point."** rather than dumping the user back to "not configured."

This is the single highest-leverage backend fix for the "setup doesn't persist such that later calls resolve it" class of bug.

---

## 4. JAM / Book-a-Set as the top of the ladder

**Rule: the JAM button is DISABLED-WITH-REASON until the ledger is genuinely green. It must never silently fail.** Today it's an always-live glowing button (`index.html:249`) with no guard — the exact anti-pattern.

### Precondition set (all must hold)
Drawn from the real gates the engine already checks inside `one_click_taste_mix`:

1. **Configured** — `default_paths().configured != null` (`app.py:556`).
2. **Environment OK** — `doctor().ok` (`app.py:1389`), specifically ffmpeg/ffprobe present (`app.py:1355-1356`) and roots writable.
3. **Readiness met** — `taste_readiness().ready == true`, i.e. `failures[]` empty (`app.py:2839`). This is the per-role `have ≥ need` contract from `readiness_need()` (`plan/math.py:49`).
4. **Preflight passes** — `preflight().ready` over the approved *loop* pool (`app.py:3441`); guards the "No approved loops" case (`app.py:3451`).

### Exact microcopy per not-ready reason

Button label + tooltip, keyed to the blocking row (source in parentheses):

| Blocking condition (source) | Button state | Microcopy |
|---|---|---|
| `configured == null` (`app.py:556`) | Disabled | **"Set up your library first"** — click routes to Wizard |
| ffmpeg missing (`doctor` check `app.py:1356`) | Disabled | **"FFmpeg isn't installed — EarCrate can't decode audio yet. Run Install-Dependencies."** |
| root not writable (`doctor` `app.py:1367`) | Disabled | **"Can't write to your workspace: `<detail>`. Open the folder to fix permissions."** |
| `pool_size == 0` (`taste_readiness` `app.py:2839` / residents `app.py:5025`) | Disabled → **"Auditioning"** | **"EarCrate hasn't listened to your library yet. Start — this takes a while the first time (watch the count below)."** (auto-runs the harvest) |
| role shortfall, e.g. `foreground` (`failures[]` `app.py:2834`) | Disabled | **"Need more foreground moments: have 2, need 4. Harvest more of your library or approve a bigger hot pool."** (verbatim from `failures[]`, reworded) |
| `sources` shortfall (`app.py:2834`) | Disabled | **"Only 3 distinct songs are deck-ready; a set needs at least 5. Add more music or scan deeper."** |
| approved-loop pool empty (`preflight` `app.py:3451`) | Disabled | **"No loops approved yet — approve a hot pool in Library → Loop review."** (`/api/loops/auto_approve_quota`) |
| all green | **Enabled, glowing** (`index.html:249` animation) | **"Book a set"** |

### Implementation
- **[NEW]** `bookSet()` (`index.html:280`) reads the cached ledger before firing; if not ready, it does **not** POST — it focuses the blocking ledger row. The disabled visual state replaces the unconditional glow.
- The precondition data is a strict subset of `/api/readiness_ledger` (§1), so the button binds to the same object the ledger renders — no second source of truth.
- **Note:** because `one_click_taste_mix` *itself* auto-harvests to satisfy readiness (`app.py:3336`), for the **never-auditioned** case the button is intentionally a "Start auditioning" affordance (enabled), not a hard block — it's the one precondition the engine will resolve for you. Every *other* not-ready reason is a genuine block.

---

## 5. Long-term deployment

### First-run detection & migration
- **Detection:** `default_paths().configured == null` (`app.py:556`) is the canonical first-run signal — already correct, just unused by the UI. **[NEW]** branch in `boot()`.
- **Migration is already substantial and shippable.** `startup_janitor()` (`app.py:1391`) runs 2s after launch (`server.py:377`) and: purges stale analyzer/engine caches (`app.py:1406-1416`), archives pre-v0.7.4 `" (N)"` duplicate-suffix files (`app.py:1417-1432`), and **adopts legacy Jukebreaker/earcrate workspaces** by re-ingesting their masters (content-hash deduped) and rescuing renders (`app.py:1433-1469`), writing a receipt to `agent/janitor_last.json` (surfaced in `doctor` `app.py:1374-1378`). The DB rename `jukebreaker.sqlite → earcrate.sqlite` is adopted in place (`app.py:1003-1004`), and legacy hidden pointers are adopted on load (`app.py:483-491`). **[NEW]** surface the janitor's `legacy_workspaces[]` findings in the ledger ("Found an older EarCrate library — imported N songs, rescued M mixes; the old folder is safe to delete") instead of burying it in doctor text.

### Self-healing config
- The §3 Trap-A/Trap-B fixes ARE the self-healing story: write the pointer to all legitimate read locations; keep configured-but-source-missing distinguishable so the app guides ("reconnect the drive") instead of resetting to zero.
- **[NEW]** a "Re-point workspace" action in Setup that re-runs `configure()` against a moved music folder without losing the workspace (the DB, analysis cache, and judgments all live under `agent_root`, independent of `master_root`).

### Debug log as the support/telemetry seam
- `EARCRATE_DEBUG` (`server.py:35-76`) is the shippable support seam: opt-in, off by default, thread-safe append-only log of every request's status + elapsed ms, and **full tracebacks on any handler exception** (`server.py:234-235, 341-347`). It redacts the session token from logged paths (`server.py:88-89`). `Debug-EarCrate.cmd` wires the app + a live tail together. **This is exactly the right "what is it doing / where is it failing" feed for a non-technical user to send to support** — no code change needed to ship it; **[NEW]** just add a Setup toggle + "Reveal log file" button (path is at `DEBUG_LOG.path()`, `server.py:61`, already printed at startup `server.py:364`). It is a support seam, **not** telemetry — nothing leaves the machine, consistent with PRODUCT.md rule 4.

### Update path & cross-machine portability
- **Portability is designed in:** `visible_app_dir()` (`util.py:26`) deliberately anchors the pointer to the package-holding dir (portable, next to `START_HERE`), overridable by `EARCRATE_HOME` (`util.py:46-48`), never a hidden AppData nest. The whole workspace (masters copied in, DB, caches, renders, receipts) lives under the user-chosen roots, so moving the folder to another machine and re-pointing works. `EARCRATE_HOME` is treated as an authoritative override, not a hint (`util.py:82-88`) — good for sandboxed/portable installs.
- **Update path:** today it's `.cmd` launchers (`START_HERE.cmd`, `Launch-EarCrate.cmd`) + a single-file build (`build/make_singlefile.py` → `dist/earcrate.py`). The build stamp is content-hashed so a code change visibly changes the header (`server.py:8-23`) — a real "did the update take?" signal. MILESTONES §5 flags the signed installer/updater as **not started** — that's the main deployment gap for non-technical users.

### Non-technical user vs dev — what makes it shippable
- **Already user-safe:** private/local (PRODUCT.md rule 4), network off by default with a single opt-in switch (`index.html:567-569`, AcoustID behind `EARCRATE_ACOUSTID_KEY`), all source-mutating ops are dry-run/apply-guarded and journaled/reversible (Library reorganize Preview→Apply→Rollback, `index.html:485,519-521`), renders quarantined on gate failure rather than shipped (`sessions_list` refusals `app.py:5047-5063`).
- **Still dev-only:** must set up via `.cmd` or `python -m`; no signed installer/auto-update (MILESTONES §5); the first-run flow assumes you'll find Setup (fixed by §2-3); refusals are engineer-worded (fixed by §1, §4 microcopy).

---

## 6. Sequenced build plan

Each step is independently shippable and moves toward the vertical slice. **Cheap = wire up existing code; New = net-new.**

**Step 1 — Config-optional doctor + ledger endpoint (New, ~1 day).** Split `doctor()` (`app.py:1352`) into environment checks (no config) + root checks (config). Add `GET /api/readiness_ledger` that returns rows 1-2 pre-config and folds in `taste_readiness`/`preflight` when configured, never throwing. *Unblocks everything else; also fixes the pre-config `runDoctor` 500 (`index.html:582`).*

**Step 2 — First-run routing + Ledger UI (Cheap, ~1 day).** Branch `boot()` (`index.html:614`) on `configured==null` → Setup. Render the ledger as a persistent column (reuse `renderReadiness` bars `index.html:359`). Poll it in `pollStatus` (`index.html:603`). *Kills the "land on Play, no idea what to do" dead end.*

**Step 3 — JAM disabled-with-reason (Cheap, ~0.5 day).** Bind `bookSet()` (`index.html:280`) and the button (`index.html:249`) to the ledger; render the §4 microcopy; don't POST when blocked. *Kills the silent-500 JAM.*

**Step 4 — Config-resolution trap fixes (New, ~1-2 days).** Trap A: write the pointer to all `pointer_search_dirs()` on `configure()` (`app.py:539`) or assert write/read dirs are identical. Trap B: load configured-but-source-missing into a degraded flagged state in `load_config_if_present()` (`app.py:470-471`) so the ledger guides instead of lying. *The durable reliability fix; without it steps 1-3 intermittently report "not configured" on a configured box.*

**Step 5 — Setup Wizard (New, ~2 days).** Three-step wizard replacing the flat `renderSetup()` (`index.html:549`); surface `workspace_candidates` reasons (`app.py:607`, already computed); inline doctor validation; self-heal buttons. *The "walk them through setup" half of the vision.*

**Step 6 — Slice payoff: auto-play the first render (Cheap, ~0.5 day).** On `one_click` success, auto-`setPlayer(status.last_render_path)` (`index.html:590`, path set at `app.py:3418`) and land on Play with endless armed. *Completes "the user hears one mashup."*

**Step 7 — Graph-freshness receipt + janitor surfacing (New, ~1 day).** Persist the pool-hash the graph was built against (ledger row 5); surface `startup_janitor().legacy_workspaces` (`app.py:1433`) in the ledger. *Long-term deployment polish; lower urgency.*

**Step 8 — Debug-log Setup toggle (Cheap, ~0.5 day).** Toggle + "Reveal log" in Setup, bound to the existing `EARCRATE_DEBUG` seam (`server.py:35-76`). *Support story; no engine change.*

Steps 1-3 alone convert the first minute from "borks with a red error" to "guides you to the one thing that's missing." Steps 4-6 deliver the full unbroken fresh-install-to-audio slice. Steps 7-8 are the deployment hardening.

---

### Appendix — files read
- `earcrate/ui/server.py` (endpoints, `EARCRATE_DEBUG` `_DebugLog`)
- `earcrate/app.py` (`EarcrateCore`: `__init__`, `load_config_if_present`, `configure`, `configure_workspace`, `default_paths`, `workspace_candidates`, `ensure_config`, `doctor`, `startup_janitor`, `taste_readiness`, `preflight`, `build_compatibility_graph`, `one_click_taste_mix`, `residents`, `run_background`)
- `earcrate/ui/static/index.html` (`boot`, `go`, `renderPlay`, `bookSet`, `renderReadiness`, `renderPipe`, `renderStatus`, `renderSetup`, `saveWorkspace`, `runDoctor`, `pollStatus`, NAV/state)
- `earcrate/core/util.py` (`visible_app_dir`, `pointer_search_dirs`, `app_state_dir`, `sibling_workspace`)
- `earcrate/plan/math.py` (`readiness_need`, `sources_needed`, floors/bases)
- `PRODUCT.md`, `README_FIRST.txt`, `MILESTONES.md` (product arc, capability matrix, known real-library defects incl. the pointer mismatch)
