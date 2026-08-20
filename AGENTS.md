# EarCrate agent constitution

## Source-of-truth hierarchy

1. `ALBUM_ONE.md` and `configs/album_one/manifest.v1.json`.
2. Executable acceptance tests.
3. `PRODUCT.md`.
4. Track-specific accepted scores, manifests, bindings, verdicts, and receipts.
5. `JUKEBREAKER_SPEC_v2_CONSOLIDATED.md`.
6. Versioned TasteSpec profiles in `profiles/` and their schema.
7. Architecture and rebuild plans.
8. `CHANGELOG.md`.

The Album One ledger names the deliverables. Executable tests protect the
contracts. Track-specific evidence describes what actually happened. Generic
architecture does not overrule a track verdict. `BUILD_SPEC` and Addendum-era
documents are historical inputs only and are not parallel constitutions.

## Required pull-request declaration

Every pull request must state:

- `album_scope`: `A1-01` through `A1-07`, `album-wide`, or
  `infrastructure-with-track-demand`;
- `musical_gap`: the exact failure being addressed;
- `control_or_baseline`: the incumbent or naive mechanism the change must beat;
- `owner_audition_effect`: the new or improved listening decision, or `none` --
  and when it is not `none`, the exact track state each admissible verdict changes,
  under the admission rule below;
- `private_execution_required`: the local media, evidence, hardware, or
  credentials still required.

Work that cannot name a track-level or repeated album-wide demand is exploratory.
It may be retained as research, but it may not present itself as Album One
progress.

## Nonnegotiable rules

- Do not lower a gate to make a render pass.
- Do not add rescue, degraded, floor-safe, single-crate, or old-render fallback behavior.
- Do not silently discard a selected layer during rendering.
- Do not let the composer select an atom that has not already passed transform feasibility.
- Do not write a WAV from an arrangement that fails its TasteSpec or track contract.
- Do not hardcode a successful arrangement for tests.
- Do not introduce network dependencies into the core runtime.
- Do not modify source audio.
- Preserve deterministic seeds, path containment, guarded writes, rollback,
  runtime accounting, source provenance, analysis multiprocessing, caches, and
  current user data.
- Do not claim completion unless the behavior has been exercised through the
  actual UI, API, renderer, or owner-review surface.
- Do not treat provider installation, execution, benchmark passage, or signal
  sanity as musical acceptance.
- Do not begin with a tool and search for a song-shaped excuse to use it.
- Do not send work to the local Estate unless the repository already supplies the
  track contract, source requirements, controls, execution plan, and expected
  returned evidence.
- Owner reviews must disclose the intended musical delta and the invariant set for
  every option before listening. Blind provider or source identity only when that
  isolation answers a real question; never make the owner infer what changed by
  guessing from the audio.
- If every option shares the same dominant audible mechanism or defect, classify
  the frontier as non-discriminating and iterate that mechanism. Do not require an
  owner ranking of tiny downstream deltas.
- Keep copyrighted source media, private prompts, lyrics, paths, credentials,
  model assets, private option maps, and owner-review authority outside the
  repository. Public cut notes may disclose musical mechanisms without exposing
  private media or credentials.

## Execution policy

Four events are routinely collapsed into one:

```text
one candidate loses
one mechanism fails
one track closes
the whole program stops
```

Only the first three can follow from a negative musical result. The fourth never
follows. A losing candidate ends that candidate. A failing mechanism ends that
mechanism. A closed track ends that track. None of them ends Album One, and none of
them is a reason to pause source recovery, rendering, provider auditions,
system-reference execution, or work on a different commissioned track.

This is written down because the opposite happened for a long time. The evidence
controls prevented false claims, which was worth having. The scheduling habit they
grew into — treat every uncertainty as a gate, every gate as a pull request, every
negative result as a stop instruction — prevented throughput, which was not.

Tracks advance in parallel. An owner review blocks acceptance of the candidate it is
reviewing, and nothing else.

### Continue without asking

```text
source searches and exact binding
non-destructive estate reads
tool installation under versioned custody
provider execution
score extraction
rendering
comparison construction
test and gate repairs
branching, committing, pushing, and pull-request creation
evidence replication
work on other Album tracks
```

### Interrupt the owner only for

```text
a short genuine musical verdict on an admissible object
spending money or entering credentials
destructive deletion
publication or rights authority
an irreversible product decision
```

The first line is the one that gets abused, so admission is defined below rather than
left to judgement.

### What a pull request must do

At least one of:

- produce owner-auditionable audio;
- bind material required to produce audio;
- repair a defect directly preventing audio.

A pull request that does none of these may still be worth landing, but it is not
Album One progress and it may not become a prerequisite for production work.

### What an owner review may cost

An owner review is the smallest object that can answer its question, and no larger.
If two candidates are identical outside one span, the review leads with that span and
the context around it; the full-length objects travel as optional material, not as the
task. Handing over two four-minute files whose difference is ten seconds is not a
listening task, it is a search task, and it spends owner attention on the wrong thing.

### Owner review admission

Sizing a review correctly does not make it admissible. The question before "how small"
is "may this reach a person at all", and it has an answer.

An object may reach the owner only when the verdict can immediately:

1. select or reject a complete track candidate;
2. select or reject one localized edit shown in sufficient full-context;
3. accept or reject a master.

These objects may never create `owner_review_pending`:

```text
diagnostic
engineering render
score reduction
provider probe
control-candidate measurement
partial excerpt whose result cannot move the track
```

Every review request must name the exact track state that each admissible verdict
changes. When no verdict can advance, close, or materially redirect the track, the
system refuses to ask.

The gate is one question:

> **Can this artifact's verdict change a track-level authority state immediately?**

When the answer is no, it never reaches the owner. A machine disposition is recorded
instead, and the lane keeps running. This is not a courtesy. An inadmissible review
spends the one input the program cannot manufacture, and it spends it on a question the
machine was supposed to answer.

An artifact does not become admissible by being placed underneath something else. A
reduction with a bed under it is still a reduction; a probe that gained a second option
is still a probe.

One limit is known rather than discovered. A completed system reference is a declared
authority state in this repository, and it is not a track state -- so a verdict on an
inferred candidate against its naive control cannot reach the owner under this rule, and
that lane is decided on its declared measurements instead. Widening the rule to admit a
fourth object is an owner decision, and it has not been taken.

### Who executes what

```text
LOCAL ESTATE
Execute source binding, analysis, rendering, provider work, gates,
branches, receipts, and parallel tracks without asking.
Stop only when a real owner-admissible musical object exists.

CLOUD ASSISTANT
Triage diagnostics and issue machine dispositions.
Do not convert provider probes or engineering evidence into owner tasks.
Do not invent another architecture program from a negative result.

OWNER
Hear complete-track candidates, properly contextualized edits, and masters.
No provider benchmarks, no receipt adjudication, no status approvals.
```

### How a report ends

A report that contains no owner-admissible artifact ends with the next action already
running. Not "say the word", not "what do you want", and not another review request.
Asking what to do next is the same failure as asking for an inadmissible verdict: it
converts machine work into owner work.

### No new organs are authorized

The mechanisms already exist:

```text
source custody          audio analysis          score and OMR ingestion
MIDI authority          crate retrieval         rack binding
deterministic rendering MixScore                ACE-Step
project and DAW export  review authority        mastering
system-reference governance
```

The work is to connect them into repeated musical output. A new organ still enters
only under the rule below — a declared track gap, or an album-wide bottleneck two
tracks demonstrate — and "the existing organ is inconvenient here" is not that.

## Album One completion model

A track has an accepted album master only when the owner accepts the music, every
selected source and transform is accounted for, and the accepted render is
exactly reproducible. A system reference is complete only when the accepted gold
decisions are withheld and an inferred candidate blindly beats the declared
naive control. Rights and release remain a third, separate decision.

The honest current ledger is **1/7 accepted album masters** and **0/7 completed
system references**. `A1-07` is the active track: its master is accepted, and its
withheld-answer system reference is still open. Acceptance required a verdict that
named the mastered object itself. Machine qualification is never acceptance, and a
transparent transform of an accepted object is still a different object.

## Product direction

EarCrate is an album-making system before it is a generalized platform. Its
current job is to complete Album One and convert repeated successful mechanisms
into the reusable floor. The Homelab factory, Reference Zero, generative provider
floor, score and MIDI organs, live runtime, retrieval systems, and TasteSpecs are
means to that end.

The controlling question is: which Album One track becomes more likely to reach
an owner-accepted master because this change exists, and what control will prove
it?
