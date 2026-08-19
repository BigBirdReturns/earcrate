"""Score authority v2, extracted from the recovered PDF and carrying its own provenance.

The historical extraction is gone. Its bytes exist nowhere, and the identity it was
sealed under may not be reused for anything regenerated. So this is a new object with a
new identity, built from the one artifact that did survive: the four-page PDF, recovered
byte-exact at `e029e1a3…`.

Every accepted note records where it came from — page, staff, printed measure, voice,
beat offset, duration, pitch, tie state, dynamic — so a later disagreement can be argued
about a specific note rather than a total. The historical count of 1,257 is a witness,
not a target: nothing here adds, splits, merges or suppresses an event to reach it.

The OMR output this reads is a candidate. A completed batch command is not a
transcription anyone should trust, and the fact that this module can parse it says
nothing about whether it heard the page correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile

from .. import score_timeline as st

# MusicXML note types to quarter-note lengths, for scores that omit <duration>.
TYPE_QUARTERS: dict[str, float] = {
    "breve": 8.0, "whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
    "16th": 0.25, "32nd": 0.125, "64th": 0.0625,
}


class ExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScoreNote:
    """One printed note, with the evidence that put it there."""

    index: int
    page: int
    staff: int
    printed_measure: int
    voice: str
    beat_offset: float
    duration_beats: float
    pitch: int
    step: str
    octave: int
    alter: int
    tie_in: bool
    tie_out: bool
    in_chord: bool
    dynamic: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "page": self.page, "staff": self.staff,
            "printed_measure": self.printed_measure, "voice": self.voice,
            "beat_offset": round(self.beat_offset, 6),
            "duration_beats": round(self.duration_beats, 6),
            "pitch": self.pitch, "step": self.step, "octave": self.octave,
            "alter": self.alter, "tie_in": self.tie_in, "tie_out": self.tie_out,
            "in_chord": self.in_chord, "dynamic": self.dynamic,
        }


def _musicxml_root(path: Path) -> ET.Element:
    """Read a .mxl container or a bare .xml score."""
    path = Path(path)
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist()
                     if name.endswith(".xml") and "META-INF" not in name]
            if not names:
                raise ExtractionError(f"no score xml inside {path.name}")
            return ET.fromstring(archive.read(names[-1]))
    return ET.fromstring(path.read_text(encoding="utf-8"))


def _pitch_number(step: str, octave: int, alter: int) -> int:
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[step]
    return (octave + 1) * 12 + base + alter


def _page_of_measure(root: ET.Element) -> dict[int, int]:
    """Which page each printed measure was engraved on, from the new-page breaks."""
    pages: dict[int, int] = {}
    page = 1
    for measure in root.findall(".//measure"):
        number = int(measure.get("number"))
        for layout in measure.findall("print"):
            if layout.get("new-page") == "yes":
                page += 1
        pages[number] = page
    return pages


def extract(musicxml: Path, *, source_pdf_sha256: str) -> dict[str, Any]:
    """Turn an OMR candidate into a provenance-carrying extraction candidate."""
    root = _musicxml_root(musicxml)
    pages = _page_of_measure(root)

    divisions = 1.0
    for element in root.iter("divisions"):
        divisions = float(element.text or 1.0)
        break

    notes: list[ScoreNote] = []
    dynamic: str | None = None
    for measure in root.findall(".//measure"):
        number = int(measure.get("number"))
        cursor = 0.0
        previous = 0.0
        for child in measure:
            if child.tag == "direction":
                marks = [row.tag for row in child.iter() if row.tag in
                         ("p", "pp", "ppp", "mp", "mf", "f", "ff", "fff")]
                if marks:
                    dynamic = marks[0]
                continue
            if child.tag == "backup":
                cursor -= float(child.findtext("duration") or 0) / divisions
                continue
            if child.tag == "forward":
                cursor += float(child.findtext("duration") or 0) / divisions
                continue
            if child.tag != "note":
                continue

            chord = child.find("chord") is not None
            duration = child.findtext("duration")
            if duration is not None:
                length = float(duration) / divisions
            else:
                length = TYPE_QUARTERS.get(child.findtext("type") or "", 1.0)

            pitch = child.find("pitch")
            if pitch is None:                       # a rest advances time and nothing else
                if not chord:
                    previous = length
                    cursor += length
                continue

            if chord:
                start = cursor - previous
            else:
                start = cursor
                previous = length
                cursor += length

            step = pitch.findtext("step") or "C"
            octave = int(pitch.findtext("octave") or 4)
            alter = int(float(pitch.findtext("alter") or 0))
            ties = {row.get("type") for row in child.findall("tie")}

            notes.append(ScoreNote(
                index=len(notes), page=pages.get(number, 1),
                staff=int(child.findtext("staff") or 1),
                printed_measure=number, voice=child.findtext("voice") or "1",
                beat_offset=start, duration_beats=length,
                pitch=_pitch_number(step, octave, alter), step=step, octave=octave,
                alter=alter, tie_in="stop" in ties, tie_out="start" in ties,
                in_chord=chord, dynamic=dynamic))

    if not notes:
        raise ExtractionError("the OMR candidate carries no pitched notes")

    printed = sorted({row.printed_measure for row in notes})
    per_measure = {measure: sum(1 for row in notes if row.printed_measure == measure)
                   for measure in printed}
    performed = sum(per_measure.get(measure, 0) for measure, _ in st.performed_order())

    return {
        "kind": "earcrate_a1_02_score_extraction_v2",
        "schema_version": 1,
        "status": "omr_candidate_unreviewed",
        "authority": "none until reconciled against the score pages",
        "source_pdf_sha256": source_pdf_sha256,
        "printed_measures_seen": len(printed),
        "printed_measure_range": [printed[0], printed[-1]],
        "printed_note_count": len(notes),
        "performed_note_count": performed,
        "notes_per_printed_measure": per_measure,
        "staff_distribution": {
            str(staff): sum(1 for row in notes if row.staff == staff)
            for staff in sorted({row.staff for row in notes})},
        "voice_distribution": {
            voice: sum(1 for row in notes if row.voice == voice)
            for voice in sorted({row.voice for row in notes})},
        "tie_counts": {"tie_in": sum(row.tie_in for row in notes),
                       "tie_out": sum(row.tie_out for row in notes)},
        "chord_member_count": sum(row.in_chord for row in notes),
        "dynamics_seen": sorted({row.dynamic for row in notes if row.dynamic}),
        "notes": [row.as_dict() for row in notes],
    }


def hard_gate(extraction: dict[str, Any], annotations: dict[str, Any]) -> list[str]:
    """The contract the extraction must satisfy before it is anything but a candidate."""
    problems: list[str] = []
    if extraction["printed_measures_seen"] != st.PRINTED_MEASURES:
        problems.append(
            f"printed measures: saw {extraction['printed_measures_seen']}, "
            f"score declares {st.PRINTED_MEASURES}")
    if extraction["printed_measure_range"] != [1, st.PRINTED_MEASURES]:
        problems.append(f"printed measures are not 1..{st.PRINTED_MEASURES}")
    if len(extraction["staff_distribution"]) != 2:
        problems.append("the score has two staves; the extraction does not")

    meter = annotations.get("meter") or {}
    if (meter.get("numerator"), meter.get("denominator")) != (4, 4):
        problems.append("the annotations do not declare 4/4")
    if (annotations.get("key_signature") or {}).get("fifths") != -4:
        problems.append("the annotations do not declare four flats")

    for measure, count in extraction["notes_per_printed_measure"].items():
        if count <= 0:
            problems.append(f"printed measure {measure} carries no notes")
    if st.measures_never_performed(float((annotations.get("tempo") or {}).get("bpm") or 130)):
        problems.append("a printed measure is never reached by the traversal")
    return problems
