"""The system-reference challenge: authority model and state machine only.

An accepted master proves the track. A system reference proves the *system*: that a
result can be recovered from controlled evidence while the answer is withheld, rather
than surviving in our memory and session history. A1-07 is accepted and its challenge
has not run, which is exactly why the counter reads 1 accepted master and 0 completed
references.

What is deliberately absent is how a track gets reconstructed. That is the next
concrete challenge design, and inventing it here -- from a track whose challenge has
never been specified -- would repeat the mistake `docs/EXTRACTION_BOUNDARY.md` exists
to prevent.

The rules that are worth encoding now are the ones that make the claim meaningful:

* a challenge cannot start before the master is accepted;
* the withheld answer may not appear in the allowed evidence set, at any depth;
* `system_reference` completes only on a passed challenge;
* a failed challenge never revokes the accepted master -- the album claim and the
  autonomy claim are independent, and conflating them would make us reluctant to run
  an honest challenge;
* repeating the same result transition is byte-idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from ..evidence.identity import canonical_json_bytes, seal, sha256_bytes

NOT_STARTED = "not_started"
PREPARED = "prepared"
ANSWER_WITHHELD = "answer_withheld"
EXECUTED = "executed"
EVALUATED = "evaluated"
PASSED = "passed"
FAILED = "failed"

STATES = (NOT_STARTED, PREPARED, ANSWER_WITHHELD, EXECUTED, EVALUATED, PASSED, FAILED)

LEGAL_MOVES: dict[str, tuple[str, ...]] = {
    NOT_STARTED: (PREPARED,),
    PREPARED: (ANSWER_WITHHELD,),
    ANSWER_WITHHELD: (EXECUTED,),
    EXECUTED: (EVALUATED,),
    EVALUATED: (PASSED, FAILED),
    PASSED: (),
    FAILED: (),
}

MASTER_ACCEPTED = "master_accepted"


class SystemReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Challenge:
    """The bindings a challenge needs before it can claim to have proved anything."""

    track_id: str
    commission_sha256: str
    accepted_master_authority_sha256: str
    withheld_answer_identities: tuple[str, ...]
    allowed_evidence: tuple[Mapping[str, Any], ...]
    forbidden_evidence: tuple[str, ...]
    execution_environment: Mapping[str, Any]
    procedure: str
    evaluator: str
    success_criteria: tuple[str, ...]
    state: str = NOT_STARTED
    result_receipt_sha256: str | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise SystemReferenceError(f"unknown challenge state {self.state!r}")
        leaked = leaked_answers(self.withheld_answer_identities, self.allowed_evidence)
        if leaked:
            raise SystemReferenceError(
                f"withheld answer material is inside the allowed evidence set: {leaked}. "
                "A challenge that hands over the answer proves nothing.")
        if not self.success_criteria:
            raise SystemReferenceError(
                "a challenge with no success criteria cannot be failed, so it cannot be passed")

    @property
    def challenge_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes({
            "track_id": self.track_id,
            "commission_sha256": self.commission_sha256,
            "accepted_master_authority_sha256": self.accepted_master_authority_sha256,
            "withheld_answer_identities": list(self.withheld_answer_identities),
            "allowed_evidence": [dict(row) for row in self.allowed_evidence],
            "forbidden_evidence": list(self.forbidden_evidence),
            "execution_environment": dict(self.execution_environment),
            "procedure": self.procedure,
            "evaluator": self.evaluator,
            "success_criteria": list(self.success_criteria),
        }))


def leaked_answers(withheld: tuple[str, ...],
                   allowed: tuple[Mapping[str, Any], ...]) -> list[str]:
    """Any withheld identity appearing anywhere inside the allowed evidence."""
    text = canonical_json_bytes([dict(row) for row in allowed]).decode("utf-8")
    return [identity for identity in withheld if identity and identity in text]


def prepare(*, track_id: str, commission_sha256: str, master_state: str,
            accepted_master_authority_sha256: str, withheld_answer_identities: tuple[str, ...],
            allowed_evidence: tuple[Mapping[str, Any], ...],
            forbidden_evidence: tuple[str, ...], execution_environment: Mapping[str, Any],
            procedure: str, evaluator: str,
            success_criteria: tuple[str, ...]) -> Challenge:
    """Start a challenge, refusing if the master it is meant to re-derive is unaccepted."""
    if master_state != MASTER_ACCEPTED:
        raise SystemReferenceError(
            f"{track_id} is {master_state!r}; a system reference cannot precede an accepted "
            "master, because there would be no accepted result to recover")
    if not accepted_master_authority_sha256:
        raise SystemReferenceError("the challenge binds no accepted master authority")
    challenge = Challenge(
        track_id=track_id, commission_sha256=commission_sha256,
        accepted_master_authority_sha256=accepted_master_authority_sha256,
        withheld_answer_identities=tuple(withheld_answer_identities),
        allowed_evidence=tuple(allowed_evidence),
        forbidden_evidence=tuple(forbidden_evidence),
        execution_environment=dict(execution_environment),
        procedure=procedure, evaluator=evaluator,
        success_criteria=tuple(success_criteria), state=NOT_STARTED)
    return replace(challenge, state=PREPARED)


def advance(challenge: Challenge, to_state: str, *,
            findings: tuple[str, ...] = ()) -> Challenge:
    """Move one step. Repeating the state you are already in is a no-op, not an error."""
    if to_state not in STATES:
        raise SystemReferenceError(f"unknown challenge state {to_state!r}")
    if to_state == challenge.state:
        if findings and tuple(findings) != challenge.findings:
            raise SystemReferenceError(
                "re-stating the same result with different findings is not idempotent")
        return challenge
    if to_state not in LEGAL_MOVES[challenge.state]:
        raise SystemReferenceError(
            f"{challenge.state!r} -> {to_state!r} is not a legal move; the order is "
            f"{' -> '.join(STATES[:5])} then passed or failed")
    return replace(challenge, state=to_state, findings=tuple(findings) or challenge.findings)


def result_receipt(challenge: Challenge) -> dict[str, Any]:
    """A body-free record of an evaluated challenge, whichever way it went."""
    if challenge.state not in (PASSED, FAILED):
        raise SystemReferenceError(
            f"{challenge.track_id} is {challenge.state!r}; only an evaluated challenge has a "
            "result")
    passed = challenge.state == PASSED
    return seal({
        "kind": "earcrate_album_one_public_system_reference_receipt",
        "schema_version": 1,
        "visibility": "public",
        "track_id": challenge.track_id,
        "verdict": "PASSED" if passed else "FAILED",
        "challenge_sha256": challenge.challenge_sha256,
        "commission_sha256": challenge.commission_sha256,
        "bound_authority": {
            "accepted_master_authority_sha256": challenge.accepted_master_authority_sha256,
        },
        "withheld": {
            "identity_count": len(challenge.withheld_answer_identities),
            "note": "The withheld material's identities are bound; the material is not here.",
        },
        "evaluator": challenge.evaluator,
        "success_criteria": list(challenge.success_criteria),
        "findings": list(challenge.findings),
        "state": {
            "system_reference_complete": passed,
            "completed_system_references": 1 if passed else 0,
            "accepted_album_master_revoked": False,
            "note": ("A failed challenge never revokes an accepted master. The album claim "
                     "and the autonomy claim are independent, and conflating them would "
                     "make an honest challenge something we avoid running."),
        },
        "boundary": {"private_paths_included": False, "source_audio_exported": False},
    }, "receipt_sha256")


def may_complete_system_reference(challenge: Challenge) -> bool:
    return challenge.state == PASSED
