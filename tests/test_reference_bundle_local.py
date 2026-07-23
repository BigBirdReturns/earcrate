from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.midi.render import midi_render_ledger
from earcrate.providers.notes import notes_canonical_json, notes_observation_payload
from earcrate.study.reference_bundle import (
    ReferenceBundleError,
    reference_compile_bundle,
    reference_verify_source,
    reference_write_bundle,
)
from earcrate.study.reference_grid import (
    reference_accept_grid,
    reference_drum_observation,
    reference_grid_from_beats,
    reference_manual_note_observation,
)


def _write_reference(path: Path, sample_rate: int = 8_000) -> None:
    seconds = 8.0
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    audio = 0.05 * np.sin(2.0 * np.pi * 110.0 * time)
    for beat in np.arange(0.25, 7.76, 0.5):
        start = int(beat * sample_rate)
        count = min(int(0.04 * sample_rate), len(audio) - start)
        if count > 0:
            envelope = np.exp(-np.arange(count) / max(1.0, 0.009 * sample_rate))
            audio[start : start + count] += 0.55 * envelope
    sf.write(path, audio.astype(np.float32), sample_rate, subtype="FLOAT")


def _accepted_grid() -> dict:
    proposed = reference_grid_from_beats(
        [0.25 + 0.5 * index for index in range(16)],
        meter_numerator=4,
        accepted=False,
        provider={"name": "test-grid", "version": "1", "kind": "fixture"},
        confidence=0.99,
    )
    return reference_accept_grid(proposed, actor="gate", reason="fixture beats are authored")


def _observations() -> tuple[list[dict], dict]:
    bass = reference_manual_note_observation(
        [
            {"note_id": "bass-1", "start_s": 0.26, "end_s": 1.24, "pitch_midi": 36, "velocity": 96},
            {"note_id": "bass-2", "start_s": 1.26, "end_s": 2.24, "pitch_midi": 39, "velocity": 98},
            {"note_id": "bass-3", "start_s": 2.26, "end_s": 3.24, "pitch_midi": 43, "velocity": 101},
        ],
        source_identity="fixture:bass-stem",
        label="bass",
    )
    lead = reference_manual_note_observation(
        [
            {
                "note_id": "lead-1",
                "start_s": 4.26,
                "end_s": 5.24,
                "pitch_midi": 72,
                "velocity": 110,
                "pitch_bends": [0, 1, 2, 1, 0],
            },
            {"note_id": "lead-2", "start_s": 5.26, "end_s": 6.24, "pitch_midi": 76, "velocity": 108},
        ],
        source_identity="fixture:lead-stem",
        label="lead",
    )
    drums = reference_drum_observation(
        [
            {"event_id": "kick-1", "time_seconds": 0.25, "note": 36, "velocity": 118, "role": "kick"},
            {"event_id": "snare-1", "time_seconds": 1.25, "note": 38, "velocity": 112, "role": "snare"},
            {"event_id": "kick-2", "time_seconds": 2.25, "note": 36, "velocity": 120, "role": "kick"},
            {"event_id": "snare-2", "time_seconds": 3.25, "note": 38, "velocity": 114, "role": "snare"},
        ],
        source_identity="fixture:drum-stem",
    )
    tracks = [
        {"track_id": "bass", "name": "Bass", "role": "bass", "program": 33, "observation": bass},
        {"track_id": "lead", "name": "Synth Lead", "role": "synth_lead", "program": 80, "observation": lead},
    ]
    return tracks, drums


def test_reference_bundle_quantizes_every_observation_and_renders_neutral(tmp_path: Path) -> None:
    audio = tmp_path / "reference.wav"
    _write_reference(audio)
    tracks, drums = _observations()
    bundle = reference_compile_bundle(
        audio,
        _accepted_grid(),
        tracks,
        drum_observation=drums,
        ppq=480,
        quantization_subdivisions=4,
        maximum_quantization_error_seconds=0.03,
        sample_rate=8_000,
    )
    assert bundle["complete"] is True
    assert bundle["refused_decision_count"] == 0
    assert bundle["decision_count"] == bundle["accepted_decision_count"] == 9
    assert bundle["midi_statistics"]["note_on_count"] == 9
    assert bundle["midi_statistics"]["pitchwheel_count"] == 6
    assert len(bundle["note_execution"]) == 9
    assert len({row["output_event_id"] for row in bundle["note_execution"]}) == 9
    assert bundle["grid"]["origin_seconds"] == 0.25
    assert bundle["grid"]["source_pcm_sha256"] == bundle["source"]["pcm_sha256"]
    assert bundle["anatomy"]["selected_event_count"] == 9
    assert reference_verify_source(bundle)["ok"] is True

    render = midi_render_ledger(bundle["midi_ledger"], tmp_path / "reference-neutral.wav", sample_rate=8_000)
    assert render["complete_execution"] is True
    assert render["selected_event_count"] == render["executed_event_count"] == 9


def test_reference_bundle_refuses_unaccepted_grid_and_large_quantization_error(tmp_path: Path) -> None:
    audio = tmp_path / "reference.wav"
    _write_reference(audio)
    proposed = reference_grid_from_beats([0.25 + 0.5 * index for index in range(16)], accepted=False)
    tracks, drums = _observations()
    try:
        reference_compile_bundle(audio, proposed, tracks, drum_observation=drums, sample_rate=8_000)
    except ReferenceBundleError as exc:
        assert "accepted beat grid" in str(exc)
    else:
        raise AssertionError("reference compilation accepted an unapproved grid")

    off_grid = reference_manual_note_observation(
        [{"note_id": "off", "start_s": 0.42, "end_s": 0.92, "pitch_midi": 60, "velocity": 100}],
        source_identity="fixture:off-grid",
    )
    bundle = reference_compile_bundle(
        audio,
        _accepted_grid(),
        [{"track_id": "off", "name": "Off Grid", "role": "lead", "program": 80, "observation": off_grid}],
        maximum_quantization_error_seconds=0.02,
        sample_rate=8_000,
    )
    assert bundle["complete"] is False
    assert bundle["accepted_decision_count"] == 0
    assert bundle["refused_decision_count"] == 1
    assert bundle["refusals"][0]["reasons"]
    try:
        reference_write_bundle(bundle, tmp_path / "refused.json", tmp_path / "refused.mid")
    except ReferenceBundleError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete reference bundle was published without an override")


def test_basic_pitch_shaped_observation_is_replaceable_input(tmp_path: Path) -> None:
    audio = tmp_path / "reference.wav"
    _write_reference(audio)
    observation = reference_manual_note_observation(
        [{"note_id": "bp-1", "start_s": 0.25, "end_s": 0.75, "pitch_midi": 60, "velocity": 100}],
        source_identity="fixture:basic-pitch-stem",
    )
    observation["provider"] = "basic-pitch"
    observation["provider_version"] = "0.4.0"
    observation["model_sha256"] = "a" * 64
    observation["model_path"] = "/ignored/in/authority/hash.onnx"
    observation["observation_sha256"] = hashlib.sha256(
        notes_canonical_json(notes_observation_payload(observation)).encode("utf-8")
    ).hexdigest()
    bundle = reference_compile_bundle(
        audio,
        _accepted_grid(),
        [{"track_id": "melody", "name": "Melody", "role": "lead", "program": 80, "observation": observation}],
        sample_rate=8_000,
    )
    assert bundle["complete"] is True
    receipt = bundle["observation_receipts"][0]
    assert receipt["provider"] == "basic-pitch"
    assert receipt["model_sha256"] == "a" * 64
    assert bundle["note_execution"][0]["source_event_id"] == "bp-1"


def test_reference_source_mutation_is_detected(tmp_path: Path) -> None:
    audio = tmp_path / "reference.wav"
    _write_reference(audio)
    tracks, drums = _observations()
    bundle = reference_compile_bundle(audio, _accepted_grid(), tracks, drum_observation=drums, sample_rate=8_000)
    data, rate = sf.read(audio, dtype="float32")
    data[0] = 0.99
    sf.write(audio, data, rate, subtype="FLOAT")
    receipt = reference_verify_source(bundle, raise_on_error=False)
    assert receipt["ok"] is False
    assert "byte identity changed" in receipt["failures"][0]


def test_reference_writer_and_single_file_namespace(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    audio = tmp_path / "reference.wav"
    _write_reference(audio)
    tracks, drums = _observations()
    bundle = reference_compile_bundle(audio, _accepted_grid(), tracks, drum_observation=drums, sample_rate=8_000)
    receipt = reference_write_bundle(bundle, tmp_path / "reference.bundle.json", tmp_path / "reference.mid")
    assert receipt["complete"] is True
    assert json.loads((tmp_path / "reference.bundle.json").read_text(encoding="utf-8"))["bundle_sha256"] == receipt["bundle_sha256"]

    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    namespace = runpy.run_path(str(root / "dist" / "earcrate.py"), run_name="earcrate_reference_singlefile_gate")
    result = namespace["reference_compile_bundle"](
        audio,
        _accepted_grid(),
        tracks,
        drum_observation=drums,
        sample_rate=8_000,
    )
    assert result["complete"] is True
    assert result["decision_count"] == 9
