# Proof 003 — persona-and-transition delta (issue #116, candidate 2)

Starts against the surviving incumbent: **the corrected v0.8.30 external-target
render** (`S:\EarCrate\jam\flight-001\proof-002\3_V0830_CORRECTED.wav`,
Proof 002: PROMOTE, result `6309354571dc…`).

Bounded scope — the v0.8.29 musical additions, one factor at a time:

- ONE best-supported persona (not all twenty-two)
- recurrence-hook selection
- per-beat state
- anchor-derived MaterialRegions and transitions

Port sources staged in `_port_hunks\`: `plan/transitions.py` (577 diff lines),
`materials/regions.py` (285), `tastespec/remix_builder.py` (183), plus their
tests. Persona profiles come from PR #27's `profiles/remix_*_v1.json`.

Execution law inherited from Proof 002: reproduction gate first (all additions
disabled must re-emit the incumbent at PCM identity via the exact float grid —
never rebuild from rounded receipt values); one-factor and pairwise deltas;
each critic votes only after separating its paired damaged control; machine
dispositions only; no owner listening request.
