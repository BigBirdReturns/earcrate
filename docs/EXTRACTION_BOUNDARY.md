# The extraction boundary

A1-07 reached an accepted album master through a bespoke stack. That stack cannot be
the permanent architecture: a new track should describe music, not rebuild workflow
infrastructure. But extraction has a failure mode of its own — generalizing from one
implementation freezes that implementation's assumptions into a framework, and the
next track then fights the framework instead of the music.

So the boundary is drawn by evidence, not by naming.

## The threshold

A concept may become shared machinery only when all four hold:

1. **Two materially different concrete implementations exist.**
2. **Shared invariants were identified by comparing them**, not by anticipating them.
3. **The proposed shared API needs no track-id branching.**
4. **The shared contract makes no source-modality assumption** — nothing that
   presumes recorded audio, or symbolic input, or a particular renderer.

If a proposed abstraction needs

```python
if track_id == "A1-07":
    ...
elif track_id == "A1-02":
    ...
```

it is not ready. That branch is the evidence that the invariant has not been found
yet.

## Extracted, because it already survived more than one use

| Piece | Evidence |
| --- | --- |
| `evidence/identity.py` | seal and canonical JSON written twice; a cross-check gate forces agreement |
| `evidence/receipts.py` | the sealed body-free receipt was written four times; two landed with pointers that no longer resolved |
| `evidence/provenance` (tree digest) | implemented twice, and its file set was misclassified twice — package-wide instead of audio-affecting |
| `album/transitions.py` | the ledger transition was hand-applied three times and produced a stale-copy defect every time |
| Bound verdicts | a verdict bound to an object identity was written twice: monitoring→render PCM, acceptance→master PCM and container |
| Master states | `frontier_selected → master_qualified → master_accepted` was designed track-agnostic and immediately caught a real over-claim |

The common property is that each one had **observable failure modes independent of
Beggin's musical structure**. They are about authority, identity and staleness — not
about how Frankie's phrases are placed.

## Deliberately not extracted

`ArrangementGraph`, `PerformanceRealizer` and `FrontierBuilder` are **not** shared
framework types, and no module under `earcrate/evidence/` or `earcrate/album/` may
introduce them.

Track-local implementations may exist and should. What may not exist yet is a shared
abstraction, because A1-07 is the only instance and extracting from it would encode
these as if they were universal properties of a track lane:

```text
recorded-clip placement
phrase-map form
timing-law frontier construction
protected PCM regions
```

A1-02 is specifically commissioned to challenge all four: it starts from symbolic
performance data and must realize a played object, so it has no donor clips to place,
no phrase map of source windows, no timing-law frontier, and no protected region
inherited from an incumbent render.

The extraction happens after A1-02 exists concretely, by comparing two real
implementations. Until then, a shared arrangement abstraction would be invention
wearing the costume of extraction.

## Mastering is a contract, not a policy

`MasteringPlan` is extracted, but A1-07's linear-gain policy is not the universal
algorithm. The shared object carries source identity, ordered processing stages,
declared signal constraints, a determinism policy, section-invariance checks, refusal
conditions, execution receipts and output identities. A1-07's plan is one instance of
it:

```text
stage: linear_gain
gain_db: +2.5
limiter_allowed: false
eq_allowed: false
dither_allowed: false
```

A future track may legitimately need EQ or limiting. The shared machinery verifies
that the **executed chain matches the declared plan**. It does not prohibit
processing globally.

## The system-reference challenge is a scaffold

The state machine is extracted:

```text
not_started → prepared → answer_withheld → execution_complete → evaluated → passed | failed
```

together with the authority boundary: withheld material identity, allowed evidence
set, executing environment, evaluator, success criteria, result receipt, and the rule
that `system_reference = true` requires a passed challenge.

The recovery *mechanism* is deliberately absent. It is not designed yet, and
inventing it before A1-07's challenge is specified would repeat exactly the mistake
this document exists to prevent.
