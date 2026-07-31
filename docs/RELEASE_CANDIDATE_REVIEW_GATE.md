# Reviewed Release-Candidate Gate

## Purpose

EarCrate must not confuse a technically clean render with music that a person has
accepted. The release-candidate profile makes that boundary executable:

```text
candidate builder
    → ReleaseCandidate
    → independent SignalEvaluation
    → HumanMusicalReview
    → rights-policy decision
    → ReleaseGateReceipt
```

A candidate may be source-derived, symbolic, generated, restored, mastered, or mixed.
The profile does not decide whether the underlying music is good. It proves which
questions have been answered, by whom, and which questions still block release.

## Status vector

Every release gate carries independent dimensions:

```text
custody
build_reproducibility
signal_sanity
recurrence_identity
transition_integrity
musical_acceptance
rights_eligibility
whole_organism_status
release_status
summary
```

The canonical state after automatic checks pass but before a musician listens is:

```text
signal_sane_human_review_pending
```

That state is blocked. It is not an informal synonym for approved.

## Normative objects

### AudioEditPlan

A sample-accurate map of:

- input artifact identities;
- source frame intervals;
- output frame intervals;
- gain and edit operations;
- exact overlap transitions;
- declared and prohibited operations;
- source-only status.

The validator refuses uncovered output gaps, undeclared overlap, transition/segment
disagreement, unknown sources, operation conflicts, and prohibited operations.

### ReleaseCandidate

Binds the `AudioEditPlan`, a source/performance `TimeMap`, one or more
`PhraseContract` objects, canonical decoded PCM identity, delivery containers, the
builder identity, and an initially blocked status. The builder cannot set musical
acceptance or open the release gate.

### SignalEvaluation

A separate evaluator records metrics and explicit hard gates such as first audible
frame, silence, loudness, true peak, recurrence similarity, and onset continuity. The
signal evaluator identity must differ from the candidate builder identity.

Signal qualification is not musical acceptance. Transition integrity may remain
`provisional_pass` when automatic checks cannot establish groove, phrasing, lyric,
recognizability, or seam quality.

### HumanMusicalReview

A human reviewer records one verdict:

```text
pending
accept
revise
reject
```

The review may include normalized dimensions, listening conditions, notes, and
`ReviewPatch` references. Machine-generated output and builder self-approval are
refused.

### ReleaseGateReceipt

The gate opens only when all of these are true:

```text
exact custody passed
clean-build reproducibility passed
independent signal evaluation passed
human musical verdict is accept
rights status is accepted_by_policy
```

Rights policy records an assertion and policy decision. It may not claim a legal
determination. A successful release gate does not imply Buffalo Gate or
whole-organism passage.

## Empire State fixture

`proofs/specimens/pretty_lights_empire_release_candidate_v1/` contains the source-free
fixture generated from the externally bound recording whose container SHA-256 is:

```text
af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979
```

The edit retains eight contiguous bars and replaces the following four bars with a
non-overlapping recurrence. Its only audio operations are source seek/copy, global
gain, and a 35 ms equal-power crossfade. The source audio and generated delivery audio
are not committed.

The committed proof records:

```text
candidate duration:                31.261 s
first audible frame:                0.000 s
longest silence below -55 dB:       0.000 s
integrated loudness:               -9.019 LUFS
4x true peak:                      -0.500 dBFS
chroma recurrence similarity:       0.993
mel recurrence similarity:          0.994
onset recurrence correlation:       0.890
independent clean builds:            2
PCM/WAV/MP3 repeatability:          bit exact
```

The pending release gate remains blocked by:

```text
human musical acceptance
rights-policy acceptance
```

## Commands

```bash
python -m earcrate floor release-capability

python -m earcrate floor release-adapt-recurrence \
  receipt.json \
  build/release-floor

python -m earcrate floor release-review-template \
  build/release-floor/release_candidate.json \
  build/release-floor/human_review.json \
  --reviewer-id musician.example

python -m earcrate floor release-gate \
  build/release-floor/release_candidate.json \
  build/release-floor/release_gate.json \
  --signal-evaluation build/release-floor/signal_evaluation.json \
  --human-review build/release-floor/human_review.json \
  --custody passed \
  --reproducibility passed \
  --rights-status accepted_by_policy \
  --rights-policy internal_release_policy_v1 \
  --rights-decided-by rights.reviewer.example
```

A blocked gate returns exit status `3`; malformed or authority-laundering input returns
exit status `1`.

## Rebuilding the fixture

The source-free builder is committed beside the fixture:

```bash
python proofs/specimens/pretty_lights_empire_release_candidate_v1/build_release_candidate.py \
  /path/to/exact-source.mp3 \
  build/empire-release-candidate \
  --zip build/Empire_State_release_candidate_v4.zip
```

The builder refuses any source with a different container hash and runs the complete
audio build twice in isolated directories before sealing the candidate. Canonical
musical identity is decoded stereo float32 PCM. Delivery WAV and MP3 identities are
retained separately.
