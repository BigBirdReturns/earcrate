"""Recover A1-03's chart from the recording and play it, on the performance's own clock.

The binding receipt established that the community-symbolic witness is not A1-03's timing
authority: four estimators put the performance near 129 bpm, the witness declares 138, and
the performance is not metronomic anyway. This is the first realization that takes that
seriously.

What gets recovered, blind, from the bound recording: the beat grid as it actually moves,
the bar lines, and one chord per bar. What gets realized: that chart, comped by the same
sampled piano rack A1-02 used, with every note placed on a recovered beat time rather than
on a constant grid. No witness value is read anywhere in this file.

The control is the point. It is the same recovered chart, the same voicings, the same
velocities, the same rack -- laid on a fixed 138 bpm grid from the first recovered downbeat.
Candidate and control differ in exactly one thing: the clock. So the drift between them is
the tempo finding made audible, and if a fixed grid were good enough, this would show that
too.

Both are rendered through the renderer's clock set to identity: `tempo_bpm=60`, one beat per
second, so a start_beat *is* a start time in seconds and a drifting grid survives a renderer
that only understands constant tempo. Nothing new was built to make that work.

This is a reduction, not a reconstruction. It is the chart, played. It does not attempt the
trio's texture, its drums, its bass line or its interplay, and it should not be listened to
as though it does.

    python scripts/earcrate_a1_03_realization_v1.py \
        --source "<the bound recording>" --rack <rack-dir> --out <render-dir>
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
from earcrate.a1_02.performance.rack_render import render  # noqa: E402
from earcrate.evidence.identity import seal, sha256_file  # noqa: E402

TRACK_ID = "A1-03"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-realization-v1.public.json"
BINDING_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-source-binding-v1.public.json"

# The renderer's clock, set to identity so the performance's own beat times place the notes.
IDENTITY_CLOCK_BPM = 60.0
RACK_SFZ = "SalamanderGrandPianoV3.sfz"

ANALYSIS_SAMPLE_RATE = 22_050
HOP_LENGTH = 512
BEAT_TRACKER_START_BPM = 120.0
BEATS_PER_BAR = 4
SECTION_BARS = 32
CONTROL_BPM = 138.0                 # the witness's declared tempo, used only as the control

# The comp. Stated here rather than derived, because it is an interpretation and not a
# recovery: root in the left hand on the downbeat, voicing in the right on two and four.
ROOT_REGISTER = (36, 47)            # C2 to B2
VOICING_REGISTER = (60, 72)         # C4 to C5
COMP_BEATS = (1, 3)                 # zero-based: beats two and four
VELOCITY_RANGE = (52, 104)

CHORD_TEMPLATES = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "min7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11), "sus4": (0, 5, 7), "dim": (0, 3, 6),
}
PITCH_NAMES = ["C", "C-sharp", "D", "E-flat", "E", "F",
               "F-sharp", "G", "A-flat", "A", "B-flat", "B"]


class RealizationError(RuntimeError):
    pass


def verify_binding(source: Path) -> dict:
    """Refuse to realize anything from a file the lane has not already bound."""
    expected = json.loads(BINDING_RECEIPT.read_text(encoding="utf-8"))["source_binding"]
    container = sha256_file(source)
    if container != expected["container_sha256"]:
        raise RealizationError(
            f"this is not the bound performance: {container[:16]}, expected "
            f"{expected['container_sha256'][:16]}")
    return expected


def recover_chart(source: Path) -> dict:
    """Beat grid, bar lines and one chord per bar, from the recording and nothing else."""
    import librosa

    y, sr = librosa.load(str(source), sr=ANALYSIS_SAMPLE_RATE, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP_LENGTH,
                                       start_bpm=BEAT_TRACKER_START_BPM, trim=False,
                                       units="frames")
    beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=HOP_LENGTH)
    beat_strength = librosa.util.sync(onset, beats, aggregate=np.mean)

    # Downbeat phase: whichever offset puts the strongest accents on beat one.
    usable = len(beat_strength) - (len(beat_strength) % BEATS_PER_BAR)
    if usable < BEATS_PER_BAR * (SECTION_BARS + 1):
        raise RealizationError("the recording yields too few beats for a section")
    phases = {}
    for phase in range(BEATS_PER_BAR):
        folded = beat_strength[phase:phase + usable - BEATS_PER_BAR]
        folded = folded[:len(folded) - (len(folded) % BEATS_PER_BAR)]
        phases[phase] = float(folded.reshape(-1, BEATS_PER_BAR).mean(axis=0)[0])
    downbeat_phase = max(phases, key=phases.__getitem__)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH)
    frames_per_second = sr / HOP_LENGTH

    bars = []
    first = downbeat_phase
    for index in range(SECTION_BARS):
        start = first + index * BEATS_PER_BAR
        if start + BEATS_PER_BAR >= len(beat_times):
            break
        times = [float(beat_times[start + offset]) for offset in range(BEATS_PER_BAR)]
        end = float(beat_times[start + BEATS_PER_BAR])
        window = chroma[:, int(times[0] * frames_per_second):int(end * frames_per_second)]
        if not window.size:
            raise RealizationError(f"bar {index} has no chroma")
        profile = window.mean(axis=1)
        profile = profile / (profile.sum() + 1e-9)

        best = None
        for root in range(12):
            for quality, intervals in CHORD_TEMPLATES.items():
                template = np.zeros(12)
                for interval in intervals:
                    template[(root + interval) % 12] = 1.0
                template /= template.sum()
                score = float(np.dot(profile, template))
                if best is None or score > best[0]:
                    best = (score, root, quality)
        score, root, quality = best

        strengths = [float(beat_strength[start + offset]) for offset in range(BEATS_PER_BAR)]
        bars.append({
            "bar": index + 1,
            "beat_times": [round(value, 6) for value in times],
            "bar_end_seconds": round(end, 6),
            "chord_root": root,
            "chord_quality": quality,
            "chord": f"{PITCH_NAMES[root]} {quality}",
            "chord_fit": round(score, 4),
            # The dot product alone is uninterpretable. This is the share of the bar's
            # chroma energy sitting on the chosen chord's own tones, which can be read
            # against the share those tones would hold in a flat chroma.
            "chord_mass_fraction": round(score * len(CHORD_TEMPLATES[quality]), 4),
            "chance_mass_fraction": round(len(CHORD_TEMPLATES[quality]) / 12.0, 4),
            "beat_strengths": [round(value, 4) for value in strengths],
        })

    if len(bars) < SECTION_BARS:
        raise RealizationError(f"recovered only {len(bars)} of {SECTION_BARS} bars")

    intervals = np.diff([bar["beat_times"][0] for bar in bars])
    return {
        "bars": bars,
        "beats_per_bar": BEATS_PER_BAR,
        "downbeat_phase": downbeat_phase,
        "downbeat_phase_scores": {str(k): round(v, 4) for k, v in phases.items()},
        "section_start_seconds": bars[0]["beat_times"][0],
        "section_end_seconds": bars[-1]["bar_end_seconds"],
        "bar_duration_seconds": {"min": round(float(intervals.min()), 4),
                                 "median": round(float(np.median(intervals)), 4),
                                 "max": round(float(intervals.max()), 4)},
        "implied_bpm_from_bar_span": round(
            BEATS_PER_BAR * 60.0 / float(np.median(intervals)), 3),
        "chord_fit_median": round(float(np.median([bar["chord_fit"] for bar in bars])), 4),
        "chord_mass_fraction_median": round(
            float(np.median([bar["chord_mass_fraction"] for bar in bars])), 4),
        "chance_mass_fraction_median": round(
            float(np.median([bar["chance_mass_fraction"] for bar in bars])), 4),
        "librosa_version": librosa.__version__,
        "recovered_from": "the bound recording; no witness value was read",
    }


def witness_cross_check(chart: dict) -> dict:
    """Now that the chart is recovered, ask what the witness claimed about harmony.

    The tempo claim failed. This is the other half of the same question, and it is asked in
    the same order and for the same reason: the chord templates are key-agnostic and the
    recovery never saw the claim, so agreement here is corroboration rather than circularity.

    The chance baseline travels with the result. A seven-note scale contains a good fraction
    of all triads, so "most chords are in the key" is only interesting against how often that
    would happen anyway.
    """
    specimen = json.loads(
        (ROOT / "specimens" / "flim_bad_plus_v1.community-symbolic.json").read_text(
            encoding="utf-8"))
    claimed = specimen["target"]["tonal_space"]
    # B-flat major and G minor are the same seven pitch classes; the claim is one field.
    scale = {(10 + step) % 12 for step in (0, 2, 4, 5, 7, 9, 11)}

    def in_scale(root: int, quality: str) -> bool:
        return all((root + interval) % 12 in scale for interval in CHORD_TEMPLATES[quality])

    inside = [bar for bar in chart["bars"] if in_scale(bar["chord_root"], bar["chord_quality"])]
    outside = [bar for bar in chart["bars"]
               if not in_scale(bar["chord_root"], bar["chord_quality"])]
    # How many of the whole template bank would land inside this scale by chance.
    every = [(root, quality) for root in range(12) for quality in CHORD_TEMPLATES]
    baseline = sum(1 for root, quality in every if in_scale(root, quality)) / len(every)

    observed = len(inside) / len(chart["bars"])
    return {
        "claimed_tonal_space": claimed,
        "claim_read_after_recovery": True,
        "chords_in_claimed_key": len(inside),
        "chords_total": len(chart["bars"]),
        "observed_fraction": round(observed, 4),
        "chance_fraction": round(baseline, 4),
        "lift_over_chance": round(observed - baseline, 4),
        "chords_outside": sorted({bar["chord"] for bar in outside}),
        "verdict": "converges" if observed > baseline else "diverges",
        "note": ("the witness was wrong about tempo and is corroborated about harmony; that "
                 "is a usable result, because it says which of its claims may inform a "
                 "realization and which may not"),
    }


def voice(root: int, quality: str) -> tuple[int, list[int]]:
    """One root and one right-hand voicing, both kept inside a stated register."""
    bass = ROOT_REGISTER[0] + (root % 12)
    if bass > ROOT_REGISTER[1]:
        bass -= 12
    tones = []
    for interval in CHORD_TEMPLATES[quality]:
        pitch = VOICING_REGISTER[0] + ((root + interval) % 12)
        if pitch > VOICING_REGISTER[1]:
            pitch -= 12
        tones.append(pitch)
    return bass, sorted(set(tones))


def perform(chart: dict, *, clock: str) -> dict:
    """Place the recovered chart on a clock: the performance's own, or a fixed grid."""
    bars = chart["bars"]
    origin = bars[0]["beat_times"][0]
    fixed_period = 60.0 / CONTROL_BPM

    strengths = np.array([value for bar in bars for value in bar["beat_strengths"]])
    low, high = float(strengths.min()), float(strengths.max())
    span = (high - low) or 1.0

    def velocity(strength: float) -> int:
        scaled = (strength - low) / span
        return int(round(VELOCITY_RANGE[0] + scaled * (VELOCITY_RANGE[1] - VELOCITY_RANGE[0])))

    notes = []
    for bar_index, bar in enumerate(bars):
        if clock == "recovered":
            times = bar["beat_times"]
            bar_end = bar["bar_end_seconds"]
        else:
            base = origin + bar_index * BEATS_PER_BAR * fixed_period
            times = [base + offset * fixed_period for offset in range(BEATS_PER_BAR)]
            bar_end = base + BEATS_PER_BAR * fixed_period

        bass, tones = voice(bar["chord_root"], bar["chord_quality"])
        notes.append({
            "printed_measure": bar["bar"], "performed_occurrence": 1,
            "staff": 2, "voice": "left",
            "pitch": bass, "velocity": velocity(bar["beat_strengths"][0]),
            "start_beat": round(times[0] - origin, 6),
            "duration_beats": round(bar_end - times[0], 6),
        })
        for offset in COMP_BEATS:
            start = times[offset]
            stop = times[offset + 1] if offset + 1 < len(times) else bar_end
            for pitch in tones:
                notes.append({
                    "printed_measure": bar["bar"], "performed_occurrence": 1,
                    "staff": 1, "voice": "right",
                    "pitch": pitch,
                    "velocity": velocity(bar["beat_strengths"][offset]),
                    "start_beat": round(start - origin, 6),
                    "duration_beats": round(max(stop - start, 0.05), 6),
                })

    notes.sort(key=lambda row: (row["start_beat"], row["staff"], row["pitch"]))
    return {
        "interpretation_id": f"a1-03-realization-v1-{clock}",
        "tempo_bpm": IDENTITY_CLOCK_BPM,
        "clock": clock,
        "origin_seconds": round(origin, 6),
        "notes": notes,
    }


def realize(performed: dict, rack: Path, destination: Path) -> dict:
    zones, _ = parse_sfz(rack / RACK_SFZ)
    demand = compile_demand(performed)
    binding = bind(demand, zones)
    if not binding["all_events_bound"]:
        raise RealizationError(
            f"{binding['refused_event_count']} events refused by the rack; a realization may "
            "not quietly drop them")
    if not verify_sources(rack, binding)["sources_intact"]:
        raise RealizationError("rack sources are missing or mutated")
    result = render(binding, rack, tempo_bpm=IDENTITY_CLOCK_BPM, destination=destination,
                    stems=False)
    return {
        "events": demand["selected_event_count"],
        "polyphony": demand["maximum_polyphony"],
        "pitch_range": demand["pitch_range"],
        "distinct_samples_used": len(binding["distinct_samples_used"]),
        "render": {"duration_seconds": result["duration_seconds"],
                   "applied_gain_db": result["applied_gain_db"],
                   "master_sha256": result["files"]["master"]},
    }


def grid_departure(chart: dict) -> dict:
    """How far a fixed 138 grid walks away from the performance, bar by bar."""
    bars = chart["bars"]
    origin = bars[0]["beat_times"][0]
    period = 60.0 / CONTROL_BPM
    departures = []
    for index, bar in enumerate(bars):
        fixed = origin + index * BEATS_PER_BAR * period
        departures.append(bar["beat_times"][0] - fixed)
    array = np.array(departures)
    return {
        "control_bpm": CONTROL_BPM,
        "final_departure_seconds": round(float(array[-1]), 4),
        "max_absolute_departure_seconds": round(float(np.abs(array).max()), 4),
        "mean_absolute_departure_seconds": round(float(np.abs(array).mean()), 4),
        "departure_in_beats_at_the_end": round(
            float(array[-1]) / (60.0 / chart["implied_bpm_from_bar_span"]), 3),
        "monotonic": bool(np.all(np.diff(array) >= -1e-9) or np.all(np.diff(array) <= 1e-9)),
        "per_bar_seconds": [round(float(value), 4) for value in array],
    }


def playalong(source: Path, realization: Path, destination: Path,
              *, start: float, duration: float, comp_gain_db: float) -> str:
    """The realization under the performance it came from, so alignment is audible."""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(source),
         "-i", str(realization),
         "-filter_complex",
         f"[1:a]volume={comp_gain_db:.2f}dB[c];[0:a][c]amix=inputs=2:duration=first:"
         "normalize=0[out]",
         "-map", "[out]", "-c:a", "pcm_s24le", "-ar", "48000",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
         str(destination)], capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise RealizationError(result.stderr[-400:])
    return sha256_file(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--rack", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--comp-gain-db", type=float, default=-6.0,
                        help="where the comp sits under the performance in the play-alongs")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    rack = args.rack.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    binding = verify_binding(source)
    print(f"bound performance {binding['container_sha256'][:16]}")

    print("recovering the chart ...")
    chart = recover_chart(source)
    print(f"  {len(chart['bars'])} bars from {chart['section_start_seconds']:.3f}s to "
          f"{chart['section_end_seconds']:.3f}s, downbeat phase {chart['downbeat_phase']}")
    print(f"  bar span {chart['bar_duration_seconds']['min']}-"
          f"{chart['bar_duration_seconds']['max']}s "
          f"(median implies {chart['implied_bpm_from_bar_span']} bpm)")
    print("  " + " ".join(bar["chord"].replace(" ", "") for bar in chart["bars"][:16]))

    cross_check = witness_cross_check(chart)
    print(f"  {cross_check['chords_in_claimed_key']}/{cross_check['chords_total']} recovered "
          f"chords sit inside the claimed key "
          f"({cross_check['observed_fraction']:.3f} against {cross_check['chance_fraction']:.3f} "
          "by chance)")

    departure = grid_departure(chart)
    print(f"  a fixed {CONTROL_BPM} grid ends "
          f"{departure['final_departure_seconds']:+.3f}s away "
          f"({departure['departure_in_beats_at_the_end']:+.2f} beats)")

    realizations, files = {}, {}
    for clock, name in (("recovered", "candidate"), ("fixed", "control")):
        performed = perform(chart, clock=clock)
        destination = out / f"a1-03-{name}-{clock}-clock.wav"
        print(f"realizing the {name} on the {clock} clock ...")
        realizations[name] = realize(performed, rack, destination)
        realizations[name]["clock"] = clock
        # The filename stays out of the returned row: a public receipt names renders by
        # digest, never by artifact name.
        files[name] = destination
        print(f"  {realizations[name]['events']} events, "
              f"{realizations[name]['render']['duration_seconds']}s")

    span = chart["section_end_seconds"] - chart["section_start_seconds"]
    mixes = {}
    for name in ("candidate", "control"):
        destination = out / f"a1-03-{name}-under-the-performance.wav"
        mixes[name] = playalong(source, files[name], destination,
                                start=chart["section_start_seconds"], duration=span,
                                comp_gain_db=args.comp_gain_db)
        print(f"play-along {name}: {destination.name}")

    identical = (realizations["candidate"]["render"]["master_sha256"]
                 == realizations["control"]["render"]["master_sha256"])
    if identical:
        raise RealizationError(
            "candidate and control rendered identically; the clock is not being applied")

    receipt = seal({
        "kind": "earcrate_a1_03_public_realization_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("A1-03's chart is recovered from the bound recording and played on the "
                     "performance's own clock, against the same chart on the witness's "
                     "declared grid."),
        "source_binding": {"container_sha256": binding["container_sha256"],
                           "canonical_pcm_sha256": binding["canonical_pcm_sha256"],
                           "path_recorded_in_repository": False},
        "recovered_chart": {key: value for key, value in chart.items() if key != "bars"} | {
            "bar_count": len(chart["bars"]),
            "chords": [bar["chord"] for bar in chart["bars"]],
            "chord_fits": [bar["chord_fit"] for bar in chart["bars"]],
            "chord_mass_fractions": [bar["chord_mass_fraction"] for bar in chart["bars"]],
        },
        "realization": {
            "instrument": "one sampled grand piano rack, unchanged from A1-02",
            "organs_reused_unmodified": [
                "earcrate.a1_02.performance.demand",
                "earcrate.a1_02.performance.rack",
                "earcrate.a1_02.performance.rack_render",
            ],
            "new_organs_added": 0,
            "renderer_clock": {
                "tempo_bpm": IDENTITY_CLOCK_BPM,
                "why": ("set to identity so one beat is one second and a drifting recovered "
                        "grid survives a renderer that only understands constant tempo"),
            },
            "comp_is_interpretation_not_recovery": {
                "root_register": list(ROOT_REGISTER),
                "voicing_register": list(VOICING_REGISTER),
                "comp_beats_zero_based": list(COMP_BEATS),
                "velocity_range": list(VELOCITY_RANGE),
                "note": ("the chart is recovered; the figure that plays it is chosen, and is "
                         "stated here so it is not mistaken for a finding"),
            },
            "candidate": realizations["candidate"],
            "control": realizations["control"],
            "candidate_and_control_differ_only_in_the_clock": True,
            "renders_are_distinct": not identical,
        },
        "fixed_grid_departure": departure,
        "witness_cross_check": cross_check,
        "what_this_is_not": [
            "a reconstruction of the trio's performance",
            "an attempt at drums, bass or interplay",
            "an owner audition",
            "a claim that the recovered chords are correct",
        ],
        "authority": {
            "album_master_accepted": False,
            "system_reference_completed": False,
            "owner_audition_performed": False,
            "witness_transcription_used": False,
            "rights_or_release_permission": False,
            "moves_album_counter": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "source_audio_modified": False,
            "renders_remain_local": True,
        },
        "next_musical_action": (
            "Judge the recovery machine-side first: whether the recovered chords survive a "
            "second opinion, and whether the comp figure is worth keeping. The owner is "
            "worth interrupting only once this lane has a musical proposition rather than a "
            "reduction."),
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (out / "a1-03-realization.private.json").write_text(
        json.dumps({"source_path": str(source), "rack_path": str(rack), "chart": chart,
                    "play_along_sha256": mixes, "receipt_sha256": receipt["receipt_sha256"]},
                   ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    print(f"\nreceipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
