from __future__ import annotations

"""Pure equations used by player-piano programs.

No equation decides whether music is legal.  Laws define the feasible set.
Programs arrange these equations into lexicographic objective stages, which is
how the same mathematical vocabulary becomes more than one musical mind.
"""

import math
from typing import Any, Mapping, Sequence

from earcrate.music.model import MusicEvent, MusicHarmonyFrame, MusicState, music_pc

MUSIC_EQUATION_NAMES = (
    "source_evidence",
    "harmonic_stability",
    "voice_leading",
    "metrical_weight",
    "resolution_value",
    "motif_identity",
    "register_fit",
    "orchestral_separation",
    "novelty",
    "repetition_control",
    "tension_color",
)


def music_equation_clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))


def music_equation_pitch_distance(a: int, b: int) -> int:
    return abs(int(a) - int(b))


def music_equation_pc_distance(a: int, b: int) -> int:
    raw = abs(music_pc(a) - music_pc(b))
    return min(raw, 12 - raw)


def music_equation_source_evidence(pitch: int | None, evidence: Mapping[int, float] | None) -> float:
    if pitch is None:
        return 1.0 if not evidence else music_equation_clamp(float(evidence.get(-1, 0.0)))
    if not evidence:
        return 0.5
    values = [max(0.0, float(value)) for value in evidence.values()]
    peak = max(values, default=0.0)
    if peak <= 1e-12:
        return 0.0
    return music_equation_clamp(max(0.0, float(evidence.get(int(pitch), 0.0))) / peak)


def music_equation_harmonic_stability(pitch: int | None, frame: MusicHarmonyFrame | None) -> float:
    if pitch is None or frame is None:
        return 1.0
    pc = music_pc(pitch)
    if pc in frame.stable_pitch_classes:
        return 1.0
    if pc in frame.pitch_classes:
        return 0.72
    if music_equation_pc_distance(pc, frame.root_pc) == 1:
        return 0.32
    return 0.0


def music_equation_voice_leading(previous_pitch: int | None, pitch: int | None, span: int = 12) -> float:
    if pitch is None or previous_pitch is None:
        return 1.0
    return music_equation_clamp(1.0 - music_equation_pitch_distance(previous_pitch, pitch) / max(1.0, float(span)))


def music_equation_metrical_weight(
    event: MusicEvent,
    *,
    steps_per_beat: int,
    stable: bool,
    syncopation_preference: float,
) -> float:
    if event.pitch is None and event.role not in {"percussion", "drums", "fx"}:
        return 0.5
    step_in_beat = int(event.start_step) % max(1, int(steps_per_beat))
    on_beat = step_in_beat == 0
    offbeat = step_in_beat == max(1, int(steps_per_beat)) // 2
    if on_beat:
        return 1.0 if stable else music_equation_clamp(0.55 + 0.35 * float(syncopation_preference))
    if offbeat:
        return music_equation_clamp(0.70 + 0.30 * float(syncopation_preference))
    return music_equation_clamp(0.45 + 0.45 * float(syncopation_preference))


def music_equation_resolution_value(discharged_count: int, urgency: float) -> float:
    if int(discharged_count) <= 0:
        return 0.0
    return music_equation_clamp(0.65 + 0.35 * float(urgency))


def music_equation_motif_identity(
    previous_pitch: int | None,
    pitch: int | None,
    motif_intervals: Sequence[int],
    motif_index: int,
) -> float:
    if pitch is None or previous_pitch is None or not motif_intervals:
        return 0.5
    expected = int(motif_intervals[int(motif_index) % len(motif_intervals)])
    actual = int(pitch) - int(previous_pitch)
    error = abs(actual - expected)
    return music_equation_clamp(1.0 - error / 12.0)


def music_equation_register_fit(pitch: int | None, target: float, width: float) -> float:
    if pitch is None:
        return 1.0
    return music_equation_clamp(1.0 - abs(float(pitch) - float(target)) / max(1.0, float(width)))


def music_equation_orchestral_separation(
    state: MusicState,
    event: MusicEvent,
    *,
    preferred_semitones: int,
) -> float:
    if event.pitch is None:
        return 1.0
    active = [row for row in state.active_events(event.start_step, exclude_voice=event.voice_id) if row.pitch is not None]
    if not active:
        return 1.0
    nearest = min(abs(int(event.pitch) - int(row.pitch)) for row in active)
    return music_equation_clamp(nearest / max(1.0, float(preferred_semitones)))


def music_equation_novelty(previous_pitch: int | None, pitch: int | None, recent: Sequence[int]) -> float:
    if pitch is None:
        return 0.5
    if not recent:
        return 1.0
    repeats = sum(1 for value in recent[-8:] if int(value) == int(pitch))
    pc_repeats = sum(1 for value in recent[-8:] if music_pc(value) == music_pc(pitch))
    motion = 0 if previous_pitch is None else abs(int(pitch) - int(previous_pitch))
    return music_equation_clamp(0.82 - 0.12 * repeats - 0.04 * pc_repeats + min(0.28, motion / 36.0))


def music_equation_repetition_control(previous_pitch: int | None, pitch: int | None, run_length: int) -> float:
    if pitch is None or previous_pitch is None or int(pitch) != int(previous_pitch):
        return 1.0
    if int(run_length) <= 2:
        return 0.82
    return music_equation_clamp(0.82 - 0.24 * (int(run_length) - 2))


def music_equation_tension_color(pitch: int | None, frame: MusicHarmonyFrame | None, creates_obligation: bool) -> float:
    if pitch is None or frame is None:
        return 0.0
    pc = music_pc(pitch)
    if pc in frame.stable_pitch_classes:
        return 0.15
    if pc in frame.pitch_classes:
        return 0.55
    if creates_obligation:
        return 1.0
    return 0.0


def music_equation_terms(
    *,
    state: MusicState,
    event: MusicEvent,
    frame: MusicHarmonyFrame | None,
    evidence: Mapping[int, float] | None,
    steps_per_beat: int,
    syncopation_preference: float,
    motif_intervals: Sequence[int],
    motif_index: int,
    register_target: float,
    register_width: float,
    preferred_separation: int,
    discharged_count: int,
    obligation_urgency: float,
    creates_obligation: bool,
    repetition_run: int,
) -> dict[str, float]:
    previous = state.last_event(event.voice_id)
    previous_pitch = None if previous is None else previous.pitch
    stable = bool(frame is None or event.pitch is None or music_pc(event.pitch) in frame.stable_pitch_classes)
    recent = [row.pitch for row in state.events if row.voice_id == event.voice_id and row.pitch is not None]
    terms = {
        "source_evidence": music_equation_source_evidence(event.pitch, evidence),
        "harmonic_stability": music_equation_harmonic_stability(event.pitch, frame),
        "voice_leading": music_equation_voice_leading(previous_pitch, event.pitch),
        "metrical_weight": music_equation_metrical_weight(
            event,
            steps_per_beat=steps_per_beat,
            stable=stable,
            syncopation_preference=syncopation_preference,
        ),
        "resolution_value": music_equation_resolution_value(discharged_count, obligation_urgency),
        "motif_identity": music_equation_motif_identity(previous_pitch, event.pitch, motif_intervals, motif_index),
        "register_fit": music_equation_register_fit(event.pitch, register_target, register_width),
        "orchestral_separation": music_equation_orchestral_separation(
            state, event, preferred_semitones=preferred_separation
        ),
        "novelty": music_equation_novelty(previous_pitch, event.pitch, recent),
        "repetition_control": music_equation_repetition_control(previous_pitch, event.pitch, repetition_run),
        "tension_color": music_equation_tension_color(event.pitch, frame, creates_obligation),
    }
    missing = set(MUSIC_EQUATION_NAMES) - set(terms)
    if missing:
        raise RuntimeError(f"equation term implementation missing: {sorted(missing)}")
    return {name: round(float(terms[name]), 12) for name in MUSIC_EQUATION_NAMES}
