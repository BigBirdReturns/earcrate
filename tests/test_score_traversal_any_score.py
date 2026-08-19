"""Reading a score's navigation must not know which score it is.

The A1-02 traversal was derived by hand from one set of printed markers. That is fine
as a fact about that piece and wrong as a mechanism: a repeat sign, a first and second
ending, a Segno and a D.S. al Coda are notation, not facts about Children.

Every score in this file is synthesized here. None of them is Children, and the module
under test contains no score's name.
"""

from __future__ import annotations

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.score_v2 import traversal as tv  # noqa: E402


def _score(measures: int, *, marks: dict[int, str] | None = None) -> ET.Element:
    """A minimal MusicXML skeleton with the requested navigation."""
    marks = marks or {}
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part", id="P1")
    for number in range(1, measures + 1):
        measure = ET.SubElement(part, "measure", number=str(number))
        # Tokens, not substrings. Matching on substrings planted a Coda sign at every
        # To Coda, because "tocoda" contains "coda" -- which quietly satisfied the very
        # refusal one of these tests exists to prove.
        mark = set(marks.get(number, "").split())
        if "forward" in mark:
            barline = ET.SubElement(measure, "barline", location="left")
            ET.SubElement(barline, "repeat", direction="forward")
        if "ending1" in mark:
            barline = ET.SubElement(measure, "barline", location="right")
            ET.SubElement(barline, "ending", number="1", type="stop")
        if "ending2" in mark:
            barline = ET.SubElement(measure, "barline", location="left")
            ET.SubElement(barline, "ending", number="2", type="start")
        if "backward" in mark:
            barline = ET.SubElement(measure, "barline", location="right")
            ET.SubElement(barline, "repeat", direction="backward")
        if "segno" in mark:
            direction = ET.SubElement(measure, "direction")
            ET.SubElement(ET.SubElement(direction, "direction-type"), "segno")
        if "coda" in mark:
            direction = ET.SubElement(measure, "direction")
            ET.SubElement(ET.SubElement(direction, "direction-type"), "coda")
        if "ds" in mark:
            direction = ET.SubElement(measure, "direction")
            ET.SubElement(direction, "sound", dalsegno="yes")
        if "tocoda" in mark:
            direction = ET.SubElement(measure, "direction")
            ET.SubElement(direction, "sound", tocoda="yes")
        if "fine" in mark:
            direction = ET.SubElement(measure, "direction")
            ET.SubElement(direction, "sound", fine="yes")
    return root


def _expand(root: ET.Element) -> dict:
    return tv.expand(tv.read_marks(root))


def test_a_score_with_no_navigation_is_played_once_through():
    result = _expand(_score(35))
    assert result["performed_measures"] == 35
    assert result["expansion_ratio"] == 1.0
    assert result["measures_never_performed"] == []
    assert result["navigation_found"]["repeat_backward"] == 0


def test_a_simple_repeat_doubles_its_span():
    result = _expand(_score(8, marks={1: "forward", 4: "backward"}))
    assert result["performed_order"] == [1, 2, 3, 4, 1, 2, 3, 4, 5, 6, 7, 8]
    assert result["performed_measures"] == 12


def test_alternate_endings_are_played_on_their_own_pass():
    """First ending on pass one, second on pass two, and never both."""
    result = _expand(_score(6, marks={1: "forward", 4: "ending1 backward",
                                      5: "ending2"}))
    assert result["performed_order"] == [1, 2, 3, 4, 1, 2, 3, 5, 6]
    assert result["performed_order"].count(4) == 1
    assert result["performed_order"].count(5) == 1


def test_a_dal_segno_returns_to_the_segno_once():
    result = _expand(_score(8, marks={3: "segno", 6: "ds"}))
    assert result["performed_order"] == [1, 2, 3, 4, 5, 6, 3, 4, 5, 6, 7, 8]
    assert result["performed_order"].count(3) == 2, "the D.S. must not loop forever"


def test_a_to_coda_only_fires_after_the_dal_segno():
    result = _expand(_score(10, marks={2: "segno", 5: "tocoda", 7: "ds", 8: "coda"}))
    order = result["performed_order"]
    assert order[:7] == [1, 2, 3, 4, 5, 6, 7], "To Coda must be passed over the first time"
    assert order[7:] == [2, 3, 4, 5, 8, 9, 10], "the second pass takes the coda"


def test_fine_ends_the_piece_after_the_return():
    result = _expand(_score(8, marks={2: "segno", 5: "fine", 7: "ds"}))
    order = result["performed_order"]
    assert order == [1, 2, 3, 4, 5, 6, 7, 2, 3, 4, 5], order
    assert order[-1] == 5, "the piece ends at the Fine, not at the last measure"


def test_a_jump_with_no_destination_is_refused_rather_than_guessed():
    with pytest.raises(tv.TraversalError, match="no Segno"):
        _expand(_score(6, marks={4: "ds"}))
    with pytest.raises(tv.TraversalError, match="no Coda"):
        _expand(_score(6, marks={2: "segno", 3: "tocoda", 5: "ds"}))


def test_a_supplement_can_add_navigation_the_transcription_missed():
    """Glyph recognition misses things; another reading of the page may supply them."""
    marks = tv.read_marks(_score(8, marks={6: "ds"}))
    with pytest.raises(tv.TraversalError):
        tv.expand(marks)

    fixed = tv.supplement(marks, {3: {"segno": True}})
    assert tv.expand(fixed)["performed_measures"] == 12

    with pytest.raises(tv.TraversalError, match="not in the score"):
        tv.supplement(marks, {99: {"segno": True}})


def test_the_walk_terminates_or_says_it_cannot():
    """A navigation that loops must fail loudly rather than run forever."""
    marks = tv.read_marks(_score(4, marks={2: "segno", 4: "ds"}))
    looping = [tv.MeasureMarks(number=row.number, segno=row.segno, dal_segno=row.dal_segno)
               for row in marks]
    assert tv.expand(looping)["performed_measures"] < tv.MAX_MEASURES


def test_the_module_names_no_score():
    source = (ROOT / "earcrate" / "a1_02" / "score_v2" / "traversal.py").read_text("utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    body = code.split('"""')
    executable = "".join(body[::2])
    for name in ("Children", "children", "Miles", "A1-02", "105", "69"):
        assert name not in executable, f"the traversal reader hardcodes {name!r}"


def test_the_compiler_takes_a_traversal_rather_than_importing_one():
    from earcrate.a1_02.score_v2 import compile_performed

    extraction = {
        "printed_measures_seen": 4, "printed_note_count": 4,
        "source_pdf_sha256": "a" * 64,
        "notes": [{"index": i, "page": 1, "staff": 1, "printed_measure": i + 1,
                   "voice": "1", "beat_offset": 0.0, "duration_beats": 4.0,
                   "pitch": 60 + i, "tie_in": False, "tie_out": False, "dynamic": None}
                  for i in range(4)],
    }
    performed = compile_performed(
        extraction, order=[(1, "-"), (2, "-"), (1, "-"), (2, "-")],
        tempo_bpm=90.0, enforce_score_literal_tempo=False)

    assert performed["performed_measures"] == 4
    assert performed["performed_note_count"] == 4
    assert performed["printed_measures"] == 4, "it must not report another score's count"
    assert performed["navigation"] == [], "a supplied traversal carries no A1-02 navigation"
    assert [row["printed_measure"] for row in performed["notes"]] == [1, 2, 1, 2]
