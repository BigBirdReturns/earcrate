# EarCrate Open Music Evidence Floor

## Purpose

The music-software ecosystem has excellent individual organs: source separation,
beat and downbeat tracking, transcription, embeddings, fingerprinting, search,
rights catalogs, time stretching, deck engines, DAWs, hardware protocols, review
interfaces, and benchmark suites. The interoperability gap is not another model.
It is a trustworthy control plane between them.

EarCrate Floor defines the minimum portable contract required for an external
organ to participate in musical reasoning without silently becoming musical
authority.

```text
source bytes / score / community witness / accepted revision
                         ↓
                ProviderRequest
       exact identity + branch + evidence tier
                         ↓
             third-party provider
                         ↓
 Observation | Candidate | Measurement | Refusal
 DerivedArtifact | unapplied ReviewPatch
                         ↓
              InvocationReceipt
                         ↓
 independent EvaluationLedger / tournament
                         ↓
             EarCrate adjudication
                         ↓
 SongGenome | PerformanceScore | MixScore | review child
```

The protocol is language-agnostic. A provider receives one JSON object on
standard input, emits one JSON object on standard output, writes derived files
beneath one negotiated artifact directory, and may write diagnostics to standard
error. It does not need to import EarCrate.

## Non-goals

Floor is not:

- a universal song model;
- a DAW project format;
- a plugin DSP ABI;
- a source-separation model;
- a rights-clearance service;
- a benchmark score pretending to be musical truth;
- a real-time audio callback interface;
- an operating-system sandbox.

It is the custody, authority, evaluation, and interchange layer beneath those
systems.

## Evidence ladder

Every request carries both an evidence branch and an evidence tier.

Branches describe the causal domain:

```text
score
symbolic
 audio
convergence
performance
review
evolution
```

Tiers describe what kind of claim the evidence can support:

```text
unspecified
authoritative_score
community_symbolic_witness
blind_audio_inference
cross_modal_accepted
performance_realization
human_review
campaign_evidence
```

A community transcription can support a playable witness. It may not claim blind
listening. A score-derived render may support execution. It may not be smuggled
into an audio-inference branch. A model output can be useful without being
accepted.

## Provider authority ceiling

A provider may emit only:

```text
Observation
Candidate
Measurement
Refusal
DerivedArtifact
unapplied ReviewPatch
```

It may never directly write or claim:

```text
SongGenome
PerformanceScore
MixScore
accepted or canonical musical state
applied review patch
legal determination or rights clearance
whole-organism passage
benchmark winner as truth
```

The host recursively checks provider payloads for authority-bearing objects and
flags. This is not a complete semantic security proof, but it prevents the common
case in which a model response silently overwrites accepted state.

## Normative objects

### ProviderManifest

Declares:

- stable provider ID and version;
- wire-protocol version;
- argv-array entrypoint;
- capabilities;
- accepted media kinds;
- evidence branches and tiers;
- result kinds;
- network declaration;
- determinism declaration;
- runtime and output limits;
- authority ceiling;
- license, executable, model, and signature metadata.

Registration or catalog discovery does not imply installation, trust,
conformance, quality, selection, or acceptance.

### ProviderRequest

Binds:

- one capability;
- exact input artifact identities and sizes;
- evidence branch and tier;
- ancestry;
- parameters;
- allowed result kinds;
- forbidden authority claims;
- network policy;
- resource limits;
- contextual constraints.

The semantic request hash excludes machine-local paths and URIs. The same bytes
and contract moved between machines retain the same request identity.

### ProviderResult

Contains:

- request and manifest identities;
- provider identity;
- success, refusal, or error status;
- evidence emissions;
- derived-artifact declarations;
- explicit refusals;
- provider measurements;
- semantic and full result identities.

The semantic result identity excludes local artifact locators. It remains stable
when the same result is reproduced in another artifact directory.

### TimeMap

Maintains source time and performance time as separate domains. Segment modes:

```text
continuous
jump
loop
retrigger
reverse
hold
```

This is the minimum representation needed for deck jumps, loops, cue relaunches,
source substitutions, and non-destructive timeline interchange.

### PhraseContract

Defines “fungible for this slot,” not merely “nearby in an embedding.” It carries:

- role;
- start, length, and meter;
- entry and exit grammar;
- allowed transformations;
- hard constraints;
- soft objectives;
- identity obligations;
- future obligations;
- evidence references;
- a rights envelope.

Open constraint dictionaries are deliberate. Music systems can extend the
contract without changing the base protocol, while hard/soft and
identity/future semantics remain stable.

### RightsEnvelope

Carries assertions, not a legal decision:

- source identity;
- assertion status;
- license expression;
- policy URI;
- allowed and prohibited uses;
- attribution duties;
- supporting evidence;
- jurisdiction and expiry.

Every envelope states that the provider may not decide legality. An EarCrate
policy or human decision remains downstream.

### ReviewPatch

A provider may propose a content-addressed JSON-patch-like correction containing:

- target revision;
- target object;
- operations;
- rationale;
- supporting evidence;
- invalidation hints;
- proposer identity.

It must remain unapplied. EarCrate creates the child revision and decides what to
recompute.

### InvocationReceipt

The reference host records:

- provider manifest;
- request and result identities;
- resolved executable identity when available;
- argv and working directory;
- verified input custody;
- verified output custody;
- stdout and stderr identities;
- process outcome and elapsed time;
- resource limits;
- network declaration;
- explicit security limits.

The receipt says `host_enforcement = declaration_only` and
`os_sandbox_proved = false` for network access. Floor refuses to convert a policy
declaration into a false sandbox claim.

### EvaluationPolicy and EvaluationLedger

Protocol conformance and musical quality are different objects.

An independent evaluator records metrics against one provider result. The
provider may not be its own evaluator identity. A sealed policy defines hard
gates followed by lexicographic objective stages. This prevents one hidden scalar
from compensating for a disqualifying failure.

### TournamentReport

A tournament winner means only:

> best among these submitted results, on this fixture, under this sealed policy.

It is not canonical musical truth. EarCrate still adjudicates whether and how the
provider participates.

### FloorCrate

A portable crate contains:

```text
provider.manifest.json
request.json
result.json
invocation.receipt.json
annotations.jams.json
provenance.prov.json
rights.odrl.json
ro-crate-metadata.json
checksums.sha256
floor-crate.json
```

Source media is not copied by default. Derived artifacts are copied only through
an explicit option and are reverified first.

## Reference subprocess protocol

```text
stdin       one UTF-8 ProviderRequest JSON object
stdout      one UTF-8 ProviderResult JSON object; no log text
stderr      diagnostics
artifacts   files below FLOOR_ARTIFACT_DIR
```

The host:

1. seals the manifest and request;
2. checks capability compatibility;
3. verifies every local input hash and size;
4. creates an empty artifact directory;
5. expands only declared Floor placeholders;
6. launches an argv array with `shell=False`;
7. supplies a small, sanitized environment;
8. applies runtime and output-size limits;
9. parses exactly one stdout JSON object;
10. refuses absolute paths, parent traversal, drive prefixes, and symlinks;
11. recomputes all artifact hashes and sizes;
12. validates the provider authority ceiling;
13. seals the result and invocation receipt.

Reference placeholders:

```text
${PYTHON}
${FLOOR_MANIFEST_DIR}
${FLOOR_ARTIFACT_DIR}
```

Reference environment variables:

```text
FLOOR_ARTIFACT_DIR
FLOOR_REQUEST_SHA256
FLOOR_PROVIDER_MANIFEST_SHA256
FLOOR_NETWORK_POLICY
```

## Provider quick start

Generate a movable, standard-library-only provider:

```bash
python -m earcrate floor scaffold build/reference-floor-provider
```

Run repeatability conformance:

```bash
python -m earcrate floor conformance \
  build/reference-floor-provider/reference.floor-provider.json \
  build/reference-floor-provider/request.json \
  build/reference-floor-conformance \
  --repeat 2
```

The generated provider imports no EarCrate code. It can be moved as a directory
without changing its manifest identity because its entrypoint uses
`${FLOOR_MANIFEST_DIR}`.

## Catalog behavior

Discover manifests:

```bash
python -m earcrate floor catalog ./providers ./third-party
```

Filter for one sealed request:

```bash
python -m earcrate floor catalog ./providers --request request.json
```

The catalog:

- reports malformed manifests instead of silently skipping them;
- refuses conflicting manifests for the same provider ID and version;
- separates accepted and incompatible entries;
- does not rank or select;
- can expose existing EarCrate in-process providers as honest, non-conformant
  adapter projections.

## Conformance versus quality

A passing conformance report proves only:

```text
request accepted
input identity verified
result schema accepted
artifacts contained
artifact identity verified
repeatability checked when requested
network declaration checked
```

It does not prove:

```text
accurate transcription
clean stems
recognizable recurrence
good arrangement
correct legal status
best provider
```

Those claims require a fixture, independent evaluator, and sealed evaluation
policy.

## Standards posture

Floor is connective tissue beneath existing standards, not a replacement.

| Surface | Existing standards or ecosystems | Floor posture |
|---|---|---|
| Annotations | JAMS | Mapping |
| Feature plugins | Vamp | Adapter |
| Scores | MusicXML, MNX | Interchange |
| Performance and devices | MIDI 2.0, MIDI-CI | Interchange |
| DAW/timeline | DAWproject, OpenTimelineIO | Lowering |
| Native DSP | CLAP | Host adapter |
| Model execution | ONNX | Provider runtime |
| Packaging/signatures | OCI, Sigstore, SLSA | Supply-chain identity |
| Research/provenance | RO-Crate, W3C PROV | Mapping |
| License/policy | SPDX, ODRL | Assertion mapping |
| Music metadata/provenance | DDEX, C2PA | Future adapter |
| Benchmarks | mirdata, MIREX | Fixture/evaluation bridge |

The committed mappings are not certification by those standards bodies.

## Remaining gaps

The executable gap register is available through:

```bash
python -m earcrate floor gaps
```

Implemented surfaces include evidence-tier isolation, provider authority,
source/performance time, phrase contracts, rights assertions, artifact custody,
path containment, supply-chain identity, conformance/quality separation,
evaluator independence, lexicographic tournaments, review proposals, portable
crates, language neutrality, and catalog conflict handling.

Honest partial or open boundaries include:

- OS-enforced network isolation;
- OS/container CPU, memory, GPU, and process isolation;
- remote attestation;
- privacy and data-locality policy;
- real-time callback safety;
- owner-approved licensing of the normative specification.

## Licensing boundary

The repository is currently governed by its existing project license. Becoming a
universal floor for commercial DAWs, DJ applications, research systems, hardware,
and model vendors likely requires a separate owner-approved permissive license
for the normative schemas, examples, and conformance fixtures.

No implementation commit may silently make that ownership decision.

A likely long-term split is:

```text
Normative Floor schemas, vocabulary, examples, conformance fixtures
    -> owner-approved permissive license

EarCrate planner, constitutions, product implementation, private integrations
    -> project-selected license
```

## Governing rule

> Make it easier to contribute one musical organ than to build another
> incompatible organism—and impossible for that organ to conceal its evidence,
> authority, supply chain, refusals, or evaluation boundary.
