# MixScore source-transport runtime

EarCrate now has a second execution medium beside MIDI/rack voices: a deterministic
source-transport score. The purpose of this runtime is narrow and testable. It must
prove that two or more independently moving recordings can remain live at the same
time while the performance jumps, loops, fades, cuts, nudges, and synchronizes them.

This is the missing turntable layer. A sample crate says what material exists. A
`MixScore` says what each loaded deck is doing at every master beat.

## Boundary

The existing live planner is retained. Its phrase horizon, persona policy,
technique selection, immutable state, compiled crate, performance host, and fixed
ring-buffer callback are useful machinery. The old execution path, however, lowers
committed phrases through MIDI and rack voices. It does not maintain independent
source playheads.

The new sibling path is:

```text
approved source assets + beat grids + cues
    -> MixScore
    -> N independent source transports
    -> deck automation + equal-power crossfader
    -> per-deck stems + stereo master
    -> event ledger + render receipt
```

`MixScore` deliberately separates three clocks:

- master beat: the resulting performance timeline;
- source beat: the analyzed beat grid inside one recording;
- source frame: the decoded PCM playhead actually consumed by a deck.

A cue jump changes source time without changing master time. A cut stops one deck
without stopping any other deck. A loop wraps one source playhead while the shared
master clock continues.

## Run the proof

The self-contained demo creates two synthetic sources with different source BPMs,
then renders a 12-second performance that exercises simultaneous playback, tempo
sync, an equal-power fade, crossfader motion, a cue jump, a two-beat loop, loop exit,
and a hard cut:

```text
python -m earcrate mix capability
python -m earcrate mix demo "mixscore-demo" --sample-rate 24000
```

The demo writes:

```text
mixscore-demo/
  demo-deck-a.wav
  demo-deck-b.wav
  demo.mixscore.json
  demo-mix.wav
  demo-mix.mixscore.sealed.json
  demo-mix.events.json
  demo-mix.receipt.json
  demo-stems/A.wav
  demo-stems/B.wav
```

The WAV sources are generated test signals, not repository media and not copyrighted
reference audio.

Render an edited score:

```text
python -m earcrate mix render \
  "mixscore-demo/demo.mixscore.json" \
  "mixscore-demo/edited.wav" \
  --stems-dir "mixscore-demo/edited-stems"
```

Create a starting score for two real files when their BPMs are known:

```text
python -m earcrate mix scaffold \
  "instrumental.wav" \
  "acapella.wav" \
  "two-deck.mixscore.json" \
  --bpm 100 \
  --source-a-bpm 100 \
  --source-b-bpm 96 \
  --bars 16
```

The scaffold is intentionally only a score draft. Set exact downbeats and cues before
judging musical quality.

## Schema

A minimal score is a JSON object:

```json
{
  "schema_version": 1,
  "kind": "earcrate_mix_score",
  "clock": {"bpm": 100, "beats_per_bar": 4, "sample_rate": 48000},
  "end_beat": 32,
  "assets": [
    {
      "asset_id": "instrumental",
      "path": "instrumental.wav",
      "source_bpm": 100,
      "downbeat_seconds": 0,
      "cues": {"start": 0, "hook": 16}
    }
  ],
  "decks": [
    {"deck_id": "A", "crossfader_side": "A", "gain_db": -2, "pan": 0}
  ],
  "events": [
    {"at_beat": 0, "deck_id": "A", "op": "load", "asset_id": "instrumental"},
    {"at_beat": 0, "deck_id": "A", "op": "play", "cue": "start", "sync": true},
    {"at_beat": 8, "deck_id": "A", "op": "jump", "cue": "hook"}
  ]
}
```

The runtime supports N decks. `A` and `B` are crossfader assignments, not a hard
limit on channel count. A deck assigned to `NONE` bypasses the crossfader while
remaining subject to its own gain, mute, and pan automation.

## Operations

Transport operations:

```text
load
play
stop
cut
jump / seek
loop
exit_loop
set_rate
nudge
```

Deck automation:

```text
set_gain
fade
mute
unmute
set_pan
```

Master mixer automation:

```text
set_crossfader
crossfade
```

Point operations use `at_beat`. Fades and crossfades use `from_beat` and `to_beat`.
Events at the same master frame execute in score order. Every normalized event gets
a deterministic `event_id` and must appear exactly once as `executed` in the output
ledger.

## Tempo sync

Version 1 uses explicit varispeed:

```text
source_speed = master_bpm / source_bpm * deck_rate
```

That is turntable behavior: tempo and pitch move together. It avoids pretending the
current implementation has independent key lock. A future optional stretch provider
can replace this one transform while preserving the same source transport, event,
and receipt contracts.

Resampling uses the existing anti-aliasing direction of the EarCrate deck path:
polyphase FIR for normal spans and exact output-length reconciliation. Very short
spans use bounded interpolation because polyphase filter setup is longer than the
material itself.

## Exactness and refusal

A render is complete only when all selected events execute. The renderer refuses:

- missing files, missing assets, missing cues, or unknown decks;
- invalid or duplicate event IDs;
- events outside `end_beat`;
- jumps, nudges, or loops outside decoded source bounds;
- stopping an already stopped deck or exiting a nonexistent loop;
- source exhaustion while a deck remains selected for playback;
- a source whose byte hash differs from `expected_file_sha256`;
- a source that changes while ffmpeg is decoding it;
- an unaccounted event or a master/stem reconciliation mismatch.

The first render binds unpinned assets to their observed file SHA-256 in the emitted
sealed score. Re-rendering that sealed score therefore detects a swapped or modified
source.

Each output deck stem receives the same master attenuation. The numerical sum of all
stems is recomputed and must reproduce the master before any files are committed.
WAV and JSON artifacts are written through same-directory temporary files and atomic
replacement; the final receipt is written last.

## Current limit and next seam

This slice is an offline deterministic proof renderer. It is not yet a callback-time
interactive deck engine, and it does not claim key-locked stretching, scratch models,
EQ isolators, filter sweeps, or controller mappings.

The next integration step is not another sampler. It is to lower committed live
planner techniques into bounded MixScore windows, prepare each deck's next PCM block
on the non-audio thread, and feed the existing fixed ring-buffer callback. The
callback contract remains unchanged: no planning, search, decoding, source binding,
or allocation in the audio callback.
