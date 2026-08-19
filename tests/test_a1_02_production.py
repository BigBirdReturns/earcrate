"""Gates for the A1-02 production candidate.

A production is an arrangement of a performance, and the claim that makes it reviewable
is that it is *only* an arrangement: the same notes, the same performance, the same rack,
with level moves on top. These gates hold that claim to its two checkable halves --
the role split reconstructs the performance it came from, and a production with every
arrangement move set to zero reproduces that performance.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402
from earcrate.mix.model import MixScoreError, mixscore_seal  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-02-production-candidate-v1.public.json"

# The arrangement is a level move on top of a performance, so the two ways it could
# silently stop being that are a split that loses material and a null arrangement that
# does not reproduce its source. Both are bounded well below audibility.
RECONSTRUCTION_CEILING = 1e-4


def test_the_role_split_reconstructs_the_performance_it_came_from():
    """Three stems that do not sum back to the incumbent are three different renders."""
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    split = receipt["role_split"]
    counts = split["counts"]
    assert sum(counts.values()) == 1253, "the split must account for every performed note"
    assert counts["melody"] and counts["inner"] and counts["bass"]
    assert 0.0 <= split["roles_reconstruct_incumbent_residual"] < RECONSTRUCTION_CEILING

    # Each role was renormalised on its own peak, so the split is only faithful because
    # each was put back on the incumbent's gain. That correction has to be recorded.
    assert set(split["rebalanced_onto_incumbent_db"]) == set(counts)


def test_a_null_arrangement_reproduces_the_performance():
    """The control that makes every audible difference attributable to the arrangement."""
    receipt = load_sealed(RECEIPT)
    control = receipt["control_property"]
    assert control["verified"] is True
    assert 0.0 <= control["peak_matched_residual"] < RECONSTRUCTION_CEILING


def test_every_arrangement_move_lands_inside_the_piece_and_moves_forward():
    receipt = load_sealed(RECEIPT)
    anchors = receipt["arrangement"]["anchors"]
    beats = [float(row["beat"]) for row in anchors]

    assert beats == sorted(beats), "arrangement anchors are out of order"
    assert len(set(beats)) == len(beats), "two arrangement anchors share a beat"
    assert beats[0] == 0.0, "the arrangement must state where it starts"
    assert beats[-1] <= 417.0, "an anchor lands after the end of the score"
    for row in anchors:
        assert str(row["intent"]).strip(), "an arrangement move with no stated intent"


def test_the_render_executed_the_whole_arrangement():
    receipt = load_sealed(RECEIPT)
    renders = receipt["renders"]
    assert renders["refused_events"] == 0
    assert renders["executed_events"] == receipt["arrangement"]["event_count"]
    assert renders["stem_reconciliation_max_abs"] == 0.0
    assert renders["per_deck_stems"] == 3


def test_determinism_is_claimed_at_the_identity_the_writer_actually_holds():
    """The MixScore writer stamps a timestamp into the container.

    Two identical renders therefore differ by one header byte. Claiming file-level
    determinism here would be false, so the receipt has to claim PCM determinism and say
    why -- otherwise the caveat gets quietly dropped and the claim becomes a lie later.
    """
    receipt = load_sealed(RECEIPT)
    renders = receipt["renders"]
    assert renders["bit_identical_across_executions"] is True
    assert renders["identity_is_pcm_not_container"] is True
    assert "PEAK" in renders["container_caveat"]
    assert len(renders["master_pcm_f32le_sha256"]) == 64


def test_the_receipt_states_what_the_production_did_not_do():
    receipt = load_sealed(RECEIPT)
    assert receipt["arrangement"]["section_scale_changed"] is False
    assert "not pulled" in receipt["arrangement"]["section_scale_note"]
    assert "one piano" in receipt["limits"]["orchestration"]
    assert receipt["state"]["album_authority_changed"] is False
    assert receipt["state"]["audio_answer_key"] == "unbound"
    assert receipt["independence"]["reference_recording_consulted"] is False

    pack = receipt["owner_pack"]
    assert pack["blind"] is False, "the control is disclosed; this review is not blind"
    assert pack["level_matched_lufs"] == min(pack["measured_lufs"].values())
    assert "A1-01" in pack["on_loss"], "the receipt must record what a loss costs"


# --- the refusal this actually hit -------------------------------------------------

def test_a_deck_may_not_be_played_past_the_end_of_its_asset(tmp_path):
    """The bug that stopped the first production render.

    One role stem was shorter than the piece, because a role whose last note is early
    produces a shorter render. Playing it to the end would have run the deck off the end
    of its source. Silently padding that with whatever follows in memory is the failure
    mode worth refusing, so the transport refuses instead.
    """
    import numpy as np
    import soundfile as sf

    from earcrate.mix.render import mixscore_render_to_files

    short = tmp_path / "short.wav"
    sf.write(str(short), np.zeros((4800, 2), dtype=np.float32), 48000, subtype="FLOAT")

    score = {
        "kind": "earcrate_mix_score", "schema_version": 1, "title": "runs off the end",
        "clock": {"bpm": 120.0, "beats_per_bar": 4, "sample_rate": 48000},
        "end_beat": 64.0,
        "assets": [{"asset_id": "s", "path": str(short), "source_bpm": 120.0}],
        "decks": [{"deck_id": "d", "crossfader_side": "none"}],
        "events": [{"op": "load", "at_beat": 0.0, "deck_id": "d", "asset_id": "s"},
                   {"op": "play", "at_beat": 0.0, "deck_id": "d", "asset_id": "s"}],
    }
    with pytest.raises(MixScoreError) as caught:
        mixscore_render_to_files(mixscore_seal(score), tmp_path / "out.wav")
    assert "exhausted" in str(caught.value)


def test_an_arrangement_move_after_the_end_of_the_piece_is_refused(tmp_path):
    """A fade whose target beat is past end_beat is a move that never happens."""
    import numpy as np
    import soundfile as sf

    asset = tmp_path / "a.wav"
    sf.write(str(asset), np.zeros((48000, 2), dtype=np.float32), 48000, subtype="FLOAT")

    score = {
        "kind": "earcrate_mix_score", "schema_version": 1, "title": "move off the end",
        "clock": {"bpm": 120.0, "beats_per_bar": 4, "sample_rate": 48000},
        "end_beat": 8.0,
        "assets": [{"asset_id": "s", "path": str(asset), "source_bpm": 120.0}],
        "decks": [{"deck_id": "d", "crossfader_side": "none"}],
        "events": [{"op": "load", "at_beat": 0.0, "deck_id": "d", "asset_id": "s"},
                   {"op": "fade", "from_beat": 4.0, "to_beat": 40.0, "deck_id": "d",
                    "from_db": 0.0, "to_db": -6.0, "curve": "s_curve"}],
    }
    with pytest.raises(MixScoreError):
        mixscore_seal(score)


def test_the_arrangement_is_present_in_the_render_not_only_in_the_plan():
    """An arrangement that does not show up in the audio is a plan, not a track."""
    receipt = load_sealed(RECEIPT)
    arc = receipt["measured_arc"]
    sections = arc["sections"]
    assert len(sections) >= 10

    by_name = {row["section"]: row["delta_db"] for row in sections}
    # The shape that was designed has to be the shape that was measured, or the
    # arrangement anchors are decorative.
    assert by_name["intro"] < 0, "the intro was meant to be held back"
    assert by_name["B repeat"] < 0, "the repeat was meant to turn inward"
    assert by_name["body peak"] > by_name["body opening"], "the body was meant to build"
    assert by_name["D.S. return"] < 0, "the return was meant to read as a return"
    assert by_name["Coda"] > 0, "the coda is the densest music and was meant to open up"

    # Restraint is a choice, and the receipt has to own it rather than let a timid
    # arrangement pass as a bold one.
    assert arc["largest_move_db"] < 3.0
    assert "too timid" in arc["restraint_note"]


def test_the_production_can_be_rebuilt_from_the_repository():
    """A candidate that only exists as bytes on one machine is not reproducible work."""
    receipt = load_sealed(RECEIPT)
    repro = receipt["reproduction"]
    assert (ROOT / repro["script"]).exists(), "the build script named by the receipt is missing"
    assert repro["private_paths_in_script"] is False
    assert repro["rebuilt_from_a_clean_directory"] is True
    assert repro["rebuild_reproduced_master_pcm_f32le_sha256"] is True

    # The score digest is path-bound, so it is not the arrangement's identity. Saying so
    # here stops a later reader from reading a changed digest as a changed render.
    assert repro["score_digest_is_path_bound"] is True
    assert "survives relocation" in repro["score_digest_note"]
