"""Gates for the adjudicated A1-07 recovery result.

A losing attempt is the easiest thing in this repository to quietly improve. The failure
modes are all forms of the same move — letting the outcome drift toward the one that would
have been nicer:

The verdict softens. `control_wins` becomes "inconclusive", or the receipt stops saying which
way it went. So the recorded verdict has to be the one the sealed ledger holds, and a loss has
to keep `system_reference_completed` false.

The comparison happens too early. Measuring the candidate against the gold before a verdict
exists lets the answer leak backwards into the next attempt. The receipt has to assert the
order, and the order is the reason the comparison lives in its own object.

The blind leaks on the way out. Publishing which letter carried which object burns the pack
for any future use, and the mapping was never the repository's to hold.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-result-v1.public.json"
ATTEMPT = ROOT / "proofs" / "album_one" / "a1-07-inference-v1.public.json"

TERMINAL = {"control_wins", "tie_terminates_lineage", "reject_all_terminates_lineage"}


def test_the_recorded_verdict_is_the_one_that_was_sealed():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    assert receipt["verdict"] in TERMINAL | {"candidate_beats_control", "abstain"}
    assert receipt["candidate_beat_control"] == (receipt["verdict"] == "candidate_beats_control")
    for field in ("ledger_sha256", "submission_sha256", "assignment_sha256"):
        assert len(receipt[field]) == 64

    # The attempt it adjudicates is the one that was actually run.
    assert receipt["assignment_sha256"] == load_sealed(ATTEMPT)["review"]["assignment_sha256"]


def test_a_loss_stays_a_loss():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    if receipt["verdict"] in TERMINAL:
        assert receipt["candidate_beat_control"] is False
        assert authority["system_reference_completed"] is False
        assert authority["candidate_lineage"] == "terminated"
    assert authority["moves_album_counter"] is False
    assert authority["rights_or_release_permission"] is False


def test_losing_the_recovery_does_not_unmake_the_album_master():
    """Two different claims. The accepted master was never what this was testing."""
    authority = load_sealed(RECEIPT)["authority"]
    assert authority["track_still_accepted_as_album_master"] is True
    assert authority["challenge_still_open"] is True


def test_the_gold_was_only_opened_after_a_verdict_existed():
    comparison = load_sealed(RECEIPT)["gold_comparison"]
    assert comparison["performed_after_verdict"] is True
    assert "before submission" in comparison["permitted_because"]

    # And the attempt itself must still record that it never consulted the gold.
    attempt = load_sealed(ATTEMPT)
    assert attempt["gold_score_consulted"] is False
    assert attempt["authority"]["gold_similarity_measured"] is False


def test_the_comparison_says_what_was_wrong_rather_than_only_that_it_lost():
    comparison = load_sealed(RECEIPT)["gold_comparison"]
    assert comparison["findings"], "a loss with no findings teaches the next attempt nothing"
    assert {"pitch", "balance", "granularity"} & {f["decision"] for f in comparison["findings"]}
    for finding in comparison["findings"]:
        assert finding["assessment"] in {"near", "wrong", "coarser", "finer"}
        assert finding["source_id"]


def test_the_blind_mapping_does_not_leave_with_the_result():
    receipt = load_sealed(RECEIPT)
    assert receipt["boundary"]["option_map_exported"] is False
    assert "withheld" in receipt["which_letter_carried_which_object"]

    # No stray letter-to-object statement anywhere in the receipt.
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {"option_map", "semantic_choice"}
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(receipt)


def test_the_owner_was_not_given_scores_they_did_not_give():
    receipt = load_sealed(RECEIPT)
    assert receipt["numeric_dimension_scores_returned"] is False
    assert receipt["owner_notes"], "the verdict was qualitative; the words are the record"


def test_the_owner_note_addendum_sits_beside_the_adjudication_not_inside_it():
    """Additive by construction: it references the ledger and disclaims every power it lacks.

    The risk with a post-verdict note is that it becomes a second verdict by accident -- read
    later as though the owner revised something. So it carries the ledger's own digest, and it
    says in its own fields that it changes neither the adjudication nor the dimensions and
    authorizes nothing.
    """
    receipt = load_sealed(RECEIPT)
    addendum = receipt.get("owner_note_addendum")
    if addendum is None:
        return

    assert addendum["kind"] == "earcrate_reference_zero_owner_note_addendum"
    assert addendum["review_ledger_sha256"] == receipt["ledger_sha256"]
    assert addendum["changes_adjudication"] is False
    assert addendum["changes_dimensions"] is False
    assert addendum["authorizes_new_attempt"] is False
    assert addendum["revealed_role"] in {"candidate", "control"}
    assert addendum["owner_note"].strip()

    # A note cannot flip the outcome it comments on.
    assert receipt["candidate_beat_control"] == (addendum["revealed_role"] == "candidate"
                                                 and receipt["verdict"] == "candidate_beats_control")
    # And it still may not resurrect dimension scores nobody gave.
    assert receipt["numeric_dimension_scores_returned"] is False


def test_machine_findings_are_not_dressed_up_as_owner_words():
    """The comparison is analysis. Attributing it to the owner would manufacture authority."""
    receipt = load_sealed(RECEIPT)
    attribution = receipt["attribution"]
    assert "gold_comparison" in attribution["machine_analysis"]
    assert "gold_comparison" not in attribution["owner_words"]
    assert "owner_notes" in attribution["owner_words"]
    assert "not attributable to the owner" in attribution["note"]

    # Nothing in the comparison may claim to be a score somebody returned.
    for finding in receipt["gold_comparison"]["findings"]:
        assert "owner" not in json_keys(finding)


def json_keys(node, seen=None):
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        for key, value in node.items():
            seen.add(str(key))
            json_keys(value, seen)
    elif isinstance(node, list):
        for value in node:
            json_keys(value, seen)
    return " ".join(sorted(seen))
