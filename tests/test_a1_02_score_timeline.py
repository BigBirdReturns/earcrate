"""Gates for the score-side performed timeline.

The expansion is only worth anything if it reproduces the count the bound score branch
already sealed, and if its navigation is the same navigation the specimen adapter
uses. Both are checked here rather than asserted in prose, because a timeline that
drifts from the score authority would quietly become a second, competing answer key.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02 import score_timeline as st  # noqa: E402

SPECIMEN = json.loads((ROOT / "specimens" / "children_v1.json").read_text(encoding="utf-8"))
ANNOTATIONS = json.loads(
    (ROOT / "specimens" / "children_v1.annotations.json").read_text(encoding="utf-8"))
PRINTED_BPM = ANNOTATIONS["tempo"]["bpm"]


def test_the_expansion_reproduces_the_sealed_performed_measure_count():
    """69 printed measures become 105 performed ones, or this file is wrong."""
    expected = SPECIMEN["expected"]["performed_measure_count"]
    assert len(st.performed_order()) == expected
    assert st.PRINTED_MEASURES == SPECIMEN["expected"]["printed_measure_count"]
    assert len(st.timeline(PRINTED_BPM)) == expected


def test_the_navigation_is_the_adapters_navigation():
    """A second, competing reading of the form is worse than none."""
    source = (ROOT / "earcrate" / "specimen" / "children.py").read_text(encoding="utf-8")
    assert st.cross_check_navigation(source) == []

    # And every jump is evidenced by a printed marker the annotations actually carry.
    marked = {int(row["printed_measure"]) for row in ANNOTATIONS["form_markers"]}
    for source_measure, target, kind in st.NAVIGATION:
        if kind in ("repeat", "repeat_exit"):
            continue
        assert marked & {source_measure, target, target - 1, source_measure + 1}, \
            f"the {kind} edge {source_measure}->{target} rests on no printed marker"


def test_every_printed_measure_is_performed_at_least_once():
    assert st.measures_never_performed(PRINTED_BPM) == (), \
        "a measure the navigation never reaches means the traversal is wrong"


def test_the_timeline_is_contiguous_and_ordered():
    rows = st.timeline(PRINTED_BPM)
    step = st.seconds_per_measure(PRINTED_BPM)
    assert rows[0].start_seconds == 0.0
    for left, right in zip(rows, rows[1:]):
        assert right.order_index == left.order_index + 1
        assert right.start_seconds == pytest.approx(left.end_seconds, abs=1e-6)
        assert left.end_seconds - left.start_seconds == pytest.approx(step, abs=1e-6)


def test_the_sections_cover_the_form_without_gaps():
    rows = st.sections(PRINTED_BPM)
    labels = [row["label"] for row in rows]
    assert labels[0] == "intro" and labels[-1] == "Coda"
    assert sum(row["measures"] for row in rows) == len(st.performed_order())
    for left, right in zip(rows, rows[1:]):
        assert right["start_seconds"] == pytest.approx(left["end_seconds"], abs=1e-6)
    assert rows[-1]["end_seconds"] == pytest.approx(st.total_seconds(PRINTED_BPM), abs=1e-6)


def test_the_score_side_length_cannot_cover_the_declared_edition():
    """The finding that shapes the comparator before any audio is heard.

    105 measures of 4/4 at the printed 130 bpm run about 3:14. The declared edition
    runs about 7:06. The sheet is therefore not a bar-for-bar transcription of that
    recording, and alignment must anchor on section correspondence rather than on
    total duration. This is not a verdict about the recording; it is a constraint on
    how the comparison may be made.
    """
    manifest = json.loads(
        (ROOT / "configs" / "album_one" / "manifest.v1.json").read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    declared = row["edition_declaration"]["approximate_duration"]
    minutes, seconds = declared.split(":")
    declared_seconds = int(minutes) * 60 + int(seconds)

    expectation = st.duration_expectation(PRINTED_BPM, declared_seconds)
    assert expectation["score_side_seconds"] == pytest.approx(193.85, abs=0.5)
    assert expectation["ratio"] > 2.0
    assert expectation["one_to_one_mapping_possible"] is False
    assert "section correspondence" in expectation["implication"]


def test_a_faster_reading_does_not_close_the_gap():
    """The record sits near 139 bpm; the shortfall widens rather than resolves."""
    at_139 = st.duration_expectation(139.0, 426)
    at_130 = st.duration_expectation(PRINTED_BPM, 426)
    assert at_139["score_side_seconds"] < at_130["score_side_seconds"]
    assert at_139["one_to_one_mapping_possible"] is False


def test_the_printed_tempo_is_the_only_tempo_this_module_claims():
    """A constant tempo is the score's claim, not a parameter to tune for fit."""
    source = (ROOT / "earcrate" / "a1_02" / "score_timeline.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    body = code.split('"""')
    executable = "".join(body[::2]) if len(body) > 1 else code
    assert "130" not in executable, \
        "the printed tempo belongs to the annotations; hardcoding it here would let the " \
        "timeline outlive a corrected reading of the sheet"
