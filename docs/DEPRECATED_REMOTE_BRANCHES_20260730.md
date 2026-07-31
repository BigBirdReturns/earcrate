# Deprecated remote branch residue — 2026-07-30

These remote heads have no open pull request and are not ancestors of reconciled
`main`. They are preserved evidence, not active product lanes. Their exact heads
were captured in the local `remote-branches-20260730` archive bundle before any
contained branch cleanup.

| Branch | Preserved head |
| --- | --- |
| `agent/forge-material-breeder-v0` | `d27c0b1ac1ebf464ddec726105c5fdd546f02563` |
| `agent/gate8-canonical-integration` | `3b70d343997470d7af93daa84558306db3abdf0f` |
| `agent/integrated-score-cutover` | `b307dd613ffc61c7efa2faea2e8d6fdb15ab8fdb` |
| `agent/production-cleanup-inline-probe` | `45ef903f15dd3b813f51193371f688ab46cfad94` |
| `agent/production-cleanup-inline-probe-2` | `a0e19f4b9320ce551c6c6e1d61bfe3d3b00bcc9b` |
| `agent/release-candidate-discipline` | `18e3af22d03c32f280198ea4416c43abbb827357` |
| `agent/release-candidate-review-floor` | `0816d0cee08f77b1873fec9c03ee065db3154eda` |
| `agent/release-review-floor-v1` | `1249ab673e4250d4c5f8361a5712e1f031fe9f9b` |
| `claude/earcrate-v0.9.0-complete-wrz7lw` | `95cb411da8e99cafbd64b9f0769da77f7e0e2a99` |

Do not merge, rebase, or resume these branches by default. Admit a unique organ
only through a new real-path failure or acceptance run against current `main`.
Deletion requires an explicit decision because each head carries commits not
currently reachable from `main`.