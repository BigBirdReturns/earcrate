earcrate: Album One production program

READ FIRST:
  ALBUM_ONE.md
  configs/album_one/manifest.v1.json
  PRODUCT.md
  AGENTS.md

Current truth:
  Album masters accepted:      0/7
  System references completed: 0/7
  Active proving track:        A1-07 Beggin' × Beggin'

Run (Windows): double-click START_HERE.cmd
Run (dev):     python -m earcrate
Build 1-file:  python build/make_singlefile.py  -> dist/earcrate.py
Verify:        python VERIFY_PACKAGE.py
Tests/gates:   python tests/run_gates.py

Every change must declare album_scope, musical_gap, control_or_baseline,
owner_audition_effect, and private_execution_required. Provider execution,
green gates, reproducibility, and signal sanity are evidence, not musical
acceptance.

Generated output stays inside configured workspace roots. Source recordings,
stems, prompts, lyrics, model assets, credentials, private paths, option maps,
and owner-review authority remain local. A render writes no WAV unless the
complete selected arrangement passes its TasteSpec and track contract.
