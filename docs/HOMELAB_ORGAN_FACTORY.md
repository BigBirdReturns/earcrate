# EarCrate Homelab Organ Factory

## Purpose

The Provider Arcade already inventories eighty-seven musical organs and keeps the distinction between installed, loadable, benchmarked, auditioned, accepted, rejected, deferred, and reference-only. The Organ Factory is the production circulation layer above that cabinet. It turns exact source bindings and the current Homelab audit into typed provider graphs, executes compatible organs, preserves every intermediate identity, renders bounded recipe families, selects a quality-diversity frontier, prepares blind review, converts the owner's review into a scoped correction, and emits a source-free circulation packet.

The factory does not replace the Open Music Evidence Floor, Homelab store, cephalopod reader, PlayerPiano authority, MixScore, sealed racks, review governance, or publication membrane. It composes those organs without granting any one provider canonical musical authority.

```text
catalog + authoritative audit + exact source bindings
    -> specimen-scoped provider trials
    -> typed recipe graph
    -> CPU / 4060 / independent 3090 workers
    -> exact intermediate and final receipts
    -> hard signal gates
    -> quality-diversity frontier
    -> leak-resistant blind owner review
    -> scoped preference update / ReviewPatch
    -> selective child recomputation
    -> source-free circulation packet
```

## What lands in this pull request

The pull request carries the complete source-free execution surface required by the local estate:

- normalized specimen suite for Beggin, Animal × Toxic, and sombr × Yellow;
- deterministic provider-role policy and current adapter policy;
- cloud-specimen intake, exact local source binding, and specimen campaign compilation;
- the organ-factory manifest, bounded recipe design, worker execution, quality archive, blind review, preference update, and circulation implementation;
- the real Beggin phrase-local timing runner and its tests;
- Windows, Linux, and command wrappers;
- schemas and focused CI;
- source-free telemetry for the real estate sweep, Demucs GPU smoke, timing review assignment, and current human-review boundary.

No recording, derived audition audio, model weight, credential, private review map, or absolute local path is committed.

## One-command local operation

The operator supplies only authoritative local objects and exact source-binding objects. The repository supplies the suite, policies, recipes, scheduler, worker logic, review protocol, and circulation logic.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_HOMELAB_FACTORY.ps1 `
  -Catalog "<estate>\survey\homelab.catalog.json" `
  -Audit "<estate>\survey\homelab.audit.json" `
  -Bindings "<estate>\bindings\organ-transplants-v1" `
  -Workspace "<estate>\factory\campaign-001" `
  -Profile core `
  -Gpu 0,1,2
```

The command bootstraps the workspace if it does not exist, compiles the source-bound provider and recipe graph, runs every dependency-ready machine task, verifies all sealed outputs, and stops at the human review boundary. The public review directories contain only opaque candidate files and public assignments. Candidate-specific metrics, mappings, local paths, and review tokens remain private.

After the owner listens, one command seals the review, creates the scoped preference update, resumes selective recomputation, and emits the circulation packet:

```powershell
python scripts\earcrate_factory.py review `
  --workspace "<estate>\factory\campaign-001" `
  --case beggin-four-seasons-x-maneskin-handoff `
  --choice A `
  --dimensions-json '{"vocal authority":5,"phrase placement":5,"percussion impact":4,"room continuity":3}' `
  --note "Freeze timing; improve cymbal and room continuity." `
  --gpu 0 --gpu 1 --gpu 2
```

## Factory objects

The factory adds sealed objects to the existing Homelab vocabulary:

```text
earcrate_homelab_factory_manifest
earcrate_homelab_factory_recipe
earcrate_homelab_factory_run
earcrate_homelab_factory_state
earcrate_homelab_quality_archive
earcrate_homelab_factory_review_assignment
earcrate_homelab_factory_private_assignment_authority
earcrate_homelab_factory_review_submission
earcrate_homelab_factory_review_ledger
earcrate_homelab_preference_update
earcrate_homelab_circulation_packet
```

The existing specimen objects remain:

```text
earcrate_homelab_specimen_suite
earcrate_homelab_specimen_intake_receipt
earcrate_homelab_specimen_source_binding
earcrate_homelab_specimen_trial_receipt
```

All objects use deterministic semantic identities. Source bindings and private review authorities remain sensitive. Public circulation contains redacted projections and explicitly states that projected bytes are not the original authority.

## Bounded recipe search

The factory does not render the Cartesian product of every provider. It starts with an incumbent recipe, performs one-factor swaps to measure each organ's marginal contribution, and then adds bounded pairwise swaps to expose important interactions. Each recipe records exact provider task identities, operations, protected musical invariants, quality descriptors, and authority limits.

The recipe families are specimen-specific:

### Beggin across generations

- incumbent percussion transplant;
- phrase-local transplant preserving Frankie and terminal call durations;
- hybrid drum body using eventization and reconstruction;
- one-factor and pairwise provider substitutions for timing, separation, transcription, and rendering organs.

### Animal × Toxic

- modern percussion chassis with Toxic identity punctuation and negative space;
- vocal handoff and production-grammar transfer;
- reverse grammar with KATSEYE material inside Toxic's dry rhythmic body;
- provider substitutions across beat, tonality, separation, structure, stretching, and reconstruction.

### sombr × Yellow

- ancestral guitar-width and live-band-lift reconstruction in the target harmony;
- drum-lift and microtiming transfer;
- reverse modern body while retaining Yellow's identity;
- provider substitutions across tempo, tuning, structure, stems, transcription, and execution.

## Provider execution

Adapters are explicit. The committed defaults cover FFmpeg/ffprobe, Chromaprint, Demucs, librosa, aubio, Basic Pitch, Rubber Band, SoundFile, Mido, and the EarCrate signal evaluator. `audio-separator` is registered but refuses execution until a local override supplies one exact model filename. Unknown targets are refused with durable evidence instead of being converted into hidden manual work.

Local adapter overrides may add exact model filenames, provider manifests, or argv arrays. Commands use `shell=False`, sanitized environments, bounded timeouts, explicit GPU assignment, and content-addressed outputs. One worker is assigned per declared GPU; VRAM is never described as pooled.

The default resource split is:

```text
CPU               custody, fingerprints, observations, signal evaluation, circulation
RTX 4060          incumbents, smoke controls, short Demucs fixtures
RTX 3090 #1       one heavy separator or model family
RTX 3090 #2       a complementary heavy separator or model family
physical device   review playback only under an explicit playback contract
```

## Quality-diversity and owner review

Only signal-sane audio runs enter the archive. The archive records impact, timing, bleed, room continuity, recognizability, vocal authority, compute cost, and audio-artifact count. It retains the best candidate in each descriptor cell, then fills the bounded frontier with the strongest remaining nonduplicate candidates. The machine frontier is a listening workload reduction mechanism, not musical acceptance.

Review preparation randomizes the frontier into opaque options. Candidate-specific metrics are withheld until submission, closing the hash-correlation leak identified in the first Beggin timing package. Public assignments contain only common review requirements and opaque content identities. The private authority retains the option map, source-artifact commitments, and token.

The review submission is HMAC-bound to the assignment, reviewer, choice, dimensional scores, and notes. Adjudication proves the source chain and reveals the winning run only after submission.

## Learning and selective recomputation

An accepted review produces a scoped `earcrate_homelab_preference_update`. It records the winning run, observed dimensions, protected invariants, exact invalidation scope, and next-round requirements. It cannot claim a general taste model or assume transfer to another fixture.

The update must preserve source identities, historical review objects, unrelated provider receipts, and unselected cases. Only the reviewed recipe ranking and declared descendants may change. A later campaign must demonstrate changed routing or ranking under comparable evidence before the project may claim learning.

## Circulation

The final machine stage emits a source-free circulation directory containing projected objects, per-file hashes, a sealed circulation packet, and `SHA256SUMS.txt`. It redacts absolute paths, commands, environments, output logs, review tokens, option maps, source-artifact maps, and credential-like fields. It publishes no source media, model bytes, derived auditions, private database, or authoritative private object.

The circulation packet is the material that belongs on GitHub. The local store, recordings, models, raw stems, candidates, and review authority remain local.

## Failure behavior

The factory fails closed on changed source bytes, invalid object seals, missing dependencies, unknown provider adapters, absent exact model configuration, unsafe paths, nonzero provider exits, timeouts, missing recipe artifacts, insufficient review frontier, invalid review tokens, mismatched assignments, and public-export leaks.

A refusal is retained as evidence. A least-bad candidate is not promoted. `reject_all` remains a valid human result. Provider execution never becomes provider adoption, and a preferred recipe never becomes release permission.
