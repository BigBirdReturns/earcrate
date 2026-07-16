# EarCrate immutable project API

The existing LATTICE server exposes the complete project and score lifecycle under
`/api/projects`. The browser remains loopback-only and requires the existing
`X-JB-Token` header. Every mutating request creates or advances an immutable
revision; render and export operations bind their artifacts to a revision SHA and
score SHA.

## Read operations

| Method | Route | Result |
|---|---|---|
| `GET` | `/api/projects` | Visible project records. |
| `GET` | `/api/projects/{project_id}` | Project record and active revision. Add `?revision=<sha>` for a named revision. |
| `GET` | `/api/projects/{project_id}/history` | Append-only command history. |
| `GET` | `/api/projects/{project_id}/runs` | Revision-bound project render receipts. |

## Compile and import

| Method | Route | Body |
|---|---|---|
| `POST` | `/api/projects/compile` | Existing `project_compile` parameters: `taste_profile`, `target_seconds`, `name`, optional `candidate_count`, `seed`, tempo/key and intent controls. |
| `POST` | `/api/projects/import` | `arrangement`, optional `name`, `project_id`, `created_by`, `static_gate_receipt`, and `compiler_receipt`. |

Compilation uses the configured catalog, approved EarAtoms, compatibility graph,
transform-feasible deck lattice, runtime TasteSpec, bounded candidate search, and
existing renderer capabilities. Import is the compatibility boundary for legacy
arrangements and project-scoped external sources.

## Revision commands

| Method | Route | Body |
|---|---|---|
| `POST` | `/api/projects/{project_id}/commands` | One typed command such as `set_gain`, `set_pan`, `trim_clip`, `replace_clip`, `set_stem`, `set_transition`, `mute`, `solo`, `lock`, or `unlock`. |
| `POST` | `/api/projects/{project_id}/undo` | Empty object. |
| `POST` | `/api/projects/{project_id}/redo` | Empty object. |
| `POST` | `/api/projects/{project_id}/recompile` | Optional compiler overrides; locked decisions are preserved. |

Example command:

```json
{
  "actor": "human",
  "kind": "set_pan",
  "payload": {
    "clip_id": "clip_…",
    "pan": 0.1
  }
}
```

## Audio and exports

| Method | Route | Body / result |
|---|---|---|
| `POST` | `/api/projects/{project_id}/preview` | Optional `start_beat`, `duration_beats`, `revision_sha`, and contained `dst`; returns a revision-bound WAV crop. |
| `POST` | `/api/projects/{project_id}/render` | Optional `revision_sha` and contained render `dst`; runs premaster, explicit mastering revision, publication render, and verification. |
| `POST` | `/api/projects/{project_id}/export` | Optional `revision_sha` and contained `destination`; returns EDL, RPP, and score-sheet paths. |
| `POST` | `/api/projects/{project_id}/export/edl` | Same export contract, with `format: "edl"` and the EDL path in `path`. |
| `POST` | `/api/projects/{project_id}/export/rpp` | Same export contract, with `format: "rpp"` and the RPP path in `path`. |
| `POST` | `/api/projects/{project_id}/export/sheet` | Same export contract, with `format: "sheet"` and the score-sheet path in `path`. |

The front end should treat `project.active_revision_sha` as its optimistic
concurrency head. It should refresh the project after every command, undo, redo,
recompile, or render because mastering creates a machine-authored child revision.
It should never post a loose arrangement to the render endpoint.
