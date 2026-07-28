# Community and OSS commodity sweep: DJ source transports

Sweep date: 2026-07-27

This sweep asks which parts of a live mashup engine are mature commodities and which
parts must remain EarCrate authority. It is intentionally broader than a dependency
shopping list. GPL systems can be excellent architecture references without being
eligible linked dependencies under EarCrate's current PolyForm Noncommercial
distribution terms.

## Decision summary

Adopt now:

- the existing ffmpeg decode boundary for broad source-format support;
- NumPy float32 buffers, SciPy polyphase resampling, and SoundFile float WAVs for the
  deterministic offline proof;
- the existing EarCrate source hashing, exact execution-ledger, per-bus stem, guarded
  write, and no-fallback patterns;
- the existing equal-power DJ fade law and varispeed deck discipline;
- the existing fixed SPSC phrase-buffer callback and `sounddevice` host as the future
  live output boundary, not as the source-transport model.

Evaluate next:

- Signalsmith Stretch as the primary permissive native provider for independent
  time/pitch transforms;
- libsamplerate as a native arbitrary/time-varying sample-rate converter when the
  Python reference renderer moves into a native callback worker;
- miniaudio only if EarCrate needs a bundled native device/mixer host beyond the
  current PortAudio/sounddevice boundary.

Study, do not link into core by default:

- Mixxx for deck/channel/control architecture and adversarial DJ behavior;
- Rubber Band as a quality benchmark and separately licensed studio provider;
- Ableton Link for future LAN beat/tempo/phase synchronization;
- aubio and Essentia for research comparison, not canonical beat authority;
- Spotify Pedalboard for offline effect-host ergonomics, subject to GPL constraints.

## Commodity matrix

| Project | What it already solves | License/distribution observation | EarCrate disposition |
|---|---|---|---|
| python-sounddevice + PortAudio | Cross-platform device enumeration and callback/blocking audio I/O over NumPy buffers | python-sounddevice is MIT; PortAudio is a mature cross-platform callback host | Keep as optional live device host. Prepared PCM and callback invariants remain EarCrate authority. |
| miniaudio | Single-file C playback/capture, decoding, resampling, channel mapping, mixing, filters, node graph, broad platform backends | Public-domain or MIT-0 choice | Strong native-host candidate, but no adoption in this slice because replacing the host does not create independent playheads by itself. |
| Signalsmith Stretch | C++11 polyphonic time stretching and pitch shifting, streaming API, reported latency | MIT; upstream says modest 0.75x-1.5x stretches are the best-quality range | Preferred future optional key-lock/stretch provider behind a receipt-bearing transform interface. |
| libsamplerate | High-quality sample-rate conversion including arbitrary/time-varying conversion | BSD-2-Clause; current upstream release line is 0.2.x | Existing evaluated commodity. Candidate native varispeed/resample backend. |
| Mixxx | Complete DJ application: decks, live mixing, loops, key lock, controller ecosystem, stems, broadcast/record workflows | GPLv2; current stable release line remains active | Architecture and test-corpus study only. Do not copy or link code into the default core. |
| Rubber Band | Mature offline/real-time time stretch and pitch shift; separate high-quality and faster engines | GPLv2+ or commercial license | Quality oracle and optional external/studio provider only after explicit license review. Not a default linked dependency. |
| Ableton Link | Local-network synchronization of musical beat, tempo, and phase, including callback clock/latency guidance | GPLv2+ or proprietary license | Future external synchronization seam. It must not become EarCrate's internal transport authority. |
| aubio | Onset, pitch, tempo, beat, MFCC, phase-vocoder, filters, and live tools | GPLv3; latest tagged release is old relative to this sweep | Research comparison only. Existing beat observations remain receipt-bearing and replaceable. |
| Essentia | Large C++/Python MIR and DSP algorithm collection | AGPLv3 or separate commercial licensing | Research/studio provider only; unsuitable as default core dependency under current distribution. |
| Spotify Pedalboard | Python audio I/O, built-in effects, and VST3/AU hosting | GPLv3 | Useful ergonomic reference and studio-only candidate, not the source-transport core. |

Primary upstream sources consulted:

- https://github.com/spatialaudio/python-sounddevice
- https://github.com/PortAudio/portaudio
- https://github.com/mackron/miniaudio
- https://github.com/Signalsmith-Audio/signalsmith-stretch
- https://github.com/libsndfile/libsamplerate
- https://github.com/mixxxdj/mixxx
- https://github.com/breakfastquay/rubberband
- https://github.com/Ableton/link
- https://github.com/aubio/aubio
- https://github.com/MTG/essentia
- https://github.com/spotify/pedalboard

## The community architecture worth harvesting

### A deck is a state machine, not an audio clip

The mature DJ systems converge on persistent channel/deck state: loaded source,
playhead, rate, synchronization mode, loop/cue state, gain, filter/EQ, crossfader
assignment, and controls. EarCrate's MixScore adopts that concept while keeping the
state and event schema independent of any one engine.

### Two-deck UI, N-deck renderer

The cultural metaphor remains two turntables and a mixer. The internal renderer must
allow extra channels for one-shots, fills, textures, retained beds, and transition
material. Crossfader side A/B is routing metadata, not a channel-count limit.

### Callback work must stay tiny

PortAudio, sounddevice, Ableton Link guidance, and the existing EarCrate performance
host all point to the same rule: decode, search, planning, binding, and expensive DSP
belong off the audio callback. The callback consumes prepared bounded PCM and updates
small transport/meter state.

### Synchronization is not selection

Ableton Link or a beat tracker may supply observations about beat/phase. Neither
chooses material, validates cues, or owns the resulting performance. EarCrate must
retain source identity, cue selection, event order, transform feasibility, and
execution receipts.

### License is part of architecture

A technically excellent GPL engine is not automatically a shippable linked component
for a PolyForm-distributed application. The clean seam is provider isolation: exact
inputs and outputs, pinned binary/model identity, measured capability, no authority
over EarCrate records, and explicit distribution class.

## Build order after the proof

1. Keep the Python renderer as the executable semantic oracle and regression target.
2. Lower one committed live-planner phrase into a bounded MixScore window.
3. Prepare N deck buffers on the non-audio thread and feed the existing SPSC callback.
4. Benchmark Signalsmith Stretch against varispeed on vocals, drums, bass, and full
   mixes over 0.75x-1.5x; record latency, CPU, transient integrity, and stem sum.
5. Add three-band DJ EQ/filter automation only after transport semantics remain exact.
6. Add controller mappings and live cue capture as events, not hidden mutable state.
7. Evaluate Ableton Link only after local sample-clock and output-latency accounting
   are measured and stable.
8. Consider miniaudio if packaging or callback-host limits are demonstrated by data.

The rule is simple: buy the commodity, own the score.
