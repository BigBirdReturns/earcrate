# EarCrate OSS commodity map

## Contract

EarCrate owns the musical record. External projects may parse, measure, separate, transform, synthesize, solve, verify, display, or export it, but no provider object is allowed to become the source of truth. Canonical project revisions, MIDI performance ledgers, rack mappings, material bindings, human locks, exact lowering, and execution or refusal receipts remain EarCrate data.

Every provider result is derived and replaceable. Its receipt must identify the input content, provider and code version, model hash when applicable, complete configuration, canonical output hash, validation result, and cache state. Code and model licenses are recorded independently. Mutable names such as `latest`, `main`, and `best` are forbidden as reproducibility identities.

## Landed in this change

### Mido as the Standard MIDI File codec

Mido is the event-preserving boundary for SMF type 0, 1, and 2 files. EarCrate immediately converts its messages into `earcrate_midi_ledger` schema version 1, which stores absolute ticks, track-local event order, meta-message identity, channels, controllers, programs, pitch bends, notes, and tempo events. The semantic hash excludes file path, byte encoding, and `end_of_track` padding so parse, write, and reparse can prove that musical content survived even when container bytes differ.

### Neutral player-piano renderer

The neutral renderer executes note on/off messages, tempo changes, sustain pedal, channel volume, expression, pan, and pitch bend. It produces a deterministic stereo WAV, optional per-track stems, an exact render program, an event-level execution ledger, and a final receipt. The same global scale is applied to every stem, so their sum must reproduce the master within the floating-point file tolerance.

Rendering is event-sparse. Ten thousand declared tracks do not allocate ten thousand audio buffers or require a per-sample scan of every track. The renderer materializes only note spans and occupied tracks. The scale gate explicitly verifies a 10,000-track ledger with one occupied track.

### Basic Pitch as a note-observation provider

Basic Pitch is registered behind the new `notes` provider seam. It supplies observed note starts, ends, MIDI pitch, amplitude, velocity, and pitch-bend contours from one isolated audio stem. EarCrate records the installed package version, hashes the actual model file or directory tree, hashes each raw model-output array, canonicalizes note observations, and can bank the observation and generated MIDI in the L3 ArtifactStore.

Basic Pitch is not allowed to decide the accepted tempo map, quantization, section form, layer entrances, sample binding, or arrangement. Its timestamps remain observations in seconds until a later alignment step maps them onto an EarCrate beat grid with an explicit receipt.

### Performance demands and immutable sample racks

A finished MIDI ledger now compiles to `earcrate_performance_demand` schema version 1. Each track/channel/program slot records every event that must be preserved plus note and velocity coverage, gate duration, polyphony, controller use, pitch bend, General MIDI family, role hints, and a machine-readable library-search query.

A human or future library-search provider may propose a rack draft, but only EarCrate seals it. `RackRevision` schema version 1 identifies the exact source file and byte hash, decoded PCM slice hash, key and velocity range, root key, trigger behavior, loop and crossfade, tuning, gain, pan, and envelope. A changed file or decoded slice fails source revalidation before rendering or SFZ compilation.

`earcrate_rack_binding_plan` schema version 1 performs deterministic compatibility checking and compiles every selected MIDI event to one exact rack zone. Missing key ranges, velocity layers, duration coverage, explicit assignments, racks, zones, or source identities remain unresolved. There is no runtime sample fallback.

The in-process rack renderer is the proof backend. It executes one-shot and gated voices, variable-rate pitch, pitch bend, loop crossfades, velocity and channel controls, source revalidation, per-track stems, and event-level execution receipts. Every selected event is classified as executed, truncated, or refused. A full render fails unless all events execute. Generated SFZ is portable object code for external samplers; it never replaces the EarCrate rack or binding ledger.

### Dependency and model governance

`third_party/components.lock.json` classifies adopted and evaluated code dependencies by version range, SPDX license, distribution class, source, and authority boundary. `third_party/models.lock.json` is separate and presently contains no bundled model. `scripts/oss_audit.py` refuses duplicate entries, mutable version labels, active components with incomplete metadata, approved models without a SHA-256, and a runtime MIDI codec that is not pinned in `requirements.txt`.

Runtime-discovered optional models may execute locally only when their exact bytes are hashed into the run receipt. They may not be bundled until the model ledger records a reviewed license, source, provider, status, and SHA-256.

## Commodity adoption queue

| Capability | Commodity | State | EarCrate authority retained |
|---|---|---:|---|
| SMF parse/write | Mido | landed | canonical event ledger and semantic hash |
| Fast symbolic operations | Symusic | evaluated | revision and event semantics |
| Neutral SoundFont reference | Symusic or FluidSynth | evaluated | execution receipt and accepted backend |
| Sample-rack contract and proof renderer | EarCrate RackRevision and binding ledgers | landed | source identities, zones, transforms, bindings, and execution |
| Portable sample-rack export | generated SFZ | landed | RackRevision and binding plan |
| External SFZ playback | liquidsfz | evaluated | accepted backend and execution receipt |
| Time/pitch transform | Signalsmith Stretch | evaluated | transform budget and chosen parameters |
| Sample-rate conversion | libsamplerate | evaluated | source and output identities |
| Source separation | allowlisted model host, retaining pinned Demucs baseline | evaluated | stem role, model lock, recombination and leakage gates |
| Beat/downbeat measurement | Beat This, existing deterministic fallback | evaluated | accepted tempo and meter maps |
| Polyphonic AMT | Basic Pitch | landed optional | quantization and arrangement |
| Monophonic F0 | torchcrepe | candidate | accepted note contour |
| Recording identity | Chromaprint, AcoustID, MusicBrainz | existing/candidate | evidence tier and human confirmation |
| Temporal alignment | Sync Toolbox and constrained CQT/chroma DTW | candidate | accepted mapping and sample relation |
| Arrangement search | OR-Tools CP-SAT | evaluated | variables, constraints, objective, selected solution, and timeout meaning |
| Hard invariant proof | Z3 | evaluated | invariant definitions and publication decision |
| Annotation/evaluation | JAMS and mir_eval | candidate | canonical project data and release interpretation |
| Large timeline canvas | PixiJS | candidate | typed edit commands and revision history |
| Cross-DAW export | MIDI, RPP, DAWproject, stems, optional AbletonOSC | candidate | canonical project revision |

## Distribution classes

`core` components may enter the default package after reproducibility and compatibility gates. `reviewed-weak-copyleft` components require an explicit linking and redistribution decision. `studio-optional` components run as user-installed external workers and cannot be required for canonical execution. `research-only` components may be benchmarked but cannot be bundled or used to support a release claim.

Inference code and checkpoint weights never inherit one another's license automatically. An MIT wrapper that can download an unreviewed checkpoint remains unable to bundle that checkpoint.

## Merge gates

1. MIDI parse, canonicalize, write, and reparse must preserve the semantic ledger.
2. Every selected note or sample trigger must execute exactly once or produce an explicit refusal.
3. Per-track neutral and rack stems must sum to their masters within the specified numerical tolerance.
4. A generated arrangement must remain intelligible through neutral instruments before crate binding or mastering.
5. Every sounded crate sample must resolve to a rack revision, source byte identity, decoded PCM slice identity, zone, and binding event.
6. A fixed event set on 10,000 declared tracks must render through only its occupied tracks and active voices.
7. Provider replacement may create new derived revisions but may not mutate historical canonical revisions.
8. Model execution requires an exact runtime model hash; bundled models additionally require a reviewed model-ledger entry.
9. A failed provider, solver, verifier, rack, binding, or renderer cannot silently substitute a degraded result.
10. Publication refuses changed source identities and any selected event missing from the executed-event ledger.

## Next vertical slices

The immediate next slice is a library-to-rack adapter. It should accept a `PerformanceDemand`, search only approved EarAtoms and stem regions, propose scored sample zones, and emit an unsealed rack draft plus complete candidate receipts. The existing rack sealer, binder, renderer, and SFZ compiler then remain unchanged. This is the plug-and-play boundary: search may improve indefinitely without changing execution semantics.

The following slice should decompose a reference mix into stems, measure its beat grid, run Basic Pitch on tonal stems, derive drum triggers from onset-role measurements, align every observation to the accepted grid, and render the resulting multitrack performance through neutral tones. Once that reconstruction passes, the library adapter can replace its neutral voices with approved crate material through the same rack contract.
