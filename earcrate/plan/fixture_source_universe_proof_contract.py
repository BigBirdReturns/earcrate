"""Require explicit proof evidence before Stage 2D source selection.

The Stage 2D source-universe authority consumes a refusal-attached census. A
boolean impossibility label and a failure-class name do not constitute the
mathematical evidence needed to authorize source-universe reduction. This
additive guard requires a nonempty mapping-valued proof before either MILP
phase can run, while preserving the existing parent-receipt and learned-pair
classifications installed by the main review closure.
"""
from __future__ import annotations

import copy
import sys
from typing import Any, Dict, Mapping

from earcrate.plan import fixture_source_universe as _source


def _proof_failure(
    candidate: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> Dict[str, Any]:
    parent_identity = str(
        candidate.get("fixture_sha256")
        or candidate.get("fixture_id")
        or ""
    )
    parent_source_count = len(
        {
            str(source_id)
            for island in candidate.get("islands") or []
            for source_id in island.get("source_include_ids") or []
        }
    )
    result = _source._failure(
        "parent_exact_pool_refusal_proof_missing_or_malformed",
        (
            "the bound parent exact-pool refusal must carry a nonempty "
            "mapping-valued proof before source-universe selection may run"
        ),
        solver={
            "method": "not_run",
            "parent_failure_class": str(
                parent.get("failure_class") or ""
            ),
            "proof_payload_type": type(parent.get("proof")).__name__,
        },
        parent_fixture_identity=parent_identity,
        parent_source_count=parent_source_count,
    )
    result["private_acceptance"] = _source.PAIR_CONSTRAINT_HALT
    result["parent_exact_pool_refusal"] = copy.deepcopy(dict(parent))
    return result


def install_fixture_source_universe_proof_contract() -> None:
    """Install the fail-closed parent-proof boundary once."""
    if getattr(
        _source,
        "_fixture_source_universe_proof_contract_installed",
        False,
    ):
        return

    original_select = _source.select_planable_source_universe

    def guarded_select(
        candidate: Mapping[str, Any],
        census_campaign: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        parent = census_campaign.get("parent_exact_pool_refusal")
        if (
            isinstance(parent, Mapping)
            and parent.get("impossibility_claimed") is True
            and str(parent.get("failure_class") or "")
        ):
            proof = parent.get("proof")
            if not isinstance(proof, Mapping) or not proof:
                return _proof_failure(candidate, parent)
        return original_select(candidate, census_campaign, **kwargs)

    guarded_select.__name__ = original_select.__name__
    guarded_select.__doc__ = original_select.__doc__
    guarded_select.__wrapped__ = original_select
    _source.select_planable_source_universe = guarded_select

    public = sys.modules.get("earcrate.plan.fixture_slot_qualification")
    if public is not None:
        public.select_planable_source_universe = guarded_select

    _source._fixture_source_universe_proof_contract_installed = True


__all__ = ["install_fixture_source_universe_proof_contract"]
