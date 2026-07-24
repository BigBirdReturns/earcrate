from __future__ import annotations

"""SourcePhrase identity, registration, alignment, and validation."""

from copy import deepcopy
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from earcrate.music.model import music_sha256_json

SOURCE_PHRASE_SCHEMA = "earcrate/source-phrase@1"
SOURCE_PHRASE_EXECUTION_SCHEMA = "earcrate/source-phrase-execution@1"
FOREGROUND_GATE_SCHEMA = "earcrate/foreground-identity-gate@1"
FOREGROUND_LADDER_SCHEMA = "earcrate/foreground-gate-ladder@1"
SOURCE_PHRASE_TEMPLATE_SCHEMA = "earcrate/source-phrase-registration-template@1"
PUBLICATION_ELIGIBLE_ORIGINS = {
    "owned_source_recording", "licensed_source_recording", "sealed_rack_atom", "commissioned_performance"
}
DIAGNOSTIC_ORIGINS = {"comparison_reference", "reference_derived_stem", "reference_derived_proxy"}


class MusicSourcePhraseError(ValueError):
    pass


def sp_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sp_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MusicSourcePhraseError("source phrase cannot contain non-finite values")
        return value
    if isinstance(value, Mapping):
        return {str(k): sp_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sp_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return sp_jsonable(value.item())
    return str(value)


def sp_payload(value: Mapping[str, Any], hash_key: str) -> dict[str, Any]:
    return {str(k): sp_jsonable(v) for k, v in value.items() if str(k) != hash_key}


def sp_decode(path: str | Path, sample_rate: int):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MusicSourcePhraseError(f"missing source recording: {source}")
    try:
        audio, rate = sf.read(str(source), always_2d=True, dtype="float32")
    except Exception as exc:
        raise MusicSourcePhraseError(f"cannot decode {source}: {exc}") from exc
    if not audio.size:
        raise MusicSourcePhraseError(f"source recording decoded zero frames: {source}")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    elif audio.shape[1] > 2:
        audio = audio[:, :2]
    if int(rate) != int(sample_rate):
        divisor = math.gcd(int(rate), int(sample_rate))
        audio = resample_poly(audio, int(sample_rate) // divisor, int(rate) // divisor, axis=0)
    return np.asarray(audio, dtype=np.float32), int(sample_rate)


def _source_info(path: str | Path) -> tuple[float, int, int]:
    import soundfile as sf

    source = Path(path).expanduser().resolve()
    info = sf.info(str(source))
    if info.frames <= 0 or info.samplerate <= 0:
        raise MusicSourcePhraseError("source recording has invalid duration")
    return float(info.frames / info.samplerate), int(info.frames), int(info.samplerate)


def _slice_sha(path: str | Path, start: float, end: float, sample_rate: int = 44_100) -> str:
    import numpy as np

    audio, _ = sp_decode(path, sample_rate)
    a, b = int(round(start * sample_rate)), int(round(end * sample_rate))
    if a < 0 or b <= a or b > audio.shape[0]:
        raise MusicSourcePhraseError("source phrase slice is invalid")
    return hashlib.sha256(np.asarray(audio[a:b], dtype="<f4").tobytes()).hexdigest()


def _features(path: str | Path, start: float, end: float | None, sample_rate: int = 11_025):
    import numpy as np
    from scipy.signal import stft

    audio, _ = sp_decode(path, sample_rate)
    a = max(0, int(round(start * sample_rate)))
    b = audio.shape[0] if end is None else min(audio.shape[0], int(round(end * sample_rate)))
    mono = np.mean(audio[a:b], axis=1)
    if mono.size < 2048:
        raise MusicSourcePhraseError("alignment audio is too short")
    frequencies, _, spectrum = stft(mono, fs=sample_rate, nperseg=1024, noverlap=768, boundary=None)
    magnitude = np.abs(spectrum) + 1e-9
    bands = []
    for low, high in ((80, 250), (250, 900), (900, 2600), (2600, 4800)):
        mask = (frequencies >= low) & (frequencies < high)
        bands.append(np.log1p(magnitude[mask].mean(axis=0)))
    energy = np.log1p(magnitude.mean(axis=0))
    flux = np.maximum(0.0, np.diff(energy, prepend=energy[:1]))
    features = np.vstack((*bands, energy, flux)).astype(np.float32)
    features -= features.mean(axis=1, keepdims=True)
    features /= np.maximum(features.std(axis=1, keepdims=True), 1e-6)
    norms = np.linalg.norm(features, axis=0, keepdims=True)
    return features / np.maximum(norms, 1e-6), 256, sample_rate


def music_align_source_phrase(
    source_path: str | Path,
    comparison_vocal_proxy_path: str | Path,
    *,
    destination_start_seconds: float,
    destination_end_seconds: float,
    source_search_start_seconds: float = 0.0,
    source_search_end_seconds: float | None = None,
    sample_rate: int = 11_025,
) -> dict[str, Any]:
    import numpy as np

    target, hop, rate = _features(
        comparison_vocal_proxy_path, destination_start_seconds, destination_end_seconds, sample_rate
    )
    source, _, _ = _features(source_path, source_search_start_seconds, source_search_end_seconds, sample_rate)
    width = target.shape[1]
    if source.shape[1] < width:
        raise MusicSourcePhraseError("source search window is shorter than target phrase")
    target_flat = target.reshape(-1)
    target_norm = max(float(np.linalg.norm(target_flat)), 1e-9)
    best_index, best_score = 0, -1e9
    for index in range(source.shape[1] - width + 1):
        candidate = source[:, index:index + width].reshape(-1)
        score = float(np.dot(target_flat, candidate) / (target_norm * max(float(np.linalg.norm(candidate)), 1e-9)))
        if score > best_score:
            best_index, best_score = index, score
    duration = float(destination_end_seconds - destination_start_seconds)
    start = float(source_search_start_seconds) + best_index * hop / rate
    end = start + duration
    receipt = {
        "schema": "earcrate/source-phrase-alignment@1",
        "source_path": str(Path(source_path).expanduser().resolve()),
        "source_region": {"start_seconds": round(start, 9), "end_seconds": round(end, 9)},
        "destination_region": {
            "start_seconds": round(float(destination_start_seconds), 9),
            "end_seconds": round(float(destination_end_seconds), 9),
        },
        "normalized_similarity": round(best_score, 9),
        "feature_rate": rate / hop,
    }
    receipt["alignment_sha256"] = music_sha256_json(receipt)
    return receipt


def _onsets(path: str | Path, start: float, end: float) -> list[dict[str, Any]]:
    import numpy as np
    from scipy.signal import find_peaks

    rate = 22_050
    audio, _ = sp_decode(path, rate)
    a, b = int(round(start * rate)), int(round(end * rate))
    mono = np.mean(audio[a:b], axis=1)
    frame, hop = 1024, 256
    if mono.size < frame:
        return [{"attack_id": "attack_0000", "source_relative_seconds": 0.0, "strength": 1.0}]
    energy = np.array([np.sqrt(np.mean(mono[i:i + frame] ** 2) + 1e-12) for i in range(0, mono.size - frame + 1, hop)])
    flux = np.maximum(0.0, np.diff(energy, prepend=energy[:1]))
    threshold = float(np.median(flux) + 2.5 * np.median(np.abs(flux - np.median(flux))))
    peaks, _ = find_peaks(flux, height=max(threshold, 1e-8), distance=2)
    if not peaks.size:
        peaks = np.asarray([int(np.argmax(flux))])
    peaks = peaks[:96]
    maximum = max(float(flux[peaks].max()), 1e-9)
    return [
        {
            "attack_id": f"attack_{i:04d}",
            "source_relative_seconds": round(float(frame_index * hop / rate), 9),
            "strength": round(float(flux[frame_index]) / maximum, 9),
        }
        for i, frame_index in enumerate(peaks)
    ]


def music_source_phrase_registration_template(
    *, identity_label: str = "The Notorious B.I.G. — Juicy — opening verse",
    destination_start_seconds: float = 21.432018,
    destination_end_seconds: float = 30.0,
) -> dict[str, Any]:
    template = {
        "schema": SOURCE_PHRASE_TEMPLATE_SCHEMA,
        "identity_label": identity_label,
        "source_path": "<path to independently owned/licensed source>",
        "origin_kind": "owned_source_recording",
        "publication_eligible": True,
        "source_role": "foreground_vocal",
        "source_start_seconds": "auto",
        "source_end_seconds": "auto",
        "source_search_start_seconds": 0.0,
        "source_search_end_seconds": None,
        "destination_start_seconds": float(destination_start_seconds),
        "destination_end_seconds": float(destination_end_seconds),
        "comparison_reference_sha256": "<reference sha256; must differ from source>",
        "pitch_shift_semitones": 0.0,
        "tuning_cents": 0.0,
        "gain_db": -1.5,
        "pan": 0.0,
        "mix_policy": {
            "mid_duck_db": 5.5, "low_duck_db": 1.5, "high_duck_db": 2.0,
            "attack_ms": 8.0, "release_ms": 110.0,
            "highpass_hz": 95.0, "lowpass_hz": 7600.0, "saturation": 0.16,
        },
    }
    template["template_sha256"] = music_sha256_json(template)
    return template


def music_build_source_phrase(
    source_path: str | Path, *, identity_label: str, origin_kind: str,
    publication_eligible: bool, source_role: str, source_start_seconds: float,
    source_end_seconds: float, destination_start_seconds: float,
    destination_end_seconds: float, comparison_reference_sha256: str = "",
    derived_from_reference_sha256: str = "", pitch_shift_semitones: float = 0.0,
    tuning_cents: float = 0.0, gain_db: float = -1.5, pan: float = 0.0,
    mix_policy: Mapping[str, Any] | None = None,
    provider_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    duration, frames, rate = _source_info(source)
    a, b, ta, tb = map(float, (source_start_seconds, source_end_seconds, destination_start_seconds, destination_end_seconds))
    if a < 0 or b <= a or b > duration + 1e-6 or ta < 0 or tb <= ta:
        raise MusicSourcePhraseError("invalid source or destination interval")
    byte_sha = sp_sha256_file(source)
    if publication_eligible and comparison_reference_sha256 and byte_sha == comparison_reference_sha256:
        raise MusicSourcePhraseError("comparison target cannot be an eligible source phrase")
    source_span, target_span = b - a, tb - ta
    attacks = _onsets(source, a, b)
    mapped = [{**row, "destination_seconds": round(ta + row["source_relative_seconds"] * target_span / source_span, 9)} for row in attacks]
    strongest = sorted(mapped, key=lambda row: (-row["strength"], row["destination_seconds"]))[:8]
    policy = {"mid_duck_db": 5.5, "low_duck_db": 1.5, "high_duck_db": 2.0, "attack_ms": 8.0,
              "release_ms": 110.0, "highpass_hz": 95.0, "lowpass_hz": 7600.0, "saturation": 0.16}
    policy.update({str(k): sp_jsonable(v) for k, v in dict(mix_policy or {}).items()})
    phrase = {
        "schema": SOURCE_PHRASE_SCHEMA,
        "identity_label": str(identity_label), "source_role": str(source_role),
        "source_recording": {"path": str(source), "byte_sha256": byte_sha, "duration_seconds": round(duration, 9), "frames": frames, "sample_rate": rate},
        "source_region": {"start_seconds": round(a, 9), "end_seconds": round(b, 9), "duration_seconds": round(source_span, 9), "pcm_sha256_44100_stereo_f32": _slice_sha(source, a, b)},
        "destination_region": {"start_seconds": round(ta, 9), "end_seconds": round(tb, 9), "duration_seconds": round(target_span, 9)},
        "attack_observations": mapped,
        "identity_anchors": [{"anchor_id": f"anchor_{i:02d}", "source_relative_seconds": row["source_relative_seconds"], "destination_seconds": row["destination_seconds"], "strength": row["strength"]} for i, row in enumerate(sorted(strongest, key=lambda row: row["destination_seconds"]))],
        "transform": {"time_ratio": round(target_span / source_span, 12), "pitch_shift_semitones": round(float(pitch_shift_semitones), 9), "tuning_cents": round(float(tuning_cents), 9), "mode": "identity" if abs(target_span - source_span) < 1e-6 and abs(pitch_shift_semitones) < 1e-9 else "phase_vocoder"},
        "mix_policy": policy, "gain_db": round(float(gain_db), 9), "pan": round(float(pan), 9),
        "provenance": {"origin_kind": str(origin_kind), "publication_eligible": bool(publication_eligible), "comparison_reference_sha256": str(comparison_reference_sha256), "derived_from_reference_sha256": str(derived_from_reference_sha256), "provider_receipt": sp_jsonable(dict(provider_receipt or {}))},
    }
    phrase["phrase_id"] = "source_phrase_" + music_sha256_json(sp_payload(phrase, "phrase_sha256"))[:24]
    phrase["phrase_sha256"] = music_sha256_json(sp_payload(phrase, "phrase_sha256"))
    music_validate_source_phrase(phrase, verify_source=True)
    return phrase


def music_resolve_source_phrase_registration(spec: Mapping[str, Any], *, comparison_vocal_proxy_path: str | Path, comparison_reference_sha256: str) -> dict[str, Any]:
    row = deepcopy(dict(spec))
    required = ("source_path", "identity_label", "origin_kind", "source_role", "destination_start_seconds", "destination_end_seconds")
    missing = [key for key in required if row.get(key) in {None, ""}]
    if missing:
        raise MusicSourcePhraseError(f"source phrase registration is incomplete: {missing}")
    start, end, alignment = row.get("source_start_seconds"), row.get("source_end_seconds"), None
    if start in {None, "", "auto"} or end in {None, "", "auto"}:
        alignment = music_align_source_phrase(
            row["source_path"], comparison_vocal_proxy_path,
            destination_start_seconds=float(row["destination_start_seconds"]),
            destination_end_seconds=float(row["destination_end_seconds"]),
            source_search_start_seconds=float(row.get("source_search_start_seconds") or 0.0),
            source_search_end_seconds=None if row.get("source_search_end_seconds") in {None, ""} else float(row["source_search_end_seconds"]),
        )
        start, end = alignment["source_region"]["start_seconds"], alignment["source_region"]["end_seconds"]
    provider = deepcopy(dict(row.get("provider_receipt") or {}))
    if alignment:
        provider["alignment"] = alignment
    return music_build_source_phrase(
        row["source_path"], identity_label=str(row["identity_label"]), origin_kind=str(row["origin_kind"]),
        publication_eligible=bool(row.get("publication_eligible", True)), source_role=str(row["source_role"]),
        source_start_seconds=float(start), source_end_seconds=float(end),
        destination_start_seconds=float(row["destination_start_seconds"]), destination_end_seconds=float(row["destination_end_seconds"]),
        comparison_reference_sha256=str(comparison_reference_sha256), derived_from_reference_sha256=str(row.get("derived_from_reference_sha256") or ""),
        pitch_shift_semitones=float(row.get("pitch_shift_semitones") or 0.0), tuning_cents=float(row.get("tuning_cents") or 0.0),
        gain_db=float(row.get("gain_db") if row.get("gain_db") is not None else -1.5), pan=float(row.get("pan") or 0.0),
        mix_policy=row.get("mix_policy") or {}, provider_receipt=provider,
    )


def music_validate_source_phrase(phrase: Mapping[str, Any], *, verify_source: bool = False) -> None:
    if phrase.get("schema") != SOURCE_PHRASE_SCHEMA or not phrase.get("phrase_id"):
        raise MusicSourcePhraseError("unsupported or unidentified source phrase")
    source, region, target, provenance = map(dict, (phrase.get("source_recording") or {}, phrase.get("source_region") or {}, phrase.get("destination_region") or {}, phrase.get("provenance") or {}))
    if not source.get("path") or len(str(source.get("byte_sha256") or "")) != 64:
        raise MusicSourcePhraseError("source phrase requires exact source identity")
    if float(region.get("start_seconds", -1)) < 0 or float(region.get("end_seconds", -1)) <= float(region.get("start_seconds", -1)):
        raise MusicSourcePhraseError("invalid source region")
    if float(target.get("start_seconds", -1)) < 0 or float(target.get("end_seconds", -1)) <= float(target.get("start_seconds", -1)):
        raise MusicSourcePhraseError("invalid destination region")
    origin, eligible = str(provenance.get("origin_kind") or ""), bool(provenance.get("publication_eligible"))
    if eligible and (origin not in PUBLICATION_ELIGIBLE_ORIGINS or provenance.get("derived_from_reference_sha256")):
        raise MusicSourcePhraseError("invalid publication provenance")
    if eligible and provenance.get("comparison_reference_sha256") == source.get("byte_sha256"):
        raise MusicSourcePhraseError("comparison reference cannot be an eligible source")
    if not eligible and origin not in PUBLICATION_ELIGIBLE_ORIGINS | DIAGNOSTIC_ORIGINS:
        raise MusicSourcePhraseError("unknown source origin")
    attack_ids = [str(row.get("attack_id") or "") for row in phrase.get("attack_observations") or []]
    if not attack_ids or not all(attack_ids) or len(attack_ids) != len(set(attack_ids)):
        raise MusicSourcePhraseError("source phrase requires unique attacks")
    if phrase.get("phrase_sha256") != music_sha256_json(sp_payload(phrase, "phrase_sha256")):
        raise MusicSourcePhraseError("phrase hash mismatch")
    if verify_source:
        path = Path(str(source["path"])).expanduser().resolve()
        if sp_sha256_file(path) != source["byte_sha256"]:
            raise MusicSourcePhraseError("source recording identity changed")
        if _slice_sha(path, float(region["start_seconds"]), float(region["end_seconds"])) != region.get("pcm_sha256_44100_stereo_f32"):
            raise MusicSourcePhraseError("source slice identity changed")


__all__ = [
    "SOURCE_PHRASE_SCHEMA", "SOURCE_PHRASE_EXECUTION_SCHEMA", "FOREGROUND_GATE_SCHEMA",
    "FOREGROUND_LADDER_SCHEMA", "SOURCE_PHRASE_TEMPLATE_SCHEMA", "PUBLICATION_ELIGIBLE_ORIGINS",
    "DIAGNOSTIC_ORIGINS", "MusicSourcePhraseError", "sp_sha256_file", "sp_jsonable", "sp_decode",
    "music_source_phrase_registration_template", "music_align_source_phrase", "music_resolve_source_phrase_registration",
    "music_build_source_phrase", "music_validate_source_phrase",
]
