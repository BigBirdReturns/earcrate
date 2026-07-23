# EarCrate local mix-machine sequence

## Product boundary

EarCrate has two related deliverables.

The immediate deliverable is a player-piano proving ground. It must preserve, inspect, execute, compare, and substitute a complete multitrack performance without hiding weak arrangement behind production. MIDI is the portable event format; EarCrate ledgers are the authority.

The longer deliverable is a local library instrument and mix machine. It must discover playable material in an owned library, reconstruct arrangement evidence from references, compose new performances under explicit constraints, bind performances to approved material, and render exact audio with no cloud service and no generative model in the authority path.

Learned local analyzers such as Basic Pitch, Demucs, or Beat This may remain optional measurement providers. The complete system must also admit manually supplied stems, MIDI, beat grids, and annotations, and every provider result must be replaceable. No provider is allowed to select the final arrangement or silently change an event.

## One authority chain

```text
SourceIdentity
    -> AnalysisObservation
    -> ApprovedMaterial
    -> PerformanceRevision
    -> PerformanceDemand
    -> LibraryRackProposal
    -> RackRevision
    -> RackBindingPlan
    -> RenderProgram
    -> ExecutionLedger
    -> Audio + stems + DAW exports
```

A later stage may refuse an earlier decision, but it may not mutate it. Every arrow is content-addressed and receipt-bearing.

## Phase 0: source and workspace truth

**Purpose.** Ensure tests, providers, and background jobs cannot silently point at another workspace or source generation.

**Required artifacts.** Canonical workspace root, source file identity, decoded PCM identity, source generation, provider cache root, append-only mutation journals.

**Merge gates.** Every pointer read location is sandboxed in tests; legacy pointer adoption is explicit; source mutation invalidates derived material; no test can write a user's pointer; no runtime pointer is committed.

**Current state.** A local pointer fix has been reported at `895689f` but is not yet visible on the remote branch. It should land before this stack is rebased onto the user checkout.

## Phase 1: exact MIDI performance authority

**Purpose.** Prove that EarCrate can preserve and execute a finished arrangement before it attempts to write one.

**Required artifacts.** `earcrate_midi_ledger`, tempo map, controller curves, note spans, neutral render program, event execution ledger.

**Merge gates.** Parse/write/reparse semantic equality; every note executed, truncated, or refused; type-2 asynchronous sequences refuse without selection; stems sum to master; a fixed event set costs approximately the same on 100 and 10,000 declared tracks.

**Current state.** Landed in PR #31 and exercised against the 5,925-note Queen fixture.

## Phase 2: performance demand and sample instruments

**Purpose.** Convert a finished performance into a library-independent specification of what must be played.

**Required artifacts.** `earcrate_performance_demand`, `earcrate_rack_revision`, SFZ object code, `earcrate_rack_binding_plan`, rack render program, rack execution ledger.

**Merge gates.** Every selected event binds exactly once or remains unresolved; source identities are revalidated; incomplete bindings cannot render; one-shot, gate, loop, controller, bend, and stem-sum behavior are exact.

**Current state.** Landed in PR #31.

## Phase 3: multi-zone approved-library substitution

**Purpose.** Keep a musically safe transpose ceiling while covering wide melodic lanes with several source roots.

**Mechanism.** Partition each pitched lane into the minimum number of non-overlapping note bands. Rank approved atoms per band. Choose a bounded deterministic combination that optimizes role fit, timbral fit, duration, loopability, local transposition, coherence, and source reuse. Seal the result as one logical rack with multiple zones.

**Merge gates.** A 55-semitone lane resolves at a per-zone limit of 18 semitones; the same lane refuses with one allowed zone; measured roots are never re-octaved to fabricate coverage; every realized event remains inside the budget; reversed atom input produces the same proposal hash.

**Current state.** Implemented in stacked PR #32.

## Phase 4: arrangement anatomy from MIDI

**Purpose.** Turn an existing MIDI arrangement into measurable structure that can be compared, copied, varied, and regenerated.

**Required artifacts.** `ArrangementAnatomy` containing bars, meter, tempo segments, track roles, active-layer count, event density, entrances and exits, register occupancy, velocity curves, rhythmic cells, motifs, harmonic rhythm, section boundaries, transition events, and recurrence relations.

**Method.** Use deterministic event statistics, self-similarity, change-point scoring, and bounded dynamic programming. Section names are optional labels over measured boundaries. The measurements must remain useful when labels are absent.

**Merge gates.** Repeated MIDI input produces byte-identical anatomy; every source event maps to an anatomy cell; section boundaries are stable under harmless track ordering changes; neutral rerender remains unchanged; extracted layer counts and entrances reproduce the source performance.

## Phase 5: reference-audio evidence bundle

**Purpose.** Reconstruct the arrangement evidence of a reference mix without letting transcription own the answer.

**Inputs.** A reference audio file plus any combination of manually supplied stems, local source separation, a known MIDI file, beat annotations, or provider observations.

**Required artifacts.** `ReferenceBundle` containing source PCM identity, accepted beat/downbeat/meter map, stem identities, note and onset observations, drum triggers, chord observations, aligned event candidates, confidence and disagreement ledgers, and a neutral multitrack reconstruction.

**Provider order.** Manual or authored data first; deterministic DSP second; optional local learned providers third. Provider disagreement remains visible. EarCrate accepts one grid and one event set through explicit rules or human edits.

**Merge gates.** Every accepted event cites an observation; raw provider times remain recoverable; alignment error is recorded; neutral reconstruction preserves entrances, exits, density, phrase lengths, rhythm, and pitch contour; provider removal can rebuild a new derived bundle without changing the old one.

## Phase 6: playable library index

**Purpose.** Convert the owned library into a durable inventory of capabilities rather than a pile of files.

**Required material classes.** Full recordings, stems, phrase loops, one-shots, sustained tones, vocal phrases, transitions, impacts, textures, and silence or tail regions.

**Required measurements.** Source and slice identity, timing grid, root pitch and confidence, key, tempo, duration, loop points, transient class, envelope, spectral shares, harmonic/percussive balance, loudness, register, polyphonic suitability, role scores, provenance, human approval, and provider receipts.

**Storage.** SQLite remains authoritative for identities, judgments, relationships, and revisions. Derived PCM and model output remain in the content-addressed ArtifactStore. Large evaluation tables may use Parquet or DuckDB, but never replace the canonical ledger.

**Merge gates.** Indexing is resumable and done-once; source mutation invalidates only dependent artifacts; rejected material never enters automatic substitution; measured root pitch prevents false octave inference; an atom can explain exactly which demands it can and cannot satisfy.

## Phase 7: demand-to-library solving

**Purpose.** Find a complete replacement ensemble for an arrangement.

**Mechanism.** Candidate retrieval narrows the pool; deterministic scoring produces receipts; CP-SAT or bounded beam search chooses racks and zones under hard constraints. Search variables include role, pitch coverage, velocity coverage, duration, loop support, source diversity, timbral continuity, polyphony, collision budgets, and transform limits.

**Required outputs.** Ranked alternatives per slot, complete solution candidates, unsatisfied-core diagnostics, selected material, and a sealed binding. A timeout means “best sealed feasible solution found under this budget,” never “optimal.”

**Merge gates.** Search order and worker count cannot change a sealed deterministic mode; every objective term is recorded; no incomplete solution reaches render; manual locks survive re-search; replacing the retriever cannot alter historical bindings.

## Phase 8: deterministic arrangement grammar

**Purpose.** Prove that the machine can arrange rather than merely substitute.

**Corpus.** MIDI arrangements and accepted ReferenceBundles. The system extracts distributions and conditional tables for section length, role occupancy, motif recurrence, harmonic rhythm, drum density, fills, silence, transitions, register, and energy.

**Authority.** The grammar is versioned data. Generation uses seeded finite-state machines, constraint propagation, dynamic programming, beam search, or CP-SAT. It does not use prompt generation or a cloud model.

**Required output.** A `PerformanceRevision` that renders through neutral instruments before any crate binding.

**Merge gates.** Same grammar, seed, inputs, and policy produce the same performance hash; all hard constraints hold; every decision carries alternatives and scores; the neutral render exposes the complete form; library substitution is a later compiler stage.

## Phase 9: mix and render compiler

**Purpose.** Execute the selected performance and material as production audio without changing the composition.

**Required capabilities.** Sparse voice scheduler, independent buses, sends, sidechain envelopes, fades, filters, EQ, dynamics, time and pitch transforms, automation, stem rendering, loudness and true-peak measurements, and exact mastering actions.

**Policy.** Simple in-process renderers remain the proof backends. External samplers, CLAP/VST hosts, Ableton, or Reaper are optional execution targets. A DAW export may not be the only executable form.

**Merge gates.** Every selected event executes; per-track and per-bus decomposition reconciles to the master; transform budgets hold; source identities are current; publication refuses gate failures; rerender is deterministic under a pinned backend.

## Phase 10: workbench and DAW interchange

**Purpose.** Make ten thousand logical tracks manageable without turning the UI into the source of truth.

**Mechanism.** Virtualized sparse timeline, typed commands, immutable revisions, viewport queries, waveform and piano-roll tiles, audition, locks, branch comparison, and receipt inspection.

**Exports.** MIDI, SFZ, stems, RPP, DAWproject, and optional AbletonOSC materialization. Export files are revision-bound object code.

**Merge gates.** View cost depends on visible events; edits create child revisions; undo and redo preserve history; rendered output corresponds to the displayed revision; save/load/render cannot silently recompose.

## Phase 11: evaluation and regression corpus

**Purpose.** Make “better” measurable without collapsing taste into one score.

**Corpora.** Synthetic adversarial fixtures, Queen MIDI, the Empire reference, curated MIDI covers, hand-annotated library atoms, and accepted local renders.

**Metrics.** Event preservation, beat and onset alignment, section boundaries, motif recurrence, layer count, register collisions, groove, source separation, note transcription, demand coverage, transform cost, execution completeness, stem reconciliation, loudness, and human judgments.

**Merge gates.** Every release retains machine-readable receipts; provider upgrades run against frozen answer keys; no threshold is changed solely to turn a failing reference green; human listening notes cite the exact revision and render identity.

## Build order from the present branch

1. Land and rebase the complete pointer fix.
2. Merge PR #31 as the MIDI, rack, and execution substrate.
3. Merge PR #32 after the real 216,034-atom run proves 16/16 coverage at the default per-zone limit.
4. Implement ArrangementAnatomy and prove it on Queen and several strong MIDI covers.
5. Build ReferenceBundle with manual inputs and deterministic DSP before adding optional local providers.
6. Add measured root pitch, loop-point, and one-shot capability indexing to the library.
7. Move complete demand solving to a deterministic solver while retaining the multi-zone beam as the reference baseline.
8. Generate the first neutral PerformanceRevision from a versioned grammar.
9. Bind that generated performance to the library and compare neutral, rack, stem, and DAW renders.
10. Move the workbench onto the unified revision and receipt chain.

## Definition of the two goals

The immediate MIDI goal is complete when EarCrate can import, analyze, edit, generate, substitute, render, and export a multitrack performance while accounting for every event and preserving a musically conservative per-zone transform budget.

The ultimate local mix-machine goal is complete when a fresh owned library can be indexed into approved capabilities, a reference or grammar can produce an inspectable PerformanceRevision, the solver can bind it completely or explain why it cannot, and the local renderer can produce reproducible audio and stems without a cloud service or hidden generative decision.
