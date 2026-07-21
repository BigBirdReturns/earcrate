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

## Plug-and-play crate substitution

EarCrate can turn a finished MIDI performance into an exact shopping list for
replacement material. A demand manifest groups notes by track, channel, and
program, then records note and velocity coverage, gate duration, polyphony,
controller use, pitch bend, General MIDI family, role hints, and every source
event that must remain accounted for.

The direct approved-library flow is dry-run first. It searches only the current
approved EarAtoms for the chosen profile and writes nothing:

```text
python -m earcrate rack-from-crate "arrangement.mid" --profile girl_talk_v1
```

When the proposal is complete, `--apply` decodes the selected source regions,
seals exact rack revisions, compiles SFZ instruments, binds every MIDI event,
and can render the substituted arrangement immediately:

```text
python -m earcrate rack-from-crate "arrangement.mid" \
  --profile girl_talk_v1 \
  --apply \
  --output "crate-build" \
  --render "crate-build/substituted.wav" \
  --stems-dir "crate-build/stems"
```

Search produces candidate receipts instead of changing execution semantics.
Each receipt records role fit, timbral fit, key and transposition distance,
duration coverage, loopability, quality terms, and the exact approved atom.
Rejected atoms are excluded. Missing coverage remains unresolved and prevents
an applied build or render. There is no runtime sample fallback.

The lower-level rack commands remain available for manual or external search
systems:

```text
python -m earcrate midi demand "arrangement.mid" "arrangement.demand.json"
python -m earcrate midi rack-template "bass.rack.draft.json" --mode pitched --rack-id crate-bass --name "Crate Bass"
# Edit the draft to point at an approved sample or EarAtom slice.
python -m earcrate midi rack-seal "bass.rack.draft.json" "bass.rack.json"
python -m earcrate midi bind "arrangement.mid" "arrangement.binding.json" "bass.rack.json" "drums.rack.json"
python -m earcrate midi render-rack "arrangement.mid" "arrangement.binding.json" "arrangement.crate.wav" --rack "bass.rack.json" --rack "drums.rack.json" --stems-dir "crate-stems"
python -m earcrate midi sfz "bass.rack.json" "bass.sfz"
```

A sealed `RackRevision` identifies the exact source file, decoded PCM slice,
key and velocity zone, root note, loop, trigger behavior, tuning, gain, pan, and
envelope. Binding compiles an event-level map from every MIDI note to one exact
rack zone. A missing key range, velocity layer, duration, rack, sample, or source
identity remains an explicit unresolved or refused event. Rendering cannot
silently substitute another sample.

The in-process rack renderer is the proof backend. It performs variable-rate
sample playback, pitch bend, looping with optional crossfade, one-shot and gated
voices, velocity and channel controls, exact source revalidation, per-track
stems, and event-level execution receipts. SFZ is generated as portable object
code for external samplers; the EarCrate rack and binding ledgers remain the
source of truth.

## Verify

- `python tests/run_gates.py`
- `python VERIFY_PACKAGE.py` builds and self-tests the single-file package and
  drives its packaged MIDI and rack command surfaces.
- `python scripts/oss_audit.py` validates code and model governance ledgers.

CI runs the package and package-verification gates on every push and pull
request. Treat a red run as a merge blocker. The complete gate ledger and the
package-verifier ledger are retained as workflow artifacts whether the run
passes or fails.

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
