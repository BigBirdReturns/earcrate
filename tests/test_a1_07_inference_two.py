"""Gates for A1-07 attempt two, which refused.

A refusal is the easiest result to lose. Three ways it goes wrong here:

It reads as a crash. "The attempt stopped" and "the attempt was never possible" are different
findings, and only the measurement separates them, so the receipt has to carry the
measurement and not just the stop.

It quietly retires the challenge. Attempt two answers nothing, so nothing about the issued
challenge changes -- and the digest it reused has to be the one that was issued.

It grows into a program. The finding names the no-stretch prior as the next decision. Naming
is not authorizing, and a negative result does not buy a revision of a different prior.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-two-v1.public.json"
CHALLENGE = ROOT / "proofs" / "album_one" / "a1-07-recovery-challenge-v1.public.json"


def test_the_refusal_is_recorded_as_a_result():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []
    assert receipt["attempt"] == 2

    placement = receipt["placement"]
    assert placement["refused"] is True
    assert placement["fallback_available"] is False
    assert placement["criterion"] == "bar-phase accent agreement across the whole window"
    assert "may not be resolved by argmax" in placement["message"]


def test_the_refusal_carries_the_measurement_that_caused_it():
    """Without this, the record cannot tell a bad criterion from an impossible placement."""
    spread = load_sealed(RECEIPT)["why_it_refused"]
    assert spread["a_constant_phase_exists"] is False
    assert spread["vocal_tempo_bpm"] != spread["band_tempo_bpm"]
    assert spread["slip_in_bars"] > 0.25, (
        "a refusal blamed on tempo divergence has to show the divergence")
    assert spread["slip_seconds_across_the_window"] > spread["band_bar_seconds"] / 4.0
    assert "nothing is stretched" in spread["why"]


def test_the_issued_challenge_is_the_one_that_was_reused():
    receipt = load_sealed(RECEIPT)
    issued = load_sealed(CHALLENGE)["challenge"]["challenge_sha256"]
    assert receipt["challenge_sha256"] == issued, (
        "attempt two answered a challenge other than the one issued")
    assert receipt["challenge_reused_not_reissued"] is True
    assert receipt["authority"]["challenge_retired"] is False
    assert receipt["authority"]["challenge_still_open"] is True


def test_a_refusal_produces_no_candidate_and_no_owner_task():
    authority = load_sealed(RECEIPT)["authority"]
    assert authority["candidate_produced"] is False
    assert authority["candidate_beat_control"] is False
    assert authority["owner_pack_built"] is False
    assert authority["owner_review_pending"] is False
    assert authority["system_reference_completed"] is False
    assert authority["moves_album_counter"] is False
    assert authority["album_master_accepted"] is True


def test_naming_the_next_decision_is_not_authorizing_it():
    named = load_sealed(RECEIPT)["named_next_decision"]
    assert "no-stretch prior" in named["decision"]
    assert named["authorized_now"] is False
    assert "does not authorize" in named["why_not"]


def test_the_answer_was_still_not_opened():
    boundary = load_sealed(RECEIPT)["boundary"]
    assert boundary["gold_score_consulted"] is False
    assert boundary["private_paths_included"] is False
    body = RECEIPT.read_text(encoding="utf-8")
    for leak in ("core-stems", ".flac", "sessions"):
        assert leak not in body, f"the public receipt leaks {leak!r}"
