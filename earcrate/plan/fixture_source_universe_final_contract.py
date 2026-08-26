"""Final Stage 2D API boundaries for pair precedence and exact-K types."""
from __future__ import annotations

import sys
from typing import Any, Dict, Mapping

from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan import fixture_source_universe as _source
from earcrate.plan import fixture_source_universe_review_closure as _review


def _pair_state_failure(
    candidate: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> Dict[str, Any] | None:
    """Classify every adverse pair receipt before proof-bearing status."""
    failure_class = str(parent.get("failure_class") or "")

    if "forbidden_final_pairs" not in parent:
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraint_receipt_inconsistent",
            reason=(
                "the parent refusal omits forbidden_final_pairs, so the "
                "source-only authority cannot prove that learned pair state "
                "is absent"
            ),
            declared_count=-1,
            observed_count=-1,
        )
    raw_pairs = parent.get("forbidden_final_pairs")
    if not isinstance(raw_pairs, list):
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraint_receipt_inconsistent",
            reason=(
                "the parent refusal's forbidden_final_pairs field is not a list"
            ),
            declared_count=-1,
            observed_count=-1,
        )
    pairs = list(raw_pairs)

    if "learned_pair_constraint_count" not in parent:
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraint_receipt_inconsistent",
            reason="the parent refusal omits its learned pair count",
            declared_count=-1,
            observed_count=len(pairs),
        )
    raw_declared = parent.get("learned_pair_constraint_count")
    if type(raw_declared) is not int:
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraint_receipt_inconsistent",
            reason=(
                "the parent refusal's learned pair count must be a "
                "nonnegative JSON integer"
            ),
            declared_count=-1,
            observed_count=len(pairs),
        )
    declared = raw_declared
    if declared < 0 or declared != len(pairs):
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraint_receipt_inconsistent",
            reason=(
                "the parent refusal's declared learned-pair count does not "
                "match its forbidden pair records"
            ),
            declared_count=declared,
            observed_count=len(pairs),
        )
    if declared > 0 or failure_class == "section_pair_compatibility":
        return _review._pair_receipt_failure(
            candidate,
            parent,
            failure_class="parent_pair_constraints_not_encoded",
            reason=(
                "the parent exact-pool refusal declares learned atom-pair "
                "constraints, but source-universe selection carries source "
                "identities only"
            ),
            declared_count=declared,
            observed_count=len(pairs),
        )
    return None


def install_fixture_source_universe_final_contract() -> None:
    """Install final pair precedence and exact-K input validation once."""
    if getattr(
        _source,
        "_fixture_source_universe_final_contract_installed",
        False,
    ):
        return

    original_select = _source.select_planable_source_universe

    def final_select(
        candidate: Mapping[str, Any],
        census_campaign: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        target = kwargs.get("target_source_count")
        if target is not None and type(target) is not int:
            raise _core.FixtureSlotQualificationError(
                "target_source_count must be a JSON integer"
            )

        parent = census_campaign.get("parent_exact_pool_refusal")
        if isinstance(parent, Mapping):
            failure = _pair_state_failure(candidate, parent)
            if failure is not None:
                return failure

        return original_select(candidate, census_campaign, **kwargs)

    final_select.__name__ = original_select.__name__
    final_select.__doc__ = original_select.__doc__
    final_select.__wrapped__ = original_select
    _source.select_planable_source_universe = final_select

    public = sys.modules.get("earcrate.plan.fixture_slot_qualification")
    if public is not None:
        public.select_planable_source_universe = final_select

    _source._fixture_source_universe_final_contract_installed = True


__all__ = ["install_fixture_source_universe_final_contract"]
