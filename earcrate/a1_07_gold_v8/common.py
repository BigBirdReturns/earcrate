from __future__ import annotations

from array import array
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave
from typing import Any, Mapping, Sequence

HEX64 = re.compile(r"^[0-9a-f]{64}$")
AUDIO_SUFFIXES = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus"}
EXPECTED = {
    "contract_v7": "858d39452319e90e054c5a4994f07f7ccfcd5d38f011a46ed6834e7a766f1048",
    "owner_review": "96aab3a610f0786047bfa076030531ea72da4d3c2b0000e35471ac5511ddc4b3",
    "parent_score": "7fdb7873702e4cd7b43e6e01fefdad89786e4461842b8c3358a63565a464d6cf",
    "parent_pcm": "0733d87869f34710cd0d2e06875ae876eb100b5065f4159e8199a2672ee863f8",
    "production_score": "6b55040010a6cc3052b58baeae99b0311c6e8a7aa099009302774cadf7747515",
    "production_pcm": "cad48725fead8b53fbe85d6bca14a461aff57ed2ed9a66cab78eb67558410f5e",
    "interplay_score": "00e80e1984f22024d1473a86baf4eb399a98462c491b30093cae17c4043b1ab3",
    "interplay_pcm": "dac665b3ca517b770c500465f11a5e6461e6352bb0d90efa8e8bf775cb43af2b",
    "arc_score": "796f40eacfe02e99f63d0cfee81d5f27eeeaa0384c6711cc25c51146e96aeba5",
    "arc_pcm": "a80ad01fe529aa4292beed2cc13b8bdc48afdcf7a75ab0a96b7ecf30caf5476c",
}
class DescentError(RuntimeError):
    pass

def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def seal(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    value.pop(field, None)
    value[field] = sha256_bytes(canonical_json_bytes(value))
    return value

def validate_seal(payload: Mapping[str, Any], field: str) -> str:
    claimed = str(payload.get(field) or "").lower()
    if not HEX64.fullmatch(claimed):
        raise DescentError(f"missing or invalid {field}")
    observed = seal(payload, field)[field]
    if observed != claimed:
        raise DescentError(f"{field} mismatch: declared {claimed}, observed {observed}")
    return claimed

def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DescentError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DescentError(f"JSON object required: {path}")
    return value

def atomic_write_json(path: Path, payload: Mapping[str, Any], *, exclusive: bool = True) -> None:
    target = path.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and target.exists():
        raise DescentError(f"refusing to overwrite {target}")
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and target.exists():
            raise DescentError(f"refusing to overwrite {target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

def run(argv: Sequence[str], *, timeout: int = 3600, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        check=False,
        shell=False,
        timeout=timeout,
        cwd=cwd,
    )


def current_git_head(repo_root: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], timeout=30, cwd=repo_root)
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DescentError(f"cannot resolve exact Git head: {result.stderr[-1000:]}")
    return value

def ffprobe_info(path: Path, ffprobe: str = "ffprobe") -> dict[str, int]:
    result = run([
        ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=sample_rate,channels", "-of", "json", str(path)
    ], timeout=120)
    if result.returncode != 0:
        raise DescentError(f"ffprobe failed for {path}: {result.stderr[-2000:]}")
    value = json.loads(result.stdout)
    streams = value.get("streams") or []
    if len(streams) != 1:
        raise DescentError(f"exactly one audio stream required: {path}")
    return {"sample_rate": int(streams[0]["sample_rate"]), "channels": int(streams[0]["channels"])}

def decode_s32(path: Path, *, sample_rate: int, channels: int, ffmpeg: str = "ffmpeg") -> bytes:
    result = subprocess.run(
        [
            ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "pcm_s32le",
            "-f", "s32le", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise DescentError(f"PCM decode failed for {path}: {result.stderr.decode('utf-8', 'replace')[-2000:]}")
    frame_bytes = channels * 4
    if len(result.stdout) % frame_bytes:
        raise DescentError(f"unaligned decoded PCM: {path}")
    return result.stdout

def canonical_pcm_sha256(path: Path, *, sample_rate: int, channels: int, ffmpeg: str = "ffmpeg") -> str:
    return sha256_bytes(decode_s32(path, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg))

def bytes_to_samples(data: bytes) -> array:
    values = array("i")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values

def samples_to_bytes(values: array) -> bytes:
    clone = array("i", values)
    if sys.byteorder != "little":
        clone.byteswap()
    return clone.tobytes()

def write_s32_wav(path: Path, data: bytes, *, sample_rate: int, channels: int) -> None:
    target = path.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise DescentError(f"refusing to overwrite {target}")
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(4)
        handle.setframerate(sample_rate)
        handle.writeframes(data)

def frame_count(data: bytes, channels: int) -> int:
    return len(data) // (channels * 4)

def find_exact_subsequence(haystack: bytes, needle: bytes, *, frame_bytes: int) -> int:
    positions: list[int] = []
    cursor = 0
    while True:
        index = haystack.find(needle, cursor)
        if index < 0:
            break
        if index % frame_bytes == 0:
            positions.append(index // frame_bytes)
        cursor = index + 1
    if len(positions) != 1:
        raise DescentError(f"expected one sample-aligned embedded core, found {len(positions)}")
    return positions[0]

def smoothstep(position: int, length: int) -> float:
    if length <= 1:
        return 1.0
    x = position / (length - 1)
    return x * x * (3.0 - 2.0 * x)

def blend_regions(
    base: array,
    replacement: array,
    *,
    base_start_frame: int,
    replacement_start_frame: int,
    frames: int,
    channels: int,
    fade_in_frames: int,
    fade_out_frames: int,
) -> None:
    if frames <= 0:
        raise DescentError("replacement region must be positive")
    for frame in range(frames):
        weight = 1.0
        if fade_in_frames and frame < fade_in_frames:
            weight = min(weight, smoothstep(frame, fade_in_frames))
        if fade_out_frames and frame >= frames - fade_out_frames:
            remaining = frames - 1 - frame
            weight = min(weight, smoothstep(remaining, fade_out_frames))
        for channel in range(channels):
            target_index = (base_start_frame + frame) * channels + channel
            source_index = (replacement_start_frame + frame) * channels + channel
            original = int(base[target_index])
            incoming = int(replacement[source_index])
            mixed = round(original * (1.0 - weight) + incoming * weight)
            base[target_index] = max(-2147483648, min(2147483647, mixed))

def compare_exact_region(a: bytes, b: bytes, *, start_frame: int, end_frame: int, channels: int) -> bool:
    frame_bytes = channels * 4
    return a[start_frame * frame_bytes:end_frame * frame_bytes] == b[start_frame * frame_bytes:end_frame * frame_bytes]

def compare_region_offsets(
    a: bytes,
    b: bytes,
    *,
    a_start_frame: int,
    b_start_frame: int,
    frames: int,
    channels: int,
) -> bool:
    frame_bytes = channels * 4
    return (
        a[a_start_frame * frame_bytes:(a_start_frame + frames) * frame_bytes]
        == b[b_start_frame * frame_bytes:(b_start_frame + frames) * frame_bytes]
    )
