"""Final source-review closure for slot-qualified fixture partitioning.

The accepted fixture-slot slice deliberately solves source-to-island and
slot-to-source decisions. Three boundaries remain outside that source-only
model and are enforced here rather than hidden in optimizer status:

* a parent exact-pool refusal with learned atom-pair constraints cannot be
  repaired by a source-only MILP;
* the requested exact-pool event cap is carried ephemerally into the ordinary
  exact-island composer, so census and replan use one cap law;
* the refined parent refusal is rebound into the durable census campaign before
  a local driver may qualify it.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, MutableMapping, Optional

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan.fixture_slot_contract import (
    qualify_fixture_candidate as _qualify_without_parent_constraints,
)

PAIR_CONSTRAINT_HALT = (
    "halt_slot_qualification_parent_pair_constraints_are_not_encoded"
)
_CAP_CONTEXT = "_fixture_slot_exact_pool_max_source_events"


def _parent_refusal_projection(deficiency: Mapping[str, Any]) -> Dict[str, Any]:
    """Bind only the exact-pool evidence that governs a qualification round."""
    forbidden = [copy.deepcopy(dict(row)) for row in deficiency.get("forbidden_final_pairs") or []]
    body: Dict[str, Any] = {
        "failure_class": str(deficiency.get("failure_class") or ""),
        "impossibility_claimed": bool(deficiency.get("impossibility_claimed")),
        "forbidden_final_pairs": forbidden,
        "learned_pair_constraint_count": int(
            deficiency.get("learned_pair_constraint_count") or len(forbidden)
        ),
    }
    for key in (
        "reason",
        "proof",
        "hall_witness",
        "unfilled_slot",
        "unfilled_slot_role",
        "saturated_reachable_sources",
        "search",
    ):
        if key in deficiency:
            body[key] = copy.deepcopy(deficiency[key])
    body["parent_refusal_sha256"] = _binding.semantic_sha256(body)
    return body


def _bind_parent_refusal(
    campaign: Mapping[str, Any], deficiency: Mapping[str, Any]
) -> Dict[str, Any]:
    body = copy.deepcopy(dict(campaign))
    body["parent_exact_pool_refusal"] = _parent_refusal_projection(deficiency)
    body["campaign_sha256"] = _binding._campaign_identity(body)
    return body


def qualify_fixture_candidate(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Stop when the parent refusal depends on atom-pair co-occurrences.

    The census exposes source reachability per slot. It does not expose the
    chosen atom value at each slot, so a source-only solve cannot honour a
    learned ``(slot, atom)`` co-occurrence. Returning a new candidate in that
    situation would claim a repair that the model never represented.
    """
    parent = census_campaign.get("parent_exact_pool_refusal")
    if isinstance(parent, Mapping):
        constraints = list(parent.get("forbidden_final_pairs") or [])
        parent_class = str(parent.get("failure_class") or "")
        if constraints or parent_class == "section_pair_compatibility":
            result = _binding._failure(
                "parent_pair_constraints_not_encoded",
                "the parent exact-pool refusal contains atom-pair co-occurrence constraints, but this qualification model carries source identities only",
                solver={
                    "method": "not_run",
                    "parent_failure_class": parent_class,
                    "learned_pair_constraint_count": len(constraints),
                },
            )
            result.update(
                {
                    "private_acceptance": PAIR_CONSTRAINT_HALT,
                    "parent_exact_pool_refusal": copy.deepcopy(dict(parent)),
                }
            )
            return result
    return _qualify_without_parent_constraints(
        candidate,
        census_campaign,
        **kwargs,
    )


def install_fixture_slot_review_closure(core_class: Any) -> Any:
    """Install the census wrapper plus exact request-cap propagation."""
    if getattr(core_class, "_fixture_slot_review_closure_installed", False):
        return core_class

    _binding.install_fixture_slot_census(core_class)
    original_compose = core_class.compose_taste_arrangement
    original_propose = core_class.propose_island_set

    def compose_with_request_cap(
        self: Any,
        pool: list[dict[str, Any]],
        params: Dict[str, Any],
        seed: int,
    ) -> Dict[str, Any]:
        effective = dict(params or {})
        declared = getattr(self, _CAP_CONTEXT, None)
        if declared is not None and effective.get("exact_pool_max_source_events") is None:
            effective["exact_pool_max_source_events"] = int(declared)
        return original_compose(self, pool, effective, seed)

    compose_with_request_cap.__name__ = getattr(
        original_compose, "__name__", "compose_taste_arrangement"
    )
    compose_with_request_cap.__doc__ = getattr(original_compose, "__doc__", None)
    compose_with_request_cap.__wrapped__ = original_compose
    core_class.compose_taste_arrangement = compose_with_request_cap

    def propose_with_refusal_binding(
        self: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        request = dict(params or {})
        previous_cap = getattr(self, _CAP_CONTEXT, None)
        had_previous = hasattr(self, _CAP_CONTEXT)
        declared = request.get("exact_pool_max_source_events")
        if declared not in (None, ""):
            cap = int(declared)
            if cap <= 0:
                raise _binding.FixtureSlotQualificationError(
                    "exact_pool_max_source_events must be positive"
                )
            setattr(self, _CAP_CONTEXT, cap)
        try:
            return original_propose(self, request)
        except Exception as exc:
            deficiency = getattr(exc, "deficiency", None)
            if not isinstance(deficiency, MutableMapping):
                raise
            campaign = deficiency.get("fixture_slot_census_campaign")
            if isinstance(campaign, Mapping):
                bound = _bind_parent_refusal(campaign, deficiency)
                deficiency["fixture_slot_census_campaign"] = bound
                prior_run = str(deficiency.get("fixture_slot_census_run_id") or "")
                try:
                    _binding._publish_census_run(
                        self,
                        request,
                        deficiency,
                        bound,
                        supersedes_run_id=prior_run or None,
                    )
                except Exception as receipt_error:
                    deficiency["fixture_slot_census_receipt_failure"] = {
                        "exception_type": type(receipt_error).__name__,
                        "error": str(receipt_error),
                        "impossibility_claimed": False,
                        "private_acceptance": _binding.INDETERMINATE_ACTION,
                    }
            raise
        finally:
            if had_previous:
                setattr(self, _CAP_CONTEXT, previous_cap)
            elif hasattr(self, _CAP_CONTEXT):
                delattr(self, _CAP_CONTEXT)

    propose_with_refusal_binding.__name__ = getattr(
        original_propose, "__name__", "propose_island_set"
    )
    propose_with_refusal_binding.__doc__ = getattr(original_propose, "__doc__", None)
    propose_with_refusal_binding.__wrapped__ = original_propose
    core_class.propose_island_set = propose_with_refusal_binding
    core_class._fixture_slot_review_closure_installed = True
    return core_class


# Keep the public installer name stable for ``earcrate.__init__``.
install_fixture_slot_census = install_fixture_slot_review_closure


__all__ = [
    "PAIR_CONSTRAINT_HALT",
    "install_fixture_slot_census",
    "install_fixture_slot_review_closure",
    "qualify_fixture_candidate",
]
