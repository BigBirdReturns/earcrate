"""Realize A1-03 as a trio: piano, bass and drums, as parts, across the whole form.

Everything before this produced a reduction. The corrected chart was played by one sampled
piano over thirty-two bars, and the one attempt at a rhythm section was a generated bed that
had never heard the recording and did not know the chord changes. Neither object can answer
whether A1-03 is a convincing reconstruction, because neither is a trio and neither is a
whole track.

So this builds the three parts the trio actually has, from material the lane already owns:

    piano    the corrected chart comped by the same Salamander rack, minus the left-hand
             root -- that root only existed because there was no bass
    bass     a walking line derived from the chart: root, chord tone, chord tone, approach
             to the next bar's root, one note per recovered beat
    drums    a swung ride pattern on the recovered beats, with the snare placed by the
             performance's own accent profile rather than by a fixed pattern

Piano is sounded by the sampled grand. Bass and drums are sounded by the crate: approved
EarAtoms are proposed against the exact performance demand, materialized, sealed into racks
and rendered by `earcrate.rack`. No new organ is built here. The crate's drum atoms are
bar-length breaks rather than hits, so a trigger region is trimmed to a stated hit length --
a selection decision inside machinery that already takes a region, not a new mechanism -- and
any atom whose attack is inaudible is dropped before the proposal sees it.

The form is the whole bound performance, not a window. The control is the incumbent
reduction -- root plus voicings, one piano -- extended over that same whole form, so the only
difference between candidate and control is the two parts that were missing.

    python scripts/earcrate_a1_03_trio_v1.py \
        --source "<the bound recording>" --rack <rack-dir> --crate <sqlite> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earcrate_a1_03_realization_v1 import (  # noqa: E402
    BEATS_PER_BAR,
    CHORD_TEMPLATES,
    COMP_BEATS,
    IDENTITY_CLOCK_BPM,
    PITCH_NAMES,
    RACK_SFZ,
    VELOCITY_RANGE,
    recover_chart,
    verify_binding,
    voice,
    witness_cross_check,
)
from earcrate.a1_02.performance.demand import compile_demand  # noqa: E402
from earcrate.a1_02.performance.rack import bind, parse_sfz, verify_sources  # noqa: E402
from earcrate.a1_02.performance.rack_render import render as rack_piano_render  # noqa: E402
from earcrate.analyze.decode import decode_audio  # noqa: E402
from earcrate.evidence.identity import seal, sha256_file  # noqa: E402
from earcrate.midi.codec import midi_read  # noqa: E402
from earcrate.rack.library import rack_build_from_atoms  # noqa: E402
from earcrate.rack.render_fix import rack_render_ledger  # noqa: E402

TRACK_ID = "A1-03"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-trio-realization-v1.public.json"

SAMPLE_RATE = 48_000
TICKS_PER_BEAT = 960                 # identity clock: one beat is one second

# The bass. Register and figure are an interpretation, stated here so they are not read as a
# recovery: the chart says which chord, not how a bassist walks through it.
BASS_REGISTER = (28, 43)             # E1 to G2
BASS_VELOCITY_RANGE = (66, 104)
BASS_DEGREES = ("root", "third", "fifth", "chromatic approach to the next bar's root")

# The drums. GM note numbers, because the crate's trigger fit is written against them.
KICK, SNARE, HAT, RIDE = 36, 38, 44, 51
SWING_RATIO = 2.0 / 3.0              # where the swung eighth sits inside its beat
# A trigger region is a slice of somebody else's groove, so it has to be shorter than the
# gap to the next hit or it brings that groove's own transients with it. The written
# moments here are as close as 0.13 s apart, which sets the ceiling.
HIT_SECONDS = {KICK: 0.18, SNARE: 0.18, HAT: 0.10, RIDE: 0.12}
DRUM_VELOCITY = {KICK: 58, SNARE: 92, HAT: 74, RIDE: 84}

# The crate pool. Deterministic, role-ordered, and screened for an audible attack.
POOL_PER_DRUM_VOICE = 8
POOL_BASS = 12
MINIMUM_HIT_PEAK = 0.05              # an atom whose attack cannot be heard is not a drum
ATTACK_FRACTION = 0.35               # what counts as the transient inside a region
SCREEN_SECONDS = 0.35

# Where the parts sit against each other. An arrangement value, not a measurement.
BASS_GAIN_DB = -4.0
DRUMS_GAIN_DB = -8.0
TASTE_PROFILE = "girl_talk_v1"


class TrioError(RuntimeError):
    pass


# ------------------------------------------------------------------------------- parts


def _velocity(strength: float, low: float, span: float, lo: int, hi: int) -> int:
    scaled = (strength - low) / (span or 1.0)
    return int(round(lo + max(0.0, min(1.0, scaled)) * (hi - lo)))


def _nearest_in_register(pitch_class: int, previous: int | None,
                         register: tuple[int, int]) -> int:
    candidates = [note for note in range(register[0], register[1] + 1)
                  if note % 12 == pitch_class % 12]
    if not candidates:
        raise TrioError(f"pitch class {pitch_class} has no note inside {register}")
    if previous is None:
        return candidates[len(candidates) // 2]
    return min(candidates, key=lambda note: (abs(note - previous), note))


def walking_bass(chart: dict) -> list[dict]:
    """One note per recovered beat: root, chord tone, chord tone, approach to the next root.

    The chart says which chord each bar is. It does not say how a bassist walks through it,
    so the figure is chosen and declared. What is not chosen is the timing: every note sits
    on a beat the recording actually played.
    """
    bars = chart["bars"]
    strengths = np.array([value for bar in bars for value in bar["beat_strengths"]])
    low, span = float(strengths.min()), float(strengths.max() - strengths.min())

    notes: list[dict] = []
    previous: int | None = None
    for index, bar in enumerate(bars):
        root = int(bar["chord_root"])
        intervals = CHORD_TEMPLATES[bar["chord_quality"]]
        third = intervals[1] if len(intervals) > 1 else 0
        fifth = intervals[2] if len(intervals) > 2 else 7
        following = int(bars[index + 1]["chord_root"]) if index + 1 < len(bars) else root
        degrees = [root, (root + third) % 12, (root + fifth) % 12, (following - 1) % 12]
        times = list(bar["beat_times"]) + [bar["bar_end_seconds"]]
        for beat in range(BEATS_PER_BAR):
            pitch = _nearest_in_register(degrees[beat], previous, BASS_REGISTER)
            previous = pitch
            notes.append({
                "part": "bass", "bar": bar["bar"], "beat": beat, "pitch": pitch,
                "velocity": _velocity(bar["beat_strengths"][beat], low, span,
                                      *BASS_VELOCITY_RANGE),
                "start_seconds": times[beat],
                "duration_seconds": max(0.08, times[beat + 1] - times[beat]),
            })
    return notes


def swung_drums(chart: dict) -> list[dict]:
    """A ride pattern on the recovered beats, with the snare placed by the performance.

    The ride and the hi-hat are a jazz figure and are chosen. The snare is not: it lands on
    whichever beat of the bar the recording itself accented hardest, and only when that accent
    stands above the bar's own mean, so the drums follow the performance rather than a grid.
    """
    notes: list[dict] = []
    for bar in chart["bars"]:
        times = list(bar["beat_times"]) + [bar["bar_end_seconds"]]
        beat_strengths = bar["beat_strengths"]
        mean = float(np.mean(beat_strengths))
        loudest = int(np.argmax(beat_strengths[1:])) + 1

        def add(pitch: int, start: float, velocity: int, why: str) -> None:
            notes.append({
                "part": "drums", "bar": bar["bar"], "pitch": pitch, "velocity": velocity,
                "start_seconds": start, "duration_seconds": HIT_SECONDS[pitch], "why": why,
            })

        for beat in range(BEATS_PER_BAR):
            add(RIDE, times[beat], DRUM_VELOCITY[RIDE], "ride, every beat")
            if beat in (1, 3):
                add(HAT, times[beat], DRUM_VELOCITY[HAT], "hi-hat foot, two and four")
                swung = times[beat] + SWING_RATIO * (times[beat + 1] - times[beat])
                add(RIDE, swung, DRUM_VELOCITY[RIDE] - 12, "swung eighth after two and four")
        add(KICK, times[0], DRUM_VELOCITY[KICK], "feathered downbeat")
        if beat_strengths[loudest] > mean:
            add(SNARE, times[loudest], DRUM_VELOCITY[SNARE],
                f"the bar's own strongest accent, beat {loudest + 1}")
    notes.sort(key=lambda row: (row["start_seconds"], row["pitch"]))
    return notes


def piano_comp(chart: dict, *, with_root: bool) -> dict:
    """The incumbent comp, optionally without its left-hand root.

    The root was in the reduction because nothing else was playing one. In a trio the bass
    plays it, so the candidate's piano drops it and the control's piano keeps it. That is the
    only difference in the piano between the two objects.
    """
    bars = chart["bars"]
    origin = bars[0]["beat_times"][0]
    strengths = np.array([value for bar in bars for value in bar["beat_strengths"]])
    low, span = float(strengths.min()), float(strengths.max() - strengths.min())

    notes = []
    for bar in bars:
        times = bar["beat_times"]
        bar_end = bar["bar_end_seconds"]
        bass, tones = voice(bar["chord_root"], bar["chord_quality"])
        if with_root:
            notes.append({
                "printed_measure": bar["bar"], "performed_occurrence": 1,
                "staff": 2, "voice": "left", "pitch": bass,
                "velocity": _velocity(bar["beat_strengths"][0], low, span, *VELOCITY_RANGE),
                "start_beat": round(times[0] - origin, 6),
                "duration_beats": round(bar_end - times[0], 6),
            })
        for offset in COMP_BEATS:
            start = times[offset]
            stop = times[offset + 1] if offset + 1 < len(times) else bar_end
            for pitch in tones:
                notes.append({
                    "printed_measure": bar["bar"], "performed_occurrence": 1,
                    "staff": 1, "voice": "right", "pitch": pitch,
                    "velocity": _velocity(bar["beat_strengths"][offset], low, span,
                                          *VELOCITY_RANGE),
                    "start_beat": round(start - origin, 6),
                    "duration_beats": round(max(stop - start, 0.05), 6),
                })
    notes.sort(key=lambda row: (row["start_beat"], row["staff"], row["pitch"]))
    return {
        "interpretation_id": f"a1-03-trio-v1-piano-{'with' if with_root else 'without'}-root",
        "tempo_bpm": IDENTITY_CLOCK_BPM,
        "clock": "recovered",
        "origin_seconds": round(origin, 6),
        "notes": notes,
    }


# ------------------------------------------------------------------------------- the crate


def _atom_row(row: sqlite3.Row, *, trim_seconds: float | None) -> dict:
    start = float(row["start_s"])
    end = float(row["end_s"])
    if trim_seconds is not None:
        end = min(end, start + trim_seconds)
    return {
        "atom_id": row["id"], "loop_id": row["loop_id"], "file_id": row["file_id"],
        "atom_status": row["status"], "path": row["path"],
        "start_s": start, "end_s": end,
        "ear_role": row["ear_role"], "render_role": row["render_role"],
        "key_root": row["key_root"], "bpm": row["bpm"], "score": row["score"],
        "hook_score": row["hook_score"], "bed_score": row["bed_score"],
        "floor_score": row["floor_score"], "bass_score": row["bass_score"],
        "spark_score": row["spark_score"], "intelligibility": row["intelligibility"],
        "low_share": row["low_share"], "mid_share": row["mid_share"],
        "high_share": row["high_share"], "loopability": row["loopability"],
        "transient_density": row["transient_density"],
        "source_audio_sha256": row["source_audio_sha256"] or "",
        "taste_profile": row["taste_profile"],
    }


def _screen(atom: dict) -> tuple[float, float]:
    """Decode the head of a candidate atom: how loud it is, and where its attack actually is.

    Both answers are needed for the same reason. An atom whose head is silent is not an
    instrument, and an atom whose transient sits twenty milliseconds inside its region is a
    drum that drags -- every hit would land late by however far the attack is from the region
    boundary. The region is already a selection this machinery takes; this measures where to
    put it rather than adding a mechanism to find it.
    """
    audio = decode_audio(Path(atom["path"]), sr=SAMPLE_RATE, start=atom["start_s"],
                         duration=min(SCREEN_SECONDS, atom["end_s"] - atom["start_s"]))
    if not audio.size:
        return 0.0, 0.0
    envelope = np.abs(audio)
    peak = float(envelope.max())
    if peak <= 0.0:
        return 0.0, 0.0
    attack = int(np.argmax(envelope > ATTACK_FRACTION * peak))
    return peak, attack / float(SAMPLE_RATE)


def crate_pool(crate: Path, key_root: int) -> tuple[list[dict], dict]:
    """Approved EarAtoms for one bass voice and three drum voices, screened and deterministic."""
    connection = sqlite3.connect(f"file:{crate.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = ("select a.*, f.path as path, f.audio_sha256 as source_audio_sha256 "
             "from ear_atoms a join files f on f.id = a.file_id "
             "where a.status = 'approved' and a.render_role = ? and f.present = 1 "
             "and {filter} order by {order} desc, a.id asc limit ?")

    wanted = (
        ("bass", f"a.key_root = {int(key_root)}", "a.bass_score * a.low_share", POOL_BASS, None),
        ("drum_anchor", "1 = 1", "a.low_share * a.transient_density", POOL_PER_DRUM_VOICE,
         HIT_SECONDS[KICK]),
        ("drum_anchor", "1 = 1", "a.mid_share * a.transient_density", POOL_PER_DRUM_VOICE,
         HIT_SECONDS[SNARE]),
        ("drum_anchor", "1 = 1", "a.high_share * a.transient_density", POOL_PER_DRUM_VOICE,
         HIT_SECONDS[RIDE]),
    )
    seen: dict[str, dict] = {}
    considered = screened_out = aligned = 0
    for role, condition, order, limit, trim in wanted:
        sql = query.format(filter=condition, order=order)
        for row in connection.execute(sql, (role, limit)):
            atom = _atom_row(row, trim_seconds=trim)
            if atom["atom_id"] in seen:
                continue
            considered += 1
            peak, attack = _screen(atom)
            if peak < MINIMUM_HIT_PEAK:
                screened_out += 1
                continue
            if trim is not None and attack > 0.0:
                # Start the region on the transient, so a triggered hit lands on its beat.
                atom["start_s"] = atom["start_s"] + attack
                atom["end_s"] = atom["start_s"] + trim
                aligned += 1
            atom["screened_peak"] = round(peak, 6)
            atom["attack_offset_seconds"] = round(attack, 6)
            seen[atom["atom_id"]] = atom
    connection.close()

    pool = [seen[key] for key in sorted(seen)]
    if not pool:
        raise TrioError("the crate offered no audible approved atom for any voice")
    report = {
        "considered": considered,
        "screened_out_for_inaudible_attack": screened_out,
        "trigger_regions_moved_onto_their_transient": aligned,
        "attack_fraction_of_peak": ATTACK_FRACTION,
        "minimum_hit_peak": MINIMUM_HIT_PEAK,
        "accepted": len(pool),
        "bass_atoms": sum(1 for atom in pool if atom["render_role"] == "bass"),
        "drum_atoms": sum(1 for atom in pool if atom["render_role"] == "drum_anchor"),
        "bass_key_root_required": int(key_root),
        "trigger_regions_trimmed_to_seconds": {str(note): HIT_SECONDS[note]
                                               for note in (KICK, SNARE, RIDE)},
        "why_trimmed": ("the crate's drum atoms are bar-length breaks, not hits; a trigger "
                        "zone plays its region once per note, so the region is trimmed to a "
                        "stated hit length rather than built by a new hit-extraction organ"),
        "taste_profile": TASTE_PROFILE,
    }
    return pool, report


# ------------------------------------------------------------------------------- rendering


def write_rhythm_midi(bass: list[dict], drums: list[dict], origin: float, path: Path) -> dict:
    """Bass and drums as one MIDI performance on the identity clock, so a beat is a second."""
    import mido

    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    for name, channel, program, rows in (("Bass", 0, 33, bass), ("Drums", 9, 0, drums)):
        events: list[tuple[int, int, int, int]] = []
        for row in rows:
            start = max(0, int(round((row["start_seconds"] - origin) * TICKS_PER_BEAT)))
            end = start + max(1, int(round(row["duration_seconds"] * TICKS_PER_BEAT)))
            events.append((start, 1, int(row["pitch"]), int(row["velocity"])))
            events.append((end, 0, int(row["pitch"]), 0))
        events.sort(key=lambda event: (event[0], event[1], event[2]))

        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        if channel != 9:
            track.append(mido.Message("program_change", channel=channel, program=program,
                                      time=0))
        clock = 0
        for tick, on, pitch, velocity in events:
            delta = tick - clock
            clock = tick
            track.append(mido.Message("note_on" if on else "note_off", channel=channel,
                                      note=pitch, velocity=velocity, time=delta))
        track.append(mido.MetaMessage("end_of_track", time=0))
        midi.tracks.append(track)
    midi.save(path)
    return {"ticks_per_beat": TICKS_PER_BEAT,
            "clock": ("identity: 60 bpm, one beat is one second, so a drifting recovered grid "
                      "survives a renderer that only understands constant tempo")}


def render_piano(performed: dict, rack: Path, destination: Path) -> dict:
    zones, _ = parse_sfz(rack / RACK_SFZ)
    demand = compile_demand(performed)
    binding = bind(demand, zones)
    if not binding["all_events_bound"]:
        raise TrioError(f"{binding['refused_event_count']} piano events refused by the rack")
    if not verify_sources(rack, binding)["sources_intact"]:
        raise TrioError("rack sources are missing or mutated")
    result = rack_piano_render(binding, rack, tempo_bpm=IDENTITY_CLOCK_BPM,
                               destination=destination, stems=False)
    return {
        "events": demand["selected_event_count"],
        "polyphony": demand["maximum_polyphony"],
        "pitch_range": demand["pitch_range"],
        "distinct_samples_used": len(binding["distinct_samples_used"]),
        "duration_seconds": result["duration_seconds"],
        "applied_gain_db": result["applied_gain_db"],
        "master_sha256": result["files"]["master"],
    }


def render_rhythm(midi_path: Path, pool: list[dict], out: Path) -> dict:
    ledger = midi_read(midi_path)
    build = rack_build_from_atoms(ledger, pool, out / "racks", taste_profile=TASTE_PROFILE,
                                  apply=True, sample_rate=SAMPLE_RATE, overwrite=True)
    if not build.get("complete"):
        raise TrioError("the crate could not cover the rhythm section: "
                        f"{json.dumps(build.get('unresolved'))[:400]}")
    result = rack_render_ledger(ledger, build["binding"], build["rack_revisions"],
                                out / "a1-03-rhythm.wav", stems_dir=out / "rhythm-stems",
                                sample_rate=SAMPLE_RATE, overwrite=True)
    if not result["complete_execution"]:
        raise TrioError(f"{result['refused_event_count']} rhythm events refused by the crate")
    proposal = json.loads(Path(build["proposal_path"]).read_text(encoding="utf-8"))
    selected = [
        {
            "slot": slot["slot_id"], "mode": slot["mode"], "role_hint": slot["role_hint"],
            "atoms": sorted({choice["atom_id"] for choice in slot["selected"]}),
            "notes": sorted({int(choice["note"]) for choice in slot["selected"]
                             if choice.get("note") is not None}),
            # A trigger zone is rooted on its own note, so the proposer's slot-wide
            # transpose budget is not transposition. Only pitched slots retune.
            "maximum_transpose_semitones": (
                max(int(choice["maximum_transpose_semitones"]) for choice in slot["selected"])
                if slot["mode"] == "pitched" else 0),
        }
        for slot in proposal["slots"]
    ]
    stems = {Path(row["path"]).stem: Path(row["path"]) for row in result["stems"]}
    return {
        "build": {
            "proposal_sha256": build["proposal_sha256"],
            "demand_sha256": build["demand_sha256"],
            "binding_sha256": build["binding_sha256"],
            "build_sha256": build["build_sha256"],
            "atom_pool_sha256": proposal["atom_pool_sha256"],
            "atom_pool_count": proposal["atom_pool_count"],
            "racks": sorted(row["rack_sha256"] for row in build["racks"]),
            "materialized_atoms": sorted(row["atom_id"] for row in build["materializations"]),
            "selected": selected,
        },
        "render": {
            "master_sha256": sha256_file(Path(result["output_path"])),
            "events": result["selected_event_count"],
            "executed": result["executed_event_count"],
            "truncated": result["truncated_event_count"],
            "stems": {name: sha256_file(path) for name, path in sorted(stems.items())},
        },
        "paths": {"master": Path(result["output_path"]), "stems": stems},
    }


# ------------------------------------------------------------------------------- mixing


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
                            capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise TrioError(result.stderr[-500:])


def loudness(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-filter_complex", "ebur128=peak=true",
         "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
    values = [line for line in result.stderr.splitlines() if "I:" in line and "LUFS" in line]
    if not values:
        raise TrioError("ebur128 reported no integrated loudness")
    return float(values[-1].split("I:")[1].split("LUFS")[0].strip())


def mix(parts: list[tuple[Path, float]], destination: Path) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    for index, (path, gain_db) in enumerate(parts):
        inputs += ["-i", str(path)]
        filters.append(f"[{index}:a]volume={gain_db:.2f}dB[p{index}]")
    chain = "".join(f"[p{index}]" for index in range(len(parts)))
    filters.append(f"{chain}amix=inputs={len(parts)}:duration=longest:normalize=0[out]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
             "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags", "+bitexact", str(destination)])


def gain_to(source: Path, destination: Path, gain_db: float) -> None:
    _ffmpeg(["-i", str(source), "-filter:a", f"volume={gain_db:.2f}dB",
             "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags", "+bitexact", str(destination)])


def part_fidelity(bass_stem: Path, drum_stem: Path, bass: list[dict],
                  drums: list[dict], origin: float) -> dict:
    """Ask the renders whether they are actually playing the parts they were given.

    Not silent is a low bar. A pitched crate zone can be retuned into mush and a trigger zone
    can smear into a wall, and either would still pass a level check. So the bass stem is
    asked which pitch class it is sounding at each written note, and the drum stem is asked
    whether an attack lands where each written hit was placed.
    """
    import librosa

    audio, rate = librosa.load(str(bass_stem), sr=22_050, mono=True)
    chroma = librosa.feature.chroma_cqt(y=audio, sr=rate, hop_length=512)
    frames_per_second = rate / 512
    agreed = measured = 0
    for note in bass:
        start = note["start_seconds"] - origin
        stop = start + min(float(note["duration_seconds"]), 0.4)
        lo, hi = int(start * frames_per_second), int(stop * frames_per_second)
        window = chroma[:, lo:hi]
        if window.size == 0:
            continue
        measured += 1
        agreed += int(np.argmax(window.mean(axis=1)) == int(note["pitch"]) % 12)

    percussion, rate = librosa.load(str(drum_stem), sr=22_050, mono=True)
    # 128 samples is 5.8 ms at this rate; 512 cannot resolve hits a seventh of a second
    # apart and would report a smear the part does not have.
    onsets = librosa.onset.onset_detect(y=percussion, sr=rate, hop_length=128,
                                        units="time", backtrack=False)
    # Several written hits share a moment -- the ride, the hi-hat and the kick all land on
    # the same downbeat -- and a detector can only report one attack there. So the part is
    # measured against the moments it asks for, not against the note count.
    written = np.array(sorted({round(row["start_seconds"] - origin, 4) for row in drums}))
    struck = 0
    offsets: list[float] = []
    if onsets.size and written.size:
        for placed in written:
            delta = onsets - placed
            nearest = float(delta[int(np.argmin(np.abs(delta)))])
            if abs(nearest) <= 0.05:
                struck += 1
                offsets.append(nearest)
    explained = 0
    if onsets.size and written.size:
        explained = sum(1 for onset in onsets
                        if float(np.min(np.abs(written - onset))) <= 0.05)
    return {
        "bass": {
            "notes_measured": measured,
            "notes_sounding_the_written_pitch_class": agreed,
            "fraction": round(agreed / measured, 4) if measured else 0.0,
            "method": "chroma argmax over each written note's own window",
        },
        "drums": {
            "hits_written": len(drums),
            "distinct_moments_written": int(written.size),
            "onsets_detected_in_the_stem": int(onsets.size),
            "moments_with_an_attack_within_50ms": struck,
            "fraction": round(struck / float(written.size), 4) if written.size else 0.0,
            "onsets_explained_by_a_written_moment": explained,
            "median_offset_seconds": round(float(np.median(offsets)), 4) if offsets else None,
            "method": ("librosa onset detection against the distinct written hit moments; the "
                       "median offset is reported so a part that drags cannot pass as a part "
                       "that lands"),
        },
    }


def pcm_sha256(path: Path) -> str:
    """Identity of what the render sounds like, not of the file it arrived in.

    The crate path materializes its samples through a WAV container that carries bytes the
    audio does not. Two runs that decode the same region write identical PCM inside
    non-identical files, and every digest downstream of that inherits the difference. So the
    receipt carries a PCM digest as well: that is the one a second run can be held to.
    """
    import hashlib

    import soundfile as sf

    audio, _ = sf.read(str(path), always_2d=True, dtype="float32")
    return hashlib.sha256(np.ascontiguousarray(audio).tobytes()).hexdigest()


def audible(path: Path) -> dict:
    import soundfile as sf

    audio, rate = sf.read(str(path), always_2d=True)
    return {"peak": round(float(np.abs(audio).max()), 6),
            "rms": round(float(np.sqrt((audio ** 2).mean())), 6),
            "seconds": round(len(audio) / float(rate), 3)}


# ------------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--rack", required=True, type=Path)
    parser.add_argument("--crate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    rack = args.rack.expanduser().resolve()
    crate = args.crate.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    binding = verify_binding(source)
    print(f"bound performance {binding['container_sha256'][:16]}")

    print("recovering the whole form ...")
    chart = recover_chart(source, section_bars=None)
    bars = chart["bars"]
    origin = bars[0]["beat_times"][0]
    form_seconds = chart["section_end_seconds"] - chart["section_start_seconds"]
    print(f"  {len(bars)} bars, {chart['section_start_seconds']:.3f}s to "
          f"{chart['section_end_seconds']:.3f}s ({form_seconds:.1f}s), median implies "
          f"{chart['implied_bpm_from_bar_span']} bpm")
    agreement = chart["reader_agreement"]
    print(f"  two readers agree on {agreement['bars_where_two_readers_agree']}/"
          f"{agreement['bars']} bars ({agreement['fraction']:.3f}, floor {agreement['floor']})")
    if agreement["fraction"] < agreement["floor"]:
        raise TrioError(
            f"chord recovery agrees on only {agreement['fraction']:.3f} of bars over the whole "
            f"form, below the {agreement['floor']} floor; a trio built on that chart would be "
            "playing a chart nobody can vouch for")
    cross_check = witness_cross_check(chart)
    print(f"  {cross_check['chords_in_claimed_key']}/{cross_check['chords_total']} chords sit "
          f"inside the claimed key ({cross_check['observed_fraction']:.3f} against "
          f"{cross_check['chance_fraction']:.3f} by chance)")

    bass = walking_bass(chart)
    drums = swung_drums(chart)
    print(f"parts: {len(bass)} bass notes, {len(drums)} drum hits")

    midi_path = out / "a1-03-rhythm.mid"
    midi_note = write_rhythm_midi(bass, drums, origin, midi_path)

    key_root = int(np.bincount([bar["chord_root"] for bar in bars], minlength=12).argmax())
    print(f"crate pool (bass rooted on {PITCH_NAMES[key_root]}) ...")
    pool, pool_report = crate_pool(crate, key_root)
    print(f"  {pool_report['accepted']} atoms accepted, "
          f"{pool_report['screened_out_for_inaudible_attack']} screened out as inaudible")

    print("rendering the rhythm section through the crate ...")
    rhythm = render_rhythm(midi_path, pool, out)
    print(f"  {rhythm['render']['executed']} events, "
          f"{len(rhythm['build']['racks'])} racks sealed")

    print("rendering the piano ...")
    candidate_piano = out / "a1-03-piano-trio.wav"
    control_piano = out / "a1-03-piano-only.wav"
    piano = render_piano(piano_comp(chart, with_root=False), rack, candidate_piano)
    control = render_piano(piano_comp(chart, with_root=True), rack, control_piano)
    print(f"  candidate {piano['events']} events, control {control['events']} events")

    stems = rhythm["paths"]["stems"]
    bass_stem = next(path for name, path in sorted(stems.items()) if "bass" in name.lower())
    drum_stem = next(path for name, path in sorted(stems.items()) if "drum" in name.lower())

    trio_raw = out / "a1-03-trio-raw.wav"
    mix([(candidate_piano, 0.0), (bass_stem, BASS_GAIN_DB), (drum_stem, DRUMS_GAIN_DB)],
        trio_raw)

    measured = {"trio": loudness(trio_raw), "control": loudness(control_piano)}
    target = min(measured.values())
    trio = out / "a1-03-trio-candidate.wav"
    control_matched = out / "a1-03-piano-only-control.wav"
    gain_to(trio_raw, trio, target - measured["trio"])
    gain_to(control_piano, control_matched, target - measured["control"])
    print(f"  LUFS {measured} -> matched to {target}")

    levels = {
        "trio": audible(trio),
        "control": audible(control_matched),
        "bass_stem": audible(bass_stem),
        "drum_stem": audible(drum_stem),
        "piano": audible(candidate_piano),
    }
    fidelity = part_fidelity(bass_stem, drum_stem, bass, drums, origin)
    print(f"  bass sounds the written pitch class on "
          f"{fidelity['bass']['fraction']:.3f} of its notes; "
          f"{fidelity['drums']['fraction']:.3f} of drum hits land an attack")

    silent = sorted(name for name, row in levels.items() if row["rms"] <= 1e-5)
    # A complete trio candidate has three audible parts that play what they were written to
    # play, across the whole form. Whether it is *good* is not a machine question.
    complete = (
        not silent
        and levels["trio"]["seconds"] >= form_seconds - 1.0
        and fidelity["bass"]["fraction"] >= 0.5
        and fidelity["drums"]["fraction"] >= 0.5
    )

    candidate_sha = sha256_file(trio)
    control_sha = sha256_file(control_matched)

    receipt = seal({
        "kind": "earcrate_a1_03_public_trio_realization_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("A1-03 is realized as a trio -- piano, bass and drums as parts, from the "
                     "corrected chart, across the whole bound form -- against the same "
                     "piano-only reduction extended over that same form."),
        "artifact_class": "complete_track_candidate" if complete else "incomplete_attempt",
        "source_binding": {
            "container_sha256": binding["container_sha256"],
            "canonical_pcm_sha256": binding["canonical_pcm_sha256"],
            "path_recorded_in_repository": False,
        },
        "form": {
            "bars": len(bars),
            "start_seconds": chart["section_start_seconds"],
            "end_seconds": chart["section_end_seconds"],
            "duration_seconds": round(form_seconds, 6),
            "bound_recording_seconds": binding["canonical_seconds"],
            "fraction_of_the_recording": round(
                form_seconds / float(binding["canonical_seconds"]), 4),
            "window_not_used": "the form is every bar the recovered beat grid supports",
        },
        "recovered_chart": {
            "bars": len(bars),
            "chords": [bar["chord"] for bar in bars],
            "reader_agreement": chart["reader_agreement"],
            "chord_fit_median": chart["chord_fit_median"],
            "implied_bpm_from_bar_span": chart["implied_bpm_from_bar_span"],
            "bar_duration_seconds": chart["bar_duration_seconds"],
            "recovered_from": chart["recovered_from"],
        },
        "witness_cross_check": cross_check,
        "parts": {
            "piano": {
                "figure": "voicings on two and four, in a stated register",
                "left_hand_root_dropped": True,
                "why": "the root was only there because nothing else played one",
                "events": piano["events"], "polyphony": piano["polyphony"],
                "pitch_range": piano["pitch_range"],
                "instrument": "one sampled grand piano rack, unchanged from A1-02",
            },
            "bass": {
                "figure": "walking quarters: " + ", ".join(BASS_DEGREES),
                "register": list(BASS_REGISTER),
                "notes": len(bass),
                "every_note_on_a_recovered_beat": True,
                "instrument": "crate rack, pitched, built from approved EarAtoms",
                "is_an_interpretation": True,
            },
            "drums": {
                "figure": ("swung ride on every beat, hi-hat on two and four, swung eighth "
                           "after two and four, feathered kick on the downbeat"),
                "snare_placed_by": ("the bar's own strongest accent in the recording, and only "
                                    "when it stands above the bar's mean"),
                "hits": len(drums),
                "swing_ratio": SWING_RATIO,
                "instrument": "crate rack, trigger, built from approved EarAtoms",
                "is_an_interpretation": True,
            },
        },
        "crate": pool_report,
        "rhythm_build": rhythm["build"],
        "renders": {
            "clock": midi_note["clock"],
            "identity_clock_bpm": IDENTITY_CLOCK_BPM,
            "candidate_sha256": candidate_sha,
            "control_sha256": control_sha,
            "rhythm_master_sha256": rhythm["render"]["master_sha256"],
            "rhythm_stems_sha256": rhythm["render"]["stems"],
            "piano_candidate_sha256": piano["master_sha256"],
            "piano_control_sha256": control["master_sha256"],
            "level_matched_lufs": target,
            "part_gains_db": {"piano": 0.0, "bass": BASS_GAIN_DB, "drums": DRUMS_GAIN_DB},
            "levels": levels,
            "renders_are_distinct": candidate_sha != control_sha,
            "pcm_sha256": {
                "candidate": pcm_sha256(trio),
                "control": pcm_sha256(control_matched),
                "rhythm_master": pcm_sha256(rhythm["paths"]["master"]),
                "bass_stem": pcm_sha256(bass_stem),
                "drum_stem": pcm_sha256(drum_stem),
                "piano_candidate": pcm_sha256(candidate_piano),
                "piano_control": pcm_sha256(control_piano),
            },
            "part_fidelity": fidelity,
        },
        "control": {
            "what": ("the incumbent reduction -- root plus voicings, one piano -- over the "
                     "same whole form"),
            "differs_from_the_candidate_by": ("the bass part, the drum part, and the piano's "
                                              "left-hand root"),
            "incumbent_may_win": True,
        },
        "authority": {
            "album_master_accepted": False,
            "moves_album_counter": False,
            "owner_audition_performed": False,
            "rights_or_release_permission": False,
            "system_reference_completed": False,
            "witness_transcription_used": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "source_audio_modified": False,
            "renders_remain_local": True,
            "crate_atoms_named_by_id_not_by_path": True,
        },
        "what_this_is_not": [
            "a transcription of the trio's actual bass line or drum part",
            "a claim that the recovered chords are correct",
            "an accepted master",
        ],
        "reproducibility": {
            "stable_across_runs": ["atom_pool_sha256", "demand_sha256", "proposal_sha256",
                                   "pcm_sha256"],
            "not_stable_across_runs": ["rack_sha256", "binding_sha256", "build_sha256",
                                       "the container digest of every render"],
            "why": ("the crate materializes each selected atom through a WAV container that "
                    "carries bytes the audio does not; two runs decode the same region to "
                    "identical PCM inside non-identical files, and the rack seal hashes the "
                    "file. The selection is deterministic and the sound is deterministic; the "
                    "wrapper is not, and that is recorded here rather than left to be "
                    "discovered by a reproduction attempt"),
            "what_a_second_run_is_held_to": "pcm_sha256",
        },
        "new_organs_added": 0,
        "organs_reused_unmodified": [
            "earcrate.a1_02.performance.demand",
            "earcrate.a1_02.performance.rack",
            "earcrate.a1_02.performance.rack_render",
            "earcrate.midi.codec",
            "earcrate.rack.library",
            "earcrate.rack.render",
        ],
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    (out / "a1-03-trio.private.json").write_text(
        json.dumps({"kind": "earcrate_a1_03_trio_private", "schema_version": 1,
                    "chart": chart, "bass": bass, "drums": drums, "pool": pool},
                   ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    (out / "REVIEW.txt").write_text(f"""A1-03 FLIM -- TRIO CANDIDATE AGAINST ITS CONTROL
=================================================

TWO FILES, {levels['trio']['seconds']:.0f} SECONDS EACH, LEVEL-MATCHED TO {target} LUFS

    {trio.name}
        piano, bass and drums

    {control_matched.name}
        the same chart, the same rack, one piano -- root plus voicings

WHAT DIFFERS
    The bass part, the drum part, and the piano's left-hand root. Nothing else. Same
    recovered chart, same {len(bars)} bars, same clock, same instrument for the piano.

WHAT THE CHART IS
    Recovered from the recording by machine -- beat grid, bar lines, one chord per bar,
    root read from the bass register. Two readers agree on
    {chart['reader_agreement']['bars_where_two_readers_agree']} of {len(bars)} bars. It is
    not a transcription of the trio's actual parts, and it may be wrong.

WHAT IS CHOSEN RATHER THAN RECOVERED
    How the bass walks the chart, and how the drums swing it. The one drum decision taken
    from the recording is where the snare falls: the beat each bar itself accented hardest.

ADMISSIBLE VERDICTS
    THE TRIO
        A1-03 has an accepted candidate; the master lane opens.
    THE CONTROL
        the added parts fail; the piano-only realization stands as A1-03's object and this
        arrangement closes.
    NEITHER
        the chart-driven realization approach for A1-03 closes.

    If the trio loses, say whether it is the bass line, the drum sound, the placement, or
    the balance. Nothing else here needs a verdict.
""", encoding="utf-8", newline="\n")

    print(f"\ncandidate {trio.name}")
    print(f"control   {control_matched.name}")
    print(f"complete trio candidate: {complete}" + (f"  (silent: {silent})" if silent else ""))
    print(f"receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
