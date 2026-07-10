# PERSONA: girl_talk_v1 — the complete math

This is the canonical *narrative* reference for the first TasteSpec persona; the
canonical *machine* source is `profiles/girl_talk_v1.json` (versioned, hashed,
schema-validated) and a gate test forbids the two from drifting from the engine. Every number the
engine uses to decide *readiness*, *compatibility*, *arrangement*, and *acceptance*
is either stated here with its derivation, or it is a bug. Every constant lives in
ONE machine-readable place — `profiles/girl_talk_v1.json` — and everything else
(the flat runtime profile, the readiness aliases, the ranking weights) is a
projection of it. Section 10 maps every number to its code home.

The reference artist is Girl Talk (Gregg Gillis). The persona does not imitate any
specific track; it encodes the *measurable mechanics* of the style: dense sample
collage, recognizable foreground over a stable floor, fast source turnover, and
phrase-grid transitions.

---

## 1. Documented source data (the ground truth)

Sample counts are from the published sample lists for each album:

| Album | Samples | Runtime | Density |
|---|---|---|---|
| Night Ripper (2006) | ~167 | ~42 min | ~4.0 / min |
| Feed the Animals (2008) | ~300+ | ~53 min | ~5.7 / min |
| All Day (2010) | 372 | ~71 min | ~5.2 / min |

The persona targets the mature-era band: **5.2–5.7 events/min**.

Observed structural facts used below:
- **2–4 recognizable elements sound at once** (a rhythmic bed + 1–2 foreground
  identities + occasional spark), essentially never 1 and rarely 5+.
- The canonical texture is a **hip-hop/R&B a cappella over a rock/pop instrumental**
  (or the inverse) — i.e., foreground and floor come from *different* sources.
- Any one source stays under **~20% of a track** as foreground.
- Foreground recurrences of the same source within All Day are **≥ ~15 min apart**
  when they happen at all.
- Cuts and entrances land on the **phrase grid** (4/8/16-bar boundaries, downbeat
  aligned), not on arbitrary timestamps.

## 2. Density model (derived constants)

| Constant | Value | Derivation |
|---|---|---|
| `seconds_per_event` | **11.0 s** | 60 / 5.45 events·min⁻¹ (midpoint of the 5.2–5.7 band) |
| `sources_per_minute` | **5.5** | mature-era mean source-introduction rate |
| `min_layers` / `max_layers` | **2 / 4** | observed simultaneous recognizable elements |
| `max_source_share` | **0.20** | any one source < ~20% of a track as foreground |
| `source_seconds` | **11.5 s** | mean dwell per source event (runtime / samples, All Day) |
| `max_source_run_s` | **16.0 s** | a single source may not hold the foreground longer than ~1.5 events |
| `first_foreground_s` | **8.0 s** | a track must state a recognizable identity inside the first phrase |

**For a track of length T seconds:** it needs ≈ `T / 11` sample-events, ≈
`5.5 · T/60` distinct sources, and a bed + foreground available at every moment.
A 2-minute sketch: ~11 events, ~11 sources (`min_feasible_sources = 11`).

## 3. Rails contract (what a plan must satisfy before rendering)

Arrangement is compiled on three typed rails from approved EarAtoms
(`EAR_ROLE_ORDER` in `core/deps.py`):

| Rail | Ear roles | Coverage obligation |
|---|---|---|
| **floor** | DRUM_BREAK, BED_CHORD, RIFF_ID, TEXTURE, BASS_RIFF | ≥ **0.70** of the timeline (`floor_coverage`) |
| **foreground** | VOX_HOOK, VOX_VERSE, VOX_SHOUT, RIFF_ID | ≥ **0.50** of the timeline (`foreground_coverage`) |
| **spark** | PICKUP_FILL, DROP_HIT, TRANSITION_TAIL, TEXTURE, VOX_SHOUT | punctuation; no coverage floor |

Plus the hard timeline rules:
- first foreground entrance ≤ **8.0 s** (`first_foreground_s`)
- no silent gap > **2.0 s** (`max_silent_gap_s`)
- no single-source foreground run > **16.0 s** (`max_source_run_s`)

## 4. Tempo math (varispeed deck discipline)

The deck is **varispeed-only**: changing speed changes tempo and pitch together,
like two turntables. Synthetic (phase-vocoder) correction is a *residual*, kept
tiny. The exact relations:

- speed ratio for tempo match: `rate = target_bpm / source_bpm`
- pitch consequence of varispeed: `Δsemitones = 12 · log₂(rate)`
  (so ±6% speed ≈ ±1 semitone; the entire legal varispeed range moves pitch < 1.5 st)
- **octave folding**: before judging distance, fold source BPM by powers of 2 into
  the octave nearest the target (`fold_bpm_to_target`, k ∈ [−3, 3]) — analyzers
  disagree by half/double-time constantly and the phrase grid is identical.

Per-role transform budgets (`drydeck_transform_limits`), the non-degradation
ceilings:

| Role | max varispeed % | max residual synthetic pitch (st) |
|---|---|---|
| drum_anchor | 8.0 | 0.75 |
| bass | 8.5 | 0.90 |
| vocal | 6.5 | 1.15 |
| harmony | 8.5 | 1.25 |
| texture / fx | 8.5 | 1.00 |
| full | 7.0 | 0.90 |

**Percussion is keyless**: drum breaks are never key-gated (their detected key is
analyzer noise); pitched roles keep full key discipline (gate:
`test_percussion_is_keyless_but_vocals_are_not`).

**Deck BPM selection** is a lattice search, not a fixed choke point
(`score_bpm_lattice`): candidates = native BPM clusters in the pool (clustered at
1.5%) ∪ ±{0, 2.5, 5}% around any user target, bounded to [70, 180]. Each candidate
is scored `avg_transform_cost + 4 · (1 − usable_ratio)` over the whole pool; the
speed that keeps 40 loops clean beats the one that keeps 8 loops slightly cleaner.

## 5. Harmony math

**Key detection** is Krumhansl–Schmuckler correlation (`krumhansl_key`) against the
exact probe-tone profiles (normalized), scored by dot product over all 24
transposed profiles; confidence = `clamp01((best − second + 0.02) · 8)`:

```
major: 6.35 2.23 3.48 2.33 4.38 4.09 2.52 5.19 2.39 3.66 2.29 2.88
minor: 6.33 2.68 3.52 5.38 2.60 3.53 2.54 4.75 3.98 2.69 3.34 3.17
```

**Pitch-shift compatibility** (`compatible_pitch_shift`): raw chromatic distance is
considered alongside its ±12 folds, smallest magnitude first, accepted if within
the budget. At key-strictness ≥ 80 only the **consonant interval set
{0, ±3, ±4, ±5, ±7}** semitones (unison, minor/major third, fourth, fifth) is
allowed — the intervals that let a hook sit inside a foreign bed without sounding
like a mistake.

**Harmonic routing**: the arranger routes loops near their **native** keys through
key eras rather than forcing one global key — readiness therefore also scores
usability at native pitch (`target_key=None`), matching what the router will do.

## 6. Compatibility graph (typed edges)

Edges are typed relations — `vocal_over_bed`, `bass_over_drums`,
`spark_into_phrase` — with distinct cost functions (`atom_edge_score`). An edge
must score ≥ **0.54** (`min_edge_score`) to exist. Edge receipts record phrase
alignment, harmonic relation, transform cost, low-end conflict, and source
contrast. The graph is deterministic: same pool + params ⇒ same edges, and it is
the *only* path into composition (TasteSpec Feasibility Invariant, spec §v0.6.1):
the composer may select atoms **only** from the transform-feasible pool at the
chosen deck BPM/key.

## 7. Acceptance gates (post-render, measured on the WAV)

From ADDENDUM A / the Audible Truth Gate — a render passes only if:

| Gate | Threshold |
|---|---|
| dynamic variety | `rms_std ≥ 4.5 dB` |
| honest silence | `silence_ratio ≥ 0.01` |
| low-end ownership | `low200_share ≥ 0.48` |
| harmonic variety | ≥ 4 distinct dominant pitch classes over 3 min |
| audible coverage | absolute coverage, first-audible, largest silent gap, active ratio all measured for ≥ 1 min renders |

No fallback render is allowed. If the gates refuse, the run refuses with receipts.

## 8. Endless-set math (the "any folder → endless mashup" contract)

The exact math, implemented in `endless_sustain` (`ear/readiness.py`) and gated by
`test_endless_math_is_exact`:

Let `S` = deck-safe distinct sources in the crate, `E` = supplyable sample-events
(each atom ≈ 2 plays before it reads as a rerun, each source ≈ 3 fresh foreground
moments), `r = sources_per_minute = 5.5`, `spe = seconds_per_event = 11.0`.

```
no-repeat runtime  T = min( 60·S / r ,  E · spe )   seconds
```

Played on loop, every source recurs with period ≈ T. The set is honestly endless
iff `T ≥ min_recycle_gap_s = 900 s` (15 min — the observed floor on foreground
recurrence gaps in All Day). Therefore:

```
sources needed for endless = ceil( 900/60 · 5.5 ) = 83 deck-safe sources
```

That is the persona's headline requirement: **~83 distinct songs that survive
transform at a common deck tempo** make a crate endless; 55 sources sustain
exactly 10 minutes before recycling. `taste_readiness` and
`crate_readiness_audit` both report the `endless` receipt: no-repeat seconds, the
binding bottleneck (`sources` vs `events`), and the exact source count still
missing. This is why stem separation is the highest-leverage unlock — it converts
the scarce roles (clean drum beds, isolatable vocals) from rare to abundant
without adding a single new song.

## 11. The ranking model (how the artist ranks raw material)

The persona doesn't just gate a crate — it ranks it, the way the artist reaches
for material. Implemented in `rank_material` (`ear/readiness.py`), surfaced via
`rank_crate` / CLI `earcrate rank` / `POST /api/rank`, gated by
`test_girl_talk_ranking`. Five priorities, highest first, each mapped to a metric
the analyzer already computes so the ranking is grounded, not taste-by-assertion:

| Priority | Weight | Why it ranks here | Metric |
|---|---|---|---|
| **recognizability** | 0.34 | the entire payoff is recognition — the "oh, THAT song" hit | `hook_score` (+ `score`), role-weighted so hooks trade on it and beds barely do |
| **role clarity** | 0.24 | a clean isolatable vocal or a clean bed beats full-mix mush | `intelligibility`+mid salience (vox), transient+low (drums), `bass_score` (bass), `floor_score`/`bed_score` (bed) |
| **danceability** | 0.18 | it's party music; the floor has to move | `energy` + `transient_density` |
| **deck feasibility** | 0.14 | a hook you can't beatmatch to the crate tempo is dead weight | varispeed % to the nearest tempo island after octave folding |
| **contrast** | 0.10 | genre/era/key distance is the collision payoff | circle-of-keys distance from the crate centroid (weak until online metadata adds genre/era — see §9) |

Deck feasibility is a hard reality, not a soft weight: a maximum-contrast loop that
can't reach the tempo scores 0 there and sinks regardless — exactly the DJ truth
that an unbeatmatchable record stays in the bag. The output is ranked overall and
`top_by_role`, each entry carrying its five sub-scores as a receipt: this is the
curation surface — which of *your* loops the artist would actually pull, and why.

## 9. What the persona does NOT yet encode (honest backlog)

- **Chroma-trajectory matching**: loops are routed to era keys, not selected by
  measured chord movement (`chord_dna` is a receipt, not analysis).
- **EQ carving / sidechain**: low-end conflict is scored on the edge, but the
  renderer does not yet carve the bed around the vocal.
- **Stems**: not integrated; readiness names it as the fix when beds/vocals are
  the bottleneck.
- **Anti-aliased varispeed**: `np.interp` resampling; audible only above ~8%
  speed change, which the budgets already forbid.

## 10. Code map (number → home)

| Number | Where it lives |
|---|---|
| **canonical source (versioned + hashed)** | `profiles/girl_talk_v1.json` (schema: `profiles/tastespec.schema.json`) |
| runtime projection | `earcrate/core/deps.py: TASTE_PROFILES` = `flat_profile(load_tastespec(...))` — never edit numbers here |
| loader / hash / projection | `earcrate/tastespec/profiles.py` |
| drift protection | `tests/test_gates.py: test_persona_single_source` (JSON must equal enforced engine values) |
| density aliases GT_* | `earcrate/ear/readiness.py` (derived from the profile) |
| targets for length T | `earcrate/ear/readiness.py: girl_talk_targets` |
| readiness audit | `earcrate/ear/readiness.py: crate_readiness_audit` |
| endless math | `earcrate/ear/readiness.py: endless_sustain` |
| atom-level readiness | `earcrate/app.py: taste_readiness` |
| role transform budgets | `earcrate/deck/transform.py: drydeck_transform_limits` |
| octave folding | `earcrate/deck/transform.py: fold_bpm_to_target` |
| BPM lattice | `earcrate/deck/lattice.py: build_bpm_lattice / score_bpm_lattice` |
| key detection | `earcrate/deck/harmony.py: krumhansl_key` |
| consonant shift set | `earcrate/deck/harmony.py: compatible_pitch_shift` |
| typed edge scoring | `earcrate/app.py: atom_edge_score` |
| audio acceptance gates | `earcrate/judge/audio.py` |
| executable persona gates | `tests/test_gates.py` |

A second persona changes the numbers in `TASTE_PROFILES`, not the machinery: same
contract form — named roles, measurable gates, deterministic edge scoring,
explicit failure receipts.
