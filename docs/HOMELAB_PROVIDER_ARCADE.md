# EarCrate Homelab Provider Arcade

## Why MAME is the right model

MAME does not treat “a binary exists” as proof that a machine is faithfully
emulated. It keeps a driver catalog, identifies the exact required assets, audits
what is present, records imperfect/not-working conditions, and retains the target
even when it cannot currently run. EarCrate needs the same discipline for music
software commodities.

The Homelab Provider Arcade maps that structure as follows:

```text
MAME driver                 EarCrate target manifest
ROM/software-list audit     package/binary/model/credential/fixture audit
machine configuration       content-addressed homelab node receipt
software list               sealed score/audio/project/library/device fixtures
working/imperfect/not run   stage receipts, blockers, stale evidence
player experience           blind/downstream human audition
maintained driver status    accepted/rejected/deferred/reference-only decision
```

The crucial difference from the original estate campaign is that **feasibility is
not completion**. A target is not adopted because it was installed, imported, or
run once. The lifecycle is finite and explicit:

```text
catalogued
  -> assets/credentials/hardware audited
  -> loaded on an identified node
  -> exercised against a sealed real fixture
  -> benchmarked by an independent policy
  -> auditioned by a human when it changes audio or workflow
  -> accepted | rejected | deferred | reference_only
```

No target disappears when it fails. A rejection is durable evidence that prevents
future sessions from repeating the same experiment without a changed reason.

## Complete target catalog

The built-in v1 catalog contains 87 named targets from all of the sweeps:

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

The catalog sources are retained in every catalog receipt:

```text
docs/OSS_INTEGRATION_AUDIT.md
docs/DJ_ENGINE_OSS_SWEEP.md
docs/OPEN_MUSIC_EVIDENCE_FLOOR.md
third_party/components.lock.json
project-session transcription and commodity sweeps
Flim's explicit target-withholding report
```

## Sealed reality fixtures

The built-in fixture registry separates the evidence routes:

```text
synthetic regression
Children authoritative score PDF
Children exact target recording (unbound until supplied)
Flim community-symbolic compact pack
Flim exact target recording (unbound until supplied)
Pretty Lights exact source recording
Pretty Lights committed release-candidate pack / PCM
approved private library
accepted real project revision
physical audio output device
```

The *Flim* target recording is a particularly important negative control. Its
community-symbolic proof explicitly states that Pop2Piano, Music2MIDI, PiCoGen2,
Basic Pitch, and the cephalopod did not receive the target recording. The Homelab
therefore keeps the symbolic pack and withheld recording as different fixtures;
one cannot satisfy the other.

## The seven content-addressed objects

### `HomelabCatalog`

Names every target, its license/terms posture, requirements, required stages,
fixtures, intended authority, and terminal decision stage.

### `HomelabNodeReceipt`

Records one machine without executing a provider:

- Python distribution versions;
- executable paths, sizes, mtimes, and bounded hashes;
- CPU, memory, storage, GPU/CUDA, and audio-device observations inherited from the
  estate rig receipt;
- names of declared credential environment variables, never their values.

It explicitly says that no provider process, model load, network request, or source
decode occurred.

### `HomelabAudit`

Combines the catalog, estate inventory, nodes, fixtures, and existing receipts. For
all 87 targets it reports:

```text
assigned node
feasibility blockers and warnings
completed / failed / missing stages
stale receipts from older manifest/catalog revisions
human-audition requirement and acceptance state
terminal disposition
lifecycle state
```

The audit is an asset/feasibility check. It cannot pass a load, benchmark,
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
human + playback chain
```

The campaign is incomplete until every catalog target has a terminal disposition.

### `HomelabStageReceipt`

Records one non-audition stage. Passing load/fixture/benchmark/interoperability
stages requires artifact SHA-256 identities. A successful process exit without
custodied outputs is not a stage passage.

### `HomelabAuditionLedger`

Records the actual reality check:

- target and manifest identity;
- node and reviewer identity;
- candidate and control PCM/artifact identities;
- declared playback chain;
- judgment dimensions;
- accept/reject/revise/abstain verdict;
- whether assignment was blinded and randomized.

Targets whose stage is `blind_audition` refuse a ledger unless both blinding and
randomization are true.

### `HomelabAdoptionDecision`

Provides the terminal target-scoped disposition:

```text
accepted
rejected
deferred
reference_only
```

`accepted` is refused while prerequisite stages are missing and, for an
audio-affecting target, without an accepting audition ledger. The decision does
not imply legal clearance or whole-Buffalo passage.

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

Audit one inventory against one or more nodes without running providers:

```bash
python -m earcrate homelab audit estate.inventory.json \
  homelab.node.workstation.json homelab.node.server.json \
  --catalog homelab.catalog.json \
  --output homelab.audit.json
```

Generate the full remediation/audition campaign:

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

Record the human audition:

```bash
python -m earcrate homelab record-audition homelab.catalog.json demucs \
  <node-sha256> <reviewer-id> <candidate-sha256> <control-sha256> accept \
  --blinded --randomized \
  --playback-json '{"device":"...","sample_rate":48000,"level":"matched"}' \
  --dimensions-json '{"bleed":4,"transients":5,"role_usefulness":5}' \
  --output demucs.audition.json
```

After inventorying those receipts again, issue a terminal decision:

```bash
python -m earcrate homelab decide homelab.audit.json demucs accepted \
  <authority-id> "passed sealed fixture, benchmark, and blind audition" \
  --receipt <stage-receipt-sha256> --receipt <audition-ledger-sha256> \
  --output demucs.decision.json
```

One read-only command generates both the estate and Homelab control surfaces:

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
2. every accepted target has all required stage receipts;
3. every accepted audio/workflow target has the required human audition;
4. every receipt matches the current target manifest and catalog revision;
5. rejected, deferred, and reference-only targets remain in the catalog;
6. all artifacts and fixtures are content-addressed;
7. EarCrate still owns musical adjudication rather than delegating it to the
   provider or benchmark.

The governing rule is:

> Inventory everything, make feasibility explicit, load only with receipts,
> audition against reality, and never let “it ran” masquerade as “it works.”
