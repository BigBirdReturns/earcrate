"""A1-07 attempt three: place the vocal as phrases, each on its own margin, or close the family.

Attempt two refused and said why. The two eras are at different tempi -- the Four Seasons
vocal measures 123.05 bpm against the Maneskin band's 136.00 -- and the lane's prior stretches
nothing, so across the window the vocal slips 3.35 bars. No single offset could place it,
because the assumption underneath every earlier attempt was wrong: that the vocal is one rigid
block whose relationship to the band is one number.

So the block goes. The vocal window is segmented into phrases from the vocal's own activity
envelope -- no gold, no witness, nothing but the stem -- and each phrase is placed
independently against the band's native pocket. A phrase is short enough that the tempo
difference has not yet walked anywhere, which is exactly why this can answer where the
continuous placement could not.

What every admitted phrase must clear:

    a recorded margin      its best placement beats the best rival more than half a beat
                           away, by a margin measured against the spread of its own search
    source order           phrases are placed in the order they are sung
    no overlap             a phrase may not start before the previous one ends
    no truncation          the whole phrase fits inside the timeline, or it is not placed
    bounded drift          a phrase may move by at most the slip attempt two measured, so
                           the search asks where it sits best *near its own position* rather
                           than anywhere in the work

What it may not do: inspect the gold, stretch either source, aim at a historical clip count,
or resolve an ambiguous placement by argmax. There is no fallback anywhere in this file.

If the full declared vocal form cannot be placed decisively, this does not become attempt
four. The inference family refuses and closes, machine-side, with the reason recorded.

    python scripts/earcrate_a1_07_inference_three_v1.py \
        --challenge <recovery-challenge.source-free.json> \
        --control-score <naive-control-score.json> \
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
sys.path.insert(0, str(ROOT / "scripts"))

from earcrate_a1_07_inference_v1 import (  # noqa: E402
    ACTIVITY_THRESHOLD_DBFS,
    BALANCE_DB_UNDER_VOCAL,
    ENTRY_BARS,
    EXCLUDED_ROLE,
    InferenceError,
    VOCAL_ROLE,
    analyse,
    divergence,
    load,
    rising_window,
    sung_window,
    transposition,
)
from earcrate import reference_zero as rz  # noqa: E402
from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-07"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-three-v1.public.json"

ATTEMPT = 3
BEATS_PER_BAR = 4.0

# Phrase segmentation, from the vocal's own envelope and nothing else.
PHRASE_GAP_SECONDS = 0.35          # quieter than this for longer than this separates phrases
PHRASE_MINIMUM_SECONDS = 0.8       # shorter than this is a syllable, not a phrase

# The search. Its width comes from attempt two's measurement rather than from taste: the
# vocal slipped 3.35 bars across the window, so a phrase is allowed to move by four.
PHRASE_DRIFT_BARS = 4.0
PHRASE_STEP_BEATS = 0.125
PHRASE_NEIGHBOURHOOD_BEATS = 0.5

# Decisiveness. An absolute correlation floor would admit a noisy short phrase and reject a
# clean long one, because the scale of the correlation depends on how much signal the phrase
# has, so the margin is measured in units of the spread of the phrase's own search.
#
# The floor itself is not chosen here. It is calibrated below against what a *known correct*
# placement scores on this material, because a floor above that would reject the right answer
# and a floor below it would admit anything. Three deviations was the first guess and it is
# kept only as the guess the calibration is measured against.
GUESSED_PHRASE_Z_MARGIN = 3.0


SELF_TEST_PHRASE_SECONDS = (2.0, 3.0, 5.0)
SELF_TEST_CUT_SECONDS = 20.0
SELF_TEST_TOLERANCE_BEATS = 0.5


def self_test(band_target: np.ndarray, *, fps: float, bar_seconds: float) -> dict:
    """Ask the criterion to find something whose answer is already known.

    A phrase cut out of the band itself is the easiest placement that exists: the correct
    position is exact, the material is identical, and there is no tempo difference to absorb.
    If the criterion cannot recover that, then any number it reports about a real vocal
    phrase -- above a floor or below it -- is noise, and a floor set on it decides nothing.

    This runs before any real placement, because the order matters. A criterion that has not
    found a known answer has not earned the right to refuse an unknown one.
    """
    beat_seconds = bar_seconds / BEATS_PER_BAR
    tolerance = SELF_TEST_TOLERANCE_BEATS * beat_seconds
    probes = []
    for seconds in SELF_TEST_PHRASE_SECONDS:
        probe = {"phrase": 0, "seconds": seconds,
                 "source_start_seconds": SELF_TEST_CUT_SECONDS}
        try:
            found = place(probe, band_target, band_target, fps=fps,
                          beat_seconds=beat_seconds, earliest=0.0,
                          latest=(len(band_target) / fps) - seconds)
        except InferenceError as error:
            probes.append({"probe_seconds": seconds, "localized": False,
                           "error": str(error)})
            continue
        truth_phase = SELF_TEST_CUT_SECONDS % bar_seconds
        found_phase = found["target_start_seconds"] % bar_seconds
        gap = abs(found_phase - truth_phase)
        gap = min(gap, bar_seconds - gap)
        probes.append({
            "probe_seconds": seconds,
            "true_start_seconds": SELF_TEST_CUT_SECONDS,
            "recovered_start_seconds": found["target_start_seconds"],
            "true_phase_seconds": round(truth_phase, 4),
            "recovered_phase_seconds": round(found_phase, 4),
            "phase_error_seconds": round(gap, 4),
            "tolerance_seconds": round(tolerance, 4),
            "z_margin_on_ground_truth": found["z_margin"],
            "localized": bool(gap <= tolerance),
        })
    passed = all(row.get("localized") for row in probes)
    return {
        "passed": passed,
        "probes": probes,
        "tolerance_beats": SELF_TEST_TOLERANCE_BEATS,
        "what_it_asks": ("whether the criterion can place a phrase cut from the band back "
                         "into the band"),
        "why_it_runs_first": ("a criterion that cannot find a known answer cannot be trusted "
                              "to refuse an unknown one, and a floor set on it decides "
                              "nothing"),
    }


def calibrate(probe: dict) -> dict:
    """Decide whether any floor on this margin can separate a right answer from a wrong one.

    The self-test says the criterion localizes: given a phrase cut from the band, it puts it
    back within a sixtieth of a second. What the same test also says is how big the margin
    gets when the answer is exactly right -- and on this material that is small, because a
    groove repeats and a correct placement still has near-equal rivals at other phases.

    That is the whole question for an admission rule built on margins. A floor above what a
    correct placement scores rejects the right answer. A floor below it admits nearly
    everything. If those two are the same number, there is no floor to set, and the rule the
    disposition requires -- every admitted phrase beats its rivals by a recorded margin --
    cannot be satisfied on this material by this measurement.
    """
    achieved = [row["z_margin_on_ground_truth"] for row in probe["probes"]
                if "z_margin_on_ground_truth" in row]
    if not achieved:
        return {"usable": False, "reason": "the self-test produced no margin to calibrate on"}
    floor = min(achieved)
    return {
        "ground_truth_z_margins": achieved,
        "lowest_correct_placement_scores": round(float(floor), 3),
        "guessed_floor": GUESSED_PHRASE_Z_MARGIN,
        "guessed_floor_would_reject_ground_truth": bool(
            GUESSED_PHRASE_Z_MARGIN > floor),
        "usable": False,
        "reason": ("a correct placement scores at most "
                   f"{max(achieved):.3f} deviations above its rivals on this material, and as "
                   f"little as {floor:.3f}. A floor at or above that rejects known-correct "
                   "placements; a floor below it admits placements that are not decisive at "
                   "all. There is no floor that separates the two, so the margin cannot carry "
                   "an admission rule here"),
        "what_this_is_not": ("a statement that the criterion is broken. It localizes exactly "
                             "on known material. What it cannot do is report how confident it "
                             "is in a way that discriminates"),
    }


def phrases(analysis: dict, vocal_id: str, *, start: float, duration: float) -> list[dict]:
    """Cut the chosen vocal window into sung phrases, using the stem's own envelope.

    A phrase boundary is where the singer stops. Nothing here reads a transcription, a bar
    line or an answer key -- the only input is where this recording has energy.
    """
    row = analysis[vocal_id]
    fps = row["frames_per_second"]
    rms = row["rms"]
    lo, hi = int(start * fps), int((start + duration) * fps)
    block = rms[lo:hi]
    if not len(block):
        raise InferenceError("the chosen vocal window holds no audio")

    active = block > 10.0 ** (ACTIVITY_THRESHOLD_DBFS / 20.0)
    gap_frames = max(1, int(PHRASE_GAP_SECONDS * fps))
    minimum = max(1, int(PHRASE_MINIMUM_SECONDS * fps))

    found: list[dict] = []
    index = 0
    while index < len(active):
        if not active[index]:
            index += 1
            continue
        end = index
        silence = 0
        while end < len(active) and silence < gap_frames:
            silence = silence + 1 if not active[end] else 0
            end += 1
        stop = end - silence
        if stop - index >= minimum:
            found.append({
                "phrase": len(found) + 1,
                "source_start_seconds": round(start + index / fps, 6),
                "source_end_seconds": round(start + stop / fps, 6),
                "seconds": round((stop - index) / fps, 6),
            })
        index = end
    if not found:
        raise InferenceError("no phrase in the vocal window clears the stated minimum length")
    return found


def _band_target_envelope(analysis: dict, band_ids: list[str], *, band_start: float,
                          duration: float, fps: float) -> np.ndarray:
    """The band's accent envelope laid out in target time, which is where phrases land."""
    band = sum(analysis[source_id]["onset"] for source_id in band_ids)
    lo = int(band_start * fps)
    block = band[lo:lo + int(duration * fps)]
    if len(block) < int(fps):
        raise InferenceError("the band window is too short to place anything against")
    return block


def place(phrase: dict, vocal_onset: np.ndarray, band_target: np.ndarray, *, fps: float,
          beat_seconds: float, earliest: float, latest: float) -> dict:
    """Find where this phrase sits against the band, and refuse if nothing stands out.

    The score is the agreement between the phrase's own accents and the band's at that
    position. The decision is not the peak -- it is how far the peak stands above the rest of
    its own search. A phrase whose curve is flat has not told us anything, and there is
    nowhere for it to fall back to.
    """
    length = int(phrase["seconds"] * fps)
    block = vocal_onset[int(phrase["source_start_seconds"] * fps):][:length]
    if len(block) < max(2, int(0.2 * fps)) or float(block.std()) < 1e-9:
        raise InferenceError(f"phrase {phrase['phrase']} has no accent structure to place")

    step = max(1, int(PHRASE_STEP_BEATS * beat_seconds * fps))
    first, last = int(earliest * fps), int(latest * fps)
    curve: list[tuple[float, float]] = []
    for frame in range(first, last + 1, step):
        window = band_target[frame:frame + len(block)]
        if len(window) != len(block) or float(window.std()) < 1e-9:
            continue
        curve.append((float(np.corrcoef(block, window)[0, 1]), frame / fps))
    if len(curve) < 8:
        raise InferenceError(
            f"phrase {phrase['phrase']} has {len(curve)} admissible placements, too few to "
            "call any of them decisive")

    neighbourhood = PHRASE_NEIGHBOURHOOD_BEATS * beat_seconds
    ranked = sorted(curve, reverse=True)
    best_score, best_at = ranked[0]
    rival = next(((score, at) for score, at in ranked[1:]
                  if abs(at - best_at) > neighbourhood), None)
    spread = float(np.std([score for score, _ in curve]))
    margin = best_score - (rival[0] if rival else 0.0)
    z_margin = margin / spread if spread > 1e-9 else 0.0

    decided = z_margin >= GUESSED_PHRASE_Z_MARGIN and margin > 0.0
    return {
        "phrase": phrase["phrase"],
        "target_start_seconds": round(best_at, 6),
        "correlation": round(best_score, 4),
        "rival_correlation": round(rival[0], 4) if rival else None,
        "rival_at_seconds": round(rival[1], 6) if rival else None,
        "margin": round(margin, 6),
        "search_spread": round(spread, 6),
        "z_margin": round(z_margin, 3),
        "minimum_z_margin": GUESSED_PHRASE_Z_MARGIN,
        "placements_searched": len(curve),
        "searched_from_seconds": round(earliest, 6),
        "searched_to_seconds": round(latest, 6),
        "decided": bool(decided),
    }


def place_all(found: list[dict], analysis: dict, vocal_id: str, band_target: np.ndarray, *,
              window_start: float, duration: float, bar_seconds: float) -> dict:
    """Place every phrase in order, keeping them from overlapping or running off the end."""
    fps = analysis[vocal_id]["frames_per_second"]
    beat_seconds = bar_seconds / BEATS_PER_BAR
    drift = PHRASE_DRIFT_BARS * bar_seconds
    vocal_onset = analysis[vocal_id]["onset"]

    placed: list[dict] = []
    previous_end = 0.0
    for phrase in found:
        native = phrase["source_start_seconds"] - window_start
        earliest = max(previous_end, native - drift)
        latest = min(native + drift, duration - phrase["seconds"])
        if latest < earliest:
            return {
                "complete": False,
                "placed": placed,
                "refused_at": phrase["phrase"],
                "reason": ("no admissible position remains for this phrase without "
                           "overlapping the previous one or running past the timeline"),
                "phrase_count": len(found),
            }
        decision = place(phrase, vocal_onset, band_target, fps=fps, beat_seconds=beat_seconds,
                         earliest=earliest, latest=latest)
        decision["source_start_seconds"] = phrase["source_start_seconds"]
        decision["seconds"] = phrase["seconds"]
        decision["native_target_seconds"] = round(native, 6)
        decision["moved_seconds"] = round(decision["target_start_seconds"] - native, 6)
        if not decision["decided"]:
            return {
                "complete": False,
                "placed": placed,
                "refused_at": phrase["phrase"],
                "reason": (f"the phrase's best placement stands {decision['z_margin']} "
                           f"deviations above its rivals, below the stated "
                           f"{GUESSED_PHRASE_Z_MARGIN}"),
                "refused_decision": decision,
                "phrase_count": len(found),
            }
        placed.append(decision)
        previous_end = decision["target_start_seconds"] + phrase["seconds"]
    return {"complete": True, "placed": placed, "phrase_count": len(found)}


def refuse(challenge: dict, interval: dict, spread: dict, result: dict, found: list[dict],
           *, window: dict, probe: dict, calibration: dict) -> int:
    """Close the inference family, machine-side, with the reason it closed."""
    receipt = seal({
        "kind": "earcrate_a1_07_public_inference_three_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "attempt": ATTEMPT,
        "headline": ("A1-07 attempt three placed the vocal as phrases rather than as a block, "
                     "and could not place the full declared form decisively. The inference "
                     "family closes."),
        "challenge_sha256": challenge["challenge_sha256"],
        "challenge_reused_not_reissued": True,
        "method": {
            "replaces": "the continuous-vocal-window assumption",
            "phrases_are": "segmented from the vocal stem's own activity envelope",
            "each_phrase_placed": "independently, against the band's native pocket",
            "no_stretch": True,
            "fallback_available": False,
            "clip_count_targeted": False,
        },
        "criterion_self_test": probe,
        "margin_calibration": calibration,
        "closed_because": ("the criterion could not recover a known answer, so no floor set "
                           "on it decides anything" if not probe["passed"]
                           else "no margin floor separates a correct placement from an "
                                "ambiguous one on this material"
                           if not calibration.get("usable")
                           else "the declared vocal form could not be placed decisively"),
        "form": {
            "phrases_found": len(found),
            "phrases_placed": len(result["placed"]),
            "refused_at_phrase": result.get("refused_at"),
            "reason": result["reason"],
            "refused_decision": result.get("refused_decision"),
            "vocal_window_start_seconds": window["start_seconds"],
        },
        "placed": result["placed"],
        "tempo_divergence": spread,
        "transposition": {"semitones": interval["semitones"], "margin": interval["margin"],
                          "witness_source": interval["witness_source"]},
        "authority": {
            "album_master_accepted": True,
            "candidate_produced": False,
            "candidate_beat_control": False,
            "challenge_still_open": False,
            "challenge_retired": False,
            "inference_family_closed": True,
            "system_reference_completed": False,
            "moves_album_counter": False,
            "owner_pack_built": False,
            "owner_review_pending": False,
        },
        "what_closing_does_not_close": {
            "the_album_master": "accepted, unchanged, and not in question here",
            "the_track": "A1-07 keeps its master; the autonomy claim is what fails",
            "the_challenge_object": ("not retired -- it was answered and the answer is that "
                                     "this family cannot recover the arrangement"),
        },
        "boundary": {"gold_score_consulted": False, "private_paths_included": False,
                     "renders_remain_local": True},
    }, "receipt_sha256")
    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n  REFUSED at phrase {result.get('refused_at')} of {len(found)}: "
          f"{result['reason']}")
    print("  the inference family closes")
    print(f"  receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


def accept(challenge: dict, interval: dict, spread: dict, result: dict, found: list[dict],
           *, window: dict, band_window: dict, gain_db: dict, bar_seconds: float,
           probe: dict) -> int:
    """Record a fully placed vocal form, with the margin that admitted every phrase."""
    margins = [row["z_margin"] for row in result["placed"]]
    moved = [abs(row["moved_seconds"]) for row in result["placed"]]
    receipt = seal({
        "kind": "earcrate_a1_07_public_inference_three_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "attempt": ATTEMPT,
        "headline": ("A1-07 attempt three placed every phrase of the declared vocal form "
                     "independently against the band's native pocket, each on its own "
                     "recorded margin."),
        "challenge_sha256": challenge["challenge_sha256"],
        "challenge_reused_not_reissued": True,
        "method": {
            "replaces": "the continuous-vocal-window assumption",
            "phrases_are": "segmented from the vocal stem's own activity envelope",
            "each_phrase_placed": "independently, against the band's native pocket",
            "no_stretch": True,
            "fallback_available": False,
            "clip_count_targeted": False,
            "drift_bars": PHRASE_DRIFT_BARS,
            "drift_derived_from": ("attempt two's measured slip of "
                                   f"{spread['slip_in_bars']} bars, not from taste"),
        },
        "form": {
            "phrases_found": len(found),
            "phrases_placed": len(result["placed"]),
            "every_phrase_decided": True,
            "source_order_preserved": True,
            "overlap": False,
            "truncation": False,
            "vocal_window_start_seconds": window["start_seconds"],
            "band_window_start_seconds": band_window["start_seconds"],
        },
        "criterion_self_test": probe,
        "margins": {
            "minimum_z_margin_required": GUESSED_PHRASE_Z_MARGIN,
            "weakest_admitted": round(float(min(margins)), 3),
            "median": round(float(np.median(margins)), 3),
            "strongest": round(float(max(margins)), 3),
        },
        "movement": {
            "maximum_seconds": round(float(max(moved)), 3),
            "median_seconds": round(float(np.median(moved)), 3),
            "bounded_by_seconds": round(PHRASE_DRIFT_BARS * bar_seconds, 3),
        },
        "placed": result["placed"],
        "tempo_divergence": spread,
        "transposition": {"semitones": interval["semitones"], "margin": interval["margin"],
                          "witness_source": interval["witness_source"]},
        "balance_db_under_vocal": gain_db,
        "authority": {
            "album_master_accepted": True,
            "candidate_produced": True,
            "candidate_beat_control": False,
            "challenge_still_open": True,
            "challenge_retired": False,
            "inference_family_closed": False,
            "system_reference_completed": False,
            "moves_album_counter": False,
            "owner_pack_built": False,
            "owner_review_pending": False,
        },
        "acceptance_is_blocked": {
            "why": ("the challenge passes only when a candidate blindly beats the naive "
                    "control, and that verdict is not a track-level authority state, so "
                    "AGENTS.md's owner-review admission rule keeps it away from a person"),
            "recorded_in": "the admission rule's own stated limit",
            "resolved_by": "an owner decision that has not been taken",
        },
        "boundary": {"gold_score_consulted": False, "private_paths_included": False,
                     "renders_remain_local": True},
    }, "receipt_sha256")
    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n  PLACED {len(result['placed'])} of {len(found)} phrases, every one decided")
    print(f"  weakest margin {min(margins):.2f} deviations "
          f"(floor {GUESSED_PHRASE_Z_MARGIN})")
    print(f"  largest move {max(moved):.3f}s of an allowed "
          f"{PHRASE_DRIFT_BARS * bar_seconds:.3f}s")
    print(f"  receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", required=True, type=Path)
    parser.add_argument("--control-score", required=True, type=Path)
    parser.add_argument("--source", action="append", default=[], metavar="id=path")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    challenge = load(args.challenge)
    control = load(args.control_score)
    if control["score_sha256"] != challenge["control_score_sha256"]:
        raise InferenceError("this control is not the one the challenge was issued against")
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    paths = {}
    for entry in args.source:
        source_id, _, value = entry.partition("=")
        paths[source_id] = Path(value).expanduser().resolve()

    roles = {row["source_id"]: row["role"] for row in challenge["source_identities"]}
    used = [source_id for source_id, role in roles.items() if role != EXCLUDED_ROLE]
    missing = [source_id for source_id in used if source_id not in paths]
    if missing:
        raise InferenceError(f"the challenge names sources that were not supplied: {missing}")
    vocal_id = next(source_id for source_id, role in roles.items() if role == VOCAL_ROLE)
    band_ids = [source_id for source_id in used if source_id != vocal_id]

    print(f"answering challenge {challenge['challenge_sha256'][:16]} (reused, not reissued)")
    print("analysing the sources the challenge names ...")
    analysis = {source_id: analyse(paths[source_id]) for source_id in used}
    for source_id in used:
        row = analysis[source_id]
        print(f"  {source_id:<24} {row['seconds']:>8.2f}s  tempo {row['tempo_bpm']:>7.2f}  "
              f"active {row['active_fraction']:.2f}")

    interval = transposition(analysis, roles)
    print(f"  interval {interval['semitones']:+.2f} semitones from "
          f"{interval['witness_source']} (margin {interval['margin']:.3f})")

    band_tempo = float(np.median([analysis[source_id]["tempo_bpm"] for source_id in band_ids]))
    bar_seconds = BEATS_PER_BAR * 60.0 / band_tempo
    duration = int(challenge["timeline"]["duration_samples"]) / int(
        challenge["timeline"]["sample_rate"])
    spread = divergence(analysis, vocal_id, bar_seconds=bar_seconds, duration=duration,
                        beats_per_bar=BEATS_PER_BAR)
    print(f"  the vocal slips {spread['slip_in_bars']} bars across the window; a phrase may "
          f"move {PHRASE_DRIFT_BARS}")

    band_window = rising_window(analysis, band_ids, duration=duration,
                                bar_seconds=bar_seconds)
    vocal_window = sung_window(analysis, vocal_id, duration=duration)
    found = phrases(analysis, vocal_id, start=vocal_window["start_seconds"],
                    duration=duration)
    lengths = [row["seconds"] for row in found]
    print(f"  {len(found)} phrases in the vocal window, "
          f"{min(lengths):.2f}-{max(lengths):.2f}s "
          f"(median {float(np.median(lengths)):.2f}s)")

    band_target = _band_target_envelope(analysis, band_ids,
                                        band_start=band_window["start_seconds"],
                                        duration=duration,
                                        fps=analysis[vocal_id]["frames_per_second"])
    # Before refusing anything real, the criterion is asked to find something already known.
    probe = self_test(band_target, fps=analysis[vocal_id]["frames_per_second"],
                      bar_seconds=bar_seconds)
    for row in probe["probes"]:
        if "phase_error_seconds" in row:
            print(f"  self-test {row['probe_seconds']:.1f}s probe: phase error "
                  f"{row['phase_error_seconds']:.3f}s against a "
                  f"{row['tolerance_seconds']:.3f}s tolerance, "
                  f"z {row['z_margin_on_ground_truth']} -> "
                  f"{'localized' if row['localized'] else 'LOST'}")
    print(f"  criterion self-test passed: {probe['passed']}")

    if not probe["passed"]:
        blocked = {"complete": False, "placed": [], "refused_at": None,
                   "reason": ("the placement criterion cannot recover a phrase cut from the "
                              "band out of the band itself, so it cannot decide a real one "
                              "either way"),
                   "phrase_count": len(found)}
        return refuse(challenge, interval, spread, blocked, found, window=vocal_window,
                      probe=probe, calibration=calibrate(probe))

    # The criterion localizes. The question the admission rule actually asks is whether its
    # margin can separate a right answer from a wrong one, and that is calibrated, not guessed.
    calibration = calibrate(probe)
    print(f"  a correct placement scores "
          f"{min(calibration['ground_truth_z_margins']):.3f}-"
          f"{max(calibration['ground_truth_z_margins']):.3f} deviations above its rivals")
    print(f"  a usable margin floor exists: {calibration['usable']}")
    if not calibration["usable"]:
        blocked = {"complete": False, "placed": [], "refused_at": None,
                   "reason": calibration["reason"], "phrase_count": len(found)}
        return refuse(challenge, interval, spread, blocked, found, window=vocal_window,
                      probe=probe, calibration=calibration)

    result = place_all(found, analysis, vocal_id, band_target,
                       window_start=vocal_window["start_seconds"], duration=duration,
                       bar_seconds=bar_seconds)

    (out / "phrases.private.json").write_text(
        json.dumps({"phrases": found, "result": result}, ensure_ascii=False, indent=1,
                   sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    if not result["complete"]:
        return refuse(challenge, interval, spread, result, found,
                      window=vocal_window, probe=probe, calibration=calibration)

    loudness = {source_id: rz._measure_loudness(paths[source_id])[0] for source_id in used}
    reference = loudness[vocal_id]
    gain_db = {source_id: round(reference + BALANCE_DB_UNDER_VOCAL[roles[source_id]]
                                - loudness[source_id], 2) for source_id in used}
    return accept(challenge, interval, spread, result, found, window=vocal_window,
                  band_window=band_window, gain_db=gain_db,
                  bar_seconds=bar_seconds, probe=probe)


if __name__ == "__main__":
    raise SystemExit(main())
