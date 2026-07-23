from __future__ import annotations

from pathlib import Path

from earcrate.providers.notes import (
    BASIC_PITCH_DEFAULT_CONFIG,
    BasicPitchNoteTranscriber,
    NoopNoteTranscriber,
    notes_artifact_key,
    notes_canonicalize_events,
    notes_hash_path,
)


def test_note_provider_is_honest_and_content_addressed(tmp_path: Path) -> None:
    noop = NoopNoteTranscriber().capability()
    assert noop["ready"] is False
    assert "basic-pitch" in noop["missing"]

    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.bin").write_bytes(b"model-v1")
    (model / "config.json").write_text('{"bins": 88}', encoding="utf-8")
    model_hash = notes_hash_path(model)
    config = dict(BASIC_PITCH_DEFAULT_CONFIG)
    key_a = notes_artifact_key("pcm:abc", "basic-pitch", "0.4.0", model_hash, config)
    key_b = notes_artifact_key("pcm:abc", "basic-pitch", "0.4.0", model_hash, config)
    assert key_a == key_b
    assert key_a.startswith("notes:")

    events = notes_canonicalize_events(
        [(0.0, 0.5, 60, 0.75, [0, 1]), (1.0, 0.5, 61, 0.4, None)],
        source_identity="pcm:abc",
        provider="basic-pitch",
        provider_version="0.4.0",
        model_sha256=model_hash,
        config=config,
    )
    assert len(events) == 1
    assert events[0]["pitch_midi"] == 60
    assert events[0]["velocity"] == 95
    assert events[0]["pitch_bends"] == [0, 1]

    capability = BasicPitchNoteTranscriber().capability()
    assert capability["provider"] == "basic-pitch"
    assert isinstance(capability["ready"], bool)
