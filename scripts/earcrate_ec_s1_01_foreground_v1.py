"""EC-S1-01: measure the material's grid, compose the piano foreground, render it per section.

The generated material is fixed audio at whatever tempo it actually came out at, not at the
tempo it was asked for. So the grid is measured from the drums and everything else is written
to that -- the A1-07 lesson, applied before anything is placed rather than after it fails.

The foreground is composed here, not generated. It is the one part of this track that carries
a tune, and a tune is the thing every closed Album One lane turned out to be missing. D minor,
i - VI - III - VII, with a melody that is withheld in its full form until the payoff.

Five sections, and the piano plays materially different music in each:

    INTRO    melody alone, no comp, no low register
    BUILD    melody plus a left-hand comp
    HOLD     single sustained tones, the melody deliberately absent
    PAYOFF   melody an octave up, full voicings, a countermelody underneath
    OUTRO    melody once more, bare, resolving

The render is sliced at section boundaries so each section is its own editable clip. The
slices are bar-aligned, so nothing is lost by cutting them apart.

    python scripts/earcrate_ec_s1_01_foreground_v1.py --material <dir> --rack <dir> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.performance.demand import compile_demand  # noqa: E402
from earcrate.a1_02.performance.rack import bind, parse_sfz, verify_sources  # noqa: E402
from earcrate.a1_02.performance.rack_render import render as rack_render  # noqa: E402
from earcrate.evidence.identity import sha256_file  # noqa: E402

COMMISSION = "EC-S1-01"
RACK_SFZ = "SalamanderGrandPianoV3.sfz"
IDENTITY_CLOCK_BPM = 60.0          # one beat is one second, so a measured grid places notes
SAMPLE_RATE = 48_000
BEATS_PER_BAR = 4

# The tune. D minor, i - VI - III - VII, one chord per bar.
KEY_ROOT = 2                        # D
PROGRESSION = ((2, "min"), (10, "maj"), (5, "maj"), (0, "maj"))
TRIADS = {"min": (0, 3, 7), "maj": (0, 4, 7)}

# Registers, stated so they read as decisions rather than as findings.
MELODY_REGISTER = (69, 84)          # A4 to C6
COMP_REGISTER = (48, 64)            # C3 to E4
BASS_REGISTER = (33, 45)            # A1 to A2

# The melody, in scale degrees of D natural minor, one entry per beat. `None` is a rest.
# It is stated in full here and withheld in performance: sections take slices of it.
SCALE = (0, 2, 3, 5, 7, 8, 10)      # natural minor
MELODY = (
    (0, None, 4, None), (3, None, 2, None), (1, None, 2, 3), (2, None, None, None),
    (4, None, 5, None), (4, None, 3, None), (2, 1, 2, None), (0, None, None, None),
)

SECTIONS = (
    {"name": "INTRO", "bars": 8, "melody": True, "comp": False, "low": False,
     "octave": 0, "counter": False, "sustain": False},
    {"name": "BUILD", "bars": 16, "melody": True, "comp": True, "low": True,
     "octave": 0, "counter": False, "sustain": False},
    {"name": "HOLD", "bars": 8, "melody": False, "comp": False, "low": True,
     "octave": 0, "counter": False, "sustain": True},
    {"name": "PAYOFF", "bars": 16, "melody": True, "comp": True, "low": True,
     "octave": 12, "counter": True, "sustain": False},
    {"name": "OUTRO", "bars": 10, "melody": True, "comp": False, "low": True,
     "octave": 0, "counter": False, "sustain": False},
)


class ForegroundError(RuntimeError):
    pass


def measure_grid(path: Path) -> dict:
    """Take the grid from the material itself, not from what the material was asked for."""
    import librosa

    y, sr = librosa.load(str(path), sr=22_050, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=512,
                                           units="time", trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    intervals = np.diff(beats)
    if intervals.size < 8:
        raise ForegroundError("the material yields too few beats to establish a grid")
    beat_seconds = float(np.median(intervals))
    return {
        "measured_bpm": round(60.0 / beat_seconds, 3),
        "tracker_bpm": round(tempo, 3),
        "beat_seconds": round(beat_seconds, 9),
        "bar_seconds": round(beat_seconds * BEATS_PER_BAR, 9),
        "beat_interval_iqr_percent": round(
            float(np.subtract(*np.percentile(intervals, [75, 25])) / beat_seconds * 100), 3),
        "first_beat_seconds": round(float(beats[0]), 6),
        "taken_from": "the generated drum material, because that is what everything plays to",
    }


def _pitch(degree: int, register: tuple[int, int], octave: int = 0) -> int:
    pitch_class = (KEY_ROOT + SCALE[degree % len(SCALE)]) % 12
    base = register[0] + ((pitch_class - register[0]) % 12) + 12 * (degree // len(SCALE))
    base += octave
    while base > register[1]:
        base -= 12
    while base < register[0]:
        base += 12
    return base


def compose(grid: dict) -> tuple[list[dict], list[dict]]:
    """Write the whole performance to the measured grid, and report where each section sits."""
    beat = grid["beat_seconds"]
    notes: list[dict] = []
    layout: list[dict] = []
    bar_index = 0

    for section in SECTIONS:
        start_bar = bar_index
        for local in range(section["bars"]):
            root, quality = PROGRESSION[(bar_index) % len(PROGRESSION)]
            bar_start = bar_index * BEATS_PER_BAR * beat

            if section["sustain"]:
                pitch = _pitch(0, COMP_REGISTER)
                pitch = COMP_REGISTER[0] + ((root - COMP_REGISTER[0]) % 12)
                notes.append({"pitch": pitch, "velocity": 54, "start": bar_start,
                              "duration": BEATS_PER_BAR * beat * 0.98, "voice": "hold"})
            if section["low"]:
                low = BASS_REGISTER[0] + ((root - BASS_REGISTER[0]) % 12)
                notes.append({"pitch": low, "velocity": 62 if section["sustain"] else 74,
                              "start": bar_start,
                              "duration": BEATS_PER_BAR * beat * 0.95, "voice": "low"})
            if section["comp"]:
                tones = sorted({COMP_REGISTER[0] + ((root + step - COMP_REGISTER[0]) % 12)
                                for step in TRIADS[quality]})
                for offset in (1, 3) if section["counter"] else (2,):
                    for pitch in tones:
                        notes.append({"pitch": pitch, "velocity": 68, "voice": "comp",
                                      "start": bar_start + offset * beat,
                                      "duration": beat * 0.9})
            if section["counter"]:
                counter_degree = PROGRESSION.index((root, quality)) + 2
                pitch = _pitch(counter_degree, COMP_REGISTER, octave=12)
                notes.append({"pitch": pitch, "velocity": 58, "voice": "counter",
                              "start": bar_start + 2 * beat, "duration": beat * 1.8})
            if section["melody"]:
                phrase = MELODY[bar_index % len(MELODY)]
                for offset, degree in enumerate(phrase):
                    if degree is None:
                        continue
                    notes.append({
                        "pitch": _pitch(degree, MELODY_REGISTER, octave=section["octave"]),
                        "velocity": 88 if section["octave"] else 80, "voice": "melody",
                        "start": bar_start + offset * beat,
                        "duration": beat * (1.6 if offset in (0, 2) else 0.8),
                    })
            bar_index += 1

        layout.append({
            "section": section["name"], "bars": section["bars"],
            "start_bar": start_bar, "end_bar": bar_index,
            "start_seconds": round(start_bar * BEATS_PER_BAR * beat, 6),
            "end_seconds": round(bar_index * BEATS_PER_BAR * beat, 6),
            "plays": [key for key in ("melody", "comp", "low", "counter", "sustain")
                      if section[key]],
            "melody_octave": section["octave"],
        })
    notes.sort(key=lambda row: (row["start"], row["pitch"]))
    return notes, layout


def performed(notes: list[dict]) -> dict:
    return {
        "interpretation_id": f"{COMMISSION.lower()}-foreground-v1",
        "tempo_bpm": IDENTITY_CLOCK_BPM,
        "clock": "measured",
        "origin_seconds": 0.0,
        "notes": [{"printed_measure": 1 + int(row["start"] // 4), "performed_occurrence": 1,
                   "staff": 1 if row["voice"] in ("melody", "counter") else 2,
                   "voice": row["voice"], "pitch": row["pitch"],
                   "velocity": row["velocity"],
                   "start_beat": round(row["start"], 6),
                   "duration_beats": round(row["duration"], 6)} for row in notes],
    }


def write_midi(notes: list[dict], grid: dict, path: Path) -> dict:
    """The foreground as MIDI at the real tempo, so a DAW opens it in the right place."""
    import mido

    ticks = 960
    beat = grid["beat_seconds"]
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=int(round(beat * 1_000_000)), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Foreground piano", time=0))
    track.append(mido.Message("program_change", channel=0, program=0, time=0))
    events: list[tuple[int, int, int, int]] = []
    for row in notes:
        start = int(round(row["start"] / beat * ticks))
        end = start + max(1, int(round(row["duration"] / beat * ticks)))
        events.append((start, 1, row["pitch"], row["velocity"]))
        events.append((end, 0, row["pitch"], 0))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    clock = 0
    for tick, on, pitch, velocity in events:
        track.append(mido.Message("note_on" if on else "note_off", channel=0, note=pitch,
                                  velocity=velocity, time=tick - clock))
        clock = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(track)
    midi.save(path)
    return {"path": str(path), "ticks_per_beat": ticks, "notes": len(notes),
            "sha256": sha256_file(path)}


def slice_section(source: Path, destination: Path, *, start: float, duration: float) -> str:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}",
         "-t", f"{duration:.6f}", "-i", str(source), "-c:a", "pcm_s24le",
         "-ar", str(SAMPLE_RATE), "-map_metadata", "-1", "-fflags", "+bitexact",
         "-flags", "+bitexact", str(destination)], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise ForegroundError(result.stderr[-400:])
    return sha256_file(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material", required=True, type=Path)
    parser.add_argument("--rack", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    material = args.material.expanduser().resolve()
    rack = args.rack.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    grid = measure_grid(material / "drums_full" / "generated.wav")
    print(f"grid: {grid['measured_bpm']} bpm measured "
          f"(tracker {grid['tracker_bpm']}), bar {grid['bar_seconds']:.4f}s, "
          f"beat spread {grid['beat_interval_iqr_percent']}%")

    notes, layout = compose(grid)
    total_bars = layout[-1]["end_bar"]
    total_seconds = layout[-1]["end_seconds"]
    print(f"form: {total_bars} bars, {total_seconds:.1f}s "
          f"({int(total_seconds // 60)}:{int(total_seconds % 60):02d})")
    for row in layout:
        print(f"  {row['section']:<7} bars {row['start_bar']:>3}-{row['end_bar']:<3} "
              f"{row['start_seconds']:>7.2f}s  plays {'+'.join(row['plays'])}")

    midi = write_midi(notes, grid, out / "foreground.mid")
    print(f"midi: {midi['notes']} notes -> {Path(midi['path']).name}")

    zones, _ = parse_sfz(rack / RACK_SFZ)
    demand = compile_demand(performed(notes))
    binding = bind(demand, zones)
    if not binding["all_events_bound"]:
        raise ForegroundError(f"{binding['refused_event_count']} piano events refused")
    if not verify_sources(rack, binding)["sources_intact"]:
        raise ForegroundError("rack sources are missing or mutated")
    whole = out / "foreground-whole.wav"
    result = rack_render(binding, rack, tempo_bpm=IDENTITY_CLOCK_BPM, destination=whole,
                         stems=False)
    print(f"rendered: {demand['selected_event_count']} events, "
          f"{result['duration_seconds']}s")

    sections = []
    for row in layout:
        destination = out / f"foreground-{row['section'].lower()}.wav"
        digest = slice_section(whole, destination,
                               start=row["start_seconds"],
                               duration=row["end_seconds"] - row["start_seconds"])
        sections.append({**row, "path": str(destination), "sha256": digest})
        print(f"  sliced {destination.name}")

    (out / "foreground.json").write_text(json.dumps({
        "commission": COMMISSION, "grid": grid, "layout": sections, "midi": midi,
        "whole_render": {"path": str(whole), "sha256": sha256_file(whole),
                         "events": demand["selected_event_count"],
                         "duration_seconds": result["duration_seconds"]},
        "total_bars": total_bars, "total_seconds": round(total_seconds, 3),
    }, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"\nforeground.json written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
