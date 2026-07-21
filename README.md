# EarCrate

Local-first layered mashup engine. The rule that names it: the composer is
never allowed to touch raw file slices. Material enters through an audition
(the ear crate) or it does not exist. Everything downstream is gated:
varispeed-only deck discipline, deterministic compatibility graph, turnover
contract, pre-render and post-render quality gates, runtime ledger with
per-stage receipts. No fallback render is allowed.

## Run

- Prerequisite: Python 3 plus `ffmpeg` and `ffprobe` on `PATH`.
- Windows (first time): `START_HERE.cmd` installs dependencies, builds, and launches.
- Windows (desktop icon): run `Create-Desktop-Shortcut.cmd` once for an
  "EarCrate" desktop icon. The icon runs `Launch-EarCrate.cmd`, the fast path
  that skips reinstalling dependencies.
- Dev: `python -m earcrate`
- Single file: `python build/make_singlefile.py` then `python dist/earcrate.py`

## Exact MIDI and the neutral player piano

MIDI enters through a source-independent, content-hashed event ledger. Mido is
used as the Standard MIDI File codec, while EarCrate retains every absolute
tick, track-local event order, tempo event, controller, program change, pitch
bend, note message, and meta message as its own canonical data.

```text
python -m earcrate midi inspect "arrangement.mid"
python -m earcrate midi ledger "arrangement.mid" "arrangement.ledger.json"
python -m earcrate midi roundtrip "arrangement.mid" "arrangement.roundtrip.mid"
python -m earcrate midi render "arrangement.mid" "arrangement.neutral.wav" --stems-dir "neutral-stems"
```

The neutral renderer deliberately uses simple tones. It executes tempo changes,
sustain, volume, expression, pan, and pitch bend. Each full render writes three
independently hashed records beside the WAV: the exact render program, an
event-level execution ledger, and the final render receipt. Every selected note
is classified as fully executed, truncated, or refused. A full render is rejected
unless every selected note is fully executed. Diagnostic renders created with
`--max-seconds` retain all partial and refused outcomes instead of reporting false
completion.

Optional per-track stems use the same master scale, so their numerical sum must
reproduce the full render within the gate tolerance. Declared track count does
not determine audio rendering cost. Only note events, control curves, active
voices, and occupied tracks are materialized.

Basic Pitch is an optional note-observation provider for isolated stems. It is
measurement only: EarCrate retains authority over the accepted beat grid,
quantization, arrangement, and sample binding.

```text
pip install "basic-pitch>=0.4,<0.5"
python -m earcrate midi transcribe "bass-stem.wav" "bass.notes.json" --provider basic-pitch
```

The provider hashes the installed model bytes and raw model outputs into the
observation receipt. No Basic Pitch model is bundled by EarCrate.

## Verify

- `python tests/run_gates.py`
- `python VERIFY_PACKAGE.py` builds and self-tests the single-file package and
  drives its packaged MIDI command surface.
- `python scripts/oss_audit.py` validates code and model governance ledgers.

CI runs the package and package-verification gates on every push and pull
request. Treat a red run as a merge blocker. The complete gate ledger is retained
as a workflow artifact whether the run passes or fails.

## Lineage

Descends from the Jukebreaker GT line (v0.5.x deck discipline, v0.6.x
fail-fast harvest / turnover contract / keyless percussion, v0.7.x modular
rebuild). Full history is in `CHANGELOG.md`. Legacy workspaces are adopted in
place: an existing `jukebreaker.sqlite` is used without migration.

Sources (audio) never enter this repository.

## License

PolyForm Noncommercial 1.0.0. It is free for personal and noncommercial use;
commercial use requires a written license from the copyright holder. See
`LICENSE.md`, which also notes that music rights are separate from software rights.
