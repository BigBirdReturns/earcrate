earcrate: Album One production program

READ FIRST:
  ALBUM_ONE.md
  ALBUM_SPRINT_01.md
  configs/album_one/manifest.v1.json
  configs/album_one/sprint-01/campaign.v1.json
  PRODUCT.md
  AGENTS.md

Current truth:
  Album masters accepted:      0/7
  System references completed: 0/7
  Proving track:               A1-07 Beggin' × Beggin'
  Active workstreams:          A1-01 through A1-07 in parallel

Run Album Sprint 01:
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\RUN_ALBUM_ONE_SPRINT_01.ps1 -Workspace "D:\Projects\Products\EarCrate\sessions\album-one-sprint-01" -VerifyBytes -ExecuteReadyAdapters

Run application: double-click START_HERE.cmd
Run dev:         python -m earcrate
Build 1-file:    python build/make_singlefile.py  -> dist/earcrate.py
Verify:          python VERIFY_PACKAGE.py
Tests/gates:     python tests/run_gates.py

Every change must declare album_scope, musical_gap, control_or_baseline,
owner_audition_effect, and private_execution_required. Provider execution,
green gates, reproducibility, and signal sanity are evidence, not musical
acceptance.

Machine diagnostics may be short. Owner-facing audio must be a complete musical
proposition. Every cut must disclose its musical delta. A frontier with one
shared dominant defect is invalid and does not require an owner ranking.

Generated output stays inside configured workspace roots. Source recordings,
stems, prompts, lyrics, model assets, credentials, private paths, option maps,
and owner-review authority remain local. A render writes no WAV unless the
complete selected arrangement passes its TasteSpec and track contract.
