"""The twelve score anchors, expressed as something a recording can be matched against.

Each anchor is one contiguous run of the 105-measure performed order, carrying a
per-bar pitch-class expectation built from the printed chord symbols and the chord
vocabulary the score branch already sealed. No audio is consulted, and no measurement
of a recording may enter here: this is the score's claim, fixed before any comparison
runs, which is the only reason a later match means anything.

Chord symbols are sparse -- 36 of them across 69 printed measures -- so a symbol holds
until the next one. That is how the sheet is read, and pretending otherwise would
invent harmonic changes the score does not notate.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .. import score_timeline as st

MANDATORY_ANCHORS = ("Coda",)


class AnchorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScoreAnchor:
    anchor_id: str
    label: str
    order: int
    performed_start: int
    performed_end: int
    printed_measures: tuple[int, ...]
    chroma: tuple[tuple[float, ...], ...]
    mandatory: bool

    @property
    def bars(self) -> int:
        return self.performed_end - self.performed_start + 1

    def as_dict(self) -> dict[str, Any]:
        return {"anchor_id": self.anchor_id, "label": self.label, "order": self.order,
                "score_performed_measures": [self.performed_start, self.performed_end],
                "score_printed_path": list(self.printed_measures),
                "bars": self.bars, "mandatory": self.mandatory}


def _harmony_by_printed_measure(annotations: Mapping[str, Any]) -> dict[int, tuple[int, ...]]:
    """Pitch classes in force at each printed measure, symbols held until replaced."""
    vocabulary = annotations.get("chord_vocabulary") or {}
    symbols = sorted((row for row in annotations.get("chord_symbols") or []),
                     key=lambda row: int(row["printed_measure"]))
    if not symbols:
        raise AnchorError("the annotations carry no chord symbols to build anchors from")

    held: dict[int, tuple[int, ...]] = {}
    current: tuple[int, ...] = ()
    pointer = 0
    for measure in range(1, st.PRINTED_MEASURES + 1):
        while pointer < len(symbols) and int(symbols[pointer]["printed_measure"]) <= measure:
            entry = vocabulary.get(str(symbols[pointer]["label"])) or {}
            classes = entry.get("pitch_classes")
            if classes:
                current = tuple(int(v) % 12 for v in classes)
            pointer += 1
        held[measure] = current
    if not any(held.values()):
        raise AnchorError("no chord symbol resolved through the sealed vocabulary")
    return held


def _chroma_for(pitch_classes: tuple[int, ...]) -> tuple[float, ...]:
    vector = np.zeros(12, dtype=float)
    for pitch in pitch_classes:
        vector[pitch % 12] = 1.0
    total = vector.sum()
    return tuple(float(v / total) for v in vector) if total else tuple([1 / 12] * 12)


def score_anchors(annotations_path: Path) -> tuple[ScoreAnchor, ...]:
    annotations = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    harmony = _harmony_by_printed_measure(annotations)
    order = st.performed_order()

    anchors: list[ScoreAnchor] = []
    start = 0
    for index in range(1, len(order) + 1):
        ends = index == len(order) or order[index][1] != order[start][1]
        if not ends:
            continue
        label = order[start][1]
        printed = tuple(measure for measure, _ in order[start:index])
        anchors.append(ScoreAnchor(
            anchor_id=f"anchor_{len(anchors):02d}_{label.replace(' ', '_').replace('.', '')}",
            label=label, order=len(anchors),
            performed_start=start, performed_end=index - 1,
            printed_measures=printed,
            chroma=tuple(_chroma_for(harmony[measure]) for measure in printed),
            mandatory=label in MANDATORY_ANCHORS))
        start = index

    if len(anchors) != 12:
        raise AnchorError(f"expected twelve score anchors, derived {len(anchors)}")
    if sum(anchor.bars for anchor in anchors) != len(order):
        raise AnchorError("the anchors do not tile the performed order")
    return tuple(anchors)


def anchor_chroma(anchor: ScoreAnchor) -> np.ndarray:
    return np.array(anchor.chroma, dtype=float)
