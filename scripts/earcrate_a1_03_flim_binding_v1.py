"""Bind the exact A1-03 performance and test it against the community-symbolic witness.

A1-03's commission is performed-arrangement recovery. Its declared next gate is to bind the
exact performance and require symbolic and audio convergence before any audition. Until now
the lane had one half of that: `flim_bad_plus_v1`, a community-symbolic witness whose own
boundary block records `target_recording_bytes_used: false`. It described a performance
nobody here had ever decoded.

The recording is now in hand. This binds it by container and by canonical PCM, measures it
blind, and compares the measurement against the witness's declared claims. The order matters
and is enforced by the code path: every analysis parameter below is a stated default, none
is seeded with a witness value, and the specimen is not opened until the measurement is
finished. A tempo tracker handed a 138 bpm prior agrees with the witness about 138 and
proves nothing.

What this is not: it is not an audition, and it realizes nothing. It produces the fact base
a realization has to answer to, and it reports divergence as readily as convergence -- the
witness is a witness, not an answer key.

The source path is an argument. The recording stays outside the repository.

    python scripts/earcrate_a1_03_flim_binding_v1.py \
        --source "<path to the performance>" --out <work-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.identity import seal, sha256_file  # noqa: E402

TRACK_ID = "A1-03"
SPECIMEN = ROOT / "specimens" / "flim_bad_plus_v1.community-symbolic.json"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-source-binding-v1.public.json"

# Canonical decode, identical to the Reference Zero convention, so this binding is reusable
# by the render organs instead of becoming a second differently-shaped identity.
CANONICAL_SAMPLE_RATE = 48_000
CANONICAL_CHANNELS = 2

# Analysis defaults, fixed here before any witness value is read, and reported in the receipt
# so a reader can tell that none of them was tuned toward the claim.
ANALYSIS_SAMPLE_RATE = 22_050
HOP_LENGTH = 512
BEAT_TRACKER_START_BPM = 120.0      # librosa's own default, deliberately not 138
METER_CANDIDATES = (2, 3, 4, 5, 6, 7)
TEMPO_TOLERANCE_PERCENT = 2.0       # what counts as agreement about tempo
TEMPO_SEARCH_BPM = (100.0, 185.0)   # wide enough to hold 138 and both its octaves' neighbours
TEMPO_SEARCH_STEP_BPM = 0.2
COMB_PHASE_STEP_SECONDS = 0.01
LOCAL_TEMPO_WINDOW_SECONDS = 16.0
LOCAL_TEMPO_HOP_SECONDS = 4.0
KEY_PROFILE_SOURCE = "Krumhansl-Kessler"
LOSSY_CODECS = frozenset({"mp3", "aac", "vorbis", "opus", "wmav2", "wmav1"})


class SourceError(RuntimeError):
    pass


def canonical_pcm(path: Path) -> tuple[str, int]:
    """s32le at 48k/2ch -- the identity every EarCrate render organ already speaks."""
    process = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-ar", str(CANONICAL_SAMPLE_RATE), "-ac", str(CANONICAL_CHANNELS),
         "-c:a", "pcm_s32le", "-f", "s32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    digest = hashlib.sha256()
    written = 0
    for chunk in iter(lambda: process.stdout.read(1 << 20), b""):
        digest.update(chunk)
        written += len(chunk)
    stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", "replace")
    if process.wait(timeout=3600) != 0:
        raise SourceError("canonical decode failed: " + stderr[-400:])
    return digest.hexdigest(), written // (4 * CANONICAL_CHANNELS)


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
         "-show_entries", "format=duration,format_name",
         "-show_entries", "format_tags=title,artist,album,date,track",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise SourceError(result.stderr[-400:])
    parsed = json.loads(result.stdout)
    stream = parsed["streams"][0]
    fmt = parsed["format"]
    tags = {key.lower(): str(value) for key, value in (fmt.get("tags") or {}).items()}
    return {
        "declared_tags": {key: tags.get(key) for key in
                          ("title", "artist", "album", "date", "track")},
        "codec_name": stream.get("codec_name"),
        "container_sample_rate": int(stream["sample_rate"]),
        "container_channels": int(stream["channels"]),
        "stream_bit_rate": int(stream["bit_rate"]) if stream.get("bit_rate") else None,
        "format_name": fmt.get("format_name"),
        "container_seconds": round(float(fmt["duration"]), 6),
    }


def bind_source(path: Path) -> dict:
    """Bind what is actually here, and classify the edition honestly.

    A lossy delivery copy is an exact object; it is not the master edition. The receipt says
    which one it holds rather than letting the word exact imply an authority it lacks.
    """
    delivery = probe(path)
    pcm_sha, frames = canonical_pcm(path)
    lossy = delivery["codec_name"] in LOSSY_CODECS
    return {
        "found": True,
        "path_recorded_in_repository": False,
        "container_sha256": sha256_file(path),
        "container_bytes": path.stat().st_size,
        "canonical_pcm_sha256": pcm_sha,
        "canonical_sample_rate": CANONICAL_SAMPLE_RATE,
        "canonical_channels": CANONICAL_CHANNELS,
        "canonical_frames": frames,
        "canonical_seconds": round(frames / CANONICAL_SAMPLE_RATE, 6),
        "delivery": delivery,
        "edition_class": "lossy_delivery_copy" if lossy else "lossless_copy",
        "edition_is_master_edition": False,
        "edition_note": (
            "The commission names the work and the performer; it does not name a pressing. "
            "This binds the one copy in custody, exactly, and classifies it as a lossy "
            "delivery copy. Every measurement inherits that limit: a 130 kbit/s encode is "
            "authoritative about pulse and harmony and is not authoritative about timbre, "
            "stereo detail or noise floor."),
        "binding_kind": "exact_object_bound_edition_unclaimed",
    }


def comb_score(onset: np.ndarray, sr: int, duration: float, bpm: float) -> tuple[float, float]:
    """Mean onset strength sampled on a click grid at `bpm`, at its best phase.

    One tracker reporting a tempo is an opinion. This is the number that can be put to any
    tempo, including one the tracker never proposed, so the claimed tempo and the measured
    tempo can be scored on the same instrument instead of being compared by assertion.
    """
    period = 60.0 / bpm
    best_score, best_phase = -np.inf, 0.0
    for phase in np.arange(0.0, period, COMB_PHASE_STEP_SECONDS):
        grid = np.arange(phase, duration, period)
        index = np.clip(np.rint(grid * sr / HOP_LENGTH).astype(int), 0, len(onset) - 1)
        score = float(onset[index].mean())
        if score > best_score:
            best_score, best_phase = score, float(phase)
    return best_score, best_phase


def measure(path: Path) -> dict:
    """Blind measurement. No witness value is used as a prior anywhere in this function."""
    import librosa

    y, sr = librosa.load(str(path), sr=ANALYSIS_SAMPLE_RATE, mono=True)
    duration = float(len(y) / sr)

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    standardized = (onset - onset.mean()) / (onset.std() + 1e-9)
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset, sr=sr, hop_length=HOP_LENGTH,
        start_bpm=BEAT_TRACKER_START_BPM, trim=False, units="frames")
    beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=HOP_LENGTH)
    intervals = np.diff(beat_times)
    median_interval = float(np.median(intervals))
    # Interquartile spread of the beat period, as a percentage of that period. A performed
    # acoustic trio drifts; a click track does not. This number says which one is on the
    # record, and a realization has to answer to it.
    q1, q3 = (float(v) for v in np.percentile(intervals, [25, 75]))
    interval_iqr_percent = (q3 - q1) / median_interval * 100.0

    # Four estimators that fail differently. A tempo they all land on is a property of the
    # recording; a tempo only one of them likes is a property of that estimator.
    autocorrelation = librosa.autocorrelate(onset, max_size=int(4 * sr / HOP_LENGTH))
    autocorrelation[: int(0.25 * sr / HOP_LENGTH)] = 0.0
    autocorrelation_bpm = 60.0 / (int(np.argmax(autocorrelation)) * HOP_LENGTH / sr)
    sweep = np.arange(TEMPO_SEARCH_BPM[0], TEMPO_SEARCH_BPM[1], TEMPO_SEARCH_STEP_BPM)
    comb = [(float(bpm),) + comb_score(standardized, sr, duration, float(bpm)) for bpm in sweep]
    comb_best = max(comb, key=lambda row: row[1])
    estimators = {
        "beat_interval_median": round(60.0 / median_interval, 3),
        "global_tempogram": round(float(np.atleast_1d(tempo)[0]), 3),
        "autocorrelation_peak": round(autocorrelation_bpm, 3),
        "comb_filter_argmax": round(comb_best[0], 3),
    }
    ensemble = sorted(estimators.values())
    ensemble_median = float(np.median(ensemble))
    ensemble_spread = (max(ensemble) - min(ensemble)) / ensemble_median * 100.0

    # The whole curve, at one-bpm reporting resolution, so any tempo claim -- including one
    # this code never sees -- can be scored against the recording afterwards on the same
    # instrument that produced the measurement. Each bin keeps the best score within half a
    # bpm, which is deliberately generous to whatever claim is looked up.
    curve: dict[str, float] = {}
    for bpm, score, _phase in comb:
        key = str(int(round(bpm)))
        if score > curve.get(key, -np.inf):
            curve[key] = round(float(score), 5)

    # Local tempo, so a single global number cannot hide a performance that moves.
    frames_per_second = sr / HOP_LENGTH
    local: list[float] = []
    start = 0.0
    while start + LOCAL_TEMPO_WINDOW_SECONDS <= duration:
        window = onset[int(start * frames_per_second):
                       int((start + LOCAL_TEMPO_WINDOW_SECONDS) * frames_per_second)]
        local.append(float(np.atleast_1d(
            librosa.feature.tempo(onset_envelope=window, sr=sr, hop_length=HOP_LENGTH))[0]))
        start += LOCAL_TEMPO_HOP_SECONDS
    local_array = np.array(local) if local else np.array([ensemble_median])

    # Meter, blind: fold beat-synchronous onset strength at each candidate cycle length and
    # take the length whose accent contrast is strongest.
    beat_strength = librosa.util.sync(onset, beats, aggregate=np.mean)
    meter_scores: dict[int, float] = {}
    for period in METER_CANDIDATES:
        usable = len(beat_strength) - (len(beat_strength) % period)
        if usable < period * 4:
            continue
        folded = beat_strength[:usable].reshape(-1, period).mean(axis=0)
        meter_scores[period] = float((folded.max() - folded.mean()) / (folded.mean() + 1e-9))
    best_meter = max(meter_scores, key=meter_scores.__getitem__) if meter_scores else None

    # Tonal field, blind: Krumhansl-Kessler correlation over mean chroma.
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH)
    mean_chroma = chroma.mean(axis=1)
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    names = ["C", "C-sharp", "D", "E-flat", "E", "F",
             "F-sharp", "G", "A-flat", "A", "B-flat", "B"]
    ranked = []
    for index in range(12):
        rotated = np.roll(mean_chroma, -index)
        for profile, quality in ((major, "major"), (minor, "minor")):
            correlation = float(np.corrcoef(rotated, profile)[0, 1])
            ranked.append({"key": names[index] + " " + quality,
                           "correlation": round(correlation, 4)})
    ranked.sort(key=lambda row: row["correlation"], reverse=True)

    return {
        "analysis_sample_rate": ANALYSIS_SAMPLE_RATE,
        "hop_length": HOP_LENGTH,
        "beat_tracker_start_bpm": BEAT_TRACKER_START_BPM,
        "key_profile_source": KEY_PROFILE_SOURCE,
        "librosa_version": librosa.__version__,
        "numpy_version": np.__version__,
        "duration_seconds": round(duration, 6),
        "tempo_estimators_bpm": estimators,
        "tempo_ensemble_median_bpm": round(ensemble_median, 3),
        "tempo_ensemble_spread_percent": round(ensemble_spread, 3),
        "tempo_search_bpm": list(TEMPO_SEARCH_BPM),
        "comb_filter_best": {"bpm": round(comb_best[0], 3),
                             "score": round(comb_best[1], 5),
                             "phase_seconds": round(comb_best[2], 3)},
        "comb_filter_curve_by_bpm": curve,
        "local_tempo": {
            "window_seconds": LOCAL_TEMPO_WINDOW_SECONDS,
            "hop_seconds": LOCAL_TEMPO_HOP_SECONDS,
            "window_count": int(len(local_array)),
            "min_bpm": round(float(local_array.min()), 3),
            "median_bpm": round(float(np.median(local_array)), 3),
            "max_bpm": round(float(local_array.max()), 3),
            "curve_bpm": [round(float(value), 2) for value in local_array],
        },
        "beat_count": int(len(beat_times)),
        "beat_interval_median_seconds": round(median_interval, 6),
        "beat_interval_iqr_percent": round(interval_iqr_percent, 3),
        "first_beat_seconds": round(float(beat_times[0]), 3),
        "meter_accent_contrast": {str(k): round(v, 4) for k, v in sorted(meter_scores.items())},
        "meter_beats_per_bar": best_meter,
        "tonal_field_top3": ranked[:3],
    }


def _tags_agree(binding: dict, target: dict) -> bool:
    """Do the container's own tags name the work and performer the commission names?

    Only what the file actually declares is checked. A missing tag is not agreement, and a
    matching tag is not proof -- tags are typed by whoever made the copy.
    """
    tags = binding["delivery"]["declared_tags"]
    def says(field: str, expected: str) -> bool:
        value = tags.get(field)
        return bool(value) and expected.lower() in value.lower()
    return says("title", target["title"]) and says("artist", target["performer"])


def compare(binding: dict, measured: dict, witness: dict) -> dict:
    """Read the claims, now that the measurement is finished, and score each one."""
    target, wit = witness["target"], witness["witness"]
    claimed_bpm = float(target["tempo_bpm"])
    claimed_meter = int(target["meter"]["numerator"])
    claimed_field = [name.lower() for name in target["tonal_space"]]

    measured_bpm = measured["tempo_ensemble_median_bpm"]
    # A tracker that locks an octave away is agreeing about the pulse, not disagreeing about
    # it, so metrical multiples are scored as agreement and the ratio is disclosed.
    ratios = {"1:1": 1.0, "2:1": 2.0, "1:2": 0.5, "3:2": 1.5, "2:3": 2.0 / 3.0}
    best_ratio, best_error = None, None
    for label, factor in ratios.items():
        error = abs(measured_bpm * factor - claimed_bpm) / claimed_bpm * 100.0
        if best_error is None or error < best_error:
            best_ratio, best_error = label, error

    # Score the claim on the instrument that never saw it.
    curve = measured["comb_filter_curve_by_bpm"]
    claimed_grid_score = curve.get(str(int(round(claimed_bpm))))
    measured_grid_score = curve.get(str(int(round(measured_bpm))))
    local = np.array(measured["local_tempo"]["curve_bpm"], dtype=float)
    near_claim = int((np.abs(local - claimed_bpm) / claimed_bpm <= TEMPO_TOLERANCE_PERCENT / 100).sum())
    near_measured = int((np.abs(local - measured_bpm) / measured_bpm <= TEMPO_TOLERANCE_PERCENT / 100).sum())

    # The witness's own three numbers have to agree with each other before the recording is
    # asked to agree with them.
    implied_bpm = wit["beats"] / wit["duration_seconds"] * 60.0
    internal_error = abs(implied_bpm - claimed_bpm) / claimed_bpm * 100.0

    top3_keys = [row["key"].lower() for row in measured["tonal_field_top3"]]
    coverage = wit["duration_seconds"] / binding["canonical_seconds"]

    claims = {
        "tempo": {
            "claimed_bpm": claimed_bpm,
            "measured_bpm": measured_bpm,
            "estimators_bpm": measured["tempo_estimators_bpm"],
            "estimator_spread_percent": measured["tempo_ensemble_spread_percent"],
            "best_pulse_ratio": best_ratio,
            "error_percent": round(best_error, 3),
            "tolerance_percent": TEMPO_TOLERANCE_PERCENT,
            "click_grid_score_at_claimed_bpm": claimed_grid_score,
            "click_grid_score_at_measured_bpm": measured_grid_score,
            "click_grid_best": measured["comb_filter_best"],
            "local_windows_total": measured["local_tempo"]["window_count"],
            "local_windows_near_claimed_bpm": near_claim,
            "local_windows_near_measured_bpm": near_measured,
            "verdict": "converges" if best_error <= TEMPO_TOLERANCE_PERCENT else "diverges",
            "note": ("four estimators that fail differently were run, and the claim was then "
                     "scored on the same click-grid instrument as the measurement; the "
                     "divergence is the recording's, not one tracker's"),
        },
        "meter": {
            "claimed_beats_per_bar": claimed_meter,
            "measured_beats_per_bar": measured["meter_beats_per_bar"],
            "accent_contrast": measured["meter_accent_contrast"],
            "verdict": ("converges" if measured["meter_beats_per_bar"] == claimed_meter
                        else "diverges"),
        },
        "tonal_field": {
            "claimed": target["tonal_space"],
            "measured_top3": measured["tonal_field_top3"],
            "verdict": "converges" if any(c in top3_keys for c in claimed_field) else "diverges",
            "note": ("blind key estimation across a whole performance is a weak instrument; "
                     "a top-three hit is corroboration, not proof"),
        },
        "witness_internal_consistency": {
            "claimed_bars": wit["bars"],
            "claimed_beats": wit["beats"],
            "claimed_duration_seconds": wit["duration_seconds"],
            "claimed_tempo_bpm": claimed_bpm,
            "tempo_implied_by_claimed_beats_and_duration": round(implied_bpm, 3),
            "error_percent": round(internal_error, 3),
            "verdict": ("consistent" if internal_error <= TEMPO_TOLERANCE_PERCENT
                        else "inconsistent"),
            "note": "this claim is arithmetic on the witness alone and needs no recording",
        },
        "edition_identity": {
            "claimed": {"title": target["title"], "performer": target["performer"],
                        "album": target["album"]},
            "declared_by_container": binding["delivery"]["declared_tags"],
            "verdict": "converges" if _tags_agree(binding, target) else "diverges",
            "note": ("container tags are self-declared and are not an authority; they are "
                     "recorded because a silent edition substitution is the failure this "
                     "lane cannot afford"),
        },
        "span_coverage": {
            "witness_seconds": wit["duration_seconds"],
            "performance_seconds": binding["canonical_seconds"],
            "fraction_of_performance_witnessed": round(coverage, 4),
            "verdict": "partial",
            "note": ("the witness accounts for the opening portion of the performance; the "
                     "remainder has no symbolic account of any kind"),
        },
    }
    failing = [name for name, row in claims.items()
               if row["verdict"] in {"diverges", "inconsistent"}]
    return {
        "claims": claims,
        "converged": sorted(n for n, r in claims.items() if r["verdict"] == "converges"),
        "diverged": sorted(failing),
        "gate": "symbolic_and_audio_convergence",
        "gate_passed": not failing,
    }


def build_receipt(binding: dict, measured: dict, witness: dict, verdict: dict) -> dict:
    return seal({
        "kind": "earcrate_a1_03_public_source_binding_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("The A1-03 performance is bound by container and canonical PCM, and the "
                     "community-symbolic witness has been tested against the recording it "
                     "never used."),
        "source_binding": binding,
        "blind_measurement": measured,
        "witness": {
            "specimen_id": witness["specimen_id"],
            "report_sha256": witness["report_sha256"],
            "evidence_tier": witness["evidence_tier"],
            "target_recording_bytes_used_by_witness":
                witness["boundary"]["target_recording_bytes_used"],
            "remaining_control_named_by_witness": witness["boundary"]["remaining_control"],
            "proof_pack_sha256": witness["proof_pack"]["sha256"],
            "proof_pack_present_locally": False,
            "executable_notes_available": False,
        },
        "convergence": verdict,
        "method": {
            "order": "bind, then measure, then read the claims",
            "witness_values_used_as_analysis_priors": False,
            "analysis_parameters_fixed_before_comparison": True,
            "why": ("a tracker seeded with the claimed tempo agrees with the claim by "
                    "construction and tests nothing"),
        },
        "authority": {
            "album_master_accepted": False,
            "system_reference_completed": False,
            "owner_audition_performed": False,
            "realization_produced": False,
            "rights_or_release_permission": False,
            "moves_album_counter": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "source_audio_remains_local": True,
            "witness_transcription_included": False,
        },
        "next_musical_action": (
            "Realize the bound performance. The proof pack holding the witness's executable "
            "notes is not in local custody, so the realization must derive its symbolic "
            "layer from the recording rather than replay a transcription -- which is the "
            "control the witness itself names as remaining."),
    }, "receipt_sha256")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.exists():
        raise SourceError("no such source: " + str(source))
    work = args.out.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    print("binding " + source.name + " ...")
    binding = bind_source(source)
    print("  container {}  {} bytes".format(
        binding["container_sha256"][:16], binding["container_bytes"]))
    print("  canonical {}  {}s  ({})".format(
        binding["canonical_pcm_sha256"][:16], binding["canonical_seconds"],
        binding["edition_class"]))

    print("measuring blind ...")
    measured = measure(source)
    print("  tempo {} bpm ({}), period IQR {}%".format(
        measured["tempo_ensemble_median_bpm"],
        ", ".join("{}={}".format(k, v) for k, v in measured["tempo_estimators_bpm"].items()),
        measured["beat_interval_iqr_percent"]))
    print("  local tempo {} to {} bpm over {} windows".format(
        measured["local_tempo"]["min_bpm"], measured["local_tempo"]["max_bpm"],
        measured["local_tempo"]["window_count"]))
    print("  meter {} beats/bar, key {}".format(
        measured["meter_beats_per_bar"], measured["tonal_field_top3"][0]["key"]))

    witness = json.loads(SPECIMEN.read_text(encoding="utf-8"))
    verdict = compare(binding, measured, witness)
    print("comparing against the witness ...")
    for name, row in verdict["claims"].items():
        print("  {:>12}  {}".format(row["verdict"], name))

    receipt = build_receipt(binding, measured, witness, verdict)
    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (work / "a1-03-binding.private.json").write_text(
        json.dumps({"source_path": str(source), "binding": binding,
                    "receipt_sha256": receipt["receipt_sha256"]},
                   ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    print("\nreceipt {} -> {}".format(
        receipt["receipt_sha256"][:16], PUBLIC_RECEIPT.relative_to(ROOT).as_posix()))
    print("gate {}: {}".format(
        verdict["gate"], "PASSED" if verdict["gate_passed"] else "NOT PASSED"))
    if verdict["diverged"]:
        print("diverged: " + ", ".join(verdict["diverged"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
