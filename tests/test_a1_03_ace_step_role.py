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
    assert receipt["audition"]["tie_counts_as_the_incumbent_winning"] is True
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
    audition = load_sealed(RECEIPT)["audition"]
    assert audition["cuts"] == 2
    assert audition["assignment_map_withheld"] is True
    assert len(audition["assignment_sealed_sha256"]) == 64
    assert "forced by the audio" in audition["assignment_derivation"]
    assert audition["bed_gain_db_under_the_comp"] < 0.0
