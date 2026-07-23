from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from earcrate.analyze.decode import decode_audio
from earcrate.midi.render import _atomic_wav
from earcrate.rack.model import RackError, rack_sha256_file

_library_module = None
import earcrate.rack.library as _library_module

_role_fit = _library_module._role_fit if _library_module is not None else _role_fit
_slot_role = _library_module._slot_role if _library_module is not None else _slot_role
_nearest_root = _library_module._nearest_root if _library_module is not None else _nearest_root
_trigger_spectral_fit = _library_module._trigger_spectral_fit if _library_module is not None else _trigger_spectral_fit
_pitched_timbre_fit = _library_module._pitched_timbre_fit if _library_module is not None else _pitched_timbre_fit


def _candidate_receipt(
    slot: Mapping[str, Any],
    atom: Mapping[str, Any],
    *,
    note: int | None,
    maximum_transpose_semitones: float,
    loopability_threshold: float,
) -> dict[str, Any]:
    """Measure duration against the fastest required playback rate."""
    role_fit = _role_fit(slot, atom)
    role = _slot_role(slot)
    center = int(
        note
        if note is not None
        else round((int(slot["minimum_note"]) + int(slot["maximum_note"])) / 2.0)
    )
    root_key = (
        center
        if slot["mode"] == "trigger"
        else (_nearest_root(int(atom["key_root"]), center) if atom["key_known"] else center)
    )
    maximum_distance = max(
        abs(int(slot["minimum_note"]) - root_key),
        abs(int(slot["maximum_note"]) - root_key),
    )
    requirements = [
        row
        for row in slot["note_requirements"]
        if note is None or int(row["note"]) == int(note)
    ]
    required_duration = max(float(row["maximum_duration_seconds"]) for row in requirements)
    maximum_upward_transpose = max(0, int(slot["maximum_note"]) - root_key)
    # Two semitones is the current default full-scale bend range. The exact binder
    # remains authoritative and can still refuse a custom wider bend configuration.
    fastest_ratio = 2.0 ** ((maximum_upward_transpose + 2.0) / 12.0)
    unlooped_coverage = float(atom["duration_s"]) / max(1e-6, fastest_ratio)
    loop_required = (
        slot["mode"] == "pitched"
        and required_duration > unlooped_coverage + 1e-6
    )
    duration_fit = (
        1.0
        if not loop_required
        else min(1.0, float(atom["loopability"]) / max(1e-6, loopability_threshold))
    )
    hard_failures = []
    if role_fit < 0.24:
        hard_failures.append("role_incompatible")
    if slot["mode"] == "pitched" and maximum_distance > float(maximum_transpose_semitones):
        hard_failures.append("transpose_budget_exceeded")
    if loop_required and float(atom["loopability"]) < float(loopability_threshold):
        hard_failures.append("insufficient_duration_and_loopability")
    if slot["mode"] == "trigger" and float(atom["duration_s"]) > 32.0:
        hard_failures.append("trigger_region_too_long")
    quality = float(atom["score"])
    timbre = (
        _trigger_spectral_fit(int(note), atom)
        if slot["mode"] == "trigger" and note is not None
        else _pitched_timbre_fit(role, atom)
    )
    key_fit = (
        1.0
        if slot["mode"] == "trigger"
        else max(
            0.0,
            1.0
            - maximum_distance
            / max(1.0, float(maximum_transpose_semitones)),
        )
    )
    score = (
        0.36 * role_fit
        + 0.20 * quality
        + 0.18 * timbre
        + 0.12 * duration_fit
        + 0.10 * key_fit
        + 0.04 * float(atom["loopability"])
    )
    return {
        "atom_id": atom["atom_id"],
        "compatible": not hard_failures,
        "hard_failures": hard_failures,
        "score": round(score, 9) if not hard_failures else None,
        "root_key": root_key,
        "maximum_transpose_semitones": maximum_distance,
        "maximum_upward_transpose_semitones": maximum_upward_transpose,
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
        "source": dict(atom),
    }


def _materialize_atom(
    selected: Mapping[str, Any],
    path: Path,
    sample_rate: int,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    """Decode one exact source region; never adopt an unreceipted partial asset."""
    atom = selected["source"]
    source = Path(str(atom["path"])).expanduser().resolve()
    if not source.is_file():
        raise RackError(f"selected atom source is missing: {source}")
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"refusing to adopt an existing unreceipted materialized atom: {path}"
        )
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
    if path.exists():
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


if _library_module is not None:
    _library_module._candidate_receipt = _candidate_receipt
    _library_module._materialize_atom = _materialize_atom
    rack_build_from_atoms = _library_module.rack_build_from_atoms
    rack_materialize_library_proposal = _library_module.rack_materialize_library_proposal
    rack_propose_from_atoms = _library_module.rack_propose_from_atoms
    rack_validate_library_proposal = _library_module.rack_validate_library_proposal
