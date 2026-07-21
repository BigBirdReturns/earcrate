from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from earcrate.analyze.decode import decode_audio
from earcrate.midi.model import midi_sha256_json, midi_validate_ledger
from earcrate.midi.render import _atomic_wav
from earcrate.rack.binding import rack_compile_binding
from earcrate.rack.demand import rack_compile_demands, rack_validate_demands
from earcrate.rack.model import (
    RackError,
    rack_atomic_json,
    rack_seal_draft,
    rack_sha256_file,
)
from earcrate.rack.sfz import rack_compile_sfz

LIBRARY_PROPOSAL_SCHEMA_VERSION = 1
LIBRARY_PROPOSAL_KIND = "earcrate_library_rack_proposal"
LIBRARY_BUILD_SCHEMA_VERSION = 1

_ROLE_COMPATIBILITY: dict[str, dict[str, float]] = {
    "drums": {"DRUM_BREAK": 1.0, "PICKUP_FILL": 0.84, "DROP_HIT": 0.80, "TRANSITION_TAIL": 0.58, "TEXTURE": 0.38},
    "kick": {"DRUM_BREAK": 1.0, "DROP_HIT": 0.82, "BASS_RIFF": 0.42, "PICKUP_FILL": 0.38},
    "snare": {"DRUM_BREAK": 1.0, "PICKUP_FILL": 0.74, "DROP_HIT": 0.62, "TEXTURE": 0.34},
    "hats": {"DRUM_BREAK": 0.92, "PICKUP_FILL": 0.82, "TEXTURE": 0.60, "TRANSITION_TAIL": 0.46},
    "percussion": {"DRUM_BREAK": 1.0, "PICKUP_FILL": 0.86, "DROP_HIT": 0.72, "TEXTURE": 0.50},
    "bass": {"BASS_RIFF": 1.0, "BED_CHORD": 0.34, "RIFF_ID": 0.30, "DRUM_BREAK": 0.24},
    "piano": {"BED_CHORD": 1.0, "RIFF_ID": 0.86, "TEXTURE": 0.58, "BASS_RIFF": 0.26},
    "chromatic_percussion": {"RIFF_ID": 0.92, "DROP_HIT": 0.78, "TEXTURE": 0.66, "BED_CHORD": 0.54},
    "organ": {"BED_CHORD": 1.0, "RIFF_ID": 0.78, "TEXTURE": 0.62},
    "guitar": {"RIFF_ID": 1.0, "BED_CHORD": 0.88, "TEXTURE": 0.48},
    "strings": {"BED_CHORD": 1.0, "TEXTURE": 0.82, "RIFF_ID": 0.60},
    "ensemble": {"BED_CHORD": 0.96, "TEXTURE": 0.84, "RIFF_ID": 0.62, "VOX_HOOK": 0.44},
    "choir": {"VOX_HOOK": 1.0, "VOX_VERSE": 0.92, "VOX_SHOUT": 0.84, "BED_CHORD": 0.64, "TEXTURE": 0.58},
    "vocal": {"VOX_HOOK": 1.0, "VOX_VERSE": 0.94, "VOX_SHOUT": 0.86, "RIFF_ID": 0.58},
    "brass": {"RIFF_ID": 0.94, "DROP_HIT": 0.88, "BED_CHORD": 0.70, "TEXTURE": 0.44},
    "reed": {"RIFF_ID": 1.0, "BED_CHORD": 0.66, "TEXTURE": 0.46},
    "pipe": {"RIFF_ID": 0.96, "BED_CHORD": 0.70, "TEXTURE": 0.52},
    "lead": {"RIFF_ID": 1.0, "VOX_HOOK": 0.70, "TEXTURE": 0.58, "DROP_HIT": 0.50},
    "synth_lead": {"RIFF_ID": 1.0, "TEXTURE": 0.68, "DROP_HIT": 0.56},
    "pad": {"BED_CHORD": 1.0, "TEXTURE": 0.90, "TRANSITION_TAIL": 0.48},
    "synth_pad": {"BED_CHORD": 1.0, "TEXTURE": 0.92, "TRANSITION_TAIL": 0.50},
    "synth_fx": {"TEXTURE": 1.0, "TRANSITION_TAIL": 0.94, "DROP_HIT": 0.84, "PICKUP_FILL": 0.72},
    "sound_fx": {"DROP_HIT": 1.0, "TRANSITION_TAIL": 0.94, "PICKUP_FILL": 0.84, "TEXTURE": 0.72},
    "ethnic": {"RIFF_ID": 0.88, "BED_CHORD": 0.72, "TEXTURE": 0.68},
    "percussive": {"DRUM_BREAK": 0.90, "DROP_HIT": 0.84, "PICKUP_FILL": 0.78, "TEXTURE": 0.60},
}

_RENDER_ROLE_FALLBACK = {
    "drum_anchor": "DRUM_BREAK",
    "bass": "BASS_RIFF",
    "harmony": "BED_CHORD",
    "vocal": "VOX_HOOK",
    "texture": "TEXTURE",
    "fx": "DROP_HIT",
    "full": "RIFF_ID",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _stable_text(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._") or "material"


def _atom_identity(atom: Mapping[str, Any]) -> str:
    value = str(atom.get("atom_id") or atom.get("id") or atom.get("loop_id") or "").strip()
    if value:
        return value
    return "atom_" + midi_sha256_json(
        {
            "path": str(atom.get("path") or ""),
            "start_s": _number(atom.get("start_s")),
            "end_s": _number(atom.get("end_s")),
            "ear_role": str(atom.get("ear_role") or ""),
        }
    )[:20]


def _normalize_atom(atom: Mapping[str, Any]) -> dict[str, Any] | None:
    path = str(atom.get("stem_path") or atom.get("path") or "").strip()
    start = _number(atom.get("stem_start_s", atom.get("start_s")), 0.0)
    end = _number(atom.get("stem_end_s", atom.get("end_s")), 0.0)
    if not path or end <= start or start < 0:
        return None
    atom_status = str(atom.get("atom_status") or "approved").lower()
    if atom_status != "approved":
        return None
    ear_role = str(atom.get("ear_role") or "").upper()
    render_role = str(atom.get("render_role") or atom.get("role") or "full").lower()
    if not ear_role:
        ear_role = _RENDER_ROLE_FALLBACK.get(render_role, "RIFF_ID")
    metrics = atom.get("metrics_json")
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}
    metrics = dict(metrics or {})
    return {
        "atom_id": _atom_identity(atom),
        "loop_id": str(atom.get("loop_id") or atom.get("id") or ""),
        "file_id": str(atom.get("file_id") or ""),
        "path": path,
        "start_s": round(start, 9),
        "end_s": round(end, 9),
        "duration_s": round(end - start, 9),
        "ear_role": ear_role,
        "render_role": render_role,
        "key_root": int(_number(atom.get("key_root"), 0.0)) % 12,
        "key_known": atom.get("key_root") not in {None, ""},
        "bpm": _number(atom.get("bpm"), 0.0),
        "score": max(0.0, min(1.0, _number(atom.get("atom_score", atom.get("score")), 0.0))),
        "hook_score": max(0.0, min(1.0, _number(atom.get("hook_score", metrics.get("hook_score")), 0.0))),
        "bed_score": max(0.0, min(1.0, _number(atom.get("bed_score", metrics.get("bed_score")), 0.0))),
        "floor_score": max(0.0, min(1.0, _number(atom.get("floor_score", metrics.get("floor_score")), 0.0))),
        "bass_score": max(0.0, min(1.0, _number(atom.get("bass_score", metrics.get("bass_score")), 0.0))),
        "spark_score": max(0.0, min(1.0, _number(atom.get("spark_score", metrics.get("spark_score")), 0.0))),
        "intelligibility": max(0.0, min(1.0, _number(atom.get("intelligibility", metrics.get("intelligibility")), 0.0))),
        "low_share": max(0.0, min(1.0, _number(atom.get("low_share", metrics.get("low_share")), 0.0))),
        "mid_share": max(0.0, min(1.0, _number(atom.get("mid_share", metrics.get("mid_share")), 0.0))),
        "high_share": max(0.0, min(1.0, _number(atom.get("high_share", metrics.get("high_share")), 0.0))),
        "loopability": max(0.0, min(1.0, _number(atom.get("loopability", metrics.get("loopability")), 0.0))),
        "transient_density": max(0.0, min(1.0, _number(atom.get("transient_density", metrics.get("transient_density")), 0.0))),
        "source_audio_sha256": str(atom.get("source_audio_sha256") or atom.get("audio_sha256") or ""),
        "source_generation": int(_number(atom.get("source_audio_generation", atom.get("audio_generation")), 0.0)),
        "artist": str(atom.get("artist") or ""),
        "title": str(atom.get("title") or ""),
        "taste_profile": str(atom.get("taste_profile") or ""),
    }


def _slot_role(slot: Mapping[str, Any]) -> str:
    role = str(slot.get("role_hint") or slot.get("gm_family") or "").lower()
    if role in _ROLE_COMPATIBILITY:
        return role
    family = str(slot.get("gm_family") or "").lower()
    return family if family in _ROLE_COMPATIBILITY else ("drums" if slot.get("mode") == "trigger" else "lead")


def _role_fit(slot: Mapping[str, Any], atom: Mapping[str, Any]) -> float:
    role = _slot_role(slot)
    fit = _ROLE_COMPATIBILITY.get(role, {}).get(str(atom["ear_role"]), 0.0)
    if fit == 0.0:
        render_role = str(atom.get("render_role") or "")
        aliases = {
            "drums": {"drum_anchor": 0.82, "fx": 0.32},
            "bass": {"bass": 0.92, "harmony": 0.24},
            "vocal": {"vocal": 0.90},
            "choir": {"vocal": 0.82, "harmony": 0.42},
            "sound_fx": {"fx": 0.88, "texture": 0.68},
        }
        fit = aliases.get(role, {}).get(render_role, 0.20 if render_role == "full" else 0.0)
    return float(fit)


def _nearest_root(key_root: int, center_note: int) -> int:
    candidates = [note for note in range(128) if note % 12 == int(key_root) % 12]
    return min(candidates, key=lambda note: (abs(note - int(center_note)), note))


def _trigger_spectral_fit(note: int, atom: Mapping[str, Any]) -> float:
    note_value = int(note)
    transient = float(atom["transient_density"])
    if note_value in {35, 36}:
        return min(1.0, 0.55 * float(atom["low_share"]) / 0.35 + 0.25 * float(atom["floor_score"]) + 0.20 * transient)
    if note_value in {38, 39, 40}:
        return min(1.0, 0.46 * float(atom["mid_share"]) / 0.50 + 0.34 * transient + 0.20 * float(atom["floor_score"]))
    if note_value in {42, 44, 46, 49, 51, 52, 53, 55, 57, 59}:
        return min(1.0, 0.55 * float(atom["high_share"]) / 0.30 + 0.35 * transient + 0.10 * float(atom["spark_score"]))
    return min(1.0, 0.52 * transient + 0.28 * float(atom["spark_score"]) + 0.20 * float(atom["floor_score"]))


def _pitched_timbre_fit(role: str, atom: Mapping[str, Any]) -> float:
    if role == "bass":
        return min(1.0, 0.62 * float(atom["bass_score"]) + 0.38 * float(atom["low_share"]) / 0.35)
    if role in {"piano", "organ", "guitar", "strings", "ensemble", "pad", "synth_pad"}:
        return min(1.0, 0.58 * float(atom["bed_score"]) + 0.22 * float(atom["mid_share"]) / 0.55 + 0.20 * float(atom["loopability"]))
    if role in {"vocal", "choir"}:
        return min(1.0, 0.55 * float(atom["hook_score"]) + 0.30 * float(atom["intelligibility"]) + 0.15 * float(atom["mid_share"]) / 0.55)
    return min(1.0, 0.52 * max(float(atom["hook_score"]), float(atom["spark_score"])) + 0.28 * float(atom["mid_share"]) / 0.55 + 0.20 * float(atom["score"]))


def _candidate_receipt(
    slot: Mapping[str, Any],
    atom: Mapping[str, Any],
    *,
    note: int | None,
    maximum_transpose_semitones: float,
    loopability_threshold: float,
) -> dict[str, Any]:
    role_fit = _role_fit(slot, atom)
    role = _slot_role(slot)
    center = int(note if note is not None else round((int(slot["minimum_note"]) + int(slot["maximum_note"])) / 2.0))
    root_key = center if slot["mode"] == "trigger" else (_nearest_root(int(atom["key_root"]), center) if atom["key_known"] else center)
    max_transpose = max(abs(int(slot["minimum_note"]) - root_key), abs(int(slot["maximum_note"]) - root_key))
    required_duration = max(float(row["maximum_duration_seconds"]) for row in slot["note_requirements"] if note is None or int(row["note"]) == int(note))
    slowest_ratio = 2.0 ** (-float(max_transpose) / 12.0)
    unlooped_coverage = float(atom["duration_s"]) / max(1e-6, slowest_ratio)
    loop_required = slot["mode"] == "pitched" and required_duration > unlooped_coverage + 1e-6
    duration_fit = 1.0 if not loop_required else min(1.0, float(atom["loopability"]) / max(1e-6, loopability_threshold))
    hard_failures = []
    if role_fit < 0.24:
        hard_failures.append("role_incompatible")
    if slot["mode"] == "pitched" and max_transpose > float(maximum_transpose_semitones):
        hard_failures.append("transpose_budget_exceeded")
    if loop_required and float(atom["loopability"]) < float(loopability_threshold):
        hard_failures.append("insufficient_duration_and_loopability")
    if slot["mode"] == "trigger" and atom["duration_s"] > 32.0:
        hard_failures.append("trigger_region_too_long")
    quality = float(atom["score"])
    timbre = _trigger_spectral_fit(int(note), atom) if slot["mode"] == "trigger" and note is not None else _pitched_timbre_fit(role, atom)
    key_fit = 1.0 if slot["mode"] == "trigger" else max(0.0, 1.0 - max_transpose / max(1.0, float(maximum_transpose_semitones)))
    score = 0.36 * role_fit + 0.20 * quality + 0.18 * timbre + 0.12 * duration_fit + 0.10 * key_fit + 0.04 * float(atom["loopability"])
    return {
        "atom_id": atom["atom_id"],
        "compatible": not hard_failures,
        "hard_failures": hard_failures,
        "score": round(score, 9) if not hard_failures else None,
        "root_key": root_key,
        "maximum_transpose_semitones": max_transpose,
        "required_duration_seconds": round(required_duration, 9),
        "unlooped_coverage_seconds": round(unlooped_coverage, 9),
        "loop_required": loop_required,
        "score_terms": {
            "role_fit": round(role_fit, 9),
            "quality": round(quality, 9),
            "timbre_fit": round(timbre, 9),
            "duration_fit": round(duration_fit, 9),
            "key_fit": round(key_fit, 9),
            "loopability": round(float(atom["loopability"]), 9),
        },
        "source": deepcopy(dict(atom)),
    }


def rack_propose_from_atoms(
    demand: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    *,
    taste_profile: str = "",
    top_k: int = 8,
    maximum_transpose_semitones: float = 18.0,
    loopability_threshold: float = 0.58,
) -> dict[str, Any]:
    """Rank approved EarAtoms against exact performance slots without writing audio."""
    rack_validate_demands(demand)
    if top_k <= 0:
        raise RackError("top_k must be positive")
    normalized = [value for value in (_normalize_atom(atom) for atom in atoms) if value is not None]
    normalized.sort(key=lambda atom: str(atom["atom_id"]))
    if not normalized:
        raise RackError("no usable approved EarAtoms were supplied")
    atom_pool_sha256 = midi_sha256_json(normalized)
    slots = []
    unresolved = []
    atom_use: Counter[str] = Counter()

    for slot in demand["slots"]:
        selected = []
        candidate_groups = []
        targets = [int(row["note"]) for row in slot["note_requirements"]] if slot["mode"] == "trigger" else [None]
        for target in targets:
            candidates = [
                _candidate_receipt(
                    slot,
                    atom,
                    note=target,
                    maximum_transpose_semitones=maximum_transpose_semitones,
                    loopability_threshold=loopability_threshold,
                )
                for atom in normalized
            ]
            compatible = [row for row in candidates if row["compatible"]]
            compatible.sort(
                key=lambda row: (
                    -(float(row["score"]) - 0.055 * atom_use[str(row["atom_id"])]),
                    str(row["atom_id"]),
                )
            )
            ranked = compatible[:top_k]
            candidate_groups.append(
                {
                    "note": target,
                    "candidate_count": len(compatible),
                    "candidates": ranked,
                    "rejected_count": len(candidates) - len(compatible),
                    "rejected_reasons": dict(Counter(reason for row in candidates for reason in row["hard_failures"])),
                }
            )
            if not ranked:
                unresolved.append(
                    {
                        "slot_id": slot["slot_id"],
                        "note": target,
                        "reason": "no_compatible_approved_atom",
                    }
                )
                continue
            choice = deepcopy(ranked[0])
            choice["note"] = target
            selected.append(choice)
            atom_use[str(choice["atom_id"])] += 1
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
                "candidate_groups": candidate_groups,
                "selected": selected,
                "complete": len(selected) == len(targets),
            }
        )

    proposal = {
        "schema_version": LIBRARY_PROPOSAL_SCHEMA_VERSION,
        "kind": LIBRARY_PROPOSAL_KIND,
        "demand_sha256": demand["demand_sha256"],
        "semantic_sha256": demand["semantic_sha256"],
        "taste_profile": str(taste_profile),
        "atom_pool_sha256": atom_pool_sha256,
        "atom_pool_count": len(normalized),
        "configuration": {
            "top_k": int(top_k),
            "maximum_transpose_semitones": float(maximum_transpose_semitones),
            "loopability_threshold": float(loopability_threshold),
        },
        "complete": not unresolved and all(slot["complete"] for slot in slots),
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
    demand = proposal.get("demand") or {}
    rack_validate_demands(demand)
    if str(proposal.get("demand_sha256") or "") != str(demand.get("demand_sha256") or ""):
        raise RackError("proposal demand identity disagrees with embedded demand")
    slots = proposal.get("slots")
    if not isinstance(slots, list):
        raise RackError("proposal slots must be a list")
    if bool(proposal.get("complete")) and proposal.get("unresolved"):
        raise RackError("complete proposal cannot contain unresolved requirements")
    expected = midi_sha256_json({key: value for key, value in proposal.items() if key != "proposal_sha256"})
    if str(proposal.get("proposal_sha256") or "") != expected:
        raise RackError("proposal_sha256 does not match proposal contents")


def _materialized_asset_path(root: Path, selected: Mapping[str, Any], sample_rate: int) -> Path:
    atom = selected["source"]
    digest = midi_sha256_json(
        {
            "atom_id": atom["atom_id"],
            "path": atom["path"],
            "start_s": atom["start_s"],
            "end_s": atom["end_s"],
            "source_audio_sha256": atom["source_audio_sha256"],
            "sample_rate": sample_rate,
        }
    )
    return root / "samples" / f"{_stable_text(atom['atom_id'])}-{digest[:16]}.wav"


def _materialize_atom(selected: Mapping[str, Any], path: Path, sample_rate: int, *, overwrite: bool) -> dict[str, Any]:
    atom = selected["source"]
    source = Path(str(atom["path"])).expanduser().resolve()
    if not source.is_file():
        raise RackError(f"selected atom source is missing: {source}")
    if path.exists() and not overwrite:
        return {
            "atom_id": atom["atom_id"],
            "source_path": str(source),
            "path": str(path),
            "sha256": rack_sha256_file(path),
            "cache_status": "existing",
        }
    audio = decode_audio(
        source,
        sr=int(sample_rate),
        start=float(atom["start_s"]),
        duration=float(atom["end_s"]) - float(atom["start_s"]),
    )
    if audio.size < max(32, int(0.02 * sample_rate)):
        raise RackError(f"selected atom decoded too little audio: {atom['atom_id']}")
    audio = np.asarray(audio, dtype=np.float32)[:, None]
    try:
        import soundfile as sf
    except Exception as exc:
        raise RackError("library rack materialization requires soundfile") from exc
    if path.exists() and overwrite:
        path.unlink()
    _atomic_wav(path, audio, int(sample_rate), sf)
    return {
        "atom_id": atom["atom_id"],
        "source_path": str(source),
        "source_start_s": atom["start_s"],
        "source_end_s": atom["end_s"],
        "source_audio_sha256": atom["source_audio_sha256"],
        "source_generation": atom["source_generation"],
        "path": str(path),
        "sha256": rack_sha256_file(path),
        "sample_rate": int(sample_rate),
        "frames": int(audio.shape[0]),
        "cache_status": "materialized",
    }


def _loop_for_selection(selected: Mapping[str, Any], frames: int, sample_rate: int, mode: str) -> dict[str, Any]:
    if mode != "pitched" or not bool(selected.get("loop_required")):
        return {"enabled": False, "start_frame": 0, "end_frame": frames, "crossfade_frames": 0}
    edge = min(max(1, int(round(0.015 * sample_rate))), max(1, frames // 20))
    start = edge
    end = frames - edge
    crossfade = min(int(round(0.025 * sample_rate)), max(0, (end - start) // 8))
    if end - start < 32 or crossfade <= 0:
        raise RackError(f"selected atom cannot form a safe loop: {selected['atom_id']}")
    return {"enabled": True, "start_frame": start, "end_frame": end, "crossfade_frames": crossfade}


def rack_materialize_library_proposal(
    ledger: Mapping[str, Any],
    proposal: Mapping[str, Any],
    output_root: str | Path,
    *,
    sample_rate: int = 44_100,
    overwrite: bool = False,
    compile_sfz: bool = True,
) -> dict[str, Any]:
    """Materialize selected atoms, seal racks, compile binding and optional SFZ."""
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
        for ordinal, selected in enumerate(slot["selected"]):
            asset = _materialized_asset_path(root, selected, int(sample_rate))
            identity = materialized.get(str(asset))
            if identity is None:
                identity = _materialize_atom(selected, asset, int(sample_rate), overwrite=overwrite)
                materialized[str(asset)] = identity
            atom = selected["source"]
            note = selected.get("note")
            if slot["mode"] == "trigger":
                key_range = [int(note), int(note)]
                root_key = int(note)
                trigger_mode = "one_shot"
            else:
                key_range = [int(slot_group) for slot_group in [
                    next(value["minimum_note"] for value in proposal["demand"]["slots"] if value["slot_id"] == slot["slot_id"]),
                    next(value["maximum_note"] for value in proposal["demand"]["slots"] if value["slot_id"] == slot["slot_id"]),
                ]]
                root_key = int(selected["root_key"])
                trigger_mode = "gate"
            zone_id = "zone_" + midi_sha256_json(
                {
                    "slot_id": slot["slot_id"],
                    "atom_id": atom["atom_id"],
                    "note": note,
                    "asset_sha256": identity["sha256"],
                }
            )[:20]
            zones.append(
                {
                    "zone_id": zone_id,
                    "sample_path": identity["path"],
                    "key_range": key_range,
                    "velocity_range": [1, 127],
                    "root_key": root_key,
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
                    ],
                }
            )
        rack_id = "rack_" + str(slot["slot_id"])[len("slot_"):]
        draft = {
            "rack_id": rack_id,
            "name": f"{slot['track_name']} crate substitute",
            "mode": slot["mode"],
            "metadata": {
                "tags": [slot["role_hint"], slot["gm_family"], "earcrate-library"],
                "slot_id": slot["slot_id"],
                "track_index": slot["track_index"],
                "track_name": slot["track_name"],
                "proposal_sha256": proposal["proposal_sha256"],
                "selected_atoms": [selected["atom_id"] for selected in slot["selected"]],
            },
            "created_by": {
                "actor": "earcrate_library_adapter",
                "reason": "deterministic approved-atom substitution",
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
    rack_atomic_json(proposal_path, proposal, overwrite=overwrite)
    rack_atomic_json(demand_path, proposal["demand"], overwrite=overwrite)
    rack_atomic_json(binding_path, binding, overwrite=overwrite)
    build = {
        "schema_version": LIBRARY_BUILD_SCHEMA_VERSION,
        "kind": "earcrate_library_rack_build",
        "ok": True,
        "semantic_sha256": ledger["semantic_sha256"],
        "demand_sha256": proposal["demand_sha256"],
        "proposal_sha256": proposal["proposal_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "complete": binding["complete"],
        "sample_rate": int(sample_rate),
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
            "selected_atom_count": sum(len(slot["selected"]) for slot in proposal["slots"]),
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
