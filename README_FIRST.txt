earcrate v0.7.0 "Library Forge"

Run (Windows): double-click START_HERE.cmd
Run (dev):     python -m earcrate            (uses the package directly)
Build 1-file:  python build/make_singlefile.py  -> dist/earcrate_gt.py
Verify:        python VERIFY_PACKAGE.py
Tests/gates:   python tests/test_gates.py

Layout follows EARCRATE_REBUILD_PLAN_v1.md (specs BUILD_SPEC v1.0 + Addendum A remain
canonical). One changelog: CHANGELOG.md. New: multi-folder ingest + organize/retag on the
Library tab (dry-run by default, journaled, rollback-able, sources never modified).
