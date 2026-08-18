"""Bind the master to the decisions and the objects that authorize it.

Two receipts come out of a master run. The private manifest names paths, private
custody and the full identity chain, and stays outside Git. The public projection
carries mechanism and identity only -- no paths, no media, no credentials -- and is
the object that lands in `proofs/album_one/`.

The split is the same one the frontier used. What is new here is that the master
is the first A1-07 object that can raise the accepted-album-master counter, so the
manifest states explicitly which authority did that and which one did not: the
monitoring ratification accepts the music, and it does not complete the withheld-
answer system reference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..a1_07_gold_v8 import common as c

MASTER_ID = "a1-07-master-v1"
MASTER_VERSION = "1.0.0"
TRACK_ID = "A1-07"
DESCENT_ID = "a1-07-full-form-v1"


class MasterReceiptError(RuntimeError):
    pass


def load_monitoring_verdict(path: Path, *, accepted_pcm_sha256: str) -> dict[str, Any]:
    """Load the monitoring-room ratification and prove it ratified THIS render.

    The blind frontier verdict explicitly did not replace this event, and it warned
    that the reviewed cut was a level-matched projection. So the ratification has to
    name the production render's own canonical PCM identity; a verdict that names
    anything else is a verdict about a different object.
    """
    value = c.load_json(path)
    if value.get("kind") != "earcrate_a1_07_monitoring_ratification":
        raise MasterReceiptError(f"wrong monitoring verdict kind: {value.get('kind')}")
    if value.get("track_id") != TRACK_ID or value.get("descent_id") != DESCENT_ID:
        raise MasterReceiptError("the monitoring verdict belongs to another track or descent")
    c.validate_seal(value, "verdict_sha256")

    reviewed = str((value.get("reviewed") or {}).get("canonical_pcm_sha256") or "")
    if reviewed != accepted_pcm_sha256:
        raise MasterReceiptError(
            f"the monitoring verdict ratified {reviewed[:12]}, not the accepted render "
            f"{accepted_pcm_sha256[:12]}")
    authority = value.get("authority") or {}
    if not authority.get("human_review"):
        raise MasterReceiptError("a monitoring ratification must be a human listening event")
    if authority.get("reopens_timing_law"):
        raise MasterReceiptError(
            "the mastering pass may not reopen the timing law; that decision is sealed")
    if not value.get("constraints"):
        raise MasterReceiptError("the ratification records no constraints to master under")
    if value.get("ceiling_dbtp") is None:
        raise MasterReceiptError("the ratification sets no true-peak ceiling")
    return value


def build_manifest(
    *,
    source_render: Path,
    frontier_manifest: Mapping[str, Any],
    frontier_manifest_path: Path,
    verdict: Mapping[str, Any],
    verdict_path: Path,
    plan: Mapping[str, Any],
    rendered: Mapping[str, Any],
    verification: Mapping[str, Any],
    source_conditions: Mapping[str, Any],
    master_tree: Mapping[str, Any],
    renderer_identity: Mapping[str, Any],
    repo_root: Path,
    candidate_id: str,
    sample_rate: int,
    channels: int,
) -> dict[str, Any]:
    """The private master manifest: every identity, including the private ones."""
    accepted = next(row for row in frontier_manifest["candidates"]
                    if row["candidate_id"] == candidate_id)
    manifest = {
        "kind": "earcrate_a1_07_master_manifest",
        "schema_version": 1,
        "master_id": MASTER_ID,
        "master_version": MASTER_VERSION,
        "track_id": TRACK_ID,
        "descent_id": DESCENT_ID,
        "earcrate_git_head": c.current_git_head(repo_root),
        # Bound to the mastering code, not to the render code. See provenance.py.
        "master_tree": dict(master_tree),
        "renderer_identity": dict(renderer_identity),
        "timeline": {"sample_rate": sample_rate, "channels": channels},
        "source": {
            "role": "the owner-selected production render, not the level-matched review cut",
            "candidate_id": candidate_id,
            "artifact_path": str(source_render),
            "container_sha256": c.sha256_file(source_render),
            "canonical_pcm_sha256": accepted["canonical_pcm_sha256"],
            "duration_seconds": accepted["duration_seconds"],
            "peak_conditions": dict(source_conditions),
        },
        "authorizing_decisions": {
            "frontier_manifest_sha256": frontier_manifest["manifest_sha256"],
            "frontier_manifest_container_sha256": c.sha256_file(frontier_manifest_path),
            "frontier_contract_sha256": frontier_manifest["contract_sha256"],
            "render_provenance_digest": (frontier_manifest.get("adapter_tree") or {}).get("digest"),
            "monitoring_verdict_sha256": verdict["verdict_sha256"],
            "monitoring_verdict_container_sha256": c.sha256_file(verdict_path),
            "monitoring_constraints": list(verdict["constraints"]),
        },
        "plan": dict(plan),
        "master": dict(rendered),
        "verification": dict(verification),
        "authority": {
            "album_master_accepted": True,
            "accepted_by": "owner, monitoring-room ratification",
            "system_reference_complete": False,
            "system_reference_note": (
                "The withheld-answer recovery challenge has not run. An accepted master is "
                "the album claim; the system reference is the separate autonomy claim."),
            "rights_and_release": "separate decision, not conferred here",
            "timing_law_reopened": False,
        },
    }
    return c.seal(manifest, "master_manifest_sha256")


def build_public_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanism and identity only. No paths, no media, no private custody."""
    plan = manifest["plan"]
    verification = manifest["verification"]
    master = manifest["master"]
    decisions = manifest["authorizing_decisions"]

    projection = {
        "kind": "earcrate_album_one_public_master_receipt",
        "schema_version": 1,
        "visibility": "public",
        "track_id": TRACK_ID,
        "descent": DESCENT_ID,
        "master_id": MASTER_ID,
        "master_version": MASTER_VERSION,
        "musical_result": {
            "headline": "A1-07 has an accepted, exactly reproducible album master.",
            "detail": (
                "The owner-selected native-pocket production render was ratified in the "
                "monitoring room and mastered with a single linear gain of "
                f"{plan['solved_gain_db']:+.2f} dB to a {plan['ceiling_dbtp']} dBTP ceiling. "
                "No limiter, no EQ, no multiband, no resampling and no dither."),
            "consequence": (
                "The vertical slice from private source custody to accepted master is closed "
                "for one track. The mechanisms it proved are custody, deterministic rendering, "
                "blind owner review and linear mastering -- not arrangement synthesis."),
            "scope_limit": (
                "This accepts one album master. It does not complete the A1-07 system "
                "reference, which requires the withheld-answer recovery challenge."),
        },
        "chain": {
            "stages": ["solved linear gain"],
            "limiter": False,
            "equalization": False,
            "multiband": False,
            "resampling": False,
            "dither": False,
            "dither_note": (
                "Omitted deliberately: dither is stochastic and would break canonical PCM "
                "equality between the two required executions."),
            "gain_db": plan["solved_gain_db"],
            "gain_solved_from": "measured true peak, not chosen",
            "ceiling_dbtp": plan["ceiling_dbtp"],
        },
        "signal": {
            "source_integrated_lufs": plan["source_integrated_lufs"],
            "source_true_peak_dbtp": plan["source_true_peak_dbtp"],
            "master_integrated_lufs": verification["integrated_lufs"],
            "master_true_peak_dbtp": verification["true_peak_dbtp"],
            "master_sample_peak_dbfs": verification["sample_peak_dbfs"],
            "flat_top_runs": verification["flat_top_run_count"],
            "hard_clipped": verification["hard_clipped"],
            "true_peak_within_ceiling": verification["true_peak_within_ceiling"],
        },
        "macro_dynamics": {
            "claim": "a linear gain moves every section by the same amount",
            "section_delta_db": {name: row["delta_db"]
                                 for name, row in verification["sections"].items()},
            "max_section_gain_drift_db": verification["max_section_gain_drift_db"],
            "macro_span_lu_source": verification["macro_span_lu_source"],
            "macro_span_lu_master": verification["macro_span_lu_master"],
            "preserved": verification["macro_dynamics_preserved"],
        },
        "provenance": {
            "master_provenance_digest": manifest["master_tree"]["digest"],
            "master_provenance_member_count": manifest["master_tree"]["member_count"],
            "master_provenance_paths": manifest["master_tree"]["declared_paths"],
            "render_provenance_digest": decisions["render_provenance_digest"],
            "frontier_contract_sha256": decisions["frontier_contract_sha256"],
            "source_canonical_pcm_sha256": manifest["source"]["canonical_pcm_sha256"],
            "master_canonical_pcm_sha256": master["canonical_pcm_sha256"],
            "master_container_sha256": master["container_sha256"],
            "deterministic_executions": master["deterministic_executions"],
            "canonical_pcm_equality_across_executions":
                master["canonical_pcm_equality_across_executions"],
            "container_equality_across_executions":
                master["container_equality_across_executions"],
        },
        "private_receipts": {
            "note": "Identities only; the receipts themselves are not in Git.",
            "master_manifest_sha256": manifest["master_manifest_sha256"],
            "frontier_manifest_sha256": decisions["frontier_manifest_sha256"],
            "monitoring_verdict_sha256": decisions["monitoring_verdict_sha256"],
        },
        "review": {
            "monitoring_room_ratification": True,
            "blind": False,
            "reopens_timing_law": False,
            "constraints": decisions["monitoring_constraints"],
        },
        "state": {
            "accepted_album_master": True,
            "accepted_album_masters": 1,
            "system_reference_complete": False,
            "completed_system_references": 0,
            "rights_and_release_decided": False,
        },
        "boundary": {
            "note": ("Identity and decision only. The master audio, the source render, the "
                     "private receipts and the source custody all remain outside Git."),
            "private_paths_included": False,
            "source_audio_exported": False,
            "master_audio_exported": False,
            "stems_included": False,
        },
    }
    return c.seal(projection, "receipt_sha256")
