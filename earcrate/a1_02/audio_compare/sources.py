"""Any source, reduced to the same bar fingerprints.

Two tracks or two hundred: parts are summed into the bar before anything examines
them, so track count never reaches the comparison. A piano reduction and a full
production of the same music produce fingerprint sequences that can be compared
directly, because both describe what is sounding per bar rather than what is playing
it.

The two readers here are audio and MIDI. A third source kind only has to produce the
same fingerprints to join.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .structure import FINGERPRINT_WIDTH, fingerprint


class SourceError(RuntimeError):
    pass


def from_audio_bars(bars: Sequence[Any]) -> np.ndarray:
    """Bar features from `features.bar_features` become fingerprints.

    Register is estimated from the chroma-weighted spectrum's own shape, because audio
    does not hand over note numbers. It is a coarse stand-in and is treated as one
    weak feature among fifteen rather than as pitch information.
    """
    rows = []
    for bar in bars:
        chroma = np.asarray(bar.chroma, dtype=float)
        weights = np.arange(12)
        total = chroma.sum() or 1.0
        centre = float((chroma * weights).sum() / total) / 11.0
        spread = float(np.sqrt(((weights - centre * 11) ** 2 * chroma).sum() / total)) / 6.0
        rows.append(fingerprint(chroma, bar.onset_density, centre, min(spread, 1.0)))
    if not rows:
        raise SourceError("no bars to fingerprint")
    return np.vstack(rows)


def from_midi_ledger(ledger: Mapping[str, Any], *, beats_per_bar: int = 4,
                     include_percussion: bool = False) -> np.ndarray:
    """Any MIDI, any number of tracks, reduced per bar.

    Percussion is excluded by default: channel 10 carries drum-map numbers rather than
    pitches, so folding it into a pitch-class histogram would describe a kit as
    harmony. Its density still reaches the fingerprint through the note count of the
    pitched parts around it, and a caller that wants it can ask.
    """
    ticks = int(ledger.get("ticks_per_beat") or 0)
    if ticks <= 0:
        raise SourceError("the MIDI ledger declares no ticks_per_beat")
    bar_ticks = ticks * beats_per_bar

    notes: list[tuple[int, int, int]] = []      # (bar, pitch, velocity)
    for track in ledger.get("tracks") or []:
        for event in track.get("events") or []:
            message = event.get("message") or {}
            if message.get("type") != "note_on" or int(message.get("velocity") or 0) <= 0:
                continue
            if not include_percussion and int(message.get("channel", -1)) == 9:
                continue
            notes.append((int(event["tick"]) // bar_ticks, int(message["note"]),
                          int(message["velocity"])))
    if not notes:
        raise SourceError("the MIDI ledger carries no pitched notes")

    last_bar = max(bar for bar, _, _ in notes)
    per_bar: list[list[tuple[int, int]]] = [[] for _ in range(last_bar + 1)]
    for bar, pitch, velocity in notes:
        per_bar[bar].append((pitch, velocity))

    densities = [len(rows) for rows in per_bar]
    ceiling = max(densities) or 1

    rows = []
    for entries in per_bar:
        classes = np.zeros(12, dtype=float)
        for pitch, velocity in entries:
            classes[pitch % 12] += velocity
        pitches = [pitch for pitch, _ in entries]
        centre = (float(np.mean(pitches)) - 21) / 87 if pitches else 0.0
        spread = float(np.std(pitches)) / 24 if len(pitches) > 1 else 0.0
        rows.append(fingerprint(classes, len(entries) / ceiling,
                                min(max(centre, 0.0), 1.0), min(spread, 1.0)))
    return np.vstack(rows)


def from_midi_file(path: Path, **kwargs: Any) -> np.ndarray:
    from ...midi.codec import midi_read

    return from_midi_ledger(midi_read(str(path)), **kwargs)


def describe_source(fingerprints: np.ndarray) -> dict[str, Any]:
    """What a source looks like once its parts stopped mattering."""
    if fingerprints.shape[1] != FINGERPRINT_WIDTH:
        raise SourceError("not a fingerprint matrix")
    return {
        "bars": int(len(fingerprints)),
        "mean_density": round(float(fingerprints[:, 12].mean()), 4),
        "mean_register_centre": round(float(fingerprints[:, 13].mean()), 4),
        "mean_register_spread": round(float(fingerprints[:, 14].mean()), 4),
        "pitch_class_entropy": round(float(_entropy(fingerprints[:, :12].mean(axis=0))), 4),
    }


def _entropy(distribution: np.ndarray) -> float:
    total = distribution.sum()
    if total <= 0:
        return 0.0
    p = distribution / total
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())
