"""Gates for the A1-03 source binding and its convergence test.

A1-03 spent this whole project with a symbolic account of a performance nobody here had
decoded. The lane's risk was never that the witness would be wrong; it was that the witness
would be *believed* -- silently promoted from a community transcription into an answer key
because it was the only symbolic object in the room.

So these gates hold three things. The binding names one exact object and does not claim the
edition authority it does not have. The convergence test is a test, which means it is
allowed to fail and is recorded as failing. And the receipt does not quietly move the album
counter on the strength of a file being found.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-source-binding-v1.public.json"
SPECIMEN = ROOT / "specimens" / "flim_bad_plus_v1.community-symbolic.json"

SOURCE_CONTAINER = "b87f5ca6588eab74f30e7b2b7afed0c236bec02a0b46868252a5e3c437788fa5"
SOURCE_CANONICAL_PCM = "0c45672ea556b3f5a4fea4ef45b67cd20da0a713504fb4c77245ddf9b5369249"


def test_the_performance_is_bound_to_one_exact_object():
    """Pinned, so that a later run against a different copy is a failure and not a drift."""
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    binding = receipt["source_binding"]
    assert binding["found"] is True
    assert binding["path_recorded_in_repository"] is False
    assert binding["container_sha256"] == SOURCE_CONTAINER
    assert binding["container_bytes"] == 4_071_424
    assert binding["canonical_pcm_sha256"] == SOURCE_CANONICAL_PCM
    assert binding["canonical_frames"] == 12_025_940
    assert binding["canonical_sample_rate"] == 48_000
    assert binding["canonical_channels"] == 2


def test_the_binding_does_not_claim_an_edition_it_cannot_prove():
    """The A1-02 lesson, applied before it can cost anything.

    A copy in hand is an exact object and is not a master edition. If this ever flips to
    True, something has claimed a pressing authority from a 130 kbit/s file.
    """
    binding = load_sealed(RECEIPT)["source_binding"]
    assert binding["edition_is_master_edition"] is False
    assert binding["edition_class"] in {"lossy_delivery_copy", "lossless_copy"}
    assert binding["binding_kind"] == "exact_object_bound_edition_unclaimed"
    if binding["edition_class"] == "lossy_delivery_copy":
        assert binding["delivery"]["codec_name"] in {"mp3", "aac", "vorbis", "opus", "wmav2"}


def test_the_witness_was_not_used_as_a_prior_for_the_measurement():
    """The order is the whole method. Seeded with 138, a tracker returns 138."""
    receipt = load_sealed(RECEIPT)
    method = receipt["method"]
    assert method["witness_values_used_as_analysis_priors"] is False
    assert method["analysis_parameters_fixed_before_comparison"] is True

    claimed = float(receipt["convergence"]["claims"]["tempo"]["claimed_bpm"])
    assert receipt["blind_measurement"]["beat_tracker_start_bpm"] != claimed


def test_the_convergence_gate_reports_its_result_rather_than_asserting_a_pass():
    """A gate that can only pass is not a gate.

    This asserts the shape and the arithmetic of the verdict, not its direction. The tempo
    claim currently diverges; if a future edition or measurement makes it converge, this
    gate should still hold, and the receipt should still say which way it went.
    """
    convergence = load_sealed(RECEIPT)["convergence"]
    assert convergence["gate"] == "symbolic_and_audio_convergence"
    assert convergence["gate_passed"] == (not convergence["diverged"])

    verdicts = {name: row["verdict"] for name, row in convergence["claims"].items()}
    assert set(convergence["converged"]) == {n for n, v in verdicts.items() if v == "converges"}
    assert set(convergence["diverged"]) == {
        n for n, v in verdicts.items() if v in {"diverges", "inconsistent"}}


def test_the_tempo_claim_is_scored_by_more_than_one_instrument():
    """A divergence resting on one tracker is a tracker's opinion, not a finding."""
    tempo = load_sealed(RECEIPT)["convergence"]["claims"]["tempo"]
    assert len(tempo["estimators_bpm"]) >= 4
    assert tempo["estimator_spread_percent"] <= 5.0

    # The claim and the measurement are scored on the same click-grid instrument, so the
    # comparison cannot be an argument between two different methods.
    assert tempo["click_grid_score_at_claimed_bpm"] is not None
    assert tempo["click_grid_score_at_measured_bpm"] is not None
    assert tempo["local_windows_near_claimed_bpm"] + tempo["local_windows_near_measured_bpm"] \
        <= tempo["local_windows_total"] * 2

    if tempo["verdict"] == "diverges":
        # Whatever the recording is doing, the claim is not the better description of it.
        assert tempo["click_grid_score_at_claimed_bpm"] <= tempo["click_grid_best"]["score"]
        assert tempo["error_percent"] > tempo["tolerance_percent"]


def test_the_witness_carries_three_tempos_and_the_receipt_says_so():
    """The declared tempo is contradicted by the witness's own duration-bearing artifacts.

    That is the finding, not a footnote: the number implied by the material the package
    generated is the one that agrees with the recording, which is what makes the declared
    138 look like metadata rather than measurement.
    """
    claims = load_sealed(RECEIPT)["convergence"]["claims"]
    declared = claims["tempo"]["claimed_bpm"]
    span = claims["witness_internal_consistency"]["tempo_implied_by_claimed_beats_and_duration"]
    continuation = claims["witness_continuation_consistency"]["tempo_implied_by_continuation"]

    assert len({declared, span, continuation}) == 3, "the three tempos are no longer distinct"

    # The continuation is nearer the measurement than the declaration is.
    measured = claims["tempo"]["measured_bpm"]
    assert abs(continuation - measured) < abs(declared - measured)
    assert claims["witness_continuation_consistency"]["error_against_measured_percent"] <         claims["tempo"]["error_percent"]


def test_the_witness_is_still_a_witness_and_not_an_answer_key():
    receipt = load_sealed(RECEIPT)
    witness = receipt["witness"]
    assert witness["target_recording_bytes_used_by_witness"] is False
    assert witness["executable_notes_available"] is False
    assert witness["proof_pack_present_locally"] is False

    # The specimen's own identity is carried, so a swapped specimen is visible.
    import json
    specimen = json.loads(SPECIMEN.read_text(encoding="utf-8"))
    assert witness["specimen_id"] == specimen["specimen_id"]
    assert witness["report_sha256"] == specimen["report_sha256"]
    assert witness["proof_pack_sha256"] == specimen["proof_pack"]["sha256"]


def test_finding_a_file_does_not_move_the_album():
    receipt = load_sealed(RECEIPT)
    authority = receipt["authority"]
    assert authority["album_master_accepted"] is False
    assert authority["system_reference_completed"] is False
    assert authority["owner_audition_performed"] is False
    assert authority["realization_produced"] is False
    assert authority["rights_or_release_permission"] is False
    assert authority["moves_album_counter"] is False

    boundary = receipt["boundary"]
    assert boundary["private_paths_included"] is False
    assert boundary["source_audio_exported"] is False
    assert boundary["witness_transcription_included"] is False
