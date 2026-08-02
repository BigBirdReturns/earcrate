# Release governance v2

Release governance consumes a sealed `ReleaseCandidate` and an independent passed
`SignalEvaluation`. It cannot create music, qualify signal, decide legal truth, or
claim whole-organism passage.

## Authority chain

```text
ReleaseCandidate + SignalEvaluation
    -> PublicReviewCampaign
    +  PrivateAssignmentAuthority
    -> BlindHumanReview quorum
    -> independent ArbitrationReview when the quorum splits
    -> use-scoped, time-bounded RightsDecision
    -> GovernedReleaseDecision
    -> format-neutral PublishPermit
    -> atomic publication directory
    -> PublicationReceipt
```

## Blinding and review commitments

Each reviewer receives an independently derived A/B permutation. The public
assignment contains only option artifact identities. The candidate/control map,
review token, reviewer identity, and external authentication receipt are sealed in
a separate private authority. The public campaign commits that authority by hash.

A review is valid only when it binds all of the following:

- campaign, candidate, control, and review-policy identities;
- public and private assignment identities;
- the private-authority identity;
- reviewer identity and external authentication evidence;
- the assignment token;
- the complete policy-defined dimension set.

The candidate builder and signal evaluator may not review. A split completed
quorum remains blocked until an independent arbitrator seals a decision over the
exact review set.

## Rights boundary

A rights decision is a policy result, not a legal determination. It names the
intended use, jurisdictions, channels, validity interval, deciding authority,
authentication evidence, and supporting evidence. Release eligibility is refused
outside the decision's validity interval. A publish permit may not outlive the
rights decision.

## Publication firewall

A permit names artifact roles, exact artifact IDs, content hashes, sizes, media
kinds, and safe output names. Publication:

1. refuses symlinked, missing, non-regular, mutated, or unpermitted inputs;
2. copies into a fresh sibling staging directory;
3. fsyncs every staged file;
4. writes a sealed permit, manifest, checksum ledger, and publication receipt;
5. fsyncs and atomically promotes the directory on POSIX, or uses Windows
   `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)`;
6. verifies the promoted directory before returning;
7. removes staging or promoted output after any failed verification.

The `PublicationReceipt` is content-addressed and binds the permit, manifest,
checksum ledger, publication time, artifact count, and durability mode.

## Commands

```text
python -m earcrate floor release-governance-capability
python -m earcrate floor release-publication-verify PATH_TO_PUBLISHED_RELEASE
```

The complete object schemas are emitted by the existing Floor schema command:

```text
python -m earcrate floor release-governance-schemas OUTPUT_DIRECTORY
```

## Non-claims

A successful publication receipt does not prove musical quality, legal clearance,
rights ownership, blind-audio inference, or Buffalo Gate passage. It proves only
that the declared independent authorities reconciled and that the exact permitted
bytes crossed the publication boundary atomically.
