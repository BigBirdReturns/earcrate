"""Canonical census binding and durable refusal receipts."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from earcrate.plan import fixture_slot_qualification_core as _core

DEFAULT_MAX_SOURCE_EVENTS = _core.DEFAULT_MAX_SOURCE_EVENTS
EPS = _core.EPS
FixtureSlotQualificationError = _core.FixtureSlotQualificationError
INDETERMINATE_ACTION = _core.INDETERMINATE_ACTION
SLOT_CENSUS_VERSION = _core.SLOT_CENSUS_VERSION
SLOT_QUALIFICATION_VERSION = _core.SLOT_QUALIFICATION_VERSION
canonical_json = _core.canonical_json
semantic_sha256 = _core.semantic_sha256

def _canonical_census_body(census: Mapping[str, Any]) -> Dict[str, Any]:
    body = copy.deepcopy(
        {
            key: value
            for key, value in census.items()
            if key != "slot_census_sha256"
        }
    )
    slots = [dict(row) for row in body.get("slots") or []]
    for row in slots:
        row["compatible_sources"] = sorted(
            {str(value) for value in row.get("compatible_sources") or []}
        )
    slots.sort(
        key=lambda row: (
            tuple(int(value) for value in row.get("slot_key") or []),
            str(row.get("role_family") or ""),
            str(row.get("slot_role") or ""),
        )
    )
    body["slots"] = slots
    sources = [dict(row) for row in body.get("sources") or []]
    for row in sources:
        for field in (
            "natural_role_families",
            "planner_role_capabilities",
        ):
            if field in row:
                row[field] = sorted(
                    {str(value) for value in row.get(field) or []}
                )
        if "reachable_slots" in row:
            row["reachable_slots"] = sorted(
                [list(value) for value in row.get("reachable_slots") or []]
            )
    sources.sort(key=lambda row: str(row.get("source_id") or ""))
    body["sources"] = sources
    for field in (
        "candidate_source_ids",
        "candidate_required_roles",
    ):
        if field in body:
            body[field] = sorted(
                {str(value) for value in body.get(field) or []}
            )
    return body


def _census_identity(census: Mapping[str, Any]) -> str:
    return semantic_sha256(_canonical_census_body(census))


def _canonical_campaign_body(
    campaign: Mapping[str, Any],
) -> Dict[str, Any]:
    body = copy.deepcopy(
        {
            key: value
            for key, value in campaign.items()
            if key != "campaign_sha256"
        }
    )
    islands = [
        _canonical_census_body(row)
        for row in body.get("islands") or []
    ]
    for row in islands:
        row["slot_census_sha256"] = _census_identity(row)
    islands.sort(key=lambda row: str(row.get("island_id") or ""))
    body["islands"] = islands
    return body


def _campaign_identity(campaign: Mapping[str, Any]) -> str:
    return semantic_sha256(_canonical_campaign_body(campaign))


def _source_capabilities(
    pool: Sequence[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    from earcrate.plan.exact_pool_assignment import (
        _stable_source_identity,
        require_stable_identity,
    )
    from earcrate.plan.islands import role_tokens

    require_stable_identity(pool)
    capabilities: Dict[str, set[str]] = {}
    for item in pool:
        source_id = _stable_source_identity(item)
        assert source_id is not None
        capabilities.setdefault(source_id, set()).update(role_tokens(item))
    return {
        source_id: sorted(values)
        for source_id, values in sorted(capabilities.items())
    }


def build_exact_pool_slot_census(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
    *,
    island_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Add planner role capabilities to the exact compatibility census."""
    body = _core.build_exact_pool_slot_census(
        core,
        arrangement,
        pool,
        params,
        seed,
        island_id=island_id,
    )
    capabilities = _source_capabilities(pool)
    for row in body.get("sources") or []:
        source_id = str(row.get("source_id") or "")
        row["planner_role_capabilities"] = list(
            capabilities.get(source_id, ())
        )
    body["slot_census_sha256"] = _census_identity(body)
    return body


def build_fixture_slot_census_campaign(
    core: Any, params: Mapping[str, Any]
) -> Dict[str, Any]:
    """Compose each refused island skeleton and bind one augmented census."""
    from earcrate.plan.islands import (
        allocate_phrase_aligned_islands,
        atom_identity,
        source_identity,
        source_pool_identity,
        validate_request,
    )

    validate_request(params)
    profile = str(params["profile"])
    excludes = {
        str(value) for value in params.get("source_exclude_ids") or []
    }
    pool = [dict(item) for item in core.approved_atom_pool(profile)]
    current_pool_sha = source_pool_identity(pool, excludes)
    if current_pool_sha != str(params["source_pool_sha256"]):
        raise FixtureSlotQualificationError(
            "source pool identity mismatch: "
            f"current {current_pool_sha}, requested "
            f"{params['source_pool_sha256']}"
        )
    usable = [
        item
        for item in pool
        if source_identity(item) not in excludes
        and atom_identity(item) not in excludes
    ]
    candidate_universe = {
        str(source_id)
        for row in params.get("islands") or []
        for source_id in row.get("source_include_ids") or []
    }
    if not candidate_universe:
        raise FixtureSlotQualificationError(
            "slot census requires an immutable candidate source universe"
        )
    usable = [
        item for item in usable if source_identity(item) in candidate_universe
    ]
    observed_universe = {source_identity(item) for item in usable}
    missing = sorted(candidate_universe - observed_universe)
    if missing:
        raise FixtureSlotQualificationError(
            f"candidate universe source is absent from the live pool: {missing[0]}"
        )
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in usable:
        by_source.setdefault(source_identity(item), []).append(item)

    requested = [dict(row) for row in params.get("islands") or []]
    allocated, _transitions, net_duration = (
        allocate_phrase_aligned_islands(
            requested, float(params.get("duration_s") or 0.0)
        )
    )
    base = _core._base_params(params)
    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(allocated):
        include_ids = sorted(
            {
                str(value)
                for value in row.get("source_include_ids") or []
            }
        )
        allowed = [
            dict(item)
            for source_id in include_ids
            for item in by_source.get(source_id, ())
        ]
        restricted, _restricted_diagnostics = core.taste_feasible_pool(
            allowed,
            float(row["target_bpm"]),
            int(row["target_key"]),
            base,
        )
        if not restricted:
            raise FixtureSlotQualificationError(
                f"island {row.get('island_id')!r} has no restricted "
                "transform-safe pool for census"
            )
        full_deck, full_diagnostics = core.taste_feasible_pool(
            usable,
            float(row["target_bpm"]),
            int(row["target_key"]),
            base,
        )
        if not full_deck:
            raise FixtureSlotQualificationError(
                f"island {row.get('island_id')!r} has no campaign-universe "
                "transform-safe pool for census"
            )
        raw, compose_params = _core._raw_island_arrangement(
            core,
            restricted,
            row,
            base,
            int(params.get("seed") or 0) + index,
        )
        census = build_exact_pool_slot_census(
            core,
            raw,
            full_deck,
            compose_params,
            int(params.get("seed") or 0) + index,
            island_id=str(row["island_id"]),
        )
        census.update(
            {
                "deck_id": str(row.get("deck_id") or ""),
                "policy_identity": _core._policy_identity(params),
                "candidate_source_ids": include_ids,
                "candidate_required_roles": sorted(
                    {
                        str(value)
                        for value in row.get("required_roles") or []
                    }
                ),
                "candidate_min_sources": int(
                    row.get("min_sources") or 1
                ),
                "candidate_max_sources": int(
                    row.get("max_sources") or len(candidate_universe)
                ),
                "campaign_universe_deck_diagnostics": {
                    "have": dict(
                        (full_diagnostics or {}).get("have") or {}
                    ),
                    "pool_size": int(
                        (full_diagnostics or {}).get("pool_size")
                        or len(full_deck)
                    ),
                },
            }
        )
        census["slot_census_sha256"] = _census_identity(census)
        rows.append(census)

    body: Dict[str, Any] = {
        "kind": "earcrate_fixture_slot_census_campaign",
        "version": SLOT_CENSUS_VERSION,
        "candidate_fixture_sha256": str(
            params.get("fixture_sha256") or params.get("fixture_id") or ""
        ),
        "source_pool_sha256": current_pool_sha,
        "source_universe_sha256": semantic_sha256(
            sorted(candidate_universe)
        ),
        "source_count": len(candidate_universe),
        "policy_identity": _core._policy_identity(params),
        "seed": int(params.get("seed") or 0),
        "duration_s": float(net_duration),
        "island_count": len(rows),
        "islands": rows,
        "impossibility_claimed": False,
        "disposition": "observed_raw_section_graphs_for_repartition",
    }
    body["campaign_sha256"] = _campaign_identity(body)
    return body


def _verified_census_campaign(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    islands = [dict(row) for row in candidate.get("islands") or []]
    census_rows = [dict(row) for row in census_campaign.get("islands") or []]
    by_census_id = {
        str(row.get("island_id") or ""): row for row in census_rows
    }
    candidate_ids = [str(row.get("island_id") or "") for row in islands]
    if not all(candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise FixtureSlotQualificationError(
            "candidate island IDs must be unique and non-empty"
        )
    if len(by_census_id) != len(census_rows):
        raise FixtureSlotQualificationError(
            "slot census island IDs must be unique and non-empty"
        )
    if set(candidate_ids) != set(by_census_id):
        raise FixtureSlotQualificationError(
            "census island set does not match the fixture candidate"
        )

    supplied_campaign_sha = str(
        census_campaign.get("campaign_sha256") or ""
    )
    calculated_campaign_sha = _campaign_identity(census_campaign)
    if supplied_campaign_sha != calculated_campaign_sha:
        raise FixtureSlotQualificationError(
            "slot census campaign digest does not match its content"
        )
    for census in census_rows:
        supplied = str(census.get("slot_census_sha256") or "")
        calculated = _census_identity(census)
        if supplied != calculated:
            raise FixtureSlotQualificationError(
                f"slot census digest mismatch for {census.get('island_id')!r}"
            )

    candidate_identity = str(candidate.get("fixture_sha256") or "")
    if not candidate_identity:
        raise FixtureSlotQualificationError(
            "fixture candidate has no fixture_sha256 semantic identity"
        )
    from earcrate.plan.fixture_diversity import fixture_projection

    calculated_candidate_identity = str(
        fixture_projection(candidate)["fixture_identity"]
    )
    if candidate_identity != calculated_candidate_identity:
        raise FixtureSlotQualificationError(
            "fixture candidate semantic identity does not match its content"
        )
    if str(census_campaign.get("candidate_fixture_sha256") or "") != candidate_identity:
        raise FixtureSlotQualificationError(
            "slot census is bound to a different fixture candidate"
        )
    candidate_pool = str(candidate.get("source_pool_sha256") or "")
    if candidate_pool and str(census_campaign.get("source_pool_sha256") or "") != candidate_pool:
        raise FixtureSlotQualificationError(
            "slot census source-pool identity does not match the candidate"
        )
    expected_policy = _core._policy_identity(candidate)
    if str(census_campaign.get("policy_identity") or "") != expected_policy:
        raise FixtureSlotQualificationError(
            "slot census policy identity does not match the candidate"
        )

    for island in islands:
        island_id = str(island["island_id"])
        census = by_census_id[island_id]
        candidate_deck = str(island.get("deck_id") or "")
        census_deck = str(census.get("deck_id") or "")
        if candidate_deck != census_deck:
            raise FixtureSlotQualificationError(
                f"slot census deck identity mismatch for {island_id!r}"
            )
        if abs(
            float(island.get("target_bpm") or 0.0)
            - float(census.get("render_bpm") or 0.0)
        ) > EPS:
            raise FixtureSlotQualificationError(
                f"slot census BPM mismatch for {island_id!r}"
            )
        if int(island.get("target_key") or 0) % 12 != int(
            census.get("target_key") or 0
        ) % 12:
            raise FixtureSlotQualificationError(
                f"slot census key mismatch for {island_id!r}"
            )
        if abs(
            float(island.get("allocated_duration_s") or 0.0)
            - float(census.get("allocated_duration_s") or 0.0)
        ) > EPS:
            raise FixtureSlotQualificationError(
                f"slot census duration mismatch for {island_id!r}"
            )
        required_roles = sorted(
            {str(value) for value in island.get("required_roles") or []}
        )
        if required_roles != list(census.get("candidate_required_roles") or []):
            raise FixtureSlotQualificationError(
                f"slot census required-role contract mismatch for {island_id!r}"
            )
        if int(island.get("min_sources") or 1) != int(
            census.get("candidate_min_sources") or 1
        ):
            raise FixtureSlotQualificationError(
                f"slot census minimum-source contract mismatch for {island_id!r}"
            )
        if int(island.get("max_sources") or 0) != int(
            census.get("candidate_max_sources") or 0
        ):
            raise FixtureSlotQualificationError(
                f"slot census maximum-source contract mismatch for {island_id!r}"
            )
    return islands, by_census_id


def _failure(
    failure_class: str,
    reason: str,
    *,
    proof: Optional[Mapping[str, Any]] = None,
    solver: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return _core._failure(
        failure_class,
        reason,
        proof=proof,
        solver=solver,
    )

def _publish_census_run(
    core: Any,
    params: Mapping[str, Any],
    deficiency: MutableMapping[str, Any],
    census: Mapping[str, Any],
) -> None:
    if not all(
        hasattr(core, name)
        for name in (
            "_run_bundle_begin",
            "_run_bundle_set_plan",
            "_run_bundle_finish",
        )
    ):
        return
    run = core._run_bundle_begin(
        "fixture_slot_census",
        {
            "entrypoint": "fixture_slot_census_after_plan_refusal",
            "parent_failure_class": str(
                deficiency.get("failure_class") or ""
            ),
            "candidate_fixture_sha256": str(
                params.get("fixture_sha256")
                or params.get("fixture_id")
                or ""
            ),
            "source_pool_sha256": str(
                params.get("source_pool_sha256") or ""
            ),
            "campaign_sha256": str(
                census.get("campaign_sha256") or ""
            ),
        },
    )
    run_id = str(run["run_id"])
    core._run_bundle_set_plan(
        run_id,
        None,
        None,
        "slot census is a receipt over a refused raw arrangement family",
        state="observed",
    )
    core._run_bundle_finish(
        run_id,
        True,
        {
            "kind": "earcrate_fixture_slot_census_campaign",
            "campaign": dict(census),
        },
    )
    deficiency["fixture_slot_census_run_id"] = run_id
    deficiency["fixture_slot_census_run_bundle"] = str(run["path"])


def install_fixture_slot_census(core_class: Any) -> Any:
    """Attach and durably receipt a census only on exact-pool refusals."""
    if getattr(core_class, "_fixture_slot_census_installed", False):
        return core_class
    original = core_class.propose_island_set

    def wrapped(self: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return original(self, params)
        except Exception as exc:
            deficiency = getattr(exc, "deficiency", None)
            if not isinstance(deficiency, MutableMapping):
                raise
            if deficiency.get("fixture_slot_census_campaign") is None:
                try:
                    census = build_fixture_slot_census_campaign(
                        self, dict(params or {})
                    )
                    deficiency["fixture_slot_census_campaign"] = census
                    _publish_census_run(
                        self,
                        dict(params or {}),
                        deficiency,
                        census,
                    )
                except Exception as census_error:
                    deficiency["fixture_slot_census_failure"] = {
                        "exception_type": type(census_error).__name__,
                        "error": str(census_error),
                        "impossibility_claimed": False,
                        "private_acceptance": INDETERMINATE_ACTION,
                    }
            raise

    wrapped.__name__ = getattr(
        original, "__name__", "propose_island_set"
    )
    wrapped.__doc__ = getattr(original, "__doc__", None)
    wrapped.__wrapped__ = original
    core_class.propose_island_set = wrapped
    core_class._fixture_slot_census_installed = True
    return core_class


__all__ = [
    "DEFAULT_MAX_SOURCE_EVENTS", "EPS", "FixtureSlotQualificationError",
    "INDETERMINATE_ACTION", "SLOT_CENSUS_VERSION",
    "SLOT_QUALIFICATION_VERSION", "build_exact_pool_slot_census",
    "build_fixture_slot_census_campaign", "canonical_json",
    "install_fixture_slot_census", "semantic_sha256",
]
