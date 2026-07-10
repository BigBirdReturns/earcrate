# earcrate

Local-first layered mashup engine. The rule that names it: the composer is
never allowed to touch raw file slices. Material enters through an audition
(the ear crate) or it does not exist. Everything downstream is gated:
varispeed-only deck discipline, deterministic compatibility graph, turnover
contract, pre-render and post-render quality gates, runtime ledger with
per-stage receipts. No fallback render is allowed.

## Run
- Windows: `START_HERE.cmd`
- Dev: `python -m earcrate`
- Single file: `python build/make_singlefile.py` then `python dist/earcrate.py`

## Verify
- `python tests/test_gates.py`
- `python VERIFY_PACKAGE.py` (builds and selftests the single file too)

CI runs both on every push. If the gates refuse, the merge refuses.

## Lineage
Descends from the Jukebreaker GT line (v0.5.x deck discipline, v0.6.x
fail-fast harvest / turnover contract / keyless percussion, v0.7.x modular
rebuild). Full history in CHANGELOG.md. Legacy workspaces are adopted in
place: an existing `jukebreaker.sqlite` is used without migration.

Sources (audio) never enter this repository.
