from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from earcrate.providers import register
from earcrate.providers.artifacts import ArtifactStore

NOTE_OBSERVATION_SCHEMA_VERSION = 1
BASIC_PITCH_DEFAULT_CONFIG = {
    "onset_threshold": 0.5,
    "frame_threshold": 0.3,
    "minimum_note_length_ms": 127.7,
    "minimum_frequency_hz": None,
    "maximum_frequency_hz": None,
    "multiple_pitch_bends": False,
    "melodia_trick": True,
    "midi_tempo": 120.0,
}


class NoteTranscriber:
    name = "abstract"

    def capability(self) -> dict[str, Any]:
        raise NotImplementedError

    def transcribe(self, audio_path: str | Path, *, source_identity: str = "", config: Mapping[str, Any] | None = None, artifact_store: ArtifactStore | None = None) -> dict[str, Any]:
        raise NotImplementedError


class NoopNoteTranscriber(NoteTranscriber):
    name = "noop"

    def capability(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "ready": False,
            "missing": ["basic-pitch"],
            "unlocks": "polyphonic note, onset, amplitude, and pitch-bend observations from isolated stems",
        }

    def transcribe(self, audio_path: str | Path, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("note transcription is disabled; select the optional basic-pitch provider")


def notes_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def notes_hash_path(path: str | Path) -> str:
    """Hash a model file or directory tree, including relative names and bytes."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        return notes_sha256_file(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(notes_sha256_file(item)))
    return digest.hexdigest()


def notes_canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def notes_observation_payload(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the path- and cache-independent payload identified by the observation hash."""
    payload = dict(observation)
    for field in ("observation_sha256", "cache_status", "model_path", "source_path"):
        payload.pop(field, None)
    return payload


def notes_artifact_key(source_identity: str, provider: str, provider_version: str, model_sha256: str, config: Mapping[str, Any]) -> str:
    payload = {
        "kind": "note_transcription",
        "source_identity": source_identity,
        "provider": provider,
        "provider_version": provider_version,
        "model_sha256": model_sha256,
        "config": dict(config),
    }
    return "notes:" + hashlib.sha256(notes_canonical_json(payload).encode("utf-8")).hexdigest()


def notes_canonicalize_events(note_events: list[tuple[float, float, int, float, list[int] | None]], *, source_identity: str, provider: str, provider_version: str, model_sha256: str, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ordinal, event in enumerate(note_events):
        start, end, pitch, amplitude, bends = event
        if not (float(end) > float(start) >= 0.0):
            continue
        if not 0 <= int(pitch) <= 127:
            continue
        identity_payload = {
            "source_identity": source_identity,
            "provider": provider,
            "provider_version": provider_version,
            "model_sha256": model_sha256,
            "config": dict(config),
            "ordinal": ordinal,
            "start_s": round(float(start), 9),
            "end_s": round(float(end), 9),
            "pitch": int(pitch),
        }
        rows.append({
            "note_id": "obsnote_" + hashlib.sha256(notes_canonical_json(identity_payload).encode("utf-8")).hexdigest()[:24],
            "ordinal": ordinal,
            "start_s": round(float(start), 9),
            "end_s": round(float(end), 9),
            "pitch_midi": int(pitch),
            "amplitude": round(max(0.0, min(1.0, float(amplitude))), 9),
            "velocity": int(round(127.0 * max(0.0, min(1.0, float(amplitude))))),
            "pitch_bends": [int(value) for value in bends] if bends else [],
        })
    return rows


def _notes_model_output_receipt(model_output: Mapping[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {}
    for name, value in sorted(model_output.items()):
        array = np.ascontiguousarray(np.asarray(value))
        receipt[str(name)] = {
            "shape": [int(dimension) for dimension in array.shape],
            "dtype": str(array.dtype),
            "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
            "min": round(float(np.min(array)), 9) if array.size else None,
            "max": round(float(np.max(array)), 9) if array.size else None,
        }
    return receipt


class BasicPitchNoteTranscriber(NoteTranscriber):
    name = "basic-pitch"

    def capability(self) -> dict[str, Any]:
        spec = importlib.util.find_spec("basic_pitch")
        version = ""
        if spec is not None:
            try:
                version = importlib.metadata.version("basic-pitch")
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        return {
            "provider": self.name,
            "ready": spec is not None,
            "version": version,
            "missing": [] if spec is not None else ["basic-pitch>=0.4,<0.5"],
            "unlocks": "polyphonic note, onset, amplitude, and pitch-bend observations from isolated stems",
        }

    def transcribe(self, audio_path: str | Path, *, source_identity: str = "", config: Mapping[str, Any] | None = None, artifact_store: ArtifactStore | None = None) -> dict[str, Any]:
        capability = self.capability()
        if not capability["ready"]:
            raise RuntimeError("Basic Pitch is unavailable: " + ", ".join(capability["missing"]))
        source = Path(audio_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        resolved_config = dict(BASIC_PITCH_DEFAULT_CONFIG)
        resolved_config.update(dict(config or {}))
        if not source_identity:
            source_identity = "byte_sha256:" + notes_sha256_file(source)

        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict

        model_path = Path(str(resolved_config.pop("model_path", ICASSP_2022_MODEL_PATH))).expanduser().resolve()
        model_sha256 = notes_hash_path(model_path)
        provider_version = str(capability.get("version") or "unknown")
        key = notes_artifact_key(source_identity, self.name, provider_version, model_sha256, resolved_config)
        store = artifact_store
        if store is not None and store.has(key):
            cached = store.get(key)
            if cached is not None:
                observation = json.loads(cached["data"].decode("utf-8"))
                observation["cache_status"] = "cached"
                return observation

        model_output, midi_data, note_events = predict(
            source,
            model_or_model_path=model_path,
            onset_threshold=float(resolved_config["onset_threshold"]),
            frame_threshold=float(resolved_config["frame_threshold"]),
            minimum_note_length=float(resolved_config["minimum_note_length_ms"]),
            minimum_frequency=resolved_config["minimum_frequency_hz"],
            maximum_frequency=resolved_config["maximum_frequency_hz"],
            multiple_pitch_bends=bool(resolved_config["multiple_pitch_bends"]),
            melodia_trick=bool(resolved_config["melodia_trick"]),
            midi_tempo=float(resolved_config["midi_tempo"]),
        )
        normalized_events = notes_canonicalize_events(
            note_events,
            source_identity=source_identity,
            provider=self.name,
            provider_version=provider_version,
            model_sha256=model_sha256,
            config=resolved_config,
        )
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as handle:
            midi_path = Path(handle.name)
        try:
            midi_data.write(str(midi_path))
            midi_bytes = midi_path.read_bytes()
        finally:
            with contextlib.suppress(Exception):
                midi_path.unlink()

        observation = {
            "schema_version": NOTE_OBSERVATION_SCHEMA_VERSION,
            "kind": "note_transcription_observation",
            "provider": self.name,
            "provider_version": provider_version,
            "model_path": str(model_path),
            "model_sha256": model_sha256,
            "source_path": str(source),
            "source_identity": source_identity,
            "config": resolved_config,
            "artifact_key": key,
            "note_count": len(normalized_events),
            "notes": normalized_events,
            "midi_sha256": hashlib.sha256(midi_bytes).hexdigest(),
            "model_outputs": _notes_model_output_receipt(model_output),
            "cache_status": "computed",
        }
        observation["observation_sha256"] = hashlib.sha256(
            notes_canonical_json(notes_observation_payload(observation)).encode("utf-8")
        ).hexdigest()
        if store is not None:
            encoded = (json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            store.put(
                key,
                encoded,
                tier="warm",
                source_identity=source_identity,
                provider=self.name,
                version=provider_version,
                extra={"model_sha256": model_sha256, "config": resolved_config, "kind": "note_observation"},
            )
            store.put(
                key + ":midi",
                midi_bytes,
                tier="warm",
                source_identity=source_identity,
                provider=self.name,
                version=provider_version,
                extra={"model_sha256": model_sha256, "config": resolved_config, "kind": "midi"},
            )
        return observation


register("notes", "noop", NoopNoteTranscriber, default=True)
register("notes", "basic-pitch", BasicPitchNoteTranscriber)
