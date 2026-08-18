"""Capture what a delivered A1-02 candidate actually is, before anything calls it an answer key.

The commission declares an edition prospectively; acquisition delivers a file. Those
are different things, and the gap between them is where an answer key quietly becomes
"whatever we downloaded". So this captures identity and measurement, records the
declared edition alongside what was observed, and stops at
`declared_answer_key_candidate`.

Nothing here promotes anything. Promotion needs the structural comparison against the
score form, and that comparison is a separate decision with two admissible outcomes.

The file is read, never rewritten: no rename, no transcode, no normalization. A
canonical PCM digest is computed by decoding, which leaves the delivered container
untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from ..album.bindings import SourceBinding
from ..evidence.identity import seal, sha256_bytes, sha256_file

CANDIDATE_STATUS = "declared_answer_key_candidate"


class CustodyError(RuntimeError):
    pass


def _run(argv: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=not binary, check=False,
                          timeout=1800)


def probe(path: Path, *, ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Container facts, read from the delivered file exactly as it arrived."""
    result = _run([ffprobe, "-v", "error", "-show_entries",
                   "format=format_name,duration,bit_rate,size",
                   "-show_entries",
                   "stream=codec_name,sample_rate,channels,bits_per_raw_sample,"
                   "bits_per_sample,sample_fmt,duration",
                   "-select_streams", "a:0", "-of", "json", str(path)])
    if result.returncode != 0:
        raise CustodyError(f"ffprobe failed for {path.name}: {result.stderr[-500:]}")
    value = json.loads(result.stdout)
    stream = (value.get("streams") or [{}])[0]
    fmt = value.get("format") or {}
    depth = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0)
    return {
        "format_name": fmt.get("format_name"),
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "bit_depth": depth or None,
        "sample_fmt": stream.get("sample_fmt"),
        "duration_seconds": round(float(fmt.get("duration") or stream.get("duration") or 0), 3),
        "bytes": int(fmt.get("size") or 0),
    }


def canonical_pcm(path: Path, *, sample_rate: int, channels: int,
                  ffmpeg: str = "ffmpeg") -> bytes:
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "pcm_s32le", "-f", "s32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=3600)
    if result.returncode != 0:
        raise CustodyError(
            f"decode failed for {path.name}: {result.stderr.decode('utf-8', 'replace')[-500:]}")
    return result.stdout


def silence_edges(pcm: bytes, *, sample_rate: int, channels: int,
                  threshold: float = 1e-4) -> dict[str, float]:
    """Leading and trailing silence, because an edit often announces itself there.

    Measured on the decoded PCM rather than trusted from metadata: a radio edit and a
    full version can share a container format and differ exactly here.
    """
    from array import array

    values = array("i")
    values.frombytes(pcm)
    frame_count = len(values) // channels
    ceiling = 2 ** 31 - 1
    limit = threshold * ceiling

    def first_sounding(indices) -> int:
        for frame in indices:
            base = frame * channels
            if any(abs(values[base + channel]) > limit for channel in range(channels)):
                return frame
        return frame_count

    lead = first_sounding(range(frame_count))
    tail = first_sounding(range(frame_count - 1, -1, -1))
    return {
        "leading_silence_seconds": round(lead / sample_rate, 4),
        "trailing_silence_seconds": round(max(0, frame_count - 1 - tail) / sample_rate, 4),
        "sounding_duration_seconds": round(max(0, tail - lead + 1) / sample_rate, 4),
    }


def capture(path: Path, *, declaration: Mapping[str, Any], provenance: Mapping[str, Any],
            ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Everything the commission asked to capture, plus what the audio actually shows."""
    path = Path(path)
    if not path.is_file():
        raise CustodyError(f"no such delivered file: {path}")

    container = probe(path, ffprobe=ffprobe)
    if not container["sample_rate"] or not container["channels"]:
        raise CustodyError(f"{path.name} carries no decodable audio stream")
    pcm = canonical_pcm(path, sample_rate=container["sample_rate"],
                        channels=container["channels"], ffmpeg=ffmpeg)
    edges = silence_edges(pcm, sample_rate=container["sample_rate"],
                          channels=container["channels"])

    observed = {
        "original_filename": path.name,
        "container_sha256": sha256_file(path),
        "canonical_pcm_sha256": sha256_bytes(pcm),
        "canonical_decode": {"sample_rate": container["sample_rate"],
                             "channels": container["channels"], "format": "s32le"},
        **container,
        **edges,
    }

    binding = SourceBinding(
        source_id=f"a1-02-audio-candidate-{observed['container_sha256'][:12]}",
        role="audio_answer_key", modality="audio_recording",
        authority_class="answer_key", privacy_class="private_local",
        custody_class="private_custody",
        identities={"container_sha256": observed["container_sha256"],
                    "canonical_pcm_sha256": observed["canonical_pcm_sha256"]},
        edition=dict(declaration), verified=True,
        verification_note=(f"{CANDIDATE_STATUS}: identities captured from the delivered "
                           "file; structural fit not yet established"),
        location=str(path))

    return seal({
        "kind": "earcrate_a1_02_audio_candidate_capture",
        "schema_version": 1,
        "track_id": "A1-02",
        "status": CANDIDATE_STATUS,
        "promotion_requires": (
            "structural comparison against the score form; FIT binds this exact object as "
            "audio_answer_key, NONFIT keeps it as a control candidate"),
        "declared_edition": dict(declaration),
        "acquisition_provenance": dict(provenance),
        "observed": observed,
        "binding": binding.public_projection(),
        "declared_versus_observed": compare_declaration(declaration, observed),
    }, "capture_sha256")


def compare_declaration(declaration: Mapping[str, Any],
                        observed: Mapping[str, Any]) -> dict[str, Any]:
    """The cheap checks that can be made before any musical comparison runs.

    Duration is the one that matters here: the declared edition is a roughly 7:06
    full-length version, and every excluded variant is materially shorter. This does not
    establish fit -- it establishes that the file is not obviously the wrong object.
    """
    findings: list[str] = []
    approximate = str(declaration.get("approximate_duration") or "")
    expected_seconds = None
    if ":" in approximate:
        minutes, seconds = approximate.split(":", 1)
        try:
            expected_seconds = int(minutes) * 60 + int(seconds)
        except ValueError:
            expected_seconds = None

    duration = float(observed.get("duration_seconds") or 0.0)
    if expected_seconds:
        delta = duration - expected_seconds
        if abs(delta) > 20:
            findings.append(
                f"duration {duration:.1f}s differs from the declared {approximate} by "
                f"{delta:+.1f}s; a radio edit or a different named mix is the usual cause")
    if duration and duration < 300:
        findings.append(
            f"duration {duration:.1f}s is far short of a full-length version; this looks "
            "like an edit rather than the declared object")
    if observed.get("bit_depth") and int(observed["bit_depth"]) < 16:
        findings.append("bit depth is below 16; this is not a lossless delivery")
    if str(observed.get("codec_name") or "") in ("mp3", "aac", "vorbis", "opus"):
        findings.append(
            f"codec {observed['codec_name']} is lossy; the declaration asks for a lossless "
            "download")

    return {
        "expected_duration_seconds": expected_seconds,
        "observed_duration_seconds": duration,
        "obvious_mismatches": findings,
        "looks_like_the_declared_object": not findings,
        "note": ("These are container-level sanity checks. They cannot establish structural "
                 "fit, and passing them is not promotion."),
    }
