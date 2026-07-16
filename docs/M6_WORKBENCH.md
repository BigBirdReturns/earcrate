# M6 — the project Workbench (functional pass)

The Workbench is rebuilt on the frozen `/api/projects` contract, exclusively. The
old loose-arrangement path (`/api/timeline/propose`, `/api/render_plan`,
`renderMix`/`proposePlan`/`renderPlanRails`) is **gone** from the UI — the front
end can no longer post an arbitrary arrangement to the renderer. Every mutation is
a typed command that advances `active_revision_sha`, and the whole project is
refetched after each command, undo, redo, recompile, preview, or render (because
mastering authors a machine child revision).

This is a **functional product pass, not the final aesthetic pass.** The existing
LATTICE shell — command strip, nav rail, skins, transport, system readout,
typography, offline/local-only constraints — is preserved unchanged. No framework,
no network dependency, no remote asset, no second state model was added; the
single-file/offline build stays viable (the UI HTML is byte-identical between
package mode and the built `dist/earcrate.py`).

## Functional surface (spec points 1–11)

1. **Project list + creation** — `POST /api/projects/compile` with persona,
   name, duration, seed, candidate count, and BPM intent controls; a project
   chooser lists every immutable project. Compatibility import remains only via
   `POST /api/projects/import`.
2. **Active project header** — project_id, active_revision_sha, showing/parent
   revision, persona, seed, BPM, duration, gate state (PASS/REFUSED), and whether
   the displayed revision is still the optimistic-concurrency head (CURRENT vs
   STALE VIEW).
3. **Three-rail timeline** — built from the active `ScoreRevision`; shows EVERY
   floor/foreground/spark clip (a section is never collapsed to its first layer),
   with timeline position + duration, role, source, stem, gain, pan, lock/mute/
   solo state, and the transition boundary markers between sections.
4. **Clip inspector** — emits typed commands (`set_gain`, `set_pan`, `trim_clip`,
   `replace_clip`, `set_stem`, `mute_clip`, `solo_clip`, `lock`, `unlock`). Gain
   and pan ranges come from the revision's compiled policy (`mix_policy.
   role_gain_db[rail]`, `pan_max_abs`) — not UI constants. An explicit **override
   persona policy** toggle sends `override_policy` for deliberate out-of-range /
   structural edits (a backend feature).
5. **Transition inspector** — selected technique, executable parameters,
   alternatives (from the revision decisions), rejection reasons, required
   capabilities and execution capability (EXECUTABLE / NOT EXECUTABLE); permits
   `set_transition`.
6. **Undo / redo / recompile-unlocked** — full project refresh after every one.
7. **Revision-bound preview / render / export** — preview crop, render (premaster
   → mastering child → verify), and EDL / Reaper / live-sheet exports; the run
   receipt (WAV path, revision + score sha, executed clip/transition counts,
   mastering actions) is shown in the Runs view.
8. **History + Runs** — revision ancestry as a lineage (from→to per command, HEAD
   marked), not a flattened activity log; runs are the revision-bound render
   receipts.
9. **M5 morning triage** — lists piano runs (kept / discarded / refused attempts
   with persona, revision, render, reason); keep/reject writes THROUGH the
   atom-judgment path (`set_atom_judgment`) so it becomes M4 training data.
10. Every loose-arrangement Workbench control was removed.
11. No framework / network / remote asset / second state model; single-file build
    verified.

## Backend additions

- `GET /api/piano/runs`, `POST /api/piano/triage` (+ `project_piano_runs` /
  `project_piano_triage` on the core). A triage keep/reject maps the attempt's
  revision back to its approved-atom material (via the arrangement layers'
  `atom_id`) and records the verdict on the run receipt.
- Project policy/validation/concurrency errors now map to **4xx** (400/409/404)
  instead of 500, so a legitimate refusal is a clean client error the Workbench
  renders as a toast — and is not counted as a console error.

## Verification receipt

`tests/manual/verify_workbench_dom.py` (Playwright/Chromium, not a `run_gates`
gate — needs a live server + browser) drives the full lifecycle. **Package-mode
run: PASS, zero console errors**, every step green:

```
header               True        preview              True
timeline_clips       4           render               True
ranges               True        export               True   (edl+rpp+sheet)
edit_new_revision    True        runs_verified        True
undo_prior           True        history_head         True
redo_edited          True        triage_run           True
transition_inspector True        triage_keep          True
                                 reopen_head          True   (after page restart)
CONSOLE_ERRORS: 0
```

Lifecycle exercised: compile/import → inspect timeline → edit a clip → verify new
revision → undo → verify prior → redo → preview → render → export → reopen after
restart. The single-file artifact serves the byte-identical Workbench (embedded
HTML `== ` the package `index.html`) and passes its self-test; the built
`dist/earcrate.py` server binds and serves the same `/api/projects` +
`/api/piano/*` endpoints.

Hermetic gates (run by `run_gates.py`, 201/201):
- `test_project_http_api_exposes_frontend_contract` — extended with the piano
  runs endpoint, sheet export, and the 4xx refusal / 404 mapping.
- `test_piano_triage_feeds_m4_judgments` — keep/reject writes atom judgments.

Screenshots (desktop 1440px + narrow 760px) captured during the package-mode run:
`package_desktop_timeline.png`, `package_desktop_triage.png`,
`package_narrow_timeline.png`.

## Visual decisions deferred to the on-box design review

These are intentionally left for the aesthetic pass with the owner's eye on real
compiled output — the functional pass did not decide them blind:

1. **Timeline density / zoom.** Clips are positioned by bar across the full set
   width; a long real set (many sections) will want horizontal zoom/scroll and a
   minimum clip width, not the current fit-to-width. Waveform thumbnails per clip
   are deferred.
2. **Rail colour semantics.** Floor/foreground/spark currently share the accent
   with lock/mute/solo shown as glyphs (⛒ ⊘ ◎). Whether rails get distinct hues
   (and how that reads across the four skins) is a design call.
3. **Inspector as panel vs modal.** The clip/transition inspector is a fixed
   right column; on narrow widths it drops below the timeline. A docked vs
   floating vs drawer treatment is unresolved.
4. **`replace_clip` UX.** Currently a raw-JSON prompt (advanced/escape hatch). The
   real interaction — pick a replacement atom from the crate with audition — is a
   design task that should reuse the Crate audition surface.
5. **Gate/refusal presentation.** A refused edit is a toast; a REFUSED gate badge
   sits in the header. How prominently to surface *which* structural failures and
   how to guide the fix is deferred.
6. **Header field priority at narrow widths.** All ten header cells wrap; which
   collapse first (and whether some move into a disclosure) wants a real design
   pass.
7. **Transport integration.** Preview/render load the WAV into the existing
   transport; a project-scoped scrub/marker view tied to the timeline is a future
   idea, not built.

None of these block the functional lifecycle, which is fully usable without CLI
intervention today.
