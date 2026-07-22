from __future__ import annotations

"""Harmony, tendency, obligation, bass, register, and closure laws."""

from typing import Any, Mapping, Sequence

from earcrate.music.law_context import MusicLawContext
from earcrate.music.law_voice import music_law_fail, music_law_is_strong_step, music_law_ok
from earcrate.music.model import (
    MusicEvent, MusicLawVerdict, MusicObligation, MusicState, PlayerPianoProgram,
    music_frozen_mapping, music_pc, music_stable_id,
)

def music_law_find_source_event(state: MusicState, obligation: MusicObligation) -> MusicEvent | None:
    return next((row for row in state.events if row.event_id == obligation.source_event_id), None)

def music_law_obligation_can_discharge(state: MusicState, event: MusicEvent, obligation: MusicObligation) -> bool:
    if event.voice_id != obligation.voice_id or event.pitch is None:
        return False
    if event.start_step <= obligation.created_step or event.start_step > obligation.due_step:
        return False
    if music_pc(event.pitch) not in obligation.allowed_pitch_classes:
        return False
    source = music_law_find_source_event(state, obligation)
    if source is None or source.pitch is None:
        return True
    motion = int(event.pitch) - int(source.pitch)
    if abs(motion) > int(obligation.max_motion):
        return False
    if int(obligation.direction) < 0 and motion >= 0:
        return False
    if int(obligation.direction) > 0 and motion <= 0:
        return False
    return True

def music_law_obligation_discharge(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del context, program
    relevant = [row for row in state.obligations if row.voice_id == event.voice_id]
    discharged = [row.obligation_id for row in relevant if music_law_obligation_can_discharge(state, event, row)]
    unpaid_due = [
        row.obligation_id
        for row in relevant
        if row.obligation_id not in discharged and int(event.start_step) >= int(row.due_step)
    ]
    if unpaid_due:
        return music_law_fail(
            "obligation_discharge",
            "obligation_due_without_resolution",
            facts={"unpaid": unpaid_due, "discharged": discharged},
        )
    return music_law_ok(
        "obligation_discharge",
        discharged_obligation_ids=tuple(discharged),
        facts={"open_before": [row.obligation_id for row in relevant], "discharged": discharged},
    )

def music_law_nearest_pcs(source_pc: int, targets: Sequence[int], *, max_distance: int = 2) -> tuple[int, ...]:
    rows = [music_pc(value) for value in targets if min((music_pc(value) - source_pc) % 12, (source_pc - music_pc(value)) % 12) <= max_distance]
    return tuple(sorted(set(rows)))

def music_law_obligation_reachable(
    event: MusicEvent,
    obligation: MusicObligation,
    context: MusicLawContext,
) -> bool:
    if event.pitch is None:
        return False
    low, high = context.range_for(event.role)
    future_steps = context.future_for(event.voice_id, event.start_step, obligation.due_step)
    if not future_steps:
        return False
    for pitch in range(int(low), int(high) + 1):
        if music_pc(pitch) not in obligation.allowed_pitch_classes:
            continue
        motion = int(pitch) - int(event.pitch)
        if abs(motion) > int(obligation.max_motion):
            continue
        if obligation.direction < 0 and motion >= 0:
            continue
        if obligation.direction > 0 and motion <= 0:
            continue
        return True
    return False

def music_law_make_obligation(
    *,
    event: MusicEvent,
    kind: str,
    due_step: int,
    allowed_pitch_classes: Sequence[int],
    max_motion: int,
    direction: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> MusicObligation:
    payload = {
        "kind": str(kind),
        "voice_id": event.voice_id,
        "source_event_id": event.event_id,
        "created_step": int(event.start_step),
        "due_step": int(due_step),
        "allowed_pitch_classes": sorted({music_pc(value) for value in allowed_pitch_classes}),
        "max_motion": int(max_motion),
        "direction": int(direction),
        "metadata": music_frozen_mapping(metadata),
    }
    return MusicObligation(obligation_id=music_stable_id("music_obligation", payload), **payload)

def music_law_harmony_tendency(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    if event.pitch is None or event.role in {"percussion", "drums", "fx", "sample_trigger", "silence"}:
        return music_law_ok("harmony_tendency", facts={"unpitched": True})
    frame = context.frame_at(event.start_step)
    if frame is None:
        return music_law_fail("harmony_tendency", "no_harmony_frame_at_event")
    pc = music_pc(event.pitch)
    strong = music_law_is_strong_step(event, context)
    window = max(1, int(program.parameters.get("resolution_window_steps", context.steps_per_beat)))
    max_motion = max(1, int(program.parameters.get("resolution_max_motion", 2)))
    created: list[MusicObligation] = []
    relation = ""

    if pc in frame.stable_pitch_classes:
        relation = "stable_chord_tone"
    elif pc in frame.pitch_classes:
        relation = "chord_color"
        extensions_create = bool(program.parameters.get("extensions_create_obligation", False))
        if extensions_create and event.role not in {"harmony", "pad"}:
            created.append(
                music_law_make_obligation(
                    event=event,
                    kind="extension_to_stability",
                    due_step=min(state.phrase_end_step, event.start_step + window),
                    allowed_pitch_classes=frame.stable_pitch_classes,
                    max_motion=max_motion,
                    metadata={"frame": frame.label},
                )
            )
    else:
        leading_pc = music_pc(frame.root_pc - 1)
        fourth_pc = music_pc(frame.root_pc + 5)
        if pc == leading_pc and bool(program.parameters.get("allow_leading_tone", True)):
            relation = "leading_tone"
            created.append(
                music_law_make_obligation(
                    event=event,
                    kind="leading_tone_to_tonic",
                    due_step=min(state.phrase_end_step, event.start_step + window),
                    allowed_pitch_classes=(frame.root_pc,),
                    max_motion=1,
                    direction=1,
                    metadata={"frame": frame.label},
                )
            )
        elif pc == fourth_pc and bool(program.parameters.get("allow_scale_degree_four", True)):
            thirds = tuple(value for value in frame.stable_pitch_classes if music_pc(value - frame.root_pc) in {3, 4})
            if not thirds:
                return music_law_fail("harmony_tendency", "scale_degree_four_has_no_third_destination")
            relation = "scale_degree_four"
            created.append(
                music_law_make_obligation(
                    event=event,
                    kind="scale_degree_four_to_three",
                    due_step=min(state.phrase_end_step, event.start_step + window),
                    allowed_pitch_classes=thirds,
                    max_motion=2,
                    direction=-1,
                    metadata={"frame": frame.label},
                )
            )
        elif pc in context.scale_pitch_classes and not strong and bool(program.parameters.get("allow_passing_tones", True)):
            destinations = music_law_nearest_pcs(pc, frame.pitch_classes, max_distance=max_motion)
            if not destinations:
                return music_law_fail("harmony_tendency", "passing_tone_has_no_stepwise_destination")
            relation = "weak_passing_tone"
            created.append(
                music_law_make_obligation(
                    event=event,
                    kind="passing_tone_to_chord",
                    due_step=min(state.phrase_end_step, event.start_step + window),
                    allowed_pitch_classes=destinations,
                    max_motion=max_motion,
                    metadata={"frame": frame.label},
                )
            )
        elif bool(program.parameters.get("allow_chromatic_approach", False)) and (
            not strong or bool(program.parameters.get("allow_strong_tension", False))
        ):
            destinations = music_law_nearest_pcs(pc, frame.pitch_classes, max_distance=1)
            if not destinations:
                return music_law_fail("harmony_tendency", "chromatic_note_is_not_an_approach")
            relation = "chromatic_approach"
            direction = 0
            if len(destinations) == 1:
                up = (destinations[0] - pc) % 12
                direction = 1 if up == 1 else (-1 if up == 11 else 0)
            created.append(
                music_law_make_obligation(
                    event=event,
                    kind="chromatic_approach_to_chord",
                    due_step=min(state.phrase_end_step, event.start_step + max(1, min(window, context.steps_per_beat))),
                    allowed_pitch_classes=destinations,
                    max_motion=1,
                    direction=direction,
                    metadata={"frame": frame.label},
                )
            )
        else:
            return music_law_fail(
                "harmony_tendency",
                "pitch_has_no_admissible_harmonic_function",
                facts={"pitch_class": pc, "frame": frame.label, "strong": strong},
            )

    unreachable = [row.obligation_id for row in created if not music_law_obligation_reachable(event, row, context)]
    if unreachable:
        return music_law_fail(
            "harmony_tendency",
            "tension_has_no_reachable_resolution",
            facts={"relation": relation, "unreachable": unreachable, "frame": frame.label},
        )
    return music_law_ok(
        "harmony_tendency",
        created_obligations=tuple(created),
        facts={"relation": relation, "frame": frame.label, "strong": strong, "pitch_class": pc},
    )

def music_law_bass_function(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del state
    if event.pitch is None or event.role not in set(program.parameters.get("bass_roles", ["bass", "sub_bass", "bass_articulation"])):
        return music_law_ok("bass_function", facts={"bass": False})
    frame = context.frame_at(event.start_step)
    if frame is None:
        return music_law_fail("bass_function", "bass_has_no_harmony_frame")
    pc = music_pc(event.pitch)
    if pc in frame.bass_pitch_classes:
        return music_law_ok("bass_function", facts={"bass": True, "relation": "structural", "frame": frame.label})
    weak = not music_law_is_strong_step(event, context)
    approach = music_law_nearest_pcs(pc, frame.bass_pitch_classes, max_distance=1)
    if weak and approach and bool(program.parameters.get("allow_bass_approach", False)):
        return music_law_ok("bass_function", facts={"bass": True, "relation": "approach", "destinations": list(approach)})
    return music_law_fail(
        "bass_function",
        "illegal_bass_relation",
        facts={"pitch_class": pc, "allowed": list(frame.bass_pitch_classes), "frame": frame.label},
    )

def music_law_register_collision(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    del context
    if event.pitch is None or event.role in {"percussion", "drums", "fx", "sample_trigger", "silence"}:
        return music_law_ok("register_collision")
    minimum = int(program.parameters.get("minimum_register_separation", 2))
    doubling_ops = set(program.parameters.get("doubling_operators") or ["double", "octave_double"])
    collisions = []
    for active in state.active_events(event.start_step, exclude_voice=event.voice_id):
        if active.pitch is None:
            continue
        distance = abs(int(event.pitch) - int(active.pitch))
        if distance < minimum and event.operator not in doubling_ops:
            collisions.append({"event_id": active.event_id, "voice_id": active.voice_id, "distance": distance})
    if collisions:
        return music_law_fail("register_collision", "unlicensed_register_collision", facts={"collisions": collisions, "minimum": minimum})
    return music_law_ok("register_collision", facts={"minimum": minimum})

def music_law_phrase_closure(state: MusicState, event: MusicEvent, context: MusicLawContext, program: PlayerPianoProgram) -> MusicLawVerdict:
    if event.end_step < state.phrase_end_step:
        return music_law_ok("phrase_closure", facts={"terminal": False})
    if event.pitch is None or event.role in {"percussion", "drums", "fx", "sample_trigger", "silence"}:
        return music_law_ok("phrase_closure", facts={"terminal": True, "unpitched_or_trigger": True})
    frame = context.frame_at(min(event.start_step, state.phrase_end_step - 1))
    open_functions = set(program.parameters.get("open_ending_functions") or [])
    if event.function in open_functions:
        return music_law_ok("phrase_closure", facts={"terminal": True, "open_ending": True})
    if frame is None or music_pc(event.pitch) not in frame.stable_pitch_classes:
        return music_law_fail("phrase_closure", "phrase_ends_on_unstable_pitch")
    return music_law_ok("phrase_closure", facts={"terminal": True, "stable": True, "frame": frame.label})
