"""The post-master audition: the only thing that can accept an album master.

Every other receipt in this lane is machine evidence or an authorization. The
frontier verdict chose a timing law from a blind comparison. The monitoring verdict
accepted the production render and authorized a master to be cut. Neither of them
heard the mastered object, because it did not exist yet.

So acceptance lives here, and it is deliberately narrow. The verdict must name the
mastered PCM and the mastered container by identity -- not the render it came from,
not "the master", not a path -- and it admits exactly two outcomes. Anything else
leaves the counter where it is.

The tempting shortcut is the reason this module exists. The transfer is a linear
gain of a known size, so the mastered object is a mathematically transparent
function of an already accepted one, and it is very easy to argue that hearing it
is a formality. That argument is an inference standing in for a listening decision,
and this repository does not let an inference do that job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..a1_07_gold_v8 import common as c
from .receipt import DESCENT_ID, MASTER_ACCEPTED, MASTER_ID, MASTER_QUALIFIED, TRACK_ID

ACCEPT = "ACCEPT_MASTER"
REVISE = "MASTER_REVISION_REQUIRED"
ADMISSIBLE = (ACCEPT, REVISE)


class AcceptanceError(RuntimeError):
    pass


def load_master_verdict(path: Path, *, master_pcm_sha256: str,
                        master_container_sha256: str) -> dict[str, Any]:
    """Load a post-master audition verdict and prove it heard THIS master."""
    value = c.load_json(path)
    if value.get("kind") != "earcrate_a1_07_master_acceptance_verdict":
        raise AcceptanceError(f"wrong acceptance verdict kind: {value.get('kind')}")
    if value.get("track_id") != TRACK_ID or value.get("descent_id") != DESCENT_ID:
        raise AcceptanceError("the acceptance verdict belongs to another track or descent")
    c.validate_seal(value, "verdict_sha256")

    verdict = str(value.get("verdict") or "")
    if verdict not in ADMISSIBLE:
        raise AcceptanceError(f"inadmissible master verdict: {verdict!r}; expected {ADMISSIBLE}")

    audited = value.get("audited") or {}
    if str(audited.get("canonical_pcm_sha256") or "") != master_pcm_sha256:
        raise AcceptanceError(
            f"the verdict audited {str(audited.get('canonical_pcm_sha256'))[:12]}, not the "
            f"mastered PCM {master_pcm_sha256[:12]}")
    if str(audited.get("container_sha256") or "") != master_container_sha256:
        raise AcceptanceError(
            "the verdict names a different container than the mastered file; a master is "
            "accepted as an exact object, not as a description")
    if not (value.get("authority") or {}).get("human_review"):
        raise AcceptanceError("a master acceptance must be a human listening event")
    for reopened in ("reopens_timing_law", "reopens_arrangement", "reopens_mix"):
        if (value.get("authority") or {}).get(reopened):
            raise AcceptanceError(
                f"{reopened} is set; the post-master audition reopens no frontier")
    return value


def build_acceptance_receipt(manifest: Mapping[str, Any],
                             verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The body-free receipt that either advances the counter or explains why not."""
    if (manifest.get("authority") or {}).get("master_state") != MASTER_QUALIFIED:
        raise AcceptanceError(
            "only a qualified master can be auditioned; qualify it before accepting it")

    accepted = verdict["verdict"] == ACCEPT
    master = manifest["master"]
    decisions = manifest["authorizing_decisions"]

    receipt = {
        "kind": "earcrate_album_one_public_master_acceptance_receipt",
        "schema_version": 1,
        "visibility": "public",
        "track_id": TRACK_ID,
        "descent": DESCENT_ID,
        "master_id": MASTER_ID,
        "verdict": verdict["verdict"],
        "master_state": MASTER_ACCEPTED if accepted else MASTER_QUALIFIED,
        "audition": {
            "kind": "narrow post-master audition against the accepted production render",
            "blind": False,
            "human_review": True,
            "reopened_frontiers": [],
            "admissible_outcomes": list(ADMISSIBLE),
            "findings": verdict.get("findings"),
        },
        "audited_object": {
            "canonical_pcm_sha256": master["canonical_pcm_sha256"],
            "container_sha256": master["container_sha256"],
            "source_canonical_pcm_sha256": manifest["source"]["canonical_pcm_sha256"],
            "note": ("The accepted object is the mastered PCM itself, not the render it was "
                     "derived from and not the chain that derived it."),
        },
        "authorizing_chain": {
            "frontier_selected_by": "sealed blind owner verdict",
            "authorized_for_mastering_by": decisions.get("monitoring_verdict"),
            "monitoring_verdict_sha256": decisions["monitoring_verdict_sha256"],
            "master_manifest_sha256": manifest["master_manifest_sha256"],
            "acceptance_verdict_sha256": verdict["verdict_sha256"],
        },
        "state": {
            "owner_master_acceptance": accepted,
            "accepted_album_master": accepted,
            "accepted_album_masters": 1 if accepted else 0,
            "system_reference_complete": False,
            "completed_system_references": 0,
            "rights_and_release_decided": False,
        },
        "scope_limit": (
            "This decides one album master and nothing else. The withheld-answer recovery "
            "challenge remains a separate unmet contract, and rights remain a third decision."),
        "boundary": {
            "note": ("Identity and decision only. The mastered audio, the source render and "
                     "the private receipts all remain outside Git."),
            "private_paths_included": False,
            "master_audio_exported": False,
            "source_audio_exported": False,
        },
    }
    return c.seal(receipt, "receipt_sha256")
