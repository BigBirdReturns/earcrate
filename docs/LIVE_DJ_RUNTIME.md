# EarCrate live-DJ runtime contract

## Object

The live runtime performs from precompiled musical knowledge. MIDI is its exact teaching, planning, and conformance language. It is not the final sound source and it is not treated as a substitute for DJ technique.

The expensive stage is offline. Source decoding, identity, stem separation, beat and section measurement, note observation, approved-material search, multi-zone rack assembly, sample materialization, and source verification may consume a GPU and substantial time. Their outputs are immutable, content-addressed artifacts.

After compilation, the live path is CPU-local and has three execution domains:

1. The control thread owns `LiveSetState`, applies human commands, chooses a persona, evaluates typed techniques, plans a bounded horizon, and commits one legal phrase.
2. The phrase-render thread lowers that phrase to exact MIDI, binds every event to a sealed rack zone, verifies source identities, and prepares stereo float PCM.
3. The audio callback consumes a fixed single-producer/single-consumer ring, swaps prepared phrase buffers, and copies frames. Planning, library search, sample decode, and event binding are forbidden in that domain.

## Canonical chain

```text
SourceIdentity
  -> AnalysisObservation
  -> ApprovedMaterial
  -> LiveCrateAtlas
  -> LiveSetState
  -> HorizonPlan
  -> EngineStep
  -> MIDI Lowering
  -> RackBindingPlan
  -> RenderProgram
  -> PhraseBuffer
  -> AudioCallback receipt
```

Each transition has an independent hash. A later stage may refuse an earlier artifact but may not silently rewrite it.

## Personas

The initial policy set contains `club`, `girl_talk`, `pretty_lights`, and `minimal`. A persona is a numerical policy, not a prompt. It controls phrase length, planning horizon, target energy, density, risk, maximum layers, source turnover, category priorities, technique weights, and objective weights.

Persona changes are queued until a phrase boundary legal for both the current and requested policies. Switching persona does not rescan or reanalyze the library.

## Techniques

The typed operator set contains:

- blend
- hard cut
- loop extend
- drop to floor
- foreground swap
- tease
- build layers
- breakdown
- echo out
- drum rebuild
- sample chop
- layer accumulation

Each operator has explicit preconditions, selected layers, parameterized commands, risk, velocity behavior, energy change, deterministic identity, refusal reasons, and execution outcomes. The complete registry is tested independently from persona planning.

## Controls

The live state accepts persona changes, technique enable and disable, forced techniques, energy, density, risk, maximum layers, holds, releases, and source-pattern skips. Controls are immutable state transitions. The next phrase is replanned from the resulting state, while prior decisions remain unchanged.

## Measured activity

`LiveActivityRecorder` instruments the actual planning, library-search, sample-decode, binding, pattern-scan, material-scan, and CPU-command call sites. Every event is attributed to one execution domain: offline compilation, control, phrase rendering, CPU execution, or audio callback.

The offline crate receipt must contain a positive measured library-search count and the measured number of candidate materials scanned. A runtime receipt derives its no-scan result from the activity delta after compilation rather than from a constant. Phrase receipts must contain positive measured planning, binding, and sample-decode counts outside the callback.

The callback purity gate invokes the real planner while the activity domain is set to `audio_callback`. Instrumentation must count that call and raise `LiveCallbackPurityError`. This negative gate proves that the zero callback planning count is produced by an active detector, rather than by a literal field.

## Precompiled crate

`LiveCrateAtlas` binds an exact source performance and its live material atlas to sealed multi-zone sample racks built from approved library atoms. The slow candidate search and sample extraction happen once. Runtime sessions revalidate those rack sources. Their measured activity delta must contain no additional library-search or material-scan call.

## Audio boundary

A prepared phrase must be sample-identical to the existing exact rack renderer. Its receipt contains the source state, next state, persona, techniques, MIDI identity, binding identity, render-program identity, selected and executed event counts, PCM identity, source-verification result, and measured activity delta.

`LiveAudioCallback` consumes only validated prepared buffers. Queue overflow is a refusal. Missing prepared audio produces silence and increments an underrun counter. The queue and completion history are fixed-capacity rings. The callback hot path has no lock, no unbounded append, no collection comprehension, and an explicit call allowlist enforced by a source-structure gate. Its planning, library-search, sample-decode, and binding values come from the shared measured activity recorder.

`LiveSoundDevicePlayer` is an optional thin host for `sounddevice`. EarCrate's deterministic planning, crate compilation, phrase rendering, and conformance tests do not require that package or an audio device.

## Concurrency

Planner scoring is argument-pure. Requested risk is passed explicitly into candidate scoring; no module-level mutable scoring state remains. A concurrent gate runs low-risk and high-risk plans repeatedly through a thread pool and requires every result to match its corresponding sequential result.

The live `*_fix.py` monkey-patch modules have been removed. Planner, runtime, capability, provenance-ordering, and command-identity behavior live in their owning modules, so correctness does not depend on import order.

## Publication gates

A live-runtime publication requires:

- deterministic plan, state, MIDI, program, and execution hashes;
- complete note, controller, technique-command, rack-binding, and render accounting;
- independent gates for all named techniques and persona policies;
- phrase-safe persona and control changes;
- concurrent planner equality with sequential reference results;
- long-set planning with bounded sparse runtime operations;
- positive measured offline search and zero measured runtime library search;
- positive measured phrase planning, binding, and sample loading;
- prepared PCM equal to the exact rack-render reference;
- active refusal when the real planner is invoked in the callback domain;
- no callback lock, unbounded completion allocation, or non-allowlisted call;
- generated single-file command execution;
- source-mutation refusal;
- retained gate and package-verifier ledgers.

Synthetic gates validate the mechanics and the instrumentation. A real private-library acceptance run and a real audio-device latency/underrun receipt remain separate venue-specific evidence and must not be inferred from CI.
