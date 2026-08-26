"""Nonpublishing slot-census probe below the product turnover admission gate.

The ordinary TasteSpec composer correctly refuses a deck whose exact-deck pool
cannot meet its source-turnover target. A refusal-attached slot census has a
different job: observe the section/layer skeleton that the same composer law
would build so a fixture partition can be qualified against its role slots.

This module changes no product path. It replaces only the private raw-skeleton
helper used by fixture-slot census construction. The probe uses the real
transform-safe exact-deck pool and bypasses only the pre-skeleton source-count
admission check by reporting the required count to the ordinary composer. The
actual and required counts are retained in the census as diagnostic evidence.
Every other composer failure remains fatal to census construction.
"""
from __future__ import annotations

import contextlib
import copy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan import fixture_slot_qualification_core as _core

PROBE_VERSION = "earcrate_slot_census_turnover_probe_v1"

_ORIGINAL_RAW_ISLAND_ARRANGEMENT = _core._raw_island_arrangement
_ORIGINAL_BUILD_EXACT_POOL_SLOT_CENSUS = _binding.build_exact_pool_slot_census


def _required_source_count(params: Mapping[str, Any]) -> int:
    """Reproduce the ordinary composer's source-turnover arithmetic."""
    from earcrate.app import TASTE_PROFILES
    from earcrate.plan.math import DEFAULT_SOURCE_SECONDS, sources_needed

    profile_name = str(params.get("taste_profile") or "girl_talk_v1")
    profile = dict(
        TASTE_PROFILES.get(profile_name)
        or TASTE_PROFILES["girl_talk_v1"]
    )
    override = dict(params.get("reuse_policy_override") or {})
    if override:
        profile.update(override)
    target_seconds = float(params.get("target_seconds") or 120.0)
    source_seconds = float(
        profile.get("source_seconds") or DEFAULT_SOURCE_SECONDS
    )
    return int(sources_needed(target_seconds, source_seconds))


def _actual_source_count(
    feasible: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
) -> int:
    have = dict(diagnostics.get("have") or {})
    declared = int(have.get("sources") or 0)
    if declared > 0:
        return declared

    from earcrate.plan.exact_pool_assignment import (
        _stable_source_identity,
        require_stable_identity,
    )

    items = [dict(item) for item in feasible]
    require_stable_identity(items)
    identities = set()
    for item in items:
        source_id = _stable_source_identity(item)
        if source_id is not None:
            identities.add(str(source_id))
    return len(identities)


class _DiagnosticExactDeckProxy:
    """Exact-deck proxy that records and bypasses one admission predicate."""

    def __init__(self, core: Any, target_bpm: float, target_key: int):
        self._core = core
        self.target_bpm = float(target_bpm)
        self.target_key = int(target_key) % 12
        self.turnover_observation: Optional[Dict[str, Any]] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)

    def choose_taste_deck(
        self,
        pool: list[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        feasible, raw_diagnostics = self._core.taste_feasible_pool(
            pool,
            self.target_bpm,
            self.target_key,
            params,
        )
        diagnostics = copy.deepcopy(dict(raw_diagnostics or {}))
        actual = _actual_source_count(feasible, diagnostics)
        required = _required_source_count(params)
        bypassed = actual < required
        have = dict(diagnostics.get("have") or {})
        if bypassed:
            # The ordinary composer reads only this count before it constructs
            # sections. The real pool is left untouched, so no source, role,
            # transform, slot, or pairing is invented.
            have["sources"] = int(required)
        diagnostics["have"] = have
        observation = {
            "version": PROBE_VERSION,
            "disposition": "diagnostic_only_no_publication",
            "bypassed_precondition": (
                "taste_deck_distinct_playable_sources"
                if bypassed
                else None
            ),
            "actual_distinct_playable_sources": int(actual),
            "required_distinct_playable_sources": int(required),
            "exact_target_bpm": self.target_bpm,
            "exact_target_key": self.target_key,
            "pool_item_count": len(feasible),
            "real_feasible_pool_unchanged": True,
            "ordinary_product_path_unchanged": True,
        }
        diagnostics["slot_census_probe"] = copy.deepcopy(observation)
        self.turnover_observation = observation
        if not feasible:
            raise _core.FixtureSlotQualificationError(
                f"exact deck {self.target_bpm:.9f} BPM/key "
                f"{self.target_key} retains no material for slot census"
            )
        return {
            "pool": feasible,
            "render_bpm": self.target_bpm,
            "target_key": self.target_key,
            "diagnostics": diagnostics,
            "lattice": {
                "best": diagnostics,
                "lattice": [diagnostics],
            },
        }


def _diagnostic_raw_island_arrangement(
    core: Any,
    pool: Sequence[Mapping[str, Any]],
    row: Mapping[str, Any],
    base_params: Mapping[str, Any],
    seed: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build a nonpublishing skeleton through the ordinary composer law."""
    bpm = float(row["target_bpm"])
    key = int(row["target_key"]) % 12
    params = dict(base_params)
    params.update(
        {
            "target_seconds": float(row["allocated_duration_s"]),
            "bpm": bpm,
            "exact_target_bpm": bpm,
            "exact_target_key": key,
            "stem_export": True,
            "island_id": str(row["island_id"]),
        }
    )
    if str(params.get("phrase_playback_law") or "") == "proof001_phrase_law":
        params["phrase_playback"] = True
    persona = str(params.get("persona") or "")
    if persona:
        with contextlib.suppress(Exception):
            import earcrate.app as app_module

            contract = dict(
                getattr(app_module, "TASTE_PROFILES", {}).get(persona) or {}
            )
            if contract:
                params["reuse_policy_override"] = contract

    proxy = _DiagnosticExactDeckProxy(core, bpm, key)
    original = getattr(core, "_ordinary_compose_taste_arrangement", None)
    if original is None:
        wrapped = getattr(core, "compose_taste_arrangement")
        original = getattr(wrapped, "__wrapped__", None)
    if original is None:
        raise _core.FixtureSlotQualificationError(
            "ordinary TasteSpec composer is unavailable for slot census"
        )
    compose = getattr(original, "__func__", original)
    result = compose(
        proxy,
        [dict(item) for item in pool],
        params,
        int(seed),
    )
    if abs(float(result.get("bpm") or 0.0) - bpm) > _core.EPS:
        raise _core.FixtureSlotQualificationError(
            f"raw census arrangement did not retain exact BPM for "
            f"{row['island_id']}"
        )
    actual_key = result.get("target_key")
    if actual_key is None or int(actual_key) % 12 != key:
        raise _core.FixtureSlotQualificationError(
            f"raw census arrangement did not retain exact key for "
            f"{row['island_id']}"
        )
    observation = proxy.turnover_observation
    if not isinstance(observation, Mapping):
        raise _core.FixtureSlotQualificationError(
            "slot-census turnover probe produced no observation"
        )
    result = dict(result)
    result["slot_census_probe"] = copy.deepcopy(dict(observation))
    return result, params


def _build_census_with_probe_receipt(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
    *,
    island_id: Optional[str] = None,
) -> Dict[str, Any]:
    body = dict(
        _ORIGINAL_BUILD_EXACT_POOL_SLOT_CENSUS(
            core,
            arrangement,
            pool,
            params,
            seed,
            island_id=island_id,
        )
    )
    observation = arrangement.get("slot_census_probe")
    if isinstance(observation, Mapping):
        body["diagnostic_skeleton_probe"] = copy.deepcopy(dict(observation))
        body["slot_census_sha256"] = _binding._census_identity(body)
    return body


def install_slot_census_turnover_probe() -> None:
    """Install one private diagnostic path; leave product composition untouched."""
    if getattr(_core, "_slot_census_turnover_probe_installed", False):
        return
    _core._raw_island_arrangement = _diagnostic_raw_island_arrangement
    _binding.build_exact_pool_slot_census = _build_census_with_probe_receipt
    _core._slot_census_turnover_probe_installed = True


__all__ = [
    "PROBE_VERSION",
    "install_slot_census_turnover_probe",
]
