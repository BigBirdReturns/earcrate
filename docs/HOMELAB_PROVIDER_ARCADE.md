# EarCrate Homelab Provider Arcade

## Why MAME is the right model

MAME does not treat “a binary exists” as proof that a machine is faithfully
emulated. It keeps a driver catalog, identifies the exact required assets, audits
what is present, records imperfect and not-working conditions, and retains the
target even when it cannot currently run. EarCrate needs the same discipline for
music-software commodities.

The Homelab Provider Arcade maps that structure as follows:

```text
MAME driver                 EarCrate target manifest
ROM/software-list audit     package/binary/model/credential/fixture audit
machine configuration       content-addressed Homelab node receipt
software list               sealed score/audio/project/library/device fixtures
working/imperfect/not run   stage receipts, blockers, stale evidence
player experience           committed blind/downstream human audition
maintained driver status    accepted/rejected/deferred/reference-only decision
```

The crucial rule is that **feasibility is not completion**. A target is not
adopted because it was installed, imported, or run once. The lifecycle is finite
and explicit:

```text
catalogued
  -> assets, credentials, hardware, and exact fixture bytes audited
  -> loaded on an identified node
  -> exercised against a sealed real fixture
  -> benchmarked by an independent policy
  -> auditioned by a human when it changes audio or workflow
  -> accepted | rejected | deferred | reference_only
```

No target disappears when it fails. A rejection is durable evidence that prevents
a later session from repeating the same experiment without a changed reason.

## Complete target catalog

The built-in v1 catalog contains 87 named targets from the project sweeps:

- current primitives and core dependencies;
- beat, downbeat, structure, chord, pitch, separation, embedding, fingerprint,
  transcription, stretch, renderer, solver, and device-host candidates;
- network metadata and lawful-material services;
- crate, corpus, automatic-mashup, automatic-DJ, and structured-composition
  antecedents;
- commercial workflow comparators;
- interoperability and supply-chain standards.

The inventory includes, among others:

```text
allin1, madmom, BeatNet, Beat This, sf_segmenter, CREPE, MSAF
Basic Pitch, Music2MIDI, Pop2Piano, PiCoGen2
Demucs, UVR families, demucs-rs
MERT, LAION-CLAP, MuQ
Panako, Chromaprint, AcoustID, MusicBrainz, Discogs
Signalsmith Stretch, Rubber Band, libsamplerate, miniaudio
Mixxx, Ableton Link, aubio, Essentia, Pedalboard
Polymath, Nendo, Freesound, Tracklib, WhoSampled, Selekt
CataRT, SKataRT, AudioStellar, ACorEx, Sononym
AutoMashUpper, the 2018 automatic-DJ system, TOMI
Fadr, Traktor, DJ.Studio, rekordbox, djay
JAMS, Vamp, MusicXML, MNX, MIDI 2.0/MIDI-CI
DAWproject, OpenTimelineIO, CLAP, ONNX
OCI, Sigstore, SLSA, RO-Crate, W3C PROV
SPDX, ODRL, DDEX, C2PA, mirdata, MIREX
```

Catalog receipts retain the source sweeps that led to those entries:

```text
docs/OSS_INTEGRATION_AUDIT.md
docs/DJ_ENGINE_OSS_SWEEP.md
docs/OPEN_MUSIC_EVIDENCE_FLOOR.md
third_party/components.lock.json
project-session transcription and commodity sweeps
Flim's explicit target-withholding report
```

## Sealed reality fixtures

The built-in registry separates the evidence routes:

```text
synthetic regression
Children authoritative score PDF
Children exact target recording, unbound until supplied
Flim community-symbolic compact pack
Flim exact target recording, unbound until supplied
Pretty Lights exact source recording
Pretty Lights committed release-candidate pack or PCM
approved private library
accepted real project revision
physical audio output device
```

A SHA mentioned by a report is not custody. A fixture becomes locally available
only when the current exact bytes are present and verified. The audit accepts
strong inventory hashes that can be reopened and rehashed, or a sealed
`HomelabFixtureBinding` that points to a current regular file and is itself
reverified. A missing or changed file makes the binding stale.

The *Flim* target recording is an important negative control. Its
community-symbolic proof explicitly states that Pop2Piano, Music2MIDI, PiCoGen2,
Basic Pitch, and the cephalopod did not receive the target recording. The Homelab
therefore keeps the symbolic pack and withheld recording as different fixtures;
one cannot satisfy the other.

## Evidence objects

### `HomelabCatalog`

Names every target, license or terms posture, requirements, required stages,
fixtures, intended authority, and terminal decision stage.

### `HomelabNodeReceipt`

Records one machine without executing a provider:

- Python distribution versions;
- executable paths, sizes, mtimes, and bounded hashes;
- CPU, memory, storage, GPU/CUDA, and audio-device observations inherited from the
  estate rig receipt;
- names of declared credential environment variables, never their values.

It explicitly records that no provider process, model load, network request, or
source decode occurred.

### `HomelabFixtureBinding`

Binds one catalog fixture to exact local bytes without copying the source. It
records:

```text
catalog and fixture identity
absolute local path, treated as sensitive
artifact SHA-256, byte count, and mtime
optional decoded-PCM identity
media kind
binding authority and reason
```

Creation refuses symlinked paths and hashes a regular file while checking that its
size and mtime remain stable. Every audit reopens and rehashes the file, or finds
another current strong-inventory location with the same bytes. A declaration of a
SHA inside another JSON object never satisfies fixture availability.

### `HomelabAudit`

Combines the catalog, estate inventory, nodes, fixture bindings, and existing
receipts. For all 87 targets it reports:

```text
assigned node
feasibility blockers and warnings
completed, failed, refused, and missing stages
stale receipts from older manifest or catalog revisions
human-audition requirement and acceptance state
terminal disposition
lifecycle state
```

The audit is an asset and feasibility check. It cannot pass a load, benchmark,
audition, or adoption stage.

### `HomelabCampaign`

Turns every missing prerequisite and stage into a local task. Missing packages,
binaries, assets, fixtures, credentials, GPUs, and audio devices become explicit
remediation tasks. No install or download is hidden in campaign generation.

Stage tasks are dependency ordered and resource classified:

```text
CPU
GPU or CPU
network
physical audio device
human plus playback chain
authority decision
```

The campaign is incomplete until every catalog target has a terminal disposition.

### `HomelabStageReceipt`

Records one non-audition stage. Passing load, fixture, benchmark, and
interoperability stages requires artifact SHA-256 identities. A successful process
exit without custodied outputs is not passage.

### Committed review objects

A blind review is represented by four independently sealed objects rather than by
self-reported booleans:

```text
HomelabReviewAssignment
    public randomized A/B identities, sizes, fixtures, playback contract,
    private-authority commitment, and review-token commitment

HomelabPrivateAssignmentAuthority
    private candidate/control map, exact source identities and sizes,
    nonce, and private review token

HomelabReviewSubmission
    A, B, tie, or abstain; assignment and fixture binding;
    token commitment plus HMAC proving possession of the private token

HomelabAuditionLedger
    separately adjudicated candidate/control verdict bound to all three sources
```

The audit recomputes adjudication from the assignment, private authority, and
submission. Missing source objects, changed option identities, inconsistent
fixtures, invalid HMAC evidence, or a direct ledger that merely says
`blinded=true` cause the audition stage to remain incomplete.

### `HomelabAdoptionDecision`

Provides the terminal target-scoped disposition:

```text
accepted
rejected
deferred
reference_only
```

`accepted` is refused while prerequisite stages are missing and, for an
audio-affecting target, without an accepting current audition ledger. The decision
does not imply legal clearance, release approval, or whole-Buffalo passage.

## Durable operation

Sealed JSON remains evidence authority. The Homelab SQLite store is an index,
dependency scheduler, lease manager, and append-only hash-chained event journal.
It provides:

- idempotent and concurrent-safe object ingestion;
- WAL and full synchronous durability;
- priorities, dependencies, bounded attempts, retries, leases, heartbeats,
  expiry recovery, cancellation, and exclusive GPU/audio-device resource groups;
- task completion only with an already ingested evidence object;
- doctor checks for SQLite, event-chain, object, task, dependency, and lease
  integrity;
- source-free dashboards and public projections;
- verified private backup and hash-approved atomic restore;
- deterministic standard-library `earcrate-homelab.pyz` distribution.

## Commands

Emit the complete catalog:

```bash
python -m earcrate homelab catalog --output homelab.catalog.json
```

Create a node receipt from a prior estate rig receipt:

```bash
python -m earcrate homelab node estate.rig.json \
  --catalog homelab.catalog.json \
  --output homelab.node.workstation.json
```

Bind exact local fixture bytes before treating a recording or proof pack as
available:

```bash
python -m earcrate homelab fixture-bind homelab.catalog.json \
  fixture.flim.target_recording \
  /path/to/exact-recording.flac \
  operator:owner \
  "withheld blind-audio control" \
  --output flim.target.fixture-binding.json
```

Audit one inventory against one or more nodes without running providers:

```bash
python -m earcrate homelab audit estate.inventory.json \
  homelab.node.workstation.json homelab.node.server.json \
  --catalog homelab.catalog.json \
  --output homelab.audit.json
```

Generate the remediation and audition campaign:

```bash
python -m earcrate homelab campaign homelab.audit.json \
  --catalog homelab.catalog.json \
  --output homelab.campaign.json
```

Record a real load or benchmark stage only after it produces exact artifacts:

```bash
python -m earcrate homelab record-stage homelab.catalog.json demucs fixture_run \
  <node-sha256> passed \
  --fixture fixture.pretty_lights.source_audio \
  --artifact <stem-pack-sha256> \
  --measurements-json '{"reconstruction_max_abs":0.0}' \
  --output demucs.fixture.receipt.json
```

Prepare the blind review. The public and private destinations must be new,
disjoint directories:

```bash
python -m earcrate homelab review-prepare \
  homelab.catalog.json demucs <node-sha256> <reviewer-id> \
  candidate.wav control.wav \
  --fixture fixture.pretty_lights.source_audio \
  --fixture fixture.private_library.real \
  --playback-json '{"device":"...","sample_rate":48000,"level":"matched"}' \
  --public-dir review/public \
  --private-dir review/private
```

The reviewer receives only the public A/B directory and the private token through
the chosen authentication channel:

```bash
python -m earcrate homelab review-submit \
  review/public/assignment.json <reviewer-id> \
  review/private/review-token.txt A \
  --dimensions-json '{"bleed":4,"transients":5,"role_usefulness":5}' \
  --output review/submission.json
```

Adjudication combines all committed sources:

```bash
python -m earcrate homelab review-adjudicate \
  homelab.catalog.json \
  review/public/assignment.json \
  review/private/assignment-authority.json \
  review/submission.json \
  --output demucs.audition.json
```

After inventorying the receipts again, issue a terminal decision:

```bash
python -m earcrate homelab decide homelab.audit.json demucs accepted \
  <authority-id> "passed sealed fixture, benchmark, and blind audition" \
  --receipt <stage-receipt-sha256> \
  --receipt <audition-ledger-sha256> \
  --output demucs.decision.json
```

One read-only command generates both estate and Homelab control surfaces:

```bash
python -m earcrate homelab sweep \
  --root "D:\EarCrate" \
  --root "S:\EarCrate Cache" \
  --root "D:\Models" \
  --estate-root "D:\EarCrate Estate" \
  --output-dir "D:\EarCrate Estate\runs\homelab\initial" \
  --canon docs/canon/canon-ledger.v1.json \
  --audio-devices
```

The sweep creates reports only. It does not install, download, invoke, decode,
benchmark, audition, or adopt anything.

## Completion policy

A Homelab campaign is not complete because everything is runnable. It completes
only when:

1. every catalog target has a terminal decision;
2. every accepted target has all required current stage receipts;
3. every accepted audio or workflow target has the required committed human
   audition;
4. every receipt matches the current target manifest, catalog revision, node, and
   fixture set;
5. every required external fixture is backed by current verified bytes rather
   than a declaration alone;
6. rejected, deferred, and reference-only targets remain in the catalog;
7. all artifacts and fixtures are content-addressed;
8. EarCrate still owns musical adjudication rather than delegating it to the
   provider or benchmark.

The governing rule is:

> Inventory everything, make feasibility explicit, bind the real bytes, load only
> with receipts, audition against reality, and never let “it ran” masquerade as
> “it works.”
