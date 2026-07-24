# First-30 cephalopod song-reader proof

This receipt records the private first-30-second Pretty Lights experiment that
forced EarCrate to read the recording before attempting composition or library
realization. The reference audio is not bundled; its byte and decoded-body hashes
bind this report to the exact locally supplied recording.

## Thesis result

The proof passes the narrow thesis:

> A distributed recurrence reader can recover the reference's heartbeat, repeated
> audible objects, exact instance timeline, and one-time foreground entrance more
> faithfully than the rejected note-generating paths, without inventing chords or
> using same-time PCM for recurrent events.

- Tempo proposal: **92.285156 BPM**
- Phrase heartbeat: **8 beats**
- Exact observations: **237**
- Canonical events: **76**
- Event instances: **186**
- Recurrent events: **73**
- Recurrent instances: **183**
- Audible recurrent instances using their own same-time PCM: **0**
- One-time foreground entrance: **21.350748 s**

## Raw correspondence

No reference-conditioned mastering or post-hoc spectral matching is used.

| Artifact | Onset correlation | Mel-frame cosine | Chroma cosine | Waveform correlation |
|---|---:|---:|---:|---:|
| Rejected 158-event neutral proof | -0.0167 | 0.7653 | 0.6997 | 0.0093 |
| Rejected full-song generator | -0.0201 | 0.7845 | 0.6807 | -0.0074 |
| Recurrence leave-one-out | **0.5021** | **0.8712** | 0.7407 | -0.0093 |
| SongGenome diagnostic | **0.7812** | **0.9136** | **0.7891** | 0.3615 |

The leave-one-out render executes each audible recurrent cell using another
occurrence from the recording. Unique cells remain silent, so raw waveform
correlation is not the target of that proof. The diagnostic adds two explicitly
named same-time residual objects: the one-time drop transition and a foreground
SourcePhrase candidate.

## What is proved

- Every event instance cites exact PCM-frame observations.
- Repetition is represented as one canonical object plus exact instances.
- The selected phrase cycle is non-trivial rather than a one-beat self-match.
- Physical layer projections reconstruct the input before recurrence analysis.
- No invented MIDI chord, pad, melody, or sustain contributes audio.
- No symbolic cue contributes PCM.
- The new reader materially outperforms both rejected generators on raw onset and
  timbral correspondence.

## What is not proved

The one-time residual is extracted from the comparison reference. It is a teacher
artifact and is not publication-eligible. Final execution still requires an
eligible SourcePhrase or sealed rack for each accepted event, rights review, and a
project-bound producer verdict.

The machine-readable receipt is
`proofs/song_reader/pretty_lights_first30.proof.json`.
