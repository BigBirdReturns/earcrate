"""Gates for the first A1-07 inference attempt.

An autonomy claim is easy to fake and hard to notice being faked. The three ways this one
could quietly stop being a test:

The answer leaks in. The attempt must never have opened the gold score, and the receipt has
to keep saying which sources it used and which it refused — because the previously reviewed
compound is *part of* the answer, and a candidate built on it would be scored for recovering
something it was handed.

A decision gets resolved by argmax when the measurement did not actually answer. The first
version picked its transposition witness by chroma entropy, and the winning margin was six
thousandths across stems that disagreed by five semitones. Every decisive decision here has
to carry the margin that made it decisive.

Both options share a defect. If the candidate and the control both distort, the blind pair
stops asking which arrangement is better and starts asking which one clips less. That is a
non-discriminating frontier and it may not reach the owner.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RESULT = ROOT / "proofs" / "album_one" / "a1-07-inference-result-v1.public.json"
RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-v1.public.json"
CHALLENGE_RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-recovery-challenge-v1.public.json"


def test_the_attempt_answers_the_challenge_that_is_actually_issued():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    issued = load_sealed(CHALLENGE_RECEIPT)["challenge"]
    assert receipt["challenge_sha256"] == issued["challenge_sha256"], (
        "the attempt answers a retired challenge")
    assert receipt["review"]["control_score_sha256"] == issued["control_score_sha256"]


def test_the_gold_was_not_consulted_and_the_compound_was_not_used():
    receipt = load_sealed(RECEIPT)
    assert receipt["gold_score_consulted"] is False
    assert receipt["authority"]["gold_similarity_measured"] is False

    sources = receipt["sources"]
    assert "gold_v6_reviewed_compound" in sources["excluded"]
    assert "gold_v6_reviewed_compound" not in sources["used"]
    assert len(sources["used"]) == sources["named_by_challenge"] - len(sources["excluded"])


def test_the_leak_in_the_challenge_is_disclosed_rather_than_enjoyed():
    """The challenge publishes roles, and one of them names the answer's own material."""
    leak = load_sealed(RECEIPT)["leak_disclosed"]
    assert leak["exploited"] is False
    assert "protected_incumbent_compound" in leak["leak"]
    assert leak["why_disclosed"]


def test_every_decisive_decision_carries_the_margin_that_made_it_decisive():
    decisions = load_sealed(RECEIPT)["decisions"]
    assert decisions["every_decision_is_a_stated_prior_on_a_measurement"] is True

    interval = decisions["transposition"]
    assert interval["margin"] >= interval["minimum_margin"], (
        "a transposition below its own floor should have stopped the attempt")
    assert interval["rejected_witnesses"], "a choice with no rejected alternative is not a choice"
    # The witness has to beat the alternatives on the criterion that selected it.
    assert all(interval["margin"] >= row["margin"]
               for row in interval["rejected_witnesses"].values())


def test_no_live_lineage_rests_on_a_placement_that_cannot_discriminate():
    """The receipt on this head is attempt one's, and attempt one's lineage is terminated.

    So the rule is not "this receipt has the new shape" -- it is that a lineage still standing
    may not rest on the placement that lost. A terminated attempt keeps its own record; a live
    one has to carry a placement decided by a margin, with no fallback.
    """
    alignment = load_sealed(RECEIPT)["decisions"]["alignment"]
    terminated = load_sealed(RESULT)["authority"]["candidate_lineage"] == "terminated"

    if alignment.get("decided_by") != "bar-phase accent agreement across the whole window":
        assert terminated, (
            "a live lineage is resting on the non-discriminating placement that lost")
        return

    assert alignment["fallback_available"] is False
    assert alignment["margin"] >= alignment["minimum_margin"], (
        "a placement below its own floor should have stopped the attempt")
    runner_up = alignment["runner_up"]
    if runner_up["offset_seconds"] is not None:
        assert alignment["correlation"] - runner_up["correlation"] >= alignment["minimum_margin"]

    # A bar-periodic criterion decides phase, and saying so keeps it from being read as
    # having chosen the entry bar.
    assert alignment["decides"] == "phase within the bar"
    assert "which bar" in alignment["does_not_decide"]
    assert abs(alignment["offset_seconds"]) <= alignment["bar_seconds"] / 2.0 + 1e-6


def test_the_placement_that_lost_cannot_come_back():
    """Attempt one's lineage was terminated on synchronisation. The mechanism that produced
    it is named in the source so its return is a visible edit rather than a quiet one."""
    source = (ROOT / "scripts" / "earcrate_a1_07_inference_v1.py").read_text(encoding="utf-8")
    assert "MINIMUM_ALIGNMENT_LOCK" not in source, "the onset-lock threshold is back"
    assert "MINIMUM_ALIGNMENT_MARGIN" in source
    assert "_bar_phase_profile" in source


def test_the_candidate_does_not_distort_either():
    headroom = load_sealed(RECEIPT)["candidate"]["headroom"]
    assert headroom["boost_refused"] is True
    assert headroom["solved_master_gain_db"] <= 0.0
    assert headroom["measured_true_peak_dbtp"] + headroom["solved_master_gain_db"] <= \
        headroom["ceiling_dbtp"] + 0.01
    assert headroom["probe_true_peak_dbtp"] < -0.5, (
        "the probe render sat at full scale, so the overshoot it measured had already been "
        "clamped away")


def test_the_candidate_reproduces_and_the_review_is_blind():
    receipt = load_sealed(RECEIPT)
    candidate = receipt["candidate"]
    assert candidate["renders_identically"] is True
    assert len(candidate["canonical_pcm_sha256"]) == 64

    review = receipt["review"]
    assert review["option_map_withheld"] is True
    assert set(review["choices"]) >= {"tie", "reject_all", "abstain"}
    assert review["acceptance"]["candidate_must_beat_control"] is True
    assert review["acceptance"]["least_bad_does_not_pass"] is True
    assert receipt["boundary"]["option_map_exported"] is False


def test_an_attempt_completes_nothing_by_existing():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    assert authority["system_reference_completed"] is False
    assert authority["candidate_beat_control"] is False
    assert authority["owner_audition_performed"] is False
    assert authority["moves_album_counter"] is False

    # And losing has to close the lineage without closing anything else.
    outcomes = receipt["admissible_outcomes"]
    assert "terminates" in outcomes["control_wins_or_tie_or_reject_all"]
    assert "stays open" in outcomes["control_wins_or_tie_or_reject_all"]


def _synthetic(bar_seconds: float, fps: float, bars: int, phase_seconds: float,
               *, structured: bool):
    """An accent envelope with peaks at a known phase in each bar, or with none."""
    import numpy as np

    frames = int(bars * bar_seconds * fps)
    time = np.arange(frames) / fps
    if not structured:
        # Deterministic broadband wobble with no bar period in it at all.
        return 0.5 + 0.5 * np.sin(2.0 * np.pi * time / (bar_seconds * 0.37))
    phase = (time - phase_seconds) % bar_seconds
    beat = bar_seconds / 4.0
    envelope = np.zeros(frames)
    for index, weight in enumerate((1.0, 0.45, 0.7, 0.45)):
        envelope += weight * np.exp(-((phase - index * beat) ** 2) / (2 * (beat / 8.0) ** 2))
    return envelope + 0.05


def test_the_new_placement_criterion_actually_separates_its_candidates():
    """The criterion that lost could not tell its candidates apart. This one is required to,
    on a signal whose answer is known, before it is trusted with a real one."""
    import importlib.util

    import numpy as np

    spec = importlib.util.spec_from_file_location(
        "inference", ROOT / "scripts" / "earcrate_a1_07_inference_v1.py")
    inference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inference)

    bar_seconds, fps, bars = 2.0, 43.0, 60
    truth = 0.5                                   # the vocal sits half a second into the bar
    analysis = {
        "vocal": {"onset": _synthetic(bar_seconds, fps, bars, truth, structured=True),
                  "frames_per_second": fps},
        "band": {"onset": _synthetic(bar_seconds, fps, bars, 0.0, structured=True),
                 "frames_per_second": fps},
    }
    placed = inference.align(analysis, "vocal", ["band"], vocal_start=0.0, band_start=0.0,
                             duration=bars * bar_seconds - 1.0, bar_seconds=bar_seconds,
                             beats_per_bar=4.0)
    # Rotating the vocal forward by (bar - truth) puts its accents back on the band's.
    expected = -truth
    assert abs(placed["offset_seconds"] - expected) <= 3.0 * placed["phase_resolution_seconds"], (
        f"recovered {placed['offset_seconds']:.3f}s, expected {expected:.3f}s")
    assert placed["margin"] >= placed["minimum_margin"]
    assert placed["fallback_available"] is False


def test_an_unplaceable_vocal_stops_the_attempt_instead_of_being_placed():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "inference", ROOT / "scripts" / "earcrate_a1_07_inference_v1.py")
    inference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inference)

    bar_seconds, fps, bars = 2.0, 43.0, 60
    analysis = {
        "vocal": {"onset": _synthetic(bar_seconds, fps, bars, 0.0, structured=False),
                  "frames_per_second": fps},
        "band": {"onset": _synthetic(bar_seconds, fps, bars, 0.0, structured=True),
                 "frames_per_second": fps},
    }
    try:
        inference.align(analysis, "vocal", ["band"], vocal_start=0.0, band_start=0.0,
                        duration=bars * bar_seconds - 1.0, bar_seconds=bar_seconds,
                        beats_per_bar=4.0)
    except inference.InferenceError as error:
        assert "below the stated" in str(error)
        assert "may not be resolved by argmax" in str(error)
        return
    raise AssertionError("a vocal with no bar-phase structure was placed anyway")
