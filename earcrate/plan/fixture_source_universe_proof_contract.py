"""Normalize producer evidence and enforce the Stage 2D proof boundary.

Stage 2D may reduce a fixture's mandatory source universe only after a genuine
proof-bearing exact-pool refusal. Product refusals historically expose that
proof in several shapes: Hall evidence under ``hall_witness``, mapping-valued
``proof`` payloads, and scalar proof statements for counting, cap, and
construction exhaustion. This module normalizes those producer shapes at the
refusal-projection boundary, then requires one typed, nonempty mapping before
either source-universe MILP may run.

Learned atom-pair state is classified by the existing review closure first.
The proof guard applies only after the parent receipt explicitly establishes a
consistent zero learned-pair state.
"""
from __future__ import annotations

import copy
import sys
from typing import Any, Dict, Mapping, Optional

from earcrate.plan import fixture_source_universe as _source
from earcrate.plan import fixture_slot_review_closure as _review


def _normalized_producer_proof(
    deficiency: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return a deterministic typed proof object without inventing evidence."""
    raw_proof = deficiency.get("proof")
    if isinstance(raw_proof, Mapping) and raw_proof:
        return {
            "kind": "mapping_proof",
            "payload": copy.deepcopy(dict(raw_proof)),
        }

    hall_witness = deficiency.get("hall_witness")
    if isinstance(hall_witness, Mapping) and hall_witness:
        return {
            "kind": "hall_witness",
            "witness": copy.deepcopy(dict(hall_witness)),
        }
    if hall_witness not in (None, "", [], {}):
        return {
            "kind": "hall_witness",
            "witness": copy.deepcopy(hall_witness),
        }

    if isinstance(raw_proof, str) and raw_proof.strip():
        return {
            "kind": "producer_proof_statement",
            "statement": raw_proof,
        }
    if raw_proof not in (None, "", [], {}):
        return {
            "kind": "producer_proof_payload",
            "payload": copy.deepcopy(raw_proof),
        }
    return None


def _install_parent_proof_normalizer() -> None:
    if getattr(
        _review,
        "_fixture_source_universe_parent_proof_normalizer_installed",
        False,
    ):
        return

    original_projection = _review._parent_refusal_projection

    def projection_with_normalized_proof(
        deficiency: Mapping[str, Any],
    ) -> Dict[str, Any]:
        body = copy.deepcopy(dict(original_projection(deficiency)))
        normalized = _normalized_producer_proof(deficiency)
        if normalized is None:
            body.pop("proof", None)
        else:
            body["proof"] = normalized
        body.pop("parent_refusal_sha256", None)
        body["parent_refusal_sha256"] = _review._binding.semantic_sha256(body)
        return body

    projection_with_normalized_proof.__name__ = (
        original_projection.__name__
    )
    projection_with_normalized_proof.__doc__ = original_projection.__doc__
    projection_with_normalized_proof.__wrapped__ = original_projection
    _review._parent_refusal_projection = projection_with_normalized_proof
    _review._fixture_source_universe_parent_proof_normalizer_installed = True


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


def _has_explicit_zero_pair_state(parent: Mapping[str, Any]) -> bool:
    """Return true only after the pair receipt is complete and constraint-free."""
    if str(parent.get("failure_class") or "") == (
        "section_pair_compatibility"
    ):
        return False
    if "forbidden_final_pairs" not in parent:
        return False
    pairs = parent.get("forbidden_final_pairs")
    if not isinstance(pairs, list):
        return False
    if "learned_pair_constraint_count" not in parent:
        return False
    declared = parent.get("learned_pair_constraint_count")
    if type(declared) is not int:
        return False
    return declared == 0 and len(pairs) == 0


def install_fixture_source_universe_proof_contract() -> None:
    """Install producer normalization and the fail-closed proof boundary once."""
    _install_parent_proof_normalizer()
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
            and _has_explicit_zero_pair_state(parent)
        ):
            proof = parent.get("proof")
            if not isinstance(proof, Mapping) or not proof:
                return _proof_failure(candidate, parent)
        # Missing, malformed, inconsistent, positive, or section-pair state is
        # deliberately delegated to the existing pair-receipt authority.
        return original_select(candidate, census_campaign, **kwargs)

    guarded_select.__name__ = original_select.__name__
    guarded_select.__doc__ = original_select.__doc__
    guarded_select.__wrapped__ = original_select
    _source.select_planable_source_universe = guarded_select

    public = sys.modules.get("earcrate.plan.fixture_slot_qualification")
    if public is not None:
        public.select_planable_source_universe = guarded_select

    _source._fixture_source_universe_proof_contract_installed = True


__all__ = [
    "install_fixture_source_universe_proof_contract",
]
