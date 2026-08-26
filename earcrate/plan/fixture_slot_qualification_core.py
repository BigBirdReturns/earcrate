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

SLOT_CENSUS_VERSION = "earcrate_exact_pool_slot_census_v2"
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
        rows.append(
            {
                "source_id": source_id,
                "atom_id": atom_id,
                "ear_role": str(item.get("ear_role") or ""),
                "render_role": str(item.get("render_role") or item.get("role") or ""),
                "bpm_hex": float(item.get("bpm") or 0.0).hex(),
                "key_root": int(item.get("key_root") or 0) % 12,
                "bars": int(item.get("bars") or 0),
            }
        )
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
    """Project the exact assignment graph without publishing an assignment.

    The pool may be wider than the candidate's current island allowlist. This is
    intentional: a repartitioner needs to know which sources from the immutable
    campaign universe could occupy the observed skeleton if moved here.
    """
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
    source_slots: Dict[str, List[List[int]]] = {source_id: [] for source_id in ordered_sources}
    sections = list(arrangement.get("sections") or [])
    for slot_key, section_index, layer_index, layer in slots:
        slot_role = str(layer.get("role") or "full")
        family = rotation._role_family(slot_role)
        role_counts[family] += 1
        compatible_sources = sorted(edges.get(slot_key, {}))
        for source_id in compatible_sources:
            source_slots[source_id].append([int(slot_key[0]), int(slot_key[1])])
        section = sections[section_index] if section_index < len(sections) else {}
        slot_rows.append(
            {
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
            }
        )

    source_rows: List[Dict[str, Any]] = []
    for source_id in ordered_sources:
        natural_families = sorted(
            {
                rotation._role_family(rotation._natural_role(item))
                for item in pool_by_source[source_id]
            }
        )
        source_rows.append(
            {
                "source_id": source_id,
                "natural_role_families": natural_families,
                "reachable_slots": source_slots[source_id],
                "reachable_slot_count": len(source_slots[source_id]),
                "reach_diagnostics": {
                    key: int(value) for key, value in sorted(reach[source_id].items())
                },
            }
        )

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
        "island_id": str(island_id or params.get("island_id") or arrangement.get("island_id") or ""),
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
            "taste_profile": str(params.get("taste_profile") or params.get("profile") or ""),
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

            contract = dict(getattr(app_module, "TASTE_PROFILES", {}).get(persona) or {})
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


__all__ = [
    "DEFAULT_MAX_SOURCE_EVENTS", "EPS", "FixtureSlotQualificationError",
    "INDETERMINATE_ACTION", "SLOT_CENSUS_VERSION", "SLOT_QUALIFICATION_VERSION",
    "_base_params", "_candidate_body", "_failure", "_global_hall_witness",
    "_policy_identity", "_raw_island_arrangement", "build_exact_pool_slot_census",
    "canonical_json", "semantic_sha256",
]
