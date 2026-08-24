"""Public-safe slot census for exact-deck fixture qualification."""
from __future__ import annotations

from collections import Counter
import contextlib
import contextvars
import copy
import functools
import hashlib
import json
from typing import Any, Dict, Mapping, MutableMapping, Sequence

VERSION = "earcrate_fixture_slot_qualification_v1"
_CURRENT_CANDIDATE_BINDING: contextvars.ContextVar[Mapping[str, str] | None] = (
    contextvars.ContextVar("earcrate_fixture_candidate_binding", default=None)
)


class FixtureSlotQualificationError(ValueError):
    """A slot census or qualification input cannot be bound honestly."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def role_family(role: str) -> str:
    value = str(role or "full")
    if value in {"foreground", "vocal"}:
        return "foreground"
    if value == "bass":
        return "bass"
    if value in {"floor", "drum_anchor", "harmony", "full"}:
        return "floor"
    if value in {"spark", "texture", "fx"}:
        return "spark"
    return value


def slot_census_from_arrangement(
    arrangement: Mapping[str, Any],
    *,
    island_id: str = "",
    candidate_fixture_sha256: str = "",
    source_pool_sha256: str = "",
) -> Dict[str, Any]:
    """Project only musical slot positions and role families from one skeleton."""
    sections = list(arrangement.get("sections") or [])
    if not all(isinstance(row, Mapping) for row in sections):
        raise FixtureSlotQualificationError("arrangement sections must be mappings")
    indexed = sorted(
        enumerate(sections),
        key=lambda item: (
            float(item[1].get("start_s") or 0.0),
            int(item[1].get("bar_start") or 0),
            item[0],
        ),
    )
    slots: list[Dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for musical_index, (declaration_index, section) in enumerate(indexed):
        layers = list(section.get("layers") or [])
        if not all(isinstance(row, Mapping) for row in layers):
            raise FixtureSlotQualificationError(
                f"section {declaration_index} layers must be mappings"
            )
        bar = int(section.get("bar_start") or 0)
        for layer_index, layer in enumerate(layers):
            role = str(layer.get("role") or layer.get("render_role") or "full")
            family = role_family(role)
            counts[family] += 1
            slots.append(
                {
                    "slot_key": f"{bar}:{layer_index}",
                    "bar_start": bar,
                    "section_musical_index": musical_index,
                    "layer_index": layer_index,
                    "section_type": str(
                        section.get("type") or section.get("section_type") or ""
                    ),
                    "role": role,
                    "role_family": family,
                }
            )
    params = arrangement.get("params") or {}
    body: Dict[str, Any] = {
        "version": VERSION,
        "island_id": str(island_id or arrangement.get("island_id") or ""),
        "candidate_fixture_sha256": str(candidate_fixture_sha256),
        "source_pool_sha256": str(source_pool_sha256),
        "exact_target_bpm": float(
            arrangement.get("bpm") or params.get("exact_target_bpm") or 0.0
        ),
        "exact_target_key": int(
            arrangement.get("target_key")
            if arrangement.get("target_key") is not None
            else params.get("exact_target_key") or 0
        )
        % 12,
        "composer_law": copy.deepcopy(dict(arrangement.get("dj_compiler") or {})),
        "slot_count": len(slots),
        "role_family_counts": {key: counts[key] for key in sorted(counts)},
        "slots": slots,
        "path_semantics": "no_paths_or_media_identity_in_slot_census",
    }
    body["slot_census_sha256"] = _sha(body)
    return body


def _refresh_census_digest(census: MutableMapping[str, Any]) -> None:
    census.pop("slot_census_sha256", None)
    census["slot_census_sha256"] = _sha(census)


def _active_binding(params: Mapping[str, Any]) -> Dict[str, str]:
    inherited = dict(_CURRENT_CANDIDATE_BINDING.get() or {})
    return {
        "candidate_fixture_sha256": str(
            params.get("fixture_sha256")
            or inherited.get("candidate_fixture_sha256")
            or ""
        ),
        "source_pool_sha256": str(
            params.get("fixture_source_pool_sha256")
            or params.get("source_pool_sha256")
            or inherited.get("source_pool_sha256")
            or ""
        ),
    }


def attach_slot_census_to_error(
    error: Exception,
    arrangement: Mapping[str, Any],
    params: Mapping[str, Any],
) -> Exception:
    """Attach evidence to a structured refusal without changing its classification."""
    deficiency = getattr(error, "deficiency", None)
    if isinstance(deficiency, MutableMapping) and "slot_census" not in deficiency:
        binding = _active_binding(params)
        deficiency["slot_census"] = slot_census_from_arrangement(
            arrangement,
            island_id=str(params.get("island_id") or arrangement.get("island_id") or ""),
            candidate_fixture_sha256=binding["candidate_fixture_sha256"],
            source_pool_sha256=binding["source_pool_sha256"],
        )
        deficiency["slot_census_capture"] = {
            "ok": True,
            "classification_preserved": True,
        }
    return error


def _install_candidate_binding() -> None:
    """Bind direct fixture provenance around planning without moving arrangement bytes."""
    import earcrate.plan as plan_package
    import earcrate.plan.islands as islands

    if getattr(islands, "_slot_candidate_binding_installed", False):
        return
    original_plan = islands.plan_island_set

    @functools.wraps(original_plan)
    def bound_plan(core: Any, params: Mapping[str, Any]):
        fixture = str(params.get("fixture_sha256") or "")
        pool = str(params.get("source_pool_sha256") or "")
        token = _CURRENT_CANDIDATE_BINDING.set(
            {
                "candidate_fixture_sha256": fixture,
                "source_pool_sha256": pool,
            }
        )
        try:
            result = original_plan(core, params)
            if not fixture:
                return result
            bound = dict(result)
            bound["candidate_fixture_sha256"] = fixture
            bound["candidate_source_pool_sha256"] = pool
            qualification = params.get("slot_qualification")
            if isinstance(qualification, Mapping):
                bound["slot_qualification_sha256"] = _sha(dict(qualification))
            return bound
        finally:
            _CURRENT_CANDIDATE_BINDING.reset(token)

    islands._pre_slot_binding_plan_island_set = original_plan
    islands.plan_island_set = bound_plan
    plan_package.plan_island_set = bound_plan
    islands._slot_candidate_binding_installed = True


def install_slot_census_evidence() -> None:
    """Install candidate binding and enrich only exact-pool refusals."""
    import earcrate.plan.source_rotation as rotation

    _install_candidate_binding()
    if getattr(rotation, "_slot_census_evidence_installed", False):
        return
    original = rotation.rebalance_exact_pool_sources

    @functools.wraps(original)
    def wrapped(core: Any, arrangement: Mapping[str, Any], pool: Sequence[Mapping[str, Any]], params: Mapping[str, Any], seed: int):
        try:
            return original(core, arrangement, pool, params, seed)
        except Exception as exc:
            try:
                attach_slot_census_to_error(exc, arrangement, params)
            except Exception as capture_error:
                deficiency = getattr(exc, "deficiency", None)
                if isinstance(deficiency, MutableMapping):
                    deficiency["slot_census_capture"] = {
                        "ok": False,
                        "classification_preserved": True,
                        "error_class": type(capture_error).__name__,
                    }
            raise

    rotation._slot_census_original_rebalance = original
    rotation.rebalance_exact_pool_sources = wrapped
    rotation._slot_census_evidence_installed = True


def _base_params(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    transform = candidate.get("transform_policy") or {}
    out: Dict[str, Any] = {
        "taste_profile": str(candidate["profile"]),
        "persona": str(candidate["persona"]),
        "phrase_playback_law": str(candidate["phrase_playback_law"]),
        "stretch_budget": float(
            transform.get("stretch_budget") or candidate.get("stretch_budget") or 8.0
        ),
        "pitch_shift_budget": int(
            transform.get("pitch_shift_budget")
            or candidate.get("pitch_shift_budget")
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
    ):
        if key in candidate:
            out[key] = copy.deepcopy(candidate[key])
    return out


def _raw_compose(
    core: Any,
    pool: Sequence[Mapping[str, Any]],
    row: Mapping[str, Any],
    base: Mapping[str, Any],
    seed: int,
) -> Dict[str, Any]:
    from earcrate.plan.islands import ExactDeckProxy

    raw = getattr(core, "_ordinary_compose_taste_arrangement", None) or getattr(
        type(core), "_ordinary_compose_taste_arrangement", None
    )
    if raw is None:
        raise FixtureSlotQualificationError("ordinary composer handle is unavailable")
    bpm = float(row["target_bpm"])
    key = int(row["target_key"]) % 12
    params = dict(base)
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
    if params.get("phrase_playback_law") == "proof001_phrase_law":
        params["phrase_playback"] = True
    persona = str(params.get("persona") or "")
    if persona:
        with contextlib.suppress(Exception):
            import earcrate.app as app_module

            contract = dict(getattr(app_module, "TASTE_PROFILES", {}).get(persona) or {})
            if contract:
                params["reuse_policy_override"] = contract
    compose = getattr(raw, "__func__", raw)
    result = compose(ExactDeckProxy(core, bpm, key), list(pool), params, int(seed))
    if (
        abs(float(result.get("bpm") or 0.0) - bpm) > 1e-9
        or result.get("target_key") is None
        or int(result["target_key"]) % 12 != key
    ):
        raise FixtureSlotQualificationError(
            f"slot probe did not retain exact deck for {row['island_id']}"
        )
    return dict(result)


def probe_candidate_slot_census(
    core: Any, candidate: Mapping[str, Any]
) -> Dict[str, Any]:
    """Run the ordinary composer as a diagnostic and never enter publication."""
    from earcrate.plan.fixture_diversity import fixture_projection
    from earcrate.plan.islands import (
        allocate_phrase_aligned_islands,
        atom_identity,
        missing_roles,
        source_identity,
        source_pool_identity,
        validate_request,
    )

    validate_request(candidate)
    projection = fixture_projection(candidate)
    fixture = str(candidate.get("fixture_sha256") or projection["fixture_identity"])
    if fixture != projection["fixture_identity"]:
        raise FixtureSlotQualificationError(
            "candidate fixture_sha256 does not match projection"
        )
    profile = str(candidate["profile"])
    excludes = {str(value) for value in candidate.get("source_exclude_ids") or []}
    pool = list(core.approved_atom_pool(profile))
    pool_sha = source_pool_identity(pool, excludes)
    if pool_sha != str(candidate["source_pool_sha256"]):
        raise FixtureSlotQualificationError("source pool identity mismatch")
    by_source: Dict[str, list[Dict[str, Any]]] = {}
    for item in pool:
        source = source_identity(item)
        if source not in excludes and atom_identity(item) not in excludes:
            by_source.setdefault(source, []).append(dict(item))
    rows, transitions, net_duration = allocate_phrase_aligned_islands(
        [dict(row) for row in candidate.get("islands") or []],
        float(candidate.get("duration_s") or 0.0),
        int(candidate.get("phrase_bars") or 4),
    )
    base = _base_params(candidate)
    seed = int(candidate.get("seed") or 0)
    censuses: list[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        source_ids = sorted({str(value) for value in row.get("source_include_ids") or []})
        unknown = [source for source in source_ids if source not in by_source]
        if unknown:
            raise FixtureSlotQualificationError(f"unavailable source {unknown[0]}")
        feasible, diagnostics = core.taste_feasible_pool(
            [item for source in source_ids for item in by_source[source]],
            float(row["target_bpm"]),
            int(row["target_key"]),
            base,
        )
        missing = missing_roles(feasible, list(row.get("required_roles") or []))
        if not feasible or missing:
            raise FixtureSlotQualificationError(
                f"island {row['island_id']} cannot be probed honestly"
            )
        arrangement = _raw_compose(core, feasible, row, base, seed + index)
        census = slot_census_from_arrangement(
            arrangement,
            island_id=str(row["island_id"]),
            candidate_fixture_sha256=fixture,
            source_pool_sha256=pool_sha,
        )
        census.update(
            {
                "deck_id": str(row.get("deck_id") or ""),
                "allocated_duration_s": float(row["allocated_duration_s"]),
                "candidate_source_count": len(source_ids),
                "diagnostics_have": copy.deepcopy(
                    dict((diagnostics or {}).get("have") or {})
                ),
            }
        )
        _refresh_census_digest(census)
        censuses.append(census)
    body: Dict[str, Any] = {
        "kind": "earcrate_fixture_slot_census_receipt",
        "version": VERSION,
        "candidate_fixture_sha256": fixture,
        "source_pool_sha256": pool_sha,
        "seed": seed,
        "net_duration_s": float(net_duration),
        "transition_count": len(transitions),
        "islands": censuses,
        "publication_authority": False,
        "diagnostic_only": True,
        "path_semantics": "operational_paths_are_not_slot_or_fixture_identity",
    }
    body["slot_census_family_sha256"] = _sha(body)
    return body
