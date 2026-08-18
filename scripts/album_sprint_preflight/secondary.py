from __future__ import annotations

from pathlib import Path
import sys
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


def album_acceptance(track_id: str) -> dict[str, Any]:
    """Where an acceptance claim legitimately comes from.

    The frontier manifest is the wrong source and is correctly immutable: it records
    machine qualification and says so. Reading acceptance off it could only ever
    report False, which was true while nothing was accepted and would have gone on
    being reported after something was. So acceptance is read from the album ledger
    and the landed receipt it names, in this precedence:

        machine readiness      adapter manifest + invocation evidence
        frontier selection     sealed frontier receipt
        master qualification   mastering receipt, deterministic pair, signal gates
        master acceptance      acceptance receipt naming the exact mastered PCM
        system reference       separate withheld-answer recovery challenge

    Each level is reported on its own evidence. A lower level never implies a higher
    one, and a missing receipt reports absence rather than failure.
    """
    state = {
        "master_state": None,
        "master_qualified": False,
        "owner_master_acceptance": False,
        "accepted_album_master": False,
        "human_acceptance": False,
        "system_reference_complete": False,
        "acceptance_receipt_sha256": None,
        "acceptance_evidence": "no album ledger entry",
    }
    ledger_path = ROOT / "configs/album_one/manifest.v1.json"
    if not ledger_path.is_file():
        return state
    try:
        ledger = load(ledger_path)
        require_seal(ledger, "manifest_sha256")
    except Exception as exc:
        state["acceptance_evidence"] = f"album ledger unreadable or unsealed: {exc}"
        return state

    row = next((entry for entry in ledger.get("tracks") or []
                if entry.get("track_id") == track_id), None)
    if row is None:
        return state

    status = row.get("status") or {}
    qualification = row.get("master_qualification") or {}
    state["master_state"] = qualification.get("master_state")
    state["master_qualified"] = bool(qualification)
    state["system_reference_complete"] = status.get("system_reference") == "complete"

    if status.get("album_master") != "accepted":
        state["acceptance_evidence"] = (
            f"ledger reports album_master={status.get('album_master')!r}")
        return state

    # The ledger says accepted; the receipt has to say it too, and name the same
    # object. A ledger that claims more than its evidence is the failure mode here.
    master = row.get("accepted_master") or {}
    for relative in row.get("repo_evidence") or []:
        if not str(relative).endswith(".public.json"):
            continue
        try:
            receipt = load(ROOT / relative)
        except Exception:
            continue
        if not str(receipt.get("kind", "")).endswith("master_acceptance_receipt"):
            continue
        if receipt.get("receipt_sha256") != master.get("acceptance_receipt_sha256"):
            continue
        if receipt.get("verdict") != "ACCEPT_MASTER":
            continue
        audited = (receipt.get("audited_object") or {}).get("canonical_pcm_sha256")
        if audited != master.get("canonical_pcm_sha256"):
            continue
        state["owner_master_acceptance"] = True
        state["accepted_album_master"] = True
        state["human_acceptance"] = True
        state["acceptance_receipt_sha256"] = receipt.get("receipt_sha256")
        state["acceptance_evidence"] = f"acceptance receipt {relative}"
        return state

    state["acceptance_evidence"] = (
        "the ledger claims acceptance but no landed receipt binds that verdict to this "
        "mastered PCM")
    return state


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

    acceptance = album_acceptance(track_id)
    head = current_git_head()
    clean = worktree_is_clean()
    executed_head = str(manifest.get("earcrate_git_head") or "")

    # Bind the receipt to the CODE that produced it, not to the commit counter. A
    # later commit that cannot touch the audio -- a changelog line, a packaging
    # fix, the sealed verdict itself -- must not invalidate a render, and an equal
    # head SHA must not excuse a dirty checkout. See a1_07_full_form/provenance.py.
    declared_tree = str((manifest.get("adapter_tree") or {}).get("digest") or "")
    observed_tree = ""
    try:
        sys.path.insert(0, str(ROOT))
        from earcrate.a1_07_full_form.provenance import adapter_tree_digest
        observed_tree = str(adapter_tree_digest(ROOT)["digest"])
    except Exception:
        observed_tree = ""
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
        if not declared_tree:
            invocation_errors.append("manifest records no adapter tree digest")
        elif not observed_tree:
            invocation_errors.append("cannot recompute the adapter tree digest")
        elif declared_tree != observed_tree:
            invocation_errors.append(
                f"adapter code changed since the render: manifest {declared_tree[:12]}, "
                f"working tree {observed_tree[:12]}")
        if clean is False:
            invocation_errors.append(
                "the checkout is dirty, so the recorded provenance does not identify the code that ran")
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
        "adapter_code_unchanged_since_render": bool(
            declared_tree and observed_tree and declared_tree == observed_tree),
        "adapter_tree_digest_declared": declared_tree or None,
        "adapter_tree_digest_observed": observed_tree or None,
        "exact_head_execution": bool(
            declared_tree and declared_tree == observed_tree and clean),
        "head_advanced_since_render": bool(head and executed_head and executed_head != head),
        "worktree_clean": clean,
        "qualified_candidate_count": len(qualified_rows),
        "frontier_admissible": bool(gate.get("frontier_admissible")),
        "deterministic_reproduction": reproduced,
        "form_sections_declared": sorted(sections),
        "phrase_map_declared": bool(phrase_map.get("vocal_phrases")),
        "representative_full_form_invocation_receipt_bound": bool(manifest),
        # Machine qualification never speaks for the owner, so acceptance is read
        # from the album ledger and its acceptance receipt -- never from this
        # manifest, which correctly only ever reports machine qualification.
        "manifest_declares_human_acceptance": bool(
            (manifest.get("authority") or {}).get("human_acceptance", False)),
        "master_state": acceptance["master_state"],
        "master_qualified": acceptance["master_qualified"],
        "owner_master_acceptance": acceptance["owner_master_acceptance"],
        "human_acceptance": acceptance["human_acceptance"],
        "accepted_album_master": acceptance["accepted_album_master"],
        "system_reference_complete": acceptance["system_reference_complete"],
        "acceptance_receipt_sha256": acceptance["acceptance_receipt_sha256"],
        "acceptance_evidence": acceptance["acceptance_evidence"],
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
