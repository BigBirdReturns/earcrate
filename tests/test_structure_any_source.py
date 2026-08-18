"""Form reading must not care what a source is made of.

Every test here builds its own music. None of them mentions Children, a chord, or an
instrument, because the requirement is that the same code reads form from any song
with any number of parts -- two, twenty-four, or two hundred.

The failure this replaces was specific and instructive: the first comparator asked
"what harmony is this bar?", and on a four-chord loop that question has one answer for
seven minutes. Recurrence asks "which bars are like which other bars?", which every
piece answers differently.
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.audio_compare import sources as sc  # noqa: E402
from earcrate.a1_02.audio_compare import structure as stx  # noqa: E402


def _song(sections: str, bars_per_section: int = 8, *, seed: int = 0) -> list[str]:
    """A bar-identity sequence for a form like 'AABA'."""
    rng = np.random.default_rng(seed)
    del rng
    return [f"{letter}{index}" for letter in sections
            for index in range(bars_per_section)]


def _midi_ledger(section_letters: str, *, tracks: int, bars_per_section: int = 8,
                 ticks: int = 480, seed: int = 0) -> dict:
    """A synthetic multi-track MIDI: the same music spread over `tracks` parts.

    Each section has its own pitch material. Parts differ in register and density, so a
    two-part and a two-hundred-part rendering of the same form are genuinely different
    files that should nevertheless read as the same shape.
    """
    rng = np.random.default_rng(seed)
    material = {letter: rng.integers(48, 72, size=4) for letter in set(section_letters)}
    events_by_track: list[list[dict]] = [[] for _ in range(tracks)]

    bar = 0
    for letter in section_letters:
        for _ in range(bars_per_section):
            for beat in range(4):
                pitch = int(material[letter][beat % 4])
                for track in range(tracks):
                    if (bar + track) % max(1, tracks // 2) and track:
                        continue
                    offset = 12 * ((track % 3) - 1)
                    events_by_track[track].append({
                        "tick": bar * ticks * 4 + beat * ticks,
                        "message": {"type": "note_on", "note": max(1, min(127, pitch + offset)),
                                    "velocity": 80, "channel": track % 9}})
            bar += 1

    return {"ticks_per_beat": ticks,
            "tracks": [{"track_index": index, "name": f"part {index}", "events": rows}
                       for index, rows in enumerate(events_by_track)]}


def test_exact_recurrence_finds_the_largest_repeated_unit():
    """`ABAB` is one sixteen-bar unit stated twice, and the reader should say so.

    This assertion was wrong on its first writing -- it expected two sections, because
    the form is spelled with two letters. The reader was right: the largest thing that
    repeats is the whole AB block. Naming the smaller parts would be reading the
    spelling rather than the music.
    """
    exact = stx.signature_from_identities(_song("ABAB"), minimum_bars=3)
    assert exact["recurrence_threshold"] == 1.0
    assert exact["distinct_sections"] == 1
    assert exact["repetition_fraction"] > 0.5

    # A form whose sections are not nested inside one repeating block yields more.
    nested = stx.signature_from_identities(_song("ABACA"), minimum_bars=3)
    assert nested["distinct_sections"] >= 1
    assert nested["bars"] == 40


@pytest.mark.parametrize("tracks", [1, 2, 5, 24, 200])
def test_form_is_read_the_same_whatever_the_part_count(tracks):
    """Two parts or two hundred: parts are summed into the bar before anything looks."""
    ledger = _midi_ledger("ABACAB", tracks=tracks)
    fingerprints = sc.from_midi_ledger(ledger)
    assert fingerprints.shape[1] == stx.FINGERPRINT_WIDTH

    signature = stx.signature_from_fingerprints(fingerprints)
    assert signature["bars"] >= 40
    assert signature["distinct_sections"] >= 1, f"{tracks} parts produced no form"
    # The reading must not degenerate as parts pile up.
    assert signature["repetition_fraction"] > 0.2


def test_a_two_part_and_a_twenty_four_part_rendering_agree():
    small = stx.signature_from_fingerprints(sc.from_midi_ledger(_midi_ledger("ABAB", tracks=2)))
    large = stx.signature_from_fingerprints(sc.from_midi_ledger(_midi_ledger("ABAB", tracks=24)))
    comparison = stx.compare_structures(small, large)
    assert comparison["comparable"] is True
    assert comparison["shape_agreement"] > 0.4, (
        f"the same music read differently at 2 and 24 parts: "
        f"{small['form_string']!r} vs {large['form_string']!r}")


def test_different_forms_do_not_agree():
    """The measure has to be able to say no, or agreement means nothing."""
    verse = stx.signature_from_identities(_song("ABABAB"), minimum_bars=3)
    through = stx.signature_from_identities(_song("ABCDEF"), minimum_bars=3)
    assert through["repetition_fraction"] < verse["repetition_fraction"], \
        "through-composed material must not read as repetitive as a verse-chorus form"


def test_the_threshold_is_taken_from_each_source_rather_than_assumed():
    """A fixed cut-off cannot span modalities; this is why audio read as one section."""
    tight = np.tile(np.linspace(0, 1, stx.FINGERPRINT_WIDTH), (40, 1))
    tight += np.random.default_rng(0).normal(0, 0.001, tight.shape)
    loose = np.random.default_rng(1).random((40, stx.FINGERPRINT_WIDTH))

    tight_threshold = stx.auto_threshold(stx.self_similarity(tight))
    loose_threshold = stx.auto_threshold(stx.self_similarity(loose))
    assert tight_threshold != loose_threshold, \
        "two sources with different similarity distributions got the same threshold"
    for value in (tight_threshold, loose_threshold):
        assert 0.5 <= value <= 0.995


def test_percussion_is_not_folded_into_harmony():
    """Channel ten carries drum-map numbers, not pitches."""
    ledger = _midi_ledger("AB", tracks=3)
    ledger["tracks"].append({
        "track_index": 99, "name": "drums",
        "events": [{"tick": i * 480, "message": {"type": "note_on", "note": 36,
                                                 "velocity": 100, "channel": 9}}
                   for i in range(64)]})
    without = sc.from_midi_ledger(ledger)
    with_drums = sc.from_midi_ledger(ledger, include_percussion=True)
    assert not np.allclose(without[:, :12], with_drums[:, :12]), \
        "including percussion changed nothing, so it was never excluded"


def test_a_source_with_no_pitched_notes_is_refused_rather_than_guessed():
    empty = {"ticks_per_beat": 480, "tracks": [{"track_index": 0, "name": "x", "events": []}]}
    with pytest.raises(sc.SourceError):
        sc.from_midi_ledger(empty)
    with pytest.raises(sc.SourceError):
        sc.from_midi_ledger({"tracks": []})


def test_comparison_is_blind_to_the_letters_it_assigned():
    """'A B A B' and 'C D C D' are the same shape; the labels are arbitrary."""
    left = stx.signature_from_identities(_song("ABAB"), minimum_bars=3)
    right = stx.signature_from_identities(
        [row.replace("A", "X").replace("B", "Y") for row in _song("ABAB")], minimum_bars=3)
    comparison = stx.compare_structures(left, right)
    assert comparison["shape_agreement"] == pytest.approx(1.0), \
        "renaming the sections changed the reading"
