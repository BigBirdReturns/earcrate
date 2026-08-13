# Album One Sprint 01 execution

Album One runs seven track lanes in parallel. A1-07 remains the proving track for reusable timing and conductor behavior, but it is not a dependency for A1-01 through A1-06.

## Single Estate entrypoint

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_ALBUM_ONE_SPRINT_01.ps1 `
  -Workspace "D:\Projects\Products\EarCrate\sessions\album-one-sprint-01" `
  -VerifyBytes `
  -ExecuteReadyAdapters `
  -OpenWorkspace
```

The first run creates the durable workspace, a private source-binding manifest, seven track dossiers, the task queue, and a source-free public projection. With populated bindings, the same command verifies the exact private objects, materializes adapter commands, fans out runnable adapters, and leaves every lane in a typed state.

The runner never copies private source audio into the repository or public projection. It refuses to overwrite an existing workspace.

## Track states

`machine_work_ready` means that the Estate has a complete dossier and can continue machine work. It does not mean an owner audition exists.

`frontier_ready` means the lane has one to four full-form, reproducible cuts; every musical delta is disclosed; setup, body, and payoff or release are present; and the cuts do not share one dominant audible defect.

`blocked_exact_source`, `blocked_exact_credential`, and `blocked_rights_or_custody` are legal only when the complete runnable contract is already present and the blocker names the exact missing object.

`failed` requires the exact failure. “Needs research” is not a terminal state.

## Owner-time boundary

Short excerpts, separator tests, pulse trials, timing-law probes, provider seed sweeps, and stem checks remain machine-side. Except where the musical object is inherently short, no owner frontier may be a unit test. The owner receives a complete dramatic proposition.

Each track receives at most one owner frontier in this sprint and at most four cuts. Provider identity may be blinded when useful. The musical operation and invariant set may not be hidden.

## Sprint exit

Sprint 01 ends only when all seven tracks have either a machine-qualified full-form frontier or an exact irreducible blocker with the complete runnable contract already prepared. Album acceptance, system-reference completion, and release rights remain separate later authorities.
