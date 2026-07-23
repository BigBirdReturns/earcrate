from __future__ import annotations

"""Hard musical constitution registry and proof-carrying candidate evaluation."""

from typing import Callable, Sequence

from earcrate.music.equations import music_equation_terms
from earcrate.music.law_context import MusicLawContext
from earcrate.music.law_voice import (
    music_law_temporal, music_law_pitch_range, music_law_source_grounding,
    music_law_monophony, music_law_voice_leading,
)
from earcrate.music.law_harmony import (
    music_law_obligation_discharge, music_law_harmony_tendency,
    music_law_bass_function, music_law_register_collision, music_law_phrase_closure,
)
from earcrate.music.model import (
    MusicCandidateProof, MusicError, MusicEvent, MusicLawVerdict, MusicObligation,
    MusicState, PlayerPianoProgram, music_sha256_json, music_state_with_event,
)

MUSIC_LAW_NAMES = (
    "temporal", "pitch_range", "source_grounding", "monophony", "voice_leading",
    "obligation_discharge", "harmony_tendency", "bass_function",
    "register_collision", "phrase_closure",
)

MUSIC_LAW_REGISTRY: dict[str, Callable[[MusicState, MusicEvent, MusicLawContext, PlayerPianoProgram], MusicLawVerdict]] = {
    "temporal": music_law_temporal,
    "pitch_range": music_law_pitch_range,
    "source_grounding": music_law_source_grounding,
    "monophony": music_law_monophony,
    "voice_leading": music_law_voice_leading,
    "obligation_discharge": music_law_obligation_discharge,
    "harmony_tendency": music_law_harmony_tendency,
    "bass_function": music_law_bass_function,
    "register_collision": music_law_register_collision,
    "phrase_closure": music_law_phrase_closure,
}

def music_validate_program_laws(program: PlayerPianoProgram) -> None:
    unknown = [name for name in program.law_order if name not in MUSIC_LAW_REGISTRY]
    if unknown:
        raise MusicError(f"player piano program references unknown laws: {unknown}")
    missing = [name for name in MUSIC_LAW_NAMES if name not in program.law_order]
    if missing:
        raise MusicError(f"player piano constitution omits required laws: {missing}")

def music_repetition_run(state: MusicState, event: MusicEvent) -> int:
    if event.pitch is None:
        return 0
    rows = [row for row in state.events if row.voice_id == event.voice_id and row.pitch is not None]
    run = 1
    for row in reversed(rows):
        if int(row.pitch) != int(event.pitch):
            break
        run += 1
    return run

def music_prove_candidate(
    state: MusicState,
    event: MusicEvent,
    context: MusicLawContext,
    program: PlayerPianoProgram,
    *,
    motif_intervals: Sequence[int] = (),
    motif_index: int = 0,
) -> MusicCandidateProof:
    music_validate_program_laws(program)
    verdicts = tuple(MUSIC_LAW_REGISTRY[name](state, event, context, program) for name in program.law_order)
    failures = [failure for verdict in verdicts for failure in verdict.failures]
    discharged = {value for verdict in verdicts for value in verdict.discharged_obligation_ids}
    created = [row for verdict in verdicts for row in verdict.created_obligations]
    remaining = [row for row in state.obligations if row.obligation_id not in discharged]

    same_voice_open = [row.obligation_id for row in remaining if row.voice_id == event.voice_id]
    if created and same_voice_open:
        failures.append("new_tension_before_prior_obligation_is_paid")
    if len({row.obligation_id for row in [*remaining, *created]}) != len([*remaining, *created]):
        failures.append("duplicate_obligation_identity")
    obligations_after = tuple([*remaining, *created])
    if event.end_step >= state.phrase_end_step and obligations_after:
        failures.append("phrase_ends_with_open_obligations")

    legal = not failures and all(verdict.admissible for verdict in verdicts)
    next_state = (
        music_state_with_event(state, event, obligations=obligations_after)
        if legal
        else state
    )
    discharged_rows = [row for row in state.obligations if row.obligation_id in discharged]
    urgency = max(
        (
            (event.start_step - row.created_step) / max(1.0, row.due_step - row.created_step)
            for row in discharged_rows
        ),
        default=0.0,
    )
    frame = context.frame_at(event.start_step)
    evidence = context.evidence_at(event.voice_id, event.start_step)
    target = float(context.register_targets.get(event.voice_id, context.register_targets.get(event.role, event.pitch or 60)))
    width = float(program.parameters.get("register_width", 16.0))
    terms = music_equation_terms(
        state=state,
        event=event,
        frame=frame,
        evidence=evidence,
        steps_per_beat=context.steps_per_beat,
        syncopation_preference=float(program.parameters.get("syncopation_preference", 0.5)),
        motif_intervals=motif_intervals,
        motif_index=motif_index,
        register_target=target,
        register_width=width,
        preferred_separation=int(program.parameters.get("preferred_orchestral_separation", 12)),
        discharged_count=len(discharged),
        obligation_urgency=urgency,
        creates_obligation=bool(created),
        repetition_run=music_repetition_run(state, event),
    )
    tie = music_sha256_json({"program": program.program_sha256, "state": state.state_sha256, "event": event.to_dict()})
    rank = program.rank_terms(terms, tie)
    return MusicCandidateProof(
        program_id=program.program_id,
        program_sha256=program.program_sha256,
        state_before_sha256=state.state_sha256,
        event=event,
        verdicts=verdicts,
        legal=legal,
        failures=tuple(failures),
        equation_terms=terms,
        rank_vector=rank,
        obligations_after=obligations_after,
        state_after_sha256=next_state.state_sha256,
    )

def music_commit_proof(state: MusicState, proof: MusicCandidateProof) -> MusicState:
    if not proof.legal:
        raise MusicError(f"refusing to commit illegal musical event: {list(proof.failures)}")
    if proof.state_before_sha256 != state.state_sha256:
        raise MusicError("candidate proof belongs to another musical state")
    next_state = music_state_with_event(state, proof.event, obligations=proof.obligations_after)
    if next_state.state_sha256 != proof.state_after_sha256:
        raise MusicError("candidate proof state_after_sha256 does not match committed state")
    return next_state
