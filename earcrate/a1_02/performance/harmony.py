"""Realize the score's harmony across its performed traversal.

Every note here comes from three sealed things: the printed chord symbols, the chord
vocabulary that resolves them to pitch classes, and the navigation that says which
printed measure sounds when. Nothing is invented to fill a gap, and nothing is taken
from a recording.

What this is not: the performance. The score's 1,257 notes live in a reconstruction
MIDI that is unavailable, so voicing, melody, rhythm within the bar, articulation and
pedalling are absent rather than guessed. A chord symbol says which harmony is in
force, not how it was played, and a realization that pretended otherwise would be
inventing the very thing A1-02 exists to derive.

The result is a diagnostic that can be listened to: it exposes the traversal, the
repeats, the D.S. return, the coda, and whether 130 bpm reads as a plausible tempo for
this music. Those are real questions and they do not need the melody to be answered.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .. import score_timeline as st

# Section dynamics, declared rather than derived: the score's printed dynamics are on
# pages we do not have, so these are stated as an engineering choice and labelled as
# one. They shape the diagnostic; they do not claim to be the score's markings.
SECTION_VELOCITY: dict[str, int] = {
    "intro": 52,
    "A first pass": 64,
    "A repeat": 68,
    "A second ending": 70,
    "B first pass": 76,
    "B repeat": 80,
    "body": 84,
    "C first pass": 88,
    "C repeat": 92,
    "C second ending": 88,
    "D.S. from the Segno": 84,
    "Coda": 72,
}

BASS_OCTAVE = 3          # written pitch class -> sounding octave for the left hand
CHORD_OCTAVE = 5         # and for the right-hand chord


class HarmonyRealizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Note:
    """One sounding note, with the score evidence that put it there."""

    start_beat: float
    duration_beats: float
    pitch: int
    velocity: int
    hand: str
    performed_measure: int
    printed_measure: int
    chord_label: str
    section: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_beat": round(self.start_beat, 6),
            "duration_beats": round(self.duration_beats, 6),
            "pitch": self.pitch, "velocity": self.velocity, "hand": self.hand,
            "performed_measure": self.performed_measure,
            "printed_measure": self.printed_measure,
            "chord_label": self.chord_label, "section": self.section,
        }


def harmony_by_printed_measure(annotations: Mapping[str, Any]) -> dict[int, tuple[str, dict]]:
    """Which chord is in force at each printed measure, symbols held until replaced."""
    vocabulary = annotations.get("chord_vocabulary") or {}
    symbols = sorted(annotations.get("chord_symbols") or [],
                     key=lambda row: int(row["printed_measure"]))
    if not symbols:
        raise HarmonyRealizationError("the annotations carry no chord symbols")

    held: dict[int, tuple[str, dict]] = {}
    current: tuple[str, dict] | None = None
    pointer = 0
    for measure in range(1, st.PRINTED_MEASURES + 1):
        while pointer < len(symbols) and int(symbols[pointer]["printed_measure"]) <= measure:
            label = str(symbols[pointer]["label"])
            entry = vocabulary.get(label)
            if entry:
                current = (label, dict(entry))
            pointer += 1
        if current is None:
            raise HarmonyRealizationError(
                f"printed measure {measure} precedes the first resolvable chord symbol")
        held[measure] = current
    return held


def realize(annotations_path: Path, *, beats_per_measure: int = 4) -> dict[str, Any]:
    """Sound the harmony through the performed traversal, deterministically."""
    annotations = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    harmony = harmony_by_printed_measure(annotations)
    order = st.performed_order()

    notes: list[Note] = []
    for index, (printed, section) in enumerate(order):
        label, entry = harmony[printed]
        velocity = SECTION_VELOCITY.get(section, 72)
        start = index * beats_per_measure

        # Left hand holds the bass for the bar; right hand states the chord on beats
        # one and three. Two voices, because the score authority names two staves.
        for pitch_class in entry.get("bass_pitch_classes") or [entry["root_pc"]]:
            notes.append(Note(start, float(beats_per_measure),
                              12 * BASS_OCTAVE + int(pitch_class) % 12,
                              max(1, velocity - 12), "left", index + 1, printed, label,
                              section))
        for offset in (0.0, beats_per_measure / 2):
            for pitch_class in entry.get("stable_pitch_classes") or entry["pitch_classes"]:
                notes.append(Note(start + offset, beats_per_measure / 2,
                                  12 * CHORD_OCTAVE + int(pitch_class) % 12,
                                  velocity, "right", index + 1, printed, label, section))

    notes.sort(key=lambda row: (row.start_beat, row.pitch, row.hand))
    tempo = float((annotations.get("tempo") or {}).get("bpm") or 0)
    if tempo <= 0:
        raise HarmonyRealizationError("the annotations declare no printed tempo")

    return {
        "kind": "earcrate_a1_02_harmony_realization",
        "schema_version": 1,
        "what_this_is": ("a harmonic realization of the score's traversal, not the "
                         "note-level performance"),
        "what_is_absent": [
            "melody and inner voices, which live in the unavailable reconstruction MIDI",
            "printed dynamics, which are on the unavailable score pages",
            "articulation, pedalling and voicing",
            "rhythm within the bar beyond a stated chord pattern",
        ],
        "reference_pcm_used": False,
        "reference_recording_consulted": False,
        "tempo_bpm": tempo,
        "beats_per_measure": beats_per_measure,
        "performed_measures": len(order),
        "printed_measures": st.PRINTED_MEASURES,
        "note_count": len(notes),
        "section_velocity": dict(SECTION_VELOCITY),
        "notes": [row.as_dict() for row in notes],
        "source_authority": {
            "annotations_sha256": None,      # filled by the caller that read the file
            "chord_symbols": len(annotations.get("chord_symbols") or []),
            "chord_vocabulary": len(annotations.get("chord_vocabulary") or {}),
            "navigation": [f"{a}->{b} {kind}" for a, b, kind in st.NAVIGATION],
        },
    }
