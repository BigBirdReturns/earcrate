from earcrate.core.deps import *
from earcrate.core.util import now_utc, ulidish
from earcrate.project.model import ScoreRevision, ProjectValidationError
from earcrate.project.policy import compile_taste_policy, policy_gain_bounds
from earcrate.project.bridge import revision_from_arrangement, rail_for_layer


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _lock_matches(lock: Dict[str, Any], target_type: str, target_id: str) -> bool:
    return str(lock.get("target_type") or "") == target_type and str(lock.get("target_id") or "") == target_id


def _locked(revision: ScoreRevision, target_type: str, target_id: str) -> bool:
    return any(_lock_matches(lock, target_type, target_id) for lock in revision.locks)


def _find_layer(arrangement: Dict[str, Any], clip_id: str) -> Tuple[int, int, Dict[str, Any]]:
    for section_index, section in enumerate(arrangement.get("sections") or []):
        for layer_index, layer in enumerate(section.get("layers") or []):
            if str(layer.get("clip_id") or "") == str(clip_id):
                return section_index, layer_index, layer
    raise ProjectValidationError(f"clip not found: {clip_id}")


def _find_transition(arrangement: Dict[str, Any], transition_id: str) -> Tuple[int, Dict[str, Any]]:
    for section_index, section in enumerate(arrangement.get("sections") or []):
        transition = dict(section.get("transition_in") or {})
        if str(transition.get("transition_id") or "") == str(transition_id):
            return section_index, transition
    raise ProjectValidationError(f"transition not found: {transition_id}")


def _validate_gates(core: Any, arrangement: Dict[str, Any], override: bool) -> Dict[str, Any]:
    preflight = core.arrangement_preflight_gate(arrangement)
    taste = core.taste_arrangement_gate(arrangement)
    failures = list(preflight.get("failures") or []) + list(taste.get("failures") or [])
    if failures and not override:
        raise ProjectValidationError("edit violates the active TasteSpec: " + "; ".join(failures))
    return {
        "passed": not failures,
        "override": bool(override and failures),
        "failures": failures,
        "warnings": list(preflight.get("warnings") or []) + list(taste.get("warnings") or []),
        "preflight": preflight,
        "taste_gate": taste,
    }


def apply_project_command(core: Any, revision: ScoreRevision, command: Dict[str, Any]) -> ScoreRevision:
    revision.validate()
    kind = str(command.get("kind") or "")
    actor = str(command.get("actor") or "human")
    payload = dict(command.get("payload") or {})
    if not kind:
        raise ProjectValidationError("command kind is required")
    arrangement = _copy(revision.arrangement)
    locks = _copy(revision.locks)
    master_actions = _copy(revision.master_actions)
    override = bool(payload.get("override_policy"))
    target: Optional[str] = None

    if kind == "lock":
        target_type = str(payload.get("target_type") or "")
        target = str(payload.get("target_id") or "")
        if target_type not in {"clip", "transition", "master_action", "source"} or not target:
            raise ProjectValidationError("lock requires target_type and target_id")
        if not any(_lock_matches(lock, target_type, target) for lock in locks):
            locks.append({
                "target_type": target_type, "target_id": target,
                "actor": actor, "reason": str(payload.get("reason") or ""),
                "created_at": now_utc(),
            })
    elif kind == "unlock":
        target_type = str(payload.get("target_type") or "")
        target = str(payload.get("target_id") or "")
        locks = [lock for lock in locks if not _lock_matches(lock, target_type, target)]
    elif kind in {"set_gain", "set_pan", "mute_clip", "solo_clip", "trim_clip", "move_clip", "replace_clip", "set_stem"}:
        target = str(payload.get("clip_id") or "")
        if not target:
            raise ProjectValidationError(f"{kind} requires clip_id")
        if _locked(revision, "clip", target):
            raise ProjectValidationError(f"human lock prevents mutation of clip {target}")
        section_index, layer_index, layer = _find_layer(arrangement, target)
        if kind == "set_gain":
            value = float(payload["gain_db"])
            rail = rail_for_layer(layer)
            lo, _target, hi = policy_gain_bounds(compile_taste_policy(str(revision.intent["taste_profile"]["id"])), rail)
            if not lo <= value <= hi and not override:
                raise ProjectValidationError(f"gain {value:.2f} dB outside persona range [{lo:.2f},{hi:.2f}] for {rail}")
            layer["gain_db"] = value
        elif kind == "set_pan":
            value = float(payload["pan"])
            maximum = float((revision.intent.get("compiled_policy") or {}).get("mix_policy", {}).get("pan_max_abs", 0.35))
            if abs(value) > maximum and not override:
                raise ProjectValidationError(f"pan {value:.3f} outside persona maximum +/-{maximum:.3f}")
            layer["pan"] = max(-1.0, min(1.0, value))
        elif kind == "mute_clip":
            layer["muted"] = bool(payload.get("muted", True))
        elif kind == "solo_clip":
            layer["solo"] = bool(payload.get("solo", True))
        elif kind == "trim_clip":
            start_s = float(payload.get("source_start_s", layer.get("source_start_s") or 0.0))
            end_s = float(payload.get("source_end_s", layer.get("source_end_s") or 0.0))
            if end_s <= start_s:
                raise ProjectValidationError("trim would make the source window empty")
            layer["source_start_s"] = start_s
            layer["source_end_s"] = end_s
            if layer.get("external_ref"):
                ref = dict(layer["external_ref"])
                ref["start_s"] = start_s
                ref["len_s"] = end_s - start_s
                layer["external_ref"] = ref
            if "bar_len" in payload:
                bars = int(payload["bar_len"])
                if bars <= 0:
                    raise ProjectValidationError("bar_len must be positive")
                layer["bar_len"] = bars
        elif kind == "move_clip":
            new_section_index = int(payload.get("section_index", section_index))
            sections = arrangement.get("sections") or []
            if not 0 <= new_section_index < len(sections):
                raise ProjectValidationError("move target section is out of range")
            new_offset = int(payload.get("bar_offset", layer.get("bar_offset") or 0))
            if new_offset < 0 or new_offset >= int(sections[new_section_index].get("bars") or 0):
                raise ProjectValidationError("bar_offset is outside the target section")
            if new_section_index != section_index:
                moved = sections[section_index]["layers"].pop(layer_index)
                moved["bar_offset"] = new_offset
                sections[new_section_index].setdefault("layers", []).append(moved)
            else:
                layer["bar_offset"] = new_offset
        elif kind == "replace_clip":
            replacement = dict(payload.get("replacement") or {})
            if not replacement:
                raise ProjectValidationError("replace_clip requires a replacement object")
            keep = {"clip_id": target, "bar_offset": layer.get("bar_offset", 0), "bar_len": layer.get("bar_len"),
                    "gain_db": layer.get("gain_db", 0.0), "pan": layer.get("pan", 0.0),
                    "muted": layer.get("muted", False), "solo": layer.get("solo", False)}
            replacement.update({k: v for k, v in keep.items() if k not in replacement})
            arrangement["sections"][section_index]["layers"][layer_index] = replacement
        elif kind == "set_stem":
            choice = str(payload.get("stem") or "")
            if choice not in {"mix", "vocals", "no_vocals", "drums", "bass", "other", "external_target"}:
                raise ProjectValidationError(f"unsupported stem choice: {choice}")
            layer["stem_choice"] = {"choice": choice, "provider": "human", "reason": "explicit project command"}
    elif kind == "set_transition":
        target = str(payload.get("transition_id") or "")
        if not target:
            raise ProjectValidationError("set_transition requires transition_id")
        if _locked(revision, "transition", target):
            raise ProjectValidationError(f"human lock prevents mutation of transition {target}")
        section_index, current = _find_transition(arrangement, target)
        current.update({k: v for k, v in payload.items() if k not in {"transition_id", "override_policy"}})
        current["transition_id"] = target
        arrangement["sections"][section_index]["transition_in"] = current
    elif kind == "set_master_action":
        target = str(payload.get("action_id") or "")
        if _locked(revision, "master_action", target):
            raise ProjectValidationError(f"human lock prevents mutation of master action {target}")
        found = False
        for action in master_actions:
            if str(action.get("action_id") or "") == target:
                action["parameters"] = {**dict(action.get("parameters") or {}), **dict(payload.get("parameters") or {})}
                action.setdefault("evidence", {})["human_override"] = True
                found = True
                break
        if not found:
            raise ProjectValidationError(f"master action not found: {target}")
    else:
        raise ProjectValidationError(f"unsupported project command: {kind}")

    gate = _validate_gates(core, arrangement, override) if kind not in {"lock", "unlock", "set_master_action"} else revision.static_gate_receipt
    child = revision_from_arrangement(
        core,
        arrangement,
        project_id=revision.project_id,
        parent_revision_sha=revision.revision_sha,
        created_by={"actor": actor, "reason": kind, "command_id": str(command.get("command_id") or ulidish())},
        locks=locks,
        master_actions=master_actions,
        static_gate_receipt=gate if isinstance(gate, dict) else {},
        compiler_receipt={
            "last_command": {"kind": kind, "target": target, "actor": actor, "override_policy": override},
            "parent_compiler_receipt": revision.compiler_receipt,
        },
    )
    return child
