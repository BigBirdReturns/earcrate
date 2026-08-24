"""earcrate.plan — pure composition arithmetic and deterministic plan contracts.

The ordinary math helpers remain the single source of composition arithmetic.
Multi-island helpers add an explicit exact-deck schedule without changing any
single-deck caller unless the new entrypoint is invoked.
"""
from earcrate.plan.math import (
    readiness_scale,
    sources_needed,
    readiness_need,
    bars_exact,
    target_bars,
)
from earcrate.plan.islands import (
    ISLAND_SET_KIND,
    ISLAND_SET_SCHEMA_VERSION,
    IslandPlanError,
    allocate_phrase_aligned_islands,
    plan_island_set,
    source_pool_identity,
)
from earcrate.plan.fixture_slot_qualification import (
    FixtureSlotQualificationError,
    probe_candidate_slot_census,
    qualify_fixture_candidate,
    slot_census_from_arrangement,
)

__all__ = [
    "readiness_scale",
    "sources_needed",
    "readiness_need",
    "bars_exact",
    "target_bars",
    "ISLAND_SET_KIND",
    "ISLAND_SET_SCHEMA_VERSION",
    "IslandPlanError",
    "allocate_phrase_aligned_islands",
    "plan_island_set",
    "source_pool_identity",
    "FixtureSlotQualificationError",
    "probe_candidate_slot_census",
    "qualify_fixture_candidate",
    "slot_census_from_arrangement",
]
