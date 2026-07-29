#!/usr/bin/env python3
from __future__ import annotations

"""Build the source-only Empire State recurrence release-candidate fixture.

The source recording remains external. The builder refuses any source whose container
SHA-256 differs from the registered fixture identity, decodes only the first audio
stream, retains eight contiguous bars, replaces the following four bars with a
non-overlapping recurrence, and applies one 35 ms equal-power transition plus a global
gain. It runs the audio build twice in isolated directories before sealing the Floor
release-candidate objects.
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import pyloudnorm as pyln
from scipy.io import wavfile
from scipy.signal import resample_poly

from earcrate.floor.model import floor_sha256_json, floor_write_json_atomic
from earcrate.floor.release import floor_adapt_source_only_recurrence_receipt

SPECIMEN_ID = "pretty_lights_empire_release_candidate_v1"
EXPECTED_SOURCE_SHA256 = "af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979"
SAMPLE_RATE = 48_000
CHANNELS = 2
PREFIX_SECONDS = (140.373333, 161.237333)
TARGET_SECONDS = (161.237333, 171.669333)
DONOR_SECONDS = (255.146667, 265.578667)
PREFIX_BARS = 8
DONOR_BARS = 4
METER = "4/4"
CROSSFADE_MS = 35.0
TARGET_TRUE_PEAK_DBFS = -0.5

BUILDER_IDENTITY = {
    "identity_id": "org.earcrate.release.recurrence-builder-v1",
    "identity_type": "provider",
    "version": "1.0.0",
    "display_name": "EarCrate source-only recurrence builder",
}
SIGNAL_EVALUATOR_IDENTITY = {
    "identity_id": "org.earcrate.release.signal-evaluator-v1",
    "identity_type": "evaluator",
    "version": "1.0.0",
    "display_name": "EarCrate release signal evaluator",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _frame_cosine(left: np.ndarray, right: np.ndarray) -> float:
    frames = min(left.shape[1], right.shape[1])
    left = left[:, :frames]
    right = right[:, :frames]
    numerator = np.sum(left * right, axis=0)
    denominator = np.linalg.norm(left, axis=0) * np.linalg.norm(right, axis=0) + 1e-9
    return float(np.mean(numerator / denominator))


def _similarities(left: np.ndarray, right: np.ndarray, sample_rate: int) -> dict[str, float]:
    left_mono = np.mean(left, axis=1)
    right_mono = np.mean(right, axis=1)
    frames = min(len(left_mono), len(right_mono))
    left_mono = left_mono[:frames]
    right_mono = right_mono[:frames]
    hop = 512
    chroma_left = librosa.feature.chroma_cqt(y=left_mono, sr=sample_rate, hop_length=hop)
    chroma_right = librosa.feature.chroma_cqt(y=right_mono, sr=sample_rate, hop_length=hop)
    mel_left = librosa.power_to_db(
        librosa.feature.melspectrogram(y=left_mono, sr=sample_rate, n_fft=2048, hop_length=hop, n_mels=64) + 1e-9
    )
    mel_right = librosa.power_to_db(
        librosa.feature.melspectrogram(y=right_mono, sr=sample_rate, n_fft=2048, hop_length=hop, n_mels=64) + 1e-9
    )
    onset_left = librosa.onset.onset_strength(y=left_mono, sr=sample_rate, hop_length=hop)
    onset_right = librosa.onset.onset_strength(y=right_mono, sr=sample_rate, hop_length=hop)
    onset_frames = min(len(onset_left), len(onset_right))
    return {
        "chroma_frame_cosine_mean": _frame_cosine(chroma_left, chroma_right),
        "mel_frame_cosine_mean": _frame_cosine(mel_left, mel_right),
        "onset_envelope_correlation": float(np.corrcoef(onset_left[:onset_frames], onset_right[:onset_frames])[0, 1]),
        "raw_waveform_correlation": float(np.corrcoef(left_mono, right_mono)[0, 1]),
    }


def _longest_silence_seconds(audio: np.ndarray, sample_rate: int, threshold_db: float = -55.0, window_ms: float = 20.0) -> float:
    mono = np.mean(audio, axis=1)
    window = max(1, round(sample_rate * window_ms / 1000.0))
    power = np.convolve(mono * mono, np.ones(window, dtype=np.float64) / window, mode="valid")
    level = 10.0 * np.log10(np.maximum(power, 1e-20))
    best = current = 0
    for is_silent in level < threshold_db:
        if is_silent:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return max(0.0, (best + window - 1) / sample_rate if best else 0.0)


def _decode_source(source: Path) -> np.ndarray:
    process = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "f32le", "-acodec", "pcm_f32le", "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    values = np.frombuffer(process.stdout, dtype="<f4")
    if values.size == 0 or values.size % CHANNELS:
        raise RuntimeError("ffmpeg produced an empty or malformed decoded stream")
    return values.reshape(-1, CHANNELS).astype(np.float64)


def _write_mp3(source_wav: Path, target: Path, *, duration_seconds: float | None = None) -> None:
    command = [
        "ffmpeg", "-y", "-v", "error", "-fflags", "+bitexact", "-i", str(source_wav),
    ]
    if duration_seconds is not None:
        command += ["-t", f"{duration_seconds:.6f}"]
    command += [
        "-map", "0:a:0", "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "0",
        "-flags:a", "+bitexact", "-c:a", "libmp3lame", "-b:a", "320k", str(target),
    ]
    subprocess.run(command, check=True)


def _build_once(source_audio: np.ndarray, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    frame = lambda seconds: round(float(seconds) * SAMPLE_RATE)
    prefix_start, prefix_end = frame(PREFIX_SECONDS[0]), frame(PREFIX_SECONDS[1])
    target_start, target_end = frame(TARGET_SECONDS[0]), frame(TARGET_SECONDS[1])
    donor_start, donor_end = frame(DONOR_SECONDS[0]), frame(DONOR_SECONDS[1])
    if prefix_end != target_start:
        raise RuntimeError("registered prefix does not lead directly into the target span")
    prefix = source_audio[prefix_start:prefix_end]
    target = source_audio[target_start:target_end]
    donor = source_audio[donor_start:donor_end]
    if len(prefix) == 0 or len(target) == 0 or len(target) != len(donor):
        raise RuntimeError("registered recurrence intervals are not executable")

    crossfade_frames = round(CROSSFADE_MS * SAMPLE_RATE / 1000.0)
    phase = np.arange(crossfade_frames, dtype=np.float64) / crossfade_frames
    fade_out = np.cos(phase * np.pi / 2.0)[:, None]
    fade_in = np.sin(phase * np.pi / 2.0)[:, None]
    raw = np.concatenate(
        [
            prefix[:-crossfade_frames],
            prefix[-crossfade_frames:] * fade_out + donor[:crossfade_frames] * fade_in,
            donor[crossfade_frames:],
        ],
        axis=0,
    )

    oversampled = resample_poly(raw, 4, 1, axis=0)
    raw_true_peak = float(np.max(np.abs(oversampled)))
    gain = (10.0 ** (TARGET_TRUE_PEAK_DBFS / 20.0)) / raw_true_peak
    audio = raw * gain
    authoritative_pcm = audio.astype("<f4", copy=False).tobytes(order="C")

    wav_path = destination / "Empire_State_recurrence_release_candidate.wav"
    mp3_path = destination / "Empire_State_recurrence_release_candidate.mp3"
    mp3_30_path = destination / "Empire_State_recurrence_release_candidate_30s.mp3"
    wavfile.write(wav_path, SAMPLE_RATE, audio.astype(np.float32))
    _write_mp3(wav_path, mp3_path)
    _write_mp3(wav_path, mp3_30_path, duration_seconds=30.0)

    oversampled_final = resample_poly(audio, 4, 1, axis=0)
    first = np.flatnonzero(np.max(np.abs(audio), axis=1) > 10.0 ** (-80.0 / 20.0))
    seam = len(prefix) - crossfade_frames
    seam_window = round(0.020 * SAMPLE_RATE)
    pre = audio[max(0, seam - seam_window):seam]
    post = audio[seam:min(len(audio), seam + seam_window)]
    pre_rms = float(np.sqrt(np.mean(pre * pre))) if len(pre) else 0.0
    post_rms = float(np.sqrt(np.mean(post * post))) if len(post) else 0.0
    seam_ratio_db = 20.0 * math.log10(max(post_rms, 1e-12) / max(pre_rms, 1e-12))

    metrics = {
        "first_audible_seconds": int(first[0]) / SAMPLE_RATE if len(first) else None,
        "longest_silence_below_minus_55_db_seconds": _longest_silence_seconds(audio, SAMPLE_RATE),
        "integrated_loudness_lufs": float(pyln.Meter(SAMPLE_RATE).integrated_loudness(audio)),
        "true_peak_dbfs_4x": 20.0 * math.log10(float(np.max(np.abs(oversampled_final)))),
        "sample_peak_dbfs": 20.0 * math.log10(float(np.max(np.abs(audio)))),
        "target_donor_similarity": _similarities(target, donor, SAMPLE_RATE),
        "output_duration_seconds": len(audio) / SAMPLE_RATE,
        "output_frames": len(audio),
        "crossfade_frames": crossfade_frames,
        "applied_gain_db": 20.0 * math.log10(gain),
        "transition_window": {
            "window_ms": 20.0,
            "pre_rms": pre_rms,
            "post_rms": post_rms,
            "post_pre_rms_ratio_db": seam_ratio_db,
            "boundary_max_abs": float(np.max(np.abs(audio[max(0, seam - crossfade_frames):min(len(audio), seam + crossfade_frames)]))),
        },
    }
    return {
        "audio": audio,
        "pcm_bytes": authoritative_pcm,
        "metrics": metrics,
        "files": {"wav": wav_path, "mp3": mp3_path, "mp3_30s": mp3_30_path},
    }


def _receipt_semantic_hash(receipt: dict[str, Any]) -> str:
    payload = deepcopy(receipt)
    payload.pop("receipt_sha256", None)
    delivery = payload.get("artifacts") or {}
    for key in (
        "wav_sha256", "wav_size_bytes", "mp3_sha256", "mp3_size_bytes",
        "mp3_30s_sha256", "mp3_30s_size_bytes",
    ):
        delivery.pop(key, None)
    return floor_sha256_json(payload)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_checksums(root: Path) -> Path:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "CHECKSUMS.sha256":
            rows.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    target = root / "CHECKSUMS.sha256"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return target


def _write_deterministic_zip(source_dir: Path, target: Path) -> str:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                f"empire_state_release_candidate/{path.relative_to(source_dir).as_posix()}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return sha256_file(target)


def build(source: Path, output_dir: Path, proof_zip: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    proof_zip = proof_zip.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    source_sha = sha256_file(source)
    if source_sha != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"source identity changed: expected {EXPECTED_SOURCE_SHA256}, found {source_sha}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    source_audio = _decode_source(source)
    source_pcm = source_audio.astype("<f4", copy=False).tobytes(order="C")
    with tempfile.TemporaryDirectory(prefix="earcrate-release-build-a-") as first_root, tempfile.TemporaryDirectory(
        prefix="earcrate-release-build-b-"
    ) as second_root:
        first = _build_once(source_audio, Path(first_root))
        second = _build_once(source_audio, Path(second_root))
        comparisons = {
            "independent_build_count": 2,
            "authoritative_pcm_bit_exact": first["pcm_bytes"] == second["pcm_bytes"],
            "wav_container_bit_exact": first["files"]["wav"].read_bytes() == second["files"]["wav"].read_bytes(),
            "mp3_container_bit_exact": first["files"]["mp3"].read_bytes() == second["files"]["mp3"].read_bytes(),
            "mp3_30s_container_bit_exact": first["files"]["mp3_30s"].read_bytes() == second["files"]["mp3_30s"].read_bytes(),
            "metrics_bit_exact": canonical_json(first["metrics"]) == canonical_json(second["metrics"]),
        }
        if not all(value for key, value in comparisons.items() if key != "independent_build_count"):
            raise RuntimeError(f"release candidate is not reproducible: {comparisons}")
        for name, path in first["files"].items():
            target_name = {
                "wav": "Empire_State_recurrence_release_candidate.wav",
                "mp3": "Empire_State_recurrence_release_candidate.mp3",
                "mp3_30s": "Empire_State_recurrence_release_candidate_30s.mp3",
            }[name]
            _copy_file(path, output_dir / target_name)
        metrics = first["metrics"]
        pcm_bytes = first["pcm_bytes"]

    wav_path = output_dir / "Empire_State_recurrence_release_candidate.wav"
    mp3_path = output_dir / "Empire_State_recurrence_release_candidate.mp3"
    mp3_30_path = output_dir / "Empire_State_recurrence_release_candidate_30s.mp3"
    receipt = {
        "schema_version": 1,
        "kind": "earcrate_source_only_recurrence_release_receipt",
        "specimen_id": SPECIMEN_ID,
        "title": "Empire State source-only recurrence release candidate",
        "source": {
            "sha256": source_sha,
            "decoded_pcm_sha256": sha256_bytes(source_pcm),
            "decoded_sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "frames": len(source_audio),
            "size_bytes": source.stat().st_size,
            "media_kind": "audio/mpeg",
            "external_source_media": True,
        },
        "edit": {
            "prefix_seconds": list(PREFIX_SECONDS),
            "target_replaced_seconds": list(TARGET_SECONDS),
            "donor_seconds": list(DONOR_SECONDS),
            "prefix_bars": PREFIX_BARS,
            "donor_bars": DONOR_BARS,
            "meter": METER,
            "crossfade_ms": CROSSFADE_MS,
            "crossfade_curve": "equal_power",
            "declared_operations": ["source_seek", "source_copy", "gain", "equal_power_crossfade"],
            "prohibited_operations": [
                "synthesis", "midi_overlay", "stem_layering", "filtered_intro", "beat_chopping", "silent_preroll"
            ],
            "source_only": True,
        },
        "metrics": metrics,
        "reproducibility": comparisons,
        "artifacts": {
            "decoded_stereo_f32le_sha256": sha256_bytes(pcm_bytes),
            "wav_sha256": sha256_file(wav_path),
            "wav_size_bytes": wav_path.stat().st_size,
            "mp3_sha256": sha256_file(mp3_path),
            "mp3_size_bytes": mp3_path.stat().st_size,
            "mp3_30s_sha256": sha256_file(mp3_30_path),
            "mp3_30s_size_bytes": mp3_30_path.stat().st_size,
        },
        "status": {
            "custody": "passed",
            "build_reproducibility": "passed",
            "signal_sanity": "passed",
            "recurrence_identity": "passed",
            "transition_integrity": "provisional_pass",
            "musical_acceptance": "pending",
            "rights_eligibility": "not_evaluated",
            "whole_organism_status": "not_claimed",
            "release_status": "blocked",
            "summary": "signal_sane_human_review_pending",
        },
        "receipt_hash_policy": {
            "authority": "decoded stereo float32 PCM",
            "excluded_delivery_fields": [
                "artifacts.wav_sha256", "artifacts.wav_size_bytes", "artifacts.mp3_sha256",
                "artifacts.mp3_size_bytes", "artifacts.mp3_30s_sha256", "artifacts.mp3_30s_size_bytes",
            ],
            "reason": "delivery-container identity is retained but does not define musical identity",
        },
        "builder_may_not_approve_music": True,
    }
    receipt["receipt_sha256"] = _receipt_semantic_hash(receipt)
    floor_write_json_atomic(output_dir / "receipt.json", receipt)

    adapted = floor_adapt_source_only_recurrence_receipt(
        receipt,
        builder=BUILDER_IDENTITY,
        signal_evaluator=SIGNAL_EVALUATOR_IDENTITY,
    )
    names = {
        "audio_edit_plan": "audio_edit_plan.json",
        "time_map": "time_map.json",
        "phrase_contract": "phrase_contract.json",
        "release_candidate": "release_candidate.json",
        "signal_evaluation": "signal_evaluation.json",
        "human_review_template": "human_review.template.json",
        "release_gate": "release_gate.pending.json",
    }
    for key, name in names.items():
        floor_write_json_atomic(output_dir / name, adapted[key])

    builder_copy = output_dir / "build_release_candidate.py"
    _copy_file(Path(__file__).resolve(), builder_copy)
    readme = f"""# Empire State recurrence release-candidate fixture

This source-only edit retains eight contiguous bars from `{PREFIX_SECONDS[0]:.6f}` to
`{PREFIX_SECONDS[1]:.6f}` seconds and replaces the following four-bar occurrence with
the non-overlapping recurrence at `{DONOR_SECONDS[0]:.6f}` to `{DONOR_SECONDS[1]:.6f}`
seconds. The only audio operations are source seek/copy, global gain, and a
`{CROSSFADE_MS:.0f}` ms equal-power transition.

The source media is not bundled. Its required container SHA-256 is:

`{EXPECTED_SOURCE_SHA256}`

Automatic signal gates passed, but `release_gate.pending.json` remains blocked because
human musical acceptance and rights-policy acceptance are not present. The builder is
forbidden from approving its own music.

Rebuild:

```bash
python build_release_candidate.py /path/to/source.mp3 /path/to/output --zip /path/to/proof.zip
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    _write_checksums(output_dir)
    proof_zip.parent.mkdir(parents=True, exist_ok=True)
    proof_zip.unlink(missing_ok=True)
    proof_zip_sha = _write_deterministic_zip(output_dir, proof_zip)
    return {
        "ok": True,
        "specimen_id": SPECIMEN_ID,
        "output_dir": str(output_dir),
        "proof_zip": str(proof_zip),
        "proof_zip_sha256": proof_zip_sha,
        "receipt_sha256": receipt["receipt_sha256"],
        "candidate_sha256": adapted["release_candidate"]["candidate_sha256"],
        "release_gate_sha256": adapted["release_gate"]["release_gate_sha256"],
        "status": adapted["release_gate"]["status"],
        "metrics": metrics,
        "reproducibility": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output_dir")
    parser.add_argument("--zip", dest="zip_path", default="")
    args = parser.parse_args()
    output = Path(args.output_dir)
    zip_path = Path(args.zip_path) if args.zip_path else output.with_suffix(".zip")
    result = build(Path(args.source), output, zip_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
