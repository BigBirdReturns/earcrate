"""Gates for the A1-03 trio verdict.

A negative verdict is the easiest evidence to soften. The three ways it happens here:

The loss becomes a note. The receipt has to carry the outcome, what was decisive, and the two
digests of the objects that were actually judged, or a later reader gets a mood instead of a
result.

The loss grows. "The trio arrangement lost" is not "A1-03 is closed", is not "the crate is
condemned", and is not "a new architecture program is authorized". Each of those is asserted
as *not* following, because each has followed from a negative result in this repository before.

The loss gets credited. Listing machine achievements under a verdict that granted none would
report a different verdict than the one given.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-trio-verdict-v1.public.json"
TRIO = ROOT / "proofs" / "album_one" / "a1-03-trio-realization-v1.public.json"
ALBUM_ONE = ROOT / "ALBUM_ONE.md"


def test_the_verdict_is_recorded_as_a_result():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    verdict = receipt["verdict"]
    assert verdict["authority"] == "owner"
    assert verdict["answer"] == "no"
    assert verdict["outcome"] == "LOSE"
    assert verdict["selected"] == "neither"
    assert verdict["failure_primary"] == "harmony_realized_without_melody"
    assert "no melody" in verdict["decisive"]


def test_the_verdict_names_the_objects_it_judged():
    """A verdict that cannot say which audio it was given is not evidence."""
    verdict = load_sealed(RECEIPT)["verdict"]
    judged = verdict["judged_objects"]
    trio = load_sealed(TRIO)
    assert judged["candidate_pcm_sha256"] == trio["renders"]["pcm_sha256"]["candidate"]
    assert judged["control_pcm_sha256"] == trio["renders"]["pcm_sha256"]["control"]
    assert judged["candidate_pcm_sha256"] != judged["control_pcm_sha256"]
    assert verdict["trio_receipt_sha256"] == trio["receipt_sha256"]


def test_nothing_is_credited_that_the_owner_did_not_credit():
    verdict = load_sealed(RECEIPT)["verdict"]
    assert verdict["credited"] == [], (
        "the verdict granted no musical positive; listing one reports a different verdict")
    assert "misreport" in verdict["why_nothing_is_credited"]


def test_the_loss_stays_the_size_it_is():
    receipt = load_sealed(RECEIPT)
    disposition = receipt["disposition"]
    assert disposition["trio_candidate"] == "rejected"
    assert disposition["chart_driven_realization"] == "closed"
    assert disposition["a1_03_status"] == ("chart-driven realization closed; "
                                           "the track is not closed")
    assert disposition["album_one_accepted_masters"] == "1/7"
    assert disposition["moves_album_counter"] is False
    assert disposition["next_owner_facing_action_from_a1_03"] == "none"

    survives = receipt["what_this_does_not_close"]
    for key in ("the_track", "the_source_binding", "the_recovered_clock",
                "the_crate_rack_path", "ace_step"):
        assert survives[key], f"{key} lost its explicit survival"
    assert "not condemned" in survives["the_crate_rack_path"]


def test_a_negative_result_does_not_authorize_a_program():
    gap = load_sealed(RECEIPT)["named_gap_this_verdict_creates"]
    assert "requires the tune" in gap["gap"]
    assert gap["authorized_now"] is False
    assert "does not authorize an architecture program" in gap["why_not"]
    assert load_sealed(RECEIPT)["new_organs_added"] == 0


def test_the_album_ledger_carries_the_loss():
    text = ALBUM_ONE.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines()
               if line.startswith("| **A1-03** | Aphex Twin"))
    assert "lost" in row
    assert "realizes harmony and no melody" in row
    assert "chart-driven realization is closed" in row
