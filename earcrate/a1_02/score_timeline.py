"""The performed timeline the score describes, before any audio exists.

This is the half of the structural comparison that needs nothing from a download. The
printed score plus its navigation markers determine which measures are played and in
what order; the printed tempo turns that order into seconds. Deriving it now means the
comparator, when it is written against a real waveform, starts from a score-side
expectation that was fixed independently rather than fitted to whatever the audio
happens to do.

The navigation is not guessed. It is the edge set the bound specimen adapter already
uses, and `tests/test_a1_02_score_timeline.py` asserts both that the expansion
reproduces the manifest's 105 performed measures and that every edge here appears in
`earcrate/specimen/children.py`. If the two ever disagree, this file is wrong.

One consequence is already visible without a single sample of audio: 105 measures of
4/4 at the printed 130 bpm run about 3:14, while the declared full-length edition runs
about 7:06. The sheet cannot be a bar-for-bar transcription of that record. Whatever
the comparator becomes, it cannot anchor on global duration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

BEATS_PER_MEASURE = 4
PRINTED_MEASURES = 69

# (from_measure, to_measure, kind). Sequential motion is implied; only the jumps that
# change the performed order are listed, and each one is evidenced by a printed marker.
NAVIGATION: tuple[tuple[int, int, str], ...] = (
    (8, 5, "repeat"),
    (7, 9, "alternate_ending"),
    (17, 10, "repeat"),
    (17, 18, "repeat_exit"),
    (61, 54, "repeat"),
    (59, 62, "alternate_ending"),
    (64, 34, "dal_segno"),
    (52, 65, "to_coda"),
)


class TimelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class PerformedMeasure:
    order_index: int
    printed_measure: int
    start_seconds: float
    end_seconds: float
    pass_label: str


def performed_order() -> tuple[tuple[int, str], ...]:
    """(printed measure, why it is here) in performed order.

    Written as the traversal a player actually makes, because that is the thing a
    listener hears and the thing an alignment has to explain.
    """
    rows: list[tuple[int, str]] = []

    def run(first: int, last: int, label: str) -> None:
        for measure in range(first, last + 1):
            rows.append((measure, label))

    run(1, 4, "intro")
    run(5, 8, "A first pass")
    run(5, 7, "A repeat")            # (8,5) repeat, then (7,9) alternate ending
    run(9, 9, "A second ending")
    run(10, 17, "B first pass")
    run(10, 17, "B repeat")          # (17,10) repeat, then (17,18) exit
    run(18, 52, "body")              # To Coda at 52 is passed over on the first time
    run(53, 61, "C first pass")
    run(54, 59, "C repeat")          # (61,54) repeat, then (59,62) alternate ending
    run(62, 64, "C second ending")   # D.S. al Coda at 64
    run(34, 52, "D.S. from the Segno")
    run(65, 69, "Coda")
    return tuple(rows)


def seconds_per_measure(bpm: float, beats: int = BEATS_PER_MEASURE) -> float:
    if bpm <= 0:
        raise TimelineError(f"tempo must be positive: {bpm}")
    return beats * 60.0 / bpm


def timeline(bpm: float, *, beats: int = BEATS_PER_MEASURE) -> tuple[PerformedMeasure, ...]:
    """The performed measures laid out in seconds at a constant tempo.

    Constant on purpose: the score declares one tempo and no rubato, so this is what
    the *score* claims. Any deviation the audio shows is then a finding about the
    performance rather than a parameter that was quietly tuned to make it fit.
    """
    step = seconds_per_measure(bpm, beats)
    rows: list[PerformedMeasure] = []
    for index, (measure, label) in enumerate(performed_order()):
        start = index * step
        rows.append(PerformedMeasure(index, measure, round(start, 6),
                                     round(start + step, 6), label))
    return tuple(rows)


def sections(bpm: float) -> tuple[dict[str, Any], ...]:
    """Contiguous runs sharing a pass label: the coarse shape to align against."""
    rows = timeline(bpm)
    out: list[dict[str, Any]] = []
    for row in rows:
        if out and out[-1]["label"] == row.pass_label:
            out[-1]["end_seconds"] = row.end_seconds
            out[-1]["measures"] += 1
            out[-1]["printed_measures"].append(row.printed_measure)
            continue
        out.append({"label": row.pass_label, "start_seconds": row.start_seconds,
                    "end_seconds": row.end_seconds, "measures": 1,
                    "printed_measures": [row.printed_measure]})
    return tuple(out)


def total_seconds(bpm: float) -> float:
    """Rounded exactly as the timeline rounds, so the two can be compared directly."""
    return round(len(performed_order()) * seconds_per_measure(bpm), 6)


def duration_expectation(bpm: float, declared_seconds: float) -> dict[str, Any]:
    """What the score-side length implies about aligning with a declared edition.

    Deliberately not a verdict. A large ratio does not mean the recording is the wrong
    object; it means the sheet is not a bar-for-bar transcription of it, and the
    comparator has to align on sections rather than on total length.
    """
    performed = total_seconds(bpm)
    ratio = declared_seconds / performed if performed else 0.0
    return {
        "score_side_seconds": performed,
        "declared_edition_seconds": declared_seconds,
        "ratio": round(ratio, 3),
        "one_to_one_mapping_possible": 0.95 <= ratio <= 1.05,
        "implication": (
            "the sheet accounts for the whole recording"
            if 0.95 <= ratio <= 1.05 else
            "the recording contains substantial material the sheet does not notate, so "
            "alignment must anchor on section correspondence rather than total duration"),
    }


def cross_check_navigation(adapter_source: str) -> list[str]:
    """Findings if this navigation has drifted from the bound specimen adapter."""
    problems: list[str] = []
    for source, target, _kind in NAVIGATION:
        needle = f"({source}, {target})"
        if needle not in adapter_source:
            problems.append(f"edge {needle} is not in the specimen adapter")
    return problems


def describe(bpm: float, declared_seconds: float) -> dict[str, Any]:
    return {
        "printed_measures": PRINTED_MEASURES,
        "performed_measures": len(performed_order()),
        "bpm": bpm,
        "seconds_per_measure": round(seconds_per_measure(bpm), 6),
        "total_seconds": total_seconds(bpm),
        "sections": [dict(row) for row in sections(bpm)],
        "duration_expectation": duration_expectation(bpm, declared_seconds),
    }


def _iter_measures(rows: Iterable[PerformedMeasure]) -> Iterable[int]:
    return (row.printed_measure for row in rows)


def measures_never_performed(bpm: float) -> tuple[int, ...]:
    """Printed measures the navigation never reaches. Should be empty; checked, not assumed.

    Takes a tempo it does not own: the printed tempo lives in the annotations, and a
    default here would let this module outlive a corrected reading of the sheet.
    """
    played = set(_iter_measures(timeline(bpm)))
    return tuple(measure for measure in range(1, PRINTED_MEASURES + 1)
                 if measure not in played)
