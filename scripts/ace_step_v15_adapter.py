#!/usr/bin/env python3
"""Execute one EarCrate generation request through the local ACE-Step 1.5 API."""

from __future__ import annotations

import argparse
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from urllib import request as urllib_request


class AdapterError(RuntimeError):
    """Raised when the local ACE-Step service violates the expected contract."""


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> str:
    """Persist an exact private provider exchange and return its SHA-256."""
    encoded = (json.dumps(dict(value), indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _float_array(value: bytes) -> array:
    result = array("f")
    result.frombytes(value)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def _little_endian_bytes(value: array) -> bytes:
    if sys.byteorder == "little":
        return value.tobytes()
    copied = array("f", value)
    copied.byteswap()
    return copied.tobytes()


def _splice_repaint_pcm(
    source_pcm: bytes,
    provider_pcm: bytes,
    *,
    start_frame: int,
    end_frame: int,
    channels: int = 2,
    crossfade_frames: int = 2400,
) -> bytes:
    """Return source PCM with only the declared repaint mask replaced."""
    source = _float_array(source_pcm)
    provider = _float_array(provider_pcm)
    if channels <= 0 or len(source) % channels or len(provider) % channels:
        raise AdapterError("decoded repaint PCM has an invalid channel layout")
    source_frames = len(source) // channels
    provider_frames = len(provider) // channels
    if start_frame < 0 or end_frame <= start_frame:
        raise AdapterError("repaint mask must have a positive duration")
    if end_frame > source_frames or end_frame > provider_frames:
        raise AdapterError("repaint mask exceeds decoded source or provider audio")

    result = array("f", source)
    fade = min(crossfade_frames, max(1, (end_frame - start_frame) // 2))
    for frame in range(start_frame, end_frame):
        if frame < start_frame + fade:
            provider_weight = (frame - start_frame) / fade
        elif frame >= end_frame - fade:
            provider_weight = (end_frame - 1 - frame) / fade
        else:
            provider_weight = 1.0
        provider_weight = max(0.0, min(1.0, provider_weight))
        source_weight = 1.0 - provider_weight
        offset = frame * channels
        for channel in range(channels):
            index = offset + channel
            result[index] = source[index] * source_weight + provider[index] * provider_weight
    return _little_endian_bytes(result)


def _decode_f32le(path: Path, *, sample_rate: int = 48000, channels: int = 2) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AdapterError("ffmpeg is required for source-preserving repaint")
    completed = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "pipe:1",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise AdapterError(f"ffmpeg could not decode repaint audio: {completed.stderr.decode(errors='replace')}")
    return completed.stdout


def _materialize_source_preserving_repaint(
    *,
    source_audio: Path,
    provider_audio: Path,
    output_directory: Path,
    start_seconds: float,
    end_seconds: float,
) -> Path:
    sample_rate = 48000
    channels = 2
    source_pcm = _decode_f32le(source_audio, sample_rate=sample_rate, channels=channels)
    provider_pcm = _decode_f32le(provider_audio, sample_rate=sample_rate, channels=channels)
    spliced_pcm = _splice_repaint_pcm(
        source_pcm,
        provider_pcm,
        start_frame=round(start_seconds * sample_rate),
        end_frame=round(end_seconds * sample_rate),
        channels=channels,
    )

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AdapterError("ffmpeg is required for source-preserving repaint")
    raw_path = output_directory / "repaint-splice.private.f32"
    target = output_directory / "generated.wav"
    try:
        with raw_path.open("xb") as handle:
            handle.write(spliced_pcm)
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "f32le",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-i",
                str(raw_path),
                "-c:a",
                "pcm_f32le",
                str(target),
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0 or not target.is_file():
            raise AdapterError(f"ffmpeg could not encode repaint audio: {completed.stderr.decode(errors='replace')}")
    finally:
        raw_path.unlink(missing_ok=True)
    return target


def _loopback_base_url(value: str) -> str:
    parsed = urllib_parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AdapterError("ACE-Step adapter requires a loopback HTTP service")
    return value.rstrip("/")


def _json_request(
    url: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    response_path: Path | None = None,
) -> dict[str, Any]:
    request = urllib_request.Request(
        url,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        raw_body = exc.read()
        try:
            decoded_error = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded_error = {
                "http_status": exc.code,
                "reason": exc.reason,
                "body_utf8": raw_body.decode("utf-8", errors="replace"),
            }
        if not isinstance(decoded_error, dict):
            decoded_error = {"http_status": exc.code, "body": decoded_error}
        if response_path is not None:
            _write_json_exclusive(response_path, decoded_error)
        raise AdapterError(f"ACE-Step HTTP {exc.code}: {decoded_error!r}") from exc
    if not isinstance(decoded, dict):
        raise AdapterError("ACE-Step response must be a JSON object")
    if response_path is not None:
        _write_json_exclusive(response_path, decoded)
    if int(decoded.get("code", 200)) != 200 or decoded.get("error"):
        raise AdapterError(f"ACE-Step request failed: {decoded.get('error')!r}")
    return decoded


def _multipart_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _multipart_request_body(
    payload: Mapping[str, Any], source_audio: Path
) -> tuple[bytes, str, dict[str, Any]]:
    """Build a deterministic multipart request containing the private source bytes."""
    source_bytes = source_audio.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    boundary = f"----EarCrateACE{source_sha256[:24]}"
    chunks: list[bytes] = []

    def add_text(name: str, value: Any) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                _multipart_value(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for name in sorted(payload):
        value = payload[name]
        if isinstance(value, list):
            for item in value:
                add_text(name, item)
        else:
            add_text(name, value)

    filename = source_audio.name
    if any(character in filename for character in {'"', "\r", "\n"}):
        raise AdapterError("conditioning source filename is unsafe for multipart upload")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="src_audio"; filename="{filename}"\r\n'.encode(
                "utf-8"
            ),
            b"Content-Type: audio/wav\r\n\r\n",
            source_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    body = b"".join(chunks)
    manifest = {
        "content_type": f"multipart/form-data; boundary={boundary}",
        "fields": dict(payload),
        "source_upload": {
            "field": "src_audio",
            "filename": filename,
            "bytes": len(source_bytes),
            "sha256": source_sha256,
        },
        "wire_body_bytes": len(body),
        "wire_body_sha256": hashlib.sha256(body).hexdigest(),
    }
    return body, manifest["content_type"], manifest


def _multipart_request(
    url: str,
    payload: Mapping[str, Any],
    source_audio: Path,
    *,
    timeout: float,
    response_path: Path,
    body_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body, content_type, manifest = _multipart_request_body(payload, source_audio)
    with body_path.open("xb") as handle:
        handle.write(body)
    _write_json_exclusive(manifest_path, manifest)
    request = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        raw_body = exc.read()
        try:
            decoded_error = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded_error = {
                "http_status": exc.code,
                "reason": exc.reason,
                "body_utf8": raw_body.decode("utf-8", errors="replace"),
            }
        if not isinstance(decoded_error, dict):
            decoded_error = {"http_status": exc.code, "body": decoded_error}
        _write_json_exclusive(response_path, decoded_error)
        raise AdapterError(f"ACE-Step HTTP {exc.code}: {decoded_error!r}") from exc
    if not isinstance(decoded, dict):
        raise AdapterError("ACE-Step response must be a JSON object")
    _write_json_exclusive(response_path, decoded)
    if int(decoded.get("code", 200)) != 200 or decoded.get("error"):
        raise AdapterError(f"ACE-Step request failed: {decoded.get('error')!r}")
    return decoded, manifest


def _task_type(task_mode: str) -> str:
    mapping = {
        "complete": "complete",
        "vocal_to_bgm": "complete",
        "cover": "cover",
        "repaint": "repaint",
        "lego": "lego",
        "text_to_music": "text2music",
        "lyrics_to_song": "text2music",
        "segment_generation": "text2music",
        "bgm_only": "text2music",
    }
    try:
        return mapping[task_mode]
    except KeyError as exc:
        raise AdapterError(f"unsupported ACE-Step task mode: {task_mode}") from exc


def execute(
    *,
    request_path: Path,
    output_directory: Path,
    seed: int,
    base_url: str,
    source_audio: Path | None,
    timeout_seconds: float,
    poll_seconds: float,
) -> Path:
    """Submit, poll, and materialize one deterministic ACE-Step request."""
    request_object = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if int(request_object.get("seed")) != seed:
        raise AdapterError("adapter seed does not match EarCrate request")

    prompt = dict(request_object.get("prompt") or {})
    output_contract = dict(request_object.get("output_contract") or {})
    task_type = _task_type(str(request_object.get("task_mode") or ""))
    model = str(prompt.get("model") or "acestep-v15-base")
    if task_type in {"complete", "lego", "extract"} and "base" not in model.casefold():
        raise AdapterError(f"ACE-Step {task_type} requires a base model")
    payload: dict[str, Any] = {
        "prompt": prompt.get("caption") or prompt.get("style") or prompt.get("prompt") or "",
        "lyrics": prompt.get("lyrics") or "[instrumental]",
        "thinking": bool(prompt.get("thinking", False)),
        "use_cot_caption": bool(prompt.get("use_cot_caption", False)),
        "use_cot_language": bool(prompt.get("use_cot_language", False)),
        "audio_duration": float(
            prompt.get("audio_duration")
            or output_contract.get("duration_seconds")
            or 10.0
        ),
        "audio_format": "wav",
        "model": model,
        "task_type": task_type,
        "inference_steps": int(prompt.get("inference_steps") or (32 if task_type == "complete" else 8)),
        "guidance_scale": float(prompt.get("guidance_scale") or 7.0),
        "use_random_seed": False,
        "seed": seed,
        "batch_size": 1,
    }
    for key in (
        "instruction",
        "repainting_start",
        "repainting_end",
        "audio_cover_strength",
        "bpm",
        "key_scale",
        "time_signature",
        "lm_model_path",
        "lm_backend",
        "lm_temperature",
        "lm_cfg_scale",
        "lm_top_k",
        "lm_top_p",
        "lm_repetition_penalty",
        "lm_negative_prompt",
        "allow_lm_batch",
        "constrained_decoding",
    ):
        if key in prompt:
            payload[key] = prompt[key]
    if "track_classes" in prompt:
        track_classes = prompt["track_classes"]
        if not isinstance(track_classes, list) or not track_classes or not all(
            isinstance(value, str) and value.strip() for value in track_classes
        ):
            raise AdapterError("track_classes must be a non-empty list of instrument names")
        payload["track_classes"] = [value.strip() for value in track_classes]
    if task_type == "complete" and not payload.get("instruction") and not payload.get("track_classes"):
        raise AdapterError("ACE-Step complete requires explicit tracks via instruction or track_classes")
    if source_audio is not None:
        if not source_audio.is_file():
            raise AdapterError("conditioning source is not a regular file")
    elif task_type in {"complete", "cover", "repaint", "lego"}:
        raise AdapterError(f"ACE-Step {task_type} requires --source-audio")

    base_url = _loopback_base_url(base_url)
    submit_response_path = output_directory / "provider-submit-response.private.json"
    if source_audio is not None:
        submitted, request_manifest = _multipart_request(
            base_url + "/release_task",
            payload,
            source_audio,
            timeout=min(timeout_seconds, 60.0),
            response_path=submit_response_path,
            body_path=output_directory / "provider-request.private.multipart",
            manifest_path=output_directory / "provider-request.private.json",
        )
        request_sha256 = str(request_manifest["wire_body_sha256"])
    else:
        request_sha256 = _write_json_exclusive(
            output_directory / "provider-request.private.json", payload
        )
        submitted = _json_request(
            base_url + "/release_task",
            payload,
            timeout=min(timeout_seconds, 60.0),
            response_path=submit_response_path,
        )
    submit_response_sha256 = hashlib.sha256(submit_response_path.read_bytes()).hexdigest()
    task_id = str(dict(submitted.get("data") or {}).get("task_id") or "")
    if not task_id:
        raise AdapterError("ACE-Step submission returned no task_id")

    deadline = time.monotonic() + timeout_seconds
    terminal: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        queried = _json_request(
            base_url + "/query_result",
            {"task_id_list": [task_id]},
            timeout=min(timeout_seconds, 60.0),
        )
        rows = [dict(row) for row in queried.get("data") or [] if isinstance(row, Mapping)]
        row = next((candidate for candidate in rows if str(candidate.get("task_id")) == task_id), None)
        if row and int(row.get("status") or 0) in {1, 2}:
            terminal = row
            terminal_response_sha256 = _write_json_exclusive(
                output_directory / "provider-terminal-response.private.json", queried
            )
            break
        time.sleep(poll_seconds)
    if terminal is None:
        raise AdapterError("ACE-Step generation timed out")
    if int(terminal.get("status") or 0) != 1:
        raise AdapterError("ACE-Step generation failed")

    raw_result = terminal.get("result")
    results = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    if not isinstance(results, list):
        raise AdapterError("ACE-Step terminal result must be a JSON list")
    result = next(
        (dict(row) for row in results if isinstance(row, Mapping) and int(row.get("status") or 0) == 1),
        None,
    )
    file_url = str((result or {}).get("file") or "")
    if not file_url:
        raise AdapterError("ACE-Step terminal result returned no audio URL")
    audio_url = urllib_parse.urljoin(base_url + "/", file_url)
    if not audio_url.startswith(base_url + "/"):
        raise AdapterError("ACE-Step audio URL escaped the loopback service")

    provider_target = output_directory / (
        "provider-generated.wav" if task_type == "repaint" else "generated.wav"
    )
    with urllib_request.urlopen(audio_url, timeout=min(timeout_seconds, 900.0)) as response:
        audio_bytes = response.read()
    if not audio_bytes:
        raise AdapterError("ACE-Step returned an empty audio body")
    with provider_target.open("xb") as handle:
        handle.write(audio_bytes)

    target = provider_target
    if task_type == "repaint":
        if source_audio is None:
            raise AdapterError("ACE-Step repaint requires --source-audio")
        try:
            repainting_start = float(prompt["repainting_start"])
            repainting_end = float(prompt["repainting_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError("source-preserving repaint requires explicit start and end") from exc
        target = _materialize_source_preserving_repaint(
            source_audio=source_audio,
            provider_audio=provider_target,
            output_directory=output_directory,
            start_seconds=repainting_start,
            end_seconds=repainting_end,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "provider_bytes": len(audio_bytes),
                "artifact": target.name,
                "task_type": task_type,
                "model": model,
                "provider_request_sha256": request_sha256,
                "provider_submit_response_sha256": submit_response_sha256,
                "provider_terminal_response_sha256": terminal_response_sha256,
            }
        )
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--device")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--source-audio", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    execute(
        request_path=args.request,
        output_directory=args.output,
        seed=args.seed,
        base_url=args.base_url,
        source_audio=args.source_audio,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
