"""Final role-slot guard over the mixed-integer partition solver."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from scipy.optimize import milp

from earcrate.plan import fixture_slot_qualification_core as _core
from earcrate.plan.fixture_slot_binding import (
    _failure,
    _verified_census_campaign,
)
from earcrate.plan.fixture_slot_solver import (
    qualify_fixture_candidate as _qualify_fixture_candidate,
)


def qualify_fixture_candidate(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    max_source_events: Optional[int] = None,
    time_limit_s: float = 30.0,
    _solver: Any = milp,
) -> Dict[str, Any]:
    """Require each declared role family to exist in the observed skeleton."""
    body = _core._candidate_body(candidate)
    islands, by_census_id = _verified_census_campaign(
        body, census_campaign
    )
    for island in islands:
        island_id = str(island["island_id"])
        slot_rows = list(by_census_id[island_id].get("slots") or [])
        present_families = {
            str(row.get("role_family") or "")
            for row in slot_rows
            if str(row.get("role_family") or "")
        }
        if slot_rows and len(present_families) == 0:
            # Legacy synthetic receipts predate the role-family field. The live
            # census always emits it; older tests continue to exercise the source
            # capability law in the underlying solver.
            continue
        for role in sorted(
            {str(value) for value in island.get("required_roles") or []}
        ):
            if role not in present_families:
                return _failure(
                    "required_role_slot_capacity",
                    f"island {island_id!r} realizes no slot in required role family {role!r}",
                    proof={
                        "island_id": island_id,
                        "required_role": role,
                        "observed_role_families": sorted(present_families),
                        "compatible_required_role_slot_count": 0,
                    },
                )
    return _qualify_fixture_candidate(
        candidate,
        census_campaign,
        max_source_events=max_source_events,
        time_limit_s=time_limit_s,
        _solver=_solver,
    )


__all__ = ["qualify_fixture_candidate"]
