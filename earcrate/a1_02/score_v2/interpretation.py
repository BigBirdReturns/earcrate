"""A child interpretation may re-time a sealed score. It may not edit one.

The control recording measures 136 bpm and the sheet prints 130. Both are true, about
different objects, and the danger is that the recording's pulse quietly becomes a
correction to the score. So a faster reading is not an alternative extraction: it is a
child of a sealed parent, and it may change only the tempo map and the timestamps
derived from it.

Everything that makes the object the same music must survive: every note, its duration
in musical beats, its voice, its ties, its dynamic, its staff, and the printed-measure
traversal with its repeats, endings, D.S., To Coda and coda. The refusal is written
against those fields directly rather than against a digest, so a rejection can say
which note moved.
"""

from __future__ import annotations

from typing import Any, Mapping

PARENT_ID = "score_literal_130"

# What a child is allowed to differ in. Everything else is an edit, not a reading.
# `kind` is here because a child genuinely is a different kind of object and says so;
# it is not a musical field, and refusing it would only force the child to lie about
# what it is.
MUTABLE = frozenset({"kind", "tempo_bpm", "interpretation_id", "parent", "tempo_source",
                     "start_seconds", "end_seconds", "notes"})

# Per-note fields a child must preserve exactly. `start_beat` is here on purpose:
# musical position does not change when the clock does.
INVARIANT_NOTE_FIELDS = ("source_note_index", "printed_measure", "performed_occurrence",
                         "section", "page", "staff", "voice", "start_beat",
                         "duration_beats", "pitch", "velocity", "tie_in", "tie_out")


class InterpretationError(RuntimeError):
    pass


def derive_child(parent: Mapping[str, Any], *, tempo_bpm: float,
                 interpretation_id: str, rationale: str) -> dict[str, Any]:
    """Re-time a sealed parent, changing nothing else."""
    if parent.get("interpretation_id") != PARENT_ID:
        raise InterpretationError(
            f"a child derives from {PARENT_ID}, not {parent.get('interpretation_id')!r}")
    if parent.get("parent") is not None:
        raise InterpretationError("a child may not derive from another child")
    if float(tempo_bpm) <= 0:
        raise InterpretationError(f"tempo must be positive: {tempo_bpm}")
    if not rationale.strip():
        raise InterpretationError(
            "a child must say why it re-times its parent; an unexplained tempo is a "
            "correction wearing an interpretation's clothes")

    seconds_per_beat = 60.0 / float(tempo_bpm)
    child = dict(parent)
    child.update({
        "kind": "earcrate_a1_02_performed_score_v2_child",
        "interpretation_id": interpretation_id,
        "parent": PARENT_ID,
        "parent_tempo_bpm": parent["tempo_bpm"],
        "tempo_bpm": float(tempo_bpm),
        "tempo_source": rationale,
        "notes": [
            {**note,
             "start_seconds": round(float(note["start_beat"]) * seconds_per_beat, 6),
             "end_seconds": round((float(note["start_beat"]) +
                                   float(note["duration_beats"])) * seconds_per_beat, 6)}
            for note in parent["notes"]],
    })
    return child


def validate_child(child: Mapping[str, Any], parent: Mapping[str, Any]) -> list[str]:
    """Findings: exactly what a child changed that it was not allowed to change."""
    problems: list[str] = []

    if child.get("parent") != PARENT_ID:
        problems.append(f"child names parent {child.get('parent')!r}, expected {PARENT_ID}")
    if float(child.get("tempo_bpm") or 0) == float(parent["tempo_bpm"]):
        problems.append("a child that does not re-time is not an interpretation")

    for field, value in parent.items():
        if field in MUTABLE:
            continue
        if child.get(field) != value:
            problems.append(f"{field} differs from the sealed parent")

    parent_notes, child_notes = parent["notes"], child.get("notes") or []
    if len(parent_notes) != len(child_notes):
        problems.append(
            f"note count changed: parent {len(parent_notes)}, child {len(child_notes)}")
        return problems

    for index, (before, after) in enumerate(zip(parent_notes, child_notes)):
        for field in INVARIANT_NOTE_FIELDS:
            if before.get(field) != after.get(field):
                problems.append(
                    f"note {index} ({before.get('printed_measure')}:{before.get('pitch')}) "
                    f"changed {field}: {before.get(field)!r} -> {after.get(field)!r}")
    return problems


def may_seal_child(parent_sealed: bool, child: Mapping[str, Any],
                   parent: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """The ordering rule, stated once: the parent is sealed first or there is no child."""
    if not parent_sealed:
        return False, [f"{PARENT_ID} is not sealed; a child interpretation cannot precede it"]
    problems = validate_child(child, parent)
    return (not problems), problems
