# PERSONA: troubadour_v1 — the medley contract

Canonical machine source: `profiles/troubadour_v1.json` (versioned, hashed,
drift-gated by `test_persona_single_source`). This is the narrative reference.

## 1. The reference craft

Reference artists: **Pat & Sean Kelly** (@patandseankelly) — twin troubadours whose
catalog is the cleanest public corpus of *medley craft*: acoustic mashups and
medleys built live with voices and guitars. Where Girl Talk's unit is the
**collision** (foreign layers stacked), the troubadour's unit is the **splice**
(sequential hooks stitched over one continuous harmonic bed). Same recognition
payoff, opposite mechanics.

Observed from the catalog:

| Form | Evidence | Rate |
|---|---|---|
| stunt medley | "Smells Like A Medley (Of Mash-ups)" — 22 songs in 1:34 | ~14 songs/min (ceiling, gag form) |
| standard medley | typical 1–2 min multi-song cuts | ~2.5–3 songs/min, ~15–25 s per source |
| two-song blend | "Sailor Song + Caramel (FULL VERSION)" 3:12 | 2 sources, full-length interleave |

## 2. The mechanics the profile encodes

- **One harmonic bed, whole set.** The accompaniment holds a single progression
  (or a planned ladder); songs enter *because* they fit it. The deep theory: huge
  swaths of pop share a chord family (I–V–vi–IV and its rotations), so a constant
  bed can host dozens of foregrounds. → `floor_coverage 0.95`, `min_layers 1,
  max_layers 2` (voice + bed, occasionally two voices).
- **Constant key via transposition (capo logic).** They move every song to the
  set's key. Our deck analog is varispeed + bounded residual pitch — budgets are
  the enforced deck limits, identical to every persona (gated).
- **Splices land on phrase boundaries in the same chord slot** — enter on the
  matching bar of the progression, never mid-phrase. → phrase-grid discipline the
  engine already enforces; `max_silent_gap_s 1.0` (a medley never breathes dead air).
- **Recognition through melody continuity**, not density: `objective_weights`
  push recognizability (0.38) and deck feasibility (0.20 — an untransposable song
  is useless in a one-key set) above danceability (0.10).
- **Turnover**: `source_seconds 22`, `max_source_run_s 45`, ~2.7 sources/min —
  the standard-medley band, not the stunt ceiling.
- **Vocals must be intelligible** (`min_vocal_intelligibility 0.55`,
  `max_mid_mask 0.50`): the form is *sung words over accompaniment*; a masked
  vocal has no reason to exist here.

## 3. Honest gaps (what this v1 cannot yet do)

- **Chord-progression matching** is the troubadour's real selection key
  (songs grouped by shared progression) and the analyzer does not extract
  progressions yet (`chord_dna` backlog item). v1 approximates with key/consonant
  -interval compatibility; progression equivalence (rotation-invariant matching
  of I–V–vi–IV families) is the upgrade that makes this persona sing.
- **Lyric-aware splicing** (hand-off on a shared word/theme, a Pat & Sean
  signature) needs stems + transcription — same unlock chain as everything vocal.
