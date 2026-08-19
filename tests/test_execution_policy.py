"""Gates for the standing execution policy.

This file exists because the failure it guards against is a drift, not a bug. The
stop-on-negative-result habit was never written down as policy -- it accumulated, one
reasonable-looking gate at a time, until the constitution read as a queue and six tracks
waited behind one. Prose can drift back the same way, quietly, and nothing would fail.

So the two things that make the policy operative are asserted: that a closed track is
distinguished from a stopped program, and that the boundary between what proceeds and what
costs the owner attention is actually stated rather than implied.

The runner is not pytest: gate functions take no arguments, or a lone `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
ALBUM_ONE = ROOT / "ALBUM_ONE.md"
PRODUCT = ROOT / "PRODUCT.md"


def test_the_constitution_separates_a_closed_track_from_a_stopped_program():
    text = AGENTS.read_text(encoding="utf-8")
    assert "## Execution policy" in text
    for event in ("one candidate loses", "one mechanism fails", "one track closes",
                  "the whole program stops"):
        assert event in text, f"the policy no longer names {event!r}"
    assert "The fourth never" in text


def test_both_sides_of_the_owner_boundary_are_stated():
    """A list of what to continue is useless without the list of what to stop for."""
    text = AGENTS.read_text(encoding="utf-8")
    assert "### Continue without asking" in text
    assert "### Interrupt the owner only for" in text

    for action in ("provider execution", "rendering", "work on other Album tracks"):
        assert action in text
    for reason in ("a short genuine musical verdict", "destructive deletion",
                   "publication or rights authority"):
        assert reason in text


def test_a_pull_request_still_has_to_reach_audio():
    text = AGENTS.read_text(encoding="utf-8")
    assert "### What a pull request must do" in text
    for requirement in ("produce owner-auditionable audio",
                        "bind material required to produce audio",
                        "repair a defect directly preventing audio"):
        assert requirement in text


def test_the_owner_review_is_the_smallest_object_that_answers_the_question():
    text = AGENTS.read_text(encoding="utf-8")
    assert "### What an owner review may cost" in text
    assert "smallest object" in text


def test_the_album_program_runs_lanes_rather_than_a_queue():
    text = ALBUM_ONE.read_text(encoding="utf-8")
    assert "There is no execution order" in text
    assert "A review blocks its own candidate" in text

    # A numbered list under this heading is the exact shape that was retired.
    section = text.split("## Execution order", 1)[1].split("\n## ", 1)[0]
    numbered = [line for line in section.splitlines()
                if line[:2] in {"1.", "2.", "3.", "4.", "5.", "6.", "7."}]
    assert not numbered, f"the execution order became a queue again: {numbered[:3]}"


def test_the_product_name_is_settled():
    text = PRODUCT.read_text(encoding="utf-8")
    assert "## Names" in text
    assert "stays EarCrate" in text
    assert "SongGraph" in text and "not the product name" in text
    # The internal representation is allowed to have the better name.
    assert "ArrangementGraph" in text or "PerformanceGraph" in text
