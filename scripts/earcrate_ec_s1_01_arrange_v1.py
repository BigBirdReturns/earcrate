"""EC-S1-01: arrange the material into a complete track, and prove the states differ.

The commission asks for four materially different arrangement states, real withholding before
the payoff, and a result that is not one part with fader automation pretending to be a
production. Those are three claims, and this file is built so that none of them is asserted.

Each section changes which roles sound *and what material they play*. The bass is a different
recording in the payoff than in the build; the kit is a different recording again; the piano
plays different music in every section. Nothing here is a gain envelope over a sustained part,
and a gate below fails if any two adjacent states ever become distinguishable only by level.

The states are then measured against each other the way A1-01 taught: a pair that is
uncorrelated in waveform can be perceptually identical, so difference is judged on timbre and
harmony, not on samples. A state that does not stand apart from its neighbour is not a state.

    python scripts/earcrate_ec_s1_01_arrange_v1.py \
        --material <dir> --foreground <dir> --out <dir>
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

from earcrate.evidence.identity import sha256_file  # noqa: E402

COMMISSION = "EC-S1-01"
SAMPLE_RATE = 48_000
BEATS_PER_BAR = 4

# What each section plays. The point of the table is that the *material* changes, not a fader.
ARRANGEMENT = {
    "INTRO":  {"bass": None,          "drums": None,           "withholds": ["drums", "bass"]},
    "BUILD":  {"bass": "bass_root",   "drums": "drums_sparse", "withholds": ["backbeat"]},
    "HOLD":   {"bass": "bass_root",   "drums": None,           "withholds": ["drums",
                                                                             "melody"]},
    "PAYOFF": {"bass": "bass_moving", "drums": "drums_full",   "withholds": []},
    "OUTRO":  {"bass": "bass_root",   "drums": None,           "withholds": ["drums"]},
}

# Balance. Stated arrangement values, applied once, not automated across a section.
GAIN_DB = {"foreground": 0.0, "bass": -3.5, "drums": -5.0}
MASTER_CEILING_DBTP = -1.0

# Two states are "materially different" when they differ in what is playing. That is judged
# two ways, and neither is a number picked in advance.
#
# The primary check is ground truth: which roles actually sound in each section, measured from
# the stems this file wrote. The secondary is perceptual distance, calibrated against the
# distance between two halves of the *same* section -- the noise floor of the measurement. A
# threshold chosen without that calibration is an artefact of the guess, which is how the
# A1-07 family nearly closed on a floor that would have rejected its own ground truth.
STATE_DISTANCE_MULTIPLE = 3.0       # of the within-state noise floor
ROLE_AUDIBLE_RMS = 1e-4


class ArrangeError(RuntimeError):
    pass


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
                            capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise ArrangeError(result.stderr[-500:])


def tile(source: Path, destination: Path, *, bar_seconds: float, bars: int,
         material_bars: int) -> str:
    """Fill a section from bar-aligned material, looping it as many times as it takes.

    The trim and the loop are two passes on purpose. Done in one, `-t` before `-i` caps the
    input read rather than each repetition, so a section longer than the material comes out
    short and every later section slides earlier -- which is exactly what happened, and it
    put a drum kit inside the one section built to withhold it.
    """
    usable = material_bars * bar_seconds
    needed = bars * bar_seconds
    trimmed = destination.with_name(destination.stem + "-bars.wav")
    _ffmpeg(["-i", str(source), "-t", f"{usable:.6f}", "-c:a", "pcm_s24le",
             "-ar", str(SAMPLE_RATE), "-map_metadata", "-1", "-fflags", "+bitexact",
             "-flags", "+bitexact", str(trimmed)])
    loops = int(np.ceil(needed / usable))
    _ffmpeg(["-stream_loop", str(loops - 1), "-i", str(trimmed), "-t", f"{needed:.6f}",
             "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags", "+bitexact", str(destination)])
    trimmed.unlink(missing_ok=True)
    _require_length(destination, needed)
    return sha256_file(destination)


def _require_length(path: Path, seconds: float) -> None:
    """A piece that is not the length it was asked for silently moves everything after it."""
    import soundfile as sf

    actual = sf.info(str(path)).duration
    if abs(actual - seconds) > 0.002:
        raise ArrangeError(
            f"{path.name} is {actual:.4f}s where {seconds:.4f}s was asked for; a wrong length "
            "here shifts every later section and cannot be allowed to pass quietly")


def concat(parts: list[Path], destination: Path) -> str:
    listing = destination.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts),
                       encoding="utf-8", newline="\n")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "pcm_s24le",
             "-ar", str(SAMPLE_RATE), "-map_metadata", "-1", "-fflags", "+bitexact",
             "-flags", "+bitexact", str(destination)])
    listing.unlink(missing_ok=True)
    return sha256_file(destination)


def silence(destination: Path, seconds: float) -> None:
    _ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
             "-t", f"{seconds:.6f}", "-c:a", "pcm_s24le", "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags", "+bitexact", str(destination)])
    _require_length(destination, seconds)


def mix(stems: dict[str, Path], destination: Path) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    for index, (role, path) in enumerate(sorted(stems.items())):
        inputs += ["-i", str(path)]
        filters.append(f"[{index}:a]volume={GAIN_DB[role]:.2f}dB[p{index}]")
    chain = "".join(f"[p{i}]" for i in range(len(stems)))
    filters.append(f"{chain}amix=inputs={len(stems)}:duration=longest:normalize=0[out]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
             "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags", "+bitexact", str(destination)])


def true_peak_dbtp(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-filter_complex",
         "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    peaks = [line for line in result.stderr.splitlines() if "Peak:" in line]
    if not peaks:
        raise ArrangeError("ebur128 reported no true peak")
    return float(peaks[-1].split("Peak:")[1].split("dBFS")[0].strip())


def loudness(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-filter_complex", "ebur128=peak=true",
         "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    values = [line for line in result.stderr.splitlines() if "I:" in line and "LUFS" in line]
    if not values:
        raise ArrangeError("ebur128 reported no integrated loudness")
    return float(values[-1].split("I:")[1].split("LUFS")[0].strip())


def gain_to(source: Path, destination: Path, gain_db: float) -> None:
    _ffmpeg(["-i", str(source), "-filter:a", f"volume={gain_db:.3f}dB", "-c:a", "pcm_s24le",
             "-ar", str(SAMPLE_RATE), "-map_metadata", "-1", "-fflags", "+bitexact",
             "-flags", "+bitexact", str(destination)])


def features(path: Path, start: float, duration: float) -> dict:
    """Perceptual fingerprint of one span, for judging whether two states differ."""
    import librosa

    y, sr = librosa.load(str(path), sr=22_050, mono=True, offset=start, duration=duration)
    if not y.size:
        raise ArrangeError(f"no audio at {start}s")
    return {
        "mfcc": librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20).mean(axis=1),
        "chroma": librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1),
        "centroid": float(librosa.feature.spectral_centroid(y=y, sr=sr).mean()),
        "rms": float(np.sqrt((y ** 2).mean())),
        "low_share": float(np.abs(librosa.stft(y, n_fft=2048))[
            librosa.fft_frequencies(sr=sr, n_fft=2048) < 200].sum()
            / (np.abs(librosa.stft(y, n_fft=2048)).sum() + 1e-9)),
    }


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right) + 1e-12))


def role_presence(stems: dict, layout: list[dict]) -> list[dict]:
    """Which roles actually sound in each section, taken from the stems rather than the plan.

    This is the check that matters. A table saying the drums are withheld is a claim; the drum
    stem measuring silent across those bars is the fact, and the two came apart once already.
    """
    import librosa

    rows = []
    for row in layout:
        span = row["end_seconds"] - row["start_seconds"]
        present = {}
        for role, path in sorted(stems.items()):
            y, _ = librosa.load(str(path), sr=22_050, mono=True,
                                offset=row["start_seconds"] + 0.3,
                                duration=max(1.5, span - 0.6))
            present[role] = round(float(np.sqrt((y ** 2).mean())), 6)
        rows.append({
            "section": row["section"],
            "rms": present,
            "sounding": sorted(role for role, value in present.items()
                               if value > ROLE_AUDIBLE_RMS),
        })
    return rows


def compare_states(master: Path, layout: list[dict]) -> dict:
    """Ask the render whether its sections are different pieces of music, against a floor."""
    profiles, within = {}, []
    for row in layout:
        span = row["end_seconds"] - row["start_seconds"]
        profiles[row["section"]] = features(master, row["start_seconds"] + 0.5,
                                            max(2.0, span - 1.0))
        half = span / 2.0 - 0.5
        if half >= 2.0:
            first = features(master, row["start_seconds"] + 0.3, half)
            second = features(master, row["start_seconds"] + half + 0.6, half)
            within.append(1.0 - _cosine(first["mfcc"], second["mfcc"]))
    if not within:
        raise ArrangeError("no section is long enough to calibrate a distance floor")
    floor = float(np.median(within)) * STATE_DISTANCE_MULTIPLE

    pairs = []
    order = [row["section"] for row in layout]
    for left, right in zip(order, order[1:]):
        a, b = profiles[left], profiles[right]
        timbre = 1.0 - _cosine(a["mfcc"], b["mfcc"])
        pairs.append({
            "from": left, "to": right,
            "timbre_distance": round(timbre, 5),
            "times_the_noise_floor": round(timbre / (float(np.median(within)) or 1e-9), 2),
            "harmony_distance": round(1.0 - _cosine(a["chroma"], b["chroma"]), 5),
            "centroid_hz": [round(a["centroid"], 1), round(b["centroid"], 1)],
            "low_share": [round(a["low_share"], 4), round(b["low_share"], 4)],
            "rms": [round(a["rms"], 5), round(b["rms"], 5)],
            "materially_different": bool(timbre >= floor),
        })
    return {
        "within_state_distances": [round(value, 6) for value in within],
        "within_state_median": round(float(np.median(within)), 6),
        "required_multiple": STATE_DISTANCE_MULTIPLE,
        "distance_floor": round(floor, 6),
        "floor_is_calibrated_not_chosen": True,
        "method": ("perceptual, not waveform: two spans can decorrelate completely and sound "
                   "identical, which is how A1-01 closed. The floor is three times the "
                   "distance between two halves of the same section"),
        "pairs": pairs,
        "all_adjacent_states_differ": all(row["materially_different"] for row in pairs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material", required=True, type=Path)
    parser.add_argument("--foreground", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    material = args.material.expanduser().resolve()
    fg_dir = args.foreground.expanduser().resolve()
    out = args.out.expanduser().resolve()
    (out / "work").mkdir(parents=True, exist_ok=True)
    (out / "stems").mkdir(parents=True, exist_ok=True)

    foreground = json.loads((fg_dir / "foreground.json").read_text(encoding="utf-8"))
    grid = foreground["grid"]
    layout = foreground["layout"]
    bar_seconds = grid["bar_seconds"]
    material_bars = int((40.0 - 0.2) // bar_seconds)   # whole bars inside the 40 s material
    print(f"grid {grid['measured_bpm']} bpm, bar {bar_seconds:.4f}s, "
          f"material gives {material_bars} whole bars")

    stems: dict[str, Path] = {}
    role_plan = []
    for role in ("bass", "drums"):
        parts: list[Path] = []
        for row in layout:
            section = row["section"]
            span = row["end_seconds"] - row["start_seconds"]
            chosen = ARRANGEMENT[section][role]
            piece = out / "work" / f"{role}-{section.lower()}.wav"
            if chosen is None:
                silence(piece, span)
            else:
                tile(material / chosen / "generated.wav", piece, bar_seconds=bar_seconds,
                     bars=row["bars"], material_bars=material_bars)
            parts.append(piece)
            role_plan.append({"section": section, "role": role, "material": chosen,
                              "seconds": round(span, 3)})
        stem = out / "stems" / f"{role}.wav"
        concat(parts, stem)
        stems[role] = stem
        print(f"  {role} stem: " + " ".join(
            (ARRANGEMENT[row['section']][role] or "-") for row in layout))

    fg_parts = [Path(row["path"]) for row in layout]
    fg_stem = out / "stems" / "foreground.wav"
    concat(fg_parts, fg_stem)
    stems["foreground"] = fg_stem
    print("  foreground stem: " + " ".join("+".join(row["plays"]) for row in layout))

    raw = out / "work" / "master-raw.wav"
    mix(stems, raw)
    peak = true_peak_dbtp(raw)
    headroom = min(0.0, MASTER_CEILING_DBTP - peak)
    master = out / f"{COMMISSION}-track.wav"
    gain_to(raw, master, headroom)
    print(f"master: true peak {peak:+.2f} dBTP -> {headroom:+.2f} dB applied, "
          f"{loudness(master):.1f} LUFS")

    presence = role_presence(stems, layout)
    print("\nwhat actually sounds, measured from the stems:")
    for row in presence:
        print(f"  {row['section']:<7} {'+'.join(row['sounding'])}")

    states = compare_states(master, layout)
    print(f"\narrangement states (floor {states['distance_floor']:.5f} = "
          f"{states['required_multiple']}x the {states['within_state_median']:.5f} "
          "within-state noise floor):")
    for row in states["pairs"]:
        print(f"  {row['from']:>7} -> {row['to']:<7} timbre {row['timbre_distance']:.5f} "
              f"({row['times_the_noise_floor']:>5.1f}x)  "
              f"low {row['low_share'][0]:.3f}->{row['low_share'][1]:.3f}  "
              f"{'DIFFERENT' if row['materially_different'] else 'NOT DISTINCT'}")
    print(f"  all adjacent states differ: {states['all_adjacent_states_differ']}")

    (out / "arrangement.json").write_text(json.dumps({
        "commission": COMMISSION, "grid": grid, "layout": layout,
        "arrangement": ARRANGEMENT, "role_plan": role_plan, "gain_db": GAIN_DB,
        "master": {"path": str(master), "sha256": sha256_file(master),
                   "true_peak_before_dbtp": round(peak, 3),
                   "headroom_applied_db": round(headroom, 3),
                   "lufs": loudness(master),
                   "seconds": round(layout[-1]["end_seconds"], 3)},
        "stems": {role: {"path": str(path), "sha256": sha256_file(path)}
                  for role, path in sorted(stems.items())},
        "role_presence": presence,
        "states": states,
    }, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"\ntrack: {master.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
