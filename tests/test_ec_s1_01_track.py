"""Gates for EC-S1-01, the first commission outside the seven reference-bound tracks.

The commission makes three claims that every closed Album One lane failed on, so each is
gated against evidence rather than against the plan that produced it.

It has to function as a band. A table saying the drums are withheld is a claim; the drum stem
measuring silent across those bars is the fact, and the two came apart during the build -- a
tiling bug put a kit inside the one section built to withhold it.

The arrangement has to make an audible difference. A1-01 closed because a difference was
argued from waveform decorrelation, so difference here is perceptual and its floor is
calibrated against the distance between two halves of the same section, never chosen.

It cannot be one part with fader automation. Every transition has to change what is playing or
which recording is playing it, and the diff records that as data.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "commissions" / "ec-s1-01-track-v1.public.json"


def test_it_is_a_complete_track_of_the_commissioned_length():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []
    seconds = receipt["track"]["seconds"]
    assert 150.0 <= seconds <= 210.0, f"{seconds}s is outside the 2:30-3:30 commission"
    assert receipt["track"]["lufs"] < 0.0


def test_the_band_actually_plays():
    """Measured from the stems, not from the arrangement table."""
    presence = {row["section"]: row for row in load_sealed(RECEIPT)["role_presence"]}
    sounding = {name: set(row["sounding"]) for name, row in presence.items()}
    assert "drums" in sounding["PAYOFF"] and "bass" in sounding["PAYOFF"], (
        "the payoff has no rhythm section")
    assert "foreground" in set().union(*sounding.values())
    # Every role is genuinely audible somewhere.
    for role in ("foreground", "bass", "drums"):
        assert any(role in row for row in sounding.values()), f"{role} never sounds"


def test_the_withholding_is_real():
    presence = {row["section"]: set(row["sounding"])
                for row in load_sealed(RECEIPT)["role_presence"]}
    assert "drums" not in presence["INTRO"] and "bass" not in presence["INTRO"], (
        "the opening withholds nothing")
    assert "drums" not in presence["HOLD"], (
        "the section before the payoff is not holding anything back")
    assert "drums" in presence["PAYOFF"], "the payoff does not arrive"


def test_the_states_differ_against_a_calibrated_floor():
    states = load_sealed(RECEIPT)["states"]
    assert states["floor_is_calibrated_not_chosen"] is True
    assert states["distance_floor"] > 0.0
    assert states["all_adjacent_states_differ"] is True
    assert len(states["pairs"]) >= 3, "four states means at least three transitions"
    for row in states["pairs"]:
        assert row["timbre_distance"] >= states["distance_floor"]
    # The floor has to be above the noise it was calibrated from.
    assert states["distance_floor"] > states["within_state_median"]


def test_no_transition_is_a_fader_move():
    arrangement = load_sealed(RECEIPT)["arrangement"]
    assert arrangement["every_transition_changes_content"] is True
    for row in arrangement["transitions"]:
        assert row["roles_entering"] or row["roles_leaving"] or row["material_changed"], (
            f"{row['from']} -> {row['to']} changes nothing but level")


def test_the_session_is_editable_and_shows_the_withholding():
    receipt = load_sealed(RECEIPT)
    session = receipt["session"]
    assert session["format"] == "rpp"
    assert session["clips"] >= len(receipt["arrangement"]["sections"]), (
        "one clip per role per section is what makes the arrangement draggable")
    assert session["withheld_roles_have_no_item"] is True
    assert set(session["role_tracks"]) == {"foreground", "bass", "drums"}
    assert receipt["midi"]["notes"] > 0, "the composed part does not travel as notes"


def test_the_grid_came_from_the_material():
    grid = load_sealed(RECEIPT)["grid"]
    assert "measured" in " ".join(str(value) for value in grid.values()).lower() \
        or grid["measured_bpm"] > 0
    assert grid["measured_bpm"] != 88.0, (
        "the grid is the requested tempo rather than the one the material came out at")
