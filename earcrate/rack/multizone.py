from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.model import midi_sha256_json, midi_validate_ledger
from earcrate.rack.binding_stable import rack_compile_binding
from earcrate.rack.demand import rack_compile_demands, rack_validate_demands
from earcrate.rack.library import (
    LIBRARY_BUILD_SCHEMA_VERSION,
    LIBRARY_PROPOSAL_KIND,
    LIBRARY_PROPOSAL_SCHEMA_VERSION,
    _loop_for_selection,
    _materialized_asset_path,
    _normalize_atom,
    _pitched_timbre_fit,
    _role_fit,
    _slot_role,
    _stable_text,
    _trigger_spectral_fit,
)
from earcrate.rack.library_fix import _materialize_atom
from earcrate.rack.model import RackError, rack_atomic_json, rack_seal_draft, rack_sha256_file
from earcrate.rack.sfz import rack_compile_sfz

MULTIZONE_STRATEGY_VERSION = 1
DEFAULT_MAX_ZONES_PER_SLOT = 8
DEFAULT_COMBINATION_BEAM_WIDTH = 64


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _normalize_multizone_atom(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    atom = _normalize_atom(raw)
    if atom is None:
        return None
    metrics = raw.get("metrics_json")
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}
    metrics = dict(metrics or {})
    root_value = raw.get("root_midi")
    if root_value in {None, ""}:
        root_value = raw.get("fundamental_midi")
    if root_value in {None, ""}:
        root_value = metrics.get("root_midi", metrics.get("fundamental_midi"))
    root_known = root_value not in {None, ""}
    root_midi = int(round(_number(root_value, 60.0))) if root_known else None
    if root_midi is not None and not 0 <= root_midi <= 127:
        root_midi = None
        root_known = False
    atom["root_midi"] = root_midi
    atom["root_midi_known"] = bool(root_known)
    atom["root_pitch_confidence"] = max(
        0.0,
        min(1.0, _number(raw.get("root_pitch_confidence", metrics.get("root_pitch_confidence")), 0.0)),
    )
    return atom


def _note_values(requirements: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(row["note"]) for row in requirements]


def _weighted_center(requirements: Sequence[Mapping[str, Any]]) -> float:
    total = sum(max(1, int(row.get("event_count") or 1)) for row in requirements)
    return sum(
        int(row["note"]) * max(1, int(row.get("event_count") or 1))
        for row in requirements
    ) / max(1, total)


def _root_candidates(
    atom: Mapping[str, Any],
    requirements: Sequence[Mapping[str, Any]],
    maximum_transpose_semitones: float,
) -> tuple[list[int], str]:
    if atom.get("root_midi_known") and atom.get("root_midi") is not None:
        return [int(atom["root_midi"])], "measured_root_midi"
    center = int(round(_weighted_center(requirements)))
    if not bool(atom.get("key_known")):
        return [max(0, min(127, center))], "range_center_unpitched"
    notes = _note_values(requirements)
    lo = max(0, int(math.floor(min(notes) - maximum_transpose_semitones)))
    hi = min(127, int(math.ceil(max(notes) + maximum_transpose_semitones)))
    pitch_class = int(atom.get("key_root") or 0) % 12
    candidates = [note for note in range(lo, hi + 1) if note % 12 == pitch_class]
    if not candidates:
        candidates = [note for note in range(128) if note % 12 == pitch_class]
    return candidates, "pitch_class_octave_inference"


def _best_root(
    atom: Mapping[str, Any],
    requirements: Sequence[Mapping[str, Any]],
    maximum_transpose_semitones: float,
) -> dict[str, Any]:
    candidates, inference = _root_candidates(atom, requirements, maximum_transpose_semitones)
    weighted_total = sum(max(1, int(row.get("event_count") or 1)) for row in requirements)
    center = _weighted_center(requirements)

    def metrics(root: int) -> tuple[tuple[float, ...], dict[str, Any]]:
        offsets = [int(row["note"]) - int(root) for row in requirements]
        weighted_abs = sum(
            abs(int(row["note"]) - int(root)) * max(1, int(row.get("event_count") or 1))
            for row in requirements
        ) / max(1, weighted_total)
        value = {
            "root_key": int(root),
            "root_inference": inference,
            "maximum_transpose_semitones": max(abs(offset) for offset in offsets),
            "average_absolute_transpose_semitones": weighted_abs,
            "maximum_upward_transpose_semitones": max(0, max(offsets)),
            "maximum_downward_transpose_semitones": max(0, -min(offsets)),
        }
        rank = (
            float(value["maximum_transpose_semitones"]),
            float(weighted_abs),
            abs(float(root) - center),
            float(root),
        )
        return rank, value

    return min((metrics(root) for root in candidates), key=lambda item: item[0])[1]


def _duration_receipt(
    atom: Mapping[str, Any],
    requirements: Sequence[Mapping[str, Any]],
    root_key: int,
    *,
    pitch_bend_range_semitones: float,
    loopability_threshold: float,
) -> dict[str, Any]:
    coverage_rows = []
    loop_required = False
    minimum_ratio = float("inf")
    minimum_coverage = float("inf")
    for requirement in requirements:
        note = int(requirement["note"])
        required = float(requirement["maximum_duration_seconds"])
        fastest_shift = note - int(root_key) + float(pitch_bend_range_semitones)
        fastest_ratio = 2.0 ** (fastest_shift / 12.0)
        coverage = float(atom["duration_s"]) / max(1e-9, fastest_ratio)
        ratio = coverage / max(1e-9, required)
        minimum_ratio = min(minimum_ratio, ratio)
        minimum_coverage = min(minimum_coverage, coverage)
        needs_loop = required > coverage + 1e-6
        loop_required = loop_required or needs_loop
        coverage_rows.append(
            {
                "note": note,
                "required_duration_seconds": round(required, 9),
                "unlooped_coverage_seconds": round(coverage, 9),
                "requires_loop": needs_loop,
            }
        )
    if minimum_ratio == float("inf"):
        minimum_ratio = 0.0
        minimum_coverage = 0.0
    duration_fit = min(1.0, minimum_ratio)
    if loop_required:
        duration_fit = min(1.0, float(atom["loopability"]) / max(1e-9, loopability_threshold))
    return {
        "loop_required": bool(loop_required),
        "duration_fit": float(duration_fit),
        "minimum_unlooped_coverage_seconds": round(float(minimum_coverage), 9),
        "coverage": coverage_rows,
    }


def _candidate_receipt(
    slot: Mapping[str, Any],
    atom: Mapping[str, Any],
    requirements: Sequence[Mapping[str, Any]],
    *,
    maximum_transpose_semitones: float,
    loopability_threshold: float,
    pitch_bend_range_semitones: float,
) -> dict[str, Any]:
    role = _slot_role(slot)
    role_fit = _role_fit(slot, atom)
    notes = _note_values(requirements)
    key_range = [min(notes), max(notes)]
    if str(slot["mode"]) == "trigger":
        root = {
            "root_key": int(notes[0]),
            "root_inference": "trigger_note",
            "maximum_transpose_semitones": 0.0,
            "average_absolute_transpose_semitones": 0.0,
            "maximum_upward_transpose_semitones": 0.0,
            "maximum_downward_transpose_semitones": 0.0,
        }
        duration = {
            "loop_required": False,
            "duration_fit": 1.0,
            "minimum_unlooped_coverage_seconds": float(atom["duration_s"]),
            "coverage": [],
        }
        timbre = _trigger_spectral_fit(int(notes[0]), atom)
    else:
        root = _best_root(atom, requirements, maximum_transpose_semitones)
        duration = _duration_receipt(
            atom,
            requirements,
            int(root["root_key"]),
            pitch_bend_range_semitones=pitch_bend_range_semitones,
            loopability_threshold=loopability_threshold,
        )
        timbre = _pitched_timbre_fit(role, atom)

    hard_failures: list[str] = []
    if role_fit < 0.24:
        hard_failures.append("role_incompatible")
    if str(slot["mode"]) == "pitched" and float(root["maximum_transpose_semitones"]) > float(maximum_transpose_semitones) + 1e-9:
        hard_failures.append("transpose_budget_exceeded")
    if bool(duration["loop_required"]) and float(atom["loopability"]) < float(loopability_threshold):
        hard_failures.append("insufficient_duration_and_loopability")
    if str(slot["mode"]) == "trigger" and float(atom["duration_s"]) > 32.0:
        hard_failures.append("trigger_region_too_long")

    quality = float(atom["score"])
    key_fit = 1.0 if str(slot["mode"]) == "trigger" else max(
        0.0,
        1.0
        - float(root["average_absolute_transpose_semitones"])
        / max(1.0, float(maximum_transpose_semitones)),
    )
    zone_span = max(0, key_range[1] - key_range[0])
    coverage_efficiency = min(1.0, len(notes) / max(1.0, zone_span + 1.0))
    score = (
        0.31 * role_fit
        + 0.18 * quality
        + 0.18 * float(timbre)
        + 0.12 * float(duration["duration_fit"])
        + 0.12 * key_fit
        + 0.05 * float(atom["loopability"])
        + 0.04 * coverage_efficiency
    )
    identity = midi_sha256_json(
        {
            "atom_id": atom["atom_id"],
            "key_range": key_range,
            "root_key": root["root_key"],
            "notes": notes,
        }
    )
    return {
        "candidate_id": "zone_candidate_" + identity[:24],
        "atom_id": atom["atom_id"],
        "compatible": not hard_failures,
        "hard_failures": hard_failures,
        "score": round(float(score), 9) if not hard_failures else None,
        "key_range": key_range,
        "covered_notes": notes,
        "covered_event_count": sum(int(row.get("event_count") or 0) for row in requirements),
        **root,
        "pitch_bend_range_semitones": float(pitch_bend_range_semitones),
        "loop_required": bool(duration["loop_required"]),
        "minimum_unlooped_coverage_seconds": duration["minimum_unlooped_coverage_seconds"],
        "duration_coverage": duration["coverage"] if not hard_failures else [],
        "score_terms": {
            "role_fit": round(float(role_fit), 9),
            "quality": round(quality, 9),
            "timbre_fit": round(float(timbre), 9),
            "duration_fit": round(float(duration["duration_fit"]), 9),
            "key_fit": round(float(key_fit), 9),
            "loopability": round(float(atom["loopability"]), 9),
            "coverage_efficiency": round(float(coverage_efficiency), 9),
        },
        "source": dict(atom) if not hard_failures else None,
    }


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -float(candidate["score"]),
        float(candidate["maximum_transpose_semitones"]),
        float(candidate["average_absolute_transpose_semitones"]),
        str(candidate["atom_id"]),
        int(candidate["root_key"]),
        tuple(int(value) for value in candidate["key_range"]),
    )


def _rank_group(
    slot: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    requirements: Sequence[Mapping[str, Any]],
    *,
    top_k: int,
    maximum_transpose_semitones: float,
    loopability_threshold: float,
    pitch_bend_range_semitones: float,
) -> dict[str, Any]:
    top: list[dict[str, Any]] = []
    compatible_count = 0
    rejected: Counter[str] = Counter()
    for atom in atoms:
        receipt = _candidate_receipt(
            slot,
            atom,
            requirements,
            maximum_transpose_semitones=maximum_transpose_semitones,
            loopability_threshold=loopability_threshold,
            pitch_bend_range_semitones=pitch_bend_range_semitones,
        )
        if not receipt["compatible"]:
            rejected.update(receipt["hard_failures"])
            continue
        compatible_count += 1
        top.append(receipt)
        top.sort(key=_candidate_sort_key)
        if len(top) > top_k:
            top.pop()
    notes = _note_values(requirements)
    return {
        "mode": str(slot["mode"]),
        "note": int(notes[0]) if str(slot["mode"]) == "trigger" else None,
        "key_range": [min(notes), max(notes)],
        "covered_notes": notes,
        "candidate_count": compatible_count,
        "candidates": top,
        "rejected_count": max(0, len(atoms) - compatible_count),
        "rejected_reasons": dict(sorted(rejected.items())),
    }


def _initial_pitched_bands(
    requirements: Sequence[Mapping[str, Any]],
    maximum_transpose_semitones: float,
) -> list[list[dict[str, Any]]]:
    ordered = [deepcopy(dict(row)) for row in sorted(requirements, key=lambda row: int(row["note"]))]
    if not ordered:
        return []
    maximum_span = 2.0 * float(maximum_transpose_semitones)
    count = len(ordered)
    best: list[tuple[tuple[Any, ...], list[tuple[int, int]]] | None] = [None] * (count + 1)
    best[count] = ((0, 0.0, 0.0, ()), [])
    for start in range(count - 1, -1, -1):
        choices = []
        for end in range(start, count):
            width = int(ordered[end]["note"]) - int(ordered[start]["note"])
            if width > maximum_span + 1e-9:
                break
            tail = best[end + 1]
            if tail is None:
                continue
            intervals = [(start, end), *tail[1]]
            widths = [int(ordered[hi]["note"]) - int(ordered[lo]["note"]) for lo, hi in intervals]
            rank = (
                len(intervals),
                max(widths, default=0),
                sum(width * width for width in widths),
                tuple(int(ordered[hi]["note"]) for _lo, hi in intervals),
            )
            choices.append((rank, intervals))
        if choices:
            best[start] = min(choices, key=lambda item: item[0])
    if best[0] is None:
        return [[row] for row in ordered]
    return [[deepcopy(ordered[index]) for index in range(lo, hi + 1)] for lo, hi in best[0][1]]


def _split_band(requirements: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = [deepcopy(dict(row)) for row in sorted(requirements, key=lambda row: int(row["note"]))]
    if len(ordered) < 2:
        raise RackError("cannot split a one-note zone demand")
    choices = []
    for index in range(1, len(ordered)):
        gap = int(ordered[index]["note"]) - int(ordered[index - 1]["note"])
        left_width = int(ordered[index - 1]["note"]) - int(ordered[0]["note"])
        right_width = int(ordered[-1]["note"]) - int(ordered[index]["note"])
        rank = (-gap, max(left_width, right_width), abs(left_width - right_width), abs(index - len(ordered) / 2.0), index)
        choices.append((rank, index))
    split = min(choices, key=lambda item: item[0])[1]
    return ordered[:split], ordered[split:]


def _coherence(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    a = left["source"]
    b = right["source"]
    distance = (
        abs(float(a.get("low_share") or 0.0) - float(b.get("low_share") or 0.0))
        + abs(float(a.get("mid_share") or 0.0) - float(b.get("mid_share") or 0.0))
        + abs(float(a.get("high_share") or 0.0) - float(b.get("high_share") or 0.0))
    ) / 3.0
    role_match = 1.0 if str(a.get("ear_role")) == str(b.get("ear_role")) else 0.65
    return max(0.0, min(1.0, 0.72 * (1.0 - distance) + 0.28 * role_match))


def _select_combination(
    groups: Sequence[Mapping[str, Any]],
    *,
    global_atom_use: Counter[str],
    beam_width: int,
) -> tuple[list[dict[str, Any]], float]:
    states: list[dict[str, Any]] = [{"score": 0.0, "choices": [], "uses": Counter()}]
    for group in groups:
        candidates = list(group.get("candidates") or [])
        if not candidates:
            return [], float("-inf")
        expanded = []
        for state in states:
            for candidate in candidates:
                atom_id = str(candidate["atom_id"])
                local_reuse = int(state["uses"][atom_id])
                global_reuse = int(global_atom_use[atom_id])
                previous = state["choices"][-1] if state["choices"] else None
                coherence = _coherence(previous, candidate) if previous is not None else 1.0
                local_score = (
                    float(candidate["score"])
                    + 0.045 * coherence
                    - 0.070 * local_reuse
                    - 0.025 * global_reuse
                )
                uses = Counter(state["uses"])
                uses[atom_id] += 1
                choice = deepcopy(dict(candidate))
                choice["selection_terms"] = {
                    "candidate_score": round(float(candidate["score"]), 9),
                    "coherence": round(float(coherence), 9),
                    "local_reuse_penalty": round(0.070 * local_reuse, 9),
                    "global_reuse_penalty": round(0.025 * global_reuse, 9),
                }
                expanded.append(
                    {
                        "score": float(state["score"]) + local_score,
                        "choices": [*state["choices"], choice],
                        "uses": uses,
                    }
                )
        expanded.sort(
            key=lambda state: (
                -float(state["score"]),
                tuple(str(choice["candidate_id"]) for choice in state["choices"]),
            )
        )
        states = expanded[: max(1, int(beam_width))]
    winner = states[0]
    selected = []
    for index, choice in enumerate(winner["choices"]):
        row = deepcopy(choice)
        row["zone_index"] = index
        if row.get("note") is None and len(row.get("covered_notes") or []) == 1 and row["key_range"][0] == row["key_range"][1]:
            row["note"] = int(row["covered_notes"][0])
        selected.append(row)
    return selected, round(float(winner["score"]), 9)


def _groups_for_slot(
    slot: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    *,
    top_k: int,
    maximum_transpose_semitones: float,
    loopability_threshold: float,
    pitch_bend_range_semitones: float,
    max_zones_per_slot: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if str(slot["mode"]) == "trigger":
        groups = [
            _rank_group(
                slot,
                atoms,
                [requirement],
                top_k=top_k,
                maximum_transpose_semitones=maximum_transpose_semitones,
                loopability_threshold=loopability_threshold,
                pitch_bend_range_semitones=pitch_bend_range_semitones,
            )
            for requirement in slot["note_requirements"]
        ]
        unresolved = [
            {
                "slot_id": slot["slot_id"],
                "note": group["note"],
                "key_range": group["key_range"],
                "reason": "no_compatible_approved_atom",
                "rejected_reasons": group["rejected_reasons"],
            }
            for group in groups
            if not group["candidates"]
        ]
        return groups, unresolved

    bands = _initial_pitched_bands(slot["note_requirements"], maximum_transpose_semitones)
    cache: dict[tuple[int, ...], dict[str, Any]] = {}
    while True:
        if len(bands) > int(max_zones_per_slot):
            return [], [
                {
                    "slot_id": slot["slot_id"],
                    "note": None,
                    "key_range": [int(slot["minimum_note"]), int(slot["maximum_note"])],
                    "reason": "maximum_zone_count_exceeded",
                    "required_zone_count": len(bands),
                    "maximum_zone_count": int(max_zones_per_slot),
                }
            ]
        groups = []
        failing_index = None
        for index, band in enumerate(bands):
            key = tuple(_note_values(band))
            group = cache.get(key)
            if group is None:
                group = _rank_group(
                    slot,
                    atoms,
                    band,
                    top_k=top_k,
                    maximum_transpose_semitones=maximum_transpose_semitones,
                    loopability_threshold=loopability_threshold,
                    pitch_bend_range_semitones=pitch_bend_range_semitones,
                )
                cache[key] = group
            groups.append(group)
            if failing_index is None and not group["candidates"]:
                failing_index = index
        if failing_index is None:
            return groups, []
        failing_band = bands[failing_index]
        if len(failing_band) < 2 or len(bands) >= int(max_zones_per_slot):
            group = groups[failing_index]
            return groups, [
                {
                    "slot_id": slot["slot_id"],
                    "note": None,
                    "key_range": group["key_range"],
                    "covered_notes": group["covered_notes"],
                    "reason": "no_compatible_approved_atom",
                    "rejected_reasons": group["rejected_reasons"],
                }
            ]
        left, right = _split_band(failing_band)
        bands = [*bands[:failing_index], left, right, *bands[failing_index + 1 :]]


def rack_propose_from_atoms(
    demand: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    *,
    taste_profile: str = "",
    top_k: int = 8,
    maximum_transpose_semitones: float = 18.0,
    loopability_threshold: float = 0.58,
    max_zones_per_slot: int = DEFAULT_MAX_ZONES_PER_SLOT,
    combination_beam_width: int = DEFAULT_COMBINATION_BEAM_WIDTH,
) -> dict[str, Any]:
    """Build deterministic multi-zone substitutions without changing MIDI semantics."""
    rack_validate_demands(demand)
    if top_k <= 0:
        raise RackError("top_k must be positive")
    if maximum_transpose_semitones <= 0:
        raise RackError("maximum_transpose_semitones must be positive")
    if max_zones_per_slot <= 0:
        raise RackError("max_zones_per_slot must be positive")
    if combination_beam_width <= 0:
        raise RackError("combination_beam_width must be positive")
    normalized = [value for value in (_normalize_multizone_atom(atom) for atom in atoms) if value is not None]
    normalized.sort(key=lambda atom: str(atom["atom_id"]))
    if not normalized:
        raise RackError("no usable approved EarAtoms were supplied")
    atom_pool_sha256 = midi_sha256_json(normalized)
    global_atom_use: Counter[str] = Counter()
    slots = []
    unresolved = []
    bend_range = float(demand["pitch_bend_range_semitones"])

    for slot in demand["slots"]:
        groups, failures = _groups_for_slot(
            slot,
            normalized,
            top_k=top_k,
            maximum_transpose_semitones=maximum_transpose_semitones,
            loopability_threshold=loopability_threshold,
            pitch_bend_range_semitones=bend_range,
            max_zones_per_slot=max_zones_per_slot,
        )
        selected: list[dict[str, Any]] = []
        combination_score = None
        if not failures:
            selected, combination_score = _select_combination(
                groups,
                global_atom_use=global_atom_use,
                beam_width=combination_beam_width,
            )
            if not selected:
                failures = [{"slot_id": slot["slot_id"], "reason": "combination_search_exhausted"}]
        for choice in selected:
            global_atom_use[str(choice["atom_id"])] += 1
        unresolved.extend(failures)
        demanded_notes = sorted(int(row["note"]) for row in slot["note_requirements"])
        covered_notes = sorted({note for choice in selected for note in choice.get("covered_notes") or []})
        complete = not failures and covered_notes == demanded_notes
        strategy = "trigger_map" if str(slot["mode"]) == "trigger" else ("multi_zone" if len(selected) > 1 else "single_zone")
        slots.append(
            {
                "slot_id": slot["slot_id"],
                "track_index": slot["track_index"],
                "track_name": slot["track_name"],
                "channel": slot["channel"],
                "program": slot["program"],
                "mode": slot["mode"],
                "role_hint": slot["role_hint"],
                "gm_family": slot["gm_family"],
                "strategy": strategy,
                "zone_count": len(selected),
                "demanded_notes": demanded_notes,
                "covered_notes": covered_notes,
                "combination_score": combination_score,
                "candidate_groups": groups,
                "selected": selected,
                "complete": complete,
            }
        )

    proposal = {
        "schema_version": LIBRARY_PROPOSAL_SCHEMA_VERSION,
        "kind": LIBRARY_PROPOSAL_KIND,
        "strategy_version": MULTIZONE_STRATEGY_VERSION,
        "demand_sha256": demand["demand_sha256"],
        "semantic_sha256": demand["semantic_sha256"],
        "taste_profile": str(taste_profile),
        "atom_pool_sha256": atom_pool_sha256,
        "atom_pool_count": len(normalized),
        "configuration": {
            "top_k": int(top_k),
            "maximum_transpose_semitones": float(maximum_transpose_semitones),
            "loopability_threshold": float(loopability_threshold),
            "max_zones_per_slot": int(max_zones_per_slot),
            "combination_beam_width": int(combination_beam_width),
        },
        "complete": not unresolved and all(bool(slot["complete"]) for slot in slots),
        "slot_count": len(slots),
        "zone_count": sum(int(slot["zone_count"]) for slot in slots),
        "multi_zone_slot_count": sum(1 for slot in slots if slot["strategy"] == "multi_zone"),
        "selected_atom_count": sum(len(slot["selected"]) for slot in slots),
        "slots": slots,
        "unresolved": unresolved,
        "demand": deepcopy(dict(demand)),
    }
    proposal["proposal_sha256"] = midi_sha256_json(proposal)
    rack_validate_library_proposal(proposal)
    return proposal


def rack_validate_library_proposal(proposal: Mapping[str, Any]) -> None:
    if int(proposal.get("schema_version") or 0) != LIBRARY_PROPOSAL_SCHEMA_VERSION:
        raise RackError(f"unsupported library proposal schema: {proposal.get('schema_version')}")
    if str(proposal.get("kind") or "") != LIBRARY_PROPOSAL_KIND:
        raise RackError(f"unsupported library proposal kind: {proposal.get('kind')}")
    if int(proposal.get("strategy_version") or 0) != MULTIZONE_STRATEGY_VERSION:
        raise RackError(f"unsupported multi-zone strategy: {proposal.get('strategy_version')}")
    demand = proposal.get("demand") or {}
    rack_validate_demands(demand)
    if str(proposal.get("demand_sha256") or "") != str(demand.get("demand_sha256") or ""):
        raise RackError("proposal demand identity disagrees with embedded demand")
    demand_slots = {str(slot["slot_id"]): slot for slot in demand["slots"]}
    proposal_slots = proposal.get("slots")
    if not isinstance(proposal_slots, list) or len(proposal_slots) != len(demand_slots):
        raise RackError("proposal slots must correspond one-to-one with demand slots")
    seen = set()
    total_zones = 0
    complete_slots = True
    for slot in proposal_slots:
        slot_id = str(slot.get("slot_id") or "")
        if slot_id not in demand_slots or slot_id in seen:
            raise RackError(f"unknown or duplicate proposal slot: {slot_id}")
        seen.add(slot_id)
        selected = slot.get("selected")
        if not isinstance(selected, list):
            raise RackError(f"proposal slot {slot_id} selected must be a list")
        demanded_notes = sorted(int(row["note"]) for row in demand_slots[slot_id]["note_requirements"])
        covered: list[int] = []
        ranges = []
        for choice in selected:
            if not bool(choice.get("compatible")) or choice.get("hard_failures"):
                raise RackError(f"proposal slot {slot_id} selected an incompatible candidate")
            key_range = choice.get("key_range")
            if not isinstance(key_range, list) or len(key_range) != 2:
                raise RackError(f"proposal slot {slot_id} selected candidate has no key_range")
            lo, hi = int(key_range[0]), int(key_range[1])
            if lo < 0 or hi > 127 or hi < lo:
                raise RackError(f"proposal slot {slot_id} selected candidate has invalid key_range")
            if not lo <= int(choice.get("root_key", -1)) <= hi and str(slot.get("mode")) == "trigger":
                raise RackError(f"proposal trigger slot {slot_id} root is outside its key range")
            ranges.append((lo, hi))
            covered.extend(int(note) for note in choice.get("covered_notes") or [])
        if str(slot.get("mode")) == "pitched":
            for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
                if left[1] >= right[0]:
                    raise RackError(f"proposal slot {slot_id} contains overlapping pitched zones")
        covered = sorted(set(covered))
        slot_complete = bool(slot.get("complete"))
        if slot_complete != (covered == demanded_notes):
            raise RackError(f"proposal slot {slot_id} completeness disagrees with note coverage")
        complete_slots = complete_slots and slot_complete
        total_zones += len(selected)
    if int(proposal.get("zone_count") or 0) != total_zones:
        raise RackError("proposal zone_count does not match selected zones")
    complete = bool(proposal.get("complete"))
    if complete and proposal.get("unresolved"):
        raise RackError("complete proposal cannot contain unresolved requirements")
    if complete != (complete_slots and not proposal.get("unresolved")):
        raise RackError("proposal completeness disagrees with slots and unresolved requirements")
    expected = midi_sha256_json({key: value for key, value in proposal.items() if key != "proposal_sha256"})
    if str(proposal.get("proposal_sha256") or "") != expected:
        raise RackError("proposal_sha256 does not match proposal contents")


def rack_materialize_library_proposal(
    ledger: Mapping[str, Any],
    proposal: Mapping[str, Any],
    output_root: str | Path,
    *,
    sample_rate: int = 44_100,
    overwrite: bool = False,
    compile_sfz: bool = True,
) -> dict[str, Any]:
    midi_validate_ledger(ledger)
    rack_validate_library_proposal(proposal)
    if str(ledger["semantic_sha256"]) != str(proposal["semantic_sha256"]):
        raise RackError("library proposal was compiled for another MIDI performance")
    if not bool(proposal.get("complete")):
        raise RackError("cannot materialize an incomplete library proposal")
    if sample_rate <= 0:
        raise RackError("sample_rate must be positive")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    proposal_path = root / "proposal.json"
    demand_path = root / "demand.json"
    binding_path = root / "binding.json"
    build_path = root / "build.receipt.json"
    fixed_paths = [proposal_path, demand_path, binding_path, build_path]
    if not overwrite:
        conflicts = [str(path) for path in fixed_paths if path.exists()]
        if conflicts:
            raise FileExistsError("refusing to overwrite existing library rack build: " + ", ".join(conflicts))

    materialized: dict[str, dict[str, Any]] = {}
    racks = []
    rack_receipts = []
    for slot in proposal["slots"]:
        zones = []
        for selected in slot["selected"]:
            asset = _materialized_asset_path(root, selected, int(sample_rate))
            identity = materialized.get(str(asset))
            if identity is None:
                identity = _materialize_atom(selected, asset, int(sample_rate), overwrite=overwrite)
                materialized[str(asset)] = identity
            atom = selected["source"]
            key_range = [int(value) for value in selected["key_range"]]
            trigger_mode = "one_shot" if str(slot["mode"]) == "trigger" else "gate"
            zone_id = "zone_" + midi_sha256_json(
                {
                    "slot_id": slot["slot_id"],
                    "candidate_id": selected["candidate_id"],
                    "asset_sha256": identity["sha256"],
                    "key_range": key_range,
                    "root_key": selected["root_key"],
                }
            )[:20]
            zones.append(
                {
                    "zone_id": zone_id,
                    "sample_path": identity["path"],
                    "key_range": key_range,
                    "velocity_range": [1, 127],
                    "root_key": int(selected["root_key"]),
                    "trigger_mode": trigger_mode,
                    "loop": _loop_for_selection(selected, int(identity["frames"]), int(sample_rate), str(slot["mode"])),
                    "tune_cents": 0.0,
                    "gain_db": 0.0,
                    "pan": 0.0,
                    "attack_ms": 0.0 if trigger_mode == "one_shot" else 3.0,
                    "release_ms": 6.0 if trigger_mode == "one_shot" else 24.0,
                    "tags": [
                        str(slot["role_hint"]),
                        str(slot["gm_family"]),
                        str(atom["ear_role"]),
                        str(atom["render_role"]),
                        "multizone" if str(slot["strategy"]) == "multi_zone" else str(slot["strategy"]),
                    ],
                }
            )
        rack_id = "rack_" + str(slot["slot_id"])[len("slot_") :]
        draft = {
            "rack_id": rack_id,
            "name": f"{slot['track_name']} crate substitute",
            "mode": slot["mode"],
            "metadata": {
                "tags": [slot["role_hint"], slot["gm_family"], "earcrate-library", str(slot["strategy"])],
                "slot_id": slot["slot_id"],
                "track_index": slot["track_index"],
                "track_name": slot["track_name"],
                "proposal_sha256": proposal["proposal_sha256"],
                "strategy": slot["strategy"],
                "maximum_transpose_semitones": proposal["configuration"]["maximum_transpose_semitones"],
                "selected_atoms": [selected["atom_id"] for selected in slot["selected"]],
            },
            "created_by": {
                "actor": "earcrate_multizone_library_adapter",
                "reason": "deterministic approved-atom substitution under a per-zone transpose invariant",
            },
            "zones": zones,
        }
        rack = rack_seal_draft(draft)
        rack_path = root / "racks" / f"{_stable_text(rack_id)}-{rack['rack_sha256'][:12]}.rack.json"
        rack_json_receipt = rack_atomic_json(rack_path, rack, overwrite=overwrite)
        sfz_receipt = None
        if compile_sfz:
            sfz_path = root / "sfz" / f"{_stable_text(rack_id)}-{rack['rack_sha256'][:12]}.sfz"
            sfz_receipt = rack_compile_sfz(rack, sfz_path, overwrite=overwrite)
        racks.append(rack)
        rack_receipts.append(
            {
                "slot_id": slot["slot_id"],
                "strategy": slot["strategy"],
                "zone_count": len(zones),
                "rack_id": rack["rack_id"],
                "rack_sha256": rack["rack_sha256"],
                "rack_path": str(rack_path),
                "rack_file_sha256": rack_json_receipt["sha256"],
                "sfz": sfz_receipt,
            }
        )

    binding = rack_compile_binding(
        ledger,
        racks,
        assignments={row["slot_id"]: row["rack_id"] for row in rack_receipts},
        pitch_bend_range_semitones=float(proposal["demand"]["pitch_bend_range_semitones"]),
    )
    if not binding["complete"]:
        raise RackError("materialized library racks did not satisfy their own demand: " + json.dumps(binding["unresolved"], sort_keys=True))
    budget = float(proposal["configuration"]["maximum_transpose_semitones"])
    violations = [
        {
            "event_id": row["event_id"],
            "transpose_semitones": row["transpose_semitones"],
            "zone_id": row["zone_id"],
        }
        for row in binding["event_bindings"]
        if abs(float(row["transpose_semitones"])) > budget + 1e-9
    ]
    if violations:
        raise RackError("binding exceeded the sealed per-zone transpose budget: " + json.dumps(violations[:20], sort_keys=True))

    rack_atomic_json(proposal_path, proposal, overwrite=overwrite)
    rack_atomic_json(demand_path, proposal["demand"], overwrite=overwrite)
    rack_atomic_json(binding_path, binding, overwrite=overwrite)
    build = {
        "schema_version": LIBRARY_BUILD_SCHEMA_VERSION,
        "kind": "earcrate_multizone_library_rack_build",
        "ok": True,
        "semantic_sha256": ledger["semantic_sha256"],
        "demand_sha256": proposal["demand_sha256"],
        "proposal_sha256": proposal["proposal_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "complete": binding["complete"],
        "sample_rate": int(sample_rate),
        "zone_count": int(proposal["zone_count"]),
        "multi_zone_slot_count": int(proposal["multi_zone_slot_count"]),
        "maximum_transpose_semitones": budget,
        "maximum_realized_transpose_semitones": max(
            (abs(float(row["transpose_semitones"])) for row in binding["event_bindings"]),
            default=0.0,
        ),
        "materializations": sorted(materialized.values(), key=lambda value: str(value["path"])),
        "racks": rack_receipts,
        "proposal_path": str(proposal_path),
        "demand_path": str(demand_path),
        "binding_path": str(binding_path),
    }
    build["build_sha256"] = midi_sha256_json(build)
    rack_atomic_json(build_path, build, overwrite=overwrite)
    build["build_path"] = str(build_path)
    build["build_file_sha256"] = rack_sha256_file(build_path)
    build["rack_revisions"] = racks
    build["binding"] = binding
    return build


def rack_build_from_atoms(
    ledger: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    output_root: str | Path | None = None,
    *,
    taste_profile: str = "",
    top_k: int = 8,
    maximum_transpose_semitones: float = 18.0,
    loopability_threshold: float = 0.58,
    max_zones_per_slot: int = DEFAULT_MAX_ZONES_PER_SLOT,
    combination_beam_width: int = DEFAULT_COMBINATION_BEAM_WIDTH,
    sample_rate: int = 44_100,
    apply: bool = False,
    overwrite: bool = False,
    compile_sfz: bool = True,
) -> dict[str, Any]:
    demand = rack_compile_demands(ledger)
    proposal = rack_propose_from_atoms(
        demand,
        atoms,
        taste_profile=taste_profile,
        top_k=top_k,
        maximum_transpose_semitones=maximum_transpose_semitones,
        loopability_threshold=loopability_threshold,
        max_zones_per_slot=max_zones_per_slot,
        combination_beam_width=combination_beam_width,
    )
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "complete": proposal["complete"],
            "semantic_sha256": ledger["semantic_sha256"],
            "demand_sha256": demand["demand_sha256"],
            "proposal_sha256": proposal["proposal_sha256"],
            "atom_pool_count": proposal["atom_pool_count"],
            "slot_count": len(proposal["slots"]),
            "zone_count": proposal["zone_count"],
            "multi_zone_slot_count": proposal["multi_zone_slot_count"],
            "selected_atom_count": proposal["selected_atom_count"],
            "maximum_transpose_semitones": float(maximum_transpose_semitones),
            "unresolved": proposal["unresolved"],
            "proposal": proposal,
        }
    if output_root is None:
        raise RackError("output_root is required when apply=True")
    return rack_materialize_library_proposal(
        ledger,
        proposal,
        output_root,
        sample_rate=sample_rate,
        overwrite=overwrite,
        compile_sfz=compile_sfz,
    )
