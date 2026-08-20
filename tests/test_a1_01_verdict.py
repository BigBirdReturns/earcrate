"""Gates for the A1-01 verdict, and for the inference that produced a false claim.

"I cannot tell the difference" is the one verdict a pack can receive that might mean the pack
is broken rather than the edit. So the order matters: the artifact was measured before the
verdict was accepted, and the receipt has to carry that measurement or a later reader cannot
tell a delivered comparison from a failed one.

The defect it exposed is the reusable part. The lane argued audibility from waveform
correlation, which cannot support it -- two bars of the same loop-based section are
uncorrelated and sound identical. A gate holds that finding so the next pack does not repeat
the claim.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-01-verdict-v1.public.json"
PACK = ROOT / "proofs" / "album_one" / "a1-01-full-context-pack-v1.public.json"
BUILDER = ROOT / "scripts" / "earcrate_a1_01_full_context_v1.py"


def test_the_artifact_was_verified_before_the_verdict_was_accepted():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    checked = receipt["the_artifact_was_checked_first"]
    assert checked["focus_cuts_differ"] is True
    assert checked["differing_span_seconds"] == checked["expected_span_seconds"], (
        "the cuts differ somewhere other than where the edit is")
    assert checked["differing_sample_fraction"] > 0.0
    assert "delivered exactly what it claimed" in checked["conclusion"]


def test_the_verdict_is_recorded_as_a_result():
    verdict = load_sealed(RECEIPT)["verdict"]
    assert verdict["authority"] == "owner"
    assert verdict["outcome"] == "TIE"
    assert verdict["selected"] == "neither"
    assert verdict["failure_primary"] == "inaudible_edit"
    assert verdict["credited"] == []
    assert "cannot be heard has not earned itself" in verdict["decisive"]


def test_the_inaudibility_is_measured_and_not_asserted():
    why = load_sealed(RECEIPT)["why_it_is_inaudible"]
    assert why["mfcc_cosine"] >= 0.99
    assert why["chroma_cosine"] >= 0.99
    lo, hi = why["spectral_centroid_hz"]
    assert abs(lo - hi) / max(lo, hi) < 0.01
    assert why["tempo_bpm"][0] == why["tempo_bpm"][1]
    assert "after the verdict" in why["measured"]


def test_correlation_is_never_argued_as_audibility_again():
    """The claim that lost this pack its verdict, refused by name."""
    defect = load_sealed(RECEIPT)["the_measurement_defect_this_exposes"]
    assert "waveform correlation" in defect["defect"]
    assert "correlation measures sample alignment" in defect["why_it_is_wrong"]
    assert "perceptual comparison" in defect["what_would_have_caught_it"]

    # The builder may still report correlation. It may not call it obvious.
    source = BUILDER.read_text(encoding="utf-8")
    outcomes = " ".join(source.split())
    assert "and it is obvious" not in outcomes, (
        "the review sheet still argues audibility from a correlation number")


def test_the_loss_stays_the_size_it_is():
    receipt = load_sealed(RECEIPT)
    disposition = receipt["disposition"]
    assert disposition["retained_edit"] == "closed"
    assert disposition["a1_01_album_master"] == "unaccepted"
    assert disposition["mastering_authorized"] is False
    assert disposition["album_one_accepted_masters"] == "1/7"
    assert disposition["moves_album_counter"] is False
    assert disposition["next_owner_facing_action"] == "none"

    # The edit's confinement is not what failed, and the receipt says so.
    assert "not in how the edit was cut" in receipt[
        "the_measurement_defect_this_exposes"]["scope"]


def test_an_empty_board_is_recorded_and_not_acted_on():
    state = load_sealed(RECEIPT)["album_state_after_this_verdict"]
    assert state["executable_lanes_remaining"] == 0
    assert "not as an argument for authorizing anything" in state["note"]
    assert load_sealed(RECEIPT)["new_organs_added"] == 0


def test_the_verdict_names_the_pack_it_judged():
    assert load_sealed(RECEIPT)["pack_verified_against"] == load_sealed(PACK)["receipt_sha256"]
