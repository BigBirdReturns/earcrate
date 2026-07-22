from __future__ import annotations

"""Composable player pianos built from laws, equations, and operator topology."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from earcrate.music.laws import MusicLawContext, music_commit_proof, music_prove_candidate
from earcrate.music.model import (
    MusicCandidateProof,
    MusicError,
    MusicEvent,
    MusicNoLegalContinuation,
    MusicObjectiveStage,
    MusicState,
    PlayerPianoProgram,
    music_frozen_mapping,
    music_make_event,
    music_sha256_json,
)


@dataclass(frozen=True)
class MusicVoicePlan:
    voice_id: str
    role: str
    onset_steps: tuple[int, ...]
    duration_steps: int
    pitch_pool: tuple[int | None, ...]
    velocity: int = 96
    function: str = "statement"
    operator_candidates: tuple[str, ...] = ("state",)
    motif_intervals: tuple[int, ...] = ()
    source_ids: tuple[str, ...] = ()
    allow_rest: bool = False
    terminal_duration_steps: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.voice_id) or not str(self.role):
            raise MusicError("voice plan requires voice_id and role")
        onsets = tuple(sorted({int(value) for value in self.onset_steps}))
        if not onsets:
            raise MusicError("voice plan requires onset_steps")
        if int(self.duration_steps) <= 0:
            raise MusicError("voice plan duration_steps must be positive")
        if not self.pitch_pool:
            raise MusicError("voice plan requires a pitch pool")
        pitches = tuple(None if value is None else int(value) for value in self.pitch_pool)
        if any(value is not None and not 0 <= int(value) <= 127 for value in pitches):
            raise MusicError("voice plan pitch pool must lie in MIDI range")
        if not self.operator_candidates:
            raise MusicError("voice plan requires operator candidates")
        object.__setattr__(self, "onset_steps", onsets)
        object.__setattr__(self, "pitch_pool", pitches)
        object.__setattr__(self, "operator_candidates", tuple(str(value) for value in self.operator_candidates))
        object.__setattr__(self, "motif_intervals", tuple(int(value) for value in self.motif_intervals))
        object.__setattr__(self, "source_ids", tuple(str(value) for value in self.source_ids))
        object.__setattr__(self, "metadata", music_frozen_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "role": self.role,
            "onset_steps": list(self.onset_steps),
            "duration_steps": int(self.duration_steps),
            "pitch_pool": list(self.pitch_pool),
            "velocity": int(self.velocity),
            "function": self.function,
            "operator_candidates": list(self.operator_candidates),
            "motif_intervals": list(self.motif_intervals),
            "source_ids": list(self.source_ids),
            "allow_rest": bool(self.allow_rest),
            "terminal_duration_steps": int(self.terminal_duration_steps),
            "metadata": music_frozen_mapping(self.metadata),
        }


@dataclass(frozen=True)
class MusicCompositionResult:
    program: PlayerPianoProgram
    context_sha256: str
    initial_state_sha256: str
    final_state: MusicState
    voice_plans: tuple[MusicVoicePlan, ...]
    proofs: tuple[MusicCandidateProof, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "kind": "earcrate_player_piano_composition",
            "schema_version": 1,
            "program": self.program.to_dict(),
            "program_sha256": self.program.program_sha256,
            "context_sha256": self.context_sha256,
            "initial_state_sha256": self.initial_state_sha256,
            "final_state": self.final_state.to_dict(),
            "voice_plans": [row.to_dict() for row in self.voice_plans],
            "proofs": [row.to_dict() for row in self.proofs],
            "open_obligation_count": len(self.final_state.obligations),
        }
        payload["composition_sha256"] = music_sha256_json(payload)
        return payload

    @property
    def composition_sha256(self) -> str:
        return self.to_dict()["composition_sha256"]


@dataclass(frozen=True)
class _MusicBeamNode:
    state: MusicState
    proofs: tuple[MusicCandidateProof, ...]
    cumulative_rank: tuple[float, ...]


def music_program_common_law_order() -> tuple[str, ...]:
    return (
        "temporal",
        "pitch_range",
        "source_grounding",
        "monophony",
        "voice_leading",
        "obligation_discharge",
        "harmony_tendency",
        "bass_function",
        "register_collision",
        "phrase_closure",
    )


def music_conservatory_player_piano(*, voice_order: Sequence[str] = ()) -> PlayerPianoProgram:
    return PlayerPianoProgram(
        program_id="conservatory_v1",
        name="Conservatory player piano",
        version=1,
        law_order=music_program_common_law_order(),
        objective_stages=(
            MusicObjectiveStage("truth", {"source_evidence": 0.68, "harmonic_stability": 0.32}),
            MusicObjectiveStage("closure", {"resolution_value": 0.70, "repetition_control": 0.30}),
            MusicObjectiveStage(
                "craft",
                {"voice_leading": 0.38, "metrical_weight": 0.22, "motif_identity": 0.20, "register_fit": 0.20},
            ),
            MusicObjectiveStage(
                "orchestration",
                {"orchestral_separation": 0.48, "novelty": 0.18, "tension_color": -0.18, "harmonic_stability": 0.16},
            ),
        ),
        voice_order=tuple(str(value) for value in voice_order),
        operator_order=("state", "pass", "neighbor", "cadence_repair"),
        parameters={
            "minimum_source_evidence": 0.18,
            "source_grounding_exempt_functions": ["rest", "hold", "cadence_repair"],
            "maximum_leap_by_role": {"bass": 7, "sub_bass": 7, "lead": 7, "harmony": 5, "default": 7},
            "register_rupture_operators": [],
            "resolution_window_steps": 2,
            "resolution_max_motion": 2,
            "allow_leading_tone": True,
            "allow_scale_degree_four": True,
            "allow_passing_tones": True,
            "allow_chromatic_approach": False,
            "allow_strong_tension": False,
            "extensions_create_obligation": True,
            "allow_bass_approach": False,
            "bass_roles": ["bass", "sub_bass", "bass_articulation"],
            "minimum_register_separation": 2,
            "preferred_orchestral_separation": 10,
            "doubling_operators": ["double", "octave_double"],
            "open_ending_functions": [],
            "syncopation_preference": 0.36,
            "register_width": 12.0,
        },
    )


def music_electro_soul_player_piano(*, voice_order: Sequence[str] = ()) -> PlayerPianoProgram:
    return PlayerPianoProgram(
        program_id="electro_soul_wide_v1",
        name="Wide electro-soul player piano",
        version=1,
        law_order=music_program_common_law_order(),
        objective_stages=(
            MusicObjectiveStage("identity", {"source_evidence": 0.72, "motif_identity": 0.28}),
            MusicObjectiveStage(
                "charge_and_release",
                {"tension_color": 0.30, "resolution_value": 0.35, "metrical_weight": 0.20, "harmonic_stability": 0.15},
            ),
            MusicObjectiveStage(
                "orchestral_shape",
                {"orchestral_separation": 0.32, "novelty": 0.30, "register_fit": 0.16, "repetition_control": 0.22},
            ),
            MusicObjectiveStage("craft", {"voice_leading": 0.32, "harmonic_stability": 0.68}),
        ),
        voice_order=tuple(str(value) for value in voice_order),
        operator_order=("state", "pass", "chromatic_approach", "register_rupture", "octave_double", "cadence_repair"),
        parameters={
            "minimum_source_evidence": 0.18,
            "source_grounding_exempt_functions": ["rest", "hold", "cadence_repair"],
            "maximum_leap_by_role": {"bass": 12, "sub_bass": 12, "lead": 9, "harmony": 7, "default": 9},
            "register_rupture_operators": ["register_rupture"],
            "resolution_window_steps": 4,
            "resolution_max_motion": 2,
            "allow_leading_tone": True,
            "allow_scale_degree_four": True,
            "allow_passing_tones": True,
            "allow_chromatic_approach": True,
            "allow_strong_tension": True,
            "extensions_create_obligation": True,
            "allow_bass_approach": True,
            "bass_roles": ["bass", "sub_bass", "bass_articulation"],
            "minimum_register_separation": 1,
            "preferred_orchestral_separation": 14,
            "doubling_operators": ["double", "octave_double"],
            "open_ending_functions": ["open_cadence"],
            "syncopation_preference": 0.74,
            "register_width": 20.0,
        },
    )


def music_context_payload(context: MusicLawContext) -> dict[str, Any]:
    return {
        "harmony_frames": [frame.to_dict() for frame in context.harmony_frames],
        "steps_per_beat": int(context.steps_per_beat),
        "scale_pitch_classes": list(context.scale_pitch_classes),
        "role_ranges": {role: list(bounds) for role, bounds in sorted(context.role_ranges.items())},
        "evidence": {
            key: {str(pitch): round(float(value), 12) for pitch, value in sorted(rows.items())}
            for key, rows in sorted(context.evidence.items())
        },
        "future_steps": {key: list(rows) for key, rows in sorted(context.future_steps.items())},
        "register_targets": dict(sorted(context.register_targets.items())),
        "metadata": music_frozen_mapping(context.metadata),
    }


def music_add_rank(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    if not left:
        return tuple(float(value) for value in right)
    if len(left) != len(right):
        raise MusicError("player piano rank vectors have inconsistent dimensions")
    return tuple(round(float(a) + float(b), 12) for a, b in zip(left, right))


def music_ordered_operators(plan: MusicVoicePlan, program: PlayerPianoProgram) -> tuple[str, ...]:
    allowed = set(plan.operator_candidates)
    rows = tuple(operator for operator in program.operator_order if operator in allowed)
    return rows or (plan.operator_candidates[0],)


def music_voice_candidate_events(
    state: MusicState,
    plan: MusicVoicePlan,
    program: PlayerPianoProgram,
    *,
    onset_index: int,
) -> tuple[MusicEvent, ...]:
    start = int(plan.onset_steps[onset_index])
    next_start = plan.onset_steps[onset_index + 1] if onset_index + 1 < len(plan.onset_steps) else state.phrase_end_step
    duration = min(int(plan.duration_steps), max(1, int(next_start) - start))
    if onset_index == len(plan.onset_steps) - 1 and int(plan.terminal_duration_steps) > 0:
        duration = min(int(plan.terminal_duration_steps), max(1, state.phrase_end_step - start))
    terminal = start + duration >= state.phrase_end_step
    pitches = list(plan.pitch_pool)
    if plan.allow_rest and None not in pitches:
        pitches.append(None)
    events = []
    for operator in music_ordered_operators(plan, program):
        for pitch in pitches:
            function = "rest" if pitch is None else ("cadence" if terminal else plan.function)
            metadata = {
                **dict(plan.metadata),
                "voice_plan_index": onset_index,
                "phrase_restart": onset_index == 0 or bool(plan.metadata.get("phrase_restart_every") and onset_index % int(plan.metadata["phrase_restart_every"]) == 0),
                "allow_backfill": bool(plan.metadata.get("allow_backfill", True)),
            }
            events.append(
                music_make_event(
                    voice_id=plan.voice_id,
                    role=plan.role,
                    start_step=start,
                    duration_steps=duration,
                    pitch=pitch,
                    velocity=plan.velocity,
                    function=function,
                    operator=operator,
                    source_ids=plan.source_ids,
                    metadata=metadata,
                )
            )
    return tuple(events)


def music_compose_voice(
    state: MusicState,
    plan: MusicVoicePlan,
    context: MusicLawContext,
    program: PlayerPianoProgram,
    *,
    beam_width: int = 64,
) -> tuple[MusicState, tuple[MusicCandidateProof, ...]]:
    if int(beam_width) <= 0:
        raise MusicError("player piano beam_width must be positive")
    beam = (_MusicBeamNode(state=state, proofs=(), cumulative_rank=()),)
    for onset_index, step in enumerate(plan.onset_steps):
        expansions: list[_MusicBeamNode] = []
        rejected: dict[str, int] = {}
        for node in beam:
            for event in music_voice_candidate_events(node.state, plan, program, onset_index=onset_index):
                proof = music_prove_candidate(
                    node.state,
                    event,
                    context,
                    program,
                    motif_intervals=plan.motif_intervals,
                    motif_index=onset_index,
                )
                if not proof.legal:
                    for failure in proof.failures:
                        rejected[failure] = rejected.get(failure, 0) + 1
                    continue
                next_state = music_commit_proof(node.state, proof)
                expansions.append(
                    _MusicBeamNode(
                        state=next_state,
                        proofs=(*node.proofs, proof),
                        cumulative_rank=music_add_rank(node.cumulative_rank, proof.rank_vector),
                    )
                )
        if not expansions:
            raise MusicNoLegalContinuation(
                f"player piano {program.program_id} has no legal continuation for {plan.voice_id} at step {step}; "
                f"rejections={dict(sorted(rejected.items()))}"
            )
        expansions.sort(
            key=lambda node: (
                node.cumulative_rank,
                tuple(proof.event.event_id for proof in node.proofs),
            ),
            reverse=True,
        )
        beam = tuple(expansions[: int(beam_width)])
    debt_free = [node for node in beam if not any(row.voice_id == plan.voice_id for row in node.state.obligations)]
    if not debt_free:
        raise MusicNoLegalContinuation(f"player piano {program.program_id} cannot close voice {plan.voice_id} without debt")
    best = debt_free[0]
    return best.state, best.proofs


def music_plan_order(plans: Sequence[MusicVoicePlan], program: PlayerPianoProgram) -> tuple[MusicVoicePlan, ...]:
    by_id = {plan.voice_id: plan for plan in plans}
    if len(by_id) != len(plans):
        raise MusicError("voice plan IDs must be unique")
    ordered = [by_id[value] for value in program.voice_order if value in by_id]
    used = {plan.voice_id for plan in ordered}
    ordered.extend(sorted((plan for plan in plans if plan.voice_id not in used), key=lambda row: (row.role, row.voice_id)))
    return tuple(ordered)


def music_compose_player_piano(
    *,
    initial_state: MusicState,
    context: MusicLawContext,
    voice_plans: Sequence[MusicVoicePlan],
    program: PlayerPianoProgram,
    beam_width: int = 64,
) -> MusicCompositionResult:
    ordered = music_plan_order(tuple(voice_plans), program)
    state = initial_state
    proofs: list[MusicCandidateProof] = []
    for plan in ordered:
        state, voice_proofs = music_compose_voice(state, plan, context, program, beam_width=beam_width)
        proofs.extend(voice_proofs)
    if state.obligations:
        raise MusicNoLegalContinuation(
            f"player piano {program.program_id} finished with open obligations: {[row.obligation_id for row in state.obligations]}"
        )
    if not state.events:
        raise MusicNoLegalContinuation("player piano produced no events")
    return MusicCompositionResult(
        program=program,
        context_sha256=music_sha256_json(music_context_payload(context)),
        initial_state_sha256=initial_state.state_sha256,
        final_state=state,
        voice_plans=ordered,
        proofs=tuple(proofs),
    )
