# EarCrate UI — Lead Engineer Briefing

## 1. Verdict
The UI is **structurally wired but functionally hollow on the paths that matter**: navigation, config persistence, audio security, and the dry-run/apply gating are genuinely solid, but almost every *payoff* action either renders the wrong thing, silently swallows its result, or wedges the engine. "Book a Set" does not reliably book because a config-resolution trap in Setup can orphan the entire analyzed library, leaving `taste_readiness` permanently short — and when it refuses, the diagnostic is thrown away before it reaches the screen. Two independent controls (Identify, Deep-clean) can hard-lock the engine `busy` forever, and the app's single liveness indicator lies green on failure, so the whole thing "borks silently."

---

## 2. Blockers first (by user journey)

### SETUP — the config-resolution trap (root cause of the live symptom)
**HIGH — Re-saving the workspace nests it one level deeper each save, orphaning DB / analysis cache / manifests / judgments**
`index.html:552,560,580` → `server.py:262` → `app.py:761 configure_workspace` / `app.py:596 derive_workspace_paths`
- **Breaks:** First save is correct. But `renderSetup` re-populates the Workspace field from `cfg.working_root` (the `.../work` *subdir*), not the folder the user picked (`const ws=cfg.working_root||d.workspace_folder`, `index.html:552`). Any return to Setup — including `toggleWorkers`, which calls `renderSetup` — shows `.../work`. Saving again derives `working_root=.../work/work`, `agent_root=.../work/agent`. The separation check (`app.py:522`) only compares master-vs-working, so it passes silently and builds a fresh empty tree. The old `agent_root` (SQLite DB, `.npz` cache, manifests, loop/atom judgments) is **orphaned**; the app now points at nothing.
- **Why:** `Config.as_dict()` (`core/config.py:21-33`) persists no field for the parent workspace folder, so the round-trip re-derives from a subdir.
- **Fix:** Persist the chosen workspace **root** (add `workspace_root` to `Config.as_dict`/`default_paths`), bind the field to that, and make `configure_workspace` idempotent (if `workspace_folder` already ends in `/work` or contains `agent/`+`work/`, treat it as an existing root, don't re-derive).

### ANALYZE / CRATE feed
**HIGH — Ingest module is a dead control: `/api/ingest` always errors ("no source folders given")**
`index.html:483` → `libAction (index.html:546)` → `server.py:253-254` → `ingest.py:122-130`
- **Breaks:** The Ingest card ("Copy from any drive") has Dry-run / Ingest-now buttons but **no source-folder input anywhere**. `ingest_sources` reads `data.get('sources') or []`, finds it empty, returns `{ok:false,error:'no source folders given'}` on every click. The advertised primary way to feed the library is non-functional. (Scan from Setup is a separate, working feed path.)
- **Fix:** Add a source-folder input (text + `/api/browse_dir`) and pass `sources:[...]`; disable the buttons until a folder is set.

### READINESS gate
**HIGH — Preflight almost always reports "READY to render" — frontend reads `j.failures`, backend returns `j.warnings`/`j.ready`**
`index.html:390` vs `app.py:3441-3462` and `readiness.py:134-150`
- **Breaks:** `preflight()` computes `ok = j.ok!==false && !(j.failures && j.failures.length)`. But `core.preflight()` hard-sets `audit["ok"]=True` (`app.py:3459`) for any non-empty pool, and `crate_readiness_audit` **never returns `failures`** — it returns `warnings` (`readiness.py:147`) and `ready` (`readiness.py:138`). So `j.failures` is always undefined and `ok` collapses to "pool non-empty." A populated-but-not-ready crate is green-lit; the real veto is dropped, and even the empty-pool NOT-READY can't show a reason (it reads `j.failures[0]`, not `warnings`).
- **Fix:** `ok = j.ok!==false && j.ready===true;` and surface `j.warnings[0]`.

**MEDIUM (correctness, worth fixing with the above) — Preflight audits a *different dataset* than Readiness/Graph and ignores `taste_profile`**
`app.py:3449` (`approved_loop_pool()`, legacy `loops` table) vs `app.py:2816`/`2878` (`approved_atom_pool(taste_profile)`, `ear_atoms`). Preflight's verdict has no relationship to the resident/atom pool the composer actually uses, so it can contradict the Crate Readiness panel.

### BOOK A SET
**HIGH — Workbench "Render ▸" throws away the composed/saved/loaded plan and renders a *different* freshly-composed set**
`index.html:429 renderMix` → `server.py:301` → `app.py:3300 one_click_taste_mix` → `app.py:3387 propose_taste_mashup` (fresh `next_render_seed`, `app.py:3020`)
- **Breaks:** The whole Propose→edit→Save/Load→Render pipeline culminates in a button that posts only `{taste_profile:S.resident}` and re-runs the full cold-start mix from a new seed. `window.__plan` is never sent or read; there is **no endpoint that renders a saved plan**. Preflight/Save/Load are decorative. The WAV is never the plan the user reviewed.
- **Fix:** POST the plan's manifest to `/api/manifest/execute_bg`, or add a plan-aware render endpoint that accepts the arrangement/seed; at minimum feed the previewed seed into the render so output is reproducible.

### ENGINE-WIDE BLOCKER (adjacent surface, but it takes down everything)
**BLOCKER — Identify and Deep-clean wedge the engine `busy` forever (never reset on success)**
`app.py:5217-5229 run_background` + `identify_tracks`/`apply_identities`/`deep_clean_scan`; routes `server.py:311-318`
- **Breaks:** `run_background` sets `busy=True` at start and only clears it in the *exception* handler (`app.py:5227`). On success it never resets, and these three functions never call `set_status(...busy=False)` themselves (unlike scan/analyze/one_click/manifest at `1604/1802/3420/4305`). After one successful Identify or Deep-clean, the engine shows BUSY permanently and **every subsequent `run_background` — including Scan, Analyze, Book-a-Set — raises `RuntimeError('already busy')` → 500 → transient toast**. Only a server restart recovers it.
- **Fix:** Reset `busy` in a `finally`/success path in `run_background`, or don't background these read-only assessments at all.

---

## 3. The Book-a-Set failure chain

`bookSet` (`index.html:280-284`) → `POST /api/one_click_bg {taste_profile}` → `run_background(one_click_taste_mix)` (`app.py:3300`). For a set to book, this chain must hold:

1. **Doctor passes** — hard fail-fast (`app.py:3317-3318`). ✅ ordering is correct.
2. **Engine not already `busy`** — else `RuntimeError('already busy')` (`app.py:5218-5220`). ⚠️ *This is where the blocker bites: a prior Identify/Deep-clean leaves `busy=True` forever.*
3. **Self-harvest to readiness** — `scan → analyze → extract_loops → build_ear_crate` in fail-fast batches until `taste_readiness` is satisfied (`app.py:3336-3355`). Harvest writes into `agent_root`/`working_root`.
4. **`taste_readiness` clears** — `have{} ≥ need{}` across foreground/floor/bass/spark/sources (`app.py:2824-2839`), reading `approved_atom_pool(taste_profile)`. If short → refusal (`app.py:3364-3372`).
5. **`propose_taste_mashup` composes + passes the taste gate**; gate-miss retry re-harvests and **terminates deterministically when `analyze` yields 0** (`app.py:3404-3405`).
6. **Render + post-render QA passes** (`app.py:3422-3427`).

**Where it most likely snaps — and how it ties to config-resolution:**
Step 4 is the break point, and the config-resolution trap is what forces it. After *any* return to Setup + re-save, the app is re-pointed at a **fresh, empty nested workspace** (Section 2, `app.py:596/761`); the previously-analyzed DB, `.npz` cache, and approved `ear_atoms` are orphaned in the old `agent_root`. `taste_readiness` now reads `have=0` for every role → **the exact "taste_readiness failures" the user reports.** The harvest in step 3 tries to refill from scratch; if `analyze` finds nothing to add to the *new* tree (or the atom pool never fills), the gate-miss loop hits `analyze` yields 0 and step 5 **terminates into a readiness-short refusal.** This is the "analyze possibly dying on a config-resolution trap": analyze doesn't crash — it runs against the orphaned/empty tree, so its output never satisfies readiness and the run deterministically refuses.

**Why the user can't see any of this:** the run is on the `_bg` path, so `run_background` discards the rich refusal dict (`_finish_one_click_result`: per-role have/need, yield projections, harvest log — `app.py:3363/3372/3427`) and only the flattened `set_status` string reaches Activity (`app.py:5222-5224`). And Activity itself never renders `last_render_path` on success or the structured refusal — the user is dropped on a screen that shows neither the payoff nor an actionable reason. **Net user experience: click Book-a-Set → long churn → one cryptic refusal line, or nothing.**

---

## 4. Everything else (medium / low / polish)

**Setup/Doctor**
- Browse button swallows the "picker unavailable — paste the path" hint on headless boxes (`index.html:578`; response is HTTP 200 so no toast fires). *(medium)*
- Analysis-depth bar looks tunable but has no control; pinned at 180s (`index.html:553,564-566`). *(medium)*
- `toggleWorkers` before first save → raw `KeyError 'master_root'` toast (`index.html:581`→`app.py:515`). *(medium)*
- "Migrate legacy" is a dead-end: plan preview collapsed to "DRY-RUN — ok", no Apply path (`index.html:571,546`). *(medium)*
- Doctor drops per-check `detail` and the stem-capability note (`index.html:582`). *(low)*

**Library / Loop Review**
- Unconfigured `/api/tracks`,`/api/loops` render the raw exception string in red instead of a "set up workspace" CTA (`index.html:544,534`). *(medium)*
- Loop chips: `Approve` hardcoded highlighted regardless of `l.status`; `Lock` is a duplicate of `Approve` (`index.html:524,531`). *(medium/low)*
- Candidate list silently capped at 100, no pagination while counts show the true total (`index.html:534`). *(medium)*
- apply=true ingest/organize toasts "N planned" (reads like a dry-run); quota-approve count double-counts pre-locked approvals; Organize apply not primary-styled. *(low/polish)*

**Crate / Atoms / Pairs**
- Pairs panel shows the selected atom as its own partner for every right-side edge — meaningless for the entire beds role (`index.html:343`, `app.py:5142-5161`). *(medium)*
- Favorite/Lock/Reject only toast — no list refresh, no favorite/locked field fetched; rejected atoms linger (`index.html:349-353`). *(medium)*
- Readiness panel never refreshes after judging atoms; drops `failures[]`/`ready`; double-counts multi-role atoms; forced-rebuild edge drops non-locked judgments (unreachable from UI). *(low)*

**Workbench / Manifest**
- Manifest op-count (`m.ops` vs `operations`) and human summary (`m.label` vs `summary`) never display (`index.html:451`, `app.py:3696`). *(low)*
- Returning to Workbench shows "no plan yet" while Save/Render still act on the in-memory plan (`index.html:370-385`). *(medium)*
- Cross-resident re-save forks a duplicate plan; a REFUSED plan is still saveable. *(low/polish)*

**Play / Station / Queue**
- "Previous" transport button skips **forward** (wired to `endlessSkip`, titled "Next"); no way to go back (`index.html:100`). *(high on this surface — directional glyph is objectively wrong, but only active in Endless mode)*
- Skip arrows dead outside Endless; STATION "CUT" only journals, never skips; Endless silently drops renders lacking a current-engine report. *(medium)*
- Volume bar shows 85% while actual is 100%; malformed Range header 500s `/api/audio`. *(low)*

**Judge / Render QA**
- JUDGE button only shown on renders that already passed → failing gates uninspectable (`index.html:437,442`). *(high on this surface)*
- Ref-comparison harness unreachable (UI hardcodes `ref:''`); failing-gate reason is raw metric keys; `judge_render` can 500 with leaked traceback on a bad file. *(medium)*

**Sessions / Activity / Perf**
- Perf "Stages this run" permanently empty — reads `p.stages` but data is at `p.ledger.stages` (`index.html:473`, `app.py:433-438`). *(high on this surface)*
- Perf stages don't refresh on run-completion while Activity is open; empty status catch hides connection loss; fixed-width stage bars. *(medium/low/polish)*

**Reorganize / Identify**
- Deep-clean & Identify produce no visible output (results discarded by `run_background`); Identify's default no-API-key failure reported as "ok"; no UI to apply/rollback identities. *(all high — same `run_background` root as the blocker)*
- reorgApply moves originals with no confirm dialog; partial-failure apply mislabeled "refused" and drops the rollback journal (stranding a half-moved library); rollback only reachable in-session. *(high/medium)*

**Playlist**
- Track count always "0 tracks" (`.length` on an int; `index.html:538` vs `app.py:2274`); "open ▸" never appears and no playlist file is ever executed (`j.path` vs `j.manifest`). *(high on this surface)*
- Query placeholder advertises `bpm:/role:` syntax the parser ignores. *(medium)*

**Global**
- 500 `trace` returned by backend is fetched then discarded; `pollStatus` empty catch + hardcoded green "SYSTEM ONLINE" → heartbeat lies on failure; missing/invalid token → total silent failure, no recovery UI (`index.html:155,603-610,126`). *(all high — the silent-bork core)*
- Global QUERY box is a no-op on 6/7 screens; first-boot lands on Play with red "WANTS MATERIAL" from a swallowed "not configured" error, no nudge to Setup; audio failures fully silent; error responses masquerade as empty states. *(medium)*

---

## 5. Where to go from here

**Fix in this order to unblock a real run on the desktop:**

1. **Unwedge the engine first (BLOCKER).** Reset `busy` in a `finally` inside `run_background` (`app.py:5221-5228`). Nothing else can be tested reliably while one stray Identify/Deep-clean click can lock the engine until restart.
2. **Kill the config-resolution trap (HIGH, root of the live symptom).** Persist and bind the workspace **root**, and make `configure_workspace` idempotent (`app.py:596/761`, `index.html:552`). This stops the orphaning that empties the atom pool and forces the readiness refusal. *Also add the separation/idempotency guard so a double-save is a no-op.*
3. **Make failures visible (HIGH — this is why it "borks silently").** In order: (a) `pollStatus` catch must set the status dot to fault + "CONNECTION LOST / BAD TOKEN" (`index.html:609,60`); (b) surface `j.trace` (console + a dismissable panel) in `api()` (`index.html:155`); (c) persist the one_click finish dict onto `status['last_result']` and render it (per-role have/need, harvest log) in Activity instead of the flattened string (`app.py:5222`, `index.html:462-470`); (d) show `last_render_path` with a PLAY/OPEN affordance on success.
4. **Fix the readiness gate honesty (HIGH).** `Preflight`: read `j.ready`/`j.warnings` (`index.html:390`), and point it at `approved_atom_pool(taste_profile)` so it audits the same pool the composer uses (`app.py:3449`).
5. **Make Render render the plan (HIGH).** Wire `renderMix` to execute `window.__plan` via `/api/manifest/execute_bg` (or add a plan/seed-aware render endpoint) so Propose/Save/Load aren't decorative (`index.html:429`).
6. **Restore a working library feed (HIGH).** Add the Ingest source-folder input (`index.html:483`) — or confirm Scan-from-Setup is the intended path and disable the dead Ingest buttons.
7. Then the surface-local highs: Playlist count/`open` (`index.html:538`), Perf stage shape (`index.html:473`), JUDGE-on-failures (`index.html:437`), reorg partial-failure journal loss (`index.html:520`), Identify apply/rollback UI + no-key error surfacing.

**Instrument / log next (to catch the Book-a-Set snap on the real desktop):**
- Log the **resolved roots** (`master_root`/`working_root`/`agent_root`) at every `configure()` and every `connect_db()` — this makes the nesting/orphaning visible the instant it happens.
- At the top of `one_click_taste_mix`, log `approved_atom_pool` size per role and the `agent_root` in use, so a `have=0` readiness refusal is immediately attributable to "pointing at empty tree" vs "genuinely under-harvested."
- Log each harvest batch's `analyze` "analyzed" count and the readiness `have/need` delta per iteration (`app.py:3336-3405`), and emit the full refusal dict to the run bundle path already written by `_run_bundle_finish` (`app.py:309`) — then read that bundle in Activity.
- Stop shipping `trace` to the browser on `judge_render`/generic 500s once (3c) gives you a real error surface; keep it server-side in `DEBUG_LOG`.