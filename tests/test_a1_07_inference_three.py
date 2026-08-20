"""Gates for A1-07 attempt three, which closed the inference family.

Closing a family is the largest negative result this repository can record, so the gates are
about whether it was earned. Three ways it would not be.

It closes on a number someone picked. The first floor here was a guess, and the guess would
have rejected a known-correct placement. A closure resting on that would be an artefact of the
guess, so the receipt has to carry the calibration that replaced it.

It closes without asking the criterion to prove itself. A criterion that cannot find a known
answer cannot refuse an unknown one. The self-test runs first and its result travels.

It closes more than it found. The album master is accepted and stays accepted; the track keeps
its commission; the challenge object was answered, not retired. Each is asserted.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-three-v1.public.json"
CHALLENGE = ROOT / "proofs" / "album_one" / "a1-07-recovery-challenge-v1.public.json"
SCRIPT = ROOT / "scripts" / "earcrate_a1_07_inference_three_v1.py"


def test_the_criterion_proved_itself_before_it_refused_anything():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []
    assert receipt["attempt"] == 3

    probe = receipt["criterion_self_test"]
    assert probe["passed"] is True, (
        "a criterion that cannot find a known answer may not be used to refuse an unknown one")
    assert len(probe["probes"]) >= 3
    for row in probe["probes"]:
        assert row["localized"] is True
        assert row["phase_error_seconds"] <= row["tolerance_seconds"]


def test_the_closure_rests_on_a_calibration_and_not_on_a_guess():
    """The whole difference between a finding and an artefact of a chosen threshold."""
    receipt = load_sealed(RECEIPT)
    calibration = receipt["margin_calibration"]
    assert calibration["usable"] is False
    assert calibration["guessed_floor_would_reject_ground_truth"] is True, (
        "if the guessed floor did not reject ground truth, the closure is resting on the guess")
    achieved = calibration["ground_truth_z_margins"]
    assert achieved and max(achieved) < calibration["guessed_floor"]
    assert "rejects known-correct placements" in calibration["reason"]
    assert "no margin floor separates" in receipt["closed_because"]


def test_the_criterion_is_not_blamed_for_something_it_did_not_do():
    calibration = load_sealed(RECEIPT)["margin_calibration"]
    # The field name carries the negation; the value states what is being ruled out.
    assert "the criterion is broken" in calibration["what_this_is_not"]
    assert "localizes exactly on known material" in calibration["what_this_is_not"]
    # It localized. What it cannot do is report discriminating confidence.
    assert all(row["phase_error_seconds"] < 0.1
               for row in load_sealed(RECEIPT)["criterion_self_test"]["probes"])


def test_the_method_obeyed_every_stated_prohibition():
    method = load_sealed(RECEIPT)["method"]
    assert method["no_stretch"] is True
    assert method["fallback_available"] is False
    assert method["clip_count_targeted"] is False
    assert method["replaces"] == "the continuous-vocal-window assumption"
    assert "own activity envelope" in method["phrases_are"]
    assert load_sealed(RECEIPT)["boundary"]["gold_score_consulted"] is False

    source = SCRIPT.read_text(encoding="utf-8")
    assert "performance-score.json" not in source, "the attempt can see the answer key"
    assert "tempo_scale" not in source or "1.0" in source


def test_the_family_closed_and_nothing_larger_did():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    assert authority["inference_family_closed"] is True
    assert authority["candidate_produced"] is False
    assert authority["system_reference_completed"] is False
    assert authority["moves_album_counter"] is False

    # The accepted master is not what failed here.
    assert authority["album_master_accepted"] is True
    assert authority["challenge_retired"] is False
    survives = receipt["what_closing_does_not_close"]
    for key in ("the_album_master", "the_track", "the_challenge_object"):
        assert survives[key]
    assert "not retired" in survives["the_challenge_object"]


def test_a_closure_still_asks_nothing_of_the_owner():
    authority = load_sealed(RECEIPT)["authority"]
    assert authority["owner_pack_built"] is False
    assert authority["owner_review_pending"] is False


def test_the_issued_challenge_is_the_one_that_was_answered():
    receipt = load_sealed(RECEIPT)
    issued = load_sealed(CHALLENGE)["challenge"]["challenge_sha256"]
    assert receipt["challenge_sha256"] == issued
    assert receipt["challenge_reused_not_reissued"] is True


def test_the_form_that_could_not_be_placed_is_recorded():
    form = load_sealed(RECEIPT)["form"]
    assert form["phrases_found"] > 1, (
        "a form of one phrase is the continuous block this attempt was built to replace")
    assert form["phrases_placed"] == 0
    assert load_sealed(RECEIPT)["tempo_divergence"]["a_constant_phase_exists"] is False
