"""Gates for the ACE-Step bounded role on A1-03.

A generative provider earns a place in an album lane by filling a named gap, once, against an
incumbent allowed to win. The ways that quietly stops being true:

The role widens. One request becomes a search, one seed becomes a sweep, and the receipt
still says "bounded". So the generation count and the seed are asserted, and the prompt is
required to have been sealed before the provider was contacted.

The incumbent stops being able to win. If a tie ever counts as the bed winning, a generated
layer gets added for free, and every later track inherits that.

The conditioning quietly comes from somewhere better than the recovery. The bed was told a
tempo and a key; both came from the blind chart, and whether the provider actually delivered
them is measured rather than assumed.

And the one that actually happened: the probe presents itself as an owner review. Two cuts of
the same reduction, one of them carrying a bed that never saw the recording, cannot select a
track candidate or accept a master, so no verdict on them changes a track-level authority
state. The pack is machine diagnostic evidence and carries a disposition, not a question.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-ace-step-role-v1.public.json"
REALIZATION = ROOT / "proofs" / "album_one" / "a1-03-realization-v1.public.json"


def test_the_role_stayed_one_role():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    role = receipt["role"]
    assert role["role"] == "replacement_instrumentation"
    assert role["generations"] == 1, "one bounded role is one generation, not a search"
    assert isinstance(role["seed"], int)
    assert role["is_a_benchmark"] is False
    assert role["is_a_provider_census"] is False
    assert role["request_sealed_before_execution"] is True
    assert len(role["request_sha256"]) == 64


def test_the_incumbent_can_still_win():
    receipt = load_sealed(RECEIPT)
    assert receipt["role"]["incumbent_may_win"] is True
    assert receipt["probe"]["tie_counts_as_the_incumbent_winning"] is True
    assert "no bed at all" in receipt["role"]["incumbent"]
    assert "closes" in receipt["on_loss"]


def test_the_window_is_bounded_by_a_measurement_not_a_preference():
    """Sixteen bars because a constant grid holds there, not because it is a round number."""
    receipt = load_sealed(RECEIPT)
    why = receipt["why_sixteen_bars"]
    assert why["max_constant_grid_departure_seconds"] <= why["bound_seconds"]
    assert receipt["window"]["bars"] == 16
    assert receipt["window"]["max_constant_grid_departure_seconds"] == \
        why["max_constant_grid_departure_seconds"]


def test_the_conditioning_came_from_the_recovery():
    receipt = load_sealed(RECEIPT)
    assert receipt["role"]["conditioning_source"].endswith("not the witness")
    key = receipt["key"]
    assert key["witness_consulted"] is False
    assert key["derived_from"].startswith("the recovered chords")

    # The realization the bed sits under is the same recovered chart.
    realization = load_sealed(REALIZATION)
    assert receipt["window"]["tempo_bpm"] > 0
    assert realization["recovered_chart"]["bar_count"] >= receipt["window"]["bars"]


def test_whether_the_provider_delivered_is_measured_not_assumed():
    measured = load_sealed(RECEIPT)["bed_measurement"]
    assert measured["requested_bpm"] > 0
    assert measured["measured_bpm"] > 0
    assert measured["tempo_error_percent"] == round(
        min(abs(measured["measured_bpm"] * factor - measured["requested_bpm"])
            / measured["requested_bpm"] * 100.0 for factor in (1.0, 2.0, 0.5)), 3)
    # The key it landed on is recorded whether or not it matches what was asked for.
    assert measured["measured_key_top"]


def test_a_generation_is_not_an_adoption():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    assert authority["provider_adopted"] is False
    assert authority["generation_is_not_acceptance"] is True
    assert authority["owner_audition_performed"] is False
    assert authority["album_master_accepted"] is False
    assert authority["moves_album_counter"] is False

    boundary = receipt["boundary"]
    assert boundary["prompt_text_exported"] is False
    assert boundary["source_audio_exported"] is False
    assert boundary["private_paths_included"] is False


def test_the_blind_is_sealed_before_a_verdict_can_choose_it():
    probe = load_sealed(RECEIPT)["probe"]
    assert probe["cuts"] == 2
    assert probe["assignment_map_withheld"] is True
    assert len(probe["assignment_sealed_sha256"]) == 64
    assert "forced by the audio" in probe["assignment_derivation"]
    assert probe["bed_gain_db_under_the_comp"] < 0.0


def test_the_probe_never_becomes_an_owner_review():
    """The exact failure this pack was rebuilt to stop: a probe wearing a review's clothes."""
    receipt = load_sealed(RECEIPT)
    assert receipt["artifact_class"] == "provider_role_probe"

    disposition = receipt["disposition"]
    assert disposition["artifact_class"] == "provider_role_probe"
    assert disposition["owner_review_required"] is False
    assert disposition["owner_review_pending"] is False
    assert disposition["owner_action"] == "none"
    assert disposition["album_authority_changed"] is False
    assert "Owner review admission" in disposition["rule"]

    # Both directions stay open. A probe that cannot move the track cannot adopt a provider,
    # and it cannot condemn one either.
    assert disposition["ace_step_adopted"] is False
    assert disposition["ace_step_rejected_globally"] is False
    assert disposition["bounded_role_qualified"] == "not_established"

    authority = receipt["authority"]
    assert authority["owner_review_pending"] is False
    assert authority["provider_adopted"] is False
    assert authority["provider_rejected_globally"] is False


def test_what_survives_the_probe_is_named():
    """A negative disposition on the pack is not a negative disposition on the work."""
    disposition = load_sealed(RECEIPT)["disposition"]
    assert disposition["corrected_chart_retained"] is True
    assert disposition["one_generation_receipt_retained"] is True
    assert "diagnostic evidence" in disposition["evidence_status"]


def test_the_pack_ships_a_disposition_and_not_a_question():
    source = (ROOT / "scripts" / "earcrate_a1_03_ace_step_role_v1.py").read_text(encoding="utf-8")
    assert 'pack / "DISPOSITION.txt"' in source
    assert 'pack / "REVIEW.txt"' not in source, "the pack asks for an owner verdict again"
    assert "NO OWNER VERDICT IS OWED" in source
