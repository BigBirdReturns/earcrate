# MIDI-SAG as an EarCrate compositional accompaniment organ

MIDI-SAG is admitted to the Generative Provider Floor because it attacks the Beggin failure through an explicit compositional pipeline rather than asking one latent model to infer every performance decision at once.

Its detected mode performs:

```text
source vocal audio
→ singing-vocal beat tracking
→ GAME vocal-to-MIDI transcription
→ AccoMontage2 chord harmonization
→ MuseControlLite / Stable Audio Open backing generation
```

Its ground-truth mode accepts a separately established vocal-MIDI authority. Its full-song mode permits section boundaries and section-specific backing prompts. The EarCrate campaign therefore treats MIDI-SAG as a distinct `compositional_accompaniment` strategy and compares it directly with ACE-Step `vocal_to_bgm` generation.

## EarCrate authority additions

MIDI-SAG itself is a multi-repository pipeline. A runnable EarCrate request must pin:

- the MIDI-SAG repository commit;
- Singing-Vocal-Beat-Tracking source and checkpoint identities;
- GAME source and checkpoint identities;
- AccoMontage2 source and model identities;
- MuseControlLite source and checkpoint identities;
- Stable Audio Open and codec identities;
- every configuration value controlling beat bounds, downbeat phase, key, chord style, chords per bar, section boundaries, and section prompts;
- the source vocal identity, optional vocal MIDI identity, explicit seed, Estate node, and GPU identity.

The repository’s Apache-2.0 license does not erase the separate licenses and model-card conditions of its bundled upstream components. EarCrate audits and records those components individually before execution.

## Beggin audition contract

The first task is `GF12-midi-sag-compositional-bgm`.

Input authority:

```text
Four Seasons vocal stem
same-work identity
source-free Beggin section plan
accepted or candidate beat/downbeat observations
```

Required candidate families:

```text
detected mode: vocal audio → beats → MIDI → chords → accompaniment
ground-truth mode: accepted vocal MIDI → chords → accompaniment
ACE-Step vocal-to-BGM control
Reference Zero v5 incumbent
naive co-play control
```

Intermediate usefulness is scored explicitly. A final audio candidate does not erase whether the beat tracker, transcription, harmonization, or section control failed. EarCrate can later replace one weak sub-organ without replacing the whole pipeline.

The owner frontier remains limited to four audio-distinct, strategy-diverse options. Full-song and release permission remain closed until a short candidate clearly beats the incumbent and naive control.
