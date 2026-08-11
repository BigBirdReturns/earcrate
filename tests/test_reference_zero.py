from __future__ import annotations

import csv
import json
from pathlib import Path
import struct
import sys
import wave

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.reference_zero import (
    SourceMutationError,
    ValidationError,
    create_gold_receipt,
    create_recovery_challenge,
    create_source_bindings,
    create_source_registry,
    import_edl,
    prepare_candidate_control_review,
    render_performance_score,
    seal,
    sha256_file,
    submit_review,
    validate_performance_score,
    verify_reproduction,
)


def _write_tone(path: Path, *, frequency: float, seconds: float = 1.0, sample_rate: int = 48000) -> None:
    frames = round(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        data = bytearray()
        for index in range(frames):
            value = int(0.2 * 32767 * __import__("math").sin(2.0 * __import__("math").pi * frequency * index / sample_rate))
            data.extend(struct.pack("<hh", value, value))
        handle.writeframes(bytes(data))


def _score_for_sources(source_a: Path, source_b: Path, *, title: str = "Reference Zero test") -> dict:
    sample_rate = 48000
    duration_samples = 48000
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_performance_score",
            "created_at": "2026-08-10T00:00:00Z",
            "score_id": "reference-zero-test",
            "title": title,
            "timeline": {"sample_rate": sample_rate, "channels": 2, "duration_samples": duration_samples, "shared_events": []},
            "sources": [
                {"source_id": "a", "role": "lead", "container_sha256": sha256_file(source_a), "canonical_pcm_sha256": None},
                {"source_id": "b", "role": "drums", "container_sha256": sha256_file(source_b), "canonical_pcm_sha256": None},
            ],
            "tracks": [
                {
                    "track_id": "lead",
                    "role": "lead",
                    "ownership": "source-a",
                    "gain_db": 0.0,
                    "pan": 0.0,
                    "clips": [
                        {
                            "clip_id": "lead-1",
                            "source_id": "a",
                            "source_start_sample": 0,
                            "source_end_sample": 48000,
                            "target_start_sample": 0,
                            "tempo_scale": 1.0,
                            "pitch_semitones": 0.0,
                            "gain_db": -3.0,
                            "pan": 0.0,
                            "fade_in_samples": 64,
                            "fade_out_samples": 64,
                            "musical_function": "lead authority",
                            "occurrence_id": "excerpt-1",
                            "locked": True,
                        }
                    ],
                },
                {
                    "track_id": "drums",
                    "role": "drums",
                    "ownership": "source-b",
                    "gain_db": 0.0,
                    "pan": 0.0,
                    "clips": [
                        {
                            "clip_id": "drums-1",
                            "source_id": "b",
                            "source_start_sample": 0,
                            "source_end_sample": 48000,
                            "target_start_sample": 0,
                            "tempo_scale": 1.0,
                            "pitch_semitones": 0.0,
                            "gain_db": -6.0,
                            "pan": 0.0,
                            "fade_in_samples": 64,
                            "fade_out_samples": 64,
                            "musical_function": "percussion chassis",
                            "occurrence_id": "excerpt-1",
                            "locked": True,
                        }
                    ],
                },
            ],
            "master": {"gain_db": -6.0, "peak_limit_dbfs": None, "codec": "pcm_s24le"},
            "invariants": {
                "renderer_may_invent_decisions": False,
                "source_mutation_forbidden": True,
                "all_selected_clips_must_render": True,
                "human_command_history_append_only": True,
            },
            "authority": {"status": "manual_gold_candidate", "allow_unused_sources": False},
            "command_history": [
                {"sequence": 1, "command_id": "place-lead", "actor": "test", "operation": "place_clip", "target": "lead-1", "parameters_sha256": "1" * 64},
                {"sequence": 2, "command_id": "place-drums", "actor": "test", "operation": "place_clip", "target": "drums-1", "parameters_sha256": "2" * 64},
            ],
        }
    )


def _fixture(tmp_path: Path):
    source_a = tmp_path / "a.wav"
    source_b = tmp_path / "b.wav"
    _write_tone(source_a, frequency=220.0)
    _write_tone(source_b, frequency=110.0)
    score = _score_for_sources(source_a, source_b)
    bindings = create_source_bindings(score, paths={"a": source_a, "b": source_b})
    return source_a, source_b, score, bindings


def test_source_registry_is_path_free_and_sealed(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_tone(source, frequency=330.0)
    registry = create_source_registry(
        sources={"source": source},
        roles={"source": "manual_gold_material"},
    )
    assert registry["registry_sha256"]
    assert registry["sources"][0]["container_sha256"] == sha256_file(source)
    assert str(tmp_path) not in json.dumps(registry)


def test_score_is_portable_and_render_is_exact(tmp_path: Path) -> None:
    _, _, score, bindings = _fixture(tmp_path)
    score_sha = validate_performance_score(score)
    assert score_sha == score["score_sha256"]
    assert str(tmp_path) not in json.dumps(score)
    receipt = render_performance_score(
        score,
        bindings,
        output_path=tmp_path / "render.wav",
        receipt_path=tmp_path / "render.receipt.json",
    )
    assert receipt["authority"]["renderer_invented_decisions"] is False
    assert receipt["clip_count"] == 2
    assert receipt["output"]["canonical_pcm_sha256"]
    assert (tmp_path / "render.wav").stat().st_size > 1000
    reproduction = verify_reproduction(score, bindings, output_directory=tmp_path / "reproduction")
    assert reproduction["ok"] is True
    assert reproduction["canonical_pcm_sha256"]


def test_source_mutation_fails_closed(tmp_path: Path) -> None:
    source_a, _, score, bindings = _fixture(tmp_path)
    source_a.write_bytes(source_a.read_bytes() + b"mutation")
    with pytest.raises(SourceMutationError):
        render_performance_score(score, bindings, output_path=tmp_path / "mutated.wav")


def test_import_edl_builds_append_only_score(tmp_path: Path) -> None:
    source_a = tmp_path / "a.wav"
    source_b = tmp_path / "b.wav"
    _write_tone(source_a, frequency=220.0)
    _write_tone(source_b, frequency=110.0)
    registry = {
        "sources": [
            {"source_id": "a", "role": "lead", "container_sha256": sha256_file(source_a), "canonical_pcm_sha256": None},
            {"source_id": "b", "role": "drums", "container_sha256": sha256_file(source_b), "canonical_pcm_sha256": None},
        ]
    }
    edl = tmp_path / "performance.csv"
    fields = [
        "track_id", "track_role", "ownership", "clip_id", "source_id",
        "source_start_seconds", "source_end_seconds", "target_start_seconds",
        "tempo_scale", "pitch_semitones", "gain_db", "pan", "fade_in_ms",
        "fade_out_ms", "musical_function", "occurrence_id", "locked",
    ]
    with edl.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "track_id": "lead", "track_role": "lead", "ownership": "a", "clip_id": "c1", "source_id": "a",
                "source_start_seconds": "0", "source_end_seconds": "1", "target_start_seconds": "0",
                "tempo_scale": "1", "pitch_semitones": "0", "gain_db": "0", "pan": "0",
                "fade_in_ms": "0", "fade_out_ms": "0", "musical_function": "lead", "occurrence_id": "x", "locked": "true",
            }
        )
        writer.writerow(
            {
                "track_id": "drums", "track_role": "drums", "ownership": "b", "clip_id": "c2", "source_id": "b",
                "source_start_seconds": "0", "source_end_seconds": "1", "target_start_seconds": "0",
                "tempo_scale": "1", "pitch_semitones": "0", "gain_db": "-3", "pan": "0",
                "fade_in_ms": "0", "fade_out_ms": "0", "musical_function": "drums", "occurrence_id": "x", "locked": "true",
            }
        )
    score = import_edl(
        source_registry=registry,
        edl_path=edl,
        score_id="edl-test",
        title="EDL test",
        sample_rate=48000,
        channels=2,
        duration_samples=48000,
    )
    assert len(score["command_history"]) == 2
    assert [row["sequence"] for row in score["command_history"]] == [1, 2]
    assert validate_performance_score(score) == score["score_sha256"]


def test_gold_challenge_and_candidate_control_review(tmp_path: Path) -> None:
    _, _, gold_score, bindings = _fixture(tmp_path)
    gold_render = render_performance_score(
        gold_score,
        bindings,
        output_path=tmp_path / "gold.wav",
        receipt_path=tmp_path / "gold.render.json",
    )
    gold_receipt = create_gold_receipt(
        gold_score,
        gold_render,
        reviewer_id="operator:owner",
        disposition="accept",
        dimensions={"one-band coherence": 5},
        notes=["accepted test gold"],
    )
    challenge = create_recovery_challenge(gold_score, gold_receipt)
    assert challenge["withheld_answer_key"]["clip_decisions_published"] is False
    assert "tracks" not in challenge

    control_score = json.loads(json.dumps(gold_score))
    control_score.pop("score_sha256")
    control_score["score_id"] = "control"
    control_score["tracks"][1]["clips"][0]["gain_db"] = -20.0
    control_score = seal(control_score)
    control_bindings = create_source_bindings(control_score, paths={"a": tmp_path / "a.wav", "b": tmp_path / "b.wav"})
    review = prepare_candidate_control_review(
        gold_score,
        control_score,
        bindings,
        control_bindings,
        output_directory=tmp_path / "review",
        seed=7,
    )
    public = Path(review["public_directory"])
    assert (public / "A.flac").is_file()
    assert (public / "B.flac").is_file()
    assert "option_map" not in review["assignment"]
    candidate_label = next(label for label, semantic in review["authority"]["option_map"].items() if semantic == "candidate")
    submission, ledger = submit_review(
        review["assignment"],
        review["authority"],
        reviewer_id="operator:owner",
        choice=candidate_label,
        dimensions={"one-band coherence": 5},
        notes=["candidate beats control"],
    )
    assert submission["choice"] == candidate_label
    assert ledger["verdict"] == "candidate_beats_control"
    assert ledger["authority"]["full_song_permission"] is False


def test_rejects_absolute_path_inside_score(tmp_path: Path) -> None:
    source_a = tmp_path / "a.wav"
    source_b = tmp_path / "b.wav"
    _write_tone(source_a, frequency=220.0)
    _write_tone(source_b, frequency=110.0)
    score = _score_for_sources(source_a, source_b)
    broken = json.loads(json.dumps(score))
    broken.pop("score_sha256")
    broken["notes"] = [str(tmp_path / "secret.wav")]
    broken = seal(broken)
    with pytest.raises(ValidationError):
        validate_performance_score(broken)
