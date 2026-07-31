# Children v1 — cross-organ Buffalo Gate

`children_v1` is EarCrate's first content-addressed cross-modal acceptance specimen.
It is deliberately not a repository copy of a score or commercial recording. The
repository contains schemas, printed-score annotations, immutable expected hashes,
policies, and compact receipts. Source media remains external and must be supplied by
the operator.

## What this slice proves

The score branch takes six already-custodied external artifacts:

```text
vector score PDF
score extraction JSON
exact reconstructed MIDI
score proof receipt
MixScore
MixScore execution ledger
```

It verifies their manifest identities, reconciles every extracted note with the exact
MIDI note ledger, promotes printed navigation into a `FormGraph`, preserves the
105-measure traversal as a separate `PerformancePath`, promotes the printed chord
symbols into canonical `MusicHarmonyFrame` objects, and emits `MusicEvent` answer-key
events with source observations.

The supplied score establishes quarter note = 130, 4/4, four flats, repeats and
alternate endings, a Segno, `To Coda`, and `D.S. al Coda`. The current external proof
expands 69 printed measures to 105 performed measures and reconciles 1,257 notes over
`Right Hand` and `Left Hand`. Its source-transport proof executes 19 of 19 selected
operations with no refusals and exact stem/master reconciliation.

The score branch never opens the reference recording. Its observation ledger accepts
only `score` ancestors. An `audio` ledger accepts only `audio` ancestors. The
convergence process may receive both only after they have independently sealed.

## Run

Create a bindings template:

```text
python -m earcrate buffalo children-bindings children.bindings.json
```

Fill the external paths and compile the isolated score branch:

```text
python -m earcrate buffalo children-score \
  children.bindings.json \
  build/children-v1-score
```

Assemble the honest current gate receipt:

```text
python -m earcrate buffalo gate \
  build/children-v1-score \
  build/children-v1-score/buffalo-gate.receipt.json
```

Without an independent audio ledger the command succeeds as an audit operation but the
whole gate reports `overall_status: blocked` and `buffalo_gate_passed: false`.

After running the cephalopod reader or another approved audio provider without score
access, provide its sealed branch ledger:

```text
python -m earcrate buffalo gate \
  build/children-v1-score \
  build/children-v1-score/buffalo-gate.receipt.json \
  --audio-ledger build/children-v1-audio/audio.observation-ledger.json
```

## Canonical objects

The specimen layer coordinates existing organs rather than replacing them:

```text
SpecimenManifest
  ├── score ObservationLedger
  │     ├── FormGraph
  │     ├── PerformancePath
  │     └── score-derived answer key
  ├── audio ObservationLedger
  ├── ConvergenceReport
  └── BuffaloGateReceipt
```

The answer key uses the existing proof-carrying music model's `MusicEvent` and
`MusicHarmonyFrame`. MIDI remains an exact execution ledger. MixScore remains source
transport authority. The specimen layer owns cross-organ custody and independence.

## Current gate result

The committed compact proof summary records six required organs as passed:

```text
score_custody
notation_perception
form_graph
harmony_frames
exact_midi_authority
mixscore_source_transports
```

The whole gate remains blocked on six precise organs:

```text
cephalopod_audio_inference
cross_modal_convergence
proof_carrying_adjacent_move
sealed_rack_realization
review_patch_circulation
campaign_evolution
```

This distinction is binding: organ-level success is not thesis-level success.

## Branch isolation

Every observation ledger lists its input artifacts and every input's ancestor branches.
The allowed lineages are:

```text
score       <- score
 audio      <- audio
 convergence<- score + audio + convergence
 performance<- score + audio + convergence + performance
 review     <- performance + review
 evolution  <- all earlier evidence branches
```

A score-derived artifact in the audio ledger is an independence violation and is
refused. A score-rendered WAV cannot stand in for the recording branch.

## Convergence policy

The default `children_buffalo_v1` policy is frozen before audio inference and reports
separate metrics rather than collapsing musical identity into one scalar:

```text
tempo absolute error
meter equality
key root/mode equality
note-pitch recall
note-onset mean absolute error
harmony-root recall
```

A provider disagreement remains visible. Thresholds are versioned data and may not be
weakened solely to turn this specimen green.

## Source-media boundary

`specimens/children_v1.json` carries the exact external SHA-256 identities. The PDF,
recording, reconstructed audio, stems, and private-library material are not repository
content. Music rights remain separate from EarCrate's software license.
