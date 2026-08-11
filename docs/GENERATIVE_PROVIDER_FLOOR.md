# EarCrate Generative Provider Floor

## Why this exists

EarCrate has built a private, content-addressed authority system around music work. It can bind exact recordings and models, execute providers across several nodes, preserve receipts, prepare blind review, and reproduce an explicit PerformanceScore without allowing the renderer to invent musical decisions. It has not yet proved one automatically recovered musical reference.

The open music-generation ecosystem has solved a different part of the problem. ACE-Step 1.5, SongGeneration 2, HeartMuLa, YuE, DiffRhythm, Muse, SongEcho, and related studios can generate coherent songs, covers, accompaniment, continuations, edits, and alternate takes. Their native outputs are generally audio files, stems, model sessions, or service records. They do not share one portable authority contract, and they do not automatically become safe material in an EarCrate project.

The Generative Provider Floor connects these two bodies. External systems become untrusted material producers. EarCrate remains the authority for exact inputs, provider and model identity, rights scope, output custody, human review, generated-material selection, and final PerformanceScore execution.

## What EarCrate contributes that the generators do not

| EarCrate organ | Contribution |
| --- | --- |
| Exact source and canonical PCM identity | Detects changed inputs and prevents a take from being attributed to different source bytes. |
| Provider Arcade and Homelab estate | Separates catalogued, installed, loadable, executed, benchmarked, auditioned, and adopted states. |
| Cross-node receipt model | Records node, GPU, environment, model and codec assets, request, output, and return identity without assuming one CUDA namespace. |
| Open Music Evidence Floor | Lets providers submit observations or materials without granting canonical musical authority. |
| PerformanceScore | Represents every selected source, clip, transform, ownership decision, and master operation in a portable exact score. |
| Deterministic renderer | Rebuilds an accepted score without selecting, repairing, substituting, or skipping musical material. |
| Blind owner review | Treats `reject_all`, control wins, ties, and abstention as complete evidence rather than forcing promotion of a least-bad take. |
| Scoped ReviewPatch | Changes only declared descendants and preserves unrelated evidence and historical decisions. |
| Privacy and publication membrane | Keeps private recordings, model paths, prompts, review maps, and credentials local while circulating source-free identities and outcomes. |

These organs do not compensate for weak generation. They make strong generation usable, comparable, reproducible, and governable.

## What the open generators contribute that EarCrate has not built

| Provider family | Native material operations |
| --- | --- |
| ACE-Step 1.5 | Text-to-music, cover/remix, repaint, complete/vocal-to-BGM, Lego layer addition, Extract, separation, retakes, extensions, reference conditioning, LoRA personalization. |
| SongGeneration 2 | Structured lyrics-to-song, reference prompt audio, BGM-only, vocal-only, and separate vocal/accompaniment output. |
| HeartMuLa | Long-form lyrics-and-tag-conditioned generation, HeartCodec, lyric transcription, audio-text retrieval, RL-aligned model variants. |
| YuE | Autoregressive full-song generation, continuation, single-track and dual-track in-context reference conditioning, LoRA. |
| DiffRhythm | Fast diffusion generation, editing, continuation, instrumental generation, reference conditioning. |
| Muse | Reproducible long-form token generation, segment-level structure control, released training and evaluation pipeline, Suno-teacher synthetic dataset. |
| SongEcho | Melody-preserving cover generation built as a specialist task. |
| Portable Music Server and community studios | Isolated environments, one model per worker, multi-GPU routing, installation management, REST APIs, take libraries, and local operator surfaces. |

EarCrate should consume these operations instead of recreating their inference engines.

## Authority objects

The floor introduces the following sealed objects:

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

A generation request must contain the provider and task mode, exact repository revision, exact model, codec, LoRA, and auxiliary asset hashes, an explicit seed, portable prompt and section conditions, portable source commitments by content identity, an output contract, and the private-use and rights scope.

A run receipt records the request, provider, node, GPU, exact model assets, outcome, and generated artifact identities. Raw commands, logs, paths, credentials, source bytes, and model bytes remain private. A successful run creates evidence, not acceptance.

A generated material object is an unreviewed candidate. It enters a PerformanceScore only after selection, using the generated artifact SHA-256 as a new source identity and retaining its generation receipt and material identities.

## Strategy-specific organisms

The factory does not ask one model to become the entire arrangement system. It compiles organisms for distinct operations.

### Source-preserving organism

```text
work identity and material census
→ section and phrase correspondence
→ exact source clips and bounded transforms
→ PerformanceScore
→ deterministic render
```

### Generative re-performance organism

```text
source vocal or melody condition
→ accompaniment, cover, or dual-track generator
→ alternate takes
→ hard signal and identity gates
→ owner selection
→ generated material source
→ PerformanceScore
```

### Hybrid organism

```text
accepted source-preserving score
→ repaint one region, generate one fill, add one layer, or replace one weak instrument
→ preserve all material outside the declared mask
→ owner review against incumbent
→ revised PerformanceScore
```

## Beggin Suno-bones campaign

`configs/generative_floor/beggin-suno-bones.v1.json` defines eleven operations:

```text
ACE-Step Complete and vocal-to-BGM around Frankie
ACE-Step or SongEcho melody-preserving cover oracle
ACE-Step or DiffRhythm transition repaint
ACE-Step Lego drum-room layer
SongGeneration BGM-only and dual-track generation
YuE dual-track ICL
HeartMuLa and Muse section-control comparators
```

The incumbent Reference Zero v5 score and a serious naive co-play remain controls. At most four strategy-diverse files reach the owner. Full-song and release permission remain closed.

## Local estate cutover

First run a source-free capability pass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_GENERATIVE_FLOOR.ps1 `
  -Workspace "S:\Temp\EarCrate\generative-floor\beggin-001" `
  -ProbeOnly
```

This does not install, download, or execute a model. It produces one probe receipt per provider, a generation campaign, and `LOCAL_NEXT_ACTIONS.md`.

For an installed provider, create one private override from the template. The override supplies the exact executable or loopback service adapter and exact local model-asset identities. It is never committed.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_GENERATIVE_FLOOR.ps1 `
  -Workspace "S:\Temp\EarCrate\generative-floor\beggin-002" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\ace-step-1.5.json" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\songgeneration-2.json" `
  -ProviderOverride "S:\Temp\EarCrate\private\generative-overrides\portable-music-server.json"
```

Each ready task receives an exact generation request. The request CLI refuses mutable model labels without pinned asset hashes and refuses implicit random seeds.

```powershell
python scripts\earcrate_generative_floor.py request `
  --provider ace-step-1.5 `
  --task-mode complete `
  --model-repository ace-step/ACE-Step-1.5 `
  --model-revision <exact-commit> `
  --asset "acestep-v15-xl-base.safetensors:<sha256>:<bytes>" `
  --asset "vae.safetensors:<sha256>:<bytes>" `
  --seed 1001 `
  --prompt-json '{"caption":"modern live rock band following an urgent rubato male lead; no replacement lead vocal"}' `
  --conditioning-json '[{"source_id":"four_seasons_vocals","container_sha256":"<sha256>","role":"lead_vocal"}]' `
  --output "S:\Temp\EarCrate\generative-floor\beggin-002\requests\ace-complete-1001.json"
```

Execution requires a private source-binding map, local adapter, node identity, and GPU identity. The output directory is immutable.

```powershell
python scripts\earcrate_generative_floor.py run `
  --catalog configs\generative_floor\providers.v1.json `
  --provider ace-step-1.5 `
  --probe "S:\Temp\EarCrate\generative-floor\beggin-002\probes\ace-step-1.5.probe.json" `
  --request "S:\Temp\EarCrate\generative-floor\beggin-002\requests\ace-complete-1001.json" `
  --adapter "S:\Temp\EarCrate\private\generative-overrides\ace-step-1.5.json" `
  --source-bindings "S:\Temp\EarCrate\private\generative-bindings\beggin.json" `
  --node-json "S:\Temp\EarCrate\private\estate\node.json" `
  --gpu-json "S:\Temp\EarCrate\private\estate\gpu-3090-a.json" `
  --output "S:\Temp\EarCrate\generative-floor\beggin-002\runs\ace-complete-1001"
```

A selected artifact is converted into an EarCrate generated-material candidate, not directly into an accepted score:

```powershell
python scripts\earcrate_generative_floor.py materialize `
  --receipt "...\generation-receipt.json" `
  --artifact-sha256 <sha256> `
  --role accompaniment `
  --musical-function "modern band following source vocal" `
  --strategy generative_reperformance `
  --output "...\materials\ace-complete-1001.json"
```

The frontier command deduplicates by audio identity, preserves the incumbent, and prefers distinct strategy families. Owner review remains a separate existing EarCrate authority.

## Commodity-host policy

The floor catalogs `portable-music-server` as a host, not as musical authority. Its isolated environments, worker lifecycle, multi-GPU API, install management, and output library are useful commodities. EarCrate must independently verify its exact revision, generated environment manifests, model assets, output identity, and real-GPU behavior. Its default mastering is disabled or treated as a separate declared transform when comparing raw model output.

The same rule applies to ACE-Step Studio, ComfyUI graphs, SongGeneration Studio, HeartMuse, YuE-UI, AudioLab, and future local hosts. EarCrate should harvest reliable setup and execution bodies while retaining its own request, receipt, material, review, and score contracts.

## Non-claims

This floor does not claim that any model is installed on the current estate, that a checkpoint license has been approved, that a private Beggin generation has run, that generated material is good, or that EarCrate has completed a reference. It makes those claims independently provable and lets the local estate run every serious open strategy under one authority model.
