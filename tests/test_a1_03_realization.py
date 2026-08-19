"""Gates for the first A1-03 realization.

Two failures would make this receipt lie without making it look wrong.

The first is a control that is not a control. Candidate and control exist to isolate the
clock; if some other difference creeps in -- a different chart, different voicings, a
different rack -- the drift between them stops meaning what the receipt says it means.

The second is a reduction quietly promoted to a reconstruction. This plays a chart on one
piano. It has no drums, no bass and no interplay, and the receipt has to keep saying so
even after the audio starts sounding like something.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-realization-v1.public.json"
BINDING = ROOT / "proofs" / "album_one" / "a1-03-source-binding-v1.public.json"


def test_the_realization_is_made_from_the_bound_performance():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    binding = load_sealed(BINDING)["source_binding"]
    used = receipt["source_binding"]
    assert used["container_sha256"] == binding["container_sha256"]
    assert used["canonical_pcm_sha256"] == binding["canonical_pcm_sha256"]
    assert used["path_recorded_in_repository"] is False


def test_candidate_and_control_differ_only_in_the_clock():
    """The isolation is the experiment. Anything else differing invalidates the drift."""
    realization = load_sealed(RECEIPT)["realization"]
    candidate, control = realization["candidate"], realization["control"]

    assert realization["candidate_and_control_differ_only_in_the_clock"] is True
    assert candidate["clock"] == "recovered"
    assert control["clock"] == "fixed"

    # Same chart, same voicings, same rack: identical notes, range and sample set.
    assert candidate["events"] == control["events"]
    assert candidate["pitch_range"] == control["pitch_range"]
    assert candidate["distinct_samples_used"] == control["distinct_samples_used"]

    # Polyphony is deliberately NOT asserted equal. It is a timing-derived measure -- how many
    # notes happen to overlap -- and the clock is precisely what differs, so a held root
    # crossing into the next bar's comp is the isolation working rather than leaking. An
    # earlier version of this gate required equality and failed for that reason.
    assert abs(candidate["polyphony"] - control["polyphony"]) <= 1

    # And they are genuinely two objects, or the clock was never applied.
    assert realization["renders_are_distinct"] is True
    assert candidate["render"]["master_sha256"] != control["render"]["master_sha256"]


def test_the_fixed_grid_departure_is_reported_in_beats_a_listener_could_count():
    """Seconds are abstract. Beats are what a drifting comp actually sounds like."""
    departure = load_sealed(RECEIPT)["fixed_grid_departure"]
    assert departure["control_bpm"] == 138.0
    assert departure["max_absolute_departure_seconds"] >= \
        abs(departure["final_departure_seconds"])
    assert abs(departure["departure_in_beats_at_the_end"]) > 1.0
    assert len(departure["per_bar_seconds"]) == load_sealed(RECEIPT)["recovered_chart"]["bar_count"]


def test_the_harmony_cross_check_carries_its_own_chance_baseline():
    """A key claim confirmed without a baseline is a coincidence with good presentation."""
    cross = load_sealed(RECEIPT)["witness_cross_check"]
    assert cross["claim_read_after_recovery"] is True
    assert 0.0 < cross["chance_fraction"] < 1.0
    assert cross["observed_fraction"] == round(
        cross["chords_in_claimed_key"] / cross["chords_total"], 4)
    assert cross["lift_over_chance"] == round(
        cross["observed_fraction"] - cross["chance_fraction"], 4)
    assert cross["verdict"] == (
        "converges" if cross["observed_fraction"] > cross["chance_fraction"] else "diverges")


def test_the_chord_fit_is_reported_against_what_a_flat_chroma_would_give():
    chart = load_sealed(RECEIPT)["recovered_chart"]
    assert chart["chord_mass_fraction_median"] > chart["chance_mass_fraction_median"]
    assert len(chart["chords"]) == chart["bar_count"]
    assert len(chart["chord_mass_fractions"]) == chart["bar_count"]


def test_no_new_organ_was_added_to_play_a_chart():
    realization = load_sealed(RECEIPT)["realization"]
    assert realization["new_organs_added"] == 0
    assert "earcrate.a1_02.performance.rack_render" in realization["organs_reused_unmodified"]

    # The renderer only understands constant tempo. Identity clock is how a drifting grid
    # gets through it; if that ever changes, the placement stops meaning seconds.
    assert realization["renderer_clock"]["tempo_bpm"] == 60.0


def test_the_receipt_keeps_calling_a_reduction_a_reduction():
    receipt = load_sealed(RECEIPT)
    claims = " ".join(receipt["what_this_is_not"]).lower()
    assert "reconstruction" in claims
    assert "owner audition" in claims

    assert receipt["realization"]["comp_is_interpretation_not_recovery"]["note"]
    authority = receipt["authority"]
    assert authority["album_master_accepted"] is False
    assert authority["owner_audition_performed"] is False
    assert authority["witness_transcription_used"] is False
    assert authority["moves_album_counter"] is False

    boundary = receipt["boundary"]
    assert boundary["source_audio_modified"] is False
    assert boundary["source_audio_exported"] is False
    assert boundary["private_paths_included"] is False


def test_two_independent_readers_agree_about_the_chart():
    """The first version of this recovery agreed with itself and nothing else.

    Reading root and quality off one mixed chroma profile cannot separate a chord from its
    relative substitute -- B-flat major shares two of three tones with G minor 7 -- and a
    second opinion agreed with it on 12 bars of 32. Pinning the root from the bass register
    and letting two different full-spectrum readers name the quality took that to 30 of 32.

    So the chart carries its own reliability, and a realization is refused below the floor.
    A chart nobody can vouch for is not worth rendering.
    """
    agreement = load_sealed(RECEIPT)["recovered_chart"]["reader_agreement"]
    assert agreement["fraction"] >= agreement["floor"]
    assert agreement["fraction"] == round(
        agreement["bars_where_two_readers_agree"] / agreement["bars"], 4)
    assert len(agreement["readers"]) == 2
    assert agreement["root_pinned_by"] == "bass register argmax"


def test_the_chart_publishes_the_dissenting_reading_too():
    """Where the readers disagree, the receipt has to show both rather than only the winner."""
    chart = load_sealed(RECEIPT)["recovered_chart"]
    assert len(chart["second_opinions"]) == chart["bar_count"]
    assert len(chart["chords"]) == chart["bar_count"]
