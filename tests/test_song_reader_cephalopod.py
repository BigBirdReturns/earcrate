from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.reader.cli import reader_capabilities
from earcrate.reader.model import ReaderError, reader_seal, reader_validate_genome
from earcrate.reader.nervous_system import reader_read_song


def _synthetic_song(path: Path, sample_rate: int = 8_000) -> None:
    beat_seconds = 0.5
    phrase_beats = 8
    phrase_seconds = beat_seconds * phrase_beats
    phrases = 4
    frames = int(round(phrase_seconds * phrases * sample_rate))
    stereo = np.zeros((frames, 2), dtype=np.float32)

    def add_tone(start: float, duration: float, frequency: float, amplitude: float, pan: float = 0.0) -> None:
        a = int(round(start * sample_rate))
        n = min(frames - a, int(round(duration * sample_rate)))
        if n <= 0:
            return
        time = np.arange(n, dtype=np.float64) / sample_rate
        envelope = np.minimum(1.0, time / 0.01) * np.exp(-time / max(0.05, duration * 0.70))
        signal = (np.sin(2.0 * np.pi * frequency * time) * envelope * amplitude).astype(np.float32)
        stereo[a : a + n, 0] += signal * (1.0 - max(0.0, pan))
        stereo[a : a + n, 1] += signal * (1.0 + min(0.0, pan))

    def add_noise(start: float, duration: float, amplitude: float, seed: int) -> None:
        a = int(round(start * sample_rate))
        n = min(frames - a, int(round(duration * sample_rate)))
        if n <= 0:
            return
        time = np.arange(n, dtype=np.float64) / sample_rate
        noise = np.random.default_rng(seed).standard_normal(n).astype(np.float32)
        signal = noise * np.exp(-time * 22.0).astype(np.float32) * amplitude
        stereo[a : a + n, 0] += signal
        stereo[a : a + n, 1] += signal

    for phrase in range(phrases):
        phrase_start = phrase * phrase_seconds
        for beat in range(phrase_beats):
            time = phrase_start + beat * beat_seconds
            add_tone(time, 0.20, 64.0 if beat % 4 == 0 else 82.0, 0.45)
            if beat % 2 == 1:
                add_noise(time + 0.02, 0.13, 0.22, seed=phrase * 100 + beat)
            root = 220.0 if beat < 4 else 277.18
            add_tone(time + 0.06, 0.26, root, 0.16, pan=-0.20)
            add_tone(time + 0.06, 0.26, root * 1.5, 0.11, pan=0.20)
        add_noise(phrase_start + phrase_seconds - 0.18, 0.18, 0.12, seed=900 + phrase)

    foreground_start = 3 * phrase_seconds + 0.35
    for index in range(12):
        add_tone(foreground_start + index * 0.18, 0.14, 330.0 + 22.0 * (index % 4), 0.12)

    peak = float(np.max(np.abs(stereo)))
    if peak > 0.95:
        stereo *= 0.95 / peak
    sf.write(path, stereo, sample_rate, subtype="PCM_16")


def test_reader_capabilities_are_static() -> None:
    result = reader_capabilities()
    assert result["ok"] is True
    assert result["offline_after_compile"] is True
    assert "recurrence" in result["arms"]


def test_genome_refuses_instances_without_observations() -> None:
    genome = {
        "schema": "earcrate/song-genome@1",
        "body": {"body_sha256": "a" * 64, "frames": 100, "sample_rate": 10, "duration_seconds": 10.0},
        "observation_ids": [],
        "canonical_events": [{"canonical_event_id": "event_a"}],
        "instances": [
            {
                "instance_id": "instance_a",
                "canonical_event_id": "event_a",
                "start_frame": 0,
                "end_frame": 10,
                "observation_ids": [],
            }
        ],
    }
    genome = reader_seal(genome, "genome_sha256")
    try:
        reader_validate_genome(genome)
    except ReaderError as exc:
        assert "no acoustic observations" in str(exc)
    else:
        raise AssertionError("reader accepted an audible instance without observations")


def test_cephalopod_reader_discovers_and_executes_recurrence(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-song.wav"
    _synthetic_song(source)
    output = tmp_path / "reader"
    receipt = reader_read_song(
        source,
        output,
        sample_rate=8_000,
        duration_seconds=16.0,
        include_unique_residual=True,
        overwrite=False,
    )
    assert receipt["thesis_ok"] is True
    assert receipt["gates"]["leave_one_out_source_ok"] is True
    assert receipt["gates"]["layer_reconstruction_ok"] is True
    assert receipt["counts"]["recurrent_events"] >= 4
    assert receipt["counts"]["recurrent_instances"] > receipt["counts"]["recurrent_events"]
    assert receipt["metrics"]["recurrence_leave_one_out"]["onset_envelope_correlation"] >= 0.35
    assert receipt["execution"]["publication_eligible"] is False

    genome = json.loads((output / "SONG_GENOME.json").read_text(encoding="utf-8"))
    assert genome["time_map"]["phrase_beats"] >= 4
    assert all(instance["observation_ids"] for instance in genome["instances"])
    assert (output / "RECURRENCE_LEAVE_ONE_OUT.wav").is_file()
    assert (output / "SONG_GENOME_DIAGNOSTIC.wav").is_file()
    assert (output / "EVENT_ATLAS.svg").is_file()
