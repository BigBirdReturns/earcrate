"""Play the notes, rather than trigger them.

The flat render sounds like a sequencer because it is one: every note gets its printed
length and its section's velocity, and nothing knows which note is the tune. This
shapes the same 1,253 events into a performance, and changes nothing about which
events they are.

What it may change: velocity, sounding length, and micro-timing. What it may not:
pitch, note count, order, staff, voice, printed measure, performed occurrence, or the
traversal. That boundary is the point -- an expressive reading is an interpretation of
this score, and a reading that quietly drops or moves a note is a different score.

Everything here is a concrete decision about this piece, deliberately not a framework:

* the top sounding note of the right hand is the tune and is voiced above its own
  accompaniment;
* a phrase rises toward roughly its two-thirds point and eases after it;
* the last bar of a section relaxes in time and in level, because a cadence that
  arrives at full speed does not sound like an arrival;
* the sustain pedal is held through a harmony and lifted when it changes, which for
  this score means the bar line;
* notes not under the pedal detach slightly, so repeated chords articulate.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# Velocity shaping, in MIDI units. Small on purpose: the rack has sixteen velocity
# layers and large jumps step between them audibly.
MELODY_LIFT = 14
INNER_VOICE_DROP = 10
BASS_DROP = 6
PHRASE_ARC = 8
CADENCE_SOFTEN = 12

PHRASE_PEAK = 0.66          # where a phrase leans, as a fraction of its length
CADENCE_BARS = 1            # how much of a section's end relaxes
CADENCE_STRETCH = 0.16      # beats of delay added across the closing bar
PEDAL_OVERHANG_BEATS = 0.9  # how far a pedalled note rings past its written length
DETACH_FRACTION = 0.88      # written length actually sounded when unpedalled

INVARIANT = ("pitch", "printed_measure", "performed_occurrence", "staff", "voice",
             "source_note_index", "section")


class ExpressionError(RuntimeError):
    pass


def _sections(notes: Sequence[Mapping[str, Any]]) -> dict[str, tuple[float, float]]:
    spans: dict[str, tuple[float, float]] = {}
    for note in notes:
        label = str(note.get("section") or "-")
        start = float(note["start_beat"])
        end = start + float(note["duration_beats"])
        if label not in spans:
            spans[label] = (start, end)
            continue
        low, high = spans[label]
        spans[label] = (min(low, start), max(high, end))
    return spans


def _melody_pitches(notes: Sequence[Mapping[str, Any]]) -> set[int]:
    """Indices of notes that are the top of the right hand at their own onset."""
    by_onset: dict[tuple[float, int], list[int]] = {}
    for index, note in enumerate(notes):
        if int(note["staff"]) != 1:
            continue
        by_onset.setdefault((float(note["start_beat"]), 1), []).append(index)
    top: set[int] = set()
    for indices in by_onset.values():
        top.add(max(indices, key=lambda i: int(notes[i]["pitch"])))
    return top


def shape(performed: Mapping[str, Any], *, beats_per_measure: int = 4) -> dict[str, Any]:
    """Return the same performance, played."""
    notes = list(performed.get("notes") or ())
    if not notes:
        raise ExpressionError("nothing to shape")

    spans = _sections(notes)
    melody = _melody_pitches(notes)
    shaped: list[dict[str, Any]] = []
    decisions = {"melody_notes": 0, "inner_voice_notes": 0, "bass_notes": 0,
                 "cadence_notes": 0, "pedalled_notes": 0, "detached_notes": 0}

    for index, note in enumerate(notes):
        row = dict(note)
        start = float(note["start_beat"])
        length = float(note["duration_beats"])
        velocity = int(note["velocity"])
        staff = int(note["staff"])
        label = str(note.get("section") or "-")
        low, high = spans.get(label, (start, start + length))
        span = max(1e-6, high - low)
        position = (start - low) / span

        # Voicing: the tune sits above its own accompaniment, the left hand below both.
        if index in melody:
            velocity += MELODY_LIFT
            decisions["melody_notes"] += 1
        elif staff == 1:
            velocity -= INNER_VOICE_DROP
            decisions["inner_voice_notes"] += 1
        else:
            velocity -= BASS_DROP
            decisions["bass_notes"] += 1

        # Phrase arc: lean toward the two-thirds point, ease after it.
        arc = 1.0 - abs(position - PHRASE_PEAK) / max(PHRASE_PEAK, 1 - PHRASE_PEAK)
        velocity += int(round(PHRASE_ARC * arc))

        # Cadence: the closing bar of a section relaxes in level and in time.
        closing = high - CADENCE_BARS * beats_per_measure
        delay = 0.0
        if start >= closing:
            velocity -= CADENCE_SOFTEN
            decisions["cadence_notes"] += 1
            through = min(1.0, (start - closing) / (beats_per_measure or 1))
            delay = CADENCE_STRETCH * through

        # Pedal: held through a harmony, lifted at the bar line. Notes inside a
        # pedalled bar ring on; notes that would cross the lift detach instead.
        bar_end = (int(start // beats_per_measure) + 1) * beats_per_measure
        if start + length <= bar_end:
            sounded = min(length + PEDAL_OVERHANG_BEATS, bar_end - start)
            decisions["pedalled_notes"] += 1
        else:
            sounded = length * DETACH_FRACTION
            decisions["detached_notes"] += 1

        row["velocity"] = max(1, min(127, velocity))
        row["duration_beats"] = round(max(0.05, sounded), 6)
        row["start_beat"] = round(start + delay, 6)
        row["expression"] = {
            "role": "melody" if index in melody else ("inner" if staff == 1 else "bass"),
            "phrase_position": round(position, 4),
            "cadence": start >= closing,
            "pedalled": start + length <= bar_end,
            "timing_delay_beats": round(delay, 6),
            "velocity_delta": row["velocity"] - int(note["velocity"]),
        }
        shaped.append(row)

    return {
        **{key: value for key, value in performed.items() if key != "notes"},
        "kind": "earcrate_a1_02_expressive_performance",
        "interpretation_id": "expressive_performance_136",
        "parent": performed.get("interpretation_id"),
        "shaping": {
            "voicing_hierarchy": "top right-hand note at each onset is the tune",
            "melody_lift": MELODY_LIFT, "inner_voice_drop": INNER_VOICE_DROP,
            "bass_drop": BASS_DROP, "phrase_arc": PHRASE_ARC,
            "phrase_peak": PHRASE_PEAK, "cadence_soften": CADENCE_SOFTEN,
            "cadence_stretch_beats": CADENCE_STRETCH,
            "pedal_overhang_beats": PEDAL_OVERHANG_BEATS,
            "detach_fraction": DETACH_FRACTION,
            "counts": decisions,
        },
        "may_change": ["velocity", "sounding length", "micro-timing"],
        "may_not_change": list(INVARIANT) + ["note count", "order", "traversal"],
        "notes": shaped,
    }


def validate(shaped: Mapping[str, Any], flat: Mapping[str, Any]) -> list[str]:
    """Findings: what an expressive reading changed that it may not change."""
    problems: list[str] = []
    before, after = list(flat["notes"]), list(shaped.get("notes") or ())

    if len(before) != len(after):
        return [f"note count changed: {len(before)} -> {len(after)}"]

    for index, (was, now) in enumerate(zip(before, after)):
        for field in INVARIANT:
            if was.get(field) != now.get(field):
                problems.append(
                    f"note {index} changed {field}: {was.get(field)!r} -> {now.get(field)!r}")

    if [row["pitch"] for row in before] != [row["pitch"] for row in after]:
        problems.append("the pitch sequence changed")
    if shaped.get("tempo_bpm") != flat.get("tempo_bpm"):
        problems.append("an expressive reading may not also re-time; that is a child")
    if shaped.get("navigation") != flat.get("navigation"):
        problems.append("the traversal changed")

    # Micro-timing may bend, not reorder.
    starts = [float(row["start_beat"]) for row in after]
    if starts != sorted(starts):
        problems.append("micro-timing reordered the performance")
    for index, (was, now) in enumerate(zip(before, after)):
        drift = abs(float(now["start_beat"]) - float(was["start_beat"]))
        if drift > 1.0:
            problems.append(f"note {index} moved {drift:.3f} beats; that is rewriting")
    return problems
