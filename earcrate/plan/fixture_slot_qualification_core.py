"""Slot-qualified fixture partitioning for governed multi-island candidates.

Fixture derivation knows which sources survive each exact deck. Exact-pool
assignment knows whether one already-built section graph can cover an allowlist.
This module closes the layer between them without changing either authority:

* a refusal can carry a public-safe census of the role slots the ordinary
  composer actually built, together with every stable source that can occupy
  each slot under the existing role, transform and score laws;
* a deterministic mixed-integer assignment repartitions one immutable source
  universe across those observed island skeletons while preserving deck order,
  phrase allocation, policies and the per-source event cap;
* one qualification round produces a new fixture candidate identity. It does
  not claim that the next composer pass will retain the same skeleton. A later
  refusal therefore supplies the next census and another bounded round.

Solver limits and solver-only infeasibility are never reported as mathematical
proof. Only explicit counting or Hall witnesses set ``impossibility_claimed``.
"""
from __future__ import annotations

from collections import Counter, deque
import contextlib
import copy
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

SLOT_CENSUS_VERSION = "earcrate_exact_pool_slot_census_v1"
SLOT_QUALIFICATION_VERSION = "earcrate_fixture_slot_qualification_v1"
INDETERMINATE_ACTION = "halt_slot_qualification_this_is_not_an_impossibility_proof"
DEFAULT_MAX_SOURCE_EVENTS = 12
EPS = 1e-9


class FixtureSlotQualificationError(ValueError):
    """A census or fixture candidate is malformed or mutually inconsistent."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def semantic_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _policy_identity(params: Mapping[str, Any]) -> str:
    body = {
        "profile": str(params.get("profile") or params.get("taste_profile") or ""),
        "persona": str(params.get("persona") or ""),
        "phrase_playback_law": str(params.get("phrase_playback_law") or ""),
        "transform_policy": dict(params.get("transform_policy") or {}),
        "turnover_policy": dict(params.get("turnover_policy") or {}),
        "transition": dict(params.get("transition") or {}),
        "exact_pool_max_source_events": int(
            params.get("exact_pool_max_source_events") or DEFAULT_MAX_SOURCE_EVENTS
        ),
    }
    return semantic_sha256(body)


def _pool_projection(pool: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    from earcrate.plan.exact_pool_assignment import (
        _stable_atom_identity,
        _stable_source_identity,
        require_stable_identity,
    )

    require_stable_identity(pool)
    rows: List[Dict[str, Any]] = []
    for item in pool:
        source_id = _stable_source_identity(item)
        atom_id = _stable_atom_identity(item)
        assert source_id is not None and atom_id is not None
        rows.append({
            "source_id": source_id,
            "atom_id": atom_id,
            "ear_role": str(item.get("ear_role") or ""),
            "render_role": str(item.get("render_role") or item.get("role") or ""),
            "bpm_hex": float(item.get("bpm") or 0.0).hex(),
            "key_root": int(item.get("key_root") or 0) % 12,
            "bars": int(item.get("bars") or 0),
        })
    rows.sort(key=lambda row: (row["source_id"], row["atom_id"]))
    return rows


def build_exact_pool_slot_census(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
    *,
    island_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Project the exact assignment graph without publishing an assignment."""
    from earcrate.plan import source_rotation as rotation
    from earcrate.plan.exact_pool_assignment import (
        _build_edges,
        _slot_table,
        _stable_atom_identity,
        _stable_source_identity,
        require_stable_identity,
    )

    items = [dict(item) for item in pool]
    require_stable_identity(items)
    pool_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        source_id = _stable_source_identity(item)
        assert source_id is not None
        pool_by_source.setdefault(source_id, []).append(item)
    for source_id in pool_by_source:
        pool_by_source[source_id].sort(
            key=lambda item: str(_stable_atom_identity(item) or "")
        )
    ordered_sources = sorted(pool_by_source)
    frozen_sections = copy.deepcopy(list(arrangement.get("sections") or []))
    slots = _slot_table(arrangement)
    render_bpm = float(
        arrangement.get("bpm")
        or params.get("exact_target_bpm")
        or params.get("bpm")
        or 0.0
    )
    target_key = int(
        arrangement.get("target_key")
        if arrangement.get("target_key") is not None
        else params.get("exact_target_key") or 0
    ) % 12
    max_events = int(
        params.get("exact_pool_max_source_events") or DEFAULT_MAX_SOURCE_EVENTS
    )
    if max_events <= 0:
        raise FixtureSlotQualificationError(
            "exact_pool_max_source_events must be positive"
        )
    edges, reach = _build_edges(
        core,
        frozen_sections,
        slots,
        ordered_sources,
        pool_by_source,
        render_bpm,
        target_key,
        params,
        int(seed),
    )

    slot_rows: List[Dict[str, Any]] = []
    role_counts: Counter[str] = Counter()
    source_slots: Dict[str, List[List[int]]] = {
        source_id: [] for source_id in ordered_sources
    }
    sections = list(arrangement.get("sections") or [])
    for slot_key, section_index, layer_index, layer in slots:
        slot_role = str(layer.get("role") or "full")
        family = rotation._role_family(slot_role)
        role_counts[family] += 1
        compatible_sources = sorted(edges.get(slot_key, {}))
        for source_id in compatible_sources:
            source_slots[source_id].append([int(slot_key[0]), int(slot_key[1])])
        section = sections[section_index] if section_index < len(sections) else {}
        slot_rows.append({
            "slot_key": [int(slot_key[0]), int(slot_key[1])],
            "section_type": str(
                section.get("type") or section.get("section_type") or ""
            ),
            "slot_role": slot_role,
            "role_family": family,
            "incumbent_source": str(
                layer.get("source_track_key")
                or layer.get("source_id")
                or layer.get("loop_id")
                or ""
            ),
            "compatible_sources": compatible_sources,
        })

    source_rows: List[Dict[str, Any]] = []
    for source_id in ordered_sources:
        natural_families = sorted({
            rotation._role_family(rotation._natural_role(item))
            for item in pool_by_source[source_id]
        })
        source_rows.append({
            "source_id": source_id,
            "natural_role_families": natural_families,
            "reachable_slots": source_slots[source_id],
            "reachable_slot_count": len(source_slots[source_id]),
            "reach_diagnostics": {
                key: int(value) for key, value in sorted(reach[source_id].items())
            },
        })

    skeleton_projection = {
        "render_bpm_hex": render_bpm.hex(),
        "target_key": target_key,
        "sections": [
            {
                "bar_start": int(section.get("bar_start") or index),
                "bars": int(section.get("bars") or 0),
                "type": str(
                    section.get("type") or section.get("section_type") or ""
                ),
                "roles": [
                    str(layer.get("role") or "full")
                    for layer in section.get("layers") or []
                ],
            }
            for index, section in enumerate(sections)
        ],
    }
    body: Dict[str, Any] = {
        "kind": "earcrate_exact_pool_slot_census",
        "version": SLOT_CENSUS_VERSION,
        "island_id": str(
            island_id
            or params.get("island_id")
            or arrangement.get("island_id")
            or ""
        ),
        "render_bpm": render_bpm,
        "render_bpm_hex": render_bpm.hex(),
        "target_key": target_key,
        "allocated_duration_s": float(
            params.get("target_seconds") or arrangement.get("duration_s") or 0.0
        ),
        "seed": int(seed),
        "max_source_events": max_events,
        "composer_law": {
            "dj_compiler": dict(arrangement.get("dj_compiler") or {}),
            "phrase_playback_law": str(params.get("phrase_playback_law") or ""),
            "taste_profile": str(
                params.get("taste_profile") or params.get("profile") or ""
            ),
        },
        "policy_identity": _policy_identity(params),
        "pool_projection_sha256": semantic_sha256(_pool_projection(items)),
        "skeleton_sha256": semantic_sha256(skeleton_projection),
        "slot_count": len(slot_rows),
        "slot_count_by_role_family": {
            key: int(value) for key, value in sorted(role_counts.items())
        },
        "source_count": len(source_rows),
        "slots": slot_rows,
        "sources": source_rows,
        "path_semantics": "no_filesystem_or_human_media_identity",
    }
    body["slot_census_sha256"] = semantic_sha256(body)
    return body


def _base_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "taste_profile": str(params["profile"]),
        "persona": str(params["persona"]),
        "phrase_playback_law": str(params["phrase_playback_law"]),
        "stretch_budget": float(
            (params.get("transform_policy") or {}).get("stretch_budget")
            or params.get("stretch_budget")
            or 8.0
        ),
        "pitch_shift_budget": int(
            (params.get("transform_policy") or {}).get("pitch_shift_budget")
            or params.get("pitch_shift_budget")
            or 2
        ),
        "quality_mode": "stable_deck",
        "post_render_gate": True,
        "mix_mode": "tastespec_graph",
    }
    for key in (
        "recurrence_scores",
        "foreground_rank_recurrence",
        "reuse_policy_override",
        "max_aux_decks",
        "exact_pool_max_source_events",
    ):
        if key in params:
            body[key] = copy.deepcopy(params[key])
    return body


def _raw_island_arrangement(
    core: Any,
    pool: Sequence[Mapping[str, Any]],
    row: Mapping[str, Any],
    base_params: Mapping[str, Any],
    seed: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from earcrate.plan.islands import ExactDeckProxy

    bpm = float(row["target_bpm"])
    key = int(row["target_key"]) % 12
    params = dict(base_params)
    params.update({
        "target_seconds": float(row["allocated_duration_s"]),
        "bpm": bpm,
        "exact_target_bpm": bpm,
        "exact_target_key": key,
        "stem_export": True,
        "island_id": str(row["island_id"]),
    })
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

    proxy = ExactDeckProxy(core, bpm, key)
    original = getattr(core, "_ordinary_compose_taste_arrangement", None)
    if original is None:
        wrapped = getattr(core, "compose_taste_arrangement")
        original = getattr(wrapped, "__wrapped__", None)
    if original is None:
        raise FixtureSlotQualificationError(
            "ordinary TasteSpec composer is unavailable for slot census"
        )
    compose = getattr(original, "__func__", original)
    result = compose(proxy, [dict(item) for item in pool], params, int(seed))
    if abs(float(result.get("bpm") or 0.0) - bpm) > EPS:
        raise FixtureSlotQualificationError(
            f"raw census arrangement did not retain exact BPM for {row['island_id']}"
        )
    actual_key = result.get("target_key")
    if actual_key is None or int(actual_key) % 12 != key:
        raise FixtureSlotQualificationError(
            f"raw census arrangement did not retain exact key for {row['island_id']}"
        )
    return dict(result), params


def build_fixture_slot_census_campaign(
    core: Any, params: Mapping[str, Any]
) -> Dict[str, Any]:
    """Compose every raw island skeleton and census it against the campaign universe."""
    from earcrate.plan.islands import (
        allocate_phrase_aligned_islands,
        atom_identity,
        source_identity,
        source_pool_identity,
        validate_request,
    )

    validate_request(params)
    profile = str(params["profile"])
    excludes = {str(value) for value in params.get("source_exclude_ids") or []}
    pool = [dict(item) for item in core.approved_atom_pool(profile)]
    current_pool_sha = source_pool_identity(pool, excludes)
    if current_pool_sha != str(params["source_pool_sha256"]):
        raise FixtureSlotQualificationError(
            f"source pool identity mismatch: current {current_pool_sha}, requested {params['source_pool_sha256']}"
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
    if candidate_universe:
        usable = [
            item for item in usable if source_identity(item) in candidate_universe
        ]
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in usable:
        by_source.setdefault(source_identity(item), []).append(item)

    requested = [dict(row) for row in params.get("islands") or []]
    allocated, _transitions, net_duration = allocate_phrase_aligned_islands(
        requested, float(params.get("duration_s") or 0.0)
    )
    base = _base_params(params)
    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(allocated):
        include_ids = sorted({
            str(value) for value in row.get("source_include_ids") or []
        })
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
                f"island {row.get('island_id')!r} has no restricted transform-safe pool for census"
            )
        full_deck, full_diagnostics = core.taste_feasible_pool(
            usable,
            float(row["target_bpm"]),
            int(row["target_key"]),
            base,
        )
        if not full_deck:
            raise FixtureSlotQualificationError(
                f"island {row.get('island_id')!r} has no campaign-universe transform-safe pool for census"
            )
        raw, compose_params = _raw_island_arrangement(
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
        census.update({
            "deck_id": str(row.get("deck_id") or ""),
            "policy_identity": _policy_identity(params),
            "candidate_source_ids": include_ids,
            "campaign_universe_deck_diagnostics": {
                "have": dict((full_diagnostics or {}).get("have") or {}),
                "pool_size": int(
                    (full_diagnostics or {}).get("pool_size") or len(full_deck)
                ),
            },
        })
        census["slot_census_sha256"] = semantic_sha256({
            key: value
            for key, value in census.items()
            if key != "slot_census_sha256"
        })
        rows.append(census)

    body: Dict[str, Any] = {
        "kind": "earcrate_fixture_slot_census_campaign",
        "version": SLOT_CENSUS_VERSION,
        "candidate_fixture_sha256": str(
            params.get("fixture_sha256") or params.get("fixture_id") or ""
        ),
        "source_pool_sha256": current_pool_sha,
        "policy_identity": _policy_identity(params),
        "seed": int(params.get("seed") or 0),
        "duration_s": float(net_duration),
        "island_count": len(rows),
        "islands": rows,
        "impossibility_claimed": False,
        "disposition": "observed_raw_section_graphs_for_repartition",
    }
    body["campaign_sha256"] = semantic_sha256(body)
    return body


def _candidate_body(candidate: Mapping[str, Any]) -> MutableMapping[str, Any]:
    if not isinstance(candidate, Mapping):
        raise FixtureSlotQualificationError("fixture candidate must be a mapping")
    if isinstance(candidate.get("arrangement"), Mapping):
        raise FixtureSlotQualificationError(
            "slot qualification accepts direct fixture candidates, not arrangement realizations"
        )
    if str(candidate.get("kind") or "") != "earcrate_fixture_candidate":
        raise FixtureSlotQualificationError(
            "slot qualification requires kind=earcrate_fixture_candidate"
        )
    return copy.deepcopy(dict(candidate))


def _global_hall_witness(
    sources: Sequence[str],
    source_slots: Mapping[str, Sequence[Tuple[str, int, int]]],
) -> Optional[Dict[str, Any]]:
    match_slot: Dict[Tuple[str, int, int], str] = {}
    match_source: Dict[str, Tuple[str, int, int]] = {}

    def augment(source_id: str, visited: set[Tuple[str, int, int]]) -> bool:
        for slot in source_slots.get(source_id, ()):
            if slot in visited:
                continue
            visited.add(slot)
            owner = match_slot.get(slot)
            if owner is None or augment(owner, visited):
                match_slot[slot] = source_id
                match_source[source_id] = slot
                return True
        return False

    for source_id in sources:
        augment(source_id, set())
    unmatched = [source_id for source_id in sources if source_id not in match_source]
    if not unmatched:
        return None
    reached_sources = set(unmatched)
    reached_slots: set[Tuple[str, int, int]] = set()
    queue = deque(unmatched)
    while queue:
        source_id = queue.popleft()
        for slot in source_slots.get(source_id, ()):
            if slot in reached_slots:
                continue
            reached_slots.add(slot)
            owner = match_slot.get(slot)
            if owner is not None and owner not in reached_sources:
                reached_sources.add(owner)
                queue.append(owner)
    return {
        "deficient_source_subset": sorted(reached_sources),
        "deficient_source_count": len(reached_sources),
        "compatible_slot_neighbourhood": [
            [island_id, bar_start, layer_index]
            for island_id, bar_start, layer_index in sorted(reached_slots)
        ],
        "neighbourhood_slot_count": len(reached_slots),
        "deficiency": len(reached_sources) - len(reached_slots),
        "minimality": "koenig_reachable_deficient_set_not_proven_cardinality_minimal",
    }


def _failure(
    failure_class: str,
    reason: str,
    *,
    proof: Optional[Mapping[str, Any]] = None,
    solver: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    proved = proof is not None
    body: Dict[str, Any] = {
        "kind": "earcrate_fixture_slot_qualification_receipt",
        "version": SLOT_QUALIFICATION_VERSION,
        "complete": False,
        "failure_class": failure_class,
        "reason": reason,
        "impossibility_claimed": proved,
        "proof": dict(proof or {}),
        "solver": dict(solver or {}),
        "candidate": None,
    }
    if not proved:
        body["private_acceptance"] = INDETERMINATE_ACTION
    return body


def qualify_fixture_candidate(
    candidate: Mapping[str, Any],
    census_campaign: Mapping[str, Any],
    *,
    max_source_events: Optional[int] = None,
    time_limit_s: float = 30.0,
    _solver: Any = milp,
) -> Dict[str, Any]:
    """Repartition one source universe over one observed multi-island skeleton."""
    body = _candidate_body(candidate)
    islands = [dict(row) for row in body.get("islands") or []]
    if not islands:
        raise FixtureSlotQualificationError("fixture candidate has no islands")
    census_rows = [dict(row) for row in census_campaign.get("islands") or []]
    by_census_id = {
        str(row.get("island_id") or ""): row for row in census_rows
    }
    candidate_ids = [str(row.get("island_id") or "") for row in islands]
    if not all(candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise FixtureSlotQualificationError(
            "candidate island IDs must be unique and non-empty"
        )
    if set(candidate_ids) != set(by_census_id):
        raise FixtureSlotQualificationError(
            "census island set does not match the fixture candidate"
        )
    campaign_candidate = str(
        census_campaign.get("candidate_fixture_sha256") or ""
    )
    candidate_identity = str(
        body.get("fixture_sha256") or body.get("fixture_id") or ""
    )
    if (
        campaign_candidate
        and candidate_identity
        and campaign_candidate != candidate_identity
    ):
        raise FixtureSlotQualificationError(
            "slot census is bound to a different fixture candidate"
        )

    original_partition: Dict[str, str] = {}
    for island in islands:
        island_id = str(island["island_id"])
        for source_id in sorted({
            str(value) for value in island.get("source_include_ids") or []
        }):
            if source_id in original_partition:
                raise FixtureSlotQualificationError(
                    f"source {source_id!r} appears in multiple candidate islands"
                )
            original_partition[source_id] = island_id
    sources = sorted(original_partition)
    if not sources:
        raise FixtureSlotQualificationError(
            "fixture candidate has an empty source universe"
        )

    cap_values = {
        int(row.get("max_source_events") or DEFAULT_MAX_SOURCE_EVENTS)
        for row in census_rows
    }
    if max_source_events is None:
        if len(cap_values) != 1:
            raise FixtureSlotQualificationError(
                "slot censuses disagree on max_source_events"
            )
        max_events = next(iter(cap_values))
    else:
        max_events = int(max_source_events)
        if any(value != max_events for value in cap_values):
            raise FixtureSlotQualificationError(
                "requested max_source_events disagrees with a slot census"
            )
    if max_events <= 0:
        raise FixtureSlotQualificationError(
            "max_source_events must be positive"
        )

    slot_rows: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    source_slots: Dict[str, List[Tuple[str, int, int]]] = {
        source_id: [] for source_id in sources
    }
    for island_id in candidate_ids:
        census = by_census_id[island_id]
        for raw_slot in census.get("slots") or []:
            key_values = list(raw_slot.get("slot_key") or [])
            if len(key_values) != 2:
                raise FixtureSlotQualificationError(
                    f"island {island_id!r} has a malformed slot key"
                )
            slot = (island_id, int(key_values[0]), int(key_values[1]))
            if slot in slot_rows:
                raise FixtureSlotQualificationError(
                    f"duplicate slot identity: {slot}"
                )
            compatible = sorted(
                set(
                    str(value)
                    for value in raw_slot.get("compatible_sources") or []
                ).intersection(sources)
            )
            slot_rows[slot] = {
                **dict(raw_slot),
                "compatible_sources": compatible,
            }
            for source_id in compatible:
                source_slots[source_id].append(slot)
    for source_id in sources:
        source_slots[source_id].sort()

    total_slots = len(slot_rows)
    if total_slots < len(sources):
        return _failure(
            "coverage_counting_deficiency",
            "the observed section graphs contain fewer slots than mandatory sources",
            proof={
                "mandatory_source_count": len(sources),
                "slot_count": total_slots,
                "deficiency": len(sources) - total_slots,
            },
        )
    if total_slots > len(sources) * max_events:
        return _failure(
            "cap_counting_deficiency",
            "the observed section graphs contain more slots than the source universe can fill under the cap",
            proof={
                "mandatory_source_count": len(sources),
                "slot_count": total_slots,
                "max_source_events": max_events,
                "capacity": len(sources) * max_events,
                "deficiency": total_slots - len(sources) * max_events,
            },
        )
    hall = _global_hall_witness(sources, source_slots)
    if hall is not None:
        return _failure(
            "role_capacity",
            "the immutable source universe cannot cover the observed compatible slots",
            proof={"hall_witness": hall},
        )
    for island_id in candidate_ids:
        island_slots = [slot for slot in slot_rows if slot[0] == island_id]
        reachable = {
            source_id
            for slot in island_slots
            for source_id in slot_rows[slot]["compatible_sources"]
        }
        if len(island_slots) > len(reachable) * max_events:
            return _failure(
                "island_cap_counting_deficiency",
                f"island {island_id!r} has more slots than its reachable sources can fill under the cap",
                proof={
                    "island_id": island_id,
                    "slot_count": len(island_slots),
                    "reachable_source_count": len(reachable),
                    "max_source_events": max_events,
                    "capacity": len(reachable) * max_events,
                    "deficiency": len(island_slots) - len(reachable) * max_events,
                },
            )

    x_index: Dict[Tuple[str, str], int] = {}
    y_index: Dict[Tuple[Tuple[str, int, int], str], int] = {}
    variable_names: List[str] = []
    objective: List[float] = []

    for source_pos, source_id in enumerate(sources):
        reachable_islands = sorted({slot[0] for slot in source_slots[source_id]})
        for island_pos, island_id in enumerate(candidate_ids):
            if island_id not in reachable_islands:
                continue
            x_index[(source_id, island_id)] = len(variable_names)
            variable_names.append(f"x:{source_id}:{island_id}")
            moved = (
                0.0
                if original_partition[source_id] == island_id
                else 1_000_000.0
            )
            tie = (
                source_pos * max(1, len(candidate_ids)) + island_pos + 1
            ) * 1e-3
            objective.append(moved + tie)

    for slot_pos, slot in enumerate(sorted(slot_rows)):
        for source_pos, source_id in enumerate(
            slot_rows[slot]["compatible_sources"]
        ):
            y_index[(slot, source_id)] = len(variable_names)
            variable_names.append(
                f"y:{slot[0]}:{slot[1]}:{slot[2]}:{source_id}"
            )
            objective.append(
                (slot_pos + 1) * 1e-7 + (source_pos + 1) * 1e-10
            )

    rows_i: List[int] = []
    cols_i: List[int] = []
    data: List[float] = []
    lower: List[float] = []
    upper: List[float] = []

    def add_constraint(
        coefficients: Mapping[int, float], lo: float, hi: float
    ) -> None:
        row_index = len(lower)
        for column, value in sorted(coefficients.items()):
            if abs(value) <= EPS:
                continue
            rows_i.append(row_index)
            cols_i.append(column)
            data.append(float(value))
        lower.append(float(lo))
        upper.append(float(hi))

    for source_id in sources:
        coefficients = {
            index: 1.0
            for (source, _island), index in x_index.items()
            if source == source_id
        }
        if not coefficients:
            return _failure(
                "role_capacity",
                f"source {source_id!r} reaches no observed island slot",
                proof={
                    "hall_witness": {
                        "deficient_source_subset": [source_id],
                        "deficient_source_count": 1,
                        "compatible_slot_neighbourhood": [],
                        "neighbourhood_slot_count": 0,
                        "deficiency": 1,
                    }
                },
            )
        add_constraint(coefficients, 1.0, 1.0)

    for slot in sorted(slot_rows):
        coefficients = {
            index: 1.0
            for (candidate_slot, _source), index in y_index.items()
            if candidate_slot == slot
        }
        if not coefficients:
            return _failure(
                "slot_unreachable",
                f"observed slot {slot!r} has no compatible source in the immutable universe",
                proof={"slot": list(slot), "compatible_source_count": 0},
            )
        add_constraint(coefficients, 1.0, 1.0)

    for (slot, source_id), y_col in y_index.items():
        x_col = x_index[(source_id, slot[0])]
        add_constraint({y_col: 1.0, x_col: -1.0}, -math.inf, 0.0)

    for (source_id, island_id), x_col in sorted(x_index.items()):
        ys = {
            index: 1.0
            for (slot_source, source), index in y_index.items()
            if source == source_id and slot_source[0] == island_id
        }
        coverage = dict(ys)
        coverage[x_col] = coverage.get(x_col, 0.0) - 1.0
        add_constraint(coverage, 0.0, math.inf)
        cap_row = dict(ys)
        cap_row[x_col] = cap_row.get(x_col, 0.0) - float(max_events)
        add_constraint(cap_row, -math.inf, 0.0)

    matrix = coo_matrix(
        (np.asarray(data, dtype=np.float64), (rows_i, cols_i)),
        shape=(len(lower), len(variable_names)),
    ).tocsr()
    constraints = LinearConstraint(
        matrix,
        np.asarray(lower, dtype=np.float64),
        np.asarray(upper, dtype=np.float64),
    )
    result = _solver(
        c=np.asarray(objective, dtype=np.float64),
        integrality=np.ones(len(variable_names), dtype=np.int8),
        bounds=Bounds(
            np.zeros(len(variable_names), dtype=np.float64),
            np.ones(len(variable_names), dtype=np.float64),
        ),
        constraints=constraints,
        options={
            "time_limit": max(0.01, float(time_limit_s)),
            "presolve": True,
            "mip_rel_gap": 0.0,
        },
    )
    solver_receipt = {
        "method": "scipy.optimize.milp_highs",
        "status": int(getattr(result, "status", -1)),
        "success": bool(getattr(result, "success", False)),
        "message": str(getattr(result, "message", "")),
        "objective": (
            None if getattr(result, "fun", None) is None else float(result.fun)
        ),
        "variable_count": len(variable_names),
        "constraint_count": len(lower),
        "time_limit_s": float(time_limit_s),
        "deterministic_variable_order": (
            "source_then_island_then_slot_then_source"
        ),
    }
    if not solver_receipt["success"] or getattr(result, "x", None) is None:
        failure_class = (
            "solver_infeasible_without_portable_witness"
            if solver_receipt["status"] == 2
            else "solver_bound_or_failure"
        )
        return _failure(
            failure_class,
            "slot qualification did not produce a complete assignment; no impossibility is claimed without an explicit witness",
            solver=solver_receipt,
        )

    values = np.asarray(result.x, dtype=np.float64)
    partition: Dict[str, List[str]] = {
        island_id: [] for island_id in candidate_ids
    }
    for (source_id, island_id), column in sorted(x_index.items()):
        if values[column] > 0.5:
            partition[island_id].append(source_id)
    for island_id in partition:
        partition[island_id].sort()
    if sorted(
        source_id for rows in partition.values() for source_id in rows
    ) != sources:
        return _failure(
            "solver_result_invariant",
            "solver output did not assign every source exactly once",
            solver=solver_receipt,
        )

    slot_assignment: List[Dict[str, Any]] = []
    event_counts: Counter[Tuple[str, str]] = Counter()
    for (slot, source_id), column in sorted(y_index.items()):
        if values[column] <= 0.5:
            continue
        slot_assignment.append({
            "island_id": slot[0],
            "bar_start": slot[1],
            "layer_index": slot[2],
            "source_id": source_id,
        })
        event_counts[(slot[0], source_id)] += 1
    if len(slot_assignment) != total_slots:
        return _failure(
            "solver_result_invariant",
            "solver output did not fill every observed slot",
            solver=solver_receipt,
        )
    if any(count > max_events for count in event_counts.values()):
        return _failure(
            "solver_result_invariant",
            "solver output exceeded the declared per-source event cap",
            solver=solver_receipt,
        )

    parent_identity = str(
        body.get("fixture_sha256") or body.get("fixture_id") or ""
    )
    for island in islands:
        island["source_include_ids"] = partition[str(island["island_id"])]
    body["islands"] = islands
    body.pop("fixture_id", None)
    body.pop("fixture_sha256", None)
    qualification = {
        "version": SLOT_QUALIFICATION_VERSION,
        "parent_fixture_identity": parent_identity,
        "census_campaign_sha256": str(
            census_campaign.get("campaign_sha256")
            or semantic_sha256({
                key: value
                for key, value in census_campaign.items()
                if key != "campaign_sha256"
            })
        ),
        "census_identities": [
            str(by_census_id[island_id].get("slot_census_sha256") or "")
            for island_id in candidate_ids
        ],
        "source_universe_sha256": semantic_sha256(sources),
        "source_count": len(sources),
        "slot_count": total_slots,
        "max_source_events": max_events,
        "moved_source_count": sum(
            1
            for source_id in sources
            if source_id not in partition[original_partition[source_id]]
        ),
        "slot_assignment_sha256": semantic_sha256(slot_assignment),
        "solver": solver_receipt,
        "scope": "one_observed_skeleton_round_replan_required",
        "impossibility_claimed": False,
    }
    body["fixture_slot_qualification"] = qualification

    from earcrate.plan.fixture_diversity import fixture_projection

    projection = fixture_projection(body)
    new_identity = str(projection["fixture_identity"])
    body["fixture_id"] = f"season002-slot-{new_identity[:12]}"
    body["fixture_sha256"] = new_identity
    return {
        "kind": "earcrate_fixture_slot_qualification_receipt",
        "version": SLOT_QUALIFICATION_VERSION,
        "complete": True,
        "impossibility_claimed": False,
        "parent_fixture_identity": parent_identity,
        "qualified_fixture_identity": new_identity,
        "moved_source_count": qualification["moved_source_count"],
        "solver": solver_receipt,
        "candidate": body,
        "slot_assignment": slot_assignment,
    }


def install_fixture_slot_census(core_class: Any) -> Any:
    """Attach a complete raw-skeleton census to exact-pool planning refusals."""
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
                    deficiency["fixture_slot_census_campaign"] = (
                        build_fixture_slot_census_campaign(
                            self, dict(params or {})
                        )
                    )
                except Exception as census_error:
                    deficiency["fixture_slot_census_failure"] = {
                        "exception_type": type(census_error).__name__,
                        "error": str(census_error),
                        "impossibility_claimed": False,
                        "private_acceptance": INDETERMINATE_ACTION,
                    }
            raise

    wrapped.__name__ = getattr(original, "__name__", "propose_island_set")
    wrapped.__doc__ = getattr(original, "__doc__", None)
    wrapped.__wrapped__ = original
    core_class.propose_island_set = wrapped
    core_class._fixture_slot_census_installed = True
    return core_class


__all__ = [
    "DEFAULT_MAX_SOURCE_EVENTS",
    "FixtureSlotQualificationError",
    "INDETERMINATE_ACTION",
    "SLOT_CENSUS_VERSION",
    "SLOT_QUALIFICATION_VERSION",
    "build_exact_pool_slot_census",
    "build_fixture_slot_census_campaign",
    "canonical_json",
    "install_fixture_slot_census",
    "qualify_fixture_candidate",
    "semantic_sha256",
]
