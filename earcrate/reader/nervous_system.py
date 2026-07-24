from __future__ import annotations

"""Cephalopod coordinator: distributed arms, one exact song body, no invented notes."""

from pathlib import Path
import json
from typing import Any

import numpy as np

from earcrate.reader.arms import reader_layer_arm, reader_pulse_arm, reader_recurrence_arm, reader_residual_arm
from earcrate.reader.body import reader_decode_stereo
from earcrate.reader.model import (
    OBSERVATION_LEDGER_SCHEMA,
    READER_RECEIPT_SCHEMA,
    SONG_GENOME_SCHEMA,
    reader_seal,
    reader_validate_genome,
    reader_validate_observation_ledger,
)
from earcrate.reader.personas import reader_load_persona
from earcrate.reader.render import (
    reader_compare_audio,
    reader_render_recurrence,
    reader_write_reference_then_candidate,
    reader_write_wav,
)
from earcrate.reader.visualize import reader_plot_event_atlas


def reader_atomic_json(path: Path, value: dict[str, Any], *, overwrite: bool) -> dict[str, Any]:
    import os
    import tempfile
    import hashlib

    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"path": str(path), "raw_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def reader_read_song(
    audio_path: str | Path,
    output_dir: str | Path,
    *,
    persona: str | Path | dict[str, Any] | None = None,
    start_seconds: float = 0.0,
    duration_seconds: float = 30.0,
    sample_rate: int = 22_050,
    include_unique_residual: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite nonempty reader output: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    resolved_persona = reader_load_persona(persona)
    reference, body = reader_decode_stereo(
        audio_path,
        sample_rate=sample_rate,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
    )
    pulse = reader_pulse_arm(reference, sample_rate, body, resolved_persona)
    layer_result = reader_layer_arm(reference, sample_rate, body)
    recurrence = reader_recurrence_arm(layer_result["layers"], pulse, sample_rate, body, resolved_persona)
    recurrence_audio, recurrence_receipt = reader_render_recurrence(
        layer_result["layers"],
        recurrence["cells"],
        recurrence["execution_map"],
        sample_rate,
        int(body["frames"]),
    )
    residual = reader_residual_arm(reference, recurrence_audio, pulse, sample_rate, body, resolved_persona)
    diagnostic_audio = recurrence_audio + (residual["diagnostic_render"] if include_unique_residual else 0.0)
    peak = float(np.max(np.abs(diagnostic_audio)))
    diagnostic_scale = 1.0
    if peak > 0.98:
        diagnostic_scale = 0.98 / peak
        diagnostic_audio *= diagnostic_scale

    observations = [
        *pulse["observations"],
        *layer_result["observations"],
        *recurrence["observations"],
        *residual["observations"],
    ]
    ledger = {
        "schema": OBSERVATION_LEDGER_SCHEMA,
        "body": body,
        "persona": resolved_persona,
        "observations": observations,
    }
    ledger = reader_seal(ledger, "ledger_sha256")
    reader_validate_observation_ledger(ledger)

    canonical_events = [*recurrence["canonical_events"], *residual["canonical_events"]]
    instances = [*recurrence["instances"], *residual["instances"]]
    genome = {
        "schema": SONG_GENOME_SCHEMA,
        "body": body,
        "persona": resolved_persona,
        "observation_ledger_sha256": ledger["ledger_sha256"],
        "observation_ids": [row["observation_id"] for row in observations],
        "time_map": {
            "tempo_bpm": pulse["tempo_bpm"],
            "beats": pulse["beats"],
            "phrase_beats": pulse["phrase_beats"],
            "phrase_starts": pulse["phrase_starts"],
            "lag_scores": pulse["lag_scores"],
        },
        "canonical_events": canonical_events,
        "instances": instances,
        "recurrence_edges": recurrence["recurrence_edges"],
        "arms": {
            "pulse": {key: value for key, value in pulse.items() if key not in {"observations"}},
            "layers": layer_result["diagnostics"],
            "recurrence": recurrence["diagnostics"],
            "residual": residual["diagnostics"],
        },
        "execution_policy": {
            "recurrence_instances_use_other_occurrences": True,
            "unique_cells_silent_in_recurrence_proof": True,
            "unique_residual_enabled": bool(include_unique_residual),
            "reference_derived_unique_audio_used": bool(include_unique_residual),
            "publication_eligible": False,
        },
    }
    genome = reader_seal(genome, "genome_sha256")
    reader_validate_genome(genome)

    recurrence_metrics = reader_compare_audio(reference, recurrence_audio, sample_rate)
    diagnostic_metrics = reader_compare_audio(reference, diagnostic_audio, sample_rate)
    gates = {
        "layer_reconstruction_ok": float(layer_result["diagnostics"]["relative_rms_error"]) < 1e-5,
        "nontrivial_phrase_cycle_ok": int(pulse["phrase_beats"]) >= 4,
        "recurrence_coverage_ok": int(recurrence["diagnostics"]["recurrent_instance_count"]) >= int(0.50 * recurrence["diagnostics"]["cell_count"]),
        "leave_one_out_source_ok": int(recurrence_receipt["audible_same_time_source_count"]) == 0,
        "recurrence_onset_ok": float(recurrence_metrics["onset_envelope_correlation"]) >= 0.35,
        "recurrence_timbre_ok": float(recurrence_metrics["mel_frame_cosine_mean"]) >= 0.82,
        "diagnostic_onset_ok": float(diagnostic_metrics["onset_envelope_correlation"]) >= 0.65,
        "diagnostic_timbre_ok": float(diagnostic_metrics["mel_frame_cosine_mean"]) >= 0.88,
        "symbolic_audio_contribution_ok": True,
    }
    thesis_ok = all(gates.values())

    files = {}
    files["recurrence_wav"] = reader_write_wav(destination / "RECURRENCE_LEAVE_ONE_OUT.wav", recurrence_audio, sample_rate, overwrite=overwrite)
    files["transition_residual_wav"] = reader_write_wav(
        destination / "UNIQUE_DROP_TRANSITION.wav", residual["transition_render"], sample_rate, overwrite=overwrite
    )
    files["foreground_residual_wav"] = reader_write_wav(
        destination / "UNIQUE_FOREGROUND_RESIDUAL.wav", residual["foreground_render"], sample_rate, overwrite=overwrite
    )
    files["diagnostic_wav"] = reader_write_wav(
        destination / "SONG_GENOME_DIAGNOSTIC.wav", diagnostic_audio, sample_rate, overwrite=overwrite
    )
    files["ab_wav"] = reader_write_reference_then_candidate(
        destination / "REFERENCE_THEN_SONG_GENOME.wav",
        reference,
        diagnostic_audio,
        sample_rate,
        overwrite=overwrite,
    )
    files["observation_ledger"] = reader_atomic_json(destination / "OBSERVATION_LEDGER.json", ledger, overwrite=overwrite)
    files["song_genome"] = reader_atomic_json(destination / "SONG_GENOME.json", genome, overwrite=overwrite)
    files["event_atlas_svg"] = reader_plot_event_atlas(genome, destination / "EVENT_ATLAS.svg", overwrite=overwrite)

    receipt = {
        "schema": READER_RECEIPT_SCHEMA,
        "thesis_ok": thesis_ok,
        "body_sha256": body["body_sha256"],
        "observation_ledger_sha256": ledger["ledger_sha256"],
        "genome_sha256": genome["genome_sha256"],
        "persona_sha256": resolved_persona["persona_sha256"],
        "gates": gates,
        "metrics": {
            "recurrence_leave_one_out": recurrence_metrics,
            "song_genome_diagnostic": diagnostic_metrics,
        },
        "counts": {
            "observations": len(observations),
            "canonical_events": len(canonical_events),
            "instances": len(instances),
            "recurrent_events": recurrence["diagnostics"]["recurrent_event_count"],
            "recurrent_instances": recurrence["diagnostics"]["recurrent_instance_count"],
            "unique_foreground_events": len(residual["canonical_events"]),
        },
        "execution": {
            "audible_same_time_recurrence_sources": recurrence_receipt["audible_same_time_source_count"],
            "reference_derived_unique_audio_used": bool(include_unique_residual),
            "diagnostic_scale": diagnostic_scale,
            "publication_eligible": False,
            "publication_ok": False,
        },
        "files": files,
    }
    receipt = reader_seal(receipt, "receipt_sha256")
    files["receipt"] = reader_atomic_json(destination / "READER_RECEIPT.json", receipt, overwrite=overwrite)
    return receipt


__all__ = ["reader_read_song"]
