# DINNER LOCK — everything discovered & decided since the album launch

Consolidated so that when the dinner album lands it is judged with FULL context,
nothing lost. Branch `claude/code-buddy-communication-bp04lk`, gates 149/149,
25 personas.

## DISCOVERIES

1. **Remix reframe.** A remix = ONE foreground element over a bed REBUILT in a
   producer's style — distinct from the three mashup personas (collage/medley/
   album-marriage). External target vocal + style-bed.
2. **22 remix personas** built from web-researched style fingerprints via the gated
   `build_remix_persona` (Branchez, Pretty Lights + the 20-producer roster). Each
   with tempo/density/groove + its own spectral target.
3. **Per-persona spectral gate.** `drydeck_quality_gate` takes an optional
   `spectral_target`; each persona is judged on its OWN aesthetic (warm vinyl
   Pretty Lights passes where bright Girl Talk would fail). Default = GT, so
   existing personas unchanged.
4. **Answer-key recall benchmark.** `reference_recall` grades the engine's OWN
   discovery against a master's documented pairings — timed-overlap (Girl Talk)
   OR same-track co-occurrence (Donuts-style). Reports recall + the `missed[]`
   gap = what it should discover but doesn't.
5. **MATERIAL is the binding constraint (the big one).** Library manifest (14,336
   tracks, indie/rock/alt-heavy) cross-referenced vs the answer keys:
   - **J Dilla / Donuts: 0/59 source artists owned (0%)** — the whole soul-chop
     roster (Madlib/Premier/9th/Nujabes/Kanye-chipmunk) is starved here. No engine
     change fixes that; it needs the records.
   - **Girl Talk: 24/274 (9%)** — pop/rock pillars owned (Radiohead, Stones, Who,
     U2, Daft Punk, Arcade Fire, Rihanna, Katy Perry, Wale).
   - Library's real strength: indie/folk/rock → **troubadour** style refs all
     present (Bright Eyes/Elliott Smith/Iron & Wine/Sufjan/Sun Kil Moon/Fleet Foxes).
   - **Grey Album is a live target:** we own 226 Beatles tracks (its entire
     instrumental bed).
6. **Only ~1,200 / 15,290 files analyzed (8%) per the manifest** — crates are THIN.
   Album quality is currently material- and analysis-limited, not just mix-limited.
7. **Girl Talk sample extraction.** We own his audio + the timed map, so
   `plan_reference_extraction`/`sample_cut_list` resolve exactly which sample
   regions are cuttable from his own tracks (box slices the WAVs).
8. **OSS gaps (docs/OSS_INTEGRATION_AUDIT.md).** We use the right libs but
   hand-rolled the flagged weak points: beats/downbeats/structure → **madmom/allin1**,
   transforms → **Rubber Band**, recording ID / sample fingerprint → **AcoustID+
   MusicBrainz**, key → Essentia, interop/eval → JAMS/mir_eval.
9. **In-project answer-key corpus** (`earcrate/reference/index.json`): 8 graders —
   Girl Talk, Donuts, Notorious xx, Pat & Sean, Grey Album, Endtroducing, Since I
   Left You, Pop Culture. Material coverage vs the library (docs/MATERIAL_COVERAGE.md):
   Grey Album 50%, Notorious xx 50%, Pop Culture 23%, Pat & Sean 16%, Girl Talk 9%,
   Endtroducing 2%, Donuts 0%, Since I Left You 0%.
10. **The two most-latent classics are each ONE ACAPELLA AWAY.** Grey Album: we own
    the FULL Beatles instrumental bed (226 tracks) — missing only the Jay-Z Black
    Album acapellas. Notorious xx: we own the FULL The xx bed (32 tracks) — missing
    only the Biggie acapellas. Acquire those two acapella sets and both albums
    become directly rebuildable by the notorious/remix_dangermouse personas. This
    is the single most actionable material move.

## DECISIONS

- Remix personas are a first-class mode; roster built as calibration starting
  points (numbers tunable on the box, provenance says so).
- Per-persona spectral gate stays additive (default GT).
- `machine_defaults.json` left as-is — private repo, may never release.
- Proprietary sample DBs (WhoSampled/Tracklib) NOT mirrored (ToS); grow the corpus
  via cited breakdowns + OPEN sources (MusicBrainz/AcoustID/Discogs).
- Grade against library-COMPATIBLE producers first (material finding); don't waste
  harvest on 0%-coverage crates.
- Morphing ("rendered on baby toys") = a future transform-provider seam; cheap
  deterministic lo-fi DSP first, neural voice/timbre later.
- Transition engine Steps 1–2 are LIVE but additive (don't drive composition yet);
  the flip + Patches 3–5 are box work.
- Gate recalibrated to real Girl Talk truth (low200 a CEILING, presence a real
  floor); companion vocal-pair caching; recipe-aware stem keys.

## ALBUM EVALUATION RUBRIC (apply when it lands)

The dinner album renders the MASHUP personas (girl_talk / troubadour / notorious).

1. **Judge each track with its PER-PERSONA gate**, not the global GT profile.
2. **Expect presence-dark FLAGS** — the bed high-pass mix fix is still pending;
   flagged ≠ broken (see gate_recalibration in AGENT_HANDOFF.json).
3. **Material context:** girl_talk ~9% source overlap; troubadour material-rich but
   fails on composition (can't sustain the continuous medley bed); notorious owns
   The xx beds but not the Biggie acapellas.
4. **Analysis is thin (8%)** — thin crates cap coverage/diversity; more analyze
   passes are the lever before more personas.
5. **Cross-check discovery:** `reference_recall("earcrate/reference/girltalk_samples.json","girl_talk_v1")`
   → recall + `missed[]` (the discovery punch-list).
6. **Report** the tracklist + per-persona gate verdicts + which flagged tracks are
   presence-dark vs genuinely off, and tie each to the material/analysis context
   above — don't call a material-starved persona an engine failure.

## NEXT (post-album, owner's pick)
- Adopt **allin1/madmom** (beats+downbeats+structure) — fixes the kernel gap +
  unlocks MaterialRegion. Highest leverage. Box.
- **AcoustID+MusicBrainz** — precise recording-level cross-reference + audio sample
  extraction.
- Wire the **external-target remix path** (drop a fresh vocal → style-bed).
- **Bed high-pass mix fix** → gate-clean renders.
