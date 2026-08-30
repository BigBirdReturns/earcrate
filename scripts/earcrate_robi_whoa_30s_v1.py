#!/usr/bin/env python3
"""Bind, qualify, and ZIP one private-estate Robi WHOA loop candidate.

This is not a beat generator. Missing private providers, crate data, models, GPUs,
or candidate evidence cause refusal without audio. Historical Robi renders and
cloud-written synthesis are explicitly inadmissible inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT = ROOT / "configs" / "commissions" / "robi_whoa_30s_v1.json"
COMMISSION_SCHEMA = "earcrate_robi_whoa_30s_commission_v1"
CANDIDATE_SCHEMA = "earcrate_robi_whoa_candidate_v1"
AUDIO_SUFFIXES = {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


class CommissionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise CommissionError(f"JSON object required: {path}")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = _load(path)
    if value.get("schema_version") != COMMISSION_SCHEMA:
        raise CommissionError("unexpected commission schema")
    return value


def _regular(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise CommissionError(f"{label} must be a regular non-symlink file: {resolved}")
    return resolved


def _run(command: list[str], timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise CommissionError(f"required executable unavailable: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommissionError(f"command timed out: {command[0]}") from exc


def ffprobe(path: Path) -> dict[str, Any]:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,codec_type,sample_rate,channels,duration",
        "-of", "json", str(path),
    ])
    if result.returncode:
        raise CommissionError(f"ffprobe refused {path.name}: {result.stderr[-400:]}")
    return json.loads(result.stdout)


def canonical_pcm_identity(path: Path, sample_rate: int, channels: int) -> tuple[str, int]:
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
               "-ar", str(sample_rate), "-ac", str(channels), "-f", "f32le", "-"]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise CommissionError("required executable unavailable: ffmpeg") from exc
    assert process.stdout is not None and process.stderr is not None
    digest = hashlib.sha256()
    count = 0
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
        count += len(chunk)
    error = process.stderr.read().decode(errors="replace")
    if process.wait(timeout=1800):
        raise CommissionError(f"ffmpeg refused {path.name}: {error[-400:]}")
    frame_bytes = channels * 4
    if count % frame_bytes:
        raise CommissionError(f"decoded PCM is not frame-aligned: {path}")
    return digest.hexdigest(), count // frame_bytes


def bind_inputs(source: Path, bundle: Path, contract: dict[str, Any]) -> dict[str, Any]:
    source, bundle = _regular(source, "source"), _regular(bundle, "analysis bundle")
    expected = contract["source"]
    if source.stat().st_size != expected["container_bytes"] or sha256_file(source) != expected["container_sha256"]:
        raise CommissionError("source identity does not match the commission")
    expected_bundle = expected["analysis_bundle"]
    if bundle.stat().st_size != expected_bundle["bytes"] or sha256_file(bundle) != expected_bundle["sha256"]:
        raise CommissionError("analysis-bundle identity does not match the commission")
    probe = ffprobe(source)
    streams = [row for row in probe.get("streams", []) if row.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise CommissionError("exactly one source audio stream is required")
    stream = streams[0]
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    if int(stream.get("sample_rate", 0)) != expected["sample_rate"] or int(stream.get("channels", 0)) != expected["channels"]:
        raise CommissionError("source signal shape changed")
    if abs(duration - expected["duration_seconds"]) > 0.01:
        raise CommissionError("source duration changed")
    pcm = expected["canonical_pcm"]
    pcm_sha, frames = canonical_pcm_identity(source, pcm["sample_rate"], pcm["channels"])
    return {
        "kind": "earcrate_robi_whoa_private_input_receipt",
        "schema_version": 1,
        "commission_id": contract["commission_id"],
        "source": {
            "artifact_path": str(source), "bytes": source.stat().st_size,
            "sha256": sha256_file(source), "probe": probe,
            "canonical_pcm_sha256": pcm_sha, "canonical_pcm_frames": frames,
            "diagnostic_pcm_identity_matches": pcm_sha == pcm["sha256"],
            "container_identity_controls": True,
        },
        "analysis_bundle": {
            "artifact_path": str(bundle), "bytes": bundle.stat().st_size,
            "sha256": sha256_file(bundle), "classification": "failure_corpus_only",
        },
    }


def build_execution_request(receipt: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    source = receipt["source"]
    return {
        "kind": "earcrate_robi_whoa_private_execution_request",
        "schema_version": 1,
        "commission_id": contract["commission_id"],
        "source_binding": {
            "source_id": "robi_whoa_exact_source", "artifact_path": source["artifact_path"],
            "container_sha256": source["sha256"], "container_bytes": source["bytes"],
        },
        "analysis_bundle": receipt["analysis_bundle"],
        "target": contract["target"],
        "musical_contract": contract["musical_contract"],
        "allowed_authorities": contract["allowed_authorities"],
        "forbidden_mechanism_ids": contract["forbidden_mechanism_ids"],
        "candidate_manifest_schema": contract["required_candidate_manifest"]["schema"],
        "execution_policy": {
            "provider_or_crate_absent": "refuse_without_audio",
            "candidate_count_before_machine_triage": "unbounded_but_private",
            "candidate_count_after_machine_triage": 1,
            "owner_review_created_by_executor": False,
            "historical_robi_renders_are_inputs": False,
            "cloud_synthesized_audio_is_input": False,
        },
    }


def prepare_workspace(source: Path, bundle: Path, workspace: Path, contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Path]:
    contract = load_contract(contract_path)
    workspace = workspace.expanduser().resolve()
    if workspace.exists():
        raise CommissionError(f"workspace already exists: {workspace}")
    workspace.mkdir(parents=True)
    try:
        receipt = bind_inputs(source, bundle, contract)
        request = build_execution_request(receipt, contract)
        paths = {
            "receipt": workspace / "input-receipt.private.json",
            "request": workspace / "estate-execution-request.private.json",
            "contract": workspace / "commission-contract.json",
        }
        _write_exclusive(paths["receipt"], receipt)
        _write_exclusive(paths["request"], request)
        _write_exclusive(paths["contract"], contract)
        paths["workspace"] = workspace
        return paths
    except Exception:
        if not any(path.suffix.lower() in AUDIO_SUFFIXES for path in workspace.rglob("*")):
            shutil.rmtree(workspace, ignore_errors=True)
        raise


def _contained(base: Path, raw: str, label: str) -> Path:
    path = (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise CommissionError(f"{label} escapes candidate root") from exc
    return _regular(path, label)


def _file_ref(base: Path, value: dict[str, Any], label: str) -> Path:
    path = _contained(base, str(value.get("path", "")), label)
    if sha256_file(path) != str(value.get("sha256", "")).lower():
        raise CommissionError(f"{label} SHA-256 mismatch")
    return path


def _source_events(events: Iterable[dict[str, Any]], source_sha: str, duration: float) -> None:
    count = 0
    for index, event in enumerate(events):
        count += 1
        if str(event.get("source_container_sha256", "")).lower() != source_sha:
            raise CommissionError(f"source event {index} names another source")
        ss, se = float(event.get("source_start_seconds", -1)), float(event.get("source_end_seconds", -1))
        ts, te = float(event.get("target_start_seconds", -1)), float(event.get("target_end_seconds", -1))
        if not 0 <= ss < se <= duration or not 0 <= ts < te <= 30:
            raise CommissionError(f"source event {index} has an invalid span")
    if not count:
        raise CommissionError("source accounting has no Robi event")


def validate_candidate_manifest(path: Path, contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    contract = load_contract(contract_path)
    path = _regular(path, "candidate manifest")
    base, manifest = path.parent.resolve(), _load(path)
    if manifest.get("schema_version") != CANDIDATE_SCHEMA or manifest.get("commission_id") != contract["commission_id"]:
        raise CommissionError("candidate schema or commission identity mismatch")
    if int(manifest.get("candidate_count", 0)) != 1:
        raise CommissionError("owner admission requires exactly one candidate")
    candidate_id = str(manifest.get("candidate_id", ""))
    if not candidate_id:
        raise CommissionError("candidate_id is required")
    source_sha = contract["source"]["container_sha256"]
    if str(manifest.get("source", {}).get("container_sha256", "")).lower() != source_sha:
        raise CommissionError("candidate does not bind the exact Robi source")

    authority = manifest.get("authority") or {}
    allowed = {(row["kind"], row["provider_id"]) for row in contract["allowed_authorities"]}
    if (authority.get("kind"), authority.get("provider_id")) not in allowed:
        raise CommissionError("candidate authority is not admitted")
    authority_path = _file_ref(base, authority.get("receipt") or {}, "authority receipt")
    authority_receipt = _load(authority_path)
    if authority["kind"] in {"ace_step_vocal_to_bgm", "midi_sag_vocal_to_bgm"}:
        if authority_receipt.get("outcome") != "observed" or not authority_receipt.get("request_sha256") or not authority_receipt.get("receipt_sha256"):
            raise CommissionError("provider receipt is not a sealed observed execution")
        if not (authority_receipt.get("node_identity") or authority_receipt.get("node_identity_sha256")):
            raise CommissionError("provider receipt lacks node identity")
    elif not authority_receipt.get("complete_execution"):
        raise CommissionError("crate/rack authority lacks complete execution")

    construction = manifest.get("construction") or {}
    if construction.get("band_is_coherent_body") is not True:
        raise CommissionError("band is not proved as one coherent body")
    used = set(construction.get("prohibited_mechanisms_used") or [])
    forbidden = set(contract["forbidden_mechanism_ids"])
    if used:
        raise CommissionError("candidate admits prohibited mechanisms: " + ", ".join(sorted(used)))
    mechanisms = set(construction.get("mechanism_ids") or [])
    if not mechanisms or mechanisms & forbidden:
        raise CommissionError("candidate mechanism ledger is absent or forbidden")

    accounting = manifest.get("source_accounting") or {}
    if accounting.get("robi_foreground") is not True:
        raise CommissionError("Robi is not foreground identity")
    _source_events(accounting.get("events") or [], source_sha, contract["source"]["duration_seconds"])

    render = manifest.get("render") or {}
    if (int(render.get("frames", 0)), int(render.get("sample_rate", 0)), int(render.get("channels", 0))) != (1_440_000, 48_000, 2):
        raise CommissionError("master is not declared as exactly thirty seconds at 48 kHz stereo")
    if int(render.get("loop_cycles_printed", 0)) < contract["target"]["continuous_loop_check_cycles"]:
        raise CommissionError("loop check prints too few cycles")
    if float(render.get("stem_sum_peak_error", 1)) > 0.00001:
        raise CommissionError("stems do not reconcile to master")
    if float(render.get("boundary_discontinuity_dbfs", 0)) > -80:
        raise CommissionError("loop boundary is above the discontinuity floor")

    assets = []
    named = {}
    for key, label in (("master_wav", "master WAV"), ("master_flac", "master FLAC"),
                       ("preview_mp3", "preview MP3"), ("loop_check_mp3", "loop-check MP3")):
        named[key] = _file_ref(base, render.get(key) or {}, label)
        assets.append(named[key])
    stems = render.get("stems") or []
    if {row.get("role") for row in stems} < {"robi_foreground", "band"}:
        raise CommissionError("Robi and band stems are required")
    assets.extend(_file_ref(base, row, f"stem {index}") for index, row in enumerate(stems))

    wav_pcm, wav_frames = canonical_pcm_identity(named["master_wav"], 48_000, 2)
    flac_pcm, flac_frames = canonical_pcm_identity(named["master_flac"], 48_000, 2)
    if wav_frames != 1_440_000 or flac_frames != 1_440_000 or wav_pcm != flac_pcm:
        raise CommissionError("lossless masters are not the same exact thirty-second PCM")
    _, loop_frames = canonical_pcm_identity(named["loop_check_mp3"], 48_000, 2)
    if loop_frames < 1_440_000 * contract["target"]["continuous_loop_check_cycles"]:
        raise CommissionError("loop check is shorter than four uninterrupted cycles")

    disposition = manifest.get("machine_disposition") or {}
    if disposition.get("technical_pass") is not True or disposition.get("selected_candidate_id") != candidate_id or int(disposition.get("owner_options", 0)) != 1:
        raise CommissionError("machine disposition does not admit exactly this one candidate")
    for name in ("arrangement_receipt", "render_receipt", "loop_receipt"):
        reference = (manifest.get("receipts") or {}).get(name)
        if not isinstance(reference, dict):
            raise CommissionError(f"missing {name}")
        assets.append(_file_ref(base, reference, name.replace("_", " ")))
    assets.append(authority_path)
    return {"manifest": manifest, "manifest_path": path, "base": base,
            "assets": assets, "canonical_pcm_sha256": wav_pcm,
            "canonical_frames": wav_frames, "preview": named["preview_mp3"]}


def _public_projection(validated: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    manifest, render = validated["manifest"], validated["manifest"]["render"]
    return {
        "kind": "earcrate_robi_whoa_public_candidate_projection",
        "schema_version": 1,
        "commission_id": contract["commission_id"],
        "candidate_id": manifest["candidate_id"],
        "source_container_sha256": contract["source"]["container_sha256"],
        "authority": {"kind": manifest["authority"]["kind"], "provider_id": manifest["authority"]["provider_id"]},
        "construction": {"mechanism_ids": manifest["construction"]["mechanism_ids"],
                         "band_is_coherent_body": True, "prohibited_mechanisms_used": []},
        "render": {"frames": validated["canonical_frames"], "sample_rate": 48_000, "channels": 2,
                   "canonical_pcm_sha256": validated["canonical_pcm_sha256"],
                   "loop_cycles_printed": render["loop_cycles_printed"],
                   "stem_sum_peak_error": render["stem_sum_peak_error"],
                   "boundary_discontinuity_dbfs": render["boundary_discontinuity_dbfs"]},
        "authority_boundary": {"machine_qualified": True, "owner_accepted": False,
                               "rights_or_publication_authorized": False, "private_paths_included": False},
    }


def package_candidate(manifest_path: Path, output_zip: Path, contract_path: Path = DEFAULT_CONTRACT) -> Path:
    contract = load_contract(contract_path)
    validated = validate_candidate_manifest(manifest_path, contract_path)
    output_zip = output_zip.expanduser().resolve()
    if output_zip.exists():
        raise CommissionError(f"output already exists: {output_zip}")
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    base, manifest = validated["base"], validated["manifest"]
    files, seen = [], set()
    for path in validated["assets"]:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        files.append((path, "candidate/" + path.relative_to(base).as_posix()))
    public = {"projection": _public_projection(validated, contract),
              "files": [{"path": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                        for path, name in files]}
    listen = ("Robi WHOA 30-second estate candidate\n\nStart with candidate/" +
              Path(manifest["render"]["preview_mp3"]["path"]).as_posix() +
              "\nFor repetition use candidate/" + Path(manifest["render"]["loop_check_mp3"]["path"]).as_posix() +
              "\nUse WAV or FLAC for actual looping. Audio is delivered inside this ZIP.\n")
    descriptor, temporary = tempfile.mkstemp(prefix=output_zip.name + ".", suffix=".tmp", dir=output_zip.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("PUBLIC_CANDIDATE.json", _json_bytes(public))
            archive.writestr("COMMISSION_CONTRACT.json", _json_bytes(contract))
            archive.writestr("LISTEN_FIRST.txt", listen)
            for path, name in sorted(files, key=lambda item: item[1]):
                archive.write(path, name)
        with zipfile.ZipFile(temporary_path) as archive:
            bad = archive.testzip()
            if bad:
                raise CommissionError(f"ZIP CRC failure: {bad}")
        os.replace(temporary_path, output_zip)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_zip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", required=True, type=Path)
    prepare.add_argument("--analysis-bundle", required=True, type=Path)
    prepare.add_argument("--workspace", required=True, type=Path)
    qualify = commands.add_parser("qualify")
    qualify.add_argument("--candidate-manifest", required=True, type=Path)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--candidate-manifest", required=True, type=Path)
    finalize.add_argument("--output-zip", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            paths = prepare_workspace(args.source, args.analysis_bundle, args.workspace, args.contract)
            result = {"ok": True, "status": "private_execution_required", "audio_written": False,
                      "workspace": str(paths["workspace"]), "request": str(paths["request"]),
                      "input_receipt": str(paths["receipt"])}
        elif args.command == "qualify":
            value = validate_candidate_manifest(args.candidate_manifest, args.contract)
            result = {"ok": True, "candidate_id": value["manifest"]["candidate_id"],
                      "canonical_pcm_sha256": value["canonical_pcm_sha256"],
                      "frames": value["canonical_frames"], "owner_options": 1}
        else:
            output = package_candidate(args.candidate_manifest, args.output_zip, args.contract)
            result = {"ok": True, "output_zip": str(output), "bytes": output.stat().st_size,
                      "sha256": sha256_file(output), "owner_options": 1}
        print(json.dumps(result, indent=2))
        return 0
    except CommissionError as exc:
        print(json.dumps({"ok": False, "refused": True, "audio_written": False,
                          "error": str(exc)}, indent=2), file=sys.stderr)
        return 3
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
