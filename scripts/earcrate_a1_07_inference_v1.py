"""Attempt A1-07's recovery challenge: infer an arrangement without seeing the answer.

This is the autonomy claim. The album master is accepted; what has never been tested is
whether EarCrate could have found it. Reference Zero's rule is narrow and this file obeys it:
the accepted score is withheld, a naive control was declared and rendered before any
inference existed, and a candidate passes only by blindly beating that control.

**What this reads.** The source-free challenge -- five source identities with roles, a
timeline, and digest commitments to an answer it never opens -- plus the sources themselves.
It does not open `performance-score.json`, and a guard fails the run if the challenge ever
starts publishing clip decisions.

**What it decides, and on what.** Every choice below is a stated prior applied to a
measurement, not a preference:

- *Transposition.* The two catalogue eras are in different keys. The witness for that
  interval is whichever modern stem gives a *decisive* answer -- the largest margin between
  its own correlation peak and the best rival elsewhere in its own curve -- and if no stem
  clears a stated floor the attempt stops rather than resolving a tie by argmax. The search
  runs in thirds of a semitone, since nothing says the interval is an integer.
- *Time.* Nothing is stretched. A vocal-only stem does not yield a trustworthy tempo, so
  there is no measured relationship to justify stretching, and the minimal-intervention
  prior says leave it alone.
- *Where.* The band window is the one whose bar-level energy rises most across the timeline,
  so the result has somewhere to go. The vocal window is the most continuously sung stretch.
- *Together.* Both parts are folded into one bar of accent phase across the whole window,
  and the vocal is placed at the rotation whose agreement with the band beats every rival
  more than half a beat away by a stated margin. Below that margin the attempt stops. The
  first lineage measured an instantaneous onset-envelope lock instead, scored 0.070,
  correctly called that not a lock, and then placed the vocal anyway by quantizing its
  loudest early attack onto a downbeat -- which assumes that attack is a downbeat attack,
  and rotates the whole lead by however far off it was. That lineage lost on
  synchronisation. There is no fallback here.
- *Entry.* Harmonic material, then bass, then drums, on bar boundaries. An arrangement that
  arrives all at once reads as pasted.
- *Balance.* The challenge calls one source `lead_vocal_authority`. It leads, and the band
  sits under it at stated offsets.

**What it refuses.** `gold_v6_reviewed_compound` is not used. It is a previously reviewed
compound, so a candidate built on it would partly *be* the answer, and the naive control does
not have it either -- which means candidate and control differ in arrangement alone. The
challenge does leak something by naming that source `protected_incumbent_compound`, and that
leak is recorded rather than quietly enjoyed.

    python scripts/earcrate_a1_07_inference_v1.py \
        --challenge <recovery-challenge.source-free.json> \
        --control-score <naive-control-score.json> \
        --control-bindings <control-bindings.private.json> \
        --source four_seasons_vocals=<path> ... --out <attempt dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate import reference_zero as rz  # noqa: E402
from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-07"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-v1.public.json"

EXCLUDED_ROLE = "protected_incumbent_compound"
VOCAL_ROLE = "lead_vocal_authority"

ANALYSIS_SAMPLE_RATE = 22_050
HOP_LENGTH = 512
BINS_PER_OCTAVE = 36                      # thirds of a semitone
TRANSPOSE_SEARCH_SEMITONES = 6.0
ACTIVITY_THRESHOLD_DBFS = -40.0

# Stated priors. Each one is a decision this attempt makes on purpose, in advance.
ENTRY_BARS = {"modern_harmonic_and_room_material": 0,
              "modern_bass_material": 8,
              "modern_drum_material": 16}
BALANCE_DB_UNDER_VOCAL = {"lead_vocal_authority": 0.0,
                          "modern_drum_material": -6.0,
                          "modern_bass_material": -7.0,
                          "modern_harmonic_and_room_material": -12.0}
CLIP_FADE_SECONDS = 0.02
ENTRY_FADE_SECONDS = 0.5
PEAK_NEIGHBOURHOOD_SEMITONES = 1.0
MINIMUM_INTERVAL_MARGIN = 0.05            # below this the search has not answered
ALIGNMENT_PHASE_BINS = 64                 # resolution of one bar of accent phase
ALIGNMENT_NEIGHBOURHOOD_BEATS = 0.5       # how far a rival phase must sit from the peak
MINIMUM_ALIGNMENT_MARGIN = 0.15           # below this the phase search has not answered
CEILING_DBTP = -1.0                       # the candidate may not distort either
HEADROOM_PROBE_GAIN_DB = -12.0            # measure the overshoot where it cannot be clamped


class InferenceError(RuntimeError):
    pass


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def analyse(path: Path) -> dict:
    import librosa

    y, sr = librosa.load(str(path), sr=ANALYSIS_SAMPLE_RATE, mono=True)
    rms = librosa.feature.rms(y=y, hop_length=HOP_LENGTH)[0]
    active = rms > 10.0 ** (ACTIVITY_THRESHOLD_DBFS / 20.0)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    tempo = float(np.atleast_1d(
        librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=HOP_LENGTH))[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH,
                                        bins_per_octave=BINS_PER_OCTAVE,
                                        n_chroma=BINS_PER_OCTAVE)
    voiced = chroma[:, : len(active)][:, active[: chroma.shape[1]]]
    profile = voiced.mean(axis=1) if voiced.size else chroma.mean(axis=1)
    profile = profile / (profile.sum() + 1e-9)
    # Concentration of the pitch-class profile. A bass states a root; a room smears one.
    entropy = float(-(profile * np.log(profile + 1e-12)).sum())
    return {
        "seconds": round(float(len(y) / sr), 3),
        "tempo_bpm": round(tempo, 3),
        "active_fraction": round(float(active.mean()), 4),
        "chroma_entropy": round(entropy, 4),
        "rms": rms,
        "onset": onset,
        "profile": profile,
        "frames_per_second": sr / HOP_LENGTH,
    }


def _interval_curve(band: np.ndarray, target: np.ndarray) -> list[tuple[float, float]]:
    steps = int(TRANSPOSE_SEARCH_SEMITONES * BINS_PER_OCTAVE / 12)
    return [(float(np.corrcoef(np.roll(band, offset), target)[0, 1]),
             offset * 12.0 / BINS_PER_OCTAVE)
            for offset in range(-steps, steps + 1)]


def _peak_margin(curve: list[tuple[float, float]]) -> dict:
    """How decisive this stem's answer is: the peak, against the best rival elsewhere.

    A stem whose correlation curve has one sharp peak is telling us an interval. A stem with
    a flat ridge is telling us nothing, and averaging the two would launder the second into
    the first.
    """
    ranked = sorted(curve, reverse=True)
    best_score, best_semitones = ranked[0]
    rival = next(((score, semitones) for score, semitones in ranked[1:]
                  if abs(semitones - best_semitones) > PEAK_NEIGHBOURHOOD_SEMITONES), None)
    if rival is None:
        return {"semitones": best_semitones, "correlation": best_score,
                "rival_correlation": 0.0, "rival_semitones": None, "margin": best_score}
    return {"semitones": best_semitones, "correlation": best_score,
            "rival_correlation": rival[0], "rival_semitones": rival[1],
            "margin": best_score - rival[0]}


def transposition(analysis: dict, roles: dict) -> dict:
    """The interval between the two eras, read from whichever stem actually answers.

    The first version of this chose the witness by chroma entropy, which sounded principled
    and measured 3.531 against 3.537 -- a gap of six thousandths across stems that disagree
    about the answer by five semitones. A criterion that cannot separate its candidates is
    not a criterion, so this one asks the only question that matters: which stem's search has
    a peak that beats everything else in its own curve.
    """
    modern = [source_id for source_id, role in roles.items()
              if role.startswith("modern_") and role != "modern_drum_material"]
    if not modern:
        raise InferenceError("no modern harmonic stem to read an interval from")
    vocal = next(source_id for source_id, role in roles.items() if role == VOCAL_ROLE)
    target = analysis[vocal]["profile"]

    peaks = {source_id: _peak_margin(_interval_curve(analysis[source_id]["profile"], target))
             for source_id in modern}
    witness = max(peaks, key=lambda source_id: peaks[source_id]["margin"])
    chosen = peaks[witness]
    if chosen["margin"] < MINIMUM_INTERVAL_MARGIN:
        raise InferenceError(
            "no stem gives a decisive interval: best margin "
            f"{chosen['margin']:.4f} from {witness}, below the stated "
            f"{MINIMUM_INTERVAL_MARGIN} floor. A tied transposition search is a "
            "non-discriminating result and may not be resolved by argmax.")
    return {
        "witness_source": witness,
        "witness_chosen_by": ("largest margin between its own peak and the best rival "
                              "outside the peak's neighbourhood"),
        "margin": round(chosen["margin"], 4),
        "minimum_margin": MINIMUM_INTERVAL_MARGIN,
        "rejected_witnesses": {
            source_id: {"semitones": round(row["semitones"], 3),
                        "correlation": round(row["correlation"], 4),
                        "margin": round(row["margin"], 4)}
            for source_id, row in peaks.items() if source_id != witness},
        "semitones": round(chosen["semitones"], 3),
        "correlation": round(chosen["correlation"], 4),
        "runner_up": {"semitones": None if chosen["rival_semitones"] is None
                      else round(chosen["rival_semitones"], 3),
                      "correlation": round(chosen["rival_correlation"], 4)},
        "peak_neighbourhood_semitones": PEAK_NEIGHBOURHOOD_SEMITONES,
        "search_resolution_semitones": round(12.0 / BINS_PER_OCTAVE, 4),
        "applied_to": "every modern stem, so the band moves and the vocal is left alone",
    }


def rising_window(analysis: dict, source_ids: list[str], *, duration: float,
                  bar_seconds: float) -> dict:
    """The stretch of band whose bar-level energy climbs most, so the result goes somewhere."""
    frames_per_second = analysis[source_ids[0]]["frames_per_second"]
    energy = sum(analysis[source_id]["rms"] for source_id in source_ids)
    bars = int(duration / bar_seconds)
    frames_per_bar = max(1, int(bar_seconds * frames_per_second))
    ramp = np.linspace(-1.0, 1.0, bars)

    best = None
    step = frames_per_bar
    limit = len(energy) - bars * frames_per_bar
    for start in range(0, max(1, limit), step):
        block = energy[start:start + bars * frames_per_bar]
        block = block[: bars * frames_per_bar].reshape(bars, frames_per_bar).mean(axis=1)
        if block.std() < 1e-9:
            continue
        score = float(np.corrcoef(block, ramp)[0, 1])
        if best is None or score > best[0]:
            best = (score, start)
    if best is None:
        raise InferenceError("no usable band window")
    score, start = best
    return {"start_seconds": round(start / frames_per_second, 6),
            "rise_correlation": round(score, 4),
            "bars": bars,
            "criterion": "bar-level energy correlated with a linear rise"}


def sung_window(analysis: dict, source_id: str, *, duration: float) -> dict:
    """The most continuously sung stretch of the vocal."""
    row = analysis[source_id]
    frames_per_second = row["frames_per_second"]
    active = (row["rms"] > 10.0 ** (ACTIVITY_THRESHOLD_DBFS / 20.0)).astype(float)
    span = int(duration * frames_per_second)
    if span >= len(active):
        raise InferenceError("the vocal is shorter than the timeline")
    cumulative = np.concatenate([[0.0], np.cumsum(active)])
    totals = cumulative[span:] - cumulative[:-span]
    start = int(np.argmax(totals))
    return {"start_seconds": round(start / frames_per_second, 6),
            "sung_fraction": round(float(totals[start] / span), 4),
            "criterion": "densest voiced activity over one timeline length"}


def _bar_phase_profile(onset, start_frame: int, span: int, frames_per_second: float,
                       bar_seconds: float):
    """Where in the bar this signal tends to put its accents, over the whole window.

    Folding at the bar period is what makes a legato lead measurable at all. Instant by
    instant a sung line and a drum kit share almost nothing, which is why the envelope
    correlation that decided this before scored 0.07. Averaged over sixty bars, where a
    singer pushes inside the bar is a real statistic.
    """
    block = onset[start_frame:start_frame + span]
    if len(block) < ALIGNMENT_PHASE_BINS or float(block.max()) <= 0.0:
        raise InferenceError("the chosen window has no accent structure to fold")
    index = np.arange(len(block)) / frames_per_second
    phase = np.floor((index % bar_seconds) / bar_seconds * ALIGNMENT_PHASE_BINS).astype(int)
    phase = np.clip(phase, 0, ALIGNMENT_PHASE_BINS - 1)
    profile = np.zeros(ALIGNMENT_PHASE_BINS)
    counts = np.zeros(ALIGNMENT_PHASE_BINS)
    np.add.at(profile, phase, block)
    np.add.at(counts, phase, 1.0)
    if float(counts.min()) <= 0.0:
        raise InferenceError("the window is too short to fill one bar of phase")
    return profile / counts


def _phase_margin(curve: list[tuple[float, float]], bar_seconds: float,
                  beats_per_bar: float) -> dict:
    """The decisiveness of a phase answer: its peak against the best rival elsewhere.

    Same discipline as the interval witness above, for the same reason. A curve with one
    sharp phase is telling us where the vocal sits in the bar. A flat curve is telling us
    nothing, and resolving that by argmax is how the first attempt placed a vocal it could
    not actually hear against the band.
    """
    neighbourhood = ALIGNMENT_NEIGHBOURHOOD_BEATS * bar_seconds / beats_per_bar
    ranked = sorted(curve, reverse=True)
    best_score, best_offset = ranked[0]
    rival = next(((score, offset) for score, offset in ranked[1:]
                  if min(abs(offset - best_offset),
                         bar_seconds - abs(offset - best_offset)) > neighbourhood), None)
    if rival is None:
        return {"offset_seconds": best_offset, "correlation": best_score,
                "rival_correlation": 0.0, "rival_offset_seconds": None,
                "margin": best_score}
    return {"offset_seconds": best_offset, "correlation": best_score,
            "rival_correlation": rival[0], "rival_offset_seconds": rival[1],
            "margin": best_score - rival[0]}


def align(analysis: dict, vocal_id: str, band_ids: list[str], *, vocal_start: float,
          band_start: float, duration: float, bar_seconds: float,
          beats_per_bar: float) -> dict:
    """Place the vocal in the bar by where its accents actually fall, or refuse to place it.

    The first attempt measured an onset-envelope correlation between the lead and the kit,
    scored 0.070, correctly called that not a lock, and then quantized the vocal's single
    loudest early attack onto a downbeat. That fallback is not a measurement: it assumes the
    attack it found is a downbeat attack, and if the singer's phrase begins off the bar the
    whole lead is rotated by however far off it was. The owner heard exactly that -- the
    material was right and the synchronisation was not.

    So there is no fallback here. Both parts are folded into one bar of accent phase over the
    whole window, the vocal's profile is rotated against the band's, and the placement is the
    rotation whose correlation beats every rival more than half a beat away by a stated
    margin. Below that margin the search has not answered and the attempt stops.

    What this decides is phase within the bar, which is what a bar-periodic criterion can
    honestly decide. Which bar the vocal enters on is the entry decision, and it is made
    elsewhere.
    """
    frames_per_second = analysis[vocal_id]["frames_per_second"]
    span = int(duration * frames_per_second)
    band = sum(analysis[source_id]["onset"] for source_id in band_ids)

    band_profile = _bar_phase_profile(band, int(band_start * frames_per_second), span,
                                      frames_per_second, bar_seconds)
    vocal_profile = _bar_phase_profile(analysis[vocal_id]["onset"],
                                       int(vocal_start * frames_per_second), span,
                                       frames_per_second, bar_seconds)
    if band_profile.std() < 1e-9 or vocal_profile.std() < 1e-9:
        raise InferenceError("a flat accent profile cannot place anything")

    curve = []
    for bins in range(ALIGNMENT_PHASE_BINS):
        rotated = np.roll(vocal_profile, bins)
        score = float(np.corrcoef(rotated, band_profile)[0, 1])
        curve.append((score, bins * bar_seconds / ALIGNMENT_PHASE_BINS))
    chosen = _phase_margin(curve, bar_seconds, beats_per_bar)

    if chosen["margin"] < MINIMUM_ALIGNMENT_MARGIN:
        raise InferenceError(
            f"no phase places the vocal decisively: best margin {chosen['margin']:.4f} at "
            f"{chosen['offset_seconds']:.3f}s, below the stated {MINIMUM_ALIGNMENT_MARGIN} "
            "floor. A non-discriminating placement search may not be resolved by argmax, and "
            "may not fall back to quantizing one attack.")

    # A rotation past half a bar is the same placement reached backwards, and saying so keeps
    # the offset the smallest move that produces it.
    offset = chosen["offset_seconds"]
    if offset > bar_seconds / 2.0:
        offset -= bar_seconds

    return {
        "offset_seconds": round(offset, 6),
        "decided_by": "bar-phase accent agreement across the whole window",
        "correlation": round(chosen["correlation"], 4),
        "margin": round(chosen["margin"], 4),
        "minimum_margin": MINIMUM_ALIGNMENT_MARGIN,
        "runner_up": {
            "offset_seconds": (None if chosen["rival_offset_seconds"] is None
                               else round(chosen["rival_offset_seconds"], 6)),
            "correlation": round(chosen["rival_correlation"], 4),
        },
        "phase_bins_per_bar": ALIGNMENT_PHASE_BINS,
        "phase_resolution_seconds": round(bar_seconds / ALIGNMENT_PHASE_BINS, 6),
        "neighbourhood_beats": ALIGNMENT_NEIGHBOURHOOD_BEATS,
        "bar_seconds": round(bar_seconds, 6),
        "decides": "phase within the bar",
        "does_not_decide": "which bar the vocal enters on; that is the entry decision",
        "replaces": ("the 0.070 onset lock and its downbeat-quantization fallback, which "
                     "placed a vocal on the assumption that its loudest early attack was a "
                     "downbeat attack"),
        "fallback_available": False,
    }


def build_score(challenge: dict, paths: dict, decisions: dict) -> dict:
    timeline = challenge["timeline"]
    sample_rate = int(timeline["sample_rate"])
    duration_samples = int(timeline["duration_samples"])
    bar_samples = int(round(decisions["bar_seconds"] * sample_rate))
    fade = int(CLIP_FADE_SECONDS * sample_rate)
    entry_fade = int(ENTRY_FADE_SECONDS * sample_rate)

    used = [row for row in challenge["source_identities"] if row["role"] != EXCLUDED_ROLE]
    clips_by_track: dict[str, list[dict]] = {}
    for row in used:
        source_id, role = row["source_id"], row["role"]
        if role == VOCAL_ROLE:
            source_start = int(decisions["vocal_start_seconds"] * sample_rate)
            target_start = 0
            length = duration_samples
            pitch = 0.0
            fade_in = fade
        else:
            entry_bars = ENTRY_BARS[role]
            target_start = entry_bars * bar_samples
            length = duration_samples - target_start
            source_start = int((decisions["band_start_seconds"]
                                + entry_bars * decisions["bar_seconds"]) * sample_rate)
            pitch = decisions["transpose_semitones"]
            fade_in = entry_fade if entry_bars else fade
        clips_by_track[source_id] = [{
            "clip_id": f"inferred-{source_id}",
            "source_id": source_id,
            "source_start_sample": source_start,
            "source_end_sample": source_start + length,
            "target_start_sample": target_start,
            "tempo_scale": 1.0,
            "pitch_semitones": pitch,
            "gain_db": decisions["gain_db"][source_id],
            "pan": 0.0,
            "fade_in_samples": min(fade_in, length // 4),
            "fade_out_samples": min(fade, length // 4),
            "musical_function": decisions["function"][source_id],
        }]

    score = rz.seal({
        "schema_version": rz.SCHEMA_VERSION,
        "kind": "earcrate_performance_score",
        "score_id": "album-one-a1-07-inferred-candidate-v1",
        "title": "A1-07 inferred candidate v1",
        "created_at": rz.now_utc(),
        "timeline": {"sample_rate": sample_rate, "channels": int(timeline["channels"]),
                     "duration_samples": duration_samples, "shared_events": []},
        "sources": [{key: value for key, value in row.items()} for row in used],
        "tracks": [{"track_id": source_id, "clips": clips}
                   for source_id, clips in clips_by_track.items()],
        "master": {"codec": "pcm_s24le", "gain_db": 0.0, "peak_limit_dbfs": None},
        "invariants": {"renderer_may_invent_decisions": False,
                       "source_mutation_forbidden": True,
                       "all_selected_clips_must_render": True},
        "authority": {"inferred_from": challenge["challenge_sha256"],
                      "gold_score_consulted": False,
                      "human_acceptance": False},
        "command_history": [{"sequence": 1, "command_id": "inference-v1",
                             "description": decisions["summary"]}],
    })
    rz.validate_performance_score(score)
    return score


def solve_master_gain(score: dict, bindings: dict, *, work: Path) -> tuple[dict, dict]:
    """Attenuate until nothing clips, measured somewhere the measurement survives.

    The control had to learn this and so does the candidate: a blind pair in which both
    options distort is not asking which arrangement is better. The probe renders with the
    master pulled to -12 dB, because measuring the unattenuated render measures a file whose
    samples the 24-bit write already clamped.
    """
    work.mkdir(parents=True, exist_ok=True)
    probe_body = {key: value for key, value in score.items() if key != "score_sha256"}
    probe_body["master"] = {**dict(probe_body["master"]), "gain_db": HEADROOM_PROBE_GAIN_DB}
    probe = rz.seal(probe_body)
    probe_bindings = rz.create_source_bindings(
        probe, paths={row["source_id"]: Path(row["artifact_path"])
                      for row in bindings["bindings"]}, verify_pcm=False)
    rz.render_performance_score(probe, probe_bindings, output_path=work / "probe.wav",
                                receipt_path=work / "probe.receipt.json")
    _, probe_peak = rz._measure_loudness(work / "probe.wav")
    true_peak = probe_peak - HEADROOM_PROBE_GAIN_DB
    gain = min(0.0, round(CEILING_DBTP - true_peak, 2))
    body = {key: value for key, value in score.items() if key != "score_sha256"}
    body["master"] = {**dict(body["master"]), "gain_db": gain}
    solved = rz.seal(body)
    rz.validate_performance_score(solved)
    return solved, {"measured_true_peak_dbtp": round(true_peak, 2),
                    "probe_gain_db": HEADROOM_PROBE_GAIN_DB,
                    "probe_true_peak_dbtp": round(probe_peak, 2),
                    "ceiling_dbtp": CEILING_DBTP,
                    "solved_master_gain_db": gain,
                    "boost_refused": True,
                    "solved_from": "measured true peak, not chosen"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", required=True, type=Path)
    parser.add_argument("--control-score", required=True, type=Path)
    parser.add_argument("--control-bindings", required=True, type=Path)
    parser.add_argument("--source", action="append", required=True,
                        help="source_id=absolute_path")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20260819)
    args = parser.parse_args()

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    challenge = load(args.challenge)
    if challenge.get("kind") != "earcrate_reference_zero_recovery_challenge":
        raise InferenceError("that is not a recovery challenge")
    if challenge["withheld_answer_key"].get("clip_decisions_published"):
        raise InferenceError("the challenge published clip decisions; there is nothing to infer")
    for forbidden in ("tracks", "clips"):
        if forbidden in challenge:
            raise InferenceError(f"the challenge leaks {forbidden}")

    paths = {}
    for entry in args.source:
        source_id, _, path = entry.partition("=")
        paths[source_id] = Path(path).expanduser().resolve()

    roles = {row["source_id"]: row["role"] for row in challenge["source_identities"]}
    used = [source_id for source_id, role in roles.items() if role != EXCLUDED_ROLE]
    vocal_id = next(source_id for source_id, role in roles.items() if role == VOCAL_ROLE)
    band_ids = [source_id for source_id in used if source_id != vocal_id]

    print("analysing the sources the challenge names ...")
    analysis = {}
    for source_id in used:
        if source_id not in paths:
            raise InferenceError(f"no local path supplied for {source_id}")
        analysis[source_id] = analyse(paths[source_id])
        row = analysis[source_id]
        print(f"  {source_id:<24} {row['seconds']:>8.2f}s  tempo {row['tempo_bpm']:>7.2f}  "
              f"active {row['active_fraction']:.2f}  chroma entropy {row['chroma_entropy']:.3f}")

    interval = transposition(analysis, roles)
    print(f"  interval {interval['semitones']:+.2f} semitones from {interval['witness_source']} "
          f"(corr {interval['correlation']:+.3f}, runner-up "
          f"{interval['runner_up']['semitones']:+.2f} at {interval['runner_up']['correlation']:+.3f})")

    band_tempo = float(np.median([analysis[source_id]["tempo_bpm"] for source_id in band_ids]))
    beats_per_bar = 4.0
    bar_seconds = beats_per_bar * 60.0 / band_tempo
    duration = int(challenge["timeline"]["duration_samples"]) / int(
        challenge["timeline"]["sample_rate"])

    band_window = rising_window(analysis, band_ids, duration=duration, bar_seconds=bar_seconds)
    vocal_window = sung_window(analysis, vocal_id, duration=duration)
    locked = align(analysis, vocal_id, band_ids, vocal_start=vocal_window["start_seconds"],
                   band_start=band_window["start_seconds"], duration=duration,
                   bar_seconds=bar_seconds, beats_per_bar=beats_per_bar)
    vocal_start = max(0.0, vocal_window["start_seconds"] + locked["offset_seconds"])
    print(f"  band from {band_window['start_seconds']:.3f}s "
          f"(rise {band_window['rise_correlation']:+.3f}), vocal from {vocal_start:.3f}s "
          f"(bar phase {locked['offset_seconds']:+.3f}s, corr "
          f"{locked['correlation']:+.3f}, margin {locked['margin']:.3f} over "
          f"{locked['minimum_margin']})")

    # Balance, measured rather than guessed, then offset by the stated prior.
    loudness = {}
    for source_id in used:
        loudness[source_id], _ = rz._measure_loudness(paths[source_id])
    reference = loudness[vocal_id]
    gain_db = {source_id: round(reference + BALANCE_DB_UNDER_VOCAL[roles[source_id]]
                                - loudness[source_id], 2) for source_id in used}
    print("  gains " + ", ".join(f"{k}={v:+.1f}" for k, v in gain_db.items()))

    decisions = {
        "bar_seconds": bar_seconds,
        "band_tempo_bpm": round(band_tempo, 3),
        "band_start_seconds": band_window["start_seconds"],
        "vocal_start_seconds": vocal_start,
        "transpose_semitones": interval["semitones"],
        "gain_db": gain_db,
        "function": {source_id: (
            "lead vocal, present throughout" if roles[source_id] == VOCAL_ROLE
            else f"{roles[source_id]} entering at bar {ENTRY_BARS[roles[source_id]]}")
            for source_id in used},
        "summary": ("infer an arrangement from the challenge alone: transpose the modern band "
                    "to the vocal's key, stretch nothing, take the band window that rises, "
                    "lock the vocal to it, and enter harmonic, bass and drums in turn"),
    }

    score = build_score(challenge, paths, decisions)
    rz.write_json(out / "candidate-score.json", score)
    print(f"  candidate score {score['score_sha256'][:16]}")

    bindings = rz.create_source_bindings(score, paths={k: paths[k] for k in used},
                                         verify_pcm=True)
    score, headroom = solve_master_gain(score, bindings, work=out / "candidate-headroom")
    print(f"  true peak {headroom['measured_true_peak_dbtp']:+.2f} dBTP -> master "
          f"{headroom['solved_master_gain_db']:+.2f} dB")
    rz.write_json(out / "candidate-score.json", score)
    bindings = rz.create_source_bindings(score, paths={k: paths[k] for k in used},
                                         verify_pcm=True)
    rz.write_json(out / "candidate-bindings.private.json", bindings)

    reproduction = rz.verify_reproduction(score, bindings,
                                          output_directory=out / "candidate-render")
    print(f"  renders identically: {reproduction['ok']}  "
          f"{reproduction['canonical_pcm_sha256'][:16]}")

    print("preparing the blind review ...")
    review = rz.prepare_candidate_control_review(
        score, load(args.control_score), bindings, load(args.control_bindings),
        output_directory=out / "recovery-review-001",
        dimensions=challenge["review_dimensions"], seed=args.seed)
    assignment = review["assignment"]
    print(f"  assignment {assignment['assignment_sha256'][:16]}, "
          f"options {sorted(assignment['options'])}, choices {assignment['choices']}")

    receipt = seal({
        "kind": "earcrate_a1_07_public_inference_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("An arrangement was inferred from the A1-07 challenge alone and is now "
                     "blind against the naive control. Nothing is claimed until the owner "
                     "listens."),
        "challenge_sha256": challenge["challenge_sha256"],
        "gold_score_consulted": False,
        "sources": {
            "named_by_challenge": len(challenge["source_identities"]),
            "used": sorted(used),
            "excluded": [source_id for source_id, role in roles.items()
                         if role == EXCLUDED_ROLE],
            "exclusion_reason": ("a previously reviewed compound is partly the answer, and "
                                 "the control does not have it either; excluding it makes "
                                 "candidate and control differ in arrangement alone"),
        },
        "leak_disclosed": {
            "leak": ("the challenge publishes source roles, and one of them is named "
                     "protected_incumbent_compound, which tells an inference that a "
                     "previously reviewed object is available"),
            "exploited": False,
            "why_disclosed": ("a blind whose weakness is not stated is worse than a weaker "
                              "blind that is"),
        },
        "measurements": {
            source_id: {key: row[key] for key in
                        ("seconds", "tempo_bpm", "active_fraction", "chroma_entropy")}
            for source_id, row in analysis.items()},
        "decisions": {
            "transposition": interval,
            "time_stretch": {"applied": False,
                             "reason": ("a vocal-only stem yields no trustworthy tempo, so "
                                        "no measured relationship justifies stretching")},
            "band_window": band_window,
            "vocal_window": vocal_window,
            "alignment": locked,
            "entry_bars": ENTRY_BARS,
            "balance_db_under_vocal": BALANCE_DB_UNDER_VOCAL,
            "band_tempo_bpm": decisions["band_tempo_bpm"],
            "gain_db": gain_db,
            "every_decision_is_a_stated_prior_on_a_measurement": True,
        },
        "candidate": {
            "score_sha256": score["score_sha256"],
            "headroom": headroom,
            "renders_identically": reproduction["ok"],
            "canonical_pcm_sha256": reproduction["canonical_pcm_sha256"],
            "container_byte_identity": reproduction["container_byte_identity"],
        },
        "review": {
            "assignment_sha256": assignment["assignment_sha256"],
            "control_score_sha256": assignment["control_score_sha256"],
            "candidate_score_sha256": assignment["candidate_score_sha256"],
            "option_map_withheld": True,
            "choices": assignment["choices"],
            "dimensions": assignment["dimensions"],
            "acceptance": assignment["acceptance"],
        },
        "authority": {
            "system_reference_completed": False,
            "candidate_beat_control": False,
            "owner_audition_performed": False,
            "gold_similarity_measured": False,
            "rights_or_release_permission": False,
            "moves_album_counter": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "option_map_exported": False,
            "renders_remain_local": True,
        },
        "admissible_outcomes": {
            "candidate_wins": ("the withheld-answer recovery is passed and only then is the "
                               "candidate compared to the gold"),
            "control_wins_or_tie_or_reject_all": ("this candidate lineage terminates and the "
                                                  "system reference stays open; it does not "
                                                  "close the track, the challenge, or "
                                                  "another attempt"),
        },
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\nreceipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    print(f"blind pack: {review['public_directory']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
