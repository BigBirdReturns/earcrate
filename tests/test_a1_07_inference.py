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


def test_a_lock_that_is_not_a_lock_is_not_used_to_place_anything():
    alignment = load_sealed(RECEIPT)["decisions"]["alignment"]
    assert ("lock_is_real" in alignment) and ("decided_by" in alignment)
    if not alignment["lock_is_real"]:
        assert alignment["lock_correlation"] < alignment["minimum_lock"]
        assert "quantization" in alignment["decided_by"]
    else:
        assert alignment["lock_correlation"] >= alignment["minimum_lock"]
        assert alignment["decided_by"] == "onset lock"


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
