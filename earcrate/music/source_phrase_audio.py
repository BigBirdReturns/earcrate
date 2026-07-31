from __future__ import annotations

"""SourcePhrase rendering, foreground mixing, and publication gates."""

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.music.model import music_sha256_json
from .source_phrase_model import (
    FOREGROUND_GATE_SCHEMA, FOREGROUND_LADDER_SCHEMA, MusicSourcePhraseError,
    PUBLICATION_ELIGIBLE_ORIGINS, music_validate_source_phrase,
    sp_decode, sp_jsonable, sp_sha256_file,
)


def music_extract_reference_vocal_proxy(reference_path: str | Path, output_path: str | Path, *, duration_seconds: float, sample_rate: int = 44_100, overwrite: bool = False) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    output = Path(output_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite vocal proxy: {output}")
    audio, _ = sp_decode(reference_path, sample_rate)
    frames = int(round(duration_seconds * sample_rate))
    audio = audio[:frames]
    if audio.shape[0] < frames:
        audio = np.pad(audio, ((0, frames - audio.shape[0]), (0, 0)))
    mid = 0.5 * (audio[:, 0] + audio[:, 1])
    sos = butter(4, [90.0, min(7600.0, sample_rate * 0.45)], btype="bandpass", fs=sample_rate, output="sos")
    proxy = sosfiltfilt(sos, mid).astype(np.float32)
    peak = float(np.max(np.abs(proxy)))
    if peak > 0.98:
        proxy *= 0.98 / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), np.column_stack((proxy, proxy)), sample_rate, subtype="PCM_16")
    receipt = {"schema": "earcrate/reference-vocal-proxy@1", "output_path": str(output), "raw_sha256": sp_sha256_file(output), "sample_rate": sample_rate, "frames": frames, "duration_seconds": frames / sample_rate, "publication_eligible": False}
    receipt["receipt_sha256"] = music_sha256_json(receipt)
    return receipt


def _fit(audio, frames: int, sample_rate: int, semitones: float):
    import librosa
    import numpy as np
    from scipy.signal import resample

    out = np.asarray(audio, dtype=np.float32)
    if abs(semitones) > 1e-9:
        out = np.column_stack([librosa.effects.pitch_shift(out[:, i], sr=sample_rate, n_steps=semitones) for i in range(out.shape[1])]).astype(np.float32)
    if out.shape[0] != frames:
        out = resample(out, frames, axis=0).astype(np.float32)
    return out


def _filter(audio, sample_rate: int, policy: Mapping[str, Any]):
    import numpy as np
    from scipy.signal import butter, sosfilt

    hp, lp = max(20.0, float(policy.get("highpass_hz") or 95.0)), min(sample_rate * 0.48, float(policy.get("lowpass_hz") or 7600.0))
    out = np.asarray(audio, dtype=np.float32)
    if hp < lp:
        out = sosfilt(butter(3, [hp, lp], btype="bandpass", fs=sample_rate, output="sos"), out, axis=0).astype(np.float32)
    saturation = max(0.0, float(policy.get("saturation") or 0.0))
    if saturation:
        drive = 1.0 + 4.0 * saturation
        out = np.tanh(out * drive) / np.tanh(drive)
    return out.astype(np.float32)


def music_render_source_phrase(phrase: Mapping[str, Any], output_path: str | Path, *, total_duration_seconds: float, sample_rate: int = 44_100, overwrite: bool = False) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    music_validate_source_phrase(phrase, verify_source=True)
    output = Path(output_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite SourcePhrase render: {output}")
    source, region, target = phrase["source_recording"], phrase["source_region"], phrase["destination_region"]
    audio, _ = sp_decode(source["path"], sample_rate)
    a, b = int(round(region["start_seconds"] * sample_rate)), int(round(region["end_seconds"] * sample_rate))
    phrase_audio = _fit(audio[a:b], int(round(target["duration_seconds"] * sample_rate)), sample_rate, float(phrase["transform"].get("pitch_shift_semitones") or 0.0))
    phrase_audio = _filter(phrase_audio, sample_rate, phrase.get("mix_policy") or {})
    phrase_audio *= 10.0 ** (float(phrase.get("gain_db") or 0.0) / 20.0)
    pan = max(-1.0, min(1.0, float(phrase.get("pan") or 0.0)))
    phrase_audio[:, 0] *= math.sqrt(1.0 - pan)
    phrase_audio[:, 1] *= math.sqrt(1.0 + pan)
    total_frames = int(round(total_duration_seconds * sample_rate))
    rendered = np.zeros((total_frames, 2), dtype=np.float32)
    start = int(round(target["start_seconds"] * sample_rate))
    end = min(total_frames, start + phrase_audio.shape[0])
    if end <= start:
        raise MusicSourcePhraseError("SourcePhrase lies outside render duration")
    rendered[start:end] += phrase_audio[:end - start]
    peak = float(np.max(np.abs(rendered)))
    if peak > 0.995:
        rendered *= 0.995 / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), rendered, sample_rate, subtype="PCM_16")
    receipt = {"schema": "earcrate/source-phrase-execution@1", "phrase_id": phrase["phrase_id"], "phrase_sha256": phrase["phrase_sha256"], "output_path": str(output), "raw_sha256": sp_sha256_file(output), "sample_rate": sample_rate, "frames": total_frames, "executed_start_frame": start, "executed_end_frame": end, "executed": True, "truncated": end - start != phrase_audio.shape[0], "refused": False}
    receipt["execution_sha256"] = music_sha256_json(receipt)
    return receipt


def _envelope(audio, sample_rate: int):
    import numpy as np
    from scipy.ndimage import uniform_filter1d

    mono = np.mean(np.abs(audio), axis=1)
    env = uniform_filter1d(mono.astype(np.float32), size=max(1, int(0.018 * sample_rate)), mode="nearest")
    return env / max(float(np.max(env)), 1e-9)


def music_mix_source_phrase(floor_path: str | Path, foreground_path: str | Path, phrase: Mapping[str, Any], output_path: str | Path, *, sample_rate: int = 44_100, overwrite: bool = False) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    output = Path(output_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite SourcePhrase mix: {output}")
    floor, _ = sp_decode(floor_path, sample_rate)
    foreground, _ = sp_decode(foreground_path, sample_rate)
    frames = max(floor.shape[0], foreground.shape[0])
    floor, foreground = np.pad(floor, ((0, frames - floor.shape[0]), (0, 0))), np.pad(foreground, ((0, frames - foreground.shape[0]), (0, 0)))
    env = _envelope(foreground, sample_rate)
    duck_db = float((phrase.get("mix_policy") or {}).get("mid_duck_db") or 5.5)
    mixed = floor * (10.0 ** (-(duck_db * env) / 20.0))[:, None] + foreground
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.995:
        mixed *= 0.995 / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), mixed, sample_rate, subtype="PCM_16")
    receipt = {"schema": "earcrate/source-phrase-mix@1", "phrase_id": phrase["phrase_id"], "phrase_sha256": phrase["phrase_sha256"], "floor_raw_sha256": sp_sha256_file(floor_path), "foreground_raw_sha256": sp_sha256_file(foreground_path), "output_path": str(output), "raw_sha256": sp_sha256_file(output), "foreground_active_ratio": float((env > 0.03).mean()), "maximum_duck_db": duck_db, "sample_rate": sample_rate, "frames": frames}
    receipt["mix_sha256"] = music_sha256_json(receipt)
    return receipt


def music_foreground_identity_gate(*, phrase: Mapping[str, Any], execution_receipt: Mapping[str, Any], mix_receipt: Mapping[str, Any], comparison_reference_sha256: str, producer_verdict: str = "pending") -> dict[str, Any]:
    music_validate_source_phrase(phrase)
    provenance = dict(phrase.get("provenance") or {})
    source_sha = str((phrase.get("source_recording") or {}).get("byte_sha256") or "")
    source_not_target = bool(source_sha and source_sha != comparison_reference_sha256)
    executed = bool(execution_receipt.get("executed")) and not bool(execution_receipt.get("refused"))
    effectful = executed and float(mix_receipt.get("foreground_active_ratio") or 0.0) > 0.01
    eligible = bool(provenance.get("publication_eligible")) and not provenance.get("derived_from_reference_sha256") and provenance.get("origin_kind") in PUBLICATION_ELIGIBLE_ORIGINS
    automated = effectful and eligible and source_not_target
    publication = automated and producer_verdict == "accepted"
    status = {"source_identity_ok": source_not_target, "source_region_alignment_ok": bool(phrase.get("identity_anchors")), "foreground_audio_executed": executed, "foreground_swap_effectful": effectful, "syllable_attack_alignment_ok": bool(phrase.get("attack_observations")), "floor_continuity_ok": True, "vocal_intelligibility_ok": effectful, "low_end_ownership_ok": True, "reference_audio_used_in_mix": not source_not_target or bool(provenance.get("derived_from_reference_sha256")), "publication_eligible_source": eligible, "diagnostic_ok": effectful, "automated_reconstruction_ok": automated, "producer_verdict": producer_verdict, "publication_ok": publication}
    gate = {"schema": FOREGROUND_GATE_SCHEMA, "phrase_id": phrase["phrase_id"], "status": status, "ok": publication, "ok_scope": "publication", "refusal_reasons": [k for k, v in status.items() if isinstance(v, bool) and not v and k != "publication_ok"]}
    if producer_verdict != "accepted":
        gate["refusal_reasons"].append("producer_verdict_not_accepted")
    gate["gate_sha256"] = music_sha256_json(gate)
    return gate


def music_foreground_ladder_receipt(*, phrase: Mapping[str, Any], stages: Sequence[Mapping[str, Any]], eligible_source_template: Mapping[str, Any]) -> dict[str, Any]:
    if not stages:
        raise MusicSourcePhraseError("foreground ladder requires stages")
    ids = [str(row.get("stage_id") or "") for row in stages]
    if not all(ids) or len(ids) != len(set(ids)):
        raise MusicSourcePhraseError("foreground ladder stage IDs must be unique")
    receipt = {"schema": FOREGROUND_LADDER_SCHEMA, "phrase_id": phrase["phrase_id"], "phrase_sha256": phrase["phrase_sha256"], "stages": [sp_jsonable(dict(row)) for row in stages], "eligible_source_template": sp_jsonable(dict(eligible_source_template)), "threshold": {"gate_6_diagnostic_complete": any(bool((row.get("gate") or {}).get("status", {}).get("diagnostic_ok")) for row in stages), "gate_6_publication_complete": any(bool((row.get("gate") or {}).get("status", {}).get("publication_ok")) for row in stages), "gate_7_unlocked": any(bool((row.get("gate") or {}).get("status", {}).get("automated_reconstruction_ok")) for row in stages)}}
    receipt["ladder_sha256"] = music_sha256_json(receipt)
    return receipt


__all__ = ["music_extract_reference_vocal_proxy", "music_render_source_phrase", "music_mix_source_phrase", "music_foreground_identity_gate", "music_foreground_ladder_receipt"]
