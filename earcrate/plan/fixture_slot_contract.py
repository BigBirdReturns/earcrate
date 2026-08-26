"""Lineage and no-op contract over the single slot-partition solver."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from scipy.optimize import milp

from earcrate.plan.fixture_slot_binding import _failure
from earcrate.plan.fixture_slot_solver import (
    qualify_fixture_candidate as _solve_fixture_candidate,
)


def _prior_lineage(candidate: Mapping[str, Any]) -> list[str]:
    previous = candidate.get("fixture_slot_qualification")
    if not isinstance(previous, Mapping):
        return []
    values = [
        str(value)
        for value in previous.get("lineage_fixture_identities") or []
        if str(value)
    ]
    parent = str(previous.get("parent_fixture_identity") or "")
    if parent:
        values.append(parent)
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def qualify_fixture_candidate(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    max_source_events: Optional[int] = None,
    time_limit_s: float = 30.0,
    _solver: Any = milp,
) -> Dict[str, Any]:
    """Reject semantic stasis or ancestry cycles after one solved round."""
    result = _solve_fixture_candidate(
        candidate,
        census_campaign,
        max_source_events=max_source_events,
        time_limit_s=time_limit_s,
        _solver=_solver,
    )
    if not bool(result.get("complete")):
        return result

    parent = str(
        candidate.get("fixture_sha256")
        or candidate.get("fixture_id")
        or ""
    )
    qualified = str(result.get("qualified_fixture_identity") or "")
    prior = _prior_lineage(candidate)
    if qualified == parent:
        failure = _failure(
            "qualification_no_structural_change",
            "the solved slot assignment leaves the fixture partition unchanged; replanning the same deterministic candidate cannot advance the campaign",
            solver=dict(result.get("solver") or {}),
        )
        failure.update(
            {
                "parent_fixture_identity": parent,
                "qualified_fixture_identity": qualified,
                "lineage_fixture_identities": [*prior, parent],
            }
        )
        return failure
    if qualified in prior:
        failure = _failure(
            "qualification_identity_cycle",
            "the solved partition revisits a prior semantic fixture identity",
            solver=dict(result.get("solver") or {}),
        )
        failure.update(
            {
                "parent_fixture_identity": parent,
                "qualified_fixture_identity": qualified,
                "lineage_fixture_identities": [*prior, parent],
            }
        )
        return failure

    lineage = list(prior)
    if parent and parent not in lineage:
        lineage.append(parent)
    output = dict(result["candidate"])
    qualification = dict(output.get("fixture_slot_qualification") or {})
    qualification.update(
        {
            "lineage_fixture_identities": lineage,
            "qualification_round": len(lineage),
        }
    )
    output["fixture_slot_qualification"] = qualification
    result["candidate"] = output
    result["lineage_fixture_identities"] = lineage
    result["qualification_round"] = len(lineage)
    return result


__all__ = ["qualify_fixture_candidate"]
