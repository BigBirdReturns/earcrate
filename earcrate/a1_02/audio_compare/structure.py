"""Form from recurrence, for any source and any number of parts.

The first comparator failed because it asked "what harmony is this bar?" and Children
answers that question the same way for seven minutes. The question that actually
carries form is "which bars are like which other bars?" -- and every piece of music
answers that differently, whatever it is made of.

So nothing here knows about chords, instruments, or how many parts a source has. A
source becomes a sequence of bar fingerprints; a fingerprint sequence becomes a
self-similarity structure; a self-similarity structure becomes a form. Two forms are
then compared as forms.

That is what makes it work on a two-track piano reduction and a twenty-four-track
sequence of the same music, and on a stem pile with a hundred parts: parts are summed
into the bar before anything looks at them. Track count is not a parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

# A fingerprint is 12 pitch classes, plus density, register centre and register spread.
FINGERPRINT_WIDTH = 15


class StructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    """One run of bars, and which earlier run it repeats."""

    label: str
    start: int
    end: int
    repeats: str | None = None

    @property
    def bars(self) -> int:
        return self.end - self.start + 1

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "start_bar": self.start, "end_bar": self.end,
                "bars": self.bars, "repeats": self.repeats}


def fingerprint(pitch_classes: Sequence[float], density: float,
                register_centre: float, register_spread: float) -> np.ndarray:
    """One bar, described so that any source can produce it.

    Pitch classes carry harmony and melody together; density carries how busy the bar
    is; register carries where it sits. Nothing carries instrumentation, because a
    piano reduction and a full production of the same bar should land near each other.
    """
    vector = np.zeros(FINGERPRINT_WIDTH, dtype=float)
    weights = np.asarray(pitch_classes, dtype=float)
    total = weights.sum()
    vector[:12] = weights / total if total else 1.0 / 12
    vector[12] = density
    vector[13] = register_centre
    vector[14] = register_spread
    return vector


def normalize(fingerprints: np.ndarray) -> np.ndarray:
    """Put every column on its own scale, so no single feature dominates by units."""
    if fingerprints.ndim != 2 or fingerprints.shape[1] != FINGERPRINT_WIDTH:
        raise StructureError(f"expected an N x {FINGERPRINT_WIDTH} fingerprint matrix")
    out = fingerprints.astype(float).copy()
    for column in range(12, FINGERPRINT_WIDTH):
        values = out[:, column]
        spread = values.max() - values.min()
        out[:, column] = (values - values.min()) / spread if spread else 0.0
    return out


def self_similarity(fingerprints: np.ndarray) -> np.ndarray:
    """Cosine similarity of every bar against every other bar."""
    matrix = normalize(fingerprints)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    return unit @ unit.T


def auto_threshold(similarity: np.ndarray, *, percentile: float = 97.0) -> float:
    """A repeat threshold taken from the source's own similarity distribution.

    A fixed cut-off cannot work across modalities. A symbolic source repeats exactly,
    so its off-diagonal similarities are near one or near nothing; a recording is noisy
    and dense, and on this material two *unrelated* bars already sit around 0.89. The
    first audio run therefore found one section covering the whole file -- everything
    looked like everything.

    So "repeated" means "far above what this particular source calls similar", which is
    a statement each source answers for itself. That is what makes the same code work
    on a two-track reduction, a twenty-four-track sequence, and a stream rip.
    """
    count = len(similarity)
    if count < 4:
        return 0.99
    offsets = [np.diagonal(similarity, offset=k) for k in range(2, count)]
    values = np.concatenate([row for row in offsets if row.size])
    if not values.size:
        return 0.99
    return float(min(0.995, max(0.5, np.percentile(values, percentile))))


def similarity_from_identities(identities: Sequence[Any]) -> np.ndarray:
    """Exact equality as a similarity matrix, for sources that know their own repeats.

    A score with navigation does not need its recurrence estimated: two performed bars
    carrying the same printed measure ARE the same material. Expressing that as a
    matrix lets the one form-reading algorithm serve both an exact source and a noisy
    one, instead of having a second path that can disagree with the first.
    """
    tokens = list(identities)
    if not tokens:
        raise StructureError("no bar identities to read a form from")
    index = {value: position for position, value in enumerate(dict.fromkeys(tokens))}
    codes = np.array([index[value] for value in tokens])
    return (codes[:, None] == codes[None, :]).astype(float)


def recurrence(similarity: np.ndarray, *, minimum_bars: int = 4,
               threshold: float | str = "auto", gap: int = 2) -> list[tuple[int, int, int]]:
    """Repeated runs, as (first occurrence start, later occurrence start, length).

    Read off the diagonals of the self-similarity matrix: a run of high similarity
    along an offset diagonal means those bars repeat that much later. This is how a
    form announces itself regardless of what the music is made of.
    """
    if threshold == "auto":
        threshold = auto_threshold(similarity)
    count = len(similarity)
    found: list[tuple[int, int, int]] = []

    for offset in range(minimum_bars, count):
        diagonal = np.diagonal(similarity, offset=offset)
        run = 0
        for index, value in enumerate(diagonal):
            if value >= threshold:
                run += 1
                continue
            if run >= minimum_bars:
                start = index - run
                found.append((start, start + offset, run))
            run = 0
        if run >= minimum_bars:
            start = len(diagonal) - run
            found.append((start, start + offset, run))

    # Keep the longest non-overlapping statements, longest first.
    found.sort(key=lambda row: (-row[2], row[0]))
    kept: list[tuple[int, int, int]] = []
    for first, later, length in found:
        if any(not (later + length <= other_later or other_later + other_length <= later)
               for _, other_later, other_length in kept):
            continue
        kept.append((first, later, length))
    return sorted(kept, key=lambda row: row[1])


def form(similarity: np.ndarray, **kwargs: Any) -> list[Segment]:
    """A labelled form: which spans are new material and which repeat which."""
    count = len(similarity)
    repeats = recurrence(similarity, **kwargs)
    labels: list[str | None] = [None] * count
    sources: dict[int, str] = {}
    next_label = 0

    for first, later, length in repeats:
        if labels[first] is None:
            label = chr(ord("A") + next_label % 26) + ("" if next_label < 26
                                                       else str(next_label // 26))
            next_label += 1
            for offset in range(length):
                if first + offset < count and labels[first + offset] is None:
                    labels[first + offset] = label
            sources[first] = label
        label = labels[first] or "?"
        for offset in range(length):
            if later + offset < count and labels[later + offset] is None:
                labels[later + offset] = label

    segments: list[Segment] = []
    index = 0
    while index < count:
        label = labels[index]
        end = index
        while end + 1 < count and labels[end + 1] == label:
            end += 1
        name = label or "-"
        segments.append(Segment(label=name, start=index, end=end,
                                repeats=name if label and index not in sources else None))
        index = end + 1
    return segments


def form_string(segments: Iterable[Segment]) -> str:
    """The form as a readable sequence, e.g. '- A A B A B -'."""
    return " ".join(segment.label for segment in segments)


def structure_signature(similarity: np.ndarray, **kwargs: Any) -> dict[str, Any]:
    """Everything about a source's shape that another source can be compared against."""
    segments = form(similarity, **kwargs)
    labelled = [row for row in segments if row.label != "-"]
    return {
        "bars": int(len(similarity)),
        "recurrence_threshold": round(auto_threshold(similarity), 4)
        if kwargs.get("threshold", "auto") == "auto" else kwargs["threshold"],
        "segments": [row.as_dict() for row in segments],
        "form_string": form_string(segments),
        "distinct_sections": len({row.label for row in labelled}),
        "repeated_bar_count": sum(row.bars for row in labelled),
        "repetition_fraction": round(
            sum(row.bars for row in labelled) / len(similarity), 4) if len(similarity)
        else 0.0,
    }


def signature_from_fingerprints(fingerprints: np.ndarray, **kwargs: Any) -> dict[str, Any]:
    """Estimated recurrence, for a source that does not know its own repeats."""
    return structure_signature(self_similarity(fingerprints), **kwargs)


def signature_from_identities(identities: Sequence[Any], **kwargs: Any) -> dict[str, Any]:
    """Exact recurrence, for a source that does."""
    kwargs.setdefault("threshold", 1.0)
    return structure_signature(similarity_from_identities(identities), **kwargs)


def compare_structures(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """How alike two forms are, independent of how long either one is.

    Compared as sequences of labels rather than of bars, so a production that wraps the
    same song in an intro and an outro still reads as the same form with material
    around it.
    """
    a = [row["label"] for row in left["segments"] if row["label"] != "-"]
    b = [row["label"] for row in right["segments"] if row["label"] != "-"]
    if not a or not b:
        return {"comparable": False, "reason": "one side has no repeated material",
                "left_form": left["form_string"], "right_form": right["form_string"]}

    # Longest common subsequence over section labels, after mapping each side's labels
    # to the order in which they first appear. That makes 'A B A B' and 'C D C D' the
    # same shape, which is the point: the letters are arbitrary, the pattern is not.
    def canonical(rows: list[str]) -> list[int]:
        order: dict[str, int] = {}
        return [order.setdefault(row, len(order)) for row in rows]

    ca, cb = canonical(a), canonical(b)
    table = np.zeros((len(ca) + 1, len(cb) + 1), dtype=int)
    for i in range(1, len(ca) + 1):
        for j in range(1, len(cb) + 1):
            table[i][j] = table[i - 1][j - 1] + 1 if ca[i - 1] == cb[j - 1] \
                else max(table[i - 1][j], table[i][j - 1])
    common = int(table[-1][-1])

    # Coverage of each side separately, because a single ratio over the longer side
    # rewards deletion: a truncated object shrinks its own denominator and scores
    # HIGHER than the untouched one. The adverse controls caught exactly that -- a
    # four-minute edit outscored the full recording -- so shape and completeness are
    # now reported apart, and neither can stand in for the other.
    left_coverage = common / len(ca)
    right_coverage = common / len(cb)
    left_bars = sum(row["bars"] for row in left["segments"] if row["label"] != "-")
    right_bars = sum(row["bars"] for row in right["segments"] if row["label"] != "-")
    span_ratio = min(left_bars, right_bars) / max(left_bars, right_bars) if max(
        left_bars, right_bars) else 0.0

    return {
        "comparable": True,
        "left_form": left["form_string"],
        "right_form": right["form_string"],
        "left_sections": len(a),
        "right_sections": len(b),
        "common_subsequence": common,
        "left_coverage": round(left_coverage, 4),
        "right_coverage": round(right_coverage, 4),
        # Harmonic mean, so one side covering well cannot hide the other covering badly.
        "shape_agreement": round(
            2 * left_coverage * right_coverage / (left_coverage + right_coverage), 4)
        if (left_coverage + right_coverage) else 0.0,
        "repeated_bars_left": left_bars,
        "repeated_bars_right": right_bars,
        "span_ratio": round(span_ratio, 4),
        "completeness": round(min(1.0, span_ratio), 4),
        "repetition_fraction_left": left["repetition_fraction"],
        "repetition_fraction_right": right["repetition_fraction"],
    }
