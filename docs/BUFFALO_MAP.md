# EarCrate v0.9 project cutover: retained-mechanism map

This document is an executable-scope map, not a replacement architecture. The
v0.9 project cutover keeps the existing EarCrate application and changes the
musical authority that connects its subsystems. Every generated or imported set
is stored as an immutable `ScoreRevision`; the existing renderer executes that
revision, and the existing catalog, analysis, curation, provider, librarian,
study, safety, CLI, and UI surfaces remain in the repository.

## The vertical path

```text
catalog files + L0 decoded-PCM identities
    -> existing analysis + per-beat state
    -> existing loops / MaterialRegions / EarAtoms
    -> all versioned TasteSpec profiles
    -> existing compatibility graph + feasibility lattice
    -> bounded deterministic candidate search
    -> immutable ScoreRevision in working_root/projects
    -> guarded render_project manifest
    -> existing multideck renderer in premaster mode
    -> explicit persona-bounded mastering actions saved as a child revision
    -> existing multideck renderer applying only those sealed actions
    -> verification-only quality gate
    -> WAV + report + EDL + RPP + score sheet bound to one revision/score
```

## What was retained and where it enters

| Existing buffalo | Existing authority retained | Project integration |
|---|---|---|
| Catalog, SQLite state, scan, PCM identity | `earcrate/app.py`, `earcrate/analyze/decode.py` | `project/bridge.py` resolves every library clip through the current loop/file generation and seals its full decoded-PCM identity. |
| Analysis, beat state, material regions | `analyze/features.py`, `analyze/beat_features.py`, `materials/regions.py` | The existing EarAtom and external-remix paths remain the material source. No duplicate analyzer was introduced. |
| EarAtoms and curation | `ear/readiness.py`, `ear_atoms`, `atom_judgments` | `project_compile` reads `approved_atom_pool`; rejected and locked human judgments continue to outrank machine ranking. |
| All TasteSpec personas | `profiles/*.json`, `tastespec/profiles.py` | `project/policy.py` compiles every runtime profile, retains every authored field, and records derivations only for missing actuator envelopes. |
| Compatibility graph and pair judgments | `compatibility_edges`, `pair_judgments`, `atom_edge_score` | The existing composer still selects material through these typed relations before a revision can be created. |
| Tempo/key feasibility lattice | `deck/lattice.py`, `deck/transform.py`, `choose_taste_deck` | Candidate generation remains transform-feasible before compose. The revision stores the selected transforms and the renderer refuses drift. |
| Existing TasteSpec composer | `compose_taste_arrangement` | `project/runtime.py` runs bounded deterministic multi-seed search over the real composer instead of replacing it or reporting `count: 1`. |
| Anchor/transition planning | `plan/transitions.py`, existing transition grammar | `project/bridge.py` turns selected transition labels into source-bound capability records. An unavailable tail or required vocal material prevents revision creation. |
| External remix anchor inversion | `remix/external.py`, `propose_external_remix` | The existing target analysis and pinned vocal anchor remain. The resulting arrangement is imported into the same revision model as library projects. |
| Stem providers and GPU queue | `providers/stems.py`, `providers/workqueue.py`, shared ArtifactStore | Stem availability is consulted at compile time. The score explicitly selects `mix`, `vocals`, or `no_vocals`; project rendering may not silently fall back. |
| Varispeed, filtering, multi-deck tails | `deck/dsp.py`, `render_mashup` | The existing DSP is retained. Project mode adds exact source-window use, score gain and pan receipts, stereo equal-power pan, real external-source tails, and transition execution failure as a publication veto. |
| Vocal-keyed bed ducking | existing renderer | Still executed by the renderer, with its selected policy stored in the revision and its measured depth reported. |
| Presence/low-end mastering | `judge/audio.py` | `resolve_project_master_actions` measures a premaster and writes explicit actions into a child revision. `apply_project_master_actions` applies those values without solving or substituting. |
| Audible truth gate | `drydeck_quality_gate`, render-integrity gate | It is verification-only for project publication. A premaster is intermediate and never presented as success. |
| Guarded manifests and rollback | `prevalidate_manifest`, `execute_manifest`, rollback JSONL | `render_project` is a first-class guarded operation with dry-run default, destination containment, rollback inverse, and revision readback. |
| Librarian, identify, reorganize, deep-clean, migration | `librarian/ingest.py`, existing CLI/API | Preserved unchanged. The project cutover does not delete or fork library management. |
| Reference study and MusicBrainz | `study/*` | Preserved unchanged and available to future project reference constraints. |
| Existing CLI and single-file distribution | `earcrate/cli.py`, `build/make_singlefile.py` | `earcrate project ...` is additive. All prior commands remain, project modules are included in the single-file build order, and the built artifact drives the same project acceptance command. |
| Existing LATTICE UI and server | `ui/server.py`, `ui/static/index.html` | The visual UI is preserved. `/api/projects` now exposes compile/import, list/show/history/runs, commands, undo/redo, recompile, preview, render, and revision-bound exports; the next UI pass only binds Workbench controls to the documented contract in `docs/PROJECT_API.md`. |

## Project authority

The visible creative record lives under:

```text
working_root/projects/<project_id>/
    project.json
    revisions/<revision_sha>.json
    commands.jsonl
    checkpoint.json
    premaster/*.wav
    renders/*.json
    previews/*.wav
    exports/<revision_sha>.edl.json
    exports/<revision_sha>.rpp
    exports/<revision_sha>.sheet.md
```

`score_sha` identifies executable musical content. `revision_sha` identifies the
score plus ancestry, decisions, locks, gates, and compiler receipts. Human edits,
locks, undo, redo, unlocked recompilation, and machine mastering produce child
revisions. The renderer accepts a project/revision identity through a guarded
manifest. It no longer receives permission to choose a stem, substitute a source,
change a gain, invent a transition, or solve mastering during final publication.

## Compatibility boundary

`render_mashup` remains available for historical manifests and readback. New
ordinary proposals, one-click sets, external remixes, timeline proposals, CLI
compiles, album renders, and bake-offs enter through project-backed proposals.
`render_plan` is only a compatibility importer for old Workbench arrangement
JSON. It creates a project before rendering and therefore does not establish a
second creative-record model.

## Acceptance controls

`tests/test_projects.py` proves the integrated path against the actual
`EarcrateCore`:

- every runtime TasteSpec compiles through one policy contract;
- bounded candidate search creates a persisted project;
- locks, typed commands, undo, redo, restart readback, EDL, RPP, and score sheet
  bind to the active revision;
- a guarded `render_project` manifest dry-runs before writing;
- an external-source overlap creates a real outgoing tail and records
  `executed: true`;
- mastering is revision data and every action is executed from sealed values;
- a score pan produces stereo audio and is reported by the executed layer;
- preview is cropped from the same verified project render and remains revision-bound;
- edit changes the artifact, undo restores the byte-identical artifact, redo and restart recover the edited head;
- the built single-file CLI drives the same isolated acceptance lifecycle;
- a source mutation after project creation is refused.

The existing gate suite remains authoritative for the retained subsystems. The
project tests are appended to `tests/run_gates.py`; they do not replace the
catalog, librarian, provider, analysis, remix, study, album, or package gates.

`earcrate project acceptance --destination <empty-dir>` is the public acceptance
entry point. It creates real audio sources, imports a project, executes a real
multideck overlap, resolves mastering into a child revision, applies a pan edit,
proves undo/redo and restart recovery, emits preview and exports, and refuses a
post-project source mutation. The same command is exercised through package mode
and the built single-file artifact.
