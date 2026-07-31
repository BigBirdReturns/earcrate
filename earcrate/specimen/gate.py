from __future__ import annotations

"""Assemble a specimen-level Buffalo Gate without laundering missing organs as success."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .convergence import specimen_compare_score_audio
from .model import (
    SpecimenError,
    specimen_normalize_manifest,
    specimen_read_json,
    specimen_seal_gate_receipt,
    specimen_validate_observation_ledger,
    specimen_write_json_atomic,
)


def _organ(
    organ_id: str,
    status: str,
    *,
    required: bool = True,
    artifact_sha256s: list[str] | None = None,
    checks: Mapping[str, Any] | None = None,
    blockers: list[str] | None = None,
    failures: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "organ_id": str(organ_id),
        "status": str(status),
        "required": bool(required),
        "artifact_sha256s": list(artifact_sha256s or []),
        "checks": deepcopy(dict(checks or {})),
        "blockers": list(blockers or []),
        "failures": list(failures or []),
        "metadata": deepcopy(dict(metadata or {})),
    }


def _optional_receipt(path: str | Path | None, *, label: str) -> dict[str, Any] | None:
    if path in {None, ""}:
        return None
    value = specimen_read_json(Path(path).expanduser().resolve())
    if not value:
        raise SpecimenError(f"{label} receipt is empty")
    return value


def specimen_build_buffalo_gate(
    *,
    manifest: Mapping[str, Any],
    score_ledger: Mapping[str, Any],
    score_branch_receipt: Mapping[str, Any],
    output_path: str | Path | None = None,
    audio_ledger: Mapping[str, Any] | None = None,
    convergence_policy: Mapping[str, Any] | None = None,
    continuation_receipt: Mapping[str, Any] | None = None,
    rack_receipt: Mapping[str, Any] | None = None,
    review_receipt: Mapping[str, Any] | None = None,
    evolution_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_manifest = specimen_normalize_manifest(manifest)
    specimen_id = str(normalized_manifest["specimen_id"])
    specimen_validate_observation_ledger(score_ledger)
    if str(score_ledger.get("branch") or "") != "score":
        raise SpecimenError("Buffalo Gate score ledger is not a score branch")
    if str(score_ledger.get("specimen_id") or "") != specimen_id:
        raise SpecimenError("Buffalo Gate score ledger belongs to another specimen")
    if str(score_branch_receipt.get("kind") or "") != "earcrate_children_score_branch_receipt":
        raise SpecimenError("Buffalo Gate requires a Children score-branch receipt")
    if str(score_branch_receipt.get("specimen_id") or "") != specimen_id:
        raise SpecimenError("score-branch receipt belongs to another specimen")
    if str(score_branch_receipt.get("score_ledger_sha256") or "") != str(score_ledger.get("ledger_sha256") or ""):
        raise SpecimenError("score-branch receipt does not bind the supplied score ledger")
    score_checks = dict(score_branch_receipt.get("checks") or {})
    score_sha = str(score_branch_receipt.get("score_ledger_sha256") or "")

    organs: list[dict[str, Any]] = [
        _organ(
            "score_custody",
            "passed" if bool(score_checks.get("score_artifact_custody")) else "failed",
            artifact_sha256s=[score_sha],
            checks={"bound_source_identities": bool(score_checks.get("score_artifact_custody"))},
        ),
        _organ(
            "notation_perception",
            "passed" if bool(score_checks.get("score_midi_note_identity")) else "failed",
            artifact_sha256s=[score_sha],
            checks={
                "score_midi_note_identity": bool(score_checks.get("score_midi_note_identity")),
                "note_count": int((score_branch_receipt.get("counts") or {}).get("notes") or 0),
            },
            metadata={"interpretive_limits_remain_explicit": True},
        ),
        _organ(
            "form_graph",
            "passed" if bool(score_checks.get("form_graph_path_complete")) else "failed",
            artifact_sha256s=[
                str(score_branch_receipt.get("form_graph_sha256") or ""),
                str(score_branch_receipt.get("performance_path_sha256") or ""),
            ],
            checks={
                "printed_measure_count": int((score_branch_receipt.get("counts") or {}).get("printed_measures") or 0),
                "performed_measure_count": int((score_branch_receipt.get("counts") or {}).get("performed_measures") or 0),
                "path_complete": bool(score_checks.get("form_graph_path_complete")),
            },
        ),
        _organ(
            "harmony_frames",
            "passed" if bool(score_checks.get("printed_harmony_canonicalized")) else "failed",
            artifact_sha256s=[str(score_branch_receipt.get("answer_key_sha256") or "")],
            checks={
                "canonicalized": bool(score_checks.get("printed_harmony_canonicalized")),
                "printed_symbol_count": int((score_branch_receipt.get("counts") or {}).get("printed_chord_symbols") or 0),
                "performed_frame_count": int((score_branch_receipt.get("counts") or {}).get("harmony_frames") or 0),
            },
        ),
        _organ(
            "exact_midi_authority",
            "passed" if bool(score_checks.get("score_midi_note_identity")) else "failed",
            artifact_sha256s=[str(score_branch_receipt.get("midi_semantic_sha256") or "")],
            checks={"semantic_note_reconciliation": bool(score_checks.get("score_midi_note_identity"))},
        ),
        _organ(
            "mixscore_source_transports",
            "passed"
            if bool(score_checks.get("mixscore_execution_complete")) and bool(score_checks.get("mixscore_stems_reconcile"))
            else "failed",
            artifact_sha256s=[str((score_branch_receipt.get("mixscore") or {}).get("score_sha256") or "")],
            checks=deepcopy(dict(score_branch_receipt.get("mixscore") or {})),
        ),
    ]

    convergence_report: dict[str, Any] | None = None
    if audio_ledger is None:
        organs.extend(
            [
                _organ(
                    "cephalopod_audio_inference",
                    "blocked",
                    blockers=["independent reference recording is not bound", "audio ObservationLedger is not sealed"],
                ),
                _organ(
                    "cross_modal_convergence",
                    "blocked",
                    blockers=["independent score and audio branches have not both been sealed"],
                ),
            ]
        )
    else:
        specimen_validate_observation_ledger(audio_ledger)
        convergence_report = specimen_compare_score_audio(
            score_ledger,
            audio_ledger,
            policy=convergence_policy,
        )
        organs.append(
            _organ(
                "cephalopod_audio_inference",
                "passed",
                artifact_sha256s=[str(audio_ledger["ledger_sha256"])],
                checks={
                    "branch_isolated": True,
                    "observation_count": len(audio_ledger.get("observations") or []),
                },
            )
        )
        organs.append(
            _organ(
                "cross_modal_convergence",
                "passed" if bool(convergence_report["complete"]) else "failed",
                artifact_sha256s=[str(convergence_report["report_sha256"])],
                checks={
                    "required_metric_count": int(convergence_report["required_metric_count"]),
                    "passed_metric_count": int(convergence_report["passed_metric_count"]),
                },
                failures=[] if convergence_report["complete"] else [
                    str(row["metric"]) for row in convergence_report["metrics"] if not bool(row["passed"])
                ],
            )
        )

    continuation = continuation_receipt
    continuation_midi = dict((continuation or {}).get("midi") or {})
    continuation_novelty = dict((continuation or {}).get("novelty") or {})
    continuation_ok = bool(
        continuation
        and continuation.get("legal") is True
        and continuation.get("negative_control_refused") is True
        and continuation.get("rhythmic_identity_passed") is True
        and int(continuation.get("open_obligation_count", -1)) == 0
        and int(continuation_midi.get("selected_event_count") or 0) > 0
        and int(continuation_midi.get("selected_event_count") or 0) == int(continuation_midi.get("executed_event_count") or -1)
        and int(continuation_midi.get("refused_event_count") or 0) == 0
        and continuation_novelty.get("literal_copy_detected") is False
        and continuation_novelty.get("pitch_sequence_changed") is True
        and continuation_novelty.get("harmony_sequence_changed") is True
    )
    organs.append(
        _organ(
            "proof_carrying_adjacent_move",
            "passed" if continuation_ok else "blocked",
            artifact_sha256s=[] if not continuation else [str(continuation.get("receipt_sha256") or continuation.get("proof_sha256") or "")],
            blockers=[] if continuation_ok else ["no specimen-specific legal continuation and illegal negative control are sealed"],
            checks={} if not continuation else deepcopy(dict(continuation)),
        )
    )

    rack = rack_receipt
    rack_ok = bool(
        rack
        and rack.get("complete") is True
        and int(rack.get("selected_event_count") or 0) == int(rack.get("executed_event_count") or -1)
        and int(rack.get("refused_event_count") or 0) == 0
    )
    organs.append(
        _organ(
            "sealed_rack_realization",
            "passed" if rack_ok else "blocked",
            artifact_sha256s=[] if not rack else [str(rack.get("receipt_sha256") or "")],
            blockers=[] if rack_ok else ["the accepted specimen performance has not executed through sealed approved-library racks"],
            checks={} if not rack else deepcopy(dict(rack)),
        )
    )

    review = review_receipt
    review_ok = bool(
        review
        and review.get("child_revision_created") is True
        and review.get("selective_recomputation_proved") is True
        and review.get("historical_revision_preserved") is True
    )
    organs.append(
        _organ(
            "review_patch_circulation",
            "passed" if review_ok else "blocked",
            artifact_sha256s=[] if not review else [str(review.get("receipt_sha256") or "")],
            blockers=[] if review_ok else ["no listening correction has created a child revision with selective recomputation"],
            checks={} if not review else deepcopy(dict(review)),
        )
    )

    evolution = evolution_receipt
    evolution_ok = bool(
        evolution
        and evolution.get("later_decision_changed") is True
        and evolution.get("causal_evidence_recorded") is True
    )
    organs.append(
        _organ(
            "campaign_evolution",
            "passed" if evolution_ok else "blocked",
            artifact_sha256s=[] if not evolution else [str(evolution.get("receipt_sha256") or "")],
            blockers=[] if evolution_ok else ["accumulated campaign evidence has not yet changed a later musical decision"],
            checks={} if not evolution else deepcopy(dict(evolution)),
        )
    )

    gate = specimen_seal_gate_receipt(
        {
            "schema_version": 1,
            "kind": "earcrate_buffalo_gate_receipt",
            "specimen_id": specimen_id,
            "manifest_sha256": normalized_manifest["manifest_sha256"],
            "organs": organs,
            "metadata": {
                "score_branch_sha256": score_sha,
                "convergence_report_sha256": None if convergence_report is None else convergence_report["report_sha256"],
                "organ_level_success_is_not_thesis_level_success": True,
            },
        }
    )
    if output_path not in {None, ""}:
        specimen_write_json_atomic(output_path, gate)
    return {
        "ok": gate["overall_status"] != "failed",
        "buffalo_gate_passed": bool(gate["buffalo_gate_passed"]),
        "overall_status": str(gate["overall_status"]),
        "receipt": gate,
        "convergence_report": convergence_report,
    }


__all__ = ["specimen_build_buffalo_gate"]
