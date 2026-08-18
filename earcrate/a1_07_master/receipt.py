"""Bind the master to the decisions and the objects that authorize it.

Two receipts come out of a master run. The private manifest names paths, private
custody and the full identity chain, and stays outside Git. The public projection
carries mechanism and identity only -- no paths, no media, no credentials -- and is
the object that lands in `proofs/album_one/`.

The split is the same one the frontier used. What is new here is the distinction
between three states that are easy to collapse into one:

* `frontier_selected` -- the owner picked a timing law from a blind frontier;
* `master_qualified` -- a deterministic, compliant master exists and every signal
  gate passed;
* `master_accepted` -- the owner listened to the mastered object and accepted it.

The monitoring ratification is an `ACCEPT_FOR_MASTERING` verdict. It accepts the
production render and authorizes the chain; it does not accept the mastered WAV,
because that object did not exist when the verdict was given. A master can
therefore be fully qualified and still unaccepted, and nothing in this module may
advance the accepted-album-master counter. Only `acceptance.py`, holding a verdict
that names the mastered PCM itself, can do that.

The transformation being mathematically transparent -- a linear gain of exactly
+2.5 dB -- is not a substitute for the audition. Evidence doctrine does not let an
inference stand in for a listening decision, however tight the inference is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..a1_07_gold_v8 import common as c

MASTER_ID = "a1-07-master-v1"
MASTER_VERSION = "1.0.0"
TRACK_ID = "A1-07"
DESCENT_ID = "a1-07-full-form-v1"

# The only monitoring verdict that authorizes a master to be cut. It is deliberately
# not spelled "ACCEPT": the object it accepted is the production render.
MONITORING_VERDICT = "ACCEPT_FOR_MASTERING"

# The three states the lane distinguishes. A master may reach the middle one on
# machine evidence alone; only an owner audition reaches the last.
FRONTIER_SELECTED = "frontier_selected"
MASTER_QUALIFIED = "master_qualified"
MASTER_ACCEPTED = "master_accepted"
MASTER_STATES = (FRONTIER_SELECTED, MASTER_QUALIFIED, MASTER_ACCEPTED)

QUALIFIED_PENDING_AUDITION = "technically_qualified_pending_owner_audition"


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
    if value.get("verdict") != MONITORING_VERDICT:
        raise MasterReceiptError(
            f"the monitoring verdict is {value.get('verdict')!r}, not {MONITORING_VERDICT}")
    if (value.get("disposition") or {}).get("accepts_mastered_object"):
        raise MasterReceiptError(
            "a monitoring verdict cannot accept the mastered object; it predates it")
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
            "master_state": MASTER_QUALIFIED,
            "album_master_accepted": False,
            "monitoring_verdict": MONITORING_VERDICT,
            "monitoring_verdict_accepted": (
                "the production render, and authorization to master it"),
            "monitoring_verdict_did_not_accept": (
                "the mastered object, which did not exist when the verdict was given"),
            "awaiting": (
                "a narrow post-master audition of the mastered PCM against the accepted "
                "production render, admitting ACCEPT_MASTER or MASTER_REVISION_REQUIRED"),
            "system_reference_complete": False,
            "system_reference_note": (
                "The withheld-answer recovery challenge has not run. An accepted master would "
                "be the album claim; the system reference is the separate autonomy claim."),
            "rights_and_release": "separate decision, not conferred here",
            "timing_law_reopened": False,
        },
    }
    return c.seal(manifest, "master_manifest_sha256")


def rebind_manifest(manifest: Mapping[str, Any], *, verdict: Mapping[str, Any],
                    verdict_path: Path, master_tree: Mapping[str, Any],
                    repo_root: Path) -> dict[str, Any]:
    """Re-seal an existing manifest against a corrected verdict, without recutting.

    The audio is not a function of the verdict text, so a corrected verdict must not
    demand a new master. The caller proves the files on disk are still the object the
    manifest names; this swaps the authorizing identities and re-seals.
    """
    value = {key: item for key, item in manifest.items() if key != "master_manifest_sha256"}
    decisions = dict(value["authorizing_decisions"])
    decisions["monitoring_verdict_sha256"] = verdict["verdict_sha256"]
    decisions["monitoring_verdict_container_sha256"] = c.sha256_file(verdict_path)
    decisions["monitoring_constraints"] = list(verdict["constraints"])
    decisions["monitoring_verdict"] = verdict["verdict"]
    value["authorizing_decisions"] = decisions
    value["master_tree"] = dict(master_tree)
    value["earcrate_git_head"] = c.current_git_head(repo_root)
    return c.seal(value, "master_manifest_sha256")


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
        "master_state": QUALIFIED_PENDING_AUDITION,
        "musical_result": {
            "headline": ("A1-07 has a technically qualified master awaiting its owner "
                         "audition."),
            "detail": (
                "The owner-selected native-pocket production render was ratified for "
                "mastering in the monitoring room and mastered with a single linear gain of "
                f"{plan['solved_gain_db']:+.2f} dB to a {plan['ceiling_dbtp']} dBTP ceiling. "
                "No limiter, no EQ, no multiband, no resampling and no dither."),
            "consequence": (
                "Every machine claim a master can carry is now carried: determinism, ceiling, "
                "section invariance and signal condition. The listening claim is not, because "
                "the mastered object has not been heard."),
            "scope_limit": (
                "This accepts nothing. The monitoring verdict accepted the production render "
                "and authorized the chain; the mastered WAV postdates it. Neither the album "
                "master counter nor the system reference moves on this receipt."),
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
            "monitoring_verdict": decisions.get("monitoring_verdict", MONITORING_VERDICT),
            "monitoring_verdict_scope": (
                "the production render and the authorization to master it, not the mastered "
                "object"),
            "post_master_audition_complete": False,
            "blind": False,
            "reopens_timing_law": False,
            "constraints": decisions["monitoring_constraints"],
        },
        "state": {
            "master_state": MASTER_QUALIFIED,
            "owner_frontier_selected": True,
            "owner_monitoring_acceptance": True,
            "authorized_for_mastering": True,
            "mastering_chain_qualified": True,
            "deterministic_master_pair": True,
            "owner_master_acceptance": False,
            "accepted_album_master": False,
            "accepted_album_masters": 0,
            "system_reference_complete": False,
            "completed_system_references": 0,
            "rights_and_release_decided": False,
        },
        "next_decision": (
            "A narrow post-master audition of the mastered PCM against the already accepted "
            "production render. Admissible outcomes are ACCEPT_MASTER and "
            "MASTER_REVISION_REQUIRED. No timing, arrangement or mix frontier is reopened."),
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
