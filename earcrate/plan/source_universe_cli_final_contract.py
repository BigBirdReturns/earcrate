"""Final recovery validation for the Stage 2D source-universe CLI."""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan import fixture_source_universe_review_closure as _review
from earcrate.plan.fixture_source_universe_determinism_contract import (
    CANONICAL_SLOT_ASSIGNMENT_VERSION,
    _CanonicalAssignmentBound,
    _CanonicalAssignmentInvariant,
    _canonical_slot_assignment,
)
from earcrate.plan.fixture_source_universe_proof_contract import (
    _has_explicit_zero_pair_state,
    _valid_normalized_proof,
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


def _immutable_candidate_projection(
    candidate: Mapping[str, Any],
) -> Dict[str, Any]:
    """Remove only the fields Stage 2D is authorized to change."""
    body = copy.deepcopy(dict(candidate))
    for field in (
        "fixture_id",
        "fixture_sha256",
        "fixture_source_universe_selection",
    ):
        body.pop(field, None)

    islands = []
    for raw in body.get("islands") or []:
        if not isinstance(raw, Mapping):
            raise _core.FixtureSlotQualificationError(
                "fixture candidate islands must be mapping-valued"
            )
        row = copy.deepcopy(dict(raw))
        row.pop("source_include_ids", None)
        islands.append(row)
    body["islands"] = islands
    return body


def _validate_current_census(
    module: Any,
    candidate: Mapping[str, Any],
    census: Mapping[str, Any],
) -> None:
    """Apply the complete Stage 2D census custody contract before recovery."""
    contradiction = module._receipt_contradiction
    try:
        if str(census.get("kind") or "") != (
            "earcrate_fixture_slot_census_campaign"
        ):
            raise _core.FixtureSlotQualificationError(
                "Stage 2D requires a slot-census campaign object"
            )

        _review._require_stage2d_schema(census)
        _review._require_source_universe_policy(candidate, census)
        parent_body = _core._candidate_body(candidate)
        islands, _by_census_id = _binding._verified_census_campaign(
            parent_body,
            census,
        )

        source_ids = []
        seen = set()
        for island in islands:
            for value in island.get("source_include_ids") or []:
                source_id = str(value)
                if not source_id:
                    raise _core.FixtureSlotQualificationError(
                        "fixture candidate contains an empty source identity"
                    )
                if source_id in seen:
                    raise _core.FixtureSlotQualificationError(
                        "fixture candidate assigns one source to multiple islands"
                    )
                seen.add(source_id)
                source_ids.append(source_id)
        source_ids.sort()
        if not source_ids:
            raise _core.FixtureSlotQualificationError(
                "fixture candidate has an empty source universe"
            )
        if str(census.get("source_universe_sha256") or "") != (
            _core.semantic_sha256(source_ids)
        ):
            raise _core.FixtureSlotQualificationError(
                "slot census source universe does not match the candidate"
            )
        if type(census.get("source_count")) is not int or int(
            census["source_count"]
        ) != len(source_ids):
            raise _core.FixtureSlotQualificationError(
                "slot census source count does not match the candidate"
            )
        census_rows = list(census.get("islands") or [])
        if type(census.get("island_count")) is not int or int(
            census["island_count"]
        ) != len(islands) or len(census_rows) != len(islands):
            raise _core.FixtureSlotQualificationError(
                "slot census island count does not match the candidate"
            )

        parent = census.get("parent_exact_pool_refusal")
        if not isinstance(parent, Mapping):
            raise _core.FixtureSlotQualificationError(
                "Stage 2D census lacks its parent exact-pool refusal"
            )
        if parent.get("impossibility_claimed") is not True or not str(
            parent.get("failure_class") or ""
        ):
            raise _core.FixtureSlotQualificationError(
                "Stage 2D parent refusal is not proof-bearing"
            )
        if not _has_explicit_zero_pair_state(parent):
            raise _core.FixtureSlotQualificationError(
                "Stage 2D parent refusal does not establish zero learned-pair state"
            )
        if not _valid_normalized_proof(parent.get("proof")):
            raise _core.FixtureSlotQualificationError(
                "Stage 2D parent refusal lacks a recognized typed proof"
            )
        parent_digest = parent.get("parent_refusal_sha256")
        if parent_digest is not None:
            parent_body_for_digest = copy.deepcopy(dict(parent))
            parent_body_for_digest.pop("parent_refusal_sha256", None)
            if str(parent_digest) != _core.semantic_sha256(
                parent_body_for_digest
            ):
                raise _core.FixtureSlotQualificationError(
                    "Stage 2D parent refusal digest does not match its content"
                )
    except Exception as error:
        raise contradiction(
            f"current census custody: {type(error).__name__}: {error}"
        ) from error


def _validate_canonicalization_receipt(
    module: Any,
    receipt: Mapping[str, Any],
    *,
    census: Mapping[str, Any],
    target_source_count: int | None,
    time_limit_s: float,
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
    _json_int(
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
    if target_source_count is not None and selected_count != target_source_count:
        raise contradiction("requested exact source count")

    max_source_events = _json_int(
        selection.get("max_source_events"),
        "selection event cap",
        contradiction,
    )
    if max_source_events <= 0:
        raise contradiction("selection event cap")

    try:
        expected_assignment, expected_receipt = _canonical_slot_assignment(
            selected,
            census,
            max_source_events=max_source_events,
            time_limit_s=float(time_limit_s),
        )
    except (_CanonicalAssignmentBound, _CanonicalAssignmentInvariant) as error:
        raise contradiction(
            f"canonical assignment recomputation: {type(error).__name__}: {error}"
        ) from error
    except Exception as error:
        raise contradiction(
            f"canonical assignment recomputation failed: {type(error).__name__}"
        ) from error

    if list(assignment) != expected_assignment:
        raise contradiction("canonical slot assignment does not match census")
    if canonical != expected_receipt:
        raise contradiction("canonicalization receipt does not match recomputation")


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
        _validate_current_census(module, candidate, census)
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

        selected = receipt.get("selected_candidate")
        if not isinstance(selected, Mapping):
            raise module._receipt_contradiction(
                "immutable selected candidate projection"
            )
        try:
            parent_projection = _immutable_candidate_projection(candidate)
            selected_projection = _immutable_candidate_projection(selected)
        except Exception as error:
            raise module._receipt_contradiction(
                f"immutable selected candidate projection: {type(error).__name__}"
            ) from error
        if selected_projection != parent_projection:
            raise module._receipt_contradiction(
                "selected candidate changed authority outside its source partition"
            )

        _validate_canonicalization_receipt(
            module,
            receipt,
            census=census,
            target_source_count=target_source_count,
            time_limit_s=time_limit_s,
        )
        return selected_bytes

    guarded_recovery.__name__ = original.__name__
    guarded_recovery.__doc__ = original.__doc__
    guarded_recovery.__wrapped__ = original
    module._recovery_candidate_bytes = guarded_recovery
    module._source_universe_cli_final_contract_installed = True


__all__ = ["install_source_universe_cli_final_contract"]
