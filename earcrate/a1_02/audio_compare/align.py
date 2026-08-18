"""Monotonic anchor alignment, with the laws stated as code rather than as intent.

The laws, and where each one lives:

* score-anchor order is immutable      -- the search start never moves backwards
* skipped audio is allowed             -- gaps between matches become production-only spans
* skipped score anchors are findings   -- an anchor below threshold is unmatched, not forced
* section reordering is forbidden      -- monotonic search, no global assignment
* global stretch is forbidden          -- an N-bar anchor matches an N-bar window, always
* local beat normalization is allowed  -- both sides are already quantized to their own pulse
* transposition is forbidden           -- chroma is never rotated to improve a score
* ambiguity is retained                -- a near-tie is recorded, not silently resolved

The thresholds are arguments, not constants, because they are frozen elsewhere against
adverse controls. A comparator that could choose its own thresholds while looking at
the candidate is not a comparator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .anchors import ScoreAnchor, anchor_chroma
from .features import BarFeatures, chroma_matrix, onset_vector


class ComparatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Thresholds:
    """Every number the verdict depends on, in one sealable object.

    The decisive one is `min_contrast`, and it is a contrast rather than an absolute
    similarity for a reason the adverse controls exposed. A sparse chord template
    scored against dense production chroma lives in a narrow band -- on the first run
    every anchor landed between 0.62 and 0.80, so any absolute cut-off sliced through
    the middle of legitimate matches and would have been "tuned" until the preferred
    answer passed.

    What actually carries information is whether the best window stands out from the
    same file's own background. That statistic is scale-free, so it means the same
    thing for a lossy stream rip and for a lossless master, and it cannot be gamed by
    a recording that is simply louder or busier.
    """

    min_contrast: float = 2.0             # standard deviations above the file's own background
    ambiguous_contrast_margin: float = 0.5
    ambiguous_separation_bars: int = 8
    cadence_weight: float = 0.25
    rotation_margin: float = 0.02         # how much better a rotation must be to mean transposed
    minimum_windows: int = 8              # below this, contrast is not a statistic
    minimum_profile_concentration: float = 1.5   # multiples of a uniform 1/12 chroma

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_contrast": self.min_contrast,
            "ambiguous_contrast_margin": self.ambiguous_contrast_margin,
            "ambiguous_separation_bars": self.ambiguous_separation_bars,
            "cadence_weight": self.cadence_weight,
            "rotation_margin": self.rotation_margin,
            "minimum_windows": self.minimum_windows,
            "minimum_profile_concentration": self.minimum_profile_concentration,
        }


@dataclass
class AnchorMatch:
    anchor: ScoreAnchor
    decision: str
    audio_start_bar: int | None = None
    audio_end_bar: int | None = None
    audio_start_seconds: float | None = None
    audio_end_seconds: float | None = None
    harmonic: float = 0.0
    contrast: float = 0.0
    contour: float = 0.0
    rhythm: float = 0.0
    cadence: float = 0.0
    confidence: float = 0.0
    best_rotation: int = 0
    alternates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        row = self.anchor.as_dict()
        row.update({
            "matched_audio_bars": None if self.audio_start_bar is None else
            [self.audio_start_bar, self.audio_end_bar],
            "matched_audio_seconds": None if self.audio_start_seconds is None else
            [round(self.audio_start_seconds, 3), round(self.audio_end_seconds, 3)],
            "harmonic_similarity": round(self.harmonic, 4),
            "contrast_sd": round(self.contrast, 4),
            "melodic_contour_similarity": round(self.contour, 4),
            "onset_rhythm_similarity": round(self.rhythm, 4),
            "cadence_similarity": round(self.cadence, 4),
            "confidence": round(self.confidence, 4),
            "best_rotation_semitones": self.best_rotation,
            "alternate_matches": self.alternates,
            "decision": self.decision,
        })
        return row


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a.ravel() @ b.ravel() / (na * nb)) if na and nb else 0.0


def _contour(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation of the two chroma centroids' motion: a coarse melodic-shape proxy."""
    if len(a) < 2:
        return 0.0
    weights = np.arange(12)
    ca = np.diff((a * weights).sum(axis=1))
    cb = np.diff((b * weights).sum(axis=1))
    if not ca.size or not np.std(ca) or not np.std(cb):
        return 0.0
    return float(np.corrcoef(ca, cb)[0, 1])


def _window_score(template: np.ndarray, window: np.ndarray) -> tuple[float, float]:
    """Harmonic similarity and cadence similarity at rotation zero."""
    harmonic = float(np.mean([_cosine(template[i], window[i]) for i in range(len(template))]))
    return harmonic, _cosine(template[-1], window[-1])


def _rotation_evidence(template: np.ndarray, window: np.ndarray) -> tuple[int, float]:
    """Which transposition would fit best, and by how much it beats no transposition.

    Measured, never applied. A recording that matches only when transposed is not this
    score's recording. But a *nonzero best rotation is not itself evidence*: a sparse
    template scored against dense chroma will often prefer some rotation by a hair,
    which the first run mistook for transposition. Only a clear margin counts.
    """
    scores = [float(np.mean([_cosine(np.roll(template[i], shift), window[i])
                             for i in range(len(template))])) for shift in range(12)]
    best = int(np.argmax(scores))
    return best, float(scores[best] - scores[0])


def compare(anchors: Sequence[ScoreAnchor], bars: Sequence[BarFeatures], *,
            thresholds: Thresholds | None = None) -> dict[str, Any]:
    """Walk the anchors in order, never looking backwards."""
    thresholds = thresholds or Thresholds()
    if not anchors or not bars:
        raise ComparatorError("comparison needs both a score side and an audio side")

    audio_chroma = chroma_matrix(list(bars))
    audio_onsets = onset_vector(list(bars))
    matches: list[AnchorMatch] = []
    cursor = 0

    for anchor in anchors:
        template = anchor_chroma(anchor)
        span = anchor.bars
        best = (-1.0, None, 0.0, 0)
        scored: list[tuple[float, int]] = []

        for start in range(cursor, len(bars) - span + 1):
            harmonic, cadence = _window_score(template, audio_chroma[start:start + span])
            combined = (1 - thresholds.cadence_weight) * harmonic + \
                thresholds.cadence_weight * cadence
            scored.append((combined, start))
            if combined > best[0]:
                best = (combined, start, cadence, 0)

        combined, start, cadence, _ = best
        if start is None or len(scored) < thresholds.minimum_windows:
            matches.append(AnchorMatch(anchor=anchor, decision="unmatched"))
            continue

        # Contrast against this file's own background, not an absolute similarity.
        values = np.array([value for value, _ in scored], dtype=float)
        spread = float(values.std())
        contrast = (combined - float(np.median(values))) / spread if spread else 0.0

        window = audio_chroma[start:start + span]
        harmonic = float(np.mean([_cosine(template[i], window[i]) for i in range(span)]))
        contour = _contour(template, window)
        rotation, rotation_gain = _rotation_evidence(template, window)
        template_onsets = np.linspace(0, 1, span)
        actual_onsets = audio_onsets[start:start + span]
        rhythm = float(np.corrcoef(template_onsets, actual_onsets)[0, 1]) \
            if np.std(actual_onsets) else 0.0

        scored.sort(reverse=True)
        alternates = [
            {"audio_start_bar": other, "score": round(value, 4),
             "contrast_sd": round((value - float(np.median(values))) / spread, 3)
             if spread else 0.0}
            for value, other in scored[1:6]
            if (combined - value) / spread <= thresholds.ambiguous_contrast_margin
            and abs(other - start) >= thresholds.ambiguous_separation_bars] if spread else []

        # Contrast is scale-free, which also makes it blind to whether there is any
        # harmonic signal at all: flat noise produces a distribution, and a distribution
        # produces outliers. A match therefore also requires the window to have a real
        # pitch profile rather than a uniform smear.
        concentration = float(np.mean(window.max(axis=1))) * 12.0

        if concentration < thresholds.minimum_profile_concentration:
            decision = "unmatched"
        elif contrast < thresholds.min_contrast:
            decision = "unmatched"
        elif alternates:
            decision = "ambiguous"
        else:
            decision = "matched"

        match = AnchorMatch(
            anchor=anchor, decision=decision,
            audio_start_bar=start, audio_end_bar=start + span - 1,
            audio_start_seconds=bars[start].start_seconds,
            audio_end_seconds=bars[start + span - 1].end_seconds,
            harmonic=harmonic, contrast=contrast, contour=contour, rhythm=rhythm,
            cadence=cadence,
            confidence=combined,
            best_rotation=rotation if rotation_gain > thresholds.rotation_margin else 0,
            alternates=alternates)
        matches.append(match)
        if decision != "unmatched":
            cursor = start + span      # monotonic: never look back

    return _verdict(matches, bars, thresholds)


def _verdict(matches: list[AnchorMatch], bars: Sequence[BarFeatures],
             thresholds: Thresholds) -> dict[str, Any]:
    """Ten results, reported independently so one cannot carry another."""
    matched = [row for row in matches if row.decision == "matched"]
    ambiguous = [row for row in matches if row.decision == "ambiguous"]
    unmatched = [row for row in matches if row.decision == "unmatched"]
    placed = [row for row in matches if row.audio_start_bar is not None
              and row.decision != "unmatched"]

    production_only: list[dict[str, Any]] = []
    cursor = 0
    for row in placed:
        if row.audio_start_bar > cursor:
            production_only.append({
                "audio_bars": [cursor, row.audio_start_bar - 1],
                "audio_seconds": [round(bars[cursor].start_seconds, 3),
                                  round(bars[row.audio_start_bar - 1].end_seconds, 3)],
                "bars": row.audio_start_bar - cursor,
                "before_anchor": row.anchor.anchor_id})
        cursor = max(cursor, (row.audio_end_bar or 0) + 1)
    if cursor < len(bars):
        production_only.append({
            "audio_bars": [cursor, len(bars) - 1],
            "audio_seconds": [round(bars[cursor].start_seconds, 3),
                              round(bars[-1].end_seconds, 3)],
            "bars": len(bars) - cursor, "before_anchor": None})

    coda = next((row for row in matches if row.anchor.label == "Coda"), None)
    repeats = [row for row in matches if "repeat" in row.anchor.label]
    ordered = all(
        left.audio_start_bar <= right.audio_start_bar
        for left, right in zip(placed, placed[1:])) if len(placed) > 1 else True

    # An empty frontier cannot pass anything. The first run reported tonal
    # correspondence PASS for a two-semitone shift precisely because nothing matched,
    # so there was no rotation evidence to object with.
    if not placed:
        transposed = None
    else:
        transposed = any(row.best_rotation != 0 for row in placed)

    return {
        "thresholds": thresholds.as_dict(),
        "anchors": [row.as_dict() for row in matches],
        "results": {
            # Never decided here. Delivery identity is a custody fact, not an
            # acoustic one, and the comparator must not be able to grant it.
            "exact_delivery_identity": "NOT_DECIDED_BY_COMPARATOR",
            "arrangement_family_identity":
                "SUPPORTED" if len(matched) + len(ambiguous) >= 8 and not transposed
                else "UNSUPPORTED",
            "ordered_thematic_correspondence": "PASS" if ordered and len(matched) >= 6
                else "FAIL",
            "repeat_correspondence":
                "PASS" if repeats and all(row.decision != "unmatched" for row in repeats)
                else "FAIL",
            "phrase_and_cadence_correspondence":
                "PASS" if placed and float(np.mean([row.cadence for row in placed])) >= 0.6
                else "FAIL",
            "coda_correspondence":
                "PASS" if coda is not None and coda.decision == "matched" else "FAIL",
            "tonal_correspondence":
                "FAIL" if transposed is None or transposed else "PASS",
        },
        "production_only_spans": production_only,
        "ambiguous_spans": [row.anchor.anchor_id for row in ambiguous],
        "unmatched_score_anchors": [row.anchor.anchor_id for row in unmatched],
        "unmatched_audio_regions": production_only,
        "counts": {"matched": len(matched), "ambiguous": len(ambiguous),
                   "unmatched": len(unmatched), "audio_bars": len(bars)},
    }
