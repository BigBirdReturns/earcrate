# Floor Release-Candidate Discipline

A renderer producing an audio file is not the same event as EarCrate accepting the
music. The release profile makes that distinction executable.

```text
candidate builder
    -> ReleaseCandidate
    -> protocol conformance / repeatability
    -> independent signal EvaluationLedger
    -> human musical review
    -> use-scoped rights review
    -> ReleaseGateReceipt
```

The builder may prove custody and create a reviewable artifact. It may not supply
the human verdict, decide legal clearance, or claim whole-organism passage.

## Status vector

A release gate carries independent dimensions rather than one approval-like label:

```text
custody
build_reproducibility
signal_sanity
recurrence_identity
transition_integrity
human_musical_review
rights_eligibility
whole_organism_status
release_state
```

Typical intermediate state:

```text
custody:                 passed
build_reproducibility:   passed
signal_sanity:           passed
recurrence_identity:     provisional_pass
transition_integrity:    provisional_pass
human_musical_review:    pending
rights_eligibility:      not_evaluated
whole_organism_status:   not_claimed
release_state:           signal_sane_human_review_pending
```

That state is useful and audible, but it is not release approval.

## Multi-lane TimeMap

A crossfade contains two simultaneous source-time mappings. `TimeMap.segment.lane_id`
therefore permits target-time overlap across lanes while refusing overlap within one
lane.

```text
lane deck_a: target 0.000-20.864 <- source 64.725-85.589
lane deck_b: target 20.829-31.261 <- source 158.635-169.067
```

The 35 ms overlap is explicit rather than flattened into unexplained PCM.

## Pretty Lights blind fixture

The first fixture uses only the exact supplied recording bytes and the previously
sealed blind recurrence evidence. It keeps eight bars contiguous, replaces the final
four bars with a non-overlapping later occurrence, and uses a 35 ms equal-power
transition.

The content-addressed fixture records:

```text
source SHA-256:
  af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979

candidate duration:       31.261 s
first audible time:        0.000 s
longest -55 dB silence:    0.000 s
integrated loudness:      -9.100 LUFS
4x true peak:             -0.674 dBTP
chroma similarity:         0.998555
mel similarity:            0.997413
onset correlation:         0.976120
100 ms seam RMS change:    0.0899 dB
```

The builder passed two-run protocol conformance with bit-identical semantic results.
A distinct signal evaluator passed every declared automatic gate. Human musical
review and rights review remain absent, so the gate stops at
`signal_sane_human_review_pending`.

No source media or generated audio is committed. The repository retains only the
fixture identities, metrics, and authority boundary in
`proofs/floor/empire_state_recurrence_release_candidate_v1.json`.

## Commands

Write a review request:

```bash
python -m earcrate floor review-request \
  release_candidate.json \
  human_review_request.json
```

Assemble the current gate:

```bash
python -m earcrate floor release-gate \
  release_candidate.json \
  release_gate.receipt.json \
  --conformance builder.conformance.json \
  --signal-evaluation signal.evaluation.json
```

After independent human and rights decisions exist, add:

```text
--human-review human.review.json
--rights-review rights.review.json
```

`release_eligible_for_declared_use` means only that all declared gates for the named
use passed. It is not a universal legal determination and is not Buffalo Gate passage.
