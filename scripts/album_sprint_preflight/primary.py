from __future__ import annotations

from typing import Any, Mapping

from .common import ROOT, base_result, blocker, load, missing


def pretty_lights(track_id: str, spec: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    candidate = load(ROOT / "proofs/specimens/pretty_lights_empire_release_candidate_v1/release_candidate.json")
    output = candidate.get("authoritative_output") or {}
    frames, rate = int(output.get("frames") or 0), int(output.get("sample_rate") or 0)
    duration, floor = (frames / rate if rate else 0.0), float(spec["minimum_seconds"])
    result["observations"] = {
        "retained_candidate_duration_seconds": duration,
        "minimum_full_form_seconds": floor,
        "retained_candidate_pcm_sha256": output.get("decoded_pcm_sha256"),
        "deterministic_full_form_entrypoint_exists": False,
    }
    result["blockers"].append(blocker(
        "blocked_adapter_implementation", "adapter_contract",
        "The retained 31-second candidate has no deterministic full-form executable adapter."
    ))
    if duration < floor:
        result["blockers"].append(blocker(
            "blocked_full_form_adapter", "output_contract",
            "The retained executable candidate is shorter than the Album Sprint floor.",
            observed_duration_seconds=duration, minimum_duration_seconds=floor,
        ))
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_source", "binding_contract",
            "Exact reference and retained-candidate custody are not both bound.",
            missing_binding_ids=absent,
        ))
    return result


def children(track_id: str, spec: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    result["tool_contract_ready"] = (
        ROOT.joinpath("earcrate/specimen/cli.py").is_file()
        and ROOT.joinpath("earcrate/specimen/children.py").is_file()
    )
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    result["binding_contract_ready"] = not absent
    result["representative_invocation_ready"] = result["tool_contract_ready"] and result["binding_contract_ready"]
    proof = load(ROOT / "proofs/specimens/children_v1.score-side.proof.json")
    result["evidence_tier"] = "authoritative_score_summary"
    result["observations"] = {
        "required_exact_artifact_ids": list(spec.get("required_bindings") or []),
        "score_note_count": ((proof.get("score_branch") or {}).get("counts") or {}).get("notes"),
        "score_branch_historical_status": (proof.get("score_branch") or {}).get("status"),
        "full_form_performance_renderer_exists": False,
    }
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_artifact_pack", "binding_contract",
            "The Children compiler requires the complete exact six-artifact score pack.",
            missing_artifact_ids=absent,
        ))
    result["blockers"].append(blocker(
        "blocked_performance_adapter", "output_contract",
        "children-score compiles a score branch but does not render the complete 105-measure performance through an approved rack.",
        minimum_duration_seconds=float(spec["minimum_seconds"]),
    ))
    return result


def flim(track_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    report = load(ROOT / "specimens/flim_bad_plus_v1.community-symbolic.json")
    result["tool_contract_ready"] = ROOT.joinpath("earcrate/specimen/cli.py").is_file()
    result["representative_invocation_ready"] = result["tool_contract_ready"]
    result["symbolic_evidence_ready"] = report.get("evidence_tier") == "community_symbolic_witness"
    result["evidence_tier"] = report.get("evidence_tier")
    duration = float((report.get("witness") or {}).get("duration_seconds") or 0.0)
    amplitude = float((report.get("reproducibility") or {}).get("decoded_float_pcm_max_abs") or 0.0)
    floor = float(spec["minimum_seconds"])
    result["observations"] = {
        "witness_duration_seconds": duration, "minimum_full_form_seconds": floor,
        "decoded_float_pcm_max_abs": amplitude,
        "whole_organism_passed": bool(report.get("whole_organism_passed")),
        "executable_note_events_present": False,
    }
    result["blockers"].extend([
        blocker(
            "blocked_performance_realization", "observed_output",
            "The retained Flim object is a symbolic summary, not executable notes; its reported audio is silent and below the full-form floor.",
            observed_duration_seconds=duration, minimum_duration_seconds=floor,
            decoded_float_pcm_max_abs=amplitude,
        ),
        blocker(
            "blocked_performance_adapter", "output_contract",
            "flim-report validates evidence and produces no audible piano, bass, drums, or transport realization."
        ),
    ])
    return result
