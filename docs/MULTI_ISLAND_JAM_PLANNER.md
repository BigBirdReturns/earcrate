# Multi-island jam planner

This branch adds one continuous, globally accounted arrangement over several exact tempo/key islands. The private workstation derives the source survival matrix and supplies a content-bound schedule. The repository compiler validates and lowers that schedule through the existing TasteSpec composer, transition primitive, renderer, run bundle, and source ledger.

## Public entrypoint

```python
EarcrateCore.propose_island_set(params: dict) -> dict
```

The request fixes the source-pool identity, transform and turnover policy identities, exact island BPM/key pairs, per-island source sets, capacities, required roles, phrase law, and equal-power phrase-boundary transition law. BPM and key are hard constraints. Source sets are exact and disjoint. Island boundaries do not reset source history.

## Planning and rendering

`earcrate.plan.islands` is pure. It canonicalizes request order, rejects cross-island reuse, rounds capacity down to complete four-bar phrases, allocates enough phrases to meet the requested duration, combines ordinary single-deck plans on one timeline, and emits one global source ledger.

The existing renderer remains authoritative. Island sections carry exact `time_start_s`, `duration_s`, and `section_bpm`; single-deck arrangements retain their old bar/BPM fallback. The existing equal-power transition path joins adjacent islands. Role stems include a transition/bus residual so the complete float stem set reconciles to the delivered master.

## Compatibility boundary

Ordinary `choose_taste_deck`, `compose_taste_arrangement`, `propose_taste_mashup`, and single-deck rendering remain unchanged unless the island-only parameters are present. Exact-deck selection, source allowlists, and global-use exclusions are opt-in.

## Private acceptance

No private source IDs or media are committed. After repository review, the local estate runs the sealed Proof 005 schedule: at least 1,800 seconds, unchanged transform and turnover laws, role-complete islands, phrase-boundary equal-power joins, zero source reuse, full stems, deterministic PCM rerender, and a ten-minute physical-device smoke.
