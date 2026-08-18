"""A typed, validated projection of a commission the album ledger already names.

This is not a second authority. `configs/album_one/manifest.v1.json` remains the
album-level source of truth; `TrackCommission` is what tooling consumes so that every
lane stops re-reading raw JSON and re-deriving the same invariants slightly
differently.

The projection is deliberately honest about what the ledger does and does not carry.
Source requirements are prose today, so they project as untyped requirements marked
`typed=False` rather than being invented into a schema nobody wrote. A commission
that later declares `binding_requirements` gets typed roles; until then the absence
is visible instead of papered over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from ..evidence.identity import canonical_json_bytes, sha256_bytes

LEGAL_STATES = ("none", "frontier_selected", "master_qualified", "master_accepted",
                "system_reference_passed")


class CommissionError(RuntimeError):
    pass


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return value[:64] or "unnamed"


@dataclass(frozen=True)
class BindingRequirement:
    """One thing a commission says must be bound before its lane can execute."""

    role: str
    description: str
    modality: str = "unspecified"
    typed: bool = False
    edition_constraint: str | None = None
    required_identities: tuple[str, ...] = ()

    @classmethod
    def from_declaration(cls, value: Mapping[str, Any]) -> "BindingRequirement":
        role = str(value.get("role") or "")
        if not role:
            raise CommissionError("a declared binding requirement needs a role")
        return cls(
            role=role,
            description=str(value.get("description") or role),
            modality=str(value.get("modality") or "unspecified"),
            typed=True,
            edition_constraint=value.get("edition_constraint"),
            required_identities=tuple(value.get("required_identities") or ()),
        )

    @classmethod
    def from_prose(cls, text: str) -> "BindingRequirement":
        return cls(role=_slug(text), description=str(text))


@dataclass(frozen=True)
class TrackCommission:
    """What a track is commissioned to prove, and what it needs in order to try."""

    track_id: str
    album_id: str
    capability_role: str
    control_question: str
    required_bindings: tuple[BindingRequirement, ...]
    legal_states: tuple[str, ...] = LEGAL_STATES
    private_execution_required: tuple[str, ...] = ()
    acceptance_requirements: tuple[str, ...] = ()
    system_reference_requirement: str = ""
    commission_sha256: str = field(default="", compare=False)

    @property
    def typed_bindings(self) -> tuple[BindingRequirement, ...]:
        return tuple(row for row in self.required_bindings if row.typed)

    def requirement(self, role: str) -> BindingRequirement:
        for row in self.required_bindings:
            if row.role == role:
                return row
        raise CommissionError(f"{self.track_id} declares no binding role {role!r}")


def _content_identity(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def from_ledger(manifest: Mapping[str, Any], track_id: str) -> TrackCommission:
    """Project one commission out of the album ledger, validating as we go."""
    rows = [row for row in manifest.get("tracks") or []
            if row.get("track_id") == track_id]
    if not rows:
        raise CommissionError(f"{track_id} is not a commissioned track in this album")
    row = rows[0]

    capability = str(row.get("capability_role") or "")
    if not capability:
        raise CommissionError(f"{track_id} names no capability_role in the ledger")

    declared: Sequence[Mapping[str, Any]] = row.get("binding_requirements") or ()
    if declared:
        requirements = tuple(BindingRequirement.from_declaration(item) for item in declared)
    else:
        # The ledger carries prose today. Project it rather than invent a schema.
        requirements = tuple(BindingRequirement.from_prose(text)
                             for text in row.get("source_requirements") or ())

    roles = [item.role for item in requirements]
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        raise CommissionError(
            f"{track_id} declares duplicate binding roles: {duplicates}. Two requirements "
            "that cannot be told apart cannot both be satisfied.")

    completion = manifest.get("completion_model") or {}
    commission = TrackCommission(
        track_id=track_id,
        album_id=str(manifest.get("album_id") or ""),
        capability_role=capability,
        control_question=str(row.get("control_question") or ""),
        required_bindings=requirements,
        private_execution_required=tuple(row.get("private_execution_required") or ()),
        acceptance_requirements=tuple(completion.get("album_master") or ()),
        system_reference_requirement="; ".join(completion.get("system_reference") or ()),
    )
    identity = _content_identity({
        "album_id": commission.album_id,
        "track_id": commission.track_id,
        "capability_role": commission.capability_role,
        "control_question": commission.control_question,
        "required_bindings": [
            {"role": row.role, "description": row.description, "modality": row.modality,
             "typed": row.typed, "edition_constraint": row.edition_constraint,
             "required_identities": list(row.required_identities)}
            for row in commission.required_bindings],
    })
    return TrackCommission(**{**commission.__dict__, "commission_sha256": identity})


def verify_against_ledger(commission: TrackCommission,
                          manifest: Mapping[str, Any]) -> list[str]:
    """Findings if the projection and the ledger have drifted apart."""
    problems: list[str] = []
    rows = [row for row in manifest.get("tracks") or []
            if row.get("track_id") == commission.track_id]
    if not rows:
        return [f"{commission.track_id} is no longer a commissioned track"]
    row = rows[0]
    if row.get("capability_role") != commission.capability_role:
        problems.append(
            f"capability_role drifted: ledger {row.get('capability_role')!r}, "
            f"commission {commission.capability_role!r}")
    if str(manifest.get("album_id") or "") != commission.album_id:
        problems.append("the commission belongs to a different album")
    rebuilt = from_ledger(manifest, commission.track_id)
    if rebuilt.commission_sha256 != commission.commission_sha256:
        problems.append("commission identity no longer matches the ledger it came from")
    return problems
