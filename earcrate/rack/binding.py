from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.model import midi_sha256_json, midi_validate_ledger
from earcrate.rack.demand import rack_compile_demands, rack_validate_demands
from earcrate.rack.model import RackError, rack_load_revision, rack_validate_revision

BINDING_SCHEMA_VERSION = 1
BINDING_KIND = "earcrate_rack_binding_plan"


def _rack_binding_mode_accepts(rack_mode: str, slot_mode: str) -> bool:
    return rack_mode == "hybrid" or rack_mode == slot_mode


def _rack_binding_matching_zones(rack: Mapping[str, Any], note: int, velocity: int) -> list[dict[str, Any]]:
    matches = []
    for zone in rack["zones"]:
        key_lo, key_hi = [int(value) for value in zone["key_range"]]
        vel_lo, vel_hi = [int(value) for value in zone["velocity_range"]]
        if key_lo <= int(note) <= key_hi and vel_lo <= int(velocity) <= vel_hi:
            matches.append(deepcopy(dict(zone)))
    matches.sort(
        key=lambda zone: (
            int(zone["key_range"][1]) - int(zone["key_range"][0]),
            int(zone["velocity_range"][1]) - int(zone["velocity_range"][0]),
            abs(int(note) - int(zone["root_key"])),
            str(zone["zone_id"]),
        )
    )
    return matches


def _rack_binding_playable_seconds(zone: Mapping[str, Any], note: int, pitch_bend_range_semitones: float) -> float:
    if str(zone["trigger_mode"]) == "one_shot" or bool((zone.get("loop") or {}).get("enabled")):
        return float("inf")
    sample = zone["sample"]
    semitones = (
        int(note)
        - int(zone["root_key"])
        + float(zone.get("tune_cents") or 0.0) / 100.0
        + float(pitch_bend_range_semitones)
    )
    fastest_ratio = 2.0 ** (semitones / 12.0)
    return int(sample["slice_frames"]) / float(sample["sample_rate"]) / fastest_ratio


def _rack_binding_zone_for_event(
    rack: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    pitch_bend_range_semitones: float,
) -> tuple[dict[str, Any] | None, str]:
    note = int(event["note"])
    velocity = int(event["velocity"])
    zones = _rack_binding_matching_zones(rack, note, velocity)
    if not zones:
        return None, "no_key_velocity_zone"
    duration = float(event["duration_seconds"])
    for zone in zones:
        if _rack_binding_playable_seconds(zone, note, pitch_bend_range_semitones) + 1e-9 >= duration:
            return zone, ""
    return None, "sample_cannot_cover_gate_duration"


def _rack_binding_track_tokens(value: str) -> set[str]:
    return {
        token
        for token in "".join(character.lower() if character.isalnum() else " " for character in value).split()
        if len(token) >= 3
    }


def _rack_binding_candidate_score(slot: Mapping[str, Any], rack: Mapping[str, Any], event_bindings: list[dict[str, Any]]) -> float:
    tags = {str(tag).lower() for tag in (rack.get("metadata") or {}).get("tags") or []}
    score = 100.0
    if str(slot.get("role_hint") or "").lower() in tags:
        score += 30.0
    if str(slot.get("gm_family") or "").lower() in tags:
        score += 18.0
    score += 3.0 * len(_rack_binding_track_tokens(str(slot.get("track_name") or "")) & tags)
    if event_bindings:
        average_transpose = sum(abs(float(row["transpose_semitones"])) for row in event_bindings) / len(event_bindings)
        score += max(0.0, 12.0 - average_transpose)
        exact_roots = sum(abs(float(row["transpose_semitones"])) < 1e-9 for row in event_bindings)
        score += 6.0 * exact_roots / len(event_bindings)
    score -= 0.01 * len(rack["zones"])
    return round(score, 9)


def _rack_binding_candidate_receipt(
    slot: Mapping[str, Any],
    rack: Mapping[str, Any],
    *,
    pitch_bend_range_semitones: float,
) -> dict[str, Any]:
    if not _rack_binding_mode_accepts(str(rack["mode"]), str(slot["mode"])):
        return {
            "rack_id": rack["rack_id"],
            "rack_sha256": rack["rack_sha256"],
            "compatible": False,
            "score": None,
            "failure": "rack_mode_mismatch",
            "missing_events": [event["event_id"] for event in slot["events"]],
            "event_bindings": [],
        }
    bindings = []
    missing = []
    failures = []
    for event in slot["events"]:
        zone, failure = _rack_binding_zone_for_event(
            rack,
            event,
            pitch_bend_range_semitones=pitch_bend_range_semitones,
        )
        if zone is None:
            missing.append(str(event["event_id"]))
            failures.append({"event_id": event["event_id"], "reason": failure})
            continue
        transpose = int(event["note"]) - int(zone["root_key"]) + float(zone.get("tune_cents") or 0.0) / 100.0
        bindings.append(
            {
                "event_id": str(event["event_id"]),
                "slot_id": str(slot["slot_id"]),
                "rack_id": str(rack["rack_id"]),
                "rack_sha256": str(rack["rack_sha256"]),
                "zone_id": str(zone["zone_id"]),
                "note": int(event["note"]),
                "velocity": int(event["velocity"]),
                "transpose_semitones": round(float(transpose), 9),
                "trigger_mode": str(zone["trigger_mode"]),
                "source_path": str(zone["sample"]["path"]),
                "source_byte_sha256": str(zone["sample"]["byte_sha256"]),
                "source_slice_pcm_sha256": str(zone["sample"]["slice_pcm_sha256"]),
            }
        )
    compatible = not missing
    return {
        "rack_id": rack["rack_id"],
        "rack_sha256": rack["rack_sha256"],
        "compatible": compatible,
        "score": _rack_binding_candidate_score(slot, rack, bindings) if compatible else None,
        "failure": "" if compatible else "event_coverage_incomplete",
        "missing_events": missing,
        "failures": failures,
        "event_bindings": bindings,
    }


def rack_compile_binding(
    ledger: Mapping[str, Any],
    racks: Sequence[Mapping[str, Any]],
    *,
    assignments: Mapping[str, str] | None = None,
    pitch_bend_range_semitones: float = 2.0,
) -> dict[str, Any]:
    """Bind every MIDI event to one exact rack zone, or preserve the refusal."""
    midi_validate_ledger(ledger)
    if not racks:
        raise RackError("at least one rack revision is required")
    normalized = [deepcopy(dict(rack)) for rack in racks]
    for rack in normalized:
        rack_validate_revision(rack)
    ids = [str(rack["rack_id"]) for rack in normalized]
    shas = [str(rack["rack_sha256"]) for rack in normalized]
    if len(ids) != len(set(ids)):
        raise RackError("rack_id values must be unique within one binding compile")
    if len(shas) != len(set(shas)):
        raise RackError("rack_sha256 values must be unique within one binding compile")

    demand = rack_compile_demands(
        ledger,
        pitch_bend_range_semitones=pitch_bend_range_semitones,
    )
    rack_by_key = {str(rack["rack_id"]): rack for rack in normalized}
    rack_by_key.update({str(rack["rack_sha256"]): rack for rack in normalized})
    explicit = {str(key): str(value) for key, value in dict(assignments or {}).items()}
    slot_bindings = []
    event_bindings = []
    unresolved = []

    for slot in demand["slots"]:
        receipts = [
            _rack_binding_candidate_receipt(
                slot,
                rack,
                pitch_bend_range_semitones=float(demand["pitch_bend_range_semitones"]),
            )
            for rack in normalized
        ]
        chosen = None
        requested = explicit.get(str(slot["slot_id"]))
        if requested:
            assigned_rack = rack_by_key.get(requested)
            if assigned_rack is None:
                unresolved.append(
                    {
                        "slot_id": slot["slot_id"],
                        "reason": "explicit_rack_not_found",
                        "requested": requested,
                        "candidates": [
                            {key: value for key, value in row.items() if key != "event_bindings"}
                            for row in receipts
                        ],
                    }
                )
                continue
            chosen = next(row for row in receipts if row["rack_sha256"] == assigned_rack["rack_sha256"])
            if not chosen["compatible"]:
                unresolved.append(
                    {
                        "slot_id": slot["slot_id"],
                        "reason": "explicit_rack_incompatible",
                        "requested": requested,
                        "candidates": [
                            {key: value for key, value in row.items() if key != "event_bindings"}
                            for row in receipts
                        ],
                    }
                )
                continue
        else:
            compatible = [row for row in receipts if row["compatible"]]
            compatible.sort(key=lambda row: (-float(row["score"]), str(row["rack_sha256"])))
            chosen = compatible[0] if compatible else None
            if chosen is None:
                unresolved.append(
                    {
                        "slot_id": slot["slot_id"],
                        "reason": "no_compatible_rack",
                        "requested": None,
                        "candidates": [
                            {key: value for key, value in row.items() if key != "event_bindings"}
                            for row in receipts
                        ],
                    }
                )
                continue

        slot_bindings.append(
            {
                "slot_id": slot["slot_id"],
                "track_index": slot["track_index"],
                "track_name": slot["track_name"],
                "channel": slot["channel"],
                "program": slot["program"],
                "role_hint": slot["role_hint"],
                "rack_id": chosen["rack_id"],
                "rack_sha256": chosen["rack_sha256"],
                "candidate_score": chosen["score"],
                "candidate_receipts": [
                    {key: value for key, value in row.items() if key != "event_bindings"}
                    for row in receipts
                ],
            }
        )
        event_bindings.extend(chosen["event_bindings"])

    event_bindings.sort(key=lambda row: str(row["event_id"]))
    selected_ids = {
        str(event["event_id"])
        for slot in demand["slots"]
        for event in slot["events"]
    }
    bound_ids = {str(row["event_id"]) for row in event_bindings}
    if selected_ids - bound_ids:
        already = {str(row["slot_id"]) for row in unresolved}
        for slot in demand["slots"]:
            slot_event_ids = {str(event["event_id"]) for event in slot["events"]}
            if slot_event_ids & (selected_ids - bound_ids) and str(slot["slot_id"]) not in already:
                unresolved.append({"slot_id": slot["slot_id"], "reason": "events_unbound", "requested": None, "candidates": []})

    plan = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "kind": BINDING_KIND,
        "semantic_sha256": ledger["semantic_sha256"],
        "demand_sha256": demand["demand_sha256"],
        "pitch_bend_range_semitones": float(demand["pitch_bend_range_semitones"]),
        "rack_revisions": [
            {
                "rack_id": rack["rack_id"],
                "rack_sha256": rack["rack_sha256"],
                "name": rack["name"],
                "mode": rack["mode"],
            }
            for rack in sorted(normalized, key=lambda rack: str(rack["rack_sha256"]))
        ],
        "selected_event_count": int(demand["selected_event_count"]),
        "bound_event_count": len(event_bindings),
        "complete": not unresolved and len(event_bindings) == int(demand["selected_event_count"]),
        "slot_bindings": slot_bindings,
        "event_bindings": event_bindings,
        "unresolved": unresolved,
        "demand": demand,
    }
    plan["binding_sha256"] = midi_sha256_json(plan)
    rack_validate_binding(plan, ledger=ledger, racks=normalized)
    return plan


def rack_validate_binding(
    plan: Mapping[str, Any],
    *,
    ledger: Mapping[str, Any] | None = None,
    racks: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    if int(plan.get("schema_version") or 0) != BINDING_SCHEMA_VERSION:
        raise RackError(f"unsupported binding schema: {plan.get('schema_version')}")
    if str(plan.get("kind") or "") != BINDING_KIND:
        raise RackError(f"unsupported binding kind: {plan.get('kind')}")
    demand = plan.get("demand") or {}
    rack_validate_demands(demand)
    if str(plan.get("semantic_sha256") or "") != str(demand.get("semantic_sha256") or ""):
        raise RackError("binding and demand semantic identities disagree")
    if str(plan.get("demand_sha256") or "") != str(demand.get("demand_sha256") or ""):
        raise RackError("binding demand_sha256 does not match embedded demand")
    event_bindings = plan.get("event_bindings")
    if not isinstance(event_bindings, list):
        raise RackError("binding event_bindings must be a list")
    event_ids = [str(row.get("event_id") or "") for row in event_bindings]
    if any(not value for value in event_ids) or len(event_ids) != len(set(event_ids)):
        raise RackError("binding event_ids must be unique and nonempty")
    if len(event_bindings) != int(plan.get("bound_event_count") or 0):
        raise RackError("bound_event_count does not match event_bindings")
    complete = bool(plan.get("complete"))
    if complete:
        if plan.get("unresolved"):
            raise RackError("complete binding cannot contain unresolved slots")
        if int(plan.get("selected_event_count") or 0) != len(event_bindings):
            raise RackError("complete binding must account for every selected event")
    if ledger is not None:
        midi_validate_ledger(ledger)
        if str(ledger["semantic_sha256"]) != str(plan["semantic_sha256"]):
            raise RackError("binding plan was compiled for a different MIDI ledger")
    if racks is not None:
        available = {}
        for rack in racks:
            rack_validate_revision(rack)
            available[str(rack["rack_sha256"])] = rack
        declared = {str(row["rack_sha256"]) for row in plan.get("rack_revisions") or []}
        if not declared <= set(available):
            raise RackError("binding plan references unavailable rack revisions")
        for row in event_bindings:
            rack = available.get(str(row["rack_sha256"]))
            if rack is None:
                raise RackError(f"event {row['event_id']} references an unavailable rack")
            zones = {str(zone["zone_id"]): zone for zone in rack["zones"]}
            zone = zones.get(str(row["zone_id"]))
            if zone is None:
                raise RackError(f"event {row['event_id']} references a missing rack zone")
            if str(zone["sample"]["slice_pcm_sha256"]) != str(row["source_slice_pcm_sha256"]):
                raise RackError(f"event {row['event_id']} source slice identity changed")
    expected = midi_sha256_json({key: value for key, value in plan.items() if key != "binding_sha256"})
    if str(plan.get("binding_sha256") or "") != expected:
        raise RackError("binding_sha256 does not match binding contents")


def rack_load_binding(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    rack_validate_binding(value)
    return value


def rack_load_many(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    return [rack_load_revision(path) for path in paths]
