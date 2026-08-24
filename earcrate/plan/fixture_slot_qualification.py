"""Public slot-census and slot-qualified fixture partition authority."""
from __future__ import annotations

from typing import Any, Mapping

from earcrate.plan.slot_census import (
    FixtureSlotQualificationError,
    VERSION as SLOT_QUALIFICATION_VERSION,
    attach_slot_census_to_error,
    install_slot_census_evidence,
    probe_candidate_slot_census,
    role_family,
    slot_census_from_arrangement,
)
from earcrate.plan.slot_partition import (
    DEFAULT_MAX_ANCHOR_ROUNDS,
    DEFAULT_MAX_SOURCE_EVENTS,
    INDETERMINATE_ACTION,
    qualify_fixture_candidate as _qualify_fixture_candidate,
)


def qualify_fixture_candidate(
    matrix: Mapping[str, Any],
    candidate: Mapping[str, Any],
    slot_census_receipt: Mapping[str, Any],
    **kwargs: Any,
):
    """Ignore only CLI custody metadata; all semantic census bytes stay pinned."""
    semantic_receipt = dict(slot_census_receipt)
    ignored = []
    if "candidate_file" in semantic_receipt:
        semantic_receipt.pop("candidate_file")
        ignored.append("candidate_file")
    result = dict(
        _qualify_fixture_candidate(
            matrix,
            candidate,
            semantic_receipt,
            **kwargs,
        )
    )
    result["ignored_operational_census_fields"] = ignored
    return result


__all__ = [
    "DEFAULT_MAX_ANCHOR_ROUNDS",
    "DEFAULT_MAX_SOURCE_EVENTS",
    "FixtureSlotQualificationError",
    "INDETERMINATE_ACTION",
    "SLOT_QUALIFICATION_VERSION",
    "attach_slot_census_to_error",
    "install_slot_census_evidence",
    "probe_candidate_slot_census",
    "qualify_fixture_candidate",
    "role_family",
    "slot_census_from_arrangement",
]
