# EarCrate canon and campaign ledger v2

This is the current repository-history authority after release governance, the
append-only v1 ledger, and the local-estate/Homelab control plane landed.

```text
audited main:             d511414daa0c7127c1e9cfdc64726979e6682e1f
audit date:               2026-08-02
ledger SHA-256:            a8c0ab71fafcc8d2bbdf9b3c0310a67992fdd6362eca9b4cc06c4f68bca89f55
superseded v1 effective:   26eda6df9bb6e2196da6bb65b3e7d7f3e7f2c23f69347e2ac1d0f9ccefc089fe
open pull requests:        0 at audit
campaign epic:             #57
```

Machine authority:
[`docs/canon/canon-ledger.v2.json`](canon/canon-ledger.v2.json)

Schema:
[`schemas/earcrate_canon_and_nonlanding_ledger_v2.schema.json`](../schemas/earcrate_canon_and_nonlanding_ledger_v2.schema.json)

Version 1 and its correction object remain immutable historical inputs. V2 is a
new revision, not an in-place rewrite.

## Post-v1 pull requests

| PR | Disposition | Authority |
| --- | --- | --- |
| #53 | landed | Canon/nonlanding ledger, merged as `6da4a611…`. |
| #54 | superseded duplicate | No unique authority; closed and locked. |
| #55 | landed | Local estate and Homelab acceptance, merged as `d511414daa…`. |
| #56 | completed transport | Exact payload harvested into #55; closed and locked; no independent product authority. |

## What house-cleaning means

There are no open pull requests. Duplicate and temporary conversations are
terminal and locked. Historical branches are retained because their exact heads
are evidence; deleting or force-moving them would make the branch list shorter by
destroying custody. The active-work rule is therefore:

> An issue is not a branch. Create one implementation branch only when work
> starts, and squash-merge it after exact-head review. Historical refs stay frozen.

## Branch retention

| Ref | Terminal head | Classification | Pointer check | Authority |
| --- | --- | --- | --- | --- |
| `agent/canon-nonlanding-ledger` | `886040ceb7ca5e6782e5c19d69f696d6f97a9ccc` | `landed_frozen` | `identical_to_terminal_head` | PR #53 landed as 6da4a6111ed529cd470c5cec3f4a0a1988fc3a08. |
| `agent/forge-material-breeder-v0` | `d27c0b1ac1ebf464ddec726105c5fdd546f02563` | `deferred_concept_canon` | `identical_to_terminal_head` | Direction retained; branch scaffolding is not the material forge implementation. |
| `agent/gate8-canonical-integration` | `3b70d343997470d7af93daa84558306db3abdf0f` | `retired_delivery_mechanism` | `identical_to_terminal_head` | Unique overlay workflow retired after ordinary source landed through PR #39. |
| `agent/harden-release-governance-v2` | `43245748702848053af970d88011479e0044e4c7` | `landed_frozen` | `identical_to_terminal_head` | PR #52 landed as 36618a23f755b876e6d887be64a61389b5093e10. |
| `agent/homelab-integrity-transport` | `ab9e89a4966f5656c3e86c0ce4d080e5e85e1100` | `completed_transport_no_authority` | `diverged_after_terminal_close` | PR #56 terminal transport head; exact payload was harvested into PR #55. |
| `agent/integrated-score-cutover` | `b307dd613ffc61c7efa2faea2e8d6fdb15ab8fdb` | `retired_snapshot_scaffold` | `identical_to_terminal_head` | Snapshot/export mechanism retired after immutable project authority landed. |
| `agent/local-estate-control-plane` | `d2444b507153acff90b0f3d3b80892ebf46afab0` | `landed_frozen` | `identical_to_terminal_head` | PR #55 landed as d511414daa0c7127c1e9cfdc64726979e6682e1f. |
| `agent/production-cleanup-inline-probe` | `45ef903f15dd3b813f51193371f688ab46cfad94` | `failed_delivery_diagnostic` | `identical_to_terminal_head` | Single-file repository transport probe; not product work. |
| `agent/production-cleanup-inline-probe-2` | `a0e19f4b9320ce551c6c6e1d61bfe3d3b00bcc9b` | `failed_delivery_diagnostic` | `identical_to_terminal_head` | Second repository transport probe; not product work. |
| `agent/release-candidate-discipline` | `18e3af22d03c32f280198ea4416c43abbb827357` | `harvested_competing_implementation` | `identical_to_terminal_head` | Concepts were harvested; parallel authority was not. |
| `agent/release-candidate-review-floor` | `0816d0cee08f77b1873fec9c03ee065db3154eda` | `retired_scaffold` | `identical_to_terminal_head` | Export workflow and placeholders only. |
| `agent/release-review-floor-v1` | `1249ab673e4250d4c5f8361a5712e1f031fe9f9b` | `retired_scaffold` | `identical_to_terminal_head` | Export workflow and placeholders only. |
| `claude/earcrate-v0.9.0-complete-wrz7lw` | `95cb411da8e99cafbd64b9f0769da77f7e0e2a99` | `deferred_unvalidated_branch` | `identical_to_terminal_head` | Candidate organs remain harvestable only through current contracts and real-rig receipts. |

Twelve non-main pointers matched their recorded terminal heads on 2026-08-02.
The PR #56 transport pointer had diverged after closure. Its closure head and the
verified payload harvested into #55 remain authoritative; later pointer content
does not.

## Resolved since v1

- PR #53 landed the append-only canon ledger.
- PR #55 moved the song-reader matrix push trigger to `main`, closing
  `repo.ci_cross_platform_main_trigger`.
- Exact fixture binding, committed Homelab blind review, node/evidence-scoped
  adoption, durable task operations, backup/restore, and public projection became
  production contracts.
- PRs #54 and #56 are closed and locked as resolved duplicate/transport lanes.

## Evidence boundaries retained

The *Flim* pack remains a community-symbolic witness under exact pack SHA-256
`a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52`.
It does not establish blind inference from the exact target recording.

The *Children* score remains authored symbolic authority: 130 BPM, 4/4, four
flats, printed harmony, alternate endings, Segno, `To Coda`, and `D.S. al Coda`.
It may be used as an answer key only after an independent score-blind audio ledger
is sealed.

The Pretty Lights first-30 proof, reported full-recording proof, reported v3
candidate, and committed v1 fixture remain four distinct evidence objects.

## Campaign fan-out

| Issue | Priority | Track |
| --- | --- | --- |
| [#58](https://github.com/BigBirdReturns/earcrate/issues/58) | P0 | Run the first explicit-root, read-only estate sweep |
| [#59](https://github.com/BigBirdReturns/earcrate/issues/59) | P0 | Run the Flim exact-recording blind control |
| [#60](https://github.com/BigBirdReturns/earcrate/issues/60) | P0 | Seal Children audio inference and score/audio convergence |
| [#61](https://github.com/BigBirdReturns/earcrate/issues/61) | P1 | Regenerate Children receipts and execute a sealed rack realization |
| [#62](https://github.com/BigBirdReturns/earcrate/issues/62) | P0 | Run the Pretty Lights provider tournament and reconcile candidate revisions |
| [#63](https://github.com/BigBirdReturns/earcrate/issues/63) | P0 | Complete Pretty Lights human, rights, and publication governance |
| [#64](https://github.com/BigBirdReturns/earcrate/issues/64) | P1 | Circulate ReviewPatch evidence and prove campaign learning |
| [#65](https://github.com/BigBirdReturns/earcrate/issues/65) | P1 | Enforce Floor host boundaries and settle normative policy |
| [#66](https://github.com/BigBirdReturns/earcrate/issues/66) | P1 | Ship reproducible installable distribution and verifiable archive custody |
| [#67](https://github.com/BigBirdReturns/earcrate/issues/67) | P2 | Lower real-time MixScore, key lock, controllers, and external sync |
| [#68](https://github.com/BigBirdReturns/earcrate/issues/68) | P0 | Publish canon v2 and freeze the historical branch retention map |

Sequence:

1. Land #68 so canon matches current production.
2. Run #58 on the real estate and bind exact available fixtures.
3. Run #59, #60/#61, and #62 in parallel.
4. Feed an accepted, revision-resolved Pretty Lights candidate into #63.
5. Use real review evidence to close #64.
6. Advance #65, #66, and #67 independently where private fixtures are not needed.

## Non-claims

This revision does not claim that a real estate sweep has run, any of the 87
Homelab targets has been accepted, exact target recordings are locally custodied,
a human has accepted a release candidate, rights are available, or the complete
Buffalo has passed.
