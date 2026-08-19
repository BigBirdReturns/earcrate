"""What a performance actually asks of an instrument.

The demand is compiled from the accepted performed score before any rack is chosen,
so a rack is selected against a stated requirement rather than a requirement being
written to fit whatever rack was available. That ordering is the whole point: a
coverage claim made after the fact tends to describe the sample library.

Every selected note appears here by identity. A rack that cannot place one of them
refuses; it does not substitute, transpose further than declared, or fall back to a
General MIDI approximation, because each of those quietly answers a different musical
question than the one this lane is asking.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

MIDI_LOWEST, MIDI_HIGHEST = 21, 108          # a full 88-key piano


class DemandError(RuntimeError):
    pass


def compile_demand(performed: Mapping[str, Any]) -> dict[str, Any]:
    """State the instrument requirement implied by a performed score."""
    notes = performed.get("notes") or ()
    if not notes:
        raise DemandError("a performance with no notes demands nothing")

    pitches = [int(row["pitch"]) for row in notes]
    velocities = [int(row["velocity"]) for row in notes]
    durations = [float(row["duration_beats"]) for row in notes]
    seconds_per_beat = 60.0 / float(performed["tempo_bpm"])

    # Maximum simultaneity, from a sweep over note starts and ends.
    edges: list[tuple[float, int]] = []
    for row in notes:
        start = float(row["start_beat"])
        edges.append((start, 1))
        edges.append((start + float(row["duration_beats"]), -1))
    edges.sort()
    polyphony = running = 0
    for _, delta in edges:
        running += delta
        polyphony = max(polyphony, running)

    by_staff = Counter(int(row["staff"]) for row in notes)
    by_voice = Counter(str(row["voice"]) for row in notes)
    velocity_histogram = Counter(velocities)

    return {
        "kind": "earcrate_a1_02_performance_demand",
        "schema_version": 1,
        "interpretation_id": performed.get("interpretation_id"),
        "tempo_bpm": performed["tempo_bpm"],
        "selected_event_count": len(notes),
        "pitch_range": [min(pitches), max(pitches)],
        "distinct_pitches": sorted(set(pitches)),
        "pitch_histogram": {str(pitch): count
                            for pitch, count in sorted(Counter(pitches).items())},
        "velocity_range": [min(velocities), max(velocities)],
        "velocity_histogram": {str(velocity): count
                               for velocity, count in sorted(velocity_histogram.items())},
        "distinct_velocities": sorted(set(velocities)),
        "maximum_polyphony": polyphony,
        "duration_beats": {"min": min(durations), "max": max(durations),
                           "distinct": sorted(set(durations))},
        "duration_seconds": {"min": round(min(durations) * seconds_per_beat, 6),
                             "max": round(max(durations) * seconds_per_beat, 6)},
        "release_requirement_seconds": round(max(durations) * seconds_per_beat, 6),
        "sustain_required": any(row.get("tie_out") for row in notes),
        "staff_organization": {str(staff): count for staff, count in sorted(by_staff.items())},
        "voice_organization": dict(sorted(by_voice.items())),
        "instrument_policy": {
            "one_coherent_instrument": True,
            "collage_forbidden": (
                "coverage may not be satisfied by assembling piano fragments from "
                "unrelated recordings; this lane tests performed interpretation, and a "
                "timbrally incoherent rack would introduce a separate arrangement and "
                "source-selection problem"),
            "general_midi_fallback_forbidden": True,
            "silent_substitution_forbidden": True,
        },
        "selected_event_identities": [
            {"index": index, "printed_measure": row["printed_measure"],
             "performed_occurrence": row["performed_occurrence"],
             "staff": row["staff"], "voice": row["voice"], "pitch": row["pitch"],
             "velocity": row["velocity"], "start_beat": row["start_beat"],
             "duration_beats": row["duration_beats"]}
            for index, row in enumerate(notes)],
    }


def within_piano_range(demand: Mapping[str, Any]) -> list[str]:
    low, high = demand["pitch_range"]
    problems = []
    if low < MIDI_LOWEST:
        problems.append(f"lowest pitch {low} is below an 88-key piano")
    if high > MIDI_HIGHEST:
        problems.append(f"highest pitch {high} is above an 88-key piano")
    return problems
