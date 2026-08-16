# Reference Zero: prove one performance before inferring one

EarCrate currently has strong source custody, provider execution, cross-node GPU work, sealed receipts, and blind review. It does not yet have an owner-accepted musical reference. Reference Zero changes the order of proof.

The system must establish three independent facts:

1. The supplied materials can support a convincing human-authored performance.
2. EarCrate can reproduce that accepted performance exactly from a portable `PerformanceScore` without inventing decisions.
3. With the answer key withheld, the provider ensemble can recover a candidate that blindly beats a naive control.

No full-song inference campaign may claim progress until all three pass on one short fixture.

## PerformanceScore authority

A `PerformanceScore` is an explicit creative record. It contains no local paths. It declares exact source identities, a shared output timeline, every clip, source and target sample positions, Rubber Band tempo and pitch transforms, gain, pan, fades, musical function, occurrence identity, ownership, locks, master processing, and append-only command history.

A private binding manifest maps each `source_id` to an exact local file. The renderer rehashes every file before decoding and fails if either the container or declared canonical PCM identity changed.

The renderer may execute decisions. It may not choose sources, infer sections, substitute providers, repair missing clips, extend material, change a transform, or silently skip a failed layer.

## Phase A: author the manual gold

Create a private workspace directly from the verified CORE fixture:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\PREPARE_BEGGIN_REFERENCE_ZERO.ps1 `
  -CoreRoot "S:\Temp\EarCrate\beggin-cloud-handoff-v1-verify\CORE" `
  -Workspace "S:\Temp\EarCrate\reference-zero\beggin-001" `
  -VerifyPcm
```

This hashes the two masters and eight stems into a path-free source registry and creates separate gold and naive-control EDLs. It copies no audio.

For a generic fixture, create a private workspace:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_REFERENCE_ZERO.ps1 `
  -Workspace "S:\Temp\EarCrate\reference-zero\beggin-001" `
  -CreateTemplate
```

The local estate fills two files:

- `source-registry.json`: exact source identities, never paths;
- `performance.edl.csv`: the explicit human arrangement decisions.

The EDL is deliberately plain. REAPER, Mixxx, another DAW, or a human editor may be used to find the decisions, but EarCrate imports one common authority. One row is one selected clip. Automation is represented by splitting a source into additional clips, which keeps v1 deterministic and inspectable.

Compile the EDL:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_REFERENCE_ZERO.ps1 `
  -Workspace "S:\Temp\EarCrate\reference-zero\beggin-001" `
  -SourceRegistry "S:\Temp\EarCrate\reference-zero\beggin-001\source-registry.json" `
  -Edl "S:\Temp\EarCrate\reference-zero\beggin-001\performance.edl.csv" `
  -ScoreId "beggin-reference-zero-v1" `
  -Title "Four Seasons × Måneskin Beggin Reference Zero" `
  -DurationSeconds 20
```

Create private bindings:

```powershell
python scripts\earcrate_reference_zero.py bind `
  --score "S:\Temp\EarCrate\reference-zero\beggin-001\performance-score.json" `
  --source "four_seasons_vocal=S:\private\four-seasons-vocal.wav" `
  --source "four_seasons_instrumental=S:\private\four-seasons-instrumental.wav" `
  --source "maneskin_drums=S:\private\maneskin-drums.wav" `
  --source "maneskin_bass=S:\private\maneskin-bass.wav" `
  --source "maneskin_other=S:\private\maneskin-other.wav" `
  --verify-pcm `
  --output "S:\Temp\EarCrate\reference-zero\beggin-001\source-bindings.private.json"
```

Render twice and require identical canonical PCM:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_REFERENCE_ZERO.ps1 `
  -Workspace "S:\Temp\EarCrate\reference-zero\beggin-001" `
  -VerifyReproduction
```

The owner then accepts, revises, or rejects the manual result. Only `accept` creates a gold authority:

```powershell
python scripts\earcrate_reference_zero.py accept-gold `
  --score "...\performance-score.json" `
  --render-receipt "...\render\render-a.receipt.json" `
  --disposition accept `
  --dimensions-json '{"one-band coherence":5,"lead vocal authority":5,"desire to hear the next section":5}' `
  --note "This is the first accepted EarCrate reference." `
  --output "...\gold-receipt.private.json"
```

## Phase B: withhold the answer and recover it

The accepted score and gold receipt remain private. A source-free challenge exposes only source identities, timeline identity, the gold commitments, the naive-control identity, and the ear gate:

```powershell
python scripts\earcrate_reference_zero.py challenge `
  --gold-score "...\performance-score.json" `
  --gold-receipt "...\gold-receipt.private.json" `
  --control-score "...\naive-control-score.json" `
  --output "...\recovery-challenge.source-free.json"
```

The provider ensemble creates a candidate `PerformanceScore` without seeing the answer key. The candidate and naive control are rendered through the same exact renderer, level matched, randomized, and presented as A/B:

```powershell
python scripts\earcrate_reference_zero.py prepare-review `
  --candidate-score "...\candidate-score.json" `
  --candidate-bindings "...\source-bindings.private.json" `
  --control-score "...\naive-control-score.json" `
  --control-bindings "...\source-bindings.private.json" `
  --output-directory "...\recovery-review-001"
```

A candidate passes only when it blindly beats the control. `tie`, `reject_all`, and selecting the control terminate the candidate lineage. A candidate victory does not grant full-song or release permission.

## Reference Zero completion gate

The project may claim one completed reference only when:

- an owner accepted a manual twenty-second performance;
- the accepted score rendered twice to identical canonical PCM;
- every source and clip has exact custody and accounting;
- the answer key was withheld before inference;
- an inferred candidate blindly beat the naive control;
- the candidate was compared to the gold only after submission;
- no source audio, private paths, review maps, or credentials entered public circulation.

Until then, the honest count remains zero completed references.
