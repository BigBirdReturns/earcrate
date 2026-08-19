"""Gates for the A1-03 trio realization.

This is the first A1-03 object that is allowed to reach the owner, so the gates are about the
two claims that entitle it to: that it is a *trio*, and that it is a *whole track*. Both are
easy to lose quietly.

The form shrinks. A window is cheaper to render and reads the same in a receipt, and the lane
has already spent one artifact on sixteen bars. So the form is asserted against the bound
recording's own length rather than against a bar count someone chose.

The parts stop being parts. A pitched crate zone retuned too far becomes mush and a trigger
zone whose region is too long becomes a wall; both survive a level check. So the renders are
asked whether they sound the pitch classes and land the attacks they were written with.

The candidate stops having a control. If the piano-only reduction ever stops being rendered
over the same form, the comparison becomes a comparison of lengths.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-trio-realization-v1.public.json"


def test_the_form_is_the_whole_bound_performance():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    form = receipt["form"]
    assert form["fraction_of_the_recording"] >= 0.95, (
        "the trio realization shrank back to a window")
    assert form["bars"] >= 100
    assert form["duration_seconds"] <= form["bound_recording_seconds"] + 1e-6


def test_all_three_parts_are_present_and_audible():
    receipt = load_sealed(RECEIPT)
    parts = receipt["parts"]
    assert set(parts) == {"piano", "bass", "drums"}
    assert parts["bass"]["notes"] > 0 and parts["drums"]["hits"] > 0
    assert parts["piano"]["events"] > 0

    levels = receipt["renders"]["levels"]
    for name in ("piano", "bass_stem", "drum_stem", "trio", "control"):
        assert levels[name]["rms"] > 1e-5, f"{name} rendered silence"


def test_the_parts_play_what_they_were_written_to_play():
    """Not silent is a low bar. These renders are asked what they are actually sounding."""
    fidelity = load_sealed(RECEIPT)["renders"]["part_fidelity"]
    assert fidelity["bass"]["notes_measured"] > 0
    assert fidelity["bass"]["fraction"] >= 0.5, (
        "the bass rack is not sounding the pitch classes the line was written with")
    drums = fidelity["drums"]
    assert drums["hits_written"] > 0
    assert 0 < drums["distinct_moments_written"] <= drums["hits_written"], (
        "the drum part is being scored per note again, so coincident hits inflate the miss "
        "rate no detector could avoid")
    assert drums["fraction"] >= 0.5, (
        "the drum hits are not landing as attacks; the trigger regions have smeared")
    assert drums["median_offset_seconds"] is not None
    assert abs(drums["median_offset_seconds"]) <= 0.04, (
        "the drums drag: the median attack sits too far from the beat it was written on")


def test_the_trigger_regions_start_on_their_transient():
    """A region boundary that sits before the attack is a drummer who is always late."""
    crate = load_sealed(RECEIPT)["crate"]
    assert crate["trigger_regions_moved_onto_their_transient"] > 0
    assert 0.0 < crate["attack_fraction_of_peak"] < 1.0


def test_the_candidate_keeps_a_control_over_the_same_form():
    receipt = load_sealed(RECEIPT)
    assert receipt["renders"]["renders_are_distinct"] is True
    control = receipt["control"]
    assert control["incumbent_may_win"] is True
    assert "same whole form" in control["what"]
    for difference in ("bass part", "drum part", "left-hand root"):
        assert difference in control["differs_from_the_candidate_by"]


def test_the_arrangement_decisions_are_declared_as_interpretation():
    """The chart is recovered. How a bassist walks it and how a drummer swings it is not."""
    parts = load_sealed(RECEIPT)["parts"]
    assert parts["bass"]["is_an_interpretation"] is True
    assert parts["drums"]["is_an_interpretation"] is True
    assert parts["bass"]["every_note_on_a_recovered_beat"] is True
    # The one drum decision that is not chosen is the one taken from the recording.
    assert "strongest accent" in parts["drums"]["snare_placed_by"]


def test_it_is_a_candidate_and_not_an_acceptance():
    receipt = load_sealed(RECEIPT)
    assert receipt["artifact_class"] == "complete_track_candidate"
    authority = receipt["authority"]
    assert authority["album_master_accepted"] is False
    assert authority["owner_audition_performed"] is False
    assert authority["moves_album_counter"] is False
    assert authority["witness_transcription_used"] is False
    claims = " ".join(receipt["what_this_is_not"]).lower()
    assert "accepted master" in claims
    assert "transcription" in claims


def test_nothing_new_was_built_to_make_this_work():
    receipt = load_sealed(RECEIPT)
    assert receipt["new_organs_added"] == 0
    reused = receipt["organs_reused_unmodified"]
    assert "earcrate.rack.library" in reused
    assert "earcrate.a1_02.performance.rack_render" in reused

    # The one thing that could have become an organ, kept as a stated selection instead.
    crate = receipt["crate"]
    assert crate["trigger_regions_trimmed_to_seconds"]
    assert "rather than built by a new hit-extraction organ" in crate["why_trimmed"]
    assert crate["screened_out_for_inaudible_attack"] >= 0
    assert crate["accepted"] > 0


def test_the_crate_stays_inside_the_boundary():
    receipt = load_sealed(RECEIPT)
    boundary = receipt["boundary"]
    assert boundary["private_paths_included"] is False
    assert boundary["source_audio_exported"] is False
    assert boundary["source_audio_modified"] is False
    assert boundary["crate_atoms_named_by_id_not_by_path"] is True

    body = RECEIPT.read_text(encoding="utf-8")
    for leak in ("Music Library", "S:\\\\", "D:\\\\", ".mp3", ".wav"):
        assert leak not in body, f"the public receipt leaks {leak!r}"


def test_the_rhythm_section_is_sealed_and_reproducible():
    build = load_sealed(RECEIPT)["rhythm_build"]
    for digest in ("proposal_sha256", "demand_sha256", "binding_sha256", "build_sha256",
                   "atom_pool_sha256"):
        assert len(build[digest]) == 64, f"{digest} is not a sealed digest"
    assert build["racks"], "no rack was sealed for the rhythm section"
    assert build["materialized_atoms"], "no crate atom was materialized"
    for slot in build["selected"]:
        assert slot["atoms"], f"slot {slot['slot']} selected nothing"
        if slot["mode"] == "trigger":
            # A trigger zone is rooted on its own note; retuning drums is not the mechanism.
            assert slot["maximum_transpose_semitones"] == 0


def test_the_receipt_says_which_digests_a_second_run_can_be_held_to():
    """The crate's WAV wrapper is not reproducible. Saying so is the difference between a
    known limit and a false reproducibility claim discovered by whoever tries it."""
    receipt = load_sealed(RECEIPT)
    pcm = receipt["renders"]["pcm_sha256"]
    for part in ("candidate", "control", "rhythm_master", "bass_stem", "drum_stem",
                 "piano_candidate", "piano_control"):
        assert len(pcm[part]) == 64, f"{part} has no PCM identity"
    assert pcm["candidate"] != pcm["control"]

    reproducibility = receipt["reproducibility"]
    assert reproducibility["what_a_second_run_is_held_to"] == "pcm_sha256"
    assert "pcm_sha256" in reproducibility["stable_across_runs"]
    assert "proposal_sha256" in reproducibility["stable_across_runs"]
    assert "rack_sha256" in reproducibility["not_stable_across_runs"], (
        "the receipt is claiming the rack seal reproduces, and it does not"
    )
