from __future__ import annotations

"""Independent score/audio convergence for Buffalo Gate specimens."""

import statistics
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .model import (
    CONVERGENCE_REPORT_SCHEMA_VERSION,
    SpecimenError,
    specimen_normalize_convergence_policy,
    specimen_observations_by_kind,
    specimen_sha256_json,
    specimen_validate_branch_isolation,
    specimen_validate_observation_ledger,
)


def _single(ledger: Mapping[str, Any], kind: str) -> dict[str, Any]:
    rows = specimen_observations_by_kind(ledger, kind)
    if len(rows) != 1:
        raise SpecimenError(f"{ledger.get('branch')} ledger requires exactly one {kind} observation")
    value = rows[0].get("value")
    if not isinstance(value, Mapping):
        raise SpecimenError(f"{kind} observation value must be an object")
    return deepcopy(dict(value))


def _metric(name: str, value: Any, threshold: Any, passed: bool, **details: Any) -> dict[str, Any]:
    return {
        "metric": str(name),
        "value": value,
        "threshold": threshold,
        "passed": bool(passed),
        "details": deepcopy(details),
    }


def specimen_nearest_note_pairs(
    expected: Sequence[Mapping[str, Any]],
    observed: Sequence[Mapping[str, Any]],
    *,
    tolerance_steps: float,
) -> tuple[int, list[float]]:
    remaining = set(range(len(observed)))
    matched = 0
    errors: list[float] = []
    for target in sorted(expected, key=lambda row: (float(row["performed_step"]), int(row["pitch"]))):
        candidates = [
            (
                abs(float(observed[index]["performed_step"]) - float(target["performed_step"])),
                index,
            )
            for index in remaining
            if int(observed[index]["pitch"]) == int(target["pitch"])
        ]
        if not candidates:
            continue
        error, index = min(candidates)
        if error <= float(tolerance_steps):
            remaining.remove(index)
            matched += 1
            errors.append(float(error))
    return matched, errors


def specimen_compare_score_audio(
    score_ledger: Mapping[str, Any],
    audio_ledger: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare sealed branches only after enforcing lineage isolation.

    The audio branch is not permitted to cite score artifacts, score-derived MIDI,
    score renders, chord labels, form answers, or convergence artifacts. A lineage
    violation refuses before any similarity metric is calculated.
    """

    specimen_validate_observation_ledger(score_ledger)
    specimen_validate_observation_ledger(audio_ledger)
    specimen_validate_branch_isolation(score_ledger)
    specimen_validate_branch_isolation(audio_ledger)
    if str(score_ledger.get("branch") or "") != "score":
        raise SpecimenError("convergence score input must be a score branch")
    if str(audio_ledger.get("branch") or "") != "audio":
        raise SpecimenError("convergence audio input must be an audio branch")
    if str(score_ledger.get("specimen_id") or "") != str(audio_ledger.get("specimen_id") or ""):
        raise SpecimenError("score and audio branches belong to different specimens")
    if str(score_ledger.get("ledger_sha256") or "") == str(audio_ledger.get("ledger_sha256") or ""):
        raise SpecimenError("score and audio branches must be independently sealed ledgers")

    normalized_policy = specimen_normalize_convergence_policy(policy)
    metrics: list[dict[str, Any]] = []

    score_tempo = float(_single(score_ledger, "tempo")["bpm"])
    audio_tempo = float(_single(audio_ledger, "tempo")["bpm"])
    tempo_error = abs(score_tempo - audio_tempo)
    metrics.append(
        _metric(
            "tempo",
            round(tempo_error, 9),
            normalized_policy["tempo_abs_error_bpm_max"],
            tempo_error <= normalized_policy["tempo_abs_error_bpm_max"],
            score_bpm=score_tempo,
            audio_bpm=audio_tempo,
        )
    )

    score_meter = _single(score_ledger, "meter")
    audio_meter = _single(audio_ledger, "meter")
    meter_match = (
        int(score_meter.get("numerator") or 0) == int(audio_meter.get("numerator") or -1)
        and int(score_meter.get("denominator") or 0) == int(audio_meter.get("denominator") or -1)
    )
    metrics.append(
        _metric(
            "meter",
            meter_match,
            bool(normalized_policy["meter_exact"]),
            meter_match if normalized_policy["meter_exact"] else True,
            score=score_meter,
            audio=audio_meter,
        )
    )

    score_key = _single(score_ledger, "key_signature")
    audio_key = _single(audio_ledger, "key_signature")
    root_match = int(score_key.get("tonic_pc", -1)) == int(audio_key.get("tonic_pc", -2))
    mode_match = str(score_key.get("mode") or "") == str(audio_key.get("mode") or "")
    key_pass = (
        (root_match or not normalized_policy["key_root_exact"])
        and (mode_match or not normalized_policy["key_mode_exact"])
    )
    metrics.append(
        _metric(
            "key",
            {"root_match": root_match, "mode_match": mode_match},
            {
                "root_exact": normalized_policy["key_root_exact"],
                "mode_exact": normalized_policy["key_mode_exact"],
            },
            key_pass,
            score=score_key,
            audio=audio_key,
        )
    )

    score_notes = [
        {
            "pitch": int(row["value"]["pitch"]),
            "performed_step": float(row["value"]["performed_step"]),
        }
        for row in specimen_observations_by_kind(score_ledger, "performed_note")
    ]
    audio_notes = [
        {
            "pitch": int(row["value"]["pitch"]),
            "performed_step": float(row["value"]["performed_step"]),
        }
        for row in specimen_observations_by_kind(audio_ledger, "performed_note")
    ]
    matches, onset_errors = specimen_nearest_note_pairs(
        score_notes,
        audio_notes,
        tolerance_steps=max(4.0, normalized_policy["note_onset_mae_steps_max"] * 4.0),
    )
    pitch_recall = matches / max(1, len(score_notes))
    onset_mae = statistics.fmean(onset_errors) if onset_errors else float("inf")
    metrics.append(
        _metric(
            "note_pitch_recall",
            round(pitch_recall, 9),
            normalized_policy["note_pitch_recall_min"],
            pitch_recall >= normalized_policy["note_pitch_recall_min"],
            matched=matches,
            expected=len(score_notes),
            observed=len(audio_notes),
        )
    )
    metrics.append(
        _metric(
            "note_onset_mae_steps",
            None if onset_mae == float("inf") else round(onset_mae, 9),
            normalized_policy["note_onset_mae_steps_max"],
            onset_mae <= normalized_policy["note_onset_mae_steps_max"],
            matched=matches,
        )
    )

    score_harmony = {
        int(row["value"]["performed_measure_index"]): int(row["value"]["root_pc"])
        for row in specimen_observations_by_kind(score_ledger, "performed_harmony")
    }
    audio_harmony = {
        int(row["value"]["performed_measure_index"]): int(row["value"]["root_pc"])
        for row in specimen_observations_by_kind(audio_ledger, "performed_harmony")
    }
    comparable = sorted(set(score_harmony) & set(audio_harmony))
    harmony_matches = sum(score_harmony[index] == audio_harmony[index] for index in comparable)
    harmony_recall = harmony_matches / max(1, len(score_harmony))
    metrics.append(
        _metric(
            "harmony_root_recall",
            round(harmony_recall, 9),
            normalized_policy["harmony_root_recall_min"],
            harmony_recall >= normalized_policy["harmony_root_recall_min"],
            matched=harmony_matches,
            expected=len(score_harmony),
            comparable=len(comparable),
        )
    )

    metric_map = {str(row["metric"]): row for row in metrics}
    missing = sorted(set(normalized_policy["required_metrics"]) - set(metric_map))
    if missing:
        raise SpecimenError(f"convergence omitted required metrics: {missing}")
    complete = all(bool(metric_map[name]["passed"]) for name in normalized_policy["required_metrics"])
    report = {
        "schema_version": CONVERGENCE_REPORT_SCHEMA_VERSION,
        "kind": "earcrate_convergence_report",
        "specimen_id": str(score_ledger["specimen_id"]),
        "score_ledger_sha256": str(score_ledger["ledger_sha256"]),
        "audio_ledger_sha256": str(audio_ledger["ledger_sha256"]),
        "policy": normalized_policy,
        "independence": {
            "score_branch": True,
            "audio_branch": True,
            "distinct_ledgers": True,
            "audio_score_taint": False,
        },
        "metrics": metrics,
        "required_metric_count": len(normalized_policy["required_metrics"]),
        "passed_metric_count": sum(bool(metric_map[name]["passed"]) for name in normalized_policy["required_metrics"]),
        "complete": complete,
    }
    report["report_sha256"] = specimen_sha256_json(report)
    return report


__all__ = ["specimen_nearest_note_pairs", "specimen_compare_score_audio"]
