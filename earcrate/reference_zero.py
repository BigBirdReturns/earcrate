from __future__ import annotations

"""Reference Zero performance-score authority and deterministic renderer.

This module separates three claims that EarCrate previously collapsed:

1. a human-authored arrangement can be represented exactly;
2. the renderer can reproduce that representation without inventing decisions;
3. a later inference system can recover a comparable score from source evidence.

The score is portable and contains no local paths. A private binding manifest maps
source ids to exact local artifacts. Rendering is fail-closed: source mutation,
unsupported transforms, incomplete clip accounting, or output-identity drift are
errors rather than silent fallbacks.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

SCHEMA_VERSION = 1
HASH_FIELDS = {
    "earcrate_performance_score": "score_sha256",
    "earcrate_reference_zero_source_registry": "registry_sha256",
    "earcrate_performance_source_bindings": "bindings_sha256",
    "earcrate_performance_render_receipt": "receipt_sha256",
    "earcrate_reference_zero_gold_receipt": "gold_receipt_sha256",
    "earcrate_reference_zero_review_assignment": "assignment_sha256",
    "earcrate_reference_zero_private_review_authority": "authority_sha256",
    "earcrate_reference_zero_review_submission": "submission_sha256",
    "earcrate_reference_zero_review_ledger": "ledger_sha256",
    "earcrate_reference_zero_recovery_challenge": "challenge_sha256",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|file://)", re.IGNORECASE)
AUDIO_SUFFIXES = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus"}
DEFAULT_REVIEW_DIMENSIONS = (
    "one-band coherence",
    "lead vocal authority",
    "pulse and backbeat lock",
    "percussion impact",
    "harmonic stability",
    "phrase freedom",
    "transform and separation artifacts",
    "desire to hear the next section",
)


class ReferenceZeroError(RuntimeError):
    """Base error for explicit Reference Zero refusals."""


class ValidationError(ReferenceZeroError):
    """Raised when an authority object violates its contract."""


class SourceMutationError(ReferenceZeroError):
    """Raised when a local artifact no longer matches the score/binding identity."""


@dataclass(frozen=True)
class BoundSource:
    source_id: str
    artifact_path: Path
    container_sha256: str
    canonical_pcm_sha256: str | None


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").lower()))


def seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HASH_FIELDS.get(kind)
    if not field:
        raise ValidationError(f"unknown Reference Zero object kind: {kind!r}")
    value.pop(field, None)
    value[field] = sha256_bytes(canonical_json_bytes(value))
    return value


def validate_seal(payload: Mapping[str, Any], *, kind: str | None = None) -> str:
    value = deepcopy(dict(payload))
    actual_kind = str(value.get("kind") or "")
    if kind and actual_kind != kind:
        raise ValidationError(f"expected {kind}, got {actual_kind!r}")
    field = HASH_FIELDS.get(actual_kind)
    if not field:
        raise ValidationError(f"unknown Reference Zero object kind: {actual_kind!r}")
    if int(value.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValidationError("unsupported Reference Zero schema version")
    claimed = str(value.pop(field, "")).lower()
    if not is_sha256(claimed):
        raise ValidationError(f"invalid or missing {field}")
    computed = sha256_bytes(canonical_json_bytes(value))
    if computed != claimed:
        raise ValidationError(f"{field} mismatch: expected {claimed}, computed {computed}")
    return claimed


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"JSON object required: {path}")
    return value


def atomic_write(path: str | Path, data: bytes, *, exclusive: bool = False) -> Path:
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and target.exists():
        raise FileExistsError(target)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and target.exists():
            raise FileExistsError(target)
        os.replace(temporary, target)
        if os.name != "nt":
            descriptor_dir = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor_dir)
            finally:
                os.close(descriptor_dir)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def write_json(path: str | Path, value: Mapping[str, Any], *, exclusive: bool = False) -> Path:
    return atomic_write(
        path,
        (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        exclusive=exclusive,
    )


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _require_portable(value: Mapping[str, Any], *, label: str) -> None:
    leaked = [text for text in _walk_strings(value) if _ABSOLUTE_PATH_RE.match(text.strip())]
    if leaked:
        raise ValidationError(f"{label} contains absolute/local paths: {leaked[:3]}")


def _require_number(value: Any, *, label: str, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise ValidationError(f"{label} below minimum {minimum}")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{label} above maximum {maximum}")
    return number


def _require_int(value: Any, *, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be an integer") from exc
    if number != value and str(number) != str(value):
        raise ValidationError(f"{label} must be an integer")
    if minimum is not None and number < minimum:
        raise ValidationError(f"{label} below minimum {minimum}")
    return number


def validate_performance_score(score: Mapping[str, Any]) -> str:
    score_sha = validate_seal(score, kind="earcrate_performance_score")
    _require_portable(score, label="performance score")
    timeline = dict(score.get("timeline") or {})
    sample_rate = _require_int(timeline.get("sample_rate"), label="timeline.sample_rate", minimum=8000)
    channels = _require_int(timeline.get("channels"), label="timeline.channels", minimum=1)
    if channels not in {1, 2}:
        raise ValidationError("renderer v1 supports mono or stereo output")
    duration_samples = _require_int(timeline.get("duration_samples"), label="timeline.duration_samples", minimum=1)

    sources = [dict(row) for row in score.get("sources") or []]
    if not sources:
        raise ValidationError("performance score requires at least one source")
    source_ids: set[str] = set()
    for row in sources:
        source_id = str(row.get("source_id") or "").strip()
        if not source_id or source_id in source_ids:
            raise ValidationError(f"invalid or duplicate source_id: {source_id!r}")
        source_ids.add(source_id)
        if not is_sha256(row.get("container_sha256")):
            raise ValidationError(f"source {source_id} requires container_sha256")
        pcm = row.get("canonical_pcm_sha256")
        if pcm not in {None, ""} and not is_sha256(pcm):
            raise ValidationError(f"source {source_id} has invalid canonical_pcm_sha256")

    tracks = [dict(row) for row in score.get("tracks") or []]
    if not tracks:
        raise ValidationError("performance score requires at least one track")
    track_ids: set[str] = set()
    clip_ids: set[str] = set()
    referenced_sources: set[str] = set()
    clip_count = 0
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id or track_id in track_ids:
            raise ValidationError(f"invalid or duplicate track_id: {track_id!r}")
        track_ids.add(track_id)
        clips = [dict(row) for row in track.get("clips") or []]
        for clip in clips:
            clip_count += 1
            clip_id = str(clip.get("clip_id") or "").strip()
            if not clip_id or clip_id in clip_ids:
                raise ValidationError(f"invalid or duplicate clip_id: {clip_id!r}")
            clip_ids.add(clip_id)
            source_id = str(clip.get("source_id") or "")
            if source_id not in source_ids:
                raise ValidationError(f"clip {clip_id} references unknown source {source_id!r}")
            referenced_sources.add(source_id)
            source_start = _require_int(clip.get("source_start_sample"), label=f"{clip_id}.source_start_sample", minimum=0)
            source_end = _require_int(clip.get("source_end_sample"), label=f"{clip_id}.source_end_sample", minimum=1)
            if source_end <= source_start:
                raise ValidationError(f"clip {clip_id} has non-positive source duration")
            target_start = _require_int(clip.get("target_start_sample"), label=f"{clip_id}.target_start_sample", minimum=0)
            tempo_scale = _require_number(clip.get("tempo_scale", 1.0), label=f"{clip_id}.tempo_scale", minimum=0.25, maximum=4.0)
            _require_number(clip.get("pitch_semitones", 0.0), label=f"{clip_id}.pitch_semitones", minimum=-24.0, maximum=24.0)
            _require_number(clip.get("gain_db", 0.0), label=f"{clip_id}.gain_db", minimum=-120.0, maximum=24.0)
            _require_number(clip.get("pan", 0.0), label=f"{clip_id}.pan", minimum=-1.0, maximum=1.0)
            output_duration = max(1, round((source_end - source_start) / tempo_scale))
            fade_in = _require_int(clip.get("fade_in_samples", 0), label=f"{clip_id}.fade_in_samples", minimum=0)
            fade_out = _require_int(clip.get("fade_out_samples", 0), label=f"{clip_id}.fade_out_samples", minimum=0)
            if fade_in + fade_out > output_duration:
                raise ValidationError(f"clip {clip_id} fades exceed transformed duration")
            if target_start + output_duration > duration_samples:
                raise ValidationError(f"clip {clip_id} extends past timeline duration")
    if clip_count == 0:
        raise ValidationError("performance score requires at least one clip")
    unused = sorted(source_ids - referenced_sources)
    if unused and not bool((score.get("authority") or {}).get("allow_unused_sources")):
        raise ValidationError(f"unused score sources: {unused}")

    history = [dict(row) for row in score.get("command_history") or []]
    sequence = [int(row.get("sequence")) for row in history]
    if sequence and sequence != sorted(sequence):
        raise ValidationError("command_history sequence must be monotonic")
    if len(sequence) != len(set(sequence)):
        raise ValidationError("command_history sequence values must be unique")
    for row in history:
        if not str(row.get("command_id") or ""):
            raise ValidationError("every command_history row requires command_id")

    master = dict(score.get("master") or {})
    _require_number(master.get("gain_db", 0.0), label="master.gain_db", minimum=-120.0, maximum=24.0)
    limiter = master.get("peak_limit_dbfs")
    if limiter not in {None, ""}:
        _require_number(limiter, label="master.peak_limit_dbfs", minimum=-24.0, maximum=0.0)
    codec = str(master.get("codec") or "pcm_s24le")
    if codec not in {"pcm_s16le", "pcm_s24le", "pcm_s32le", "flac"}:
        raise ValidationError(f"unsupported master codec: {codec}")
    return score_sha


def validate_source_bindings(bindings: Mapping[str, Any], score: Mapping[str, Any]) -> str:
    binding_sha = validate_seal(bindings, kind="earcrate_performance_source_bindings")
    score_sha = validate_performance_score(score)
    if bindings.get("score_sha256") != score_sha:
        raise ValidationError("bindings belong to another performance score")
    expected = {str(row["source_id"]): dict(row) for row in score.get("sources") or []}
    rows = [dict(row) for row in bindings.get("bindings") or []]
    seen: set[str] = set()
    for row in rows:
        source_id = str(row.get("source_id") or "")
        if source_id not in expected or source_id in seen:
            raise ValidationError(f"unknown or duplicate binding source_id: {source_id!r}")
        seen.add(source_id)
        path = Path(str(row.get("artifact_path") or "")).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"binding path must be a regular non-symlink file: {path}")
        declared = str(row.get("container_sha256") or "").lower()
        if declared != expected[source_id].get("container_sha256"):
            raise ValidationError(f"binding identity disagrees with score for {source_id}")
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValidationError(f"missing source bindings: {missing}")
    return binding_sha


def _run(argv: Sequence[str], *, cwd: Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    result = _run([ffmpeg, "-version"], timeout=30)
    if result.returncode != 0:
        raise ReferenceZeroError(f"ffmpeg unavailable: {result.stderr[-1000:]}")
    return (result.stdout.splitlines() or [""])[0].strip()


def ffprobe_audio(path: str | Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    result = _run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_streams", "-show_format", "-of", "json", str(path)],
        timeout=120,
    )
    if result.returncode != 0:
        raise ReferenceZeroError(f"ffprobe failed for {path}: {result.stderr[-2000:]}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ReferenceZeroError("ffprobe returned a non-object")
    return value


def canonical_pcm_sha256(path: str | Path, *, sample_rate: int, channels: int, ffmpeg: str = "ffmpeg") -> str:
    process = subprocess.Popen(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-c:a",
            "pcm_s32le",
            "-f",
            "s32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    digest = hashlib.sha256()
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", "replace")
    returncode = process.wait(timeout=3600)
    if returncode != 0:
        raise ReferenceZeroError(f"canonical PCM decode failed: {stderr[-2000:]}")
    return digest.hexdigest()


def _ffmpeg_has_filter(name: str, ffmpeg: str = "ffmpeg") -> bool:
    result = _run([ffmpeg, "-hide_banner", "-filters"], timeout=60)
    return result.returncode == 0 and re.search(rf"\b{re.escape(name)}\b", result.stdout) is not None


def _linear_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _pan_gains(pan: float) -> tuple[float, float]:
    angle = (pan + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def _binding_index(bindings: Mapping[str, Any], score: Mapping[str, Any], *, verify_pcm: bool, ffmpeg: str) -> dict[str, BoundSource]:
    timeline = dict(score["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    source_rows = {str(row["source_id"]): dict(row) for row in score["sources"]}
    result: dict[str, BoundSource] = {}
    for row in bindings["bindings"]:
        source_id = str(row["source_id"])
        path = Path(str(row["artifact_path"])).expanduser().absolute()
        before = path.stat()
        container = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SourceMutationError(f"source changed while hashing: {path}")
        expected = str(source_rows[source_id]["container_sha256"])
        if container != expected or container != str(row["container_sha256"]):
            raise SourceMutationError(f"container SHA-256 mismatch for {source_id}")
        expected_pcm = source_rows[source_id].get("canonical_pcm_sha256") or row.get("canonical_pcm_sha256")
        actual_pcm = None
        if verify_pcm or expected_pcm:
            actual_pcm = canonical_pcm_sha256(path, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
            if expected_pcm and actual_pcm != expected_pcm:
                raise SourceMutationError(f"canonical PCM SHA-256 mismatch for {source_id}")
        result[source_id] = BoundSource(source_id, path, container, actual_pcm)
    return result


def _clip_rows(score: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for track in score.get("tracks") or []:
        for clip in track.get("clips") or []:
            rows.append((dict(track), dict(clip)))
    rows.sort(key=lambda pair: (int(pair[1]["target_start_sample"]), str(pair[0]["track_id"]), str(pair[1]["clip_id"])))
    return rows


def render_performance_score(
    score: Mapping[str, Any],
    bindings: Mapping[str, Any],
    *,
    output_path: str | Path,
    receipt_path: str | Path | None = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    verify_source_pcm: bool = False,
) -> dict[str, Any]:
    score_sha = validate_performance_score(score)
    binding_sha = validate_source_bindings(bindings, score)
    output = Path(output_path).expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    timeline = dict(score["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    duration_samples = int(timeline["duration_samples"])
    source_index = _binding_index(bindings, score, verify_pcm=verify_source_pcm, ffmpeg=ffmpeg)
    clips = _clip_rows(score)
    needs_rubberband = any(abs(float(clip.get("tempo_scale", 1.0)) - 1.0) > 1e-9 or abs(float(clip.get("pitch_semitones", 0.0))) > 1e-9 for _, clip in clips)
    if needs_rubberband and not _ffmpeg_has_filter("rubberband", ffmpeg):
        raise ReferenceZeroError("score requires Rubber Band, but this FFmpeg build lacks the rubberband filter")

    argv: list[str] = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y"]
    clip_receipts: list[dict[str, Any]] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (track, clip) in enumerate(clips):
        source = source_index[str(clip["source_id"])]
        argv.extend(["-i", str(source.artifact_path)])
        source_start = int(clip["source_start_sample"])
        source_end = int(clip["source_end_sample"])
        target_start = int(clip["target_start_sample"])
        tempo_scale = float(clip.get("tempo_scale", 1.0))
        pitch_semitones = float(clip.get("pitch_semitones", 0.0))
        gain_db = float(clip.get("gain_db", 0.0)) + float(track.get("gain_db", 0.0))
        pan = max(-1.0, min(1.0, float(clip.get("pan", track.get("pan", 0.0)))))
        fade_in = int(clip.get("fade_in_samples", 0))
        fade_out = int(clip.get("fade_out_samples", 0))
        output_duration = max(1, round((source_end - source_start) / tempo_scale))
        chain = [
            f"aformat=sample_rates={sample_rate}:channel_layouts={'mono' if channels == 1 else 'stereo'}",
            f"atrim=start_sample={source_start}:end_sample={source_end}",
            "asetpts=PTS-STARTPTS",
        ]
        if abs(tempo_scale - 1.0) > 1e-9 or abs(pitch_semitones) > 1e-9:
            pitch_factor = 2.0 ** (pitch_semitones / 12.0)
            chain.append(f"rubberband=tempo={tempo_scale:.12g}:pitch={pitch_factor:.12g}")
        if abs(gain_db) > 1e-12:
            chain.append(f"volume={_linear_gain(gain_db):.12g}")
        if channels == 2 and abs(pan) > 1e-12:
            left, right = _pan_gains(pan)
            chain.append(f"pan=stereo|c0={left:.12g}*c0|c1={right:.12g}*c1")
        if fade_in:
            chain.append(f"afade=t=in:ss=0:ns={fade_in}")
        if fade_out:
            chain.append(f"afade=t=out:ss={max(0, output_duration - fade_out)}:ns={fade_out}")
        if target_start:
            chain.append(f"adelay={target_start}S:all=1")
        label = f"c{index}"
        filters.append(f"[{index}:a:0]{','.join(chain)}[{label}]")
        labels.append(f"[{label}]")
        clip_receipts.append(
            {
                "clip_id": clip["clip_id"],
                "track_id": track["track_id"],
                "source_id": source.source_id,
                "source_container_sha256": source.container_sha256,
                "source_start_sample": source_start,
                "source_end_sample": source_end,
                "target_start_sample": target_start,
                "transformed_duration_samples": output_duration,
                "tempo_scale": tempo_scale,
                "pitch_semitones": pitch_semitones,
                "gain_db": gain_db,
                "pan": pan,
                "fade_in_samples": fade_in,
                "fade_out_samples": fade_out,
                "musical_function": clip.get("musical_function"),
                "occurrence_id": clip.get("occurrence_id"),
                "locked": bool(clip.get("locked", True)),
            }
        )

    master = dict(score.get("master") or {})
    master_chain = [
        f"amix=inputs={len(labels)}:duration=longest:normalize=0",
        f"apad=whole_len={duration_samples}",
        f"atrim=end_sample={duration_samples}",
        "asetpts=PTS-STARTPTS",
    ]
    master_gain = float(master.get("gain_db", 0.0))
    if abs(master_gain) > 1e-12:
        master_chain.append(f"volume={_linear_gain(master_gain):.12g}")
    peak_limit = master.get("peak_limit_dbfs")
    if peak_limit not in {None, ""}:
        # FFmpeg's alimiter enables automatic level compensation by default,
        # which lifts the limited signal back toward 0 dBFS and defeats the
        # score's declared ceiling. Preserve the requested absolute ceiling.
        master_chain.append(f"alimiter=limit={_linear_gain(float(peak_limit)):.12g}:level=false")
    filters.append(f"{''.join(labels)}{','.join(master_chain)}[out]")
    argv.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-ar", str(sample_rate), "-ac", str(channels), "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact"])
    codec = str(master.get("codec") or "pcm_s24le")
    if codec == "flac":
        argv.extend(["-c:a", "flac", "-compression_level", "8"])
    else:
        argv.extend(["-c:a", codec])
    argv.append(str(output))

    started = datetime.now(timezone.utc)
    result = _run(argv, timeout=int(master.get("timeout_seconds") or 7200))
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if result.returncode != 0 or not output.is_file():
        if output.exists():
            output.unlink()
        raise ReferenceZeroError(f"performance render failed ({result.returncode}): {result.stderr[-4000:]}")
    probe = ffprobe_audio(output, ffprobe=ffprobe)
    output_container = sha256_file(output)
    output_pcm = canonical_pcm_sha256(output, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
    receipt = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_performance_render_receipt",
            "rendered_at": now_utc(),
            "score_sha256": score_sha,
            "bindings_sha256": binding_sha,
            "ffmpeg_version": ffmpeg_version(ffmpeg),
            "output": {
                "name": output.name,
                "bytes": int(output.stat().st_size),
                "container_sha256": output_container,
                "canonical_pcm_sha256": output_pcm,
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_samples": duration_samples,
                "codec": codec,
            },
            "clip_count": len(clip_receipts),
            "clips": clip_receipts,
            "command": {
                "argv": argv,
                "returncode": result.returncode,
                "elapsed_seconds": round(elapsed, 6),
                "stderr_tail": result.stderr[-4000:],
            },
            "ffprobe": probe,
            "authority": {
                "renderer_invented_decisions": False,
                "all_selected_clips_accounted": True,
                "human_acceptance": False,
                "inference_success": False,
            },
        }
    )
    if receipt_path is not None:
        write_json(receipt_path, receipt, exclusive=True)
    return receipt


def verify_reproduction(
    score: Mapping[str, Any],
    bindings: Mapping[str, Any],
    *,
    output_directory: str | Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    root = Path(output_directory).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    first = render_performance_score(score, bindings, output_path=root / "render-a.wav", receipt_path=root / "render-a.receipt.json", ffmpeg=ffmpeg, ffprobe=ffprobe)
    second = render_performance_score(score, bindings, output_path=root / "render-b.wav", receipt_path=root / "render-b.receipt.json", ffmpeg=ffmpeg, ffprobe=ffprobe)
    same_pcm = first["output"]["canonical_pcm_sha256"] == second["output"]["canonical_pcm_sha256"]
    if not same_pcm:
        raise ReferenceZeroError("same score and bindings produced different canonical PCM")
    return {
        "ok": True,
        "score_sha256": first["score_sha256"],
        "bindings_sha256": first["bindings_sha256"],
        "canonical_pcm_sha256": first["output"]["canonical_pcm_sha256"],
        "container_byte_identity": first["output"]["container_sha256"] == second["output"]["container_sha256"],
        "render_receipts": [first["receipt_sha256"], second["receipt_sha256"]],
    }


def create_source_registry(
    *,
    sources: Mapping[str, str | Path],
    roles: Mapping[str, str] | None = None,
    verify_pcm: bool = False,
    sample_rate: int = 48000,
    channels: int = 2,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    roles = dict(roles or {})
    rows: list[dict[str, Any]] = []
    for source_id, raw_path in sorted(sources.items()):
        path = Path(raw_path).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"regular non-symlink source required: {path}")
        container = sha256_file(path)
        pcm = canonical_pcm_sha256(path, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg) if verify_pcm else None
        rows.append(
            {
                "source_id": str(source_id),
                "role": str(roles.get(str(source_id)) or "unclassified_material"),
                "container_sha256": container,
                "canonical_pcm_sha256": pcm,
                "bytes": int(path.stat().st_size),
            }
        )
    if not rows:
        raise ValidationError("source registry requires at least one source")
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_source_registry",
            "created_at": now_utc(),
            "sample_rate": int(sample_rate),
            "channels": int(channels),
            "sources": rows,
            "boundary": {
                "artifact_paths_included": False,
                "source_audio_copied": False,
                "private_registry": True,
            },
        }
    )


def create_source_bindings(
    score: Mapping[str, Any],
    *,
    paths: Mapping[str, str | Path],
    verify_pcm: bool = False,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    score_sha = validate_performance_score(score)
    timeline = dict(score["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    rows = []
    for source in score["sources"]:
        source_id = str(source["source_id"])
        if source_id not in paths:
            raise ValidationError(f"missing local path for {source_id}")
        path = Path(paths[source_id]).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"regular non-symlink file required: {path}")
        container = sha256_file(path)
        if container != source["container_sha256"]:
            raise SourceMutationError(f"provided source does not match score identity: {source_id}")
        pcm = canonical_pcm_sha256(path, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg) if verify_pcm or source.get("canonical_pcm_sha256") else None
        if source.get("canonical_pcm_sha256") and pcm != source.get("canonical_pcm_sha256"):
            raise SourceMutationError(f"canonical PCM mismatch for {source_id}")
        rows.append(
            {
                "source_id": source_id,
                "artifact_path": str(path),
                "container_sha256": container,
                "canonical_pcm_sha256": pcm,
                "bytes": int(path.stat().st_size),
            }
        )
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_performance_source_bindings",
            "created_at": now_utc(),
            "score_sha256": score_sha,
            "bindings": rows,
            "visibility": "sensitive",
        }
    )


def create_gold_receipt(
    score: Mapping[str, Any],
    render_receipt: Mapping[str, Any],
    *,
    reviewer_id: str,
    disposition: str,
    dimensions: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    score_sha = validate_performance_score(score)
    receipt_sha = validate_seal(render_receipt, kind="earcrate_performance_render_receipt")
    if render_receipt.get("score_sha256") != score_sha:
        raise ValidationError("render receipt belongs to another score")
    if disposition not in {"accept", "reject", "revise", "abstain"}:
        raise ValidationError("invalid gold disposition")
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_gold_receipt",
            "recorded_at": now_utc(),
            "score_sha256": score_sha,
            "render_receipt_sha256": receipt_sha,
            "render_canonical_pcm_sha256": render_receipt["output"]["canonical_pcm_sha256"],
            "reviewer_id": reviewer_id,
            "disposition": disposition,
            "dimensions": deepcopy(dict(dimensions)),
            "notes": [str(value) for value in notes],
            "authority": {
                "manual_performance_gold": disposition == "accept",
                "automatic_inference_passed": False,
                "full_reference_completed": False,
                "rights_or_release_permission": False,
            },
        }
    )


def create_recovery_challenge(
    gold_score: Mapping[str, Any],
    gold_receipt: Mapping[str, Any],
    *,
    control_score_sha256: str | None = None,
    review_dimensions: Sequence[str] = DEFAULT_REVIEW_DIMENSIONS,
) -> dict[str, Any]:
    score_sha = validate_performance_score(gold_score)
    gold_sha = validate_seal(gold_receipt, kind="earcrate_reference_zero_gold_receipt")
    if gold_receipt.get("score_sha256") != score_sha or gold_receipt.get("disposition") != "accept":
        raise ValidationError("recovery challenge requires an accepted gold score")
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_recovery_challenge",
            "created_at": now_utc(),
            "challenge_id": str(gold_score.get("score_id") or score_sha[:16]) + "-recovery",
            "source_identities": [
                {
                    "source_id": row["source_id"],
                    "role": row.get("role"),
                    "container_sha256": row["container_sha256"],
                    "canonical_pcm_sha256": row.get("canonical_pcm_sha256"),
                }
                for row in gold_score["sources"]
            ],
            "timeline": {
                "sample_rate": gold_score["timeline"]["sample_rate"],
                "channels": gold_score["timeline"]["channels"],
                "duration_samples": gold_score["timeline"]["duration_samples"],
            },
            "withheld_answer_key": {
                "gold_score_sha256": score_sha,
                "gold_receipt_sha256": gold_sha,
                "gold_render_pcm_sha256_commitment": gold_receipt["render_canonical_pcm_sha256"],
                "clip_decisions_published": False,
            },
            "control_score_sha256": control_score_sha256,
            "review_dimensions": [str(value) for value in review_dimensions],
            "acceptance": {
                "candidate_must_blindly_beat_naive_control": True,
                "least_bad_does_not_pass": True,
                "tie_terminates_lineage": True,
                "reject_all_terminates_lineage": True,
                "gold_similarity_is_evaluated_only_after_candidate_submission": True,
            },
        }
    )


def _measure_loudness(path: Path, ffmpeg: str = "ffmpeg") -> tuple[float, float]:
    result = _run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], timeout=1800)
    text = result.stderr
    # Read the trailing Summary block, never the per-frame trace. ebur128 prints a
    # running `I:` on every frame, and a track that opens quietly reports the -70
    # LUFS floor for its first frames. An unanchored `I: ... .*? Peak:` match pairs
    # that floor with the summary's peak, so any quiet intro measured as near
    # silence -- and _level_match below then clamps on peak, producing a cut that
    # is peak-normalized rather than level-matched.
    tail = text.rsplit("Summary:", 1)[-1] if "Summary:" in text else ""
    integrated = re.search(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", tail, flags=re.S)
    peak = re.search(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", tail, flags=re.S)
    if not integrated:
        raise ReferenceZeroError(f"could not parse loudness for {path}")
    return float(integrated.group(1)), float(peak.group(1)) if peak else -99.0


def _level_match(source: Path, destination: Path, *, target_lufs: float, peak_ceiling_dbfs: float, ffmpeg: str) -> dict[str, Any]:
    lufs, peak = _measure_loudness(source, ffmpeg=ffmpeg)
    gain = target_lufs - lufs
    if peak + gain > peak_ceiling_dbfs:
        gain = peak_ceiling_dbfs - peak
    result = _run(
        [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(source), "-af", f"volume={gain:.12g}dB", "-map_metadata", "-1", "-c:a", "flac", "-compression_level", "8", str(destination)],
        timeout=1800,
    )
    if result.returncode != 0 or not destination.is_file():
        raise ReferenceZeroError(f"level matching failed: {result.stderr[-2000:]}")
    post_lufs, post_peak = _measure_loudness(destination, ffmpeg=ffmpeg)
    return {
        "source_lufs": lufs,
        "source_peak_dbfs": peak,
        "applied_gain_db": gain,
        "output_lufs": post_lufs,
        "output_peak_dbfs": post_peak,
        "container_sha256": sha256_file(destination),
        "bytes": int(destination.stat().st_size),
    }


def prepare_candidate_control_review(
    candidate_score: Mapping[str, Any],
    control_score: Mapping[str, Any],
    candidate_bindings: Mapping[str, Any],
    control_bindings: Mapping[str, Any],
    *,
    output_directory: str | Path,
    reviewer_id: str = "operator:owner",
    dimensions: Sequence[str] = DEFAULT_REVIEW_DIMENSIONS,
    target_lufs: float = -14.0,
    peak_ceiling_dbfs: float = -2.0,
    seed: int | None = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    root = Path(output_directory).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    private = root / "private"
    public = root / "public"
    private.mkdir()
    public.mkdir()
    candidate_receipt = render_performance_score(candidate_score, candidate_bindings, output_path=private / "candidate.raw.wav", receipt_path=private / "candidate.render.json", ffmpeg=ffmpeg, ffprobe=ffprobe)
    control_receipt = render_performance_score(control_score, control_bindings, output_path=private / "control.raw.wav", receipt_path=private / "control.render.json", ffmpeg=ffmpeg, ffprobe=ffprobe)
    matched = {
        "candidate": _level_match(private / "candidate.raw.wav", private / "candidate.matched.flac", target_lufs=target_lufs, peak_ceiling_dbfs=peak_ceiling_dbfs, ffmpeg=ffmpeg),
        "control": _level_match(private / "control.raw.wav", private / "control.matched.flac", target_lufs=target_lufs, peak_ceiling_dbfs=peak_ceiling_dbfs, ffmpeg=ffmpeg),
    }
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    semantics = ["candidate", "control"]
    rng.shuffle(semantics)
    option_map: dict[str, str] = {}
    options: dict[str, dict[str, Any]] = {}
    for index, semantic in enumerate(semantics):
        label = chr(ord("A") + index)
        source = private / f"{semantic}.matched.flac"
        destination = public / f"{label}.flac"
        shutil.copyfile(source, destination)
        option_map[label] = semantic
        options[label] = {"sha256": sha256_file(destination), "bytes": int(destination.stat().st_size), "media_kind": "audio/flac"}
    token = secrets.token_urlsafe(32)
    token_sha = sha256_bytes(token.encode("utf-8"))
    authority_seed = secrets.token_hex(32)
    commitment = sha256_bytes(canonical_json_bytes({"option_map": option_map, "authority_seed": authority_seed}))
    assignment = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_review_assignment",
            "created_at": now_utc(),
            "reviewer_id": reviewer_id,
            "candidate_score_sha256": candidate_receipt["score_sha256"],
            "control_score_sha256": control_receipt["score_sha256"],
            "options": options,
            "choices": [*sorted(options), "tie", "reject_all", "abstain"],
            "dimensions": [str(value) for value in dimensions],
            "review_token_sha256": token_sha,
            "private_authority_commitment": commitment,
            "public_metrics": {"target_lufs": target_lufs, "peak_ceiling_dbfs": peak_ceiling_dbfs, "candidate_specific_metrics_withheld": True},
            "acceptance": {"candidate_must_beat_control": True, "least_bad_does_not_pass": True},
        }
    )
    authority = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_private_review_authority",
            "created_at": now_utc(),
            "assignment_sha256": assignment["assignment_sha256"],
            "reviewer_id": reviewer_id,
            "option_map": option_map,
            "authority_seed": authority_seed,
            "authority_commitment": commitment,
            "review_token": token,
            "review_token_sha256": token_sha,
            "candidate_render_receipt_sha256": candidate_receipt["receipt_sha256"],
            "control_render_receipt_sha256": control_receipt["receipt_sha256"],
            "private_level_metrics": matched,
        }
    )
    write_json(public / "assignment.json", assignment, exclusive=True)
    write_json(private / "assignment-authority.json", authority, exclusive=True)
    atomic_write(private / "review-token.txt", (token + "\n").encode("utf-8"), exclusive=True)
    return {"assignment": assignment, "authority": authority, "public_directory": str(public), "private_directory": str(private)}


def submit_review(
    assignment: Mapping[str, Any],
    authority: Mapping[str, Any],
    *,
    reviewer_id: str,
    choice: str,
    dimensions: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    assignment_sha = validate_seal(assignment, kind="earcrate_reference_zero_review_assignment")
    authority_sha = validate_seal(authority, kind="earcrate_reference_zero_private_review_authority")
    if authority.get("assignment_sha256") != assignment_sha:
        raise ValidationError("review authority belongs to another assignment")
    if reviewer_id != assignment.get("reviewer_id"):
        raise ValidationError("reviewer identity mismatch")
    if choice not in assignment.get("choices", []):
        raise ValidationError("invalid review choice")
    token = str(authority.get("review_token") or "")
    if sha256_bytes(token.encode("utf-8")) != assignment.get("review_token_sha256"):
        raise ValidationError("review token commitment mismatch")
    submission = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_review_submission",
            "submitted_at": now_utc(),
            "assignment_sha256": assignment_sha,
            "reviewer_id": reviewer_id,
            "choice": choice,
            "dimensions": deepcopy(dict(dimensions)),
            "notes": [str(value) for value in notes],
            "review_token_sha256": assignment["review_token_sha256"],
        }
    )
    semantic = (authority.get("option_map") or {}).get(choice)
    if semantic == "candidate":
        verdict = "candidate_beats_control"
    elif semantic == "control":
        verdict = "control_wins"
    elif choice == "tie":
        verdict = "tie_terminates_lineage"
    elif choice == "reject_all":
        verdict = "reject_all_terminates_lineage"
    else:
        verdict = "abstain"
    ledger = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_reference_zero_review_ledger",
            "adjudicated_at": now_utc(),
            "assignment_sha256": assignment_sha,
            "authority_sha256": authority_sha,
            "submission_sha256": submission["submission_sha256"],
            "reviewer_id": reviewer_id,
            "choice": choice,
            "semantic_choice": semantic,
            "verdict": verdict,
            "dimensions": deepcopy(dict(dimensions)),
            "notes": [str(value) for value in notes],
            "authority": {"automatic_reference_completed": verdict == "candidate_beats_control", "full_song_permission": False, "release_permission": False},
        }
    )
    return submission, ledger


def import_edl(
    *,
    source_registry: Mapping[str, Any],
    edl_path: str | Path,
    score_id: str,
    title: str,
    sample_rate: int,
    channels: int,
    duration_samples: int,
    master_gain_db: float = 0.0,
    peak_limit_dbfs: float | None = None,
    codec: str = "pcm_s24le",
) -> dict[str, Any]:
    if source_registry.get("kind") == "earcrate_reference_zero_source_registry":
        validate_seal(source_registry, kind="earcrate_reference_zero_source_registry")
    sources = [dict(row) for row in source_registry.get("sources") or []]
    source_ids = {str(row.get("source_id")) for row in sources}
    tracks: dict[str, dict[str, Any]] = {}
    history = []
    with Path(edl_path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValidationError("EDL contains no rows")
    for index, row in enumerate(rows, 1):
        track_id = str(row.get("track_id") or "").strip()
        source_id = str(row.get("source_id") or "").strip()
        if not track_id or source_id not in source_ids:
            raise ValidationError(f"invalid EDL row {index}: track/source")
        track = tracks.setdefault(
            track_id,
            {
                "track_id": track_id,
                "role": str(row.get("track_role") or "material"),
                "ownership": str(row.get("ownership") or "explicit"),
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [],
            },
        )
        def seconds(name: str) -> int:
            return round(float(row.get(name) or 0.0) * sample_rate)
        clip = {
            "clip_id": str(row.get("clip_id") or f"clip-{index:04d}"),
            "source_id": source_id,
            "source_start_sample": seconds("source_start_seconds"),
            "source_end_sample": seconds("source_end_seconds"),
            "target_start_sample": seconds("target_start_seconds"),
            "tempo_scale": float(row.get("tempo_scale") or 1.0),
            "pitch_semitones": float(row.get("pitch_semitones") or 0.0),
            "gain_db": float(row.get("gain_db") or 0.0),
            "pan": float(row.get("pan") or 0.0),
            "fade_in_samples": round(float(row.get("fade_in_ms") or 0.0) * sample_rate / 1000.0),
            "fade_out_samples": round(float(row.get("fade_out_ms") or 0.0) * sample_rate / 1000.0),
            "musical_function": str(row.get("musical_function") or "unspecified"),
            "occurrence_id": str(row.get("occurrence_id") or "unassigned"),
            "locked": str(row.get("locked") or "true").casefold() not in {"false", "0", "no"},
        }
        track["clips"].append(clip)
        history.append(
            {
                "sequence": index,
                "command_id": f"edl-import-{index:04d}",
                "actor": "human-editor",
                "operation": "place_clip",
                "target": clip["clip_id"],
                "parameters_sha256": sha256_bytes(canonical_json_bytes(clip)),
            }
        )
    score = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_performance_score",
            "created_at": now_utc(),
            "score_id": score_id,
            "title": title,
            "timeline": {"sample_rate": sample_rate, "channels": channels, "duration_samples": duration_samples, "shared_events": []},
            "sources": sources,
            "tracks": list(tracks.values()),
            "master": {"gain_db": master_gain_db, "peak_limit_dbfs": peak_limit_dbfs, "codec": codec},
            "invariants": {
                "renderer_may_invent_decisions": False,
                "source_mutation_forbidden": True,
                "all_selected_clips_must_render": True,
                "human_command_history_append_only": True,
            },
            "authority": {"status": "manual_gold_candidate", "allow_unused_sources": False},
            "command_history": history,
        }
    )
    validate_performance_score(score)
    return score


def write_edl_template(path: str | Path) -> Path:
    columns = [
        "track_id",
        "track_role",
        "ownership",
        "clip_id",
        "source_id",
        "source_start_seconds",
        "source_end_seconds",
        "target_start_seconds",
        "tempo_scale",
        "pitch_semitones",
        "gain_db",
        "pan",
        "fade_in_ms",
        "fade_out_ms",
        "musical_function",
        "occurrence_id",
        "locked",
    ]
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
    return target


def _parse_source_paths(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValidationError("--source requires source_id=path")
        source_id, raw = value.split("=", 1)
        result[source_id.strip()] = Path(raw).expanduser().absolute()
    return result


def _parse_key_values(values: Sequence[str], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValidationError(f"{label} requires key=value")
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValidationError(f"{label} contains an empty key")
        result[key] = raw.strip()
    return result


def _parse_dimensions(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValidationError("dimensions must decode to a JSON object")
    return value


def reference_zero_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="earcrate-reference-zero", description="Author, render, accept, and recover an EarCrate Reference Zero performance")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("registry", help="hash local sources into a portable path-free source registry")
    p.add_argument("--source", action="append", required=True, help="source_id=absolute_path")
    p.add_argument("--role", action="append", default=[], help="source_id=musical_role")
    p.add_argument("--sample-rate", type=int, default=48000)
    p.add_argument("--channels", type=int, default=2)
    p.add_argument("--verify-pcm", action="store_true")
    p.add_argument("--output", required=True)

    p = sub.add_parser("edl-template", help="create an empty human-editable performance EDL")
    p.add_argument("--output", required=True)

    p = sub.add_parser("import-edl", help="compile a portable PerformanceScore from an explicit EDL")
    p.add_argument("--source-registry", required=True)
    p.add_argument("--edl", required=True)
    p.add_argument("--score-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--sample-rate", type=int, default=48000)
    p.add_argument("--channels", type=int, default=2)
    p.add_argument("--duration-seconds", type=float, required=True)
    p.add_argument("--master-gain-db", type=float, default=0.0)
    p.add_argument("--peak-limit-dbfs", type=float)
    p.add_argument("--codec", default="pcm_s24le")
    p.add_argument("--output", required=True)

    p = sub.add_parser("bind", help="create a private exact-path binding manifest for a PerformanceScore")
    p.add_argument("--score", required=True)
    p.add_argument("--source", action="append", required=True, help="source_id=absolute_path")
    p.add_argument("--verify-pcm", action="store_true")
    p.add_argument("--output", required=True)

    p = sub.add_parser("render", help="render a PerformanceScore without inventing musical decisions")
    p.add_argument("--score", required=True)
    p.add_argument("--bindings", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--receipt", required=True)
    p.add_argument("--verify-source-pcm", action="store_true")

    p = sub.add_parser("verify-reproduction", help="render twice and require identical canonical PCM")
    p.add_argument("--score", required=True)
    p.add_argument("--bindings", required=True)
    p.add_argument("--output-directory", required=True)

    p = sub.add_parser("accept-gold", help="seal an owner disposition for a rendered manual PerformanceScore")
    p.add_argument("--score", required=True)
    p.add_argument("--render-receipt", required=True)
    p.add_argument("--reviewer-id", default="operator:owner")
    p.add_argument("--disposition", choices=["accept", "reject", "revise", "abstain"], required=True)
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--output", required=True)

    p = sub.add_parser("challenge", help="create a source-free recovery challenge from an accepted gold score")
    p.add_argument("--gold-score", required=True)
    p.add_argument("--gold-receipt", required=True)
    p.add_argument("--control-score")
    p.add_argument("--output", required=True)

    p = sub.add_parser("prepare-review", help="render candidate and naive control into a blind level-matched A/B review")
    p.add_argument("--candidate-score", required=True)
    p.add_argument("--candidate-bindings", required=True)
    p.add_argument("--control-score", required=True)
    p.add_argument("--control-bindings", required=True)
    p.add_argument("--output-directory", required=True)
    p.add_argument("--reviewer-id", default="operator:owner")
    p.add_argument("--seed", type=int)

    p = sub.add_parser("review", help="seal and unblind a candidate-versus-control review")
    p.add_argument("--review-directory", required=True)
    p.add_argument("--reviewer-id", default="operator:owner")
    p.add_argument("--choice", required=True)
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--note", action="append", default=[])

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "registry":
            source_paths = _parse_source_paths(args.source)
            registry = create_source_registry(
                sources=source_paths,
                roles=_parse_key_values(args.role, label="--role"),
                verify_pcm=args.verify_pcm,
                sample_rate=args.sample_rate,
                channels=args.channels,
            )
            write_json(args.output, registry, exclusive=True)
            print(json.dumps({"ok": True, "registry_sha256": registry["registry_sha256"], "sources": len(registry["sources"]), "output": str(Path(args.output).absolute())}, indent=2))
            return 0
        if args.command == "edl-template":
            path = write_edl_template(args.output)
            print(json.dumps({"ok": True, "output": str(path)}, indent=2))
            return 0
        if args.command == "import-edl":
            score = import_edl(
                source_registry=load_json(args.source_registry),
                edl_path=args.edl,
                score_id=args.score_id,
                title=args.title,
                sample_rate=args.sample_rate,
                channels=args.channels,
                duration_samples=round(args.duration_seconds * args.sample_rate),
                master_gain_db=args.master_gain_db,
                peak_limit_dbfs=args.peak_limit_dbfs,
                codec=args.codec,
            )
            write_json(args.output, score, exclusive=True)
            print(json.dumps({"ok": True, "score_sha256": score["score_sha256"], "output": str(Path(args.output).absolute())}, indent=2))
            return 0
        if args.command == "bind":
            score = load_json(args.score)
            bindings = create_source_bindings(score, paths=_parse_source_paths(args.source), verify_pcm=args.verify_pcm)
            write_json(args.output, bindings, exclusive=True)
            print(json.dumps({"ok": True, "bindings_sha256": bindings["bindings_sha256"], "output": str(Path(args.output).absolute())}, indent=2))
            return 0
        if args.command == "render":
            receipt = render_performance_score(load_json(args.score), load_json(args.bindings), output_path=args.output, receipt_path=args.receipt, verify_source_pcm=args.verify_source_pcm)
            print(json.dumps({"ok": True, "receipt_sha256": receipt["receipt_sha256"], "output": receipt["output"]}, indent=2, sort_keys=True))
            return 0
        if args.command == "verify-reproduction":
            result = verify_reproduction(load_json(args.score), load_json(args.bindings), output_directory=args.output_directory)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "accept-gold":
            receipt = create_gold_receipt(
                load_json(args.score),
                load_json(args.render_receipt),
                reviewer_id=args.reviewer_id,
                disposition=args.disposition,
                dimensions=_parse_dimensions(args.dimensions_json),
                notes=args.note,
            )
            write_json(args.output, receipt, exclusive=True)
            print(json.dumps({"ok": True, "gold_receipt_sha256": receipt["gold_receipt_sha256"], "disposition": receipt["disposition"]}, indent=2))
            return 0
        if args.command == "challenge":
            control_sha = None
            if args.control_score:
                control = load_json(args.control_score)
                control_sha = validate_performance_score(control)
            challenge = create_recovery_challenge(load_json(args.gold_score), load_json(args.gold_receipt), control_score_sha256=control_sha)
            write_json(args.output, challenge, exclusive=True)
            print(json.dumps({"ok": True, "challenge_sha256": challenge["challenge_sha256"]}, indent=2))
            return 0
        if args.command == "prepare-review":
            result = prepare_candidate_control_review(
                load_json(args.candidate_score),
                load_json(args.control_score),
                load_json(args.candidate_bindings),
                load_json(args.control_bindings),
                output_directory=args.output_directory,
                reviewer_id=args.reviewer_id,
                seed=args.seed,
            )
            print(json.dumps({"ok": True, "assignment_sha256": result["assignment"]["assignment_sha256"], "public_directory": result["public_directory"]}, indent=2))
            return 0
        if args.command == "review":
            root = Path(args.review_directory).expanduser().absolute()
            assignment = load_json(root / "public" / "assignment.json")
            authority = load_json(root / "private" / "assignment-authority.json")
            submission, ledger = submit_review(
                assignment,
                authority,
                reviewer_id=args.reviewer_id,
                choice=args.choice,
                dimensions=_parse_dimensions(args.dimensions_json),
                notes=args.note,
            )
            write_json(root / "private" / "submission.json", submission, exclusive=True)
            write_json(root / "private" / "review-ledger.json", ledger, exclusive=True)
            public_projection = deepcopy(ledger)
            public_projection.pop("semantic_choice", None)
            write_json(root / "public" / "review-result.source-free.json", public_projection, exclusive=True)
            print(json.dumps({"ok": True, "ledger_sha256": ledger["ledger_sha256"], "verdict": ledger["verdict"]}, indent=2))
            return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(reference_zero_cli_main())
