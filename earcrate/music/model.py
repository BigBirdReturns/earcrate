from __future__ import annotations

"""Canonical musical objects for EarCrate's player-piano kernel.

The music layer is intentionally independent of MIDI, audio providers, and the
legacy monolith.  It describes what a musical mind has committed, why the move
was admissible, and which obligations remain.  MIDI and audio are lowerings of
this authority, not the authority itself.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping, Sequence

MUSIC_MODEL_SCHEMA_VERSION = 1
MUSIC_PROOF_SCHEMA_VERSION = 1
MUSIC_PROGRAM_SCHEMA_VERSION = 1


class MusicError(ValueError):
    """Base error for invalid or unprovable musical objects."""


class MusicNoLegalContinuation(MusicError):
    """Raised when a player piano has no legal move and refuses to improvise."""


def music_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MusicError("musical objects cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        return {str(key): music_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [music_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return music_jsonable(value.to_dict())
    if hasattr(value, "item"):
        return music_jsonable(value.item())
    return str(value)


def music_canonical_json(value: Any) -> str:
    return json.dumps(music_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def music_sha256_json(value: Any) -> str:
    return sha256(music_canonical_json(value).encode("utf-8")).hexdigest()


def music_stable_id(prefix: str, value: Any, width: int = 24) -> str:
    return f"{prefix}_{music_sha256_json(value)[:int(width)]}"


def music_pc(value: int) -> int:
    return int(value) % 12


def music_sorted_pcs(values: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({music_pc(value) for value in values}))


def music_frozen_mapping(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # JSON round-trip gives the model a detached deterministic payload without
    # introducing a custom immutable mapping that the single-file build must know.
    return json.loads(music_canonical_json(dict(value or {})))


@dataclass(frozen=True)
class MusicHarmonyFrame:
    start_step: int
    end_step: int
    root_pc: int
    pitch_classes: tuple[int, ...]
    stable_pitch_classes: tuple[int, ...]
    bass_pitch_classes: tuple[int, ...]
    label: str = ""
    function: str = ""

    def __post_init__(self) -> None:
        if int(self.start_step) < 0 or int(self.end_step) <= int(self.start_step):
            raise MusicError("harmony frame requires a positive step range")
        object.__setattr__(self, "root_pc", music_pc(self.root_pc))
        object.__setattr__(self, "pitch_classes", music_sorted_pcs(self.pitch_classes))
        object.__setattr__(self, "stable_pitch_classes", music_sorted_pcs(self.stable_pitch_classes))
        object.__setattr__(self, "bass_pitch_classes", music_sorted_pcs(self.bass_pitch_classes))
        if self.root_pc not in self.pitch_classes:
            raise MusicError("harmony frame pitch_classes must contain its root")
        if not set(self.stable_pitch_classes).issubset(set(self.pitch_classes)):
            raise MusicError("stable pitch classes must be contained in pitch_classes")
        if not set(self.bass_pitch_classes).issubset(set(self.pitch_classes)):
            raise MusicError("bass pitch classes must be contained in pitch_classes")

    def contains(self, step: int) -> bool:
        return int(self.start_step) <= int(step) < int(self.end_step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_step": int(self.start_step),
            "end_step": int(self.end_step),
            "root_pc": int(self.root_pc),
            "pitch_classes": list(self.pitch_classes),
            "stable_pitch_classes": list(self.stable_pitch_classes),
            "bass_pitch_classes": list(self.bass_pitch_classes),
            "label": str(self.label),
            "function": str(self.function),
        }


@dataclass(frozen=True)
class MusicEvent:
    event_id: str
    voice_id: str
    role: str
    start_step: int
    duration_steps: int
    pitch: int | None
    velocity: int = 96
    function: str = "statement"
    operator: str = "state"
    source_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.event_id):
            raise MusicError("musical event requires event_id")
        if not str(self.voice_id):
            raise MusicError("musical event requires voice_id")
        if int(self.start_step) < 0 or int(self.duration_steps) <= 0:
            raise MusicError("musical event requires a positive timeline span")
        if self.pitch is not None and not 0 <= int(self.pitch) <= 127:
            raise MusicError("pitched musical event must be in MIDI range 0..127")
        if not 1 <= int(self.velocity) <= 127:
            raise MusicError("musical event velocity must be in 1..127")
        object.__setattr__(self, "source_ids", tuple(str(value) for value in self.source_ids))
        object.__setattr__(self, "metadata", music_frozen_mapping(self.metadata))

    @property
    def end_step(self) -> int:
        return int(self.start_step) + int(self.duration_steps)

    @property
    def pitch_class(self) -> int | None:
        return None if self.pitch is None else music_pc(self.pitch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "voice_id": str(self.voice_id),
            "role": str(self.role),
            "start_step": int(self.start_step),
            "duration_steps": int(self.duration_steps),
            "pitch": None if self.pitch is None else int(self.pitch),
            "velocity": int(self.velocity),
            "function": str(self.function),
            "operator": str(self.operator),
            "source_ids": list(self.source_ids),
            "metadata": music_frozen_mapping(self.metadata),
        }


@dataclass(frozen=True)
class MusicObligation:
    obligation_id: str
    kind: str
    voice_id: str
    source_event_id: str
    created_step: int
    due_step: int
    allowed_pitch_classes: tuple[int, ...]
    max_motion: int = 2
    direction: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.obligation_id) or not str(self.kind) or not str(self.voice_id):
            raise MusicError("musical obligation requires identity, kind, and voice")
        if int(self.created_step) < 0 or int(self.due_step) <= int(self.created_step):
            raise MusicError("musical obligation due_step must follow created_step")
        if int(self.max_motion) < 0:
            raise MusicError("musical obligation max_motion must be nonnegative")
        if int(self.direction) not in {-1, 0, 1}:
            raise MusicError("musical obligation direction must be -1, 0, or 1")
        pcs = music_sorted_pcs(self.allowed_pitch_classes)
        if not pcs:
            raise MusicError("musical obligation requires at least one destination pitch class")
        object.__setattr__(self, "allowed_pitch_classes", pcs)
        object.__setattr__(self, "metadata", music_frozen_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": str(self.obligation_id),
            "kind": str(self.kind),
            "voice_id": str(self.voice_id),
            "source_event_id": str(self.source_event_id),
            "created_step": int(self.created_step),
            "due_step": int(self.due_step),
            "allowed_pitch_classes": list(self.allowed_pitch_classes),
            "max_motion": int(self.max_motion),
            "direction": int(self.direction),
            "metadata": music_frozen_mapping(self.metadata),
        }


@dataclass(frozen=True)
class MusicState:
    phrase_start_step: int
    phrase_end_step: int
    current_step: int
    events: tuple[MusicEvent, ...] = ()
    obligations: tuple[MusicObligation, ...] = ()
    form_function: str = "phrase"
    motif_memory: tuple[int, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.phrase_start_step) < 0 or int(self.phrase_end_step) <= int(self.phrase_start_step):
            raise MusicError("music state requires a positive phrase range")
        if not int(self.phrase_start_step) <= int(self.current_step) <= int(self.phrase_end_step):
            raise MusicError("music state current_step is outside its phrase")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise MusicError("music state event IDs must be unique")
        obligation_ids = [row.obligation_id for row in self.obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise MusicError("music state obligation IDs must be unique")
        object.__setattr__(self, "motif_memory", tuple(int(value) for value in self.motif_memory))
        object.__setattr__(self, "metadata", music_frozen_mapping(self.metadata))

    def last_event(self, voice_id: str) -> MusicEvent | None:
        rows = [event for event in self.events if event.voice_id == str(voice_id)]
        return max(rows, key=lambda event: (event.start_step, event.end_step, event.event_id)) if rows else None

    def active_events(self, step: int, *, exclude_voice: str = "") -> tuple[MusicEvent, ...]:
        value = int(step)
        return tuple(
            event
            for event in self.events
            if event.voice_id != str(exclude_voice)
            and int(event.start_step) <= value < int(event.end_step)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MUSIC_MODEL_SCHEMA_VERSION,
            "phrase_start_step": int(self.phrase_start_step),
            "phrase_end_step": int(self.phrase_end_step),
            "current_step": int(self.current_step),
            "events": [event.to_dict() for event in self.events],
            "obligations": [row.to_dict() for row in self.obligations],
            "form_function": str(self.form_function),
            "motif_memory": list(self.motif_memory),
            "metadata": music_frozen_mapping(self.metadata),
        }

    @property
    def state_sha256(self) -> str:
        return music_sha256_json(self.to_dict())


@dataclass(frozen=True)
class MusicLawVerdict:
    law_id: str
    admissible: bool
    failures: tuple[str, ...] = ()
    cost_terms: Mapping[str, float] = field(default_factory=dict)
    created_obligations: tuple[MusicObligation, ...] = ()
    discharged_obligation_ids: tuple[str, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(sorted({str(value) for value in self.failures})))
        object.__setattr__(self, "cost_terms", {str(key): float(value) for key, value in self.cost_terms.items()})
        object.__setattr__(self, "discharged_obligation_ids", tuple(sorted({str(value) for value in self.discharged_obligation_ids})))
        object.__setattr__(self, "facts", music_frozen_mapping(self.facts))
        if self.admissible and self.failures:
            raise MusicError(f"law {self.law_id} is admissible but reports failures")

    def to_dict(self) -> dict[str, Any]:
        return {
            "law_id": str(self.law_id),
            "admissible": bool(self.admissible),
            "failures": list(self.failures),
            "cost_terms": dict(sorted(self.cost_terms.items())),
            "created_obligations": [row.to_dict() for row in self.created_obligations],
            "discharged_obligation_ids": list(self.discharged_obligation_ids),
            "facts": music_frozen_mapping(self.facts),
        }


@dataclass(frozen=True)
class MusicObjectiveStage:
    stage_id: str
    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if not str(self.stage_id):
            raise MusicError("objective stage requires stage_id")
        if not self.weights:
            raise MusicError("objective stage requires at least one equation weight")
        object.__setattr__(self, "weights", {str(key): float(value) for key, value in self.weights.items()})

    def score(self, terms: Mapping[str, float]) -> float:
        return round(sum(float(weight) * float(terms.get(name, 0.0)) for name, weight in self.weights.items()), 12)

    def to_dict(self) -> dict[str, Any]:
        return {"stage_id": str(self.stage_id), "weights": dict(sorted(self.weights.items()))}


@dataclass(frozen=True)
class PlayerPianoProgram:
    program_id: str
    name: str
    version: int
    law_order: tuple[str, ...]
    objective_stages: tuple[MusicObjectiveStage, ...]
    voice_order: tuple[str, ...]
    operator_order: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.program_id) or not str(self.name) or int(self.version) <= 0:
            raise MusicError("player piano program requires identity, name, and positive version")
        if not self.law_order or len(self.law_order) != len(set(self.law_order)):
            raise MusicError("player piano law_order must be unique and nonempty")
        if not self.objective_stages:
            raise MusicError("player piano requires objective stages")
        if not self.operator_order:
            raise MusicError("player piano requires at least one operator")
        object.__setattr__(self, "parameters", music_frozen_mapping(self.parameters))

    def rank_terms(self, terms: Mapping[str, float], tie_breaker: str = "") -> tuple[float, ...]:
        stages = tuple(stage.score(terms) for stage in self.objective_stages)
        # Lower lexical stable hash is preferred only after every declared musical
        # objective.  The hash is not an aesthetic equation.
        tie = -int((tie_breaker or "0" * 12)[:12], 16) / float(0xFFFFFFFFFFFF)
        return (*stages, tie)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MUSIC_PROGRAM_SCHEMA_VERSION,
            "program_id": str(self.program_id),
            "name": str(self.name),
            "version": int(self.version),
            "law_order": list(self.law_order),
            "objective_stages": [stage.to_dict() for stage in self.objective_stages],
            "voice_order": list(self.voice_order),
            "operator_order": list(self.operator_order),
            "parameters": music_frozen_mapping(self.parameters),
        }

    @property
    def program_sha256(self) -> str:
        return music_sha256_json(self.to_dict())


@dataclass(frozen=True)
class MusicCandidateProof:
    program_id: str
    program_sha256: str
    state_before_sha256: str
    event: MusicEvent
    verdicts: tuple[MusicLawVerdict, ...]
    legal: bool
    failures: tuple[str, ...]
    equation_terms: Mapping[str, float]
    rank_vector: tuple[float, ...]
    obligations_after: tuple[MusicObligation, ...]
    state_after_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(sorted({str(value) for value in self.failures})))
        object.__setattr__(self, "equation_terms", {str(key): float(value) for key, value in self.equation_terms.items()})
        object.__setattr__(self, "rank_vector", tuple(float(value) for value in self.rank_vector))
        if self.legal and self.failures:
            raise MusicError("legal candidate proof cannot contain failures")
        if self.legal and not all(verdict.admissible for verdict in self.verdicts):
            raise MusicError("legal candidate proof contains an inadmissible law verdict")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": MUSIC_PROOF_SCHEMA_VERSION,
            "program_id": str(self.program_id),
            "program_sha256": str(self.program_sha256),
            "state_before_sha256": str(self.state_before_sha256),
            "event": self.event.to_dict(),
            "verdicts": [verdict.to_dict() for verdict in self.verdicts],
            "legal": bool(self.legal),
            "failures": list(self.failures),
            "equation_terms": dict(sorted(self.equation_terms.items())),
            "rank_vector": list(self.rank_vector),
            "obligations_after": [row.to_dict() for row in self.obligations_after],
            "state_after_sha256": str(self.state_after_sha256),
        }
        payload["proof_sha256"] = music_sha256_json(payload)
        return payload


def music_make_event(
    *,
    voice_id: str,
    role: str,
    start_step: int,
    duration_steps: int,
    pitch: int | None,
    velocity: int = 96,
    function: str = "statement",
    operator: str = "state",
    source_ids: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> MusicEvent:
    payload = {
        "voice_id": str(voice_id),
        "role": str(role),
        "start_step": int(start_step),
        "duration_steps": int(duration_steps),
        "pitch": None if pitch is None else int(pitch),
        "velocity": int(velocity),
        "function": str(function),
        "operator": str(operator),
        "source_ids": [str(value) for value in source_ids],
        "metadata": music_frozen_mapping(metadata),
    }
    return MusicEvent(event_id=music_stable_id("music_event", payload), **payload)


def music_state_with_event(
    state: MusicState,
    event: MusicEvent,
    *,
    obligations: Sequence[MusicObligation],
    motif_memory: Sequence[int] | None = None,
) -> MusicState:
    return MusicState(
        phrase_start_step=state.phrase_start_step,
        phrase_end_step=state.phrase_end_step,
        current_step=max(int(state.current_step), int(event.start_step)),
        events=tuple([*state.events, event]),
        obligations=tuple(obligations),
        form_function=state.form_function,
        motif_memory=tuple(state.motif_memory if motif_memory is None else motif_memory),
        metadata=state.metadata,
    )
