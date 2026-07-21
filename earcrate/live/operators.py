from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from earcrate.live.model import LiveError
from earcrate.midi.model import midi_sha256_json

LIVE_TECHNIQUE_NAMES = (
    "blend",
    "hard_cut",
    "loop_extend",
    "drop_to_floor",
    "foreground_swap",
    "tease",
    "build_layers",
    "breakdown",
    "echo_out",
    "drum_rebuild",
    "sample_chop",
    "layer_accumulation",
)

_LIVE_OPERATOR_RISK = {
    "blend": 0.20,
    "hard_cut": 0.72,
    "loop_extend": 0.08,
    "drop_to_floor": 0.30,
    "foreground_swap": 0.58,
    "tease": 0.52,
    "build_layers": 0.42,
    "breakdown": 0.34,
    "echo_out": 0.38,
    "drum_rebuild": 0.50,
    "sample_chop": 0.82,
    "layer_accumulation": 0.46,
}

_LIVE_OPERATOR_VELOCITY = {
    "blend": 0.96,
    "hard_cut": 1.04,
    "loop_extend": 0.98,
    "drop_to_floor": 1.02,
    "foreground_swap": 1.00,
    "tease": 0.82,
    "build_layers": 0.92,
    "breakdown": 0.74,
    "echo_out": 0.88,
    "drum_rebuild": 1.06,
    "sample_chop": 1.08,
    "layer_accumulation": 0.90,
}


def live_technique_names() -> list[str]:
    return list(LIVE_TECHNIQUE_NAMES)


def live_validate_technique_name(name: str) -> str:
    value = str(name or "").strip()
    if value not in LIVE_TECHNIQUE_NAMES:
        raise LiveError(f"unknown live technique {name!r}; choose one of {list(LIVE_TECHNIQUE_NAMES)}")
    return value


def live_pattern_layers(pattern: Mapping[str, Any]) -> list[dict[str, Any]]:
    pattern_id = str(pattern.get("pattern_id") or "")
    if not pattern_id:
        raise LiveError("live pattern requires pattern_id")
    out = []
    for slot in pattern.get("slots") or []:
        slot_id = str(slot.get("slot_id") or "")
        if not slot_id:
            raise LiveError(f"pattern {pattern_id} contains an empty slot ID")
        payload = {
            "pattern_id": pattern_id,
            "source_bar_index": int(pattern.get("source_bar_index") or 0),
            "slot_id": slot_id,
            "track_name": str(slot.get("track_name") or slot_id),
            "role": str(slot.get("role") or "other"),
            "category": str(slot.get("category") or "other"),
            "mode": str(slot.get("mode") or "pitched"),
            "program": int(slot.get("program") or 0),
        }
        payload["layer_id"] = "live_layer_" + midi_sha256_json(payload)[:24]
        out.append(payload)
    out.sort(key=lambda row: (str(row["category"]), str(row["role"]), str(row["slot_id"]), str(row["pattern_id"])))
    return out


def _live_layer_key(layer: Mapping[str, Any]) -> tuple[str, str]:
    return str(layer.get("category") or "other"), str(layer.get("slot_id") or "")


def _live_unique_layers(
    layers: Sequence[Mapping[str, Any]],
    *,
    category_priority: Sequence[str],
    maximum_layers: int,
) -> list[dict[str, Any]]:
    priorities = {str(category): index for index, category in enumerate(category_priority)}
    ordered = sorted(
        [deepcopy(dict(row)) for row in layers],
        key=lambda row: (
            priorities.get(str(row.get("category") or "other"), len(priorities)),
            str(row.get("category") or "other"),
            str(row.get("role") or "other"),
            str(row.get("pattern_id") or ""),
            str(row.get("slot_id") or ""),
        ),
    )
    selected = []
    seen_categories: set[str] = set()
    seen_slots: set[str] = set()
    for row in ordered:
        category = str(row.get("category") or "other")
        slot_id = str(row.get("slot_id") or "")
        if not slot_id or slot_id in seen_slots:
            continue
        if category in seen_categories and category not in {"foreground", "fx"}:
            continue
        selected.append(row)
        seen_slots.add(slot_id)
        seen_categories.add(category)
        if len(selected) >= int(maximum_layers):
            break
    return selected


def _live_layers_by_category(layers: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in layers:
        out.setdefault(str(row.get("category") or "other"), []).append(deepcopy(dict(row)))
    for rows in out.values():
        rows.sort(key=lambda item: (str(item.get("role") or ""), str(item.get("slot_id") or ""), str(item.get("pattern_id") or "")))
    return out


def _live_command(kind: str, technique: str, **payload: Any) -> dict[str, Any]:
    command = {"kind": str(kind), "technique": str(technique), **deepcopy(payload)}
    command["command_id"] = "live_command_" + midi_sha256_json(command)[:24]
    return command


def _live_result(
    technique: str,
    *,
    compatible: bool,
    failures: Sequence[str],
    layers: Sequence[Mapping[str, Any]],
    commands: Sequence[Mapping[str, Any]],
    energy_delta: float = 0.0,
) -> dict[str, Any]:
    selected = [deepcopy(dict(row)) for row in layers]
    return {
        "technique": technique,
        "compatible": bool(compatible),
        "failures": sorted(set(str(value) for value in failures)),
        "layers": selected,
        "layer_ids": [str(row["layer_id"]) for row in selected],
        "pattern_ids": sorted({str(row["pattern_id"]) for row in selected}),
        "categories": sorted({str(row["category"]) for row in selected}),
        "roles": sorted({str(row["role"]) for row in selected}),
        "commands": [deepcopy(dict(row)) for row in commands],
        "risk": float(_LIVE_OPERATOR_RISK[technique]),
        "velocity_scale": float(_LIVE_OPERATOR_VELOCITY[technique]),
        "energy_delta": float(energy_delta),
    }


def live_apply_technique(
    name: str,
    *,
    active_layers: Sequence[Mapping[str, Any]],
    candidate_pattern: Mapping[str, Any],
    maximum_layers: int,
    category_priority: Sequence[str],
) -> dict[str, Any]:
    technique = live_validate_technique_name(name)
    if not 1 <= int(maximum_layers) <= 16:
        raise LiveError("operator maximum_layers must be in [1,16]")
    active = [deepcopy(dict(row)) for row in active_layers]
    candidate = live_pattern_layers(candidate_pattern)
    active_by = _live_layers_by_category(active)
    candidate_by = _live_layers_by_category(candidate)
    failures: list[str] = []
    layers: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    energy_delta = 0.0

    if technique == "hard_cut":
        layers = _live_unique_layers(candidate, category_priority=category_priority, maximum_layers=maximum_layers)
        if not layers:
            failures.append("candidate_has_no_playable_layers")
        commands.append(_live_command("replace_all_layers", technique, pattern_id=str(candidate_pattern.get("pattern_id") or "")))
        energy_delta = 0.08
    elif technique == "loop_extend":
        layers = _live_unique_layers(active, category_priority=category_priority, maximum_layers=maximum_layers)
        if not layers:
            failures.append("no_active_layers_to_loop")
        commands.append(_live_command("repeat_active_layers", technique, repeat_bars=1))
    elif technique == "blend":
        retained = [*active_by.get("floor", []), *active_by.get("bass", []), *active_by.get("harmony", [])]
        additions = [*candidate_by.get("foreground", []), *candidate_by.get("fx", []), *candidate_by.get("harmony", [])]
        if not active:
            retained = candidate
        layers = _live_unique_layers([*retained, *additions], category_priority=category_priority, maximum_layers=maximum_layers)
        if len({str(row["pattern_id"]) for row in layers}) < 2 and active:
            failures.append("blend_requires_two_pattern_sources")
        commands.append(_live_command("crossfade_layers", technique, bars=1, equal_power=True))
    elif technique == "drop_to_floor":
        pool = [*active_by.get("floor", []), *candidate_by.get("floor", []), *active_by.get("bass", []), *candidate_by.get("bass", [])]
        layers = _live_unique_layers(pool, category_priority=["floor", "bass"], maximum_layers=min(maximum_layers, 2))
        if not any(str(row["category"]) == "floor" for row in layers):
            failures.append("drop_requires_floor_material")
        commands.append(_live_command("mute_categories", technique, categories=["harmony", "foreground", "fx"]))
        energy_delta = -0.12
    elif technique == "foreground_swap":
        base = [*active_by.get("floor", []), *active_by.get("bass", []), *active_by.get("harmony", [])]
        foreground = candidate_by.get("foreground", [])
        if not foreground:
            failures.append("candidate_has_no_foreground")
        if not base:
            failures.append("foreground_swap_requires_active_base")
        layers = _live_unique_layers([*base, *foreground], category_priority=category_priority, maximum_layers=maximum_layers)
        commands.append(_live_command("replace_category", technique, category="foreground", pattern_id=str(candidate_pattern.get("pattern_id") or "")))
        energy_delta = 0.04
    elif technique == "tease":
        base = [*active_by.get("floor", []), *active_by.get("bass", [])]
        tease = [*candidate_by.get("foreground", []), *candidate_by.get("fx", [])]
        if not base:
            failures.append("tease_requires_active_floor_or_bass")
        if not tease:
            failures.append("candidate_has_no_tease_material")
        layers = _live_unique_layers([*base, *tease], category_priority=["floor", "bass", "foreground", "fx"], maximum_layers=min(maximum_layers, 3))
        commands.append(_live_command("gate_new_layers", technique, duty_cycle=0.5, bars=1))
        energy_delta = 0.02
    elif technique == "build_layers":
        layers = _live_unique_layers([*active, *candidate], category_priority=category_priority, maximum_layers=maximum_layers)
        if len(layers) <= len(active):
            failures.append("build_layers_requires_an_added_layer")
        commands.append(_live_command("expression_ramp", technique, start=0.72, end=1.0, bars=1))
        energy_delta = 0.10
    elif technique == "breakdown":
        pool = [*candidate_by.get("harmony", []), *candidate_by.get("foreground", []), *candidate_by.get("fx", []), *active_by.get("harmony", [])]
        layers = _live_unique_layers(pool, category_priority=["harmony", "foreground", "fx"], maximum_layers=min(maximum_layers, 3))
        if not layers:
            failures.append("breakdown_requires_harmony_foreground_or_fx")
        commands.append(_live_command("mute_categories", technique, categories=["floor"]))
        commands.append(_live_command("expression_ramp", technique, start=0.88, end=0.68, bars=1))
        energy_delta = -0.22
    elif technique == "echo_out":
        layers = _live_unique_layers(active, category_priority=category_priority, maximum_layers=maximum_layers)
        if not layers:
            failures.append("echo_out_requires_active_layers")
        commands.append(_live_command("expression_ramp", technique, start=1.0, end=0.18, bars=1))
        commands.append(_live_command("tail_hold", technique, beats=1.0))
        energy_delta = -0.18
    elif technique == "drum_rebuild":
        drums = candidate_by.get("floor", [])
        support = [*active_by.get("bass", []), *active_by.get("harmony", [])]
        if not drums:
            failures.append("drum_rebuild_requires_candidate_floor")
        layers = _live_unique_layers([*drums, *support], category_priority=["floor", "bass", "harmony"], maximum_layers=maximum_layers)
        commands.append(_live_command("replace_category", technique, category="floor", pattern_id=str(candidate_pattern.get("pattern_id") or "")))
        energy_delta = 0.07
    elif technique == "sample_chop":
        chop = [*candidate_by.get("foreground", []), *candidate_by.get("fx", []), *candidate_by.get("harmony", [])]
        support = [*active_by.get("floor", []), *active_by.get("bass", [])]
        if not chop:
            failures.append("sample_chop_requires_foreground_fx_or_harmony")
        layers = _live_unique_layers([*support, *chop], category_priority=["floor", "bass", "foreground", "fx", "harmony"], maximum_layers=min(maximum_layers, 4))
        commands.append(_live_command("retrigger_subdivision", technique, subdivision=4, probability=1.0))
        energy_delta = 0.09
    elif technique == "layer_accumulation":
        layers = _live_unique_layers([*active, *candidate], category_priority=category_priority, maximum_layers=maximum_layers)
        if len(layers) <= len(active):
            failures.append("layer_accumulation_requires_an_added_layer")
        commands.append(_live_command("activate_missing_categories", technique, bars=1))
        commands.append(_live_command("expression_ramp", technique, start=0.80, end=1.0, bars=1))
        energy_delta = 0.08

    compatible = not failures and bool(layers)
    if compatible:
        layer_ids = [str(row["layer_id"]) for row in layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise LiveError(f"operator {technique} produced duplicate layers")
        if len(layers) > int(maximum_layers):
            raise LiveError(f"operator {technique} exceeded the layer limit")
    return _live_result(
        technique,
        compatible=compatible,
        failures=failures,
        layers=layers if compatible else [],
        commands=commands,
        energy_delta=energy_delta,
    )


def live_operator_capabilities(pattern: Mapping[str, Any]) -> list[str]:
    categories = {str(slot.get("category") or "other") for slot in pattern.get("slots") or []}
    out = {"hard_cut", "loop_extend", "echo_out"}
    if len(categories) >= 2:
        out.update({"blend", "build_layers", "layer_accumulation"})
    if "floor" in categories:
        out.update({"drop_to_floor", "drum_rebuild"})
    if "foreground" in categories:
        out.update({"foreground_swap", "tease", "sample_chop"})
    if "fx" in categories:
        out.update({"tease", "sample_chop"})
    if categories & {"harmony", "foreground", "fx"}:
        out.add("breakdown")
    return [name for name in LIVE_TECHNIQUE_NAMES if name in out]
