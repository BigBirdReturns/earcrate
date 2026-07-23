from __future__ import annotations

"""Immutable harmonic, evidence, range, and future-event context for musical laws."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from earcrate.music.model import MusicError, MusicHarmonyFrame, music_frozen_mapping, music_pc

@dataclass(frozen=True)
class MusicLawContext:
    harmony_frames: tuple[MusicHarmonyFrame, ...]
    steps_per_beat: int
    scale_pitch_classes: tuple[int, ...]
    role_ranges: Mapping[str, tuple[int, int]]
    evidence: Mapping[str, Mapping[int, float]] = field(default_factory=dict)
    future_steps: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    register_targets: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.steps_per_beat) <= 0:
            raise MusicError("law context steps_per_beat must be positive")
        frames = tuple(sorted(self.harmony_frames, key=lambda row: (row.start_step, row.end_step, row.label)))
        if not frames:
            raise MusicError("law context requires at least one harmony frame")
        for left, right in zip(frames, frames[1:]):
            if int(right.start_step) < int(left.end_step):
                raise MusicError("harmony frames may not overlap")
        object.__setattr__(self, "harmony_frames", frames)
        object.__setattr__(self, "scale_pitch_classes", tuple(sorted({music_pc(value) for value in self.scale_pitch_classes})))
        ranges = {str(role): (int(bounds[0]), int(bounds[1])) for role, bounds in self.role_ranges.items()}
        for role, (low, high) in ranges.items():
            if not 0 <= low <= high <= 127:
                raise MusicError(f"role range {role} must lie in MIDI 0..127")
        object.__setattr__(self, "role_ranges", ranges)
        normalized_evidence = {
            str(key): {int(pitch): float(value) for pitch, value in rows.items()}
            for key, rows in self.evidence.items()
        }
        object.__setattr__(self, "evidence", normalized_evidence)
        object.__setattr__(self, "future_steps", {str(key): tuple(sorted({int(value) for value in rows})) for key, rows in self.future_steps.items()})
        object.__setattr__(self, "register_targets", {str(key): float(value) for key, value in self.register_targets.items()})
        object.__setattr__(self, "metadata", music_frozen_mapping(self.metadata))

    @staticmethod
    def evidence_key(voice_id: str, step: int) -> str:
        return f"{str(voice_id)}@{int(step)}"

    def frame_at(self, step: int) -> MusicHarmonyFrame | None:
        return next((frame for frame in self.harmony_frames if frame.contains(int(step))), None)

    def evidence_at(self, voice_id: str, step: int) -> Mapping[int, float]:
        return self.evidence.get(self.evidence_key(voice_id, step), {})

    def range_for(self, role: str) -> tuple[int, int]:
        return self.role_ranges.get(str(role), self.role_ranges.get("default", (0, 127)))

    def future_for(self, voice_id: str, after_step: int, due_step: int) -> tuple[int, ...]:
        rows = self.future_steps.get(str(voice_id), ())
        return tuple(value for value in rows if int(after_step) < int(value) <= int(due_step))
