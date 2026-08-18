"""What it means to have bound a source, for sources that are not all audio.

A1-07 bound recordings and stems, so an audio-shaped binding model would have looked
sufficient. A1-02 binds a printed score, a symbolic score, MIDI and a rack library,
and a canonical PCM digest is meaningless for a PDF. Modelling that as one large
structure full of nullable audio fields would make "not applicable" and "not yet
verified" indistinguishable -- which is the difference between a binding that is fine
and a binding that is missing.

So identity is modality-specific. Each modality declares which identities it requires
and which are meaningless for it, and a binding carrying a meaningless identity is
refused rather than ignored.

Readiness is not "a path exists". It is: the identities this modality requires are
present, the edition constraint the commission declared is satisfied, and verification
actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..evidence.identity import HEX64, canonical_json_bytes, sha256_bytes

# modality -> (required identities, meaningless identities)
MODALITIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "audio_recording": (("container_sha256", "canonical_pcm_sha256"), ()),
    "decoded_pcm": (("canonical_pcm_sha256",), ("container_sha256",)),
    "stem": (("container_sha256", "canonical_pcm_sha256"), ()),
    "printed_score": (("container_sha256",), ("canonical_pcm_sha256",)),
    "symbolic_score": (("container_sha256", "content_sha256"), ("canonical_pcm_sha256",)),
    "midi": (("container_sha256", "content_sha256"), ("canonical_pcm_sha256",)),
    "model_checkpoint": (("container_sha256", "revision"), ("canonical_pcm_sha256",)),
    "rack_preset": (("container_sha256",), ("canonical_pcm_sha256",)),
    "workspace_evidence": (("container_sha256",), ("canonical_pcm_sha256",)),
    "reference_document": (("container_sha256",), ("canonical_pcm_sha256",)),
}

AUTHORITY_CLASSES = ("answer_key", "material", "control", "incumbent", "tooling",
                     "reference")
PRIVACY_CLASSES = ("private_local", "repo_tracked", "third_party")
CUSTODY_CLASSES = ("private_custody", "repo", "external_provider", "unbound")


class BindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceBinding:
    """One bound source: what it is, where custody lives, and how it is identified."""

    source_id: str
    role: str
    modality: str
    authority_class: str
    privacy_class: str
    custody_class: str
    identities: Mapping[str, Any] = field(default_factory=dict)
    edition: Mapping[str, Any] = field(default_factory=dict)
    verified: bool = False
    verification_note: str = ""
    location: str | None = None

    def __post_init__(self) -> None:
        if self.modality not in MODALITIES:
            raise BindingError(
                f"unknown modality {self.modality!r}; declare it before binding to it")
        for name, allowed in (("authority_class", AUTHORITY_CLASSES),
                              ("privacy_class", PRIVACY_CLASSES),
                              ("custody_class", CUSTODY_CLASSES)):
            if getattr(self, name) not in allowed:
                raise BindingError(f"{name}={getattr(self, name)!r} is not one of {allowed}")

        _, meaningless = MODALITIES[self.modality]
        present = {key for key, value in self.identities.items() if value}
        offending = present & set(meaningless)
        if offending:
            raise BindingError(
                f"{self.source_id}: {sorted(offending)} is meaningless for a "
                f"{self.modality}; carrying it makes 'not applicable' look like a value")
        for key in ("container_sha256", "canonical_pcm_sha256", "content_sha256"):
            value = self.identities.get(key)
            if value and not HEX64.fullmatch(str(value)):
                raise BindingError(f"{self.source_id}: {key} is not a sha256 digest")

    @property
    def required_identities(self) -> tuple[str, ...]:
        return MODALITIES[self.modality][0]

    @property
    def missing_identities(self) -> tuple[str, ...]:
        return tuple(name for name in self.required_identities
                     if not self.identities.get(name))

    def readiness(self, requirement: Any | None = None) -> list[str]:
        """Findings, so an unready binding can say exactly what it still lacks."""
        problems = [f"missing {name}" for name in self.missing_identities]
        if not self.verified:
            problems.append("not verified; a path is not a binding")
        if requirement is not None:
            constraint = getattr(requirement, "edition_constraint", None)
            if constraint and not self.edition:
                problems.append(f"edition constraint {constraint!r} is unsatisfied")
            for name in getattr(requirement, "required_identities", ()):
                if not self.identities.get(name):
                    problems.append(f"commission requires {name}")
            modality = getattr(requirement, "modality", "unspecified")
            if modality not in ("unspecified", self.modality):
                problems.append(f"commission expects a {modality}, this is a {self.modality}")
        return problems

    def is_ready(self, requirement: Any | None = None) -> bool:
        return not self.readiness(requirement)

    def identity_digest(self) -> str:
        """Content-addressed identity of the binding, excluding custody location."""
        return sha256_bytes(canonical_json_bytes({
            "source_id": self.source_id,
            "role": self.role,
            "modality": self.modality,
            "authority_class": self.authority_class,
            "identities": dict(self.identities),
            "edition": dict(self.edition),
        }))

    def public_projection(self) -> dict[str, Any]:
        """Never carries custody location, whatever the privacy class."""
        return {
            "source_id": self.source_id,
            "role": self.role,
            "modality": self.modality,
            "authority_class": self.authority_class,
            "privacy_class": self.privacy_class,
            "custody_class": self.custody_class,
            "identities": dict(self.identities),
            "edition": dict(self.edition),
            "verified": self.verified,
            "binding_sha256": self.identity_digest(),
        }


def edition_candidate(source_id: str, role: str, modality: str, *,
                      note: str) -> SourceBinding:
    """A source we have found but have NOT established is the intended edition.

    Deliberately unverified and identity-free. An answer key cannot be authoritative
    if the edition was chosen after acquisition, so the candidate state exists to be
    visible rather than to be quietly promoted.
    """
    return SourceBinding(
        source_id=source_id, role=role, modality=modality,
        authority_class="reference", privacy_class="private_local",
        custody_class="unbound", identities={}, edition={},
        verified=False,
        verification_note=f"edition_candidate: {note}")


def readiness_report(commission: Any, bound: Mapping[str, SourceBinding]) -> dict[str, Any]:
    """Which of a commission's requirements are satisfied, and what each one lacks."""
    rows = []
    ready = True
    for requirement in commission.required_bindings:
        binding = bound.get(requirement.role)
        if binding is None:
            rows.append({"role": requirement.role, "bound": False, "ready": False,
                         "problems": ["not bound"], "typed_requirement": requirement.typed})
            ready = False
            continue
        problems = binding.readiness(requirement)
        rows.append({"role": requirement.role, "bound": True, "ready": not problems,
                     "problems": problems, "modality": binding.modality,
                     "typed_requirement": requirement.typed})
        ready = ready and not problems
    return {"track_id": commission.track_id, "all_required_bindings_ready": ready,
            "requirements": rows}
