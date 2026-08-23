"""Deterministic source rotation for exact-deck island compositions.

The ordinary TasteSpec composer is intentionally unchanged. Exact island plans,
however, carry an explicit source allowlist whose capacity calculation assumes
that the listed sources can actually enter the arrangement. The first private
Proof-005 run exposed two pathologies in the ordinary heuristic under a
restricted pool: some role-valid sources were never selected, and the composer
continued reusing a winning source after the existing arrangement gate's cap.

This module repairs only that exact-pool path. It keeps every section, slot,
role, gain, duration, transition, and deck fixed, then deterministically assigns
compatible transform-safe atoms so every allowlisted source appears and no
source exceeds the declared event cap. If the existing slots cannot satisfy
those laws, planning refuses before audio or database publication.

That first repair is a depth-1 greedy walk, and Jam Season 001 showed it refuses
feasible pools purely on visit order. It therefore no longer gets the last word:
when it refuses, ``exact_pool_assignment`` re-solves the whole assignment
atomically with augmenting paths. When it succeeds, nothing else runs and its
frozen ledger is returned untouched.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import functools
import hashlib
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

EXACT_POOL_ROTATION_VERSION = "exact_pool_rotation_v1"
DEFAULT_MAX_SOURCE_EVENTS = 12


class ExactPoolRotationError(RuntimeError):
    """The exact allowlist cannot be represented by the existing role slots."""


def _source_identity(item: Mapping[str, Any]) -> str:
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


def _atom_identity(item: Mapping[str, Any]) -> str:
    return str(item.get("atom_id") or item.get("id") or item.get("loop_id") or "")


def _loop_identity(item: Mapping[str, Any]) -> str:
    return str(item.get("id") or item.get("loop_id") or "")


def _natural_role(item: Mapping[str, Any]) -> str:
    role = str(item.get("render_role") or item.get("role") or "")
    if role:
        return role
    ear = str(item.get("ear_role") or "")
    if ear in {"VOX_HOOK", "VOX_VERSE", "VOX_SHOUT"}:
        return "vocal"
    if ear == "BASS_RIFF":
        return "bass"
    if ear == "DRUM_BREAK":
        return "drum_anchor"
    if ear in {"BED_CHORD", "RIFF_ID"}:
        return "harmony"
    if ear in {"PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL", "TEXTURE"}:
        return "texture"
    return "full"


def _role_family(role: str) -> str:
    role = str(role or "full")
    if role == "vocal":
        return "foreground"
    if role == "bass":
        return "bass"
    if role in {"drum_anchor", "harmony", "full"}:
        return "floor"
    if role in {"texture", "fx"}:
        return "spark"
    return role


def _role_compatible(slot_role: str, item: Mapping[str, Any]) -> bool:
    natural = _natural_role(item)
    if natural == slot_role:
        return True
    # Preserve the existing musical role family. This permits a full-mix floor
    # atom to occupy a harmony/drum floor slot, but never turns a bass or vocal
    # source into an unrelated role merely to satisfy a counter.
    return _role_family(natural) == _role_family(slot_role)


def _stable_rank(seed: int, *parts: Any) -> int:
    body = "|".join([str(seed), *[str(part) for part in parts]])
    return int(hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(), 16)


def _stable_source_order(source_ids: Iterable[str], seed: int) -> List[str]:
    return sorted({str(source_id) for source_id in source_ids}, key=lambda source_id: (_stable_rank(seed, source_id), source_id))


def _transform_for_slot(
    item: Mapping[str, Any],
    slot_role: str,
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    from earcrate.deck.transform import plan_varispeed_transform

    try:
        source_bpm = float(item.get("bpm") or render_bpm)
        source_key = int(item.get("key_root") or target_key) % 12
        stretch_budget = float(params.get("stretch_budget") or 8.0)
        pitch_budget = int(params.get("pitch_shift_budget") or 2)
        plan = plan_varispeed_transform(
            str(slot_role or "full"),
            source_bpm,
            float(render_bpm),
            source_key,
            int(target_key) % 12,
            stretch_budget,
            pitch_budget,
        )
    except Exception:
        return None
    if plan.get("violation"):
        return None
    return dict(plan)


def _slot_relation(slot_role: str) -> str:
    if slot_role == "vocal":
        return "vocal_over_bed"
    if slot_role == "bass":
        return "bass_over_drums"
    return "spark_into_phrase"


def _current_item(layer: Mapping[str, Any], by_atom: Mapping[str, Mapping[str, Any]], by_loop: Mapping[str, Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    atom_id = str(layer.get("atom_id") or "")
    loop_id = str(layer.get("loop_id") or "")
    return by_atom.get(atom_id) or by_loop.get(loop_id)


def _counterpart_item(
    section: Mapping[str, Any],
    layer_index: int,
    by_atom: Mapping[str, Mapping[str, Any]],
    by_loop: Mapping[str, Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    layers = list(section.get("layers") or [])
    slot_role = str((layers[layer_index] if layer_index < len(layers) else {}).get("role") or "full")
    preferred: Sequence[str]
    if slot_role == "vocal":
        preferred = ("drum_anchor", "harmony", "full", "bass")
    elif slot_role == "bass":
        preferred = ("drum_anchor", "harmony", "full")
    else:
        preferred = ("drum_anchor", "harmony", "full", "vocal", "bass")
    for wanted in preferred:
        for other_index, layer in enumerate(layers):
            if other_index == layer_index or str(layer.get("role") or "full") != wanted:
                continue
            item = _current_item(layer, by_atom, by_loop)
            if item is not None:
                return item
    return None


def _candidate_score(
    core: Any,
    candidate: Mapping[str, Any],
    section: Mapping[str, Any],
    layer_index: int,
    slot_role: str,
    transform: Mapping[str, Any],
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    by_atom: Mapping[str, Mapping[str, Any]],
    by_loop: Mapping[str, Mapping[str, Any]],
    seed: int,
) -> Optional[Tuple[float, float, float, int]]:
    edge = 0.64
    counterpart = _counterpart_item(section, layer_index, by_atom, by_loop)
    if counterpart is not None and hasattr(core, "atom_edge_score"):
        try:
            edge_value, _reasons = core.atom_edge_score(
                dict(candidate),
                dict(counterpart),
                _slot_relation(slot_role),
                float(render_bpm),
                int(target_key) % 12,
                float(params.get("stretch_budget") or 8.0),
                int(params.get("pitch_shift_budget") or 2),
            )
            edge = float(edge_value)
        except Exception:
            return None
        if edge <= 0.0:
            return None
    quality = float(candidate.get("score") or candidate.get("atom_score") or 0.0)
    hook = float(candidate.get("hook_score") or 0.0)
    exact_role = 1.0 if _natural_role(candidate) == slot_role else 0.0
    # Lower transform cost wins after compatibility and source coverage.
    transform_cost = abs(float(transform.get("varispeed_pct") or 0.0)) + abs(float(transform.get("residual_pitch_shift") or 0.0)) * 4.0
    stable = _stable_rank(seed, _source_identity(candidate), _atom_identity(candidate), layer_index)
    return (
        edge * 10.0 + exact_role * 1.5 + quality + hook * 0.25 - transform_cost * 0.01,
        quality,
        hook,
        stable,
    )


def _layer_slots(arrangement: Mapping[str, Any]) -> List[Tuple[int, int, MutableMapping[str, Any]]]:
    out: List[Tuple[int, int, MutableMapping[str, Any]]] = []
    for section_index, section in enumerate(arrangement.get("sections") or []):
        for layer_index, layer in enumerate(section.get("layers") or []):
            out.append((section_index, layer_index, layer))
    return out


def _layer_source(layer: Mapping[str, Any]) -> str:
    return str(layer.get("source_track_key") or layer.get("source_id") or layer.get("loop_id") or "")


def _apply_candidate(
    layer: MutableMapping[str, Any],
    candidate: Mapping[str, Any],
    slot_role: str,
    transform: Mapping[str, Any],
) -> None:
    source_id = _source_identity(candidate)
    layer.update({
        "loop_id": _loop_identity(candidate),
        "atom_id": _atom_identity(candidate),
        "ear_role": str(candidate.get("ear_role") or ""),
        "role": slot_role,
        "source_track_key": source_id,
        "pitch_shift": float(transform.get("synthetic_pitch_shift") or 0.0),
        "dry_high3000_share": float(candidate.get("high_share") or candidate.get("dry_high3000_share") or 0.0),
        "dry_quality_score": float(candidate.get("score") or candidate.get("atom_score") or candidate.get("dry_quality_score") or 0.0),
        "transform_mode": transform.get("transform_mode") or transform.get("mode"),
        "source_bpm_raw": transform.get("source_bpm_raw"),
        "source_bpm_folded": transform.get("source_bpm_folded"),
        "tempo_octave_multiplier": transform.get("tempo_octave_multiplier"),
        "speed_ratio": transform.get("speed_ratio"),
        "varispeed_pct": transform.get("varispeed_pct"),
        "natural_pitch_shift": transform.get("natural_pitch_shift"),
        "desired_key_shift": transform.get("desired_key_shift"),
        "residual_pitch_shift": transform.get("residual_pitch_shift"),
        "artifact_risk": transform.get("artifact_risk"),
    })


def _best_replacement(
    core: Any,
    arrangement: MutableMapping[str, Any],
    pool_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
    desired_source: str,
    counts: Counter,
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    seed: int,
    max_events: int,
) -> Optional[Tuple[int, int, MutableMapping[str, Any], Mapping[str, Any], Dict[str, Any]]]:
    pool = list(pool_by_source.get(desired_source) or [])
    if not pool or counts.get(desired_source, 0) >= max_events:
        return None
    all_items = [item for items in pool_by_source.values() for item in items]
    by_atom = {_atom_identity(item): item for item in all_items if _atom_identity(item)}
    by_loop = {_loop_identity(item): item for item in all_items if _loop_identity(item)}
    sections = list(arrangement.get("sections") or [])
    ranked: List[Tuple[Tuple[Any, ...], Tuple[int, int, MutableMapping[str, Any], Mapping[str, Any], Dict[str, Any]]]] = []
    for section_index, layer_index, layer in _layer_slots(arrangement):
        donor_source = _layer_source(layer)
        if donor_source == desired_source or counts.get(donor_source, 0) <= 1:
            continue
        slot_role = str(layer.get("role") or "full")
        section = sections[section_index]
        for candidate in pool:
            if not _role_compatible(slot_role, candidate):
                continue
            transform = _transform_for_slot(candidate, slot_role, render_bpm, target_key, params)
            if transform is None:
                continue
            score = _candidate_score(
                core,
                candidate,
                section,
                layer_index,
                slot_role,
                transform,
                render_bpm,
                target_key,
                params,
                by_atom,
                by_loop,
                seed,
            )
            if score is None:
                continue
            donor_excess = counts.get(donor_source, 0) - 1
            donor_over_cap = max(0, counts.get(donor_source, 0) - max_events)
            tie = _stable_rank(seed, section_index, layer_index, desired_source, donor_source, _atom_identity(candidate))
            rank = (donor_over_cap, donor_excess, *score, tie)
            ranked.append((rank, (section_index, layer_index, layer, candidate, transform)))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0], reverse=True)
    return ranked[0][1]


def _best_balance_replacement(
    core: Any,
    arrangement: MutableMapping[str, Any],
    pool_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
    donor_source: str,
    counts: Counter,
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
    seed: int,
    max_events: int,
) -> Optional[Tuple[int, int, MutableMapping[str, Any], Mapping[str, Any], Dict[str, Any]]]:
    receivers = [source_id for source_id in pool_by_source if source_id != donor_source and counts.get(source_id, 0) < max_events]
    receivers.sort(key=lambda source_id: (counts.get(source_id, 0), _stable_rank(seed, source_id), source_id))
    best = None
    best_rank = None
    for source_id in receivers:
        candidate = _best_replacement(
            core,
            arrangement,
            pool_by_source,
            source_id,
            counts,
            render_bpm,
            target_key,
            params,
            seed,
            max_events,
        )
        if candidate is None:
            continue
        section_index, layer_index, layer, item, transform = candidate
        if _layer_source(layer) != donor_source:
            continue
        rank = (-counts.get(source_id, 0), _stable_rank(seed, source_id, section_index, layer_index))
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = candidate
    return best


def rebalance_exact_pool_sources(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
) -> Dict[str, Any]:
    """Return an exact-pool arrangement with full source reach and bounded reuse.

    The greedy path below is the first authority and is unchanged. When it
    satisfies coverage and the cap, its arrangement — including the frozen
    ``exact_pool_rotation`` ledger — is returned exactly as it was before this
    wrapper existed, and the complete-assignment solver is never built.

    That greedy walk is order-sensitive in two coupled ways: it can only steal a
    slot from a donor that already holds two or more events, and it commits each
    missing source irreversibly in seed order. Season 001 refused four seeds on
    island 7 for exactly that reason while a complete assignment existed. So a
    refusal is no longer final: the assignment is re-solved atomically before
    anyone is told the exact pool is impossible.

    The two preconditions below are re-checked here so a genuinely malformed
    request still refuses immediately instead of being handed to the solver.
    """
    max_events_precheck = int(params.get("exact_pool_max_source_events") or DEFAULT_MAX_SOURCE_EVENTS)
    if max_events_precheck <= 0:
        raise ExactPoolRotationError("exact_pool_max_source_events must be positive")
    if not any(_source_identity(dict(item)) for item in pool):
        raise ExactPoolRotationError("exact source pool is empty")

    try:
        return _greedy_rebalance_exact_pool_sources(core, arrangement, pool, params, seed)
    except ExactPoolRotationError as greedy_refusal:
        from earcrate.plan.exact_pool_assignment import repair_exact_pool_assignment

        return repair_exact_pool_assignment(core, arrangement, pool, params, int(seed), legacy_error=greedy_refusal)


def _greedy_rebalance_exact_pool_sources(
    core: Any,
    arrangement: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    seed: int,
) -> Dict[str, Any]:
    """The original depth-1 rotation. Unchanged, and still the first authority."""
    out: Dict[str, Any] = copy.deepcopy(dict(arrangement))
    render_bpm = float(out.get("bpm") or params.get("exact_target_bpm") or params.get("bpm") or 0.0)
    target_key = int(out.get("target_key") if out.get("target_key") is not None else params.get("exact_target_key") or 0) % 12
    max_events = int(params.get("exact_pool_max_source_events") or DEFAULT_MAX_SOURCE_EVENTS)
    if max_events <= 0:
        raise ExactPoolRotationError("exact_pool_max_source_events must be positive")

    pool_by_source: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for item in sorted((dict(item) for item in pool), key=lambda item: (_source_identity(item), _atom_identity(item), _loop_identity(item))):
        source_id = _source_identity(item)
        if source_id:
            pool_by_source[source_id].append(item)
    target_sources = set(pool_by_source)
    if not target_sources:
        raise ExactPoolRotationError("exact source pool is empty")

    slots = _layer_slots(out)
    if len(slots) < len(target_sources):
        raise ExactPoolRotationError(
            f"exact pool has {len(target_sources)} sources but only {len(slots)} arrangement slots"
        )
    counts = Counter(_layer_source(layer) for _section_index, _layer_index, layer in slots if _layer_source(layer))
    replacements: List[Dict[str, Any]] = []

    missing = _stable_source_order(target_sources - set(counts), int(seed))
    for source_id in missing:
        replacement = _best_replacement(
            core,
            out,
            pool_by_source,
            source_id,
            counts,
            render_bpm,
            target_key,
            params,
            int(seed),
            max_events,
        )
        if replacement is None:
            roles = sorted({_natural_role(item) for item in pool_by_source[source_id]})
            raise ExactPoolRotationError(
                f"source {source_id!r} is unreachable in existing slots; roles={roles}"
            )
        section_index, layer_index, layer, candidate, transform = replacement
        donor_source = _layer_source(layer)
        slot_role = str(layer.get("role") or "full")
        _apply_candidate(layer, candidate, slot_role, transform)
        counts[donor_source] -= 1
        counts[source_id] += 1
        replacements.append({
            "reason": "missing_source",
            "section_index": section_index,
            "layer_index": layer_index,
            "role": slot_role,
            "from_source": donor_source,
            "to_source": source_id,
            "to_atom": _atom_identity(candidate),
        })

    # The arrangement gate already treats 12 as a hard maximum. Enforce it in
    # the compiler rather than knowingly handing an invalid plan to the gate.
    guard = 0
    while counts and max(counts.values()) > max_events:
        guard += 1
        if guard > max(1, len(slots) * 2):
            raise ExactPoolRotationError("source-reuse balancing failed to converge")
        donor_source = sorted(
            (source_id for source_id, count in counts.items() if count > max_events),
            key=lambda source_id: (counts[source_id], _stable_rank(seed, source_id), source_id),
            reverse=True,
        )[0]
        replacement = _best_balance_replacement(
            core,
            out,
            pool_by_source,
            donor_source,
            counts,
            render_bpm,
            target_key,
            params,
            int(seed),
            max_events,
        )
        if replacement is None:
            raise ExactPoolRotationError(
                f"source {donor_source!r} remains at {counts[donor_source]} events; no compatible under-cap source exists"
            )
        section_index, layer_index, layer, candidate, transform = replacement
        receiver_source = _source_identity(candidate)
        slot_role = str(layer.get("role") or "full")
        _apply_candidate(layer, candidate, slot_role, transform)
        counts[donor_source] -= 1
        counts[receiver_source] += 1
        replacements.append({
            "reason": "reuse_cap",
            "section_index": section_index,
            "layer_index": layer_index,
            "role": slot_role,
            "from_source": donor_source,
            "to_source": receiver_source,
            "to_atom": _atom_identity(candidate),
        })

    used = {source_id for source_id, count in counts.items() if count > 0}
    missing_after = sorted(target_sources - used)
    if missing_after:
        raise ExactPoolRotationError(f"exact source rotation left source unused: {missing_after[0]}")
    if counts and max(counts.values()) > max_events:
        offender = max(counts, key=counts.get)
        raise ExactPoolRotationError(f"exact source rotation left {offender!r} above cap")

    ledger = out.setdefault("taste_ledger", {})
    ledger["exact_pool_rotation"] = {
        "version": EXACT_POOL_ROTATION_VERSION,
        "target_source_count": len(target_sources),
        "used_source_count": len(used),
        "max_source_events": max_events,
        "observed_max_source_events": max(counts.values()) if counts else 0,
        "replacement_count": len(replacements),
        "source_event_counts": {source_id: int(counts[source_id]) for source_id in sorted(target_sources)},
        "replacements": replacements,
        "input_order_independent": True,
    }
    return out


def install_exact_pool_rotation(core_class: Any) -> Any:
    """Wrap only exact island composition; ordinary single-deck output is stable."""
    if getattr(core_class, "_exact_pool_rotation_installed", False):
        return core_class
    original = core_class.compose_taste_arrangement

    @functools.wraps(original)
    def wrapped(self: Any, pool: List[Dict[str, Any]], params: Dict[str, Any], seed: int) -> Dict[str, Any]:
        result = original(self, pool, params, seed)
        exact_island = bool(params.get("island_id")) and params.get("exact_target_bpm") is not None and params.get("exact_target_key") is not None
        if not exact_island:
            return result
        return rebalance_exact_pool_sources(self, result, pool, params, int(seed))

    core_class._ordinary_compose_taste_arrangement = original
    core_class.compose_taste_arrangement = wrapped
    core_class._exact_pool_rotation_installed = True
    return core_class


__all__ = [
    "DEFAULT_MAX_SOURCE_EVENTS",
    "EXACT_POOL_ROTATION_VERSION",
    "ExactPoolRotationError",
    "install_exact_pool_rotation",
    "rebalance_exact_pool_sources",
]
