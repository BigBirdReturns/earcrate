earcrate v0.8.27 "Release Consolidation"

Run (Windows): double-click START_HERE.cmd
Run (dev):     python -m earcrate            (uses the package directly)
Build 1-file:  python build/make_singlefile.py  -> dist/earcrate.py
Verify:        python VERIFY_PACKAGE.py
Tests/gates:   python tests/run_gates.py

Acceptance follows the executable gates, JUKEBREAKER_SPEC_v2_CONSOLIDATED.md, and the
versioned profiles in profiles/. Architecture is documented in EARCRATE_REBUILD_PLAN_v3.md;
BUILD_SPEC and Addendum-era documents are historical inputs only. One changelog: CHANGELOG.md.

Generated output stays inside configured workspace roots. Operations that can alter source
files are dry-run/apply guarded and journaled. A render writes no WAV unless the complete
selected arrangement passes its TasteSpec.
