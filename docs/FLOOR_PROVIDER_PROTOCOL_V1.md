# EarCrate Floor Provider Protocol v1

Status: implementation specification for the EarCrate repository. The current
project license applies. This document is not yet a separately licensed industry
standard.

## 1. Wire contract

A provider process MUST:

1. read one complete UTF-8 JSON object from standard input;
2. interpret it as an `earcrate_floor_provider_request` v1;
3. write derived files only beneath `FLOOR_ARTIFACT_DIR`;
4. write exactly one UTF-8 JSON object to standard output;
5. interpret standard error as diagnostics only;
6. exit zero after producing a protocol result;
7. avoid shell-dependent quoting assumptions.

A provider SHOULD use refusal results for unsupported but expected conditions.
A nonzero process exit is a host-level protocol failure.

## 2. Manifest

A provider manifest file name SHOULD end with one of:

```text
.floor-provider.json
.provider.floor.json
.provider.json
```

Required properties:

```text
schema_version = 1
kind = earcrate_floor_provider_manifest
provider_id
provider_version
protocol.name = earcrate-floor-stdio-json
protocol.version = 1
entrypoint.argv[]
capabilities[]
authority
supply_chain
manifest_sha256
```

`entrypoint.argv` MUST be an array. A shell command string is not valid.

## 3. Request identity

The host seals each request. Semantic identity includes content hashes, sizes,
media kinds, roles, ancestry, capability, evidence branch/tier, parameters,
allowed emissions, authority limits, network policy, and resource limits.

Machine-local `path` and `uri` fields are excluded from semantic identity.
Providers MUST bind their result to the supplied `request_sha256`.

## 4. Result authority

Allowed emission kinds:

```text
observation
candidate
measurement
refusal
derived_artifact
review_patch
```

A review patch MUST be unapplied.

Provider payloads MUST NOT claim canonical musical state, legal clearance,
applied review, whole-organism passage, or benchmark truth. The host validates a
normative forbidden set and MAY add request-specific forbidden claims.

## 5. Derived artifacts

Each declared artifact MUST include:

```text
artifact_id
relative_path or path
sha256
size_bytes
media_kind
```

The host treats the path as relative to `FLOOR_ARTIFACT_DIR` and refuses:

```text
absolute paths
empty paths
. or .. components
Windows drive prefixes
symlinks in the path
files outside the artifact root
hash or size disagreement
artifact-count or byte-budget overflow
```

## 6. Network policy

Values:

```text
forbidden
declared
required
```

The v1 reference host checks request/manifest compatibility and exposes the
policy to the process. It does not prove OS-level network isolation. Receipts MUST
say `host_enforcement = declaration_only` and `os_sandbox_proved = false` unless a
different host can prove stronger enforcement.

## 7. Determinism

Manifest declarations:

```text
unknown
best_effort
repeatable
bit_exact
```

Conformance can execute a provider repeatedly. Repeatability compares
`semantic_result_sha256`, which excludes machine-local artifact locators but
includes evidence, measurements, and artifact content identities.

Declared determinism is not trusted without a repeatability receipt.

## 8. Conformance

A conformance pass MAY assert:

- request accepted;
- inputs verified;
- result schema accepted;
- outputs contained;
- outputs verified;
- repeatability observed;
- network declaration checked.

A conformance pass MUST NOT assert musical quality or canonical authority.

## 9. Evaluation

Evaluation uses a separate `earcrate_floor_evaluation_ledger`.

The evaluator identity MUST differ from the evaluated provider identity.
Evaluation policy consists of:

1. hard gates;
2. ordered lexicographic stages.

A tournament winner remains fixture- and policy-scoped.

## 10. Versioning

Protocol v1 is intentionally conservative. Backward-compatible additions belong
in metadata or open constraint dictionaries. A change that alters required wire
semantics requires a new protocol version.

Providers SHOULD refuse unknown required capabilities rather than guessing.

## 11. Security notes

The reference host is a custody and protocol boundary, not a complete hostile-code
sandbox. Production hosts SHOULD add:

- container or OS process isolation;
- network namespaces/firewall policy;
- CPU, memory, process, disk, and GPU limits;
- read-only input mounts;
- signed provider images;
- model artifact verification;
- privacy and locality enforcement.

Those controls should augment, not replace, the content-addressed Floor receipt.

## 12. Release candidates

A candidate builder MAY produce a `ReleaseCandidate`, but it MUST NOT supply the
human musical verdict or rights eligibility. A release gate composes separate
protocol conformance, independent signal evaluation, human musical review, and
use-scoped rights review. `signal_sane_human_review_pending` is a valid terminal
state for automation and MUST NOT be represented as approval.

`TimeMap.segment.lane_id` permits target-time overlap across distinct lanes for
crossfades and layering. Segments in one lane MUST remain non-overlapping.
