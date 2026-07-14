# OSS integration audit — what we use, what we hand-rolled, what people expect

Honest pass over "are we reinventing wheels / ignoring standard OSS a system like
this should use." Split into: good choices we already made, hand-rolled things
where a stronger standard library exists, and the data-source picture.

## Already using the right, expected libraries
- **numpy / scipy** — numerics. Standard.
- **librosa** — MIR primitives (STFT, chroma, onset, tempo). Standard.
- **soundfile + ffmpeg** — audio I/O / decode. Standard.
- **pyloudnorm** — integrated LUFS normalization. Standard (ITU-R BS.1770).
- **mutagen** — tag reading. Standard.
- **demucs (+torch)** — stem separation. Standard/state-of-the-art (optional, GPU).
- **sqlite** — local store. Right call for a local-first app.

## Hand-rolled where a stronger, EXPECTED standard exists (the real gaps)
Ranked by leverage.

1. **Beat + downbeat + structure — the biggest gap.** We use librosa's `beat_track`
   and our own crude `_estimate_downbeats` (max-RMS phase) + `_estimate_sections`
   (every-4th-downbeat + energy label). Both reviewers flagged this: weak downbeat
   confidence, no real phrase/section functions. The expected tools:
   - **madmom** — RNN/DBN beat + downbeat tracking, the MIR standard for this; gives
     real downbeat probabilities (exactly the `bpm_confidence`/meter-hypothesis
     signal the transition engine wants).
   - **allin1** (All-In-One Music Structure Analyzer) — ONE model returns beats,
     downbeats, tempo, key, AND functional segments (intro/verse/chorus/bridge/drop).
     This is essentially the "AnalysisV2 / MaterialRegion" upgrade the review asked
     for, off the shelf.
   - **BeatNet** — joint real-time beat/downbeat.
   Adopting madmom or allin1 would directly close the transition-engine's
   confidence + section-type holes and de-risk the whole MaterialRegion path.

2. **Time-stretch / pitch-shift (the actual DJ transforms) — render quality.** We
   hand-roll varispeed and lean on librosa's phase vocoder (audibly artifacty on
   big shifts). **Rubber Band Library** (via **pyrubberband**) is the industry
   standard for high-quality independent time/pitch — what anyone would expect a
   DJ/mashup tool to use. This is the single biggest render-fidelity lever.

3. **Key detection.** Hand-rolled Krumhansl-Schmuckler. **Essentia** (KeyExtractor,
   `edma`/`bgate` profiles) or madmom's key model are stronger and standard.
   Essentia is the big comprehensive MIR library we don't use at all — worth a look
   for key, danceability, HPCP, onset, and more.

4. **Sample / recording identification — the killer for the cross-reference.**
   **Chromaprint / AcoustID** (open audio fingerprints) + **MusicBrainz** (CC0
   metadata) can identify whether a library track literally IS a given recording —
   turning our artist-NAME cross-reference into precise recording-level matching,
   and it's the right tool for "extract the samples from the Girl Talk audio" (see
   `plan_reference_extraction` for the documented-timestamp path; fingerprinting is
   the discovery path for undocumented samples). Note: whole-recording fingerprints
   struggle with chopped/pitched fragments, so for chops, chroma/CQT cross-
   correlation or DTW **alignment** against owned candidate sources is the technique.

5. **Time-varying harmony / chords.** Our harmony is a coarse track-level key.
   **madmom** chord recognition or Vamp plugins (**Chordino / NNLS-Chroma**) give
   per-beat chords — the local-harmony signal the transition review wanted.

6. **Structure (if not allin1).** **msaf** (Music Structure Analysis Framework).

7. **Interop + evaluation.** **JAMS** (JSON Annotated Music Specification) is the
   MIR-standard annotation format — our analysis/reference JSON could conform so it
   interops with the ecosystem, and **mir_eval** gives standard metrics for beat/
   segment/key evaluation (useful for the recall/quality benchmarks).

## Data sources — "online DBs living in the project"
- **Proprietary, cannot mirror:** WhoSampled, Tracklib. No open API; ToS forbids
  wholesale copying. Per-album breakdowns as **cited, curated** answer keys
  (`earcrate/reference/*_samples.json`, indexed in `index.json`) are reference
  fair-use — that's what we build, and it should stay curated, not a scrape dump.
- **Open, pull legitimately:** **MusicBrainz** (CC0; has some sample/derivation
  relationships + full recording metadata), **AcoustID** (open fingerprint↔MBID),
  **Discogs** (open data dumps + API; release/label/year). These are the right way
  to grow an in-project, license-clean reference DB and to get precise recording
  IDs for the cross-reference.

## Recommended adoption order (each is box-verifiable, adds a dependency)
1. **madmom or allin1** → beats/downbeats/structure (fixes the kernel gap; unlocks
   MaterialRegion + transition confidence). Highest leverage.
2. **pyrubberband (Rubber Band)** → transform quality (render fidelity).
3. **Chromaprint/AcoustID + MusicBrainz** → precise recording ID (turns artist-level
   coverage into "you own THIS sampled record"; audio sample extraction).
4. **JAMS + mir_eval** → interop + standard evaluation of the recall/quality gates.

All are additive behind the existing seams (analyzer, StemProvider-style provider,
transform layer), so they can land incrementally and be measured against the frozen
baseline rather than as a big-bang rewrite.
