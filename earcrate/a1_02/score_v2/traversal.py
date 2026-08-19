"""Read a score's own navigation and expand it into a performed order.

The A1-02 traversal was derived once, by hand, from that score's printed markers. That
was right for one score and useless for the next one: a repeat sign, a first and second
ending, a Segno and a `D.S. al Coda` are not facts about Children, they are notation.

So this reads them out of the MusicXML — which carries `<repeat>`, `<ending>`, `<segno>`,
`<coda>` and the direction words — and walks the measures the way a player would. No
score is named here, and nothing in it knows how many measures the piece has.

The walk is deliberately simple and refuses rather than guesses. Unbounded jumps, a
`D.S.` with no Segno, or a repeat structure it cannot resolve produce a finding, not an
improvised reading. A score whose navigation this cannot follow should be expanded by
someone who has looked at the page.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
import re
import xml.etree.ElementTree as ET
import zipfile

MAX_MEASURES = 20000          # a walk longer than this has not terminated


class TraversalError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeasureMarks:
    number: int
    repeat_forward: bool = False
    repeat_backward: bool = False
    repeat_times: int = 2
    ending_numbers: tuple[int, ...] = ()
    ending_stop: bool = False
    segno: bool = False
    coda: bool = False
    dal_segno: bool = False
    to_coda: bool = False
    fine: bool = False


def read_score(path: Path) -> ET.Element:
    path = Path(path)
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.endswith(".xml") and "META-INF" not in n]
            if not names:
                raise TraversalError(f"no score xml inside {path.name}")
            return ET.fromstring(archive.read(names[-1]))
    return ET.fromstring(path.read_text(encoding="utf-8"))


def read_marks(root: ET.Element) -> list[MeasureMarks]:
    """Navigation marks per measure, from barlines, directions and direction words."""
    marks: dict[int, dict[str, Any]] = {}
    for measure in root.findall(".//measure"):
        number = int(measure.get("number"))
        row = marks.setdefault(number, {"number": number, "ending_numbers": set()})

        for barline in measure.findall("barline"):
            repeat = barline.find("repeat")
            if repeat is not None:
                if repeat.get("direction") == "forward":
                    row["repeat_forward"] = True
                elif repeat.get("direction") == "backward":
                    row["repeat_backward"] = True
                    times = repeat.get("times")
                    if times and times.isdigit():
                        row["repeat_times"] = int(times)
            ending = barline.find("ending")
            if ending is not None:
                for token in re.findall(r"\d+", ending.get("number") or ""):
                    row["ending_numbers"].add(int(token))
                if ending.get("type") in ("stop", "discontinue"):
                    row["ending_stop"] = True

        for element in measure.iter():
            if element.tag == "segno":
                row["segno"] = True
            elif element.tag == "coda":
                row["coda"] = True
            elif element.tag == "sound":
                if element.get("dalsegno"):
                    row["dal_segno"] = True
                if element.get("tocoda"):
                    row["to_coda"] = True
                if element.get("fine"):
                    row["fine"] = True
            elif element.tag == "words" and element.text:
                text = element.text.strip().lower()
                if "d.s." in text or "dal segno" in text:
                    row["dal_segno"] = True
                    if "coda" in text:
                        row["needs_coda"] = True
                if "to coda" in text or text == "coda":
                    row["to_coda"] = True
                if text == "fine":
                    row["fine"] = True
                if "segno" in text and "d.s." not in text and "dal segno" not in text:
                    row["segno"] = True

    return [MeasureMarks(number=row["number"],
                         repeat_forward=row.get("repeat_forward", False),
                         repeat_backward=row.get("repeat_backward", False),
                         repeat_times=row.get("repeat_times", 2),
                         ending_numbers=tuple(sorted(row["ending_numbers"])),
                         ending_stop=row.get("ending_stop", False),
                         segno=row.get("segno", False), coda=row.get("coda", False),
                         dal_segno=row.get("dal_segno", False),
                         to_coda=row.get("to_coda", False), fine=row.get("fine", False))
            for row in (marks[key] for key in sorted(marks))]


def supplement(marks: list[MeasureMarks],
               additions: Mapping[int, Mapping[str, Any]]) -> list[MeasureMarks]:
    """Add navigation an OMR pass did not see, from an independent reading of the page.

    Glyph recognition misses things. On the score that prompted this module, Audiveris
    read the words "D.S. al Coda" but recognized neither the Segno nor the Coda sign,
    so the walk had a jump with no destination. The answer is to let another reading of
    the same page supply the marks it did see -- and to keep that supplement explicit,
    so a traversal always says which of its navigation came from where.
    """
    index = {row.number: row for row in marks}
    for number, fields in additions.items():
        if number not in index:
            raise TraversalError(f"supplemented measure {number} is not in the score")
        index[number] = replace(index[number], **dict(fields))
    return [index[key] for key in sorted(index)]


def expand(marks: list[MeasureMarks]) -> dict[str, Any]:
    """Walk the measures as written, honouring repeats, endings, D.S., To Coda and Fine."""
    if not marks:
        raise TraversalError("no measures to expand")
    index = {row.number: row for row in marks}
    numbers = [row.number for row in marks]

    segno = next((row.number for row in marks if row.segno), None)
    coda = next((row.number for row in marks if row.coda), None)

    performed: list[int] = []
    passes: dict[int, int] = {}          # repeat-start measure -> completed passes
    taken_ds = False
    taken_coda = False
    position = 0
    repeat_start = numbers[0]

    while position < len(numbers):
        number = numbers[position]
        row = index[number]

        if row.repeat_forward:
            repeat_start = number

        # An ending numbered n is only played on pass n of its repeat.
        if row.ending_numbers:
            current = passes.get(repeat_start, 0) + 1
            if current not in row.ending_numbers:
                position += 1
                continue

        performed.append(number)
        if len(performed) > MAX_MEASURES:
            raise TraversalError("the walk did not terminate; navigation is unresolvable")

        if row.to_coda and taken_ds and not taken_coda:
            if coda is None:
                raise TraversalError("a To Coda jump has no Coda to land on")
            taken_coda = True
            position = numbers.index(coda)
            continue

        if row.dal_segno and not taken_ds:
            if segno is None:
                raise TraversalError("a D.S. jump has no Segno to return to")
            taken_ds = True
            position = numbers.index(segno)
            continue

        if row.fine and taken_ds:
            break

        if row.repeat_backward:
            done = passes.get(repeat_start, 0) + 1
            passes[repeat_start] = done
            if done < row.repeat_times:
                position = numbers.index(repeat_start)
                continue

        position += 1

    return {
        "printed_measures": len(numbers),
        "performed_measures": len(performed),
        "performed_order": performed,
        "navigation_found": {
            "repeat_forward": sum(row.repeat_forward for row in marks),
            "repeat_backward": sum(row.repeat_backward for row in marks),
            "endings": sum(bool(row.ending_numbers) for row in marks),
            "segno": segno, "coda": coda,
            "dal_segno": sum(row.dal_segno for row in marks),
            "to_coda": sum(row.to_coda for row in marks),
            "fine": sum(row.fine for row in marks),
        },
        "measures_never_performed": [n for n in numbers if n not in set(performed)],
        "expansion_ratio": round(len(performed) / len(numbers), 4),
    }


def from_score(path: Path, *,
               additions: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    marks = read_marks(read_score(path))
    supplemented = supplement(marks, additions) if additions else marks
    result = expand(supplemented)
    result["navigation_supplemented"] = sorted(additions or {})
    return result
