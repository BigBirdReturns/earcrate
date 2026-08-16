# EarCrate agent constitution

## Source-of-truth hierarchy
1. Executable acceptance tests.
2. `JUKEBREAKER_SPEC_v2_CONSOLIDATED.md`.
3. Versioned TasteSpec profiles in `profiles/` and their schema.
4. Architecture and rebuild plans.
5. `CHANGELOG.md`.

`BUILD_SPEC` and Addendum-era documents are historical inputs only. They are not parallel constitutions.

## Nonnegotiable rules
- Do not lower a gate to make a render pass.
- Do not add rescue, degraded, floor-safe, single-crate, or old-render fallback behavior.
- Do not silently discard a selected layer during rendering.
- Do not let the composer select an atom that has not already passed transform feasibility.
- Do not write a WAV from an arrangement that fails its TasteSpec.
- Do not hardcode a successful arrangement for tests.
- Do not introduce network dependencies into the core runtime.
- Do not modify source audio.
- Preserve deterministic seeds, path containment, guarded writes, rollback, runtime accounting, source provenance, analysis multiprocessing, caches, and current user data.
- Do not claim completion unless the behavior has been exercised through the actual UI or API, not merely imported successfully.

## Product direction
EarCrate is a deterministic taste compiler. Girl Talk is the first acceptance profile, not the entire product. Build curation, compatibility, deterministic editable plans, exact rendering, and durable receipts around one compiler and one arrangement model.
