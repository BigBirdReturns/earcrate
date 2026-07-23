from __future__ import annotations

"""Voice-level hard laws: time, range, evidence, monophony, and melodic motion."""

from typing import Any, Mapping

from earcrate.music.equations import music_equation_source_evidence
from earcrate.music.law_context import MusicLawContext
from earcrate.music.model import MusicEvent, MusicLawVerdict, MusicState, PlayerPianoProgram

def music_law_ok(law_id: str, *, facts: Mapping[str, Any] | None = None, **kwargs: Any) -> MusicLawVerdict:
    return MusicLawVerdict(law_id=law_id, admissible=True, facts=facts or {}, **kwargs)

def music_law_fail(law_id: str, *failures: str, facts: Mapping[str, Any] | None = None) -> MusicLawVerdict:
    return MusicLawVerdict(law_id=law_id, admissible=False, failures=tuple(failures), facts=facts or {})

def music_law_is_strong_step(event: MusicEvent, context: MusicLawContext) -> bool:
    return int(event.start_step) % int(context.steps_per_beat) == 0

def music_law_temporal(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del context, program
    if event.start_step < state.phrase_start_step:
        return music_law_fail("temporal", "event_before_phrase")
    if event.end_step > state.phrase_end_step:
        return music_law_fail("temporal", "event_after_phrase")
    if event.start_step < state.current_step and not bool(event.metadata.get("allow_backfill")):
        return music_law_fail("temporal", "event_before_current_state")
    return music_law_ok("temporal", facts={"end_step": event.end_step})

def music_law_pitch_range(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del state, program
    if event.pitch is None:
        if event.role not in {"percussion", "drums", "fx", "sample_trigger", "silence"}:
            return music_law_fail("pitch_range", "pitched_role_has_no_pitch")
        return music_law_ok("pitch_range", facts={"unpitched": True})
    low, high = context.range_for(event.role)
    if not int(low) <= int(event.pitch) <= int(high):
        return music_law_fail("pitch_range", "pitch_outside_role_range", facts={"low": low, "high": high})
    return music_law_ok("pitch_range", facts={"low": low, "high": high})

def music_law_source_grounding(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del state
    threshold = float(program.parameters.get("minimum_source_evidence", 0.0))
    evidence = context.evidence_at(event.voice_id, event.start_step)
    value = music_equation_source_evidence(event.pitch, evidence)
    exempt = event.function in set(program.parameters.get("source_grounding_exempt_functions", ["rest", "hold", "cadence_repair"]))
    if threshold > 0.0 and not exempt and value + 1e-12 < threshold:
        return music_law_fail(
            "source_grounding",
            "candidate_not_supported_by_source_evidence",
            facts={"evidence": value, "threshold": threshold},
        )
    return music_law_ok("source_grounding", facts={"evidence": value, "threshold": threshold, "exempt": exempt})

def music_law_monophony(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del context, program
    overlaps = [
        row.event_id
        for row in state.events
        if row.voice_id == event.voice_id
        and max(int(row.start_step), int(event.start_step)) < min(int(row.end_step), int(event.end_step))
    ]
    if overlaps:
        return music_law_fail("monophony", "same_voice_overlap", facts={"overlaps": overlaps})
    return music_law_ok("monophony")

def music_law_voice_leading(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del context
    previous = state.last_event(event.voice_id)
    if previous is None or previous.pitch is None or event.pitch is None:
        return music_law_ok("voice_leading", facts={"motion": 0, "first_event": previous is None})
    motion = abs(int(event.pitch) - int(previous.pitch))
    by_role = program.parameters.get("maximum_leap_by_role") or {}
    maximum = int(by_role.get(event.role, by_role.get("default", 12)))
    rupture_ops = set(program.parameters.get("register_rupture_operators") or [])
    restart = bool(event.metadata.get("phrase_restart"))
    if motion > maximum and not (restart and event.operator in rupture_ops):
        return music_law_fail(
            "voice_leading",
            "unlicensed_excessive_leap",
            facts={"motion": motion, "maximum": maximum, "operator": event.operator, "phrase_restart": restart},
        )
    return music_law_ok("voice_leading", facts={"motion": motion, "maximum": maximum, "licensed_rupture": motion > maximum})
