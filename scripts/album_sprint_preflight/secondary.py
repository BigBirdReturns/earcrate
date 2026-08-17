from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .common import (
    HOMELAB_FORBIDDEN_SWITCHES, HOMELAB_REQUIRED_SWITCHES, ROOT,
    base_result, blocker, current_git_head, load, missing, powershell_switches,
    require_seal, template_switches, worktree_is_clean,
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
        errors.append("campaign has no executable full-form factory command")
    result["tool_contract_ready"] = not errors
    absent = missing(bindings, list(spec.get("required_bindings") or []))
    result["binding_contract_ready"] = not absent
    result["representative_invocation_ready"] = False
    result["observations"] = {
        "case_id": spec.get("case_id"), "runner_switches": sorted(runner_args),
        "campaign_template_switches": sorted(template_args),
        "provider_factory_available": runner.is_file() and factory.is_file(),
        "album_full_form_floor_enforced": False,
        "representative_invocation_receipt_bound": False,
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
    result["blockers"].extend([
        blocker(
            "blocked_representative_invocation", "execution_evidence",
            "No exact-head receipt proves a complete Album-form invocation for this case."
        ),
        blocker(
            "blocked_full_form_adapter", "output_contract",
            "The Homelab factory can emit provider trials, but this lane has no wrapper enforcing setup, body, payoff, duration, and deterministic full-form reproduction.",
            minimum_duration_seconds=float(spec["minimum_seconds"]),
            maximum_duration_seconds=float(spec["maximum_seconds"]),
        ),
    ])
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
    """Derive A1-07 readiness from the full-form adapter and its execution receipt.

    This probe used to hardcode `representative_invocation_ready = False` and read
    full-form readiness off the gold-v9 diagnostic's declared scope. Both were
    statements about a contract rather than about anything that ran, so the lane
    could never move no matter what was executed. Every flag below is now derived
    from evidence: the adapter must exist and validate, the private bindings must
    verify, and a sealed manifest must show a 45-120 s execution at THIS head that
    reproduced deterministically and cleared the signal floor.
    """
    result = base_result(track_id, str(spec["adapter"]))
    runner = ROOT / "scripts/RUN_A1_07_FULL_FORM_V1.ps1"
    cli = ROOT / "scripts/earcrate_a1_07_full_form_v1.py"
    package = ROOT / "earcrate/a1_07_full_form"
    contract_path = ROOT / "configs/album_one/a1-07/full-form-v1.v1.json"
    low = float(spec["minimum_seconds"])
    high = float(spec["maximum_seconds"])

    # --- tool contract: does the adapter exist, and does its contract validate? --
    tool_errors: list[str] = []
    for path in (runner, cli, contract_path):
        if not path.is_file():
            tool_errors.append(f"missing {path.name}")
    if not (package / "build.py").is_file() or not (package / "score.py").is_file():
        tool_errors.append("full-form adapter package is incomplete")
    contract: dict[str, Any] = {}
    form: dict[str, Any] = {}
    if contract_path.is_file():
        contract = load(contract_path)
        try:
            require_seal(contract, "contract_sha256")
        except Exception as exc:
            tool_errors.append(f"contract seal invalid: {exc}")
        form = contract.get("form") or {}
        if str(contract.get("descent_id") or "") != "a1-07-full-form-v1":
            tool_errors.append("contract is not the full-form descent")
    result["tool_contract_ready"] = not tool_errors

    # --- binding contract: do the exact private objects exist and verify? -------
    required = [b for b in (spec.get("required_bindings") or [])
                if b != "a1_07_full_form_execution_manifest"]
    absent = missing(bindings, required)
    result["binding_contract_ready"] = not absent

    # --- full-form obligations declared by the contract ------------------------
    sections = {str(row.get("section_id")): row for row in form.get("sections") or []}
    declared = float(form.get("declared_total_seconds") or 0.0)
    phrase_map = contract.get("phrase_map") or {}
    form_ok = bool(
        {"setup", "body", "payoff"} <= set(sections)
        and low <= declared <= high
        and phrase_map.get("vocal_phrases")
        and (phrase_map.get("vocal_invariants") or {}).get("frankie_time_stretch_forbidden")
    )

    # --- representative invocation: what actually ran, at which head? ----------
    manifest_row = bindings.get("a1_07_full_form_execution_manifest") or {}
    manifest: dict[str, Any] = {}
    invocation_errors: list[str] = []
    if not manifest_row.get("available"):
        invocation_errors.append("no full-form execution manifest is bound")
    else:
        try:
            manifest = load(Path(str(manifest_row.get("artifact_path"))))
            require_seal(manifest, "manifest_sha256")
        except Exception as exc:
            invocation_errors.append(f"manifest is unreadable or unsealed: {exc}")
            manifest = {}

    head = current_git_head()
    clean = worktree_is_clean()
    executed_head = str(manifest.get("earcrate_git_head") or "")
    gate = manifest.get("machine_gate") or {}
    qualified_rows = [row for row in gate.get("per_candidate") or [] if row.get("qualified")]
    durations = [float(row.get("duration_seconds") or 0.0) for row in manifest.get("candidates") or []]
    in_window = bool(durations) and all(low <= value <= high for value in durations)
    reproduced = bool(manifest.get("candidates")) and all(
        bool(row.get("reproduced_identically")) for row in manifest.get("candidates") or [])
    audible = all(bool(row.get("above_signal_floor")) for row in gate.get("per_candidate") or []) \
        if gate.get("per_candidate") else False

    if manifest:
        if str(manifest.get("contract_sha256") or "") != str(contract.get("contract_sha256") or ""):
            invocation_errors.append("manifest was produced against a different contract")
        if head and executed_head != head:
            invocation_errors.append(
                f"manifest records head {executed_head[:12] or '<none>'}, repository is at {head[:12]}")
        elif clean is False:
            invocation_errors.append(
                "the checkout is dirty, so the recorded head does not identify the code that ran")
        if not in_window:
            invocation_errors.append(f"executed durations are outside {low}-{high} s")
        if not reproduced:
            invocation_errors.append("a candidate did not reproduce to one canonical PCM identity")
        if not audible:
            invocation_errors.append("a candidate did not clear the signal floor")
        if not gate.get("frontier_admissible"):
            invocation_errors.append("the rendered frontier is not admissible")

    result["representative_invocation_ready"] = bool(manifest and not invocation_errors)
    result["full_form_adapter_ready"] = bool(
        form_ok and result["tool_contract_ready"] and result["representative_invocation_ready"])
    result["performance_realization_ready"] = result["full_form_adapter_ready"]

    result["observations"] = {
        "adapter_id": manifest.get("adapter_id"),
        "adapter_version": manifest.get("adapter_version"),
        "descent_id": contract.get("descent_id"),
        "contract_sha256": contract.get("contract_sha256"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "declared_form_seconds": declared,
        "minimum_full_form_seconds": low,
        "maximum_full_form_seconds": high,
        "executed_durations_seconds": durations,
        "executed_at_git_head": executed_head or None,
        "repository_git_head": head,
        "exact_head_execution": bool(head and executed_head == head and clean),
        "worktree_clean": clean,
        "qualified_candidate_count": len(qualified_rows),
        "frontier_admissible": bool(gate.get("frontier_admissible")),
        "deterministic_reproduction": reproduced,
        "form_sections_declared": sorted(sections),
        "phrase_map_declared": bool(phrase_map.get("vocal_phrases")),
        "representative_full_form_invocation_receipt_bound": bool(manifest),
        # Machine qualification never speaks for the owner.
        "human_acceptance": bool((manifest.get("authority") or {}).get("human_acceptance", False)),
        "accepted_album_master": False,
    }

    if tool_errors:
        result["blockers"].append(blocker(
            "blocked_adapter_implementation", "adapter_contract", "; ".join(tool_errors)))
    if absent:
        result["blockers"].append(blocker(
            "blocked_exact_source", "binding_contract",
            "The qualified private v7 workspace and Beggin CORE custody are not both bound.",
            missing_binding_ids=absent))
    if invocation_errors:
        result["blockers"].append(blocker(
            "blocked_representative_invocation", "execution_evidence",
            "; ".join(invocation_errors)))
    if not form_ok:
        result["blockers"].append(blocker(
            "blocked_full_form_adapter", "output_contract",
            "The bound contract does not declare a complete setup/body/payoff form with an "
            "explicit phrase map inside the album full-form window.",
            declared_form_seconds=declared,
            minimum_duration_seconds=low, maximum_duration_seconds=high))
    return result
