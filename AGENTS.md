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
- `owner_audition_effect`: the new or improved listening decision;
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
- Keep copyrighted source media, private prompts, lyrics, paths, credentials,
  model assets, option maps, and owner-review authority outside the repository.

## Album One completion model

A track has an accepted album master only when the owner accepts the music, every
selected source and transform is accounted for, and the accepted render is
exactly reproducible. A system reference is complete only when the accepted gold
decisions are withheld and an inferred candidate blindly beats the declared
naive control. Rights and release remain a third, separate decision.

The honest current ledger is **0/7 accepted album masters** and **0/7 completed
system references**. `A1-07` is the active track.

## Product direction

EarCrate is an album-making system before it is a generalized platform. Its
current job is to complete Album One and convert repeated successful mechanisms
into the reusable floor. The Homelab factory, Reference Zero, generative provider
floor, score and MIDI organs, live runtime, retrieval systems, and TasteSpecs are
means to that end.

The controlling question is: which Album One track becomes more likely to reach
an owner-accepted master because this change exists, and what control will prove
it?
