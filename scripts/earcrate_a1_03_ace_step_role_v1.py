"""Give ACE-Step one bounded musical role in A1-03, and measure it against not having it.

The A1-03 realization is a chart played by one piano. The trio it came from has a bass and a
drummer, and the reduction plainly misses them. That is a named, track-level, musical gap --
which is the only thing that entitles a generative provider to be pointed at anything.

So the role is exactly one thing: **replacement instrumentation for the missing rhythm
section**, over one window, once. Not a census, not a benchmark, not a bake-off. The
incumbent is the comp on its own, and the incumbent is allowed to win.

What this produces is a **provider role probe**, and it is not admissible as an owner review.
Two cuts of the same twenty-nine seconds of the same reduction, one of them carrying a bed
that was never conditioned on the recording and does not know the chord changes, can tell a
machine whether that bed contributes low end and rhythmic support. No verdict it can return
selects a track candidate, accepts a master, or decides whether ACE-Step is adopted -- so
under AGENTS.md's owner-review admission rule it never reaches a person, and the pack carries
a machine disposition instead. The cuts remain private diagnostic evidence.

Everything the model is told comes from the recovery, not from the witness and not from
taste: the window's own tempo, the key implied by the recovered chords, the meter, the
duration. The request is written and sealed before the provider is contacted, so the prompt
cannot be quietly tuned after hearing the first result.

Why sixteen bars. The provider generates on a constant grid; the performance does not. Over
the first sixteen bars a fixed grid at the window's own tempo departs from the recovered
downbeats by at most about a tenth of a second, so a generated bed can sit under the comp
honestly. Over thirty-two it exceeds a third of a second, and the audition would be measuring
drift rather than musical fit. The bound is the reason the window is the size it is.

    python scripts/earcrate_a1_03_ace_step_role_v1.py \
        --chart <realization private json> --comp <candidate render wav> --out <pack dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))

from ace_step_v15_adapter import execute as ace_step_execute  # noqa: E402
from earcrate.evidence.identity import seal, sha256_file  # noqa: E402

TRACK_ID = "A1-03"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-ace-step-role-v1.public.json"

WINDOW_BARS = 16
MAX_TOLERATED_GRID_DEPARTURE_SECONDS = 0.15   # why the window is sixteen bars and not more
SEED = 20260819
BED_GAIN_DB = -3.0                            # where the bed sits under the comp
ANALYSIS_SAMPLE_RATE = 22_050
HOP_LENGTH = 512

MAJOR_KEYS = ["C", "D-flat", "D", "E-flat", "E", "F",
              "G-flat", "G", "A-flat", "A", "B-flat", "B"]
CHORD_TEMPLATES = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "min7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11), "sus4": (0, 5, 7), "dim": (0, 3, 6),
}
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)


class RoleError(RuntimeError):
    pass


def window(chart: dict) -> dict:
    """The first sixteen bars, and the proof that a constant grid can serve them."""
    bars = chart["bars"][:WINDOW_BARS]
    if len(bars) < WINDOW_BARS:
        raise RoleError(f"the chart has {len(bars)} bars, fewer than the {WINDOW_BARS} needed")
    downbeats = np.array([bar["beat_times"][0] for bar in bars])
    span = float(downbeats[-1] - downbeats[0])
    bar_period = span / (WINDOW_BARS - 1)
    departure = np.abs(downbeats - (downbeats[0] + np.arange(WINDOW_BARS) * bar_period))
    worst = float(departure.max())
    if worst > MAX_TOLERATED_GRID_DEPARTURE_SECONDS:
        raise RoleError(
            f"a constant grid departs from this window by {worst:.3f}s, past the "
            f"{MAX_TOLERATED_GRID_DEPARTURE_SECONDS}s bound; the audition would measure "
            "drift rather than musical fit")
    return {
        "bars": WINDOW_BARS,
        "start_seconds": round(float(downbeats[0]), 6),
        "duration_seconds": round(span + bar_period, 6),
        "bar_period_seconds": round(bar_period, 6),
        "tempo_bpm": round(4 * 60.0 / bar_period, 3),
        "max_constant_grid_departure_seconds": round(worst, 4),
        "departure_bound_seconds": MAX_TOLERATED_GRID_DEPARTURE_SECONDS,
        "chords": [bar["chord"] for bar in bars],
    }


def key_from_chart(chart: dict) -> dict:
    """The key the recovered chords imply. Derived here, never read from the witness."""
    bars = chart["bars"][:WINDOW_BARS]
    counts = []
    for tonic in range(12):
        scale = {(tonic + step) % 12 for step in MAJOR_STEPS}
        inside = sum(
            1 for bar in bars
            if all((bar["chord_root"] + interval) % 12 in scale
                   for interval in CHORD_TEMPLATES[bar["chord_quality"]]))
        counts.append((inside, tonic))
    inside, tonic = max(counts)
    return {
        "key": f"{MAJOR_KEYS[tonic]} major",
        "chords_inside": inside,
        "chords_total": len(bars),
        "derived_from": "the recovered chords for this window",
        "witness_consulted": False,
    }


def build_request(shape: dict, key: dict) -> dict:
    """The commission, written before the provider is contacted and sealed as written."""
    caption = (
        "acoustic jazz trio rhythm section only: upright double bass and brushed drum kit. "
        "No piano, no keyboard, no guitar, no melody instrument, no vocals. "
        f"Steady {int(round(shape['tempo_bpm']))} bpm, 4/4, in {key['key']}. "
        "Close-miked small-room studio recording, warm and dry, supportive rather than busy, "
        "walking bass and light brushes holding a groove for another player to sit on top of.")
    return seal({
        "kind": "earcrate_a1_03_ace_step_role_request",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "role": "replacement_instrumentation",
        "role_statement": ("supply the rhythm section the piano-only realization is missing, "
                           "for one window, once"),
        "incumbent": "the recovered comp with no bed at all",
        "incumbent_may_win": True,
        "task_mode": "bgm_only",
        "seed": SEED,
        "prompt": {
            "caption": caption,
            "lyrics": "[instrumental]",
            "audio_duration": shape["duration_seconds"],
            "bpm": int(round(shape["tempo_bpm"])),
            "key_scale": key["key"],
            "time_signature": "4/4",
            "model": "acestep-v15-base",
        },
        "output_contract": {
            "duration_seconds": shape["duration_seconds"],
            "channels": 2,
            "authority": "bounded material supplier; not a conductor and not an acceptance",
        },
        "every_conditioning_value_comes_from": "the blind recovery, not the witness",
    }, "request_sha256")


def measure_bed(path: Path, shape: dict) -> dict:
    import librosa

    y, sr = librosa.load(str(path), sr=ANALYSIS_SAMPLE_RATE, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    tempo = float(np.atleast_1d(
        librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=HOP_LENGTH))[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH).mean(axis=1)
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    ranked = sorted(
        ((float(np.corrcoef(np.roll(chroma, -index), major)[0, 1]), MAJOR_KEYS[index])
         for index in range(12)), reverse=True)
    requested = shape["tempo_bpm"]
    ratios = {"1:1": 1.0, "2:1": 2.0, "1:2": 0.5}
    error = min(abs(tempo * factor - requested) / requested * 100.0
                for factor in ratios.values())
    return {
        "duration_seconds": round(float(len(y) / sr), 3),
        "requested_bpm": requested,
        "measured_bpm": round(tempo, 3),
        "tempo_error_percent": round(error, 3),
        "measured_key_top": ranked[0][1] + " major",
        "measured_key_correlation": round(ranked[0][0], 4),
        "note": ("the provider was asked for a tempo and a key; whether it delivered them is "
                 "measured rather than assumed"),
    }


def loudness(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-filter_complex",
         "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
    found = re.search(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)",
                      result.stderr.rsplit("Summary:", 1)[-1], re.S)
    if not found:
        raise RoleError("could not measure loudness")
    return float(found.group(1))


def cut(source: Path, destination: Path, *, duration: float, gain_db: float = 0.0) -> None:
    filters = ["atrim=0:{:.6f}".format(duration), "asetpts=N/SR/TB"]
    if gain_db:
        filters.append("volume={:.3f}dB".format(gain_db))
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
         "-af", ",".join(filters), "-c:a", "pcm_s24le", "-ar", "48000",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
         str(destination)], capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise RoleError(result.stderr[-400:])


def mix(comp: Path, bed: Path, destination: Path, *, bed_gain_db: float) -> None:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(comp), "-i", str(bed),
         "-filter_complex",
         f"[1:a]volume={bed_gain_db:.2f}dB[b];[0:a][b]amix=inputs=2:duration=first:"
         "normalize=0[out]",
         "-map", "[out]", "-c:a", "pcm_s24le", "-ar", "48000",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
         str(destination)], capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise RoleError(result.stderr[-400:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chart", required=True, type=Path,
                        help="the realization's private json, which carries the chart")
    parser.add_argument("--comp", required=True, type=Path,
                        help="the candidate render on the recovered clock")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--reuse-bed", type=Path,
                        help="an already-generated bed for this exact sealed request; the "
                             "role permits one generation, so rebuilding the pack around a "
                             "corrected comp must not call the provider again")
    args = parser.parse_args()

    out = args.out.expanduser().resolve()
    pack = out / "pack"
    work = out / "provider"
    if work.exists() and not args.reuse_bed:
        raise RoleError(f"{work} already holds a provider exchange; move it or choose another "
                        "output directory rather than overwriting an executed request")
    pack.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    chart = json.loads(args.chart.read_text(encoding="utf-8"))["chart"]
    shape = window(chart)
    key = key_from_chart(chart)
    print(f"window: {shape['bars']} bars, {shape['duration_seconds']}s, "
          f"{shape['tempo_bpm']} bpm, {key['key']}")
    print(f"  a constant grid departs by at most "
          f"{shape['max_constant_grid_departure_seconds']}s "
          f"(bound {shape['departure_bound_seconds']}s)")

    request = build_request(shape, key)
    request_path = out / "role-request.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"  request sealed {request['request_sha256'][:16]} before the provider was called")

    if args.reuse_bed:
        bed = args.reuse_bed.expanduser().resolve()
        if not bed.is_file():
            raise RoleError(f"no bed to reuse at {bed}")
        executed = work / "provider-request.private.json"
        if not executed.is_file():
            raise RoleError("cannot reuse a bed without the provider exchange that made it")
        print(f"  reusing the bed already generated for request "
              f"{request['request_sha256'][:16]}; the provider is not called again")
        generated = False
    else:
        print("executing one ACE-Step request ...")
        bed = ace_step_execute(request_path=request_path, output_directory=work, seed=SEED,
                               base_url=args.base_url, source_audio=None,
                               timeout_seconds=1800.0, poll_seconds=2.0)
        generated = True
    print(f"  bed {bed.name} ({bed.stat().st_size} bytes)")

    measured = measure_bed(bed, shape)
    print(f"  bed measures {measured['measured_bpm']} bpm "
          f"({measured['tempo_error_percent']}% from the request), "
          f"key {measured['measured_key_top']}")

    duration = shape["duration_seconds"]
    comp_only = work / "comp-only.wav"
    bed_cut = work / "bed-cut.wav"
    cut(args.comp, comp_only, duration=duration)
    cut(bed, bed_cut, duration=duration)
    with_bed = work / "comp-with-bed.wav"
    mix(comp_only, bed_cut, with_bed, bed_gain_db=BED_GAIN_DB)

    # Blind assignment forced by the two renders, so it cannot be chosen after a verdict.
    digests = {"with_bed": sha256_file(with_bed), "comp_only": sha256_file(comp_only)}
    nonce = hashlib.sha256((digests["with_bed"] + digests["comp_only"]).encode()).hexdigest()
    first = "with_bed" if int(nonce[:8], 16) % 2 == 0 else "comp_only"
    assignment = {"A": first, "B": "comp_only" if first == "with_bed" else "with_bed"}

    sources = {"with_bed": with_bed, "comp_only": comp_only}
    measured_lufs = {role: loudness(path) for role, path in sources.items()}
    target = min(measured_lufs.values())
    for letter, role in assignment.items():
        cut(sources[role], pack / f"{letter}.wav", duration=duration,
            gain_db=target - measured_lufs[role])
    print(f"  LUFS {measured_lufs} -> matched to {target}")

    (pack / "DISPOSITION.txt").write_text(f"""A1-03 FLIM -- ACE-STEP ROLE PROBE
==================================

NO OWNER VERDICT IS OWED ON THESE FILES.

    artifact class              provider_role_probe
    owner review required       no
    owner review pending        no
    ACE-Step adopted            no
    ACE-Step rejected globally  no
    bounded role qualified      not established
    album authority changed     no
    owner action                none

WHAT A.wav AND B.wav ARE
    The same {duration:.1f} seconds of the same piano comp, level-matched. One of them has a
    generated bass-and-drums bed underneath it; the other has nothing underneath it.

WHAT THE COMP IS
    The chart of the first {shape['bars']} bars of the Bad Plus performance of Flim, recovered
    from the recording by machine -- beat grid, bar lines, one chord per bar -- and played by
    a sampled grand piano on the performance's own drifting clock.

    It is a reduction. It is not the performance, and it is not trying to be.

WHAT THE BED IS
    One ACE-Step generation, one seed, one request, made before anything was heard. It was
    told only what the recovery had already established: {int(round(shape['tempo_bpm']))} bpm,
    4/4, {key['key']}, upright bass and brushed drums, no piano and no melody instrument.

    It was not conditioned on the recording, and it does not know the chord changes. It
    knows a tempo and a key.

WHY THIS IS NOT A REVIEW
    A verdict here could say whether a generated bed contributes low end and rhythmic
    support under a reduction. It could not select or reject a complete track candidate,
    accept or reject a master, or decide one localized edit in full context -- so it cannot
    change a track-level authority state, and AGENTS.md's owner-review admission rule keeps
    it away from a person. Stacking a second option under a reduction does not admit it.

    These cuts stay private machine diagnostic evidence. What survives the probe is the
    corrected bass-root chart, the recovered performance clock and the one-generation
    provider receipt -- all retained, none of them owner tasks.
""", encoding="utf-8", newline="\n")

    lines = ["{}  {}".format(sha256_file(path), path.name)
             for path in sorted(pack.glob("*")) if path.name != "MANIFEST.sha256"]
    (pack / "MANIFEST.sha256").write_text("\n".join(lines) + "\n",
                                          encoding="utf-8", newline="\n")

    private = seal({"kind": "earcrate_a1_03_ace_step_role_assignment", "schema_version": 1,
                    "track_id": TRACK_ID, "assignment": assignment, "nonce": nonce,
                    "renders": digests, "measured_lufs": measured_lufs,
                    "level_matched_to": target}, "assignment_sha256")
    (work / "assignment.private.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    receipt = seal({
        "kind": "earcrate_a1_03_public_ace_step_role_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("ACE-Step was given one bounded role on A1-03 -- the rhythm section the "
                     "piano-only realization is missing -- and the result is a provider role "
                     "probe, not an owner review."),
        "artifact_class": "provider_role_probe",
        "disposition": {
            "artifact_class": "provider_role_probe",
            "owner_review_required": False,
            "owner_review_pending": False,
            "ace_step_adopted": False,
            "ace_step_rejected_globally": False,
            "bounded_role_qualified": "not_established",
            "corrected_chart_retained": True,
            "one_generation_receipt_retained": True,
            "album_authority_changed": False,
            "owner_action": "none",
            "why": ("the two cuts compare the same reduction over the same twenty-nine "
                    "seconds; one carries a bed that was not conditioned on the recording "
                    "and does not know the chord changes. That can say whether the bed adds "
                    "low end and rhythmic support. It cannot say whether A1-03 is a "
                    "convincing reconstruction, whether ACE-Step deserves adoption, or "
                    "whether the track belongs on Album One -- so no verdict it returns "
                    "changes a track-level authority state"),
            "rule": ("AGENTS.md -- Owner review admission: a provider probe may never create "
                     "owner_review_pending, and does not become admissible by having a "
                     "second option placed under it"),
            "evidence_status": "retained as private machine diagnostic evidence",
        },
        "role": {
            "role": request["role"],
            "statement": request["role_statement"],
            "incumbent": request["incumbent"],
            "incumbent_may_win": True,
            "generations": 1,
            "provider_called_this_run": generated,
            "bed_reused_because": (None if generated else
                                   "the comp under it changed; the sealed request did not, so "
                                   "regenerating would have been a second generation the role "
                                   "does not permit"),
            "seed": SEED,
            "request_sha256": request["request_sha256"],
            "request_sealed_before_execution": True,
            "conditioning_source": request["every_conditioning_value_comes_from"],
            "is_a_benchmark": False,
            "is_a_provider_census": False,
        },
        "window": shape,
        "key": key,
        "why_sixteen_bars": {
            "max_constant_grid_departure_seconds":
                shape["max_constant_grid_departure_seconds"],
            "bound_seconds": MAX_TOLERATED_GRID_DEPARTURE_SECONDS,
            "reason": ("the provider generates on a constant grid and the performance does "
                       "not; past this bound the audition measures drift rather than "
                       "musical fit"),
        },
        "bed_measurement": measured,
        "probe": {
            "owner_review_required": False,
            "owner_review_pending": False,
            "cuts": 2,
            "blind": "which letter carries the bed",
            "assignment_sealed_sha256": private["assignment_sha256"],
            "assignment_map_withheld": True,
            "assignment_derivation": ("sha256 over the two render digests; forced by the "
                                      "audio before the pack was built"),
            "disclosed": "everything else, including what the bed is and what it was told",
            "level_matched_lufs": target,
            "bed_gain_db_under_the_comp": BED_GAIN_DB,
            "duration_seconds": duration,
            "tie_counts_as_the_incumbent_winning": True,
        },
        "authority": {
            "provider_adopted": False,
            "provider_rejected_globally": False,
            "bounded_role_qualified": "not_established",
            "owner_review_required": False,
            "owner_review_pending": False,
            "album_master_accepted": False,
            "owner_audition_performed": False,
            "generation_is_not_acceptance": True,
            "rights_or_release_permission": False,
            "moves_album_counter": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "prompt_text_exported": False,
            "renders_remain_local": True,
        },
        "on_loss": ("the role closes. Not the track, not the provider, and not the "
                    "realization lane -- one bounded role failed on one window, which is "
                    "a result and not a stop instruction"),
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\npack {pack}")
    for row in lines:
        print("  ", row[:16], row.split("  ")[1])
    print(f"receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    print(f"assignment sealed {private['assignment_sha256'][:16]} (withheld)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
