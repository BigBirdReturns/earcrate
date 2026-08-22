"""Deterministic multi-island planning over EarCrate's existing composer.

The planner consumes an explicit, content-bound schedule. Each island carries
an exact BPM/key deck and an exact source allowlist. The existing TasteSpec
composer remains the only source selector; the proxy below replaces only its
hint-based deck choice. A single global source ledger survives every boundary.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

ISLAND_SET_KIND = "earcrate_island_set"
ISLAND_SET_SCHEMA_VERSION = 1
BEATS_PER_BAR = 4
DEFAULT_PHRASE_BARS = 4
EPS = 1e-9


class IslandPlanError(RuntimeError):
    """The schedule cannot be lowered without weakening a declared law."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def semantic_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def float_identity(value: Any) -> str:
    return float(value).hex()


def source_identity(item: Mapping[str, Any]) -> str:
    explicit = item.get("source_track_key") or item.get("source_id")
    if explicit not in (None, ""):
        return str(explicit)
    try:
        from earcrate.deck.dsp import track_identity
        return str(track_identity(dict(item)))
    except Exception:
        artist = str(item.get("artist") or "").strip().lower()
        title = str(item.get("title") or "").strip().lower()
        if artist or title:
            return f"{artist}::{title}"
        return hashlib.sha1(str(item.get("path") or "").encode("utf-8", "replace")).hexdigest()[:12]


def atom_identity(item: Mapping[str, Any]) -> str:
    return str(item.get("atom_id") or item.get("id") or item.get("loop_id") or "")


def source_pool_projection(pool: Sequence[Mapping[str, Any]], excluded_ids: Iterable[str] = ()) -> List[Dict[str, Any]]:
    excluded = {str(x) for x in excluded_ids}
    rows: List[Dict[str, Any]] = []
    for item in pool:
        source_id = source_identity(item)
        atom_id = atom_identity(item)
        if source_id in excluded or atom_id in excluded:
            continue
        rows.append({
            "source_id": source_id,
            "atom_id": atom_id,
            "loop_id": str(item.get("id") or item.get("loop_id") or ""),
            "source_audio_sha256": str(item.get("source_audio_sha256") or item.get("audio_sha256") or ""),
            "ear_role": str(item.get("ear_role") or ""),
            "render_role": str(item.get("render_role") or item.get("role") or ""),
            "bpm": float_identity(item.get("bpm") or 0.0),
            "key_root": int(item.get("key_root") or 0) % 12,
            "bars": int(item.get("bars") or 0),
            "start_s": float_identity(item.get("start_s") or 0.0),
            "end_s": float_identity(item.get("end_s") or 0.0),
        })
    rows.sort(key=lambda row: (row["source_id"], row["atom_id"], row["loop_id"]))
    return rows


def source_pool_identity(pool: Sequence[Mapping[str, Any]], excluded_ids: Iterable[str] = ()) -> str:
    return semantic_sha256(source_pool_projection(pool, excluded_ids))


def role_tokens(item: Mapping[str, Any]) -> set[str]:
    ear = str(item.get("ear_role") or "")
    role = str(item.get("render_role") or item.get("role") or "")
    out = {ear, role}
    if ear in {"VOX_HOOK", "VOX_VERSE", "VOX_SHOUT", "RIFF_ID"} or role == "vocal":
        out.add("foreground")
    if ear in {"DRUM_BREAK", "BED_CHORD", "RIFF_ID", "TEXTURE"} or role in {"drum_anchor", "harmony", "full"}:
        out.add("floor")
    if ear == "BASS_RIFF" or role == "bass":
        out.add("bass")
    if ear in {"PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL", "TEXTURE", "VOX_SHOUT"} or role in {"texture", "fx"}:
        out.add("spark")
    return out


def missing_roles(pool: Sequence[Mapping[str, Any]], required: Sequence[str]) -> List[str]:
    have: set[str] = set()
    for item in pool:
        have.update(role_tokens(item))
    return [str(role) for role in required if str(role) not in have]


def phrase_seconds(bpm: float, phrase_bars: int = DEFAULT_PHRASE_BARS) -> float:
    if not math.isfinite(bpm) or bpm <= 0.0:
        raise IslandPlanError(f"invalid island BPM: {bpm!r}")
    if phrase_bars <= 0:
        raise IslandPlanError("phrase_bars must be positive")
    return phrase_bars * BEATS_PER_BAR * 60.0 / bpm


def transition_seconds(left_bpm: float, right_bpm: float) -> float:
    return min(BEATS_PER_BAR * 60.0 / left_bpm, BEATS_PER_BAR * 60.0 / right_bpm)


def allocate_phrase_aligned_islands(
    islands: Sequence[Mapping[str, Any]],
    required_duration_s: float,
    phrase_bars: int = DEFAULT_PHRASE_BARS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]:
    if not islands:
        raise IslandPlanError("at least one island is required")
    if not math.isfinite(required_duration_s) or required_duration_s <= 0.0:
        raise IslandPlanError("duration_s must be positive")

    rows: List[Dict[str, Any]] = []
    remaining = float(required_duration_s)
    for raw in islands:
        bpm = float(raw.get("target_bpm") or 0.0)
        capacity = float(raw.get("capacity_s") or 0.0)
        one_phrase = phrase_seconds(bpm, phrase_bars)
        max_phrases = int(math.floor((capacity + EPS) / one_phrase))
        if max_phrases <= 0:
            raise IslandPlanError(f"island {raw.get('island_id')!r} has no legal phrase inside capacity")
        needed = max(1, int(math.ceil(max(0.0, remaining) / one_phrase - EPS)))
        used = min(max_phrases, needed)
        rows.append({
            **dict(raw),
            "phrase_bars": int(phrase_bars),
            "phrase_seconds": one_phrase,
            "max_phrases": max_phrases,
            "used_phrases": used,
            "allocated_duration_s": used * one_phrase,
        })
        remaining -= used * one_phrase
        if remaining <= EPS:
            break
    if remaining > EPS:
        capacity = sum(float(row["allocated_duration_s"]) for row in rows)
        raise IslandPlanError(f"insufficient phrase-aligned union capacity: {capacity:.6f}s for {required_duration_s:.6f}s")

    def place() -> Tuple[List[Dict[str, Any]], float]:
        transitions: List[Dict[str, Any]] = []
        cursor = 0.0
        for index, row in enumerate(rows):
            if index:
                previous = rows[index - 1]
                overlap = transition_seconds(float(previous["target_bpm"]), float(row["target_bpm"]))
                cursor -= overlap
                transitions.append({
                    "from_island": str(previous["island_id"]),
                    "to_island": str(row["island_id"]),
                    "at_phrase_boundary": True,
                    "technique": "equal_power",
                    "curve": "equal_power",
                    "duration_s": overlap,
                    "start_s": cursor,
                    "end_s": cursor + overlap,
                })
            row["start_s"] = cursor
            cursor += float(row["allocated_duration_s"])
            row["end_s"] = cursor
        return transitions, cursor

    transitions, net_duration = place()
    while net_duration + EPS < required_duration_s:
        for row in reversed(rows):
            if int(row["used_phrases"]) < int(row["max_phrases"]):
                row["used_phrases"] = int(row["used_phrases"]) + 1
                row["allocated_duration_s"] = float(row["used_phrases"]) * float(row["phrase_seconds"])
                transitions, net_duration = place()
                break
        else:
            raise IslandPlanError(f"transition overlaps reduce net capacity below demand: {net_duration:.6f}s")
    return rows, transitions, net_duration


class ExactDeckProxy:
    """Delegate to a real core while replacing only hint-based deck selection."""

    def __init__(self, core: Any, target_bpm: float, target_key: int):
        self._core = core
        self.target_bpm = float(target_bpm)
        self.target_key = int(target_key) % 12

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)

    def choose_taste_deck(self, pool: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        feasible, diagnostics = self._core.taste_feasible_pool(pool, self.target_bpm, self.target_key, params)
        diagnostics = dict(diagnostics or {})
        diagnostics.update({"exact_deck": True, "render_bpm": self.target_bpm, "target_key": self.target_key})
        if not feasible:
            raise IslandPlanError(f"exact deck {self.target_bpm:.9f} BPM/key {self.target_key} retains no material")
        return {
            "pool": feasible,
            "render_bpm": self.target_bpm,
            "target_key": self.target_key,
            "diagnostics": diagnostics,
            "lattice": {"best": diagnostics, "lattice": [diagnostics]},
        }


def arrangement_sha(value: Mapping[str, Any]) -> str:
    try:
        from earcrate.core.deps import arrangement_sha as existing
        return str(existing(dict(value)))
    except Exception:
        return semantic_sha256(value)


def selected_source_usage(arrangement: Mapping[str, Any], bpm: float, global_start_s: float) -> Dict[str, Dict[str, float]]:
    usage: Dict[str, Dict[str, float]] = {}
    for section in arrangement.get("sections") or []:
        section_start = float(section.get("bar_start") or 0) * BEATS_PER_BAR * 60.0 / bpm
        section_bars = int(section.get("bars") or 0)
        for layer in section.get("layers") or []:
            source_id = str(layer.get("source_track_key") or layer.get("source_id") or layer.get("loop_id") or "")
            if not source_id:
                continue
            offset = int(layer.get("bar_offset") or 0)
            length = int(layer.get("bar_len") or section_bars)
            first = global_start_s + section_start + offset * BEATS_PER_BAR * 60.0 / bpm
            last = first + length * BEATS_PER_BAR * 60.0 / bpm
            row = usage.setdefault(source_id, {"first_use_s": first, "last_use_s": last})
            row["first_use_s"] = min(row["first_use_s"], first)
            row["last_use_s"] = max(row["last_use_s"], last)
    return usage


def validate_request(params: Mapping[str, Any]) -> None:
    for name in ("profile", "source_pool_sha256", "persona", "phrase_playback_law"):
        if not str(params.get(name) or ""):
            raise IslandPlanError(f"missing required field: {name}")
    if not bool((params.get("transform_policy") or {}).get("unchanged")):
        raise IslandPlanError("transform policy must be explicitly unchanged")
    if not bool((params.get("turnover_policy") or {}).get("unchanged")):
        raise IslandPlanError("turnover policy must be explicitly unchanged")
    transition = dict(params.get("transition") or {})
    if transition.get("technique") != "equal_power" or not bool(transition.get("phrase_boundary_required")):
        raise IslandPlanError("island joins require phrase-boundary equal_power transitions")


def compose_island(core: Any, pool: List[Dict[str, Any]], row: Mapping[str, Any], base_params: Mapping[str, Any], seed: int) -> Dict[str, Any]:
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
            contract = dict(getattr(app_module, "TASTE_PROFILES", {}).get(persona) or {})
            if contract:
                params["reuse_policy_override"] = contract
    proxy = ExactDeckProxy(core, bpm, key)
    compose = getattr(core.compose_taste_arrangement, "__func__", core.compose_taste_arrangement)
    result = compose(proxy, pool, params, int(seed))
    if abs(float(result.get("bpm") or 0.0) - bpm) > 1e-9:
        raise IslandPlanError(f"island {row['island_id']} did not retain exact BPM")
    actual_key = result.get("target_key")
    if actual_key is None or int(actual_key) % 12 != key:
        raise IslandPlanError(f"island {row['island_id']} did not retain exact key")
    result["island_id"] = str(row["island_id"])
    result["island_authority"] = {
        "target_bpm": bpm,
        "target_bpm_hex": float_identity(bpm),
        "target_key": key,
        "source_include_ids": sorted(str(x) for x in row.get("source_include_ids") or []),
        "capacity_s": float(row["capacity_s"]),
        "allocated_duration_s": float(row["allocated_duration_s"]),
        "phrase_bars": int(row["phrase_bars"]),
    }
    return result


def plan_island_set(core: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile the explicit schedule without writing DB rows or audio."""
    validate_request(params)
    profile = str(params["profile"])
    seed = int(params.get("seed") or 0)
    duration_s = float(params.get("duration_s") or 0.0)
    excludes = {str(x) for x in params.get("source_exclude_ids") or []}
    requested_islands = [dict(item) for item in params.get("islands") or []]
    pool = list(core.approved_atom_pool(profile))
    pool_sha = source_pool_identity(pool, excludes)
    if pool_sha != str(params["source_pool_sha256"]):
        raise IslandPlanError(f"source pool identity mismatch: current {pool_sha}, requested {params['source_pool_sha256']}")

    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in pool:
        source_id = source_identity(item)
        if source_id in excludes or atom_identity(item) in excludes:
            continue
        by_source.setdefault(source_id, []).append(dict(item))

    seen: set[str] = set()
    for row in requested_islands:
        island_id = str(row.get("island_id") or "")
        if not island_id:
            raise IslandPlanError("every island needs island_id")
        row["target_bpm"] = float(row.get("target_bpm") or 0.0)
        row["target_key"] = int(row.get("target_key") or 0) % 12
        row["capacity_s"] = float(row.get("capacity_s") or 0.0)
        includes = sorted({str(x) for x in row.get("source_include_ids") or []})
        if not includes:
            raise IslandPlanError(f"island {island_id} has an empty source allowlist")
        overlap = seen.intersection(includes)
        if overlap:
            raise IslandPlanError(f"source appears in multiple islands: {sorted(overlap)[0]}")
        forbidden = excludes.intersection(includes)
        if forbidden:
            raise IslandPlanError(f"island {island_id} includes excluded source: {sorted(forbidden)[0]}")
        unknown = [source_id for source_id in includes if source_id not in by_source]
        if unknown:
            raise IslandPlanError(f"island {island_id} names unavailable source: {unknown[0]}")
        row["source_include_ids"] = includes
        seen.update(includes)

    allocated, transitions, net_duration = allocate_phrase_aligned_islands(requested_islands, duration_s)
    base_params: Dict[str, Any] = {
        "taste_profile": profile,
        "persona": str(params["persona"]),
        "phrase_playback_law": str(params["phrase_playback_law"]),
        "stretch_budget": float((params.get("transform_policy") or {}).get("stretch_budget") or params.get("stretch_budget") or 8.0),
        "pitch_shift_budget": int((params.get("transform_policy") or {}).get("pitch_shift_budget") or params.get("pitch_shift_budget") or 2),
        "quality_mode": "stable_deck",
        "post_render_gate": True,
        "mix_mode": "tastespec_graph",
    }
    for key in ("recurrence_scores", "foreground_rank_recurrence", "reuse_policy_override", "max_aux_decks"):
        if key in params:
            base_params[key] = params[key]

    used_sources: set[str] = set()
    island_outputs: List[Dict[str, Any]] = []
    ledger: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    for index, row in enumerate(allocated):
        allowed = [item for source_id in row["source_include_ids"] for item in by_source[source_id]]
        feasible, diagnostics = core.taste_feasible_pool(allowed, float(row["target_bpm"]), int(row["target_key"]), base_params)
        if not feasible:
            raise IslandPlanError(f"island {row['island_id']} has no transform-safe material at its exact deck")
        missing = missing_roles(feasible, list(row.get("required_roles") or []))
        if missing:
            raise IslandPlanError(f"island {row['island_id']} is not role-complete: missing {', '.join(missing)}")
        arrangement = compose_island(core, feasible, row, base_params, seed + index)
        preflight = core.arrangement_preflight_gate(arrangement) if hasattr(core, "arrangement_preflight_gate") else {"passed": True}
        taste_gate = core.taste_arrangement_gate(arrangement) if hasattr(core, "taste_arrangement_gate") else {"passed": True}
        if not preflight.get("passed") or not taste_gate.get("passed"):
            failures = list(preflight.get("failures") or []) + list(taste_gate.get("failures") or [])
            raise IslandPlanError(f"island {row['island_id']} failed existing gates: {'; '.join(failures)}")

        usage = selected_source_usage(arrangement, float(row["target_bpm"]), float(row["start_s"]))
        outside = sorted(set(usage) - set(row["source_include_ids"]))
        if outside:
            raise IslandPlanError(f"island {row['island_id']} selected source outside its allowlist: {outside[0]}")
        repeated = sorted(used_sources.intersection(usage))
        if repeated:
            raise IslandPlanError(f"global source reuse across islands is forbidden: {repeated[0]}")
        used_sources.update(usage)
        for source_id, times in sorted(usage.items()):
            ledger.append({"source_id": source_id, "island_id": str(row["island_id"]), **times})
        for section in arrangement.get("sections") or []:
            copy_section = copy.deepcopy(section)
            local_start = float(copy_section.get("bar_start") or 0) * BEATS_PER_BAR * 60.0 / float(row["target_bpm"])
            local_duration = int(copy_section.get("bars") or 0) * BEATS_PER_BAR * 60.0 / float(row["target_bpm"])
            copy_section.update({
                "island_id": str(row["island_id"]),
                "island_bpm": float(row["target_bpm"]),
                "island_key": int(row["target_key"]),
                "start_s": float(row["start_s"]) + local_start,
                "end_s": float(row["start_s"]) + local_start + local_duration,
            })
            sections.append(copy_section)
        island_outputs.append({
            "island_id": str(row["island_id"]),
            "start_s": float(row["start_s"]),
            "end_s": float(row["end_s"]),
            "target_bpm": float(row["target_bpm"]),
            "target_bpm_hex": float_identity(row["target_bpm"]),
            "target_key": int(row["target_key"]),
            "source_ids": sorted(usage),
            "source_allowlist": list(row["source_include_ids"]),
            "capacity_s": float(row["capacity_s"]),
            "allocated_duration_s": float(row["allocated_duration_s"]),
            "required_roles": list(row.get("required_roles") or []),
            "diagnostics": diagnostics,
            "arrangement": arrangement,
            "arrangement_sha256": arrangement_sha(arrangement),
        })

    whole: Dict[str, Any] = {
        "kind": ISLAND_SET_KIND,
        "schema_version": ISLAND_SET_SCHEMA_VERSION,
        "seed": seed,
        "bpm": float(island_outputs[0]["target_bpm"]),
        "target_key": int(island_outputs[0]["target_key"]),
        "duration_s": net_duration,
        "requested_duration_s": duration_s,
        "params": {
            **base_params,
            "profile": profile,
            "source_pool_sha256": pool_sha,
            "source_exclude_ids": sorted(excludes),
            "transform_policy": dict(params.get("transform_policy") or {}),
            "turnover_policy": dict(params.get("turnover_policy") or {}),
            "transition": dict(params.get("transition") or {}),
            "stem_export": True,
        },
        "islands": island_outputs,
        "transitions": transitions,
        "global_source_ledger": ledger,
        "sections": sections,
        "accounting": {
            "selected_sources": len(ledger),
            "unique_sources": len(used_sources),
            "source_reuse": len(ledger) - len(used_sources),
            "island_count": len(island_outputs),
            "all_sources_accounted_once": len(ledger) == len(used_sources),
        },
    }
    whole_sha = arrangement_sha(whole)
    return {
        "ok": True,
        "kind": "earcrate_island_set_proposal",
        "source_pool_sha256": pool_sha,
        "requested_duration_s": duration_s,
        "duration_s": net_duration,
        "island_count": len(island_outputs),
        "islands": [{key: value for key, value in island.items() if key != "arrangement"} for island in island_outputs],
        "transitions": transitions,
        "global_source_ledger": ledger,
        "arrangement": whole,
        "arrangement_sha256": whole_sha,
    }


def persist_proposal(core: Any, params: Mapping[str, Any], result: MutableMapping[str, Any]) -> None:
    from earcrate.core.deps import ENGINE_VERSION, now_utc, safe_name, ulidish
    config = core.ensure_config()
    seed = int(params.get("seed") or 0)
    name = safe_name(str(params.get("name") or "EarCrate Island Set"), "EarCrate Island Set")
    mashup_id = ulidish()
    sha = str(result["arrangement_sha256"])
    destination = config.working_root / "renders" / f"{safe_name(name)}-{ENGINE_VERSION}-{sha[:8]}-{seed}.wav"
    stored_params = dict(params)
    stored_params["kind"] = ISLAND_SET_KIND
    db = core.conn()
    db.execute(
        "INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
        (mashup_id, name, seed, json.dumps(stored_params, ensure_ascii=False), json.dumps(result["arrangement"], ensure_ascii=False), str(destination), now_utc(), ENGINE_VERSION, sha),
    )
    db.commit()
    operation = {"op_id": ulidish(), "type": "render_mashup", "args": {"mashup_id": mashup_id, "dst": str(destination)}, "preconditions": {"dst_absent": True}}
    manifest = core.write_manifest("island_set", seed, f"Render multi-island set '{name}'", [operation])
    result.update({"mashup_id": mashup_id, "manifest": manifest, "dst": str(destination), "engine_version": ENGINE_VERSION})


def propose_island_set(self: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    request = dict(params or {})
    run = self._run_bundle_begin("compile", {"entrypoint": "propose_island_set", "params": request}) if hasattr(self, "_run_bundle_begin") else None
    try:
        result = plan_island_set(self, request)
        if bool(request.get("persist", True)):
            persist_proposal(self, request, result)
        if run:
            self._run_bundle_set_plan(str(run["run_id"]), result["arrangement"], result["arrangement_sha256"])
            result["run_id"] = str(run["run_id"])
            result["run_bundle"] = str(run["path"])
            self._run_bundle_finish(str(run["run_id"]), True, {key: value for key, value in result.items() if key != "arrangement"})
        return result
    except Exception as exc:
        if run:
            self._run_bundle_set_plan(str(run["run_id"]), None, None, str(exc), state="rejected")
            self._run_bundle_finish(str(run["run_id"]), False, {"error": str(exc), "exception_type": type(exc).__name__, "entrypoint": "propose_island_set"})
        raise


def install_island_set(core_class: Any) -> Any:
    if getattr(core_class, "_island_set_installed", False):
        return core_class
    from earcrate.plan.island_render import install_island_render_dispatch
    core_class.propose_island_set = propose_island_set
    install_island_render_dispatch(core_class)
    core_class._island_set_installed = True
    return core_class


__all__ = [
    "ISLAND_SET_KIND",
    "ISLAND_SET_SCHEMA_VERSION",
    "IslandPlanError",
    "allocate_phrase_aligned_islands",
    "install_island_set",
    "plan_island_set",
    "propose_island_set",
    "semantic_sha256",
    "source_pool_identity",
    "source_pool_projection",
]
