from __future__ import annotations

from typing import Any, Mapping

from .common import (
    HOMELAB_FORBIDDEN_SWITCHES, HOMELAB_REQUIRED_SWITCHES, ROOT,
    base_result, blocker, load, missing, powershell_switches, template_switches,
)


def homelab(track_id: str, spec: Mapping[str, Any], campaign_track: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    runner = ROOT / "scripts/RUN_HOMELAB_FACTORY.ps1"
    factory = ROOT / "scripts/earcrate_factory.py"
    suite_path = ROOT / "configs/homelab_factory/specimen-suite.v1.json"
    template = str((campaign_track.get("entrypoint") or {}).get("template") or "")
    runner_args, template_args = powershell_switches(runner), template_switches(template)
    suite = load(suite_path) if suite_path.is_file() else {}
    case_ids = {str(row.get("canonical_case_id") or "") for row in suite.get("cases") or []}
    errors: list[str] = []
    if not runner.is_file() or not factory.is_file() or not suite_path.is_file():
        errors.append("factory runner, CLI, or specimen suite is missing")
    if str(spec.get("case_id") or "") not in case_ids:
        errors.append(f"specimen case is absent: {spec.get('case_id')}")
    if not HOMELAB_REQUIRED_SWITCHES.issubset(runner_args):
        errors.append("runner parameter contract is incomplete")
    if HOMELAB_FORBIDDEN_SWITCHES & template_args:
        errors.append("campaign template uses nonexistent switches: " + ", ".join(sorted(HOMELAB_FORBIDDEN_SWITCHES & template_args)))
    if not HOMELAB_REQUIRED_SWITCHES.issubset(template_args):
        errors.append("campaign template omits the actual factory switches")
    result["tool_contract_ready"] = not errors
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    result["binding_contract_ready"] = not absent
    result["representative_invocation_ready"] = result["tool_contract_ready"] and result["binding_contract_ready"]
    result["observations"] = {
        "case_id": spec.get("case_id"), "runner_switches": sorted(runner_args),
        "campaign_template_switches": sorted(template_args),
        "provider_factory_available": runner.is_file() and factory.is_file(),
        "album_full_form_floor_enforced": False,
    }
    if errors:
        result["blockers"].append(blocker(
            "blocked_adapter_implementation", "adapter_contract", "; ".join(errors)
        ))
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_source", "binding_contract",
            "The factory requires exact source, catalog, and audit inputs before invocation.",
            missing_binding_ids=absent,
        ))
    result["blockers"].append(blocker(
        "blocked_full_form_adapter", "output_contract",
        "The Homelab factory can emit provider trials, but this lane has no wrapper enforcing setup, body, payoff, duration, and deterministic full-form reproduction.",
        minimum_duration_seconds=float(spec["minimum_seconds"]),
        maximum_duration_seconds=float(spec["maximum_seconds"]),
    ))
    return result


def gesture(track_id: str, spec: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    result["blockers"].append(blocker(
        "blocked_adapter_implementation", "adapter_contract",
        "The Stateside lane has a research specification but no executable gesture-retrieval-to-arrangement adapter."
    ))
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_source", "binding_contract",
            "The exact source and held-out positive and negative corpora are not bound.",
            missing_binding_ids=absent,
        ))
    return result


def beggin(track_id: str, spec: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    result = base_result(track_id, str(spec["adapter"]))
    runner = ROOT / "scripts/RUN_A1_07_GOLD_V9.ps1"
    contract_path = ROOT / "configs/album_one/a1-07/gold-v9-timing-laws.v1.json"
    v9 = load(contract_path)
    construction, review = v9.get("construction") or {}, v9.get("review_policy") or {}
    scope = str(construction.get("review_scope") or review.get("review_scope") or "")
    arc_deferred = bool(review.get("positive_arc_reapplication_deferred"))
    result["tool_contract_ready"] = runner.is_file() and contract_path.is_file()
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    result["binding_contract_ready"] = not absent
    result["representative_invocation_ready"] = result["tool_contract_ready"] and result["binding_contract_ready"]
    result["observations"] = {
        "review_scope": scope, "positive_arc_reapplication_deferred": arc_deferred,
        "minimum_full_form_seconds": float(spec["minimum_seconds"]),
    }
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_source", "binding_contract",
            "The qualified private v7 workspace is not bound.", missing_binding_ids=absent,
        ))
    if "core" in scope.casefold() or arc_deferred:
        result["blockers"].append(blocker(
            "blocked_full_form_adapter", "output_contract",
            "Gold v9 is explicitly a core-only timing-law diagnostic and cannot authorize a 45–120 second Album lane.",
            review_scope=scope, positive_arc_reapplication_deferred=arc_deferred,
        ))
    return result
