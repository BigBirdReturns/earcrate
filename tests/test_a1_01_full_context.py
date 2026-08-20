"""Gates for the A1-01 full-length recurrence pack.

The whole point of this branch is that a 31-second excerpt was standing in for a
276-second work. So these gates hold two things: the source is bound to a stated
identity rather than to whatever file happened to be nearby, and the edit really is
confined to the span it claims -- because "one recurrence substitution and nothing else"
is the entire source-only contract, and it is invisible in the audio if it quietly fails.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-01-full-context-pack-v1.public.json"

HISTORICAL_WITNESS_PCM = ("5da1bef8526576ca49628de636337e8fe"
                          "9e100b4e0da7ada0605d164a4298e59")
SOURCE_CONTAINER = "af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979"
SOURCE_PCM = "bb7fede642c57eb155c4d784c36883abfeea0e20b2ab4d551e915cd8d74de832"


def test_the_source_is_bound_to_the_identity_that_was_asked_for():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    binding = receipt["source_binding"]
    assert binding["found"] is True
    assert binding["container_sha256"] == SOURCE_CONTAINER
    assert binding["container_bytes"] == 9_745_454
    assert binding["decoded_pcm_sha256"] == SOURCE_PCM
    assert binding["decoded_frames"] == 13_266_582
    assert binding["exact_container"] is True
    assert binding["binding_kind"] == "exact_container"

    # An exact container means no dependent artifact had to be regenerated. If that ever
    # flips to a rebind, the receipt has to stop claiming it did not.
    assert binding["rebind_required"] is False
    assert binding["path_recorded_in_repository"] is False


def test_the_retained_witness_rebuilds_from_the_bound_source():
    """If the found file could not reproduce history, it would not be the right file."""
    receipt = load_sealed(RECEIPT)
    witness = receipt["witness_reproduction"]
    assert witness["decoded_pcm_sha256"] == HISTORICAL_WITNESS_PCM
    assert witness["matches_historical"] is True
    assert witness["frames"] == 1_500_528

    # The JSON record digest does differ, and saying only "reproduces" would hide that.
    assert witness["record_digest_differs"] is True
    assert "the audio is the claim" in witness["record_digest_note"]


def test_the_excerpt_is_kept_as_a_diagnostic_and_refused_as_a_candidate():
    receipt = load_sealed(RECEIPT)
    why = receipt["why_the_excerpt_is_not_the_review"]
    assert why["excerpt_seconds"] < why["work_seconds"] / 8
    assert "functions as a track" in why["excerpt_cannot_answer"]

    pack = receipt["owner_pack"]
    assert pack["diagnostic_not_candidate"] == "the retained 31-second excerpt"
    assert why["member_name"] == "EDIT_WINDOW"
    assert pack["blind"] == "which letter carries the edit"
    assert pack["assignment_map_withheld"] is True
    assert set(pack["admissible_outcomes"]) == {"WIN", "LOSE", "TIE"}
    assert "A1-03" in pack["on_loss_or_tie"]
    assert "rights" in pack["on_win"], "a win must not be read as a rights decision"


def test_the_edit_is_confined_to_the_span_it_claims():
    receipt = load_sealed(RECEIPT)
    edit = receipt["edit"]

    assert edit["samples_altered_outside_target_span"] == 0
    assert edit["duration_preserved"] is True
    assert edit["joins_inside_replaced_span"] is True
    assert edit["joins"] == ["entry", "exit"], "a replacement needs a join at both ends"
    assert edit["crossfade_ms"] == 35.0
    assert edit["join_law"].startswith("equal power")

    target = edit["target_seconds"]
    donor = edit["donor_seconds"]
    assert round(target[1] - target[0], 6) == round(donor[1] - donor[0], 6), \
        "a replacement requires spans of equal length"
    assert edit["altered_sample_count"] == edit["replaced_frames"]

    # No processing on either side, or the comparison stops being about the edit.
    assert edit["normalisation_applied"] is False
    assert edit["prohibited_operations_performed"] == []
    assert receipt["source_only_contract"]["preserved"] is True
    assert receipt["source_only_contract"]["performed"] == []
    for banned in ("beat chopping", "stem layering", "synthesis", "MIDI overlay"):
        assert banned in edit["prohibited_operations"]


def test_the_pack_does_not_move_album_authority_or_decide_rights():
    receipt = load_sealed(RECEIPT)
    state = receipt["state"]
    assert state["album_authority_changed"] is False
    assert state["album_one_accepted_masters"] == "1/7"
    assert state["a1_01_album_master"] == "unaccepted"
    assert state["release_allowed"] is False
    assert "not asked by this pack" in state["rights_eligibility"]
    assert receipt["boundary"]["source_audio_remains_local"] is True
    assert receipt["boundary"]["private_paths_included"] is False


# --- the construction itself ---------------------------------------------------------

def test_a_replacement_edit_touches_nothing_outside_its_span():
    """The property the receipt asserts, exercised on signal the test owns.

    Reproduces the builder's construction on a synthetic source so the invariant is
    checked rather than trusted: every sample outside the replaced span survives, the
    duration is unchanged, and both joins land inside the span.
    """
    from scripts.earcrate_a1_01_full_context_v1 import CROSSFADE_MS, SAMPLE_RATE

    rng = np.random.default_rng(11)
    source = rng.standard_normal((SAMPLE_RATE * 4, 2)) * 0.1
    entry, leave = SAMPLE_RATE, SAMPLE_RATE * 2
    donor_start, donor_end = SAMPLE_RATE * 2, SAMPLE_RATE * 3

    donor = source[donor_start:donor_end]
    out = source.copy()
    out[entry:leave] = donor
    frames = round(CROSSFADE_MS * SAMPLE_RATE / 1000.0)
    phase = np.arange(frames, dtype=np.float64) / frames
    fade_out = np.cos(phase * np.pi / 2.0)[:, None]
    fade_in = np.sin(phase * np.pi / 2.0)[:, None]
    out[entry:entry + frames] = source[entry:entry + frames] * fade_out + donor[:frames] * fade_in
    out[leave - frames:leave] = donor[-frames:] * fade_out + source[leave - frames:leave] * fade_in

    assert len(out) == len(source)
    assert np.array_equal(out[:entry], source[:entry])
    assert np.array_equal(out[leave:], source[leave:])
    assert not np.array_equal(out[entry:leave], source[entry:leave]), "nothing was replaced"

    # Equal power: the two join curves sum in quadrature to unity.
    assert np.allclose(fade_out[:, 0] ** 2 + fade_in[:, 0] ** 2, 1.0)


def test_spans_of_unequal_length_are_refused_rather_than_stretched():
    from scripts.earcrate_a1_01_full_context_v1 import SourceError, edit

    import scripts.earcrate_a1_01_full_context_v1 as builder

    original = builder.DONOR_SECONDS
    try:
        builder.DONOR_SECONDS = (255.146667, 260.0)  # shorter than the target span
        with pytest.raises(SourceError) as caught:
            edit(np.zeros((builder.SAMPLE_RATE * 300, 2)))
        assert "not a replacement" in str(caught.value)
    finally:
        builder.DONOR_SECONDS = original


def test_a_source_whose_audio_is_wrong_is_refused(tmp_path):
    """Binding is on identity, not on filename or plausibility."""
    import soundfile as sf

    from scripts.earcrate_a1_01_full_context_v1 import SourceError, decode

    wrong = tmp_path / "(HQ) Pretty Lights - Empire State Of Mind Remix.wav"
    sf.write(str(wrong), np.zeros((48_000, 2), dtype=np.float32), 48_000, subtype="PCM_24")
    with pytest.raises(SourceError) as caught:
        decode(wrong)
    assert "decoded PCM is" in str(caught.value)


def test_the_binding_does_not_rest_on_one_surviving_file():
    """Custody is stronger when the identity is redundantly present, and weaker if it is not."""
    receipt = load_sealed(RECEIPT)
    redundancy = receipt["source_binding"]["redundancy"]
    assert redundancy["byte_identical_copies_found"] >= 2
    assert redundancy["all_match_expected_container"] is True
    assert redundancy["paths_recorded_in_repository"] is False
    # Different filenames, identical bytes: the identity is the digest, never the name.
    assert redundancy["distinct_filenames"] >= 2
    assert len(redundancy["locations_kind"]) == redundancy["byte_identical_copies_found"]


def test_the_pack_says_where_the_difference_is_and_how_big_it_is():
    """A comparison the owner cannot locate is not a review, it is a scavenger hunt."""
    receipt = load_sealed(RECEIPT)
    extent = receipt["owner_pack"]["difference_extent"]
    edit = receipt["edit"]

    assert extent["differs_from_seconds"] == round(edit["target_seconds"][0], 3)
    assert extent["differs_to_seconds"] == round(edit["target_seconds"][1], 3)
    assert extent["identical_elsewhere"] is True
    assert extent["fraction_of_file_differing"] < 0.05
    assert receipt["owner_pack"]["focused_excerpts_provided"] is True


def test_the_blind_declares_its_own_weakness():
    """A blind that leaks and says so is usable; one that leaks silently corrupts the verdict."""
    receipt = load_sealed(RECEIPT)
    blind = receipt["owner_pack"]["blind_strength"]
    assert blind["sealed"] is True
    assert blind["robust"] is False, "this blind leaks and the receipt must not claim otherwise"
    assert "returns at 4:15" in blind["leak"]
    assert blind["affects_verdict_validity"] is False


def test_the_builder_emits_every_member_the_receipt_promises():
    """A receipt that names pack members the builder cannot produce is a promise, not a fact.

    The delivered pack carries A_FOCUS, B_FOCUS and DONOR_SOURCE, and the receipt lists them.
    The builder did not make them, so the pack could not be rebuilt from the bound source --
    which is the whole basis on which anything else in this lane is trusted.
    """
    import scripts.earcrate_a1_01_full_context_v1 as builder

    receipt = load_sealed(RECEIPT)
    promised = set(receipt["owner_pack"]["members"])
    assert set(builder.PACK_MEMBERS) == promised, (
        f"builder emits {sorted(builder.PACK_MEMBERS)}, receipt promises {sorted(promised)}")


def test_the_focus_pair_actually_contains_the_edit():
    """A focus cut that misses the difference sends the owner to listen at nothing."""
    import scripts.earcrate_a1_01_full_context_v1 as builder

    focus_start, focus_stop = builder.FOCUS_SECONDS
    edit_start, edit_stop = builder.TARGET_SECONDS
    assert focus_start < edit_start and focus_stop > edit_stop

    # And run-up and landing on both sides, not the edit jammed against an edge.
    assert edit_start - focus_start >= 15.0
    assert focus_stop - edit_stop >= 15.0

    # The donor context is where the inserted material lives in the untouched reading.
    donor_start, donor_stop = builder.DONOR_CONTEXT_SECONDS
    source_start, source_stop = builder.DONOR_SECONDS
    assert donor_start <= source_start and donor_stop >= source_stop


def test_a_losing_verdict_is_not_routed_into_a_closed_lane():
    """A review sheet that names where the work goes next has to be re-read whenever a lane
    closes. A1-03's chart-driven realization closed after this pack was written, and a sheet
    still pointing there would spend a verdict on a lane that cannot receive it."""
    source = (ROOT / "scripts" / "earcrate_a1_01_full_context_v1.py").read_text(
        encoding="utf-8")
    outcomes = source.split("ADMISSIBLE OUTCOMES", 1)[1].split("WHAT WAS AND WAS NOT DONE", 1)[0]
    flat = " ".join(outcomes.split())
    assert "move Album One to A1-03" not in flat, (
        "the sheet routes a losing verdict to A1-03, whose realization is closed")
    assert "A1-03's chart-driven realization is closed" in flat
    # A1-04 and A1-05 have no local recordings, so naming them as the next lane would be
    # routing a verdict at work the estate cannot start either.
    assert "A1-04 and A1-05 cannot be bound" in flat
    assert "obtaining them is an owner action" in flat
    assert "A1-07 system reference" in flat
    # The winning branch still has to say what it changes, or the review is inadmissible.
    assert "proceed to mastering and A1-01 acceptance" in flat
