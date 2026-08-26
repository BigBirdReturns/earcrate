"""Final fail-closed boundaries for source-universe selection.

Stage 2D consumes a refusal-attached slot census and may change which sources
remain mandatory. The census and candidate must therefore agree on every
request field that can change ordinary island composition, and the source-only
selector must stop whenever a parent refusal declares learned atom-pair state.

The expanded composition-policy identity changes census semantics, so this
closure advances every public census-version surface to schema v3 at install
time. Historical Stage 2C v2 receipts remain immutable evidence but are not
admissible inputs to Stage 2D; fresh censuses must be generated from the same
sealed candidates and preserved parent refusals.

This module changes no composer, census graph, MILP law, renderer, publication
path, or accepted authority.
"""
from __future__ import annotations

import copy
import sys
from typing import Any, Dict, Mapping

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan import fixture_source_universe as _source

SOURCE_UNIVERSE_SLOT_CENSUS_VERSION = "earcrate_exact_pool_slot_census_v3"


def _composition_policy_identity(params: Mapping[str, Any]) -> str:
    """Bind every caller-controlled field used by ordinary census composition."""
    transform = dict(params.get("transform_policy") or {})
    body: Dict[str, Any] = {
        "profile": str(
            params.get("profile") or params.get("taste_profile") or ""
        ),
        "persona": str(params.get("persona") or ""),
        "phrase_playback_law": str(
            params.get("phrase_playback_law") or ""
        ),
        "transform_policy": transform,
        "stretch_budget": float(
            transform.get("stretch_budget")
            or params.get("stretch_budget")
            or 8.0
        ),
        "pitch_shift_budget": int(
            transform.get("pitch_shift_budget")
            or params.get("pitch_shift_budget")
            or 2
        ),
        "turnover_policy": copy.deepcopy(
            params.get("turnover_policy") or {}
        ),
        "transition": copy.deepcopy(params.get("transition") or {}),
        "recurrence_scores": copy.deepcopy(
            params.get("recurrence_scores") or {}
        ),
        "foreground_rank_recurrence": copy.deepcopy(
            params.get("foreground_rank_recurrence") or {}
        ),
        "reuse_policy_override": copy.deepcopy(
            params.get("reuse_policy_override") or {}
        ),
        "max_aux_decks": copy.deepcopy(params.get("max_aux_decks")),
        "exact_pool_max_source_events": int(
            params.get("exact_pool_max_source_events")
            or _core.DEFAULT_MAX_SOURCE_EVENTS
        ),
    }
    return _core.semantic_sha256(body)


def _candidate_source_count(candidate: Mapping[str, Any]) -> int:
    return len(
        {
            str(source_id)
            for island in candidate.get("islands") or []
            for source_id in island.get("source_include_ids") or []
        }
    )


def _pair_receipt_failure(
    candidate: Mapping[str, Any],
    parent: Mapping[str, Any],
    *,
    failure_class: str,
    reason: str,
    declared_count: int,
    observed_count: int,
) -> Dict[str, Any]:
    result = _source._failure(
        failure_class,
        reason,
        solver={
            "method": "not_run",
            "parent_failure_class": str(
                parent.get("failure_class") or ""
            ),
            "declared_learned_pair_constraint_count": int(declared_count),
            "observed_forbidden_final_pair_count": int(observed_count),
        },
        parent_fixture_identity=str(
            candidate.get("fixture_sha256")
            or candidate.get("fixture_id")
            or ""
        ),
        parent_source_count=_candidate_source_count(candidate),
    )
    result["private_acceptance"] = _source.PAIR_CONSTRAINT_HALT
    result["parent_exact_pool_refusal"] = copy.deepcopy(dict(parent))
    return result


def _install_schema_version() -> None:
    _core.SLOT_CENSUS_VERSION = SOURCE_UNIVERSE_SLOT_CENSUS_VERSION
    _binding.SLOT_CENSUS_VERSION = SOURCE_UNIVERSE_SLOT_CENSUS_VERSION
    for module_name in (
        "earcrate.plan.fixture_slot_qualification",
        "earcrate.plan.fixture_slot_contract",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            module.SLOT_CENSUS_VERSION = SOURCE_UNIVERSE_SLOT_CENSUS_VERSION


def install_fixture_source_universe_review_closure() -> None:
    """Install schema, policy binding and pair validation exactly once."""
    if getattr(
        _source,
        "_fixture_source_universe_review_closure_installed",
        False,
    ):
        return

    original_select = _source.select_planable_source_universe
    _install_schema_version()
    _core._policy_identity = _composition_policy_identity

    def guarded_select(
        candidate: Mapping[str, Any],
        census_campaign: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        parent = census_campaign.get("parent_exact_pool_refusal")
        if isinstance(parent, Mapping):
            raw_pairs = parent.get("forbidden_final_pairs")
            if raw_pairs is None:
                pairs = []
            elif isinstance(raw_pairs, list):
                pairs = list(raw_pairs)
            else:
                return _pair_receipt_failure(
                    candidate,
                    parent,
                    failure_class="parent_pair_constraint_receipt_inconsistent",
                    reason=(
                        "the parent refusal's forbidden_final_pairs field is "
                        "not a list, so the source-only authority cannot prove "
                        "that learned pair state is absent"
                    ),
                    declared_count=-1,
                    observed_count=-1,
                )

            raw_declared = parent.get("learned_pair_constraint_count")
            try:
                declared = int(raw_declared or 0)
            except (TypeError, ValueError):
                return _pair_receipt_failure(
                    candidate,
                    parent,
                    failure_class="parent_pair_constraint_receipt_inconsistent",
                    reason=(
                        "the parent refusal's learned pair count is malformed"
                    ),
                    declared_count=-1,
                    observed_count=len(pairs),
                )
            if declared < 0 or declared != len(pairs):
                return _pair_receipt_failure(
                    candidate,
                    parent,
                    failure_class="parent_pair_constraint_receipt_inconsistent",
                    reason=(
                        "the parent refusal's declared learned-pair count does "
                        "not match its forbidden pair records"
                    ),
                    declared_count=declared,
                    observed_count=len(pairs),
                )
            if declared > 0:
                return _pair_receipt_failure(
                    candidate,
                    parent,
                    failure_class="parent_pair_constraints_not_encoded",
                    reason=(
                        "the parent exact-pool refusal declares learned atom-pair "
                        "constraints, but source-universe selection carries "
                        "source identities only"
                    ),
                    declared_count=declared,
                    observed_count=len(pairs),
                )
        return original_select(candidate, census_campaign, **kwargs)

    guarded_select.__name__ = original_select.__name__
    guarded_select.__doc__ = original_select.__doc__
    guarded_select.__wrapped__ = original_select
    _source.select_planable_source_universe = guarded_select

    public = sys.modules.get("earcrate.plan.fixture_slot_qualification")
    if public is not None:
        public.select_planable_source_universe = guarded_select
        public.SLOT_CENSUS_VERSION = SOURCE_UNIVERSE_SLOT_CENSUS_VERSION

    _source._fixture_source_universe_review_closure_installed = True


__all__ = [
    "SOURCE_UNIVERSE_SLOT_CENSUS_VERSION",
    "install_fixture_source_universe_review_closure",
]
