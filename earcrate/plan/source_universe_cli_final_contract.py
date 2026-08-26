"""Final recovery validation for the Stage 2D source-universe CLI."""
from __future__ import annotations

from typing import Any, Mapping

from earcrate.plan.fixture_source_universe_determinism_contract import (
    CANONICAL_SLOT_ASSIGNMENT_VERSION,
)

_EXPECTED_METHOD = (
    "lexicographically_smallest_source_vector_by_sorted_slot_"
    "with_exact_lower_bound_flow_feasibility"
)
_EXPECTED_SLOT_ORDER = "island_id_then_bar_start_then_layer_index"
_EXPECTED_SOURCE_ORDER = "stable_source_identity_lexical"
_EXPECTED_NUMERIC_SEMANTICS = "exact_integer_flow_no_floating_tie_break"


def _json_int(value: Any, field: str, contradiction: Any) -> int:
    if type(value) is not int:
        raise contradiction(f"canonicalization {field}")
    return value


def _validate_canonicalization_receipt(
    module: Any,
    receipt: Mapping[str, Any],
) -> None:
    contradiction = module._receipt_contradiction
    selected = receipt.get("selected_candidate")
    if not isinstance(selected, Mapping):
        raise contradiction("canonicalization selected candidate")
    selection = selected.get("fixture_source_universe_selection")
    if not isinstance(selection, Mapping):
        raise contradiction("canonicalization selection ledger")

    top_solver = receipt.get("solver")
    selection_solver = selection.get("solver")
    standalone = selection.get("slot_assignment_canonicalization")
    if not isinstance(top_solver, Mapping):
        raise contradiction("canonicalization top-level solver")
    if not isinstance(selection_solver, Mapping):
        raise contradiction("canonicalization selection solver")

    top_copy = top_solver.get("slot_assignment_canonicalization")
    selection_copy = selection_solver.get("slot_assignment_canonicalization")
    copies = (standalone, selection_copy, top_copy)
    if any(not isinstance(value, Mapping) for value in copies):
        raise contradiction("canonicalization receipt presence")

    canonical = dict(standalone)
    if any(dict(value) != canonical for value in copies[1:]):
        raise contradiction("canonicalization receipt equality")

    if canonical.get("version") != CANONICAL_SLOT_ASSIGNMENT_VERSION:
        raise contradiction("canonicalization version")
    if canonical.get("method") != _EXPECTED_METHOD:
        raise contradiction("canonicalization method")
    if canonical.get("slot_order") != _EXPECTED_SLOT_ORDER:
        raise contradiction("canonicalization slot order")
    if canonical.get("source_order") != _EXPECTED_SOURCE_ORDER:
        raise contradiction("canonicalization source order")
    if canonical.get("numeric_semantics") != _EXPECTED_NUMERIC_SEMANTICS:
        raise contradiction("canonicalization numeric semantics")

    assignment = receipt.get("slot_assignment")
    if not isinstance(assignment, list):
        raise contradiction("canonicalization slot assignment")
    islands = selected.get("islands")
    if not isinstance(islands, list):
        raise contradiction("canonicalization island family")

    slot_count = _json_int(
        canonical.get("slot_count"), "slot count", contradiction
    )
    selected_count = _json_int(
        canonical.get("selected_source_count"),
        "selected source count",
        contradiction,
    )
    island_count = _json_int(
        canonical.get("island_count"), "island count", contradiction
    )
    feasibility_checks = _json_int(
        canonical.get("feasibility_check_count"),
        "feasibility check count",
        contradiction,
    )
    receipt_selected_count = _json_int(
        receipt.get("selected_source_count"),
        "top-level selected source count",
        contradiction,
    )

    if slot_count != len(assignment):
        raise contradiction("canonicalization slot count")
    if selected_count != receipt_selected_count:
        raise contradiction("canonicalization selected source count")
    if island_count != len(islands):
        raise contradiction("canonicalization island count")
    if feasibility_checks < island_count + slot_count:
        raise contradiction("canonicalization feasibility check count")


def install_source_universe_cli_final_contract(module: Any) -> None:
    """Patch one loaded CLI core with complete canonical receipt recovery."""
    if getattr(module, "_source_universe_cli_final_contract_installed", False):
        return

    original = module._recovery_candidate_bytes

    def guarded_recovery(
        receipt: Mapping[str, Any],
        *,
        candidate: Mapping[str, Any],
        census: Mapping[str, Any],
        candidate_file: Mapping[str, Any],
        census_file: Mapping[str, Any],
        target_source_count: int | None,
        time_limit_s: float,
        output_path: Any,
    ) -> bytes | None:
        selected_bytes = original(
            receipt,
            candidate=candidate,
            census=census,
            candidate_file=candidate_file,
            census_file=census_file,
            target_source_count=target_source_count,
            time_limit_s=time_limit_s,
            output_path=output_path,
        )
        if selected_bytes is None:
            return None
        _validate_canonicalization_receipt(module, receipt)
        return selected_bytes

    guarded_recovery.__name__ = original.__name__
    guarded_recovery.__doc__ = original.__doc__
    guarded_recovery.__wrapped__ = original
    module._recovery_candidate_bytes = guarded_recovery
    module._source_universe_cli_final_contract_installed = True


__all__ = ["install_source_universe_cli_final_contract"]
