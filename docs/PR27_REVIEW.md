# PR #27 — full-body review: durability, OSS surfaces, and the aspirational stack

Three independent review passes over the whole 71-commit / +21.8k-line branch
(durability-architecture, OSS-adoption with 2026 license/Windows verification, and a
correctness skim of the newest unreviewed hunks), synthesized. Every finding cited
here was verified in code or by measurement; the blockers found were fixed and landed
as **v0.8.29** before this document was written.

## VERDICT: merge PR #27 to main now, as-is.

Both strategic reviewers reached the same call independently: sole developer, private
repo, 171/171 gates + package-verify + dist self-test green, and the branch IS the
two-machine transport — the box gets fixes by pulling it. An unmerged 21.8k-line
branch is itself the biggest durability risk (main is weeks and an analyzer-version
behind reality). Retroactive splitting buys review granularity nobody will use.
**Going forward: cap PRs at roughly one subsystem** — not ceremony; the handoff doc
demonstrably cannot keep its references valid across a diff this large.

## Fixed on the spot (v0.8.29) — review findings that didn't survive the day

- **BLOCKER — external vocal tail loop-tiled** (stutter-echo of the last words on
  essentially every external remix) → pure `fit_external_clip`, never tiles.
- **External vocal 14 ms fade-dips at every 4-bar seam** → pure `external_edge_fades`,
  fades only at the take's true edges.
- **Dist-only ModuleNotFoundError ×3** (function-level `from earcrate.` imports
  survive the column-0 strip; `/api/materials/regions`, `reference_recall`,
  `plan_reference_extraction` crashed only in the single file, only at call time) →
  imports hoisted; **the build now refuses to build any indented earcrate import**.
- **`resample_or_fit` was `np.interp`** — linear-interp low-pass in the render hot
  path; measured ~6% relative high-band loss per pass at 6% varispeed, compounding
  across layers → `scipy.signal.resample_poly`. Contributing (not sole) cause of
  presence-dark renders.
- **render_album counted gate-rejected renders as made tracks** (phantom WAVs on the
  tracklist) → rejected renders land in `skipped[]` with the gate reason.
- External render re-verifies the dropped file's PCM identity; feasibility demands
  bed-only turnover (`needed//3`, floor 2); gate sandbox unconditional; MB batch no
  longer caches transient errors.

## Durability — what will hurt, ranked (with smallest fixes)

1. **ANALYZER_VERSION inside `segment_id` orphans human curation.** Every analyzer
   improvement (the point of the 4060) re-keys every loop → approvals/locks/judgments
   orphaned. *Smallest fix:* drop the version from segment identity (file + samples +
   role + stem); `crate_staleness` already carries the "re-measured by newer analyzer"
   signal. **OWNER DECISION NEEDED**: doing this now orphans current v0.7.0 judgments
   ONE more time; not doing it orphans them EVERY bump forever. Do it before the box's
   next big curation pass, not after.
2. **app.py accretion** (6,607 lines, +1,364 this PR; `render_mashup` is 630 lines with
   a 340-line closure). The build ORDER is NOT the forcing function — private ownership
   of db/config/status is. *Smallest fix that stops the ratchet:* extract
   `core/store.py` (connect/schema/migrations/kv/staleness, app.py≈1441-1846) and pass
   it into new satellite code; new EarcrateCore methods become ≤15-line adapters.
   Also: `analyze_one` is a dead line-for-line duplicate of `compute_pcm_features` —
   delete it before the two copies drift.
3. **Two live models of "material" and "transition" with no cutover.** transitions.py/
   regions.py are excellent and SHIPPED DARK; production still runs the old ladder +
   fixed [8,4,2,1] stride. *Fix:* wire `propose_regions` into extraction behind
   `EARCRATE_REGIONS=1` with the frozen baseline as A/B comparator; delete the ladder
   when the box A/B confirms. Bounded window, one release cycle.
4. **Unenforced conventions:** MODULES tuple (a new test file not added never runs) →
   3-line completeness assert in run_gates; AGENT_HANDOFF line-number refs all went
   stale within this very PR → handoff schema v2 requires commit SHA + symbol names.
5. **Version constants manually synced** (ENGINE_VERSION / DISPLAY / CHANGELOG) and
   **persona JSON edits don't stale the crate** (tastespec hash exists but isn't in the
   crate stamp). Both are one-line stamps.

**Protect as invariants (the genuinely well-built):** the version/staleness lattice
(crate stamps + per-row analyzer_version + arrangement_sha + render-report sidecars);
the provider registry with noop defaults + honest capability probes; judgment-
preserving migrations ("human judgment survives machine convenience", enforced in
SQL); determinism discipline in the pure modules (no clock, no RNG, injected fetch);
CI's package↔dist loop. Every future heavy dependency enters through a provider seam
or it doesn't enter.

## OSS — adopt next, ranked (licenses/Windows verified 2026-07)

| # | Adopt | Replaces | License | Why |
|---|---|---|---|---|
| 1 | **Finishing chain: pedalboard per-role EQ + bus comp/limiter, then matchering 2.0 reference-master** behind a `FinishingProvider` seam | hand-rolled cut-only `simple_fft_filter` + tanh bus + `stable_presence_restore` band-aid | pedalboard GPLv3 (runtime use unencumbered), matchering GPLv3, both wheel-clean on Windows | **THE wound.** Matchering literally matches RMS/frequency-response/peak/width to a named reference track — "master every render against a real Girl Talk WAV" makes gate-passing brightness true *by construction*. Persona names its own reference (Pretty Lights masters against Pretty Lights). Judged by the gate we already calibrated. |
| 2 | **beat_this** (CPJKU) behind a `BeatProvider` seam | librosa beat_track + max-RMS downbeat phase hack | **MIT incl. weights** | madmom is DEAD for us (2018 PyPI, breaks py≥3.10, NC-licensed models — the older audit's #1 pick is wrong). beat_this is the ISMIR-SOTA successor, torch-optional exactly like demucs. Real downbeats fix everything downstream (sections, regions, groove, transitions). |
| 3 | **python-stretch** (Signalsmith) as a `transform_policy`; pedalboard/Rubber Band as the GPL alt | librosa phase-vocoder smear on residual pitch/stretch | MIT | The audible "watery cave" killer; policy string already lives in the transform cache key, so invalidation is free. Keep varispeed-first *planning* — that's ours. |
| 4 | **pyacoustid + chromaprint** (recording ID) + **audfprint** (chopped-sample search) in study/ | name-string cross-referencing | MIT / LGPL | Turns answer-key recall from name-guesses into recording-verified truth; audfprint (Shazam-style landmarks) finds unshifted chops; pitched chops → chroma/DTW alignment (we own both sides). |
| 5 | **allin1** as an *optional* SectionProvider (via the community fix fork) | every-4th-downbeat sections | MIT, but NATTEN dep is build-hostile on Windows | Real intro/verse/chorus/drop labels feeding MaterialRegion — wrap, don't require. |

Riding along: **mir_eval** to *measure* the beat/section provider swaps (prove the
adoption, don't assert it); a ~50-line JAMS export shim for ecosystem tooling. KEEP
hand-rolled: the Krumhansl key detector (every stronger option is AGPL/GPL/NC —
upgrade in place with chroma_cqt + Temperley profile vote), stdlib HTTP server,
`ulidish`, the deterministic journaled ingest (accept a beets library.db as a
high-trust tag source someday; don't replace).

**Proudly invented here — the actual IP, don't OSS-replace:** the answer-key recall
benchmark ("did the engine rediscover what Girl Talk proved"); TasteSpec personas +
per-persona spectral gates; varispeed-first transform planning with role budgets;
MaterialRegion role-capability crating; the receipts/determinism culture.

## The aspirational stack — voice, genre, lullabies, parody (nothing to invent)

Tiered by buildability. The through-line: **we already built the hard receiving end**
— demucs separation (verified live on the box) + the external-target remix path
(drop any vocal over a rebuilt bed, v0.8.29-clean). Everything below composes with it.
*(Statuses from training knowledge unless marked verified — re-verify licenses before
committing, same drill as the table above.)*

**Tier 1 — buildable now, mostly permissive, box-friendly:**
- **Voice swap/conversion:** **RVC** (Retrieval-based Voice Conversion, MIT) — train a
  voice from ~10 min of audio on the 4060, convert any vocal stem (sings, not just
  speech). Pipeline: demucs vocals ✓ → RVC convert → external-remix drop ✓. "This
  song, but sung by X." (so-vits-svc is the alternative; check its license first.)
- **Lullaby / "rendered on baby toys":** the Rockabye Baby! formula is a pipeline of
  parts we mostly have: **basic-pitch** (Spotify, Apache-2.0) melody→MIDI → simplify/
  quantize on our beat grid → **FluidSynth** (LGPL) with music-box/celesta/toy-piano
  SoundFonts → slow tempo (our transform planner) + gentle LPF + our loudness chain.
  Deterministic, CPU-only, genuinely shippable as a persona: `lullaby_v1` with its own
  spectral target. Lo-fi cuts: pedalboard tape/wow/LPF as a `FinishingProvider` preset.
- **Genre-swap the BED:** already shipped in spirit — that's what the 22 remix
  personas ARE (take the vocal, rebuild the backing in Branchez/Dilla/Pretty Lights
  style from library material). The aspirational version just gets better as OSS #1-#3
  land.

**Tier 2 — real but heavier:**
- **Parody songs** — decomposed, it's three parts, two of which are DONE:
  1. *Parody lyrics*: an LLM writing task (Claude, in-session — no OSS needed).
  2. *The instrumental*: demucs `no_vocals` ✓ (34 clean instrumental layers in the
     box's accepted render).
  3. *The new vocal*: **Path A (works today):** you sing the parody over the bed —
     the external-remix path places it, RVC optionally re-voices it (sing it badly,
     ship it in any voice). **Path B (synthesized):** melody from the original vocal
     stem via basic-pitch + new lyrics → singing synthesis (**DiffSinger** / NNSVS;
     GPT-SoVITS for speech-adjacent) — quality varies, moving fast; revisit quarterly.
- **Timbre/instrument morphing:** **DDSP** (Magenta, Apache-2.0) for monophonic
  timbre transfer (voice→violin, guitar→flute); transcribe-and-rerender
  (basic-pitch + FluidSynth/soundfont) for polyphonic parts.

**Tier 3 — research-grade, don't build on it yet:** full-mix neural genre transfer;
text-to-song generation (MusicGen weights are CC-BY-**NC** — license-hostile to our
direction); zero-shot everything. Watch, don't wire.

**Seam for all of it:** one `VoiceProvider` / `MorphProvider` pattern next to
`providers/stems.py` — noop default, GPU box registers the real thing, capability
probe tells the UI what's available. Same shape that made demucs painless.

## Sequencing recommendation

1. Merge #27. 2. Land the `segment_id` fix (owner call) + MODULES gate + store
extraction start. 3. OSS #1 (finishing chain) — it makes every existing persona's
renders pass their gates, including the Bill Withers flip. 4. OSS #2 (beat_this) +
wire regions cutover A/B. 5. Tier-1 aspirational: RVC voice-swap provider + the
lullaby persona (both are demos that make the whole system feel magical, and both are
weekend-scale on top of the seams). 6. Parody Path A end-to-end as the flagship demo.
