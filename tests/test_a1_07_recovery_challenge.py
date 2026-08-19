"""Gates for the A1-07 withheld-answer recovery challenge.

A1-07's album claim is closed and its autonomy claim is not, and the only thing keeping
those apart is that the accepted decisions stay withheld. So the failure this file guards
against is not a bad candidate. It is a challenge that quietly stops being a challenge:
clip decisions leaking into a public object, a control weak enough that beating it means
nothing, or an acceptance rule loosened until least-bad passes.

The one about the control is worth stating plainly. A naive control is not a bad control.
It gets every decision derivable from the challenge itself -- alignment, level -- and none
that requires the answer. If a future edit lets the control keep an arrangement decision,
or lets it hold the previously reviewed compound, a candidate victory would prove nothing
and the receipt would still read as a pass.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-recovery-challenge-v1.public.json"

CHALLENGE = "9858fe895dd275afdebc01beced7a931483fa8e85447f9c6e02835fb96b1f69e"
GOLD_SCORE = "8cbec0b72cd417d656fc1e085ae9e426b0c91e3360ea050c5afd14f655260b7c"
GOLD_RENDER_PCM = "61e20e832b98e606b241d8e91bddaa4c01a7fbfbb02b77bddc86aff1c913da58"
CONTROL_SCORE = "80515df5be48468a840e839aed37b81897ea5e2a8a259b9c560b6c53ca3426b8"

ARRANGEMENT_DECISIONS = frozenset({
    "section mapping", "progressive entry", "bar-level placement", "pitch shift",
    "tempo scaling", "phrase placement", "per-section gain",
})


def test_the_challenge_is_issued_and_pinned():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    challenge = receipt["challenge"]
    assert challenge["challenge_sha256"] == CHALLENGE
    assert challenge["gold_score_sha256"] == GOLD_SCORE
    assert challenge["gold_render_pcm_commitment"] == GOLD_RENDER_PCM
    assert challenge["control_score_sha256"] == CONTROL_SCORE
    assert challenge["source_count"] == 5
    assert challenge["timeline"]["duration_samples"] == 2_693_328


def test_the_answer_key_is_withheld():
    """The whole claim rests on this. A published decision is a published answer."""
    receipt = load_sealed(RECEIPT)
    assert receipt["challenge"]["clip_decisions_published"] is False
    assert receipt["boundary"]["clip_decisions_exported"] is False

    # Nothing anywhere in the public receipt may carry clip-shaped material.
    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {"tracks", "clips", "clip_id", "source_start_sample",
                                   "target_start_sample", "tempo_scale"}, \
                    f"clip decision leaked at {path}.{key}"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(receipt)


def test_the_control_is_naive_but_not_a_straw_man():
    control = load_sealed(RECEIPT)["control"]
    assert control["kind"] == "naive_co_play"
    assert control["every_decision_derivable_without_the_gold"] is True

    # It takes the cheap wins that need no answer ...
    taken = " ".join(control["decisions_taken"]).lower()
    assert "align" in taken
    assert "loudness" in taken

    # ... and refuses every decision the candidate is supposed to supply.
    assert ARRANGEMENT_DECISIONS <= set(control["decisions_refused"])
    assert not ARRANGEMENT_DECISIONS & set(control["decisions_taken"])


def test_the_control_does_not_hold_part_of_the_answer():
    control = load_sealed(RECEIPT)["control"]
    assert control["source_excluded"] == "gold_v6_reviewed_compound"
    assert "gold_v6_reviewed_compound" not in control["sources_used"]
    assert len(control["sources_used"]) == 4


def test_the_control_reproduces_or_it_is_not_a_control():
    receipt = load_sealed(RECEIPT)
    assert receipt["control_reproduces_identically"] is True
    assert len(receipt["control_canonical_pcm_sha256"]) == 64


def test_the_control_does_not_distort()  :
    """A control that clips is not a fair baseline.

    Four stems summed at unity overshoot full scale by 2.1 dB, and the renderer writes 24-bit
    PCM, so the overshoot becomes flat tops. If both options in a blind pair distort, the
    comparison is partly about which one distorts less, and the frontier is measuring the
    wrong thing.
    """
    headroom = load_sealed(RECEIPT)["control_headroom"]
    assert headroom["solved_from"] == "measured true peak, not chosen"
    assert headroom["boost_refused"] is True
    assert headroom["solved_master_gain_db"] <= 0.0
    assert headroom["measured_true_peak_dbtp"] + headroom["solved_master_gain_db"] <=         headroom["ceiling_dbtp"] + 0.01

    # The overshoot has to be measured somewhere it cannot already have been clamped.
    assert headroom["probe_gain_db"] < 0.0
    assert headroom["probe_true_peak_dbtp"] < -0.5, (
        "the probe render sat at full scale, so its own peak was clamped and the solve read "
        "an overshoot that had already been thrown away")


def test_the_gold_receipt_is_transcribed_and_not_invented():
    """Reference Zero needed the acceptance in its own schema. It did not need a new one."""
    transcription = load_sealed(RECEIPT)["gold_receipt_is_a_transcription"]
    assert transcription["transcribed"] is True
    assert transcription["invented"] is False
    assert transcription["monitoring_accepted_production_render"] is True
    assert transcription["master_cut_from_this_render"] is True
    assert transcription["void_if_owner_disputes_the_transcription"] is True
    assert len(transcription["sealed_blind_verdict_sha256"]) == 64


def test_the_acceptance_rule_still_refuses_least_bad():
    acceptance = load_sealed(RECEIPT)["challenge"]["acceptance"]
    assert acceptance["candidate_must_blindly_beat_naive_control"] is True
    assert acceptance["least_bad_does_not_pass"] is True
    assert acceptance["tie_terminates_lineage"] is True
    assert acceptance["reject_all_terminates_lineage"] is True
    assert acceptance["gold_similarity_is_evaluated_only_after_candidate_submission"] is True


def test_issuing_a_challenge_does_not_complete_a_reference():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    assert authority["album_master_accepted"] is True
    assert authority["system_reference_completed"] is False
    assert authority["inference_attempted"] is False
    assert authority["candidate_beat_control"] is False
    assert authority["rights_or_release_permission"] is False

    boundary = receipt["boundary"]
    assert boundary["private_paths_included"] is False
    assert boundary["source_audio_exported"] is False
