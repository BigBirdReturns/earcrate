from __future__ import annotations

import base64
from collections import defaultdict
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, MutableMapping, Sequence
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from .catalog import validate_generation_request
from .core import (
    AUDIO_SUFFIXES,
    AUTHORITY_LIMITS,
    HASH_FIELDS,
    SCHEMA_VERSION,
    ProviderUnavailable,
    ValidationError,
    artifact_identity,
    atomic_write,
    canonical_json_bytes,
    is_sha256,
    looks_like_local_path,
    now_utc,
    redact,
    require_portable,
    seal,
    sha256_bytes,
    validate_seal,
    write_json,
)


def _safe_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    keep = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
        "CUDA_VISIBLE_DEVICES", "CUDA_HOME", "TORCH_HOME", "HF_HOME", "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE", "XDG_CACHE_HOME", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    }
    env = {key: value for key, value in os.environ.items() if key in keep and value}
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _run_command(argv: Sequence[str], *, cwd: Path, timeout: int, environment: Mapping[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv), cwd=cwd, env=_safe_environment(environment), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=timeout, shell=False,
        )
        return {
            "returncode": int(completed.returncode),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_tail": completed.stdout[-20000:],
            "stderr_tail": completed.stderr[-20000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_tail": str(exc.stdout or "")[-20000:],
            "stderr_tail": str(exc.stderr or "")[-20000:],
            "timed_out": True,
        }


class _DefaultDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _format_context(value: Any, context: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(_DefaultDict(context))
    if isinstance(value, Mapping):
        return {str(key): _format_context(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [_format_context(child, context) for child in value]
    return value


def execute_generation_request(
    request: Mapping[str, Any],
    *,
    provider: Mapping[str, Any],
    probe: Mapping[str, Any],
    local_adapter: Mapping[str, Any],
    private_source_paths: Mapping[str, str | Path],
    output_directory: str | Path,
    node_identity: Mapping[str, Any],
    gpu_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request_sha = validate_generation_request(request)
    provider_id = str(request["provider_id"])
    if provider_id != provider.get("provider_id") or provider_id != probe.get("provider_id"):
        raise ValidationError("request, provider, and probe do not reconcile")
    if probe.get("ready") is not True:
        raise ProviderUnavailable("provider probe is not ready")
    if request["task_mode"] not in set(provider.get("capabilities") or []):
        raise ProviderUnavailable("provider does not declare the requested task mode")

    output = Path(output_directory).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    source_paths: dict[str, Path] = {}
    for row in request.get("conditioning") or []:
        source_id = str(row["source_id"])
        if source_id not in private_source_paths:
            raise ValidationError(f"missing private path for conditioning source {source_id}")
        path = Path(private_source_paths[source_id]).expanduser().absolute()
        identity = artifact_identity(path)
        if identity.sha256 != row["container_sha256"]:
            raise ValidationError(f"conditioning source mutated: {source_id}")
        source_paths[source_id] = path

    adapter = deepcopy(dict(local_adapter))
    context = {
        "output_dir": str(output),
        "request_json": str(output / "request.private.json"),
        "provider_id": provider_id,
        "task_mode": str(request["task_mode"]),
        "seed": str(request["seed"]),
        "gpu": str((gpu_identity or {}).get("device") or ""),
        "model_id": str(adapter.get("model_id") or provider_id),
    }
    for index, (source_id, path) in enumerate(sorted(source_paths.items())):
        context[f"source{index}"] = str(path)
        context[f"source_{source_id}"] = str(path)
    write_json(output / "request.private.json", request, exclusive=True)

    adapter_kind = str(adapter.get("kind") or "")
    command_evidence: dict[str, Any] = {}
    output_paths: list[Path] = []
    outcome = "failed"
    refusal: str | None = None

    if adapter_kind == "command":
        argv = [str(value) for value in _format_context(adapter.get("argv") or [], context)]
        if not argv:
            raise ValidationError("command adapter requires argv")
        result = _run_command(
            argv,
            cwd=output,
            timeout=int(adapter.get("timeout_seconds") or 7200),
            environment={"CUDA_VISIBLE_DEVICES": context["gpu"]} if context["gpu"] else {},
        )
        write_json(output / "execution.private.json", {"adapter_kind": adapter_kind, "result": result}, exclusive=True)
        command_evidence = {
            "adapter_kind": adapter_kind,
            "returncode": result.get("returncode"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "timed_out": result.get("timed_out"),
            "stdout_sha256": sha256_bytes(str(result.get("stdout_tail") or "").encode("utf-8")),
            "stderr_sha256": sha256_bytes(str(result.get("stderr_tail") or "").encode("utf-8")),
        }
        output_paths = [path for path in sorted(output.rglob("*")) if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES]
        outcome = "observed" if result["returncode"] == 0 and output_paths else "failed"
        if outcome == "failed":
            refusal = "command did not produce audio"
    elif adapter_kind == "http_json":
        base_url = str(adapter.get("base_url") or "").rstrip("/")
        if not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValidationError("http_json execution is limited to loopback services")
        endpoint = str(adapter.get("endpoint") or "/api/music/{model_id}").format_map(_DefaultDict(context))
        payload = _format_context(adapter.get("request_template") or {}, context)
        prompt = dict(request.get("prompt") or {})
        payload.setdefault("prompt", prompt.get("caption") or prompt.get("style") or "")
        payload.setdefault("seed", request.get("seed"))
        payload.setdefault("params", {})
        payload["params"].setdefault("earcrate_request_sha256", request_sha)
        req = urllib_request.Request(
            base_url + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib_request.urlopen(req, timeout=int(adapter.get("timeout_seconds") or 7200)) as response:
                decoded = json.loads(response.read().decode("utf-8"))
                status = response.status
            if decoded.get("audio_base64"):
                suffix = "." + str(decoded.get("format") or "wav").lstrip(".")
                target = output / f"generated{suffix}"
                atomic_write(target, base64.b64decode(decoded["audio_base64"]), exclusive=True)
                output_paths.append(target)
            output_id = decoded.get("output_id")
            if output_id and not output_paths and adapter.get("download_endpoint"):
                download_url = base_url + str(adapter["download_endpoint"]).format(output_id=output_id)
                with urllib_request.urlopen(download_url, timeout=900) as response:
                    suffix = mimetypes.guess_extension(response.headers.get_content_type()) or ".wav"
                    target = output / f"generated{suffix}"
                    atomic_write(target, response.read(), exclusive=True)
                    output_paths.append(target)
            private_metadata = {key: value for key, value in decoded.items() if key != "audio_base64"}
            write_json(output / "execution.private.json", {"adapter_kind": adapter_kind, "response_metadata": private_metadata}, exclusive=True)
            allowed = {
                key: decoded.get(key)
                for key in ("status", "model", "sample_rate", "duration_sec", "format", "generation_time_sec", "output_id")
                if key in decoded
            }
            command_evidence = {
                "adapter_kind": adapter_kind,
                "http_status": status,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "response_metadata": allowed,
            }
            outcome = "observed" if output_paths else "failed"
            if outcome == "failed":
                refusal = "local HTTP provider returned no resolvable audio"
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            command_evidence = {"adapter_kind": adapter_kind, "error_type": type(exc).__name__}
            refusal = "local HTTP provider request failed"
    else:
        raise ValidationError(f"unsupported execution adapter kind: {adapter_kind!r}")

    artifacts = []
    for path in output_paths:
        identity = artifact_identity(path)
        artifacts.append({"name": identity.name, "sha256": identity.sha256, "bytes": identity.bytes, "media_kind": identity.media_kind})
    receipt = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_run_receipt",
            "recorded_at": now_utc(),
            "request_sha256": request_sha,
            "provider_id": provider_id,
            "provider_repository": deepcopy(dict(provider.get("repository") or {})),
            "task_mode": request["task_mode"],
            "model": deepcopy(dict(request["model"])),
            "seed": request["seed"],
            "node": deepcopy(dict(node_identity)),
            "gpu": deepcopy(dict(gpu_identity or {})),
            "probe_sha256": probe.get("probe_sha256"),
            "outcome": outcome,
            "refusal": refusal,
            "conditioning": [
                {"source_id": row["source_id"], "container_sha256": row["container_sha256"]}
                for row in request.get("conditioning") or []
            ],
            "artifacts": artifacts,
            "execution": command_evidence,
            "rights_scope": deepcopy(dict(request.get("rights_scope") or {})),
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )
    require_portable(receipt, label="generation run receipt")
    write_json(output / "generation-receipt.json", receipt, exclusive=True)
    return receipt


def generated_material_from_receipt(
    receipt: Mapping[str, Any],
    *,
    artifact_sha256: str,
    role: str,
    musical_function: str,
    generation_strategy: str,
) -> dict[str, Any]:
    receipt_sha = validate_seal(receipt, kind="earcrate_generation_run_receipt")
    artifact = next((dict(row) for row in receipt.get("artifacts") or [] if row.get("sha256") == artifact_sha256), None)
    if artifact is None:
        raise ValidationError("artifact is not declared by the generation receipt")
    if receipt.get("outcome") != "observed":
        raise ValidationError("failed or refused generation cannot become material")
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generated_material",
            "created_at": now_utc(),
            "generation_receipt_sha256": receipt_sha,
            "provider_id": receipt.get("provider_id"),
            "task_mode": receipt.get("task_mode"),
            "model": deepcopy(dict(receipt.get("model") or {})),
            "seed": receipt.get("seed"),
            "artifact": artifact,
            "role": role,
            "musical_function": musical_function,
            "generation_strategy": generation_strategy,
            "rights_scope": deepcopy(dict(receipt.get("rights_scope") or {})),
            "status": "candidate_unreviewed",
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )


def material_to_performance_source(material: Mapping[str, Any], *, source_id: str) -> dict[str, Any]:
    material_sha = validate_seal(material, kind="earcrate_generated_material")
    artifact = dict(material.get("artifact") or {})
    if not is_sha256(artifact.get("sha256")):
        raise ValidationError("generated material has no artifact identity")
    return {
        "source_id": source_id,
        "container_sha256": artifact["sha256"],
        "canonical_pcm_sha256": None,
        "source_kind": "generated_material",
        "generated_material_sha256": material_sha,
        "generation_receipt_sha256": material.get("generation_receipt_sha256"),
        "provider_id": material.get("provider_id"),
        "task_mode": material.get("task_mode"),
        "role": material.get("role"),
        "musical_function": material.get("musical_function"),
    }


def build_generation_frontier(
    materials: Sequence[Mapping[str, Any]],
    *,
    incumbent: Mapping[str, Any] | None = None,
    maximum_options: int = 4,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen_audio: set[str] = set()
    if incumbent:
        rows.append({"kind": "incumbent_control", **deepcopy(dict(incumbent))})
        if is_sha256(incumbent.get("container_sha256")):
            seen_audio.add(str(incumbent["container_sha256"]))
    strategy_seen: set[str] = set()
    for material in materials:
        material_sha = validate_seal(material, kind="earcrate_generated_material")
        artifact = dict(material.get("artifact") or {})
        audio_sha = str(artifact.get("sha256") or "")
        strategy = str(material.get("generation_strategy") or "unknown")
        if audio_sha in seen_audio:
            continue
        if strategy in strategy_seen and len(rows) < maximum_options - 1:
            continue
        rows.append(
            {
                "kind": "generated_candidate",
                "material_sha256": material_sha,
                "container_sha256": audio_sha,
                "provider_id": material.get("provider_id"),
                "task_mode": material.get("task_mode"),
                "generation_strategy": strategy,
                "role": material.get("role"),
                "musical_function": material.get("musical_function"),
            }
        )
        seen_audio.add(audio_sha)
        strategy_seen.add(strategy)
        if len(rows) >= maximum_options:
            break
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_frontier",
            "created_at": now_utc(),
            "entries": rows,
            "maximum_options": maximum_options,
            "policy": {
                "incumbent_control_required": incumbent is not None,
                "deduplicate_by_audio_identity": True,
                "strategy_diversity_preferred": True,
                "machine_selection_is_not_musical_acceptance": True,
            },
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )


def build_public_projection(objects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    entries = []
    projections = []
    for value in objects:
        counters: MutableMapping[str, int] = {"paths": 0, "secrets": 0}
        projected = redact(deepcopy(dict(value)), counters=counters)
        projections.append(projected)
        kind = str(value.get("kind") or "unknown")
        field = HASH_FIELDS.get(kind)
        entries.append(
            {
                "source_kind": kind,
                "source_identity": value.get(field) if field else None,
                "projection_sha256": sha256_bytes(canonical_json_bytes(projected)),
                "paths_redacted": counters["paths"],
                "secrets_redacted": counters["secrets"],
            }
        )
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_public_projection",
            "created_at": now_utc(),
            "entries": entries,
            "projections": projections,
            "boundary": {
                "source_audio_exported": False,
                "model_weights_exported": False,
                "local_paths_exported": False,
                "credentials_exported": False,
                "human_acceptance_claimed": False,
            },
        }
    )
