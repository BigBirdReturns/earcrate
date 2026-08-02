from __future__ import annotations

"""Plan the local-only acceptance work that cloud and synthetic gates cannot prove."""

from collections import Counter
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from earcrate.estate.hardware import _estate_rig_now_utc
from earcrate.estate.model import (
    ESTATE_SCHEMA_VERSION,
    estate_seal,
    estate_sha256_file,
    estate_validate_seal,
    load_estate_json,
)

def _estate_tool_map(rig: Mapping[str, Any]) -> dict[str, bool]:
    return {str(row.get("name")): bool(row.get("available")) for row in rig.get("executables") or []}


def _estate_inventory_flags(inventory: Mapping[str, Any]) -> dict[str, Any]:
    items = list(inventory.get("items") or [])
    classes = Counter(str(item.get("classification") or "") for item in items)
    root_roles = Counter(str(root.get("role") or "") for root in inventory.get("roots") or [])
    pending_reviews = [
        item
        for item in items
        if item.get("classification") in {"audition_audio", "release_candidate"}
        or str((item.get("metadata") or {}).get("status") or "").lower() in {"pending", "blocked", "revise"}
    ]
    return {
        "classes": classes,
        "root_roles": root_roles,
        "has_workspace": bool(root_roles.get("workspace") or classes.get("workspace_config") or classes.get("database")),
        "has_source_audio": bool(classes.get("source_audio")),
        "has_projects": bool(classes.get("project_index") or classes.get("project_revision")),
        "has_proof_packs": bool(classes.get("proof_pack")),
        "has_release_candidates": bool(classes.get("release_candidate") or classes.get("audition_audio")),
        "pending_reviews": pending_reviews,
    }


def _estate_task(
    task_id: str,
    title: str,
    domain: str,
    status: str,
    reason: str,
    evidence_outputs: Sequence[str],
    *,
    resource: str,
    command: Sequence[str] | None = None,
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": title,
        "domain": domain,
        "status": status,
        "reason": reason,
        "resource": resource,
        "dependencies": list(dependencies),
        "command": list(command) if command else None,
        "required_evidence_outputs": list(evidence_outputs),
    }


def propose_local_acceptance_campaign(
    inventory: Mapping[str, Any],
    rig: Mapping[str, Any],
    *,
    canon_ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    estate_validate_seal(inventory)
    estate_validate_seal(rig)
    if inventory.get("kind") != "earcrate_estate_inventory" or rig.get("kind") != "earcrate_rig_capability_receipt":
        raise ValueError("campaign requires estate inventory and rig receipt")

    flags = _estate_inventory_flags(inventory)
    tools = _estate_tool_map(rig)
    packages = dict(rig.get("python_packages") or {})
    has_gpu = bool((rig.get("nvidia") or {}).get("gpus"))
    has_audio_device = bool((rig.get("audio_devices") or {}).get("available"))

    tasks: list[dict[str, Any]] = []
    tasks.append(
        _estate_task(
            "local.repo.full_gates",
            "Run the complete repository and package gates on the local machine",
            "repository",
            "ready" if tools.get("python") and tools.get("ffmpeg") and tools.get("ffprobe") else "needs_install",
            "Cloud gates do not prove the owner's exact Python, ffmpeg, filesystem, or device environment.",
            ["gate.log", "package-verifier.log", "local-gate-receipt.json"],
            resource="cpu",
            command=[sys.executable, "tests/run_gates.py"],
        )
    )
    tasks.append(
        _estate_task(
            "local.workspace.acceptance",
            "Validate current workspace, project revisions, database, source custody, and exact undo",
            "workspace",
            "ready" if flags["has_workspace"] and flags["has_projects"] else "needs_input",
            "This is the real-library acceptance boundary that synthetic fixtures cannot supply.",
            ["workspace-inventory.json", "project-validation.json", "undo-identity-receipt.json"],
            resource="cpu+disk",
            dependencies=["local.repo.full_gates"],
        )
    )
    tasks.append(
        _estate_task(
            "local.gpu.demucs_stems",
            "Run real Demucs stem custody and quality acceptance",
            "provider",
            "ready" if has_gpu and packages.get("torch") and packages.get("demucs") and flags["has_source_audio"] else ("needs_install" if has_gpu else "needs_hardware"),
            "The stem seam and synthetic tests do not prove this GPU, model, source library, bleed, reconstruction, or cache behavior.",
            ["provider-invocation.json", "stem-reconstruction.json", "stem-listening-review.json"],
            resource="gpu-exclusive",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.provider.allin1",
            "Evaluate allin1 beat/downbeat/section observations on real tracks",
            "provider",
            "ready" if packages.get("allin1") and flags["has_source_audio"] else "needs_install",
            "The deferred v0.9 organ was adapter-engineered but never accepted on the owner's library.",
            ["beat-provider-receipt.json", "downbeat-metrics.json", "transition-impact.json"],
            resource="gpu-or-cpu",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.provider.rubberband_ab",
            "Render and audition Rubber Band versus the current transform provider",
            "provider",
            "ready" if tools.get("rubberband") and packages.get("pyrubberband") and flags["has_projects"] else "needs_install",
            "A spectral fixture does not authorize a default change; matched local renders and a human ears verdict are required.",
            ["transform-a.json", "transform-b.json", "blind-ab-review.json", "engine-version-decision.json"],
            resource="cpu+human",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.provider.transcription_tournament",
            "Compare Basic Pitch and other available transcription providers on sealed PCM",
            "provider",
            "ready" if flags["has_source_audio"] and packages.get("basic-pitch") else "needs_install",
            "Transcription providers are observations, and their value must be measured by downstream musical usefulness and independent answer keys.",
            ["provider-results/", "evaluation-ledgers/", "tournament-report.json"],
            resource="gpu-or-cpu",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.private_library.rack_coverage",
            "Measure approved-library coverage of real PerformanceDemands",
            "private_library",
            "ready" if flags["has_workspace"] and flags["has_source_audio"] else "needs_input",
            "Synthetic rack fixtures do not prove that the owner's actual crate covers required roles, registers, velocities, durations, and transpose budgets.",
            ["approved-material-ledger.json", "rack-coverage.json", "unresolved-demands.json"],
            resource="cpu+disk",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.pretty_lights.provider_tournament",
            "Run the Pretty Lights multi-provider tournament over one sealed source PCM",
            "campaign",
            "ready" if flags["has_source_audio"] and flags["has_proof_packs"] else "needs_input",
            "This is the intended test of stems, beat/form, recurrence, transcription, fungibility, transport, and human preference on the same source.",
            ["sealed-request-set/", "provider-invocations/", "evaluation-ledgers/", "human-pairwise-review.json", "tournament-report.json"],
            resource="gpu+cpu+human",
            dependencies=["local.gpu.demucs_stems", "local.provider.transcription_tournament"],
        )
    )
    tasks.append(
        _estate_task(
            "local.live.audio_device",
            "Measure physical audio callback latency, underruns, and controller-safe execution",
            "live_runtime",
            "ready" if has_audio_device else "needs_audio_probe",
            "Prepared-buffer callback gates do not establish the physical device, driver, block size, or venue behavior.",
            ["audio-device-capability.json", "latency-measurement.json", "underrun-ledger.json"],
            resource="audio-device+human",
            dependencies=["local.repo.full_gates"],
        )
    )
    tasks.append(
        _estate_task(
            "local.workbench.lifecycle",
            "Drive the packaged and single-file Workbench against a scratch clone of the real workspace",
            "workbench",
            "ready" if flags["has_workspace"] else "needs_input",
            "The deferred Workbench and local browser lifecycle must be accepted without touching production state.",
            ["workbench-receipt.json", "screenshots/", "console-log.json", "production-state-before-after.json"],
            resource="cpu+browser",
            dependencies=["local.workspace.acceptance"],
        )
    )
    tasks.append(
        _estate_task(
            "local.human.audition_queue",
            "Review every pending release candidate and comparison control",
            "human_review",
            "needs_human" if flags["pending_reviews"] else "needs_input",
            "Signal sanity and recurrence metrics cannot decide whether the music works.",
            ["review-assignments.json", "human-reviews/", "review-patches/"],
            resource="human",
        )
    )
    tasks.append(
        _estate_task(
            "local.campaign.review_changes_future_choice",
            "Prove review circulation by changing a later provider ranking or musical decision",
            "evolution",
            "blocked",
            "A review that does not change later behavior is editing, not learning.",
            ["parent-decision.json", "review-patch.json", "child-revision.json", "changed-ranking-or-choice.json"],
            resource="cpu+human",
            dependencies=["local.human.audition_queue", "local.pretty_lights.provider_tournament"],
        )
    )

    canon: dict[str, Any] | None = None
    if canon_ledger_path:
        source = Path(canon_ledger_path).expanduser().resolve()
        try:
            value = load_estate_json(source)
            canon = {
                "path": str(source),
                "raw_sha256": estate_sha256_file(source),
                "ledger_sha256": value.get("effective_ledger_sha256") or value.get("ledger_sha256"),
                "open_obligations": [
                    {
                        "obligation_id": row.get("obligation_id"),
                        "status": row.get("status"),
                        "reason": row.get("reason"),
                    }
                    for row in value.get("open_obligations") or []
                ],
            }
        except Exception as exc:
            canon = {"path": str(source), "error": f"{type(exc).__name__}: {exc}"[:500]}

    audition_queue = [
        {
            "item_id": item.get("item_id"),
            "classification": item.get("classification"),
            "path": item.get("absolute_path"),
            "sha256": item.get("raw_sha256"),
            "status": (item.get("metadata") or {}).get("status"),
        }
        for item in flags["pending_reviews"]
    ]
    status_counts = Counter(task["status"] for task in tasks)
    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_local_acceptance_campaign",
        "created_at": _estate_rig_now_utc(),
        "inventory_sha256": inventory["inventory_sha256"],
        "rig_sha256": rig["rig_sha256"],
        "canon": canon,
        "tasks": tasks,
        "audition_queue": audition_queue,
        "summary": {
            "tasks": len(tasks),
            "statuses": dict(sorted(status_counts.items())),
            "audition_items": len(audition_queue),
            "gpu_available": has_gpu,
            "audio_device_inventory_available": has_audio_device,
            "workspace_present": flags["has_workspace"],
            "source_audio_present": flags["has_source_audio"],
        },
        "boundary": {
            "campaign_is_a_plan_not_execution": True,
            "cloud_gates_are_not_local_acceptance": True,
            "hardware_capability_is_not_quality": True,
            "human_audition_is_required_where_declared": True,
        },
    }
    return estate_seal(payload)


__all__ = ["propose_local_acceptance_campaign"]
