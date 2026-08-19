"""Compile the extraction into a performed MIDI at the printed tempo.

`score_literal_130` is what the sheet says, not how the record behaves. The measured
136 bpm of the control recording is audio-side evidence and may not reach this
compilation: letting it in would turn a measurement of a commercial performance into an
undeclared correction to the score.

The traversal is the one derived in `score_timeline`, whose navigation is the specimen
adapter's own edge set. Every performed note names the printed note it came from, so a
performed object can always be argued back to a page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import score_timeline as st

TICKS_PER_BEAT = 480
SCORE_LITERAL_TEMPO = 130.0

# Staff to channel, so the two hands stay separable downstream.
STAFF_CHANNEL = {1: 0, 2: 1}
DYNAMIC_VELOCITY = {"ppp": 24, "pp": 36, "p": 48, "mp": 62, "mf": 76,
                    "f": 92, "ff": 108, "fff": 120}
DEFAULT_VELOCITY = 72


class CompileError(RuntimeError):
    pass


def compile_performed(extraction: Mapping[str, Any], *,
                      order: Sequence[tuple[int, str]] | None = None,
                      tempo_bpm: float = SCORE_LITERAL_TEMPO,
                      enforce_score_literal_tempo: bool = True,
                      beats_per_measure: int = 4) -> dict[str, Any]:
    """Lay the printed notes out along a performed traversal.

    The traversal is an argument. It was an import once, read from A1-02's own module,
    which was correct for that lane and useless for the next score -- a traversal
    belongs to the piece, not to the compiler. A1-02's remains the default so nothing
    about that lane changes; any other score supplies its own, typically read from its
    notation by `traversal.from_score`.

    `enforce_score_literal_tempo` is likewise A1-02's rule, not a universal one: that
    lane must keep a commercial recording's measured pulse out of its score extraction.
    A score with no such control has nothing to be protected from.
    """
    if enforce_score_literal_tempo and tempo_bpm != SCORE_LITERAL_TEMPO:
        raise CompileError(
            f"score_literal_130 compiles at {SCORE_LITERAL_TEMPO} bpm. A different tempo "
            "is a child interpretation and must declare itself as one.")

    traversal = list(order) if order is not None else list(st.performed_order())
    if not traversal:
        raise CompileError("a performed traversal cannot be empty")

    by_measure: dict[int, list[Mapping[str, Any]]] = {}
    for note in extraction["notes"]:
        by_measure.setdefault(int(note["printed_measure"]), []).append(note)

    performed: list[dict[str, Any]] = []
    for occurrence, (printed, section) in enumerate(traversal):
        rows = by_measure.get(printed)
        if not rows:
            raise CompileError(f"printed measure {printed} carries no notes to perform")
        base = occurrence * beats_per_measure
        for note in rows:
            performed.append({
                "performed_occurrence": occurrence + 1,
                "section": section,
                "printed_measure": printed,
                "source_note_index": note["index"],
                "page": note["page"], "staff": note["staff"], "voice": note["voice"],
                "start_beat": base + float(note["beat_offset"]),
                "duration_beats": float(note["duration_beats"]),
                "pitch": int(note["pitch"]),
                "velocity": DYNAMIC_VELOCITY.get(note["dynamic"] or "", DEFAULT_VELOCITY),
                "tie_in": bool(note["tie_in"]), "tie_out": bool(note["tie_out"]),
            })

    performed.sort(key=lambda row: (row["start_beat"], row["staff"], row["pitch"]))
    return {
        "kind": "earcrate_a1_02_performed_score_v2",
        "schema_version": 1,
        "interpretation_id": "score_literal_130",
        "parent": None,
        "tempo_bpm": tempo_bpm,
        "tempo_source": "printed on the score, page 1",
        "reference_recording_consulted": False,
        "measured_control_pulse_used": False,
        "beats_per_measure": beats_per_measure,
        "performed_measures": len(traversal),
        "printed_measures": extraction["printed_measures_seen"],
        "performed_note_count": len(performed),
        "printed_note_count": extraction["printed_note_count"],
        "source_pdf_sha256": extraction["source_pdf_sha256"],
        "navigation": [f"{a}->{b} {kind}" for a, b, kind in st.NAVIGATION]
        if order is None else [],
        "notes": performed,
    }


def to_midi_ledger(performed: Mapping[str, Any]) -> dict[str, Any]:
    """A MIDI ledger this repository's codec can write, one track per staff."""
    beat_ticks = TICKS_PER_BEAT
    tempo_us = int(round(60_000_000 / float(performed["tempo_bpm"])))

    tracks: dict[int, list[dict[str, Any]]] = {}
    for note in performed["notes"]:
        staff = int(note["staff"])
        start = int(round(float(note["start_beat"]) * beat_ticks))
        end = start + max(1, int(round(float(note["duration_beats"]) * beat_ticks)))
        channel = STAFF_CHANNEL.get(staff, 0)
        tracks.setdefault(staff, []).extend([
            {"tick": start, "message": {"type": "note_on", "note": int(note["pitch"]),
                                        "velocity": int(note["velocity"]),
                                        "channel": channel}},
            {"tick": end, "message": {"type": "note_off", "note": int(note["pitch"]),
                                      "velocity": 0, "channel": channel}},
        ])

    def numbered(events: list[dict[str, Any]], *, meta: bool) -> list[dict[str, Any]]:
        """The ledger schema wants an explicit order alongside the tick.

        Two events can share a tick, and the file must record which one the writer
        meant to come first rather than leaving it to a sort's stability.
        """
        rows = []
        for position, event in enumerate(events):
            rows.append({"tick": event["tick"], "order": position,
                         "is_meta": meta if isinstance(meta, bool)
                         else event["message"]["type"] not in ("note_on", "note_off"),
                         "message": event["message"]})
        return rows

    header = [
        {"tick": 0, "message": {"type": "set_tempo", "tempo": tempo_us}},
        {"tick": 0, "message": {"type": "time_signature",
                                "numerator": int(performed["beats_per_measure"]),
                                "denominator": 4}},
        {"tick": 0, "message": {"type": "key_signature", "key": "Fm"}},
        {"tick": 0, "message": {"type": "end_of_track"}},
    ]
    ledger_tracks = [{"track_index": 0, "name": "score_literal_130",
                      "events": numbered(header, meta=True)}]
    for index, (staff, events) in enumerate(sorted(tracks.items()), start=1):
        # Note-off before note-on at a shared tick, so a repeated pitch retriggers.
        events.sort(key=lambda row: (row["tick"], row["message"]["type"] == "note_on",
                                     row["message"]["note"]))
        last = events[-1]["tick"] if events else 0
        events = events + [{"tick": last, "message": {"type": "end_of_track"}}]
        ledger_tracks.append({
            "track_index": index,
            "name": "Right Hand" if staff == 1 else "Left Hand",
            "events": numbered(events, meta=None)})

    from ...midi.model import midi_compute_semantic_sha256

    ledger = {"kind": "earcrate_midi_ledger", "schema_version": 1, "midi_type": 1,
              "ticks_per_beat": beat_ticks, "tracks": ledger_tracks}
    ledger["semantic_sha256"] = midi_compute_semantic_sha256(ledger)
    return ledger


def semantic_identity(ledger: Mapping[str, Any]) -> str:
    """What sounds, identified by this repository's own MIDI semantics.

    Delegated rather than reimplemented. A second semantic digest living beside the
    canonical one is the drift this codebase keeps gating against, and it would be a
    poor place to introduce it -- the whole point of the field is that two objects
    agree about it.
    """
    from ...midi.model import midi_compute_semantic_sha256

    return midi_compute_semantic_sha256(ledger)
