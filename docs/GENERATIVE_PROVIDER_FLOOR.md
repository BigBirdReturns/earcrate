# EarCrate Generative Provider Floor

## Purpose

EarCrate has built a private, content-addressed authority system around musical computation. It can bind exact recordings and models, execute providers across multiple Estate nodes, preserve receipts, prepare blind review, and reproduce an explicit PerformanceScore without allowing the renderer to invent decisions. It has not yet completed an automatically recovered musical reference.

The open ecosystem has built the learned performance organs EarCrate did not train: ACE-Step 1.5, SongGeneration 2, HeartMuLa, YuE, DiffRhythm, Muse, SongEcho, MIDI-SAG, and related local studios can generate songs, covers, accompaniment, continuations, repaints, separate tracks, and alternate takes. Their native outputs do not share one portable authority contract and do not automatically become safe material in an EarCrate project.

The Generative Provider Floor combines these bodies. External systems are untrusted material producers. EarCrate remains the authority for exact input, repository, checkpoint, codec, host, node, GPU, seed, rights, output, review, generated-material, and PerformanceScore identities.

## What EarCrate contributes

| EarCrate organ | Contribution |
| --- | --- |
| Exact source and canonical PCM identity | Detects changed inputs and prevents attribution to different bytes. |
| Provider Arcade and Homelab Estate | Separates catalogued, installed, loadable, executed, benchmarked, auditioned, and adopted states. |
| Cross-node receipts | Records provider, host, node, GPU, environment, model assets, request, output, and return identity without assuming one CUDA namespace. |
| Open Music Evidence Floor | Lets a provider submit observations or material without receiving canonical musical authority. |
| PerformanceScore | Represents every selected source, clip, transform, ownership decision, and master operation in a portable exact score. |
| Deterministic renderer | Rebuilds the score without selecting, repairing, substituting, tiling, or skipping material. |
| Blind owner review | Treats control wins, ties, `reject_all`, and abstention as complete evidence. |
| Scoped ReviewPatch | Changes declared descendants while preserving unrelated evidence and history. |
| Privacy and publication membrane | Keeps source audio, model paths, prompts, review maps, and credentials local while circulating source-free identities. |

These organs do not compensate for weak generation. They make strong generation comparable, reproducible, editable, and governable.

## What the external organs contribute

| Provider family | Native material operations |
| --- | --- |
| ACE-Step 1.5 | Text-to-music, cover/remix, repaint, Complete/vocal-to-BGM, Lego, Extract, separation, retakes, extensions, reference conditioning, LoRA. |
| SongGeneration 2 | Structured lyrics-to-song, audio prompts, BGM-only, vocal-only, and separate vocal/accompaniment output. |
| HeartMuLa | Long-form lyrics-and-tag-conditioned generation, HeartCodec, lyric transcription, retrieval, and aligned model variants. |
| YuE | Autoregressive generation, continuation, single-track and dual-track in-context conditioning, LoRA. |
| DiffRhythm | Fast diffusion generation, editing, continuation, instrumental generation, and reference conditioning. |
| Muse | Reproducible long-form token generation, section control, released training/evaluation pipeline, and Suno-teacher synthetic data. |
| SongEcho | Melody-preserving cover generation as a specialist operation. |
| MIDI-SAG | Vocal beat tracking, vocal-to-MIDI transcription, chord harmonization, section prompts, and Stable-Audio-based backing generation. |
| Portable Music Server and studios | Isolated environments, model installation, worker lifecycle, multi-GPU routing, APIs, output libraries, and operator surfaces. |

EarCrate consumes these operations instead of reimplementing their inference engines.

## Authority objects

```text
earcrate_generation_provider_catalog
earcrate_generation_provider_probe
earcrate_generation_request
earcrate_generation_campaign
earcrate_generation_run_receipt
earcrate_generated_material
earcrate_generation_frontier
earcrate_generation_public_projection
```

A request contains the provider and task mode, exact repository revision, exact model, codec, LoRA, and auxiliary asset hashes, explicit seed, portable prompt and section conditions, portable source commitments, output contract, and private-use and rights scope.

A run receipt records the request, provider, execution host, node, GPU, exact assets, outcome, and generated artifact identities. Raw commands, logs, paths, credentials, source bytes, model bytes, and private service responses remain local. A successful run creates evidence, not acceptance.

A generated-material object is an unreviewed candidate. It enters a PerformanceScore only after selection and retains its generation request, receipt, provider, model, seed, and artifact identities.

## Strategy-specific organisms

The factory does not ask one model to become the entire arrangement system.

### Source-preserving

```text
work identity and material census
→ section and phrase correspondence
→ exact source clips and bounded transforms
→ PerformanceScore
→ deterministic render
```

### Generative re-performance

```text
source vocal, melody, section, or style condition
→ accompaniment, cover, or dual-track generation
→ alternate takes
→ hard identity and signal gates
→ owner selection
→ generated material
→ PerformanceScore
```

### Compositional accompaniment

```text
source vocal
→ vocal beat tracking
→ vocal MIDI
→ chord harmonization
→ section-controlled backing generation
→ intermediate and final receipts
→ owner selection
→ PerformanceScore
```

### Hybrid repair

```text
accepted source-preserving score
→ repaint one region, generate one fill, add one layer, or replace one weak instrument
→ preserve material outside the mask
→ owner review against incumbent
→ revised PerformanceScore
```

## Beggin Suno-bones campaign

`configs/generative_floor/beggin-suno-bones.v1.json` defines twelve operations:

```text
ACE-Step Complete and vocal-to-BGM around Frankie
ACE-Step or SongEcho cover oracle
ACE-Step or DiffRhythm transition repaint
ACE-Step Lego drum-room addition
SongGeneration BGM-only and dual-track output
YuE dual-track ICL
HeartMuLa and Muse section-control comparison
DiffRhythm fast-edit control
Muse Suno-teacher-distilled comparison
MIDI-SAG compositional vocal-to-backing pipeline
```

The Reference Zero v5 score and a serious naive co-play remain controls. At most four audio-distinct, strategy-diverse files reach the owner. Full-song and release permission remain closed.

## Local Estate cutover

A source-free capability pass performs no install, download, or generation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_GENERATIVE_FLOOR.ps1 `
  -Workspace "S:\Temp\EarCrate\generative-floor\beggin-001" `
  -ProbeOnly
```

It writes one provider probe, the bounded campaign, and `LOCAL_NEXT_ACTIONS.md`.

Private overrides supply an exact executable or loopback service adapter plus exact local model assets. When a model runs through a commodity host, the runner probes the host first and inserts the sealed host-probe identity into the model override inside the private workspace. The operator does not copy authority hashes by hand.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_GENERATIVE_FLOOR.ps1 `
  -Workspace "S:\Temp\EarCrate\generative-floor\beggin-002" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\portable-music-server-host.json" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\ace-step-1.5-via-host.json" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\midi-sag.json"
```

Every ready task then receives an exact request. The CLI refuses mutable model labels without pinned assets and refuses an implicit random seed. Execution requires private source bindings, local adapter, node identity, and GPU identity. Output directories are immutable.

A selected artifact is converted into an EarCrate generated-material candidate, never directly into an accepted score. The frontier deduplicates by audio identity, retains the incumbent, and prefers distinct strategy families. Existing owner-review authority decides the result.

## Commodity-host policy

Portable Music Server is catalogued as a host rather than musical authority. EarCrate independently verifies the host revision, its sealed probe, the requested model ID, environment installation, weight installation, exact local assets, output identity, and real-GPU execution. A healthy gateway alone does not establish model readiness.

The current server implementation accepts `model_params` and returns inline `audio_base64` plus `entry_id`; the EarCrate adapter is tested against that executable contract. Host documentation is treated as a witness, not execution authority. Default mastering is disabled or retained as a separate declared transform during model comparison.

The same rule applies to ACE-Step Studio, ComfyUI graphs, SongGeneration Studio, HeartMuse, YuE-UI, AudioLab, and future hosts. EarCrate harvests reliable setup and execution bodies while retaining its own request, receipt, material, review, and score contracts.

## Non-claims

This floor does not claim that a model is installed on the Estate, that checkpoint licenses have been approved, that a private Beggin generation has run, that generated material is good, or that EarCrate has completed a reference. It makes those claims independently testable and allows every serious open strategy to enter through one authority model.
