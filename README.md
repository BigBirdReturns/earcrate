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

## Local live DJ runtime

The live engine treats MIDI as an exact teaching and execution language rather
than the final product. It compiles arrangement patterns into a `LiveMaterialAtlas`,
maintains a hashed `LiveSetState`, plans over a receding horizon, commits only the
next phrase, and replans after controls or state changes.

Four numerical persona policies ship with the runtime: `club`, `girl_talk`,
`pretty_lights`, and `minimal`. They control phrase and horizon lengths, energy,
density, risk, layer limits, source turnover, role priorities, technique weights,
and objective weights. Persona changes remain pending until a phrase boundary
that is legal for both policies.

The typed technique registry currently includes blend, hard cut, loop extension,
drop to floor, foreground swap, tease, layer building, breakdown, echo out, drum
rebuild, sample chop, and layer accumulation. Every selected technique has
preconditions, exact selected layers, lowering commands, objective terms,
ranked alternatives, and an execution outcome.

A complete scheduled set can be planned and proved through neutral instruments:

```text
python -m earcrate live capability
python -m earcrate live atlas "source-performance.mid" "live-atlas.json"
python -m earcrate live session "source-performance.mid" "live-build" \
  --bars 64 \
  --persona pretty_lights \
  --controls "controls.json" \
  --neutral-render
```

For interactive operation, keep the state as an immutable JSON revision. Apply a
control or commit one legal phrase at a time:

```text
python -m earcrate live state-init \
  "live-atlas.json" \
  "state.000.json" \
  --persona club

python -m earcrate live control \
  "state.000.json" \
  "state.requested.json" \
  --command set_persona \
  --value '"pretty_lights"'

python -m earcrate live step \
  "live-atlas.json" \
  "state.requested.json" \
  "step-001"
```

Controls may switch persona, enable, disable, or force a technique, change
energy, density, risk, or layer limits, hold and release the current material,
and skip source patterns. The next phrase is replanned from the exact current
state; the earlier performance and previous decisions are never rewritten.

### Compile the private library once

The expensive private-library search can be moved completely out of the live
path. The configured-library command reads the approved EarAtom pool, performs
the slow multi-zone search once, materializes exact sample slices, seals racks,
and optionally writes SFZ:

```text
python -m earcrate live crate-compile-library \
  "source-performance.mid" \
  "compiled-live-crate" \
  --profile girl_talk_v1 \
  --max-transpose 18
```

A portable alternative accepts an explicit approved-atom JSON export:

```text
python -m earcrate live crate-compile \
  "source-performance.mid" \
  "approved-atoms.json" \
  "compiled-live-crate" \
  --profile girl_talk_v1 \
  --max-transpose 18
```

Later sessions use only the sealed crate and execute with zero full-library
scans:

```text
python -m earcrate live crate-session \
  "compiled-live-crate/live-crate-atlas.json" \
  "live-show" \
  --bars 128 \
  --persona pretty_lights \
  --controls "controls.json" \
  --render \
  --stems
```

`crate-session` plans on CPU, revalidates the compiled sample identities, binds
the generated performance to those racks, and renders without rescanning the
library. Its CPU execution receipt reports the command count, peak active layers,
and zero pattern or material scans during execution.

### Phrase-buffered audio output

The device callback is deliberately smaller than the planner. A non-audio thread
applies controls, plans the next phrase, binds it to the sealed racks, decodes or
loads the required samples, and prepares stereo float PCM. The callback may only
swap a queued phrase buffer and copy prepared frames. It may not plan, search the
library, decode samples, or bind events.

Prepare one exact phrase and its next state:

```text
python -m earcrate live-audio capability
python -m earcrate live-audio phrase \
  "compiled-live-crate/live-crate-atlas.json" \
  "state.requested.json" \
  "prepared-phrase-001" \
  --controls "next-controls.json"
```

The phrase directory contains the WAV, next state, engine step, MIDI,
MIDI-lowering ledger, binding, render program, event execution ledger, and phrase
receipt. The in-memory `LiveAudioCallback` consumes prepared buffers in fixed
blocks and records explicit underruns. `LiveSoundDevicePlayer` is an optional
thin host around the `sounddevice` package. Core planning, tests, and offline
crate compilation do not require that package or an audio device.

## Verify

- `python tests/run_gates.py`
- `python VERIFY_PACKAGE.py` builds and self-tests the single-file package and
  drives its packaged MIDI, rack, reference, live, and live-audio command surfaces.
- `python scripts/oss_audit.py` validates code and model governance ledgers.

CI retains the complete gate ledger and package-verifier ledger as artifacts on
both successful and failed runs. Treat a red run as a merge blocker. The gate
runner prints the current executable count, so documentation does not preserve a
stale historical number.

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
