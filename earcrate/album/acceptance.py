"""Shared acceptance validation, bound to durable authority.

An acceptance says: this owner heard this object and accepted it. The three things
that must therefore be true forever are the object's canonical PCM, the delivered
container, and the authority under which it was produced. None of those change when
somebody commits a changelog line.

So acceptance binds `authority_sha256`. The event digest is recorded as provenance
context and is explicitly *not* the predicate: `event_sha256` moves with the
repository head, the wall clock and the hostname, and none of that changes which
musical object was accepted.

This module validates. It does not decide -- deciding is an owner listening to audio,
and no code in this repository may stand in for that.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..evidence.identity import ObjectIdentity, seal
from ..evidence.manifest import ManifestSchemaError, resolve_authority

ACCEPT = "ACCEPT_MASTER"
REVISE = "MASTER_REVISION_REQUIRED"
ADMISSIBLE = (ACCEPT, REVISE)

REOPENING_FLAGS = ("reopens_timing_law", "reopens_arrangement", "reopens_mix")


class AcceptanceValidationError(RuntimeError):
    pass


def audited_identity(verdict: Mapping[str, Any]) -> ObjectIdentity:
    audited = verdict.get("audited") or verdict.get("audited_object") or {}
    try:
        return ObjectIdentity(
            canonical_pcm_sha256=str(audited.get("canonical_pcm_sha256") or ""),
            container_sha256=str(audited.get("container_sha256") or "") or None,
            role="audited master")
    except Exception as exc:  # noqa: BLE001 - surface the reason, not the type
        raise AcceptanceValidationError(f"the verdict names no audited object: {exc}") from exc


def validate_verdict(verdict: Mapping[str, Any], *, expected: ObjectIdentity,
                     track_id: str) -> str:
    """Prove a verdict decided this exact object, and reopened nothing."""
    if verdict.get("track_id") != track_id:
        raise AcceptanceValidationError(
            f"verdict names track {verdict.get('track_id')!r}, not {track_id!r}")
    value = str(verdict.get("verdict") or "")
    if value not in ADMISSIBLE:
        raise AcceptanceValidationError(
            f"inadmissible verdict {value!r}; expected one of {ADMISSIBLE}")
    if not (verdict.get("authority") or {}).get("human_review"):
        raise AcceptanceValidationError("an acceptance must be a human listening event")
    for flag in REOPENING_FLAGS:
        if (verdict.get("authority") or {}).get(flag):
            raise AcceptanceValidationError(
                f"{flag} is set; the post-master audition reopens no frontier")

    heard = audited_identity(verdict)
    if not heard.matches(expected):
        raise AcceptanceValidationError(
            f"the verdict audited {heard.describe()}, not {expected.describe()}; an "
            "acceptance binds to one exact object")
    return value


def build_receipt(*, track_id: str, descent: str, master_id: str,
                  verdict: Mapping[str, Any], manifest: Mapping[str, Any],
                  migration: Mapping[str, Any] | None = None,
                  authorizing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The body-free acceptance receipt, bound to authority rather than to an event."""
    try:
        authority = resolve_authority(manifest, migration=migration)
    except ManifestSchemaError as exc:
        raise AcceptanceValidationError(str(exc)) from exc

    expected = ObjectIdentity(
        canonical_pcm_sha256=str(_authority_field(manifest, "canonical_pcm_sha256")),
        container_sha256=str(_authority_field(manifest, "container_sha256")) or None,
        role="delivered master")
    decision = validate_verdict(verdict, expected=expected, track_id=track_id)
    accepted = decision == ACCEPT

    return seal({
        "kind": "earcrate_album_one_public_master_acceptance_receipt",
        "schema_version": 2,
        "visibility": "public",
        "track_id": track_id,
        "descent": descent,
        "master_id": master_id,
        "verdict": decision,
        "audited_object": {
            "canonical_pcm_sha256": expected.canonical_pcm_sha256,
            "container_sha256": expected.container_sha256,
            "note": ("The accepted object is the mastered PCM and the delivered container, "
                     "not the chain that produced them."),
        },
        "bound_authority": {
            "authority_sha256": authority,
            "note": ("Acceptance binds the stable authority identity: what was rendered, "
                     "from which inputs and which audio-affecting code."),
        },
        "provenance_context": {
            "event_sha256": manifest.get("event_sha256"),
            "note": ("Audit only. The event identity moves with the repository head, the "
                     "wall clock and the host, none of which change what was accepted."),
        },
        "authorizing_chain": dict(authorizing or {}),
        "state": {
            "owner_master_acceptance": accepted,
            "accepted_album_master": accepted,
            "accepted_album_masters": 1 if accepted else 0,
            "system_reference_complete": False,
            "completed_system_references": 0,
            "rights_and_release_decided": False,
        },
        "boundary": {
            "note": ("Identity and decision only. The mastered audio, the source render "
                     "and the private receipts all remain outside Git."),
            "private_paths_included": False,
            "master_audio_exported": False,
            "source_audio_exported": False,
        },
    }, "receipt_sha256")


def _authority_field(manifest: Mapping[str, Any], name: str) -> Any:
    """Read a stable field from either schema, without guessing which one it is."""
    if int(manifest.get("schema_version") or 0) == 2:
        return (manifest.get("authority") or {}).get(name, "")
    return (manifest.get("master") or {}).get(name, "")
