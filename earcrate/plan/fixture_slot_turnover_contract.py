"""Exact TasteSpec turnover verification for diagnostic slot censuses.

The diagnostic census may continue past one narrowly classified product refusal:
the ordinary composer rejected the candidate-restricted exact deck because it
retained fewer distinct sources than the active TasteSpec requires.  The refusal
text is evidence, not authority.  This module independently derives the same
required count that ordinary composition uses and patches the census classifier
so both numbers in the message must agree with measured and policy-bound facts.

The patch is additive and observation-only.  It changes no product planning,
composition, qualification, rendering, or publication path.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_slot_binding as _binding
from earcrate.plan.math import DEFAULT_SOURCE_SECONDS, sources_needed

_EXPECTED_TURNOVER_SOURCES: ContextVar[Optional[int]] = ContextVar(
    "earcrate_fixture_slot_expected_turnover_sources",
    default=None,
)


def _effective_turnover_requirement(
    row: Mapping[str, Any],
    base: Mapping[str, Any],
) -> int:
    """Reproduce the ordinary composer's active source-turnover arithmetic.

    ``compose_taste_arrangement`` starts with the requested TasteSpec profile,
    then applies ``reuse_policy_override``.  The exact-island adapter replaces
    that override with the named persona contract when one exists.  Reproduce
    that precedence before applying the shared ``sources_needed`` arithmetic to
    the island's exact allocated duration.
    """
    from earcrate.core.deps import TASTE_PROFILES

    profile_name = str(
        base.get("taste_profile") or base.get("profile") or "girl_talk_v1"
    )
    profile = dict(
        TASTE_PROFILES.get(profile_name)
        or TASTE_PROFILES["girl_talk_v1"]
    )

    override = dict(base.get("reuse_policy_override") or {})
    persona = str(base.get("persona") or "")
    if persona:
        persona_contract = dict(TASTE_PROFILES.get(persona) or {})
        if persona_contract:
            # _raw_island_arrangement uses this exact replacement precedence.
            override = persona_contract
    if override:
        profile = {**profile, **override}

    source_seconds = float(
        profile.get("source_seconds") or DEFAULT_SOURCE_SECONDS
    )
    return int(
        sources_needed(
            float(row["allocated_duration_s"]),
            source_seconds,
        )
    )


def install_fixture_slot_turnover_contract() -> None:
    """Install one fail-closed required-count guard on the census observer."""
    if getattr(_binding, "_fixture_slot_turnover_contract_installed", False):
        return

    original_counts = _binding._turnover_refusal_counts
    original_compose = _binding._compose_census_skeleton

    def guarded_counts(
        error: BaseException,
        restricted_diagnostics: Optional[Mapping[str, Any]],
    ) -> Optional[Tuple[int, int]]:
        counts = original_counts(error, restricted_diagnostics)
        if counts is None:
            return None
        expected = _EXPECTED_TURNOVER_SOURCES.get()
        if expected is None or int(counts[1]) != int(expected):
            return None
        return counts

    def guarded_compose(
        core: Any,
        row: Mapping[str, Any],
        base: Mapping[str, Any],
        seed: int,
        restricted: Sequence[Mapping[str, Any]],
        restricted_diagnostics: Optional[Mapping[str, Any]],
        full_deck: Sequence[Mapping[str, Any]],
        full_diagnostics: Optional[Mapping[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
        expected = _effective_turnover_requirement(row, base)
        token = _EXPECTED_TURNOVER_SOURCES.set(expected)
        try:
            return original_compose(
                core,
                row,
                base,
                seed,
                restricted,
                restricted_diagnostics,
                full_deck,
                full_diagnostics,
            )
        finally:
            _EXPECTED_TURNOVER_SOURCES.reset(token)

    guarded_counts.__name__ = original_counts.__name__
    guarded_counts.__doc__ = original_counts.__doc__
    guarded_counts.__wrapped__ = original_counts
    guarded_compose.__name__ = original_compose.__name__
    guarded_compose.__doc__ = original_compose.__doc__
    guarded_compose.__wrapped__ = original_compose

    _binding._turnover_refusal_counts = guarded_counts
    _binding._compose_census_skeleton = guarded_compose
    _binding._fixture_slot_turnover_contract_installed = True


__all__ = [
    "install_fixture_slot_turnover_contract",
]
