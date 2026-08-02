# EarCrate historical branch retention map

Audit date: **2026-08-02**  
Audited main: `d511414daa0c7127c1e9cfdc64726979e6682e1f`  
Machine authority: `docs/canon/canon-ledger.v2.json`

Remote branches are evidence-bearing refs, not an active backlog. A branch can be
retained while its implementation is landed, harvested, retired, deferred, or
known to be a failed transport. The status determines whether code may re-enter;
the mere existence of a ref does not.

| Ref | Terminal head | Status | Current pointer check | Authority |
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

## Operational rule

- Do not develop new work on these historical refs.
- Do not merge a deferred or retired branch wholesale.
- Re-admit a candidate organ through a fresh branch from current `main`, current
  contracts, current fixtures, and current gates.
- Do not delete or force-move a historical ref until #66 produces an independently
  restorable archive manifest and an explicit owner retention decision.
- The drifted PR #56 transport pointer has no authority beyond its recorded
  terminal closure head and the exact payload already harvested into PR #55.
