# EarCrate live-DJ runtime contract

## Object

The live runtime performs from precompiled musical knowledge. MIDI is its exact teaching, planning, and conformance language. It is not the final sound source and it is not treated as a substitute for DJ technique.

The expensive stage is offline. Source decoding, identity, stem separation, beat and section measurement, note observation, approved-material search, multi-zone rack assembly, sample materialization, and source verification may consume a GPU and substantial time. Their outputs are immutable, content-addressed artifacts.

After compilation, the live path is CPU-local and has three execution domains:

1. The control thread owns `LiveSetState`, applies human commands, chooses a persona, evaluates typed techniques, plans a bounded horizon, and commits one legal phrase.
2. The phrase-render thread lowers that phrase to exact MIDI, binds every event to a sealed rack zone, verifies source identities, and prepares stereo float PCM.
3. The audio callback swaps queued phrase buffers and copies prepared frames. It does not plan, search the library, decode samples, bind events, call a model, access the network, or access a cloud service.

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

## Precompiled crate

`LiveCrateAtlas` binds an exact source performance and its live material atlas to sealed multi-zone sample racks built from approved library atoms. The slow candidate search and sample extraction happen once. Runtime sessions revalidate those rack sources and report zero full-library scans.

## Audio boundary

A prepared phrase must be sample-identical to the existing exact rack renderer. Its receipt contains the source state, next state, persona, techniques, MIDI identity, binding identity, render-program identity, selected and executed event counts, PCM identity, and source-verification result.

`LiveAudioCallback` consumes only validated prepared buffers. Queue overflow is a refusal. Missing prepared audio produces silence and increments an underrun counter. The callback receipt pins planning, library-search, sample-decode, and binding counts at zero.

`LiveSoundDevicePlayer` is an optional thin host for `sounddevice`. EarCrate's deterministic planning, crate compilation, phrase rendering, and conformance tests do not require that package or an audio device.

## Publication gates

A live-runtime publication requires:

- deterministic plan, state, MIDI, program, and execution hashes;
- complete note, controller, technique-command, rack-binding, and render accounting;
- independent gates for all named techniques and persona policies;
- phrase-safe persona and control changes;
- long-set planning with bounded sparse runtime operations;
- no full-library scan after crate compilation;
- prepared PCM equal to the exact rack-render reference;
- no planning, search, decode, binding, GPU, network, or cloud activity inside the audio callback;
- generated single-file command execution;
- source-mutation refusal;
- retained gate and package-verifier ledgers.

Synthetic gates validate the mechanics. A real private-library acceptance run and a real audio-device latency/underrun receipt remain separate venue-specific evidence and must not be inferred from CI.
