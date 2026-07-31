# EarCrate Local Estate

## Why this exists

The repository is only one part of EarCrate's history. The actual project estate
also includes:

- configured workspaces and workspace pointers;
- `earcrate.sqlite` and older `jukebreaker.sqlite` databases;
- immutable project revisions and command ledgers;
- source libraries on separate volumes;
- L3 stems, transforms, analysis caches, embeddings, and transcriptions;
- model weights and provider binaries;
- generated MIDI, racks, SFZ, MixScores, masters, stems, and listening files;
- gate, package, benchmark, rig, and campaign receipts;
- proof packs retained outside Git;
- historical repositories, single-file distributions, ZIP downloads, and
  branch snapshots;
- local-only experiments that required the owner's CPU, GPU, storage, audio
  device, browser, private library, or ears.

A repository canon ledger cannot prove what is currently present on the owner's
machine. Conversely, finding a file on disk does not make it current authority.
The Local Estate is the reconciliation layer between those two facts.

## Authority model

```text
Git/main and canon ledger
        |                    local roots, disks, workspaces, caches, models
        |                                      |
        +---------------- Estate Inventory ----+
                               |
                      conflicts and duplicates
                               |
                         Estate Policy
                               |
                         signed Estate Plan
                               |
              copy/reference-only managed Estate v1
                               |
             Rig Receipt + Local Acceptance Campaign
                               |
     GPU/CPU/private-library/device/browser/human receipts
```

The estate scanner does not choose a winning workspace, merge databases, adopt a
model, approve a render, or delete a source. It records what exists, what each
object claims to be, how strongly it is identified, and which authority boundary
applies.

## Canonical estate layout

```text
<Estate Root>/
  control/
    policy.json
    plans/
    receipts/
    inventory pointers and locks

  authority/
    projects/       immutable project revisions and command ledgers
    workspaces/     database/workspace snapshots pending explicit adoption

  evidence/
    manifests/      specimen, provider, model, provenance, and checksum records
    packs/          proof packs, gate ledgers, external evidence bundles
    reviews/        human reviews, rights decisions, release/publication receipts

  material/
    approved/       user-approved source material
    incoming/       unadjudicated material

  artifacts/
    analysis/
    stems/
    renders/
    auditions/

  models/
    weights/
    provider binaries, licenses, and model identities

  runs/
    rig/            CPU/GPU/storage/audio-device capability receipts
    campaigns/      provider tournaments and private-library acceptance
    gates/          local gates, package verification, benchmarks

  cache/
    ephemeral/
    warm/
    pinned/

  archive/
    repositories/
    workspaces/
    releases/

  quarantine/
    unknown, conflicting, corrupt, or policy-violating objects
```

This layout is a target architecture, not permission to move everything into one
folder. Source libraries may remain on their existing volumes. The Estate records
references to those sources by identity. Large derived artifacts may remain on a
fast NVMe cache. The estate root is the control and custody plane that tells us
where each body lives and why.

## Commands

### 1. Emit the policy and architecture

```bash
python -m earcrate estate architecture --output estate.architecture.json
python -m earcrate estate policy --output estate.policy.json
```

### 2. Capture the actual machine

```bash
python -m earcrate estate rig \
  --root "D:\EarCrate" \
  --root "S:\EarCrate Cache" \
  --audio-devices \
  --output estate.rig.json
```

The rig receipt records CPU count, memory, disk capacity, NVIDIA devices, CUDA and
driver observations, relevant Python package versions, ffmpeg/ffprobe/fpcalc/
Rubber Band availability, and optionally audio-device inventory. It does not run a
model, decode music, contact a network service, or claim quality passage.

### 3. Ingest metadata from every known root

```bash
python -m earcrate estate inventory \
  "C:\Users\<user>\EarCrate" \
  "D:\EarCrate" \
  "S:\EarCrate Cache" \
  "D:\Downloads" \
  --canon docs/canon/canon-ledger.v1.json \
  --hash-mode evidence \
  --output estate.inventory.json
```

`inventory` and its alias `ingest` are read-only. They scan only roots named on
the command line. Symlink directories are not followed. The default evidence hash
mode strongly hashes manifests, receipts, schemas, MIDI, proof packs, and other
bounded evidence objects while leaving large source audio as a reference unless a
stronger hash mode is requested.

Hash modes:

- `none`: metadata and declared identities only;
- `evidence`: strong hashes for evidence and bounded portable artifacts;
- `duplicates`: hash same-size candidates to prove exact duplicates;
- `all`: strong-hash every regular file within the policy size limit.

The inventory recognizes:

- active and stale workspace pointers;
- workspace configs and multiple roots;
- project indexes, revisions, and command ledgers;
- SQLite databases without opening them for writes;
- L3 blobs and metadata, including orphan pairs;
- analysis caches and stem/render material;
- model weights and model/component ledgers;
- specimen manifests, proof receipts, release candidates, and reviews;
- gate/package workflow artifacts;
- source audio, score media, MIDI, renders, stems, and audition files;
- historical repositories and built distributions;
- exact duplicates, overlapping roots, conflicting pointers, and conflicting
  project heads.

### 4. Propose the architecture and cleanup dispositions

```bash
python -m earcrate estate plan \
  estate.inventory.json \
  "D:\EarCrate Estate" \
  --policy estate.policy.json \
  --output estate.plan.json
```

The v1 plan can say:

```text
copy
reference
retain_in_place
archive_candidate
evict_candidate
quarantine_candidate
manual_review
```

Only `copy`, `reference`, and `retain_in_place` can be automatically applied in
v1. An eviction or quarantine candidate is a decision request, not a deletion.
Databases are never merged automatically. Source media remains referenced unless
the policy explicitly permits copying it.

### 5. Apply only the approved, strongly identified copy set

```bash
python -m earcrate estate apply estate.plan.json \
  --approve <exact-plan-sha256> \
  --output estate.apply.receipt.json
```

Apply requires the exact plan identity. Every copied source must have the expected
size and SHA-256 before and after copying. Each destination is written through a
new temporary file, flushed, fsynced, hash-verified, and atomically replaced.
Existing identical files are reused; different collisions refuse. A failed apply
removes only files created by that attempt. Originals are never deleted.

Rollback is similarly content-bound:

```bash
python -m earcrate estate rollback estate.apply.receipt.json \
  --approve <exact-receipt-sha256> \
  --output estate.rollback.receipt.json
```

Rollback removes only unchanged files that the named receipt created. It cannot
remove a pre-existing reused file or a file modified after apply.

### 6. Generate the local acceptance campaign

```bash
python -m earcrate estate campaign \
  estate.inventory.json estate.rig.json \
  --canon docs/canon/canon-ledger.v1.json \
  --output estate.campaign.json
```

The campaign explicitly tracks work that cloud CI and synthetic fixtures cannot
complete:

- full gates and package verification under the owner's exact environment;
- current workspace/database/project and exact-undo acceptance;
- Demucs on the actual GPU and actual library;
- allin1 beat/downbeat/section evaluation on real tracks;
- Rubber Band matched renders and a human ears decision before a default flip;
- Basic Pitch and other transcription-provider tournaments;
- approved-library rack coverage of real PerformanceDemands;
- the Pretty Lights multi-provider tournament on one sealed PCM body;
- physical audio-device latency and underrun measurement;
- Workbench lifecycle against a scratch clone of the real workspace;
- pending release-candidate auditions;
- the decisive circulation proof: a review patch changes a later ranking or
  musical choice.

Every task is marked `ready`, `needs_install`, `needs_hardware`, `needs_input`,
`needs_audio_probe`, `needs_human`, or `blocked`, with the exact receipts expected.

### One-command read-only sweep

```bash
python -m earcrate estate sweep \
  --root "C:\Users\<user>\EarCrate" \
  --root "D:\EarCrate" \
  --root "S:\EarCrate Cache" \
  --estate-root "D:\EarCrate Estate" \
  --canon docs/canon/canon-ledger.v1.json \
  --hash-mode evidence \
  --audio-devices \
  --output-dir "D:\EarCrate Estate Surveys\2026-07-31"
```

This writes architecture, policy, rig, full inventory, redacted inventory, plan,
and campaign JSON. Scanned roots are unchanged. The output names the exact plan
SHA required for a later apply.

## Local-only work retained from prior versions

The estate campaign does not discard the unmerged `claude/earcrate-v0.9.0-
complete-wrz7lw` work. It classifies its candidate organs as local acceptance
work rather than product facts:

- allin1 perception provider;
- Rubber Band transform provider;
- techno persona and external-vocal proof;
- owner-judgment taste ranker;
- unattended project-piano night shift;
- project Workbench and DOM lifecycle;
- consolidated Windows-rig receipt;
- single-file completeness guard.

The branch itself must not be merged wholesale into the newer Buffalo/Floor
organism. Each organ re-enters through a current provider, project, specimen, or
campaign contract and earns a new local receipt.

## Cleanup policy

The default policy is deliberately asymmetric:

### Never automatic

- delete source media;
- delete or merge a project database;
- overwrite a different destination;
- follow a symlink into another tree;
- move a workspace merely because it looks old;
- infer that the newest timestamp is canonical;
- treat a cache as source of truth;
- treat a render as accepted because it is signal-sane;
- treat a local branch as product because it contains more files;
- treat a past sandbox link as artifact custody.

### Safe to automate after exact approval

- copy a strongly hashed manifest, receipt, schema, proof pack, review, or
  distribution into a content-addressed destination;
- record external source references;
- reuse a byte-identical destination;
- create the managed estate directory skeleton;
- write a content-addressed plan and apply receipt;
- remove unchanged files created by that exact receipt during rollback.

### Requires human disposition

- competing SQLite databases;
- conflicting project heads;
- multiple workspace pointers;
- source-media duplicates;
- historical repositories with unique commits;
- model weights without licenses or checksums;
- unknown files;
- cache eviction;
- quarantine and final deletion;
- review and rights decisions.

## Relationship to the existing workspace migration

`plan_workspace_migration` remains a narrow historical-workspace tool. It knows
how to recognize old EarCrate/Jukebreaker database, analysis, render, manifest,
and breadcrumb layouts and can move them under one workspace.

The Estate operates one level above it:

```text
Estate inventory
    -> discovers every candidate workspace and conflict
    -> chooses no winner
    -> proposes which workspace snapshots need migration review

workspace migration
    -> executes one approved migration between known layouts
```

The estate plan must therefore precede any additional workspace migration on a
machine that has accumulated multiple generations.

## Distribution boundary

The initial estate implementation is a package/module CLI (`python -m earcrate
estate`). It is intentionally not declared part of the historical generated
`dist/earcrate.py` until the single-file builder is updated and exercised in the
same review. A local estate survey must not be blocked on that packaging surface,
and the standalone parity obligation remains explicit rather than implied.

## What still requires the owner's machine

This repository change can implement the inventory, policy, plan, and receipt
machinery. It cannot see the owner's disks, private library, GPU driver, model
cache, audio device, or ears from CI. The first authoritative estate record is the
JSON set emitted by `estate sweep` on that machine. Only that receipt can tell us
what is actually present, duplicated, stale, missing, or ready to audition.
