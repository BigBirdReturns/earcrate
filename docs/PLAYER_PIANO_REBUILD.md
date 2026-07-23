# EarCrate Player-Piano Rebuild

## Decision

EarCrate is rebuilt around a **playable causal score** and a **composable musical authority**.

Audio analysis, source search, MIDI codecs, sample racks, rendering, and live playback are mechanisms. They may propose evidence or execute a selected performance. They do not decide what the music is.

The authority chain is:

```text
source identity and PCM
  -> accepted musical evidence
  -> causal score state
  -> hard musical laws
  -> independent musical equations
  -> player-piano program graph
  -> proof-carrying musical events
  -> exact MIDI / rack / audio lowering
  -> reconstruction residual and execution receipts
```

The system has no note-level fallback. When no legal continuation exists it backtracks, simplifies, sustains, rests, closes, or refuses. It never emits an arbitrary note to keep the pipeline moving.

## Thesis

A player piano is not one aesthetic equation. It is an executable arrangement of:

- a musical state;
- a harmony and meter context;
- hard laws;
- an obligation ledger;
- independent equations;
- operator precedence;
- voice order;
- a bounded search policy;
- a lexicographic objective program;
- an instrument or sample realization backend.

The same equations can be wired into multiple player pianos. A conservatory program can prioritize voice-leading and closure. An electro-soul program can prioritize source evidence, register drama, tension color, and orchestral separation. Both remain inside the same declared constitution.

This separates **constitution** from **personality**:

- Constitution defines admissibility. An event outside it does not receive a low score; it is not a legal candidate.
- Personality determines how a player piano explores the remaining valid state space.

Dissonance, delayed resolution, metric displacement, register rupture, sample chopping, interruption, and abrasion remain possible when represented by typed operators with reachable consequences. Uncomprehended accidents do not.

## Why the current stack is not enough

The stacked score/MIDI/runtime work supplies a strong body:

| Stack | Organ |
|---|---|
| PR 29 | Immutable project revisions, decisions, lowering, render, mastering, export |
| PR 31 | Exact MIDI ledger, performance demand, sealed racks, event-complete execution |
| PR 32 | Deterministic multi-zone realization under fixed transpose limits |
| PR 33 | Derived arrangement anatomy and complete MIDI-event accounting |
| PR 34 | Deterministic pattern arranger |
| PR 35 | Accepted reference evidence compiled into exact MIDI bundles |
| PR 36 | Receding-horizon live planning and callback-safe performance |

The existing arranger and live planner are mechanically exact but musically under-specified. Their authority functions are dominated by fixed form, energy, layer count, role coverage, continuity, novelty, risk, source diversity, reuse, and deterministic jitter. Those dimensions can select a clean generic arrangement without understanding:

- chord function;
- tendency tones;
- voice-leading destinations;
- bass function;
- counterpoint;
- phrase grammar;
- motif obligations;
- source-specific musical identity;
- causal reconstruction debt.

That is the same category error exposed by the rejected full-length Pretty Lights MIDI: excellent receipts can surround the wrong composition.

## First composer-cortex slice

The `earcrate.music` package establishes the missing authority without replacing the existing stack in this change.

### Canonical musical state

`MusicHarmonyFrame`, `MusicEvent`, `MusicObligation`, and `MusicState` are deterministic, content-addressable objects. A musical event records its voice, role, timeline, pitch or gestural status, operator, source evidence, and metadata. An obligation records the destination set and time window created by a tension-bearing event.

### Hard laws

The first constitution contains independent laws for:

1. timeline validity;
2. instrumental pitch range;
3. source grounding;
4. same-voice overlap;
5. voice-leading limits and typed exceptions;
6. obligation discharge and overdue debt;
7. harmony, meter, and tendency-tone reachability;
8. bass function;
9. register collision;
10. phrase closure.

A leading tone, scale-degree four, passing tone, chromatic approach, or extension is legal only when a valid destination remains reachable in the declared future. Terminal tension cannot be constructed accidentally.

### Independent equations

The first equation library measures:

- source evidence;
- harmonic stability;
- voice-leading quality;
- metrical placement;
- resolution value;
- motif identity;
- register fit;
- orchestral separation;
- novelty;
- repetition control;
- tension color.

These equations are not summed into one universal taste score. A `PlayerPianoProgram` arranges them into ordered objective stages. Candidate comparison is lexicographic: a later preference cannot compensate for failure on an earlier musical priority.

### Proof-carrying search

The composer uses bounded beam search over only law-admissible candidates. Every committed event retains:

- the exact program and program hash;
- the prior state hash;
- one verdict per law;
- created and discharged obligations;
- equation terms;
- the lexicographic rank vector;
- the resulting state hash;
- source evidence identifiers.

Search exhaustion raises `MusicNoLegalContinuation`. There is no rescue note.

## Pretty Lights primitive gate

The included source-conditioned fixture uses the harmonic language measured in the first 30 seconds of the Pretty Lights remix: F-sharp 6/9 moving to B major 9. It compiles eleven coordinated voices and forty-four proof-carrying events:

- sub bass;
- bass articulation;
- three harmony voices;
- upper line;
- counterline;
- kick;
- snare;
- hats;
- effects.

The fixture is not claimed as a new transcription of the master. It is the kernel gate proving that source evidence, a two-block harmonic path, independent ensemble voices, deterministic search, and zero terminal obligations can coexist under the new authority.

The real acceptance target remains the uploaded 30-second master and its exact evidence bundle. The next cutover must route that bundle through this kernel and prove that the resulting score improves source-event correspondence without returning to the overly narrow, cute proof grammar.

## Buffalo harvest

`earcrate.music.heritage` is an executable transplant manifest. Every historical organ is assigned one disposition:

- **preserve** — already correct and remains authoritative in its domain;
- **adapt** — useful mechanism, moved beneath the musical authority;
- **demote** — retained as a provider or diagnostic, no longer allowed to decide composition;
- **retire** — incompatible fallback or duplicate scaffold.

Key rulings:

| Historical organ | Ruling |
|---|---|
| Immutable project revisions and receipts | Preserve as causal-score lineage |
| Beat-state and role activity | Adapt into musical evidence |
| Material regions and EarAtoms | Adapt into playable-cause candidates |
| Varispeed lattice and transform budgets | Preserve as embodiment feasibility |
| Typed DJ transitions | Adapt into formal operators with obligations |
| TasteSpec | Adapt into program topology and parameters |
| Reference answer keys and evidence bundles | Preserve as empirical and causal evidence |
| Exact MIDI and sealed racks | Preserve as lowering and realization backends |
| Arrangement anatomy | Preserve as derived evidence |
| One-bar pattern arranger | Demote beneath the constitution |
| Live receding-horizon runtime | Preserve as executor of compiled musical knowledge |
| Audio judge | Adapt into reconstruction residual routing |
| Floor-safe rescue | Retire; it violates no-fallback composition |
| Aliased two-world mode | Retire unless rebuilt as a genuinely distinct program |
| Coarse waveform correlation | Demote to a diagnostic |
| Workflow-only integration snapshot | Retire after the real stack supersedes it |

The manifest is validated and content hashed so future rebuilds cannot silently lose or resurrect an organ.

## Cutover sequence

### 1. Land the musical authority

Land `earcrate.music` as a pure package with no MIDI, provider, database, or renderer dependency. Preserve package and deterministic single-file execution.

### 2. Compile evidence into causal-score inputs

Extend the reference bundle and analysis adapters to produce:

- harmony frames with confidence and provenance;
- voice observations;
- onset and articulation observations;
- source and sample identity anchors;
- phrase and form boundary proposals;
- accepted future event positions;
- register and instrument constraints.

Providers remain observers. Accepted revisions remain explicit and hashed.

### 3. Put the pattern arranger under the constitution

PR 34's pattern bank remains useful. Its bar candidates become source-pattern proposals. A player-piano program must prove their harmonic, contrapuntal, motivic, formal, and obligation consequences before selection. The fixed seven-section form becomes one form provider, not the universal composer.

### 4. Put live planning under the same authority

PR 36 keeps receding-horizon planning, controls, sealed racks, callback purity, and execution accounting. Its operator applications must produce candidate musical-state transitions and receive law proofs before entering the beam. The current energy/density/risk score becomes a personality stage below the constitution.

### 5. Add orchestral inverse rendering

A causal score compiles against interchangeable playable acoustic bases:

- General MIDI;
- sealed sample racks;
- orchestra or ensemble dictionaries;
- synthesizer parameter graphs;
- literal source-sample triggers.

Render, compare against the reference, classify the residual, and route unexplained tonal, transient, low-frequency, formant, and broadband evidence back to the appropriate observer or musical law.

### 6. Promote the 30-second proof into a publication gate

The gate must compare three candidates:

1. the source-conditioned player-piano reconstruction;
2. the rejected generic full-length arranger excerpt;
3. deliberately damaged negative controls.

The new system must:

- prefer the source-conditioned causal score;
- reject terminal obligations, illegal bass, role collisions, copied lanes, and misplaced source cues;
- preserve exact execution and source identity;
- produce distinct valid outputs from different player-piano programs;
- show that personality expansion reduces cuteness without lowering the constitution.

## Non-goals of this slice

This change does not yet:

- replace the PR 34 or PR 36 production planners;
- claim a complete universal theory of musical value;
- claim exact Pretty Lights sample reconstruction;
- merge or close any stacked pull request;
- add a rescue path;
- give waveform similarity authority over musical evidence.

It establishes the first durable primitive required to do those integrations correctly: **a portable, executable arrangement of musical laws that can prove each committed move before a renderer is allowed to play it.**
