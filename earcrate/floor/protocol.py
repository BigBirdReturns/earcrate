from __future__ import annotations

"""Reference subprocess host for the EarCrate Open Music Evidence Floor.

Wire contract:

* stdin: one sealed ProviderRequest JSON object
* stdout: one ProviderResult JSON object and no log text
* stderr: diagnostic text
* derived files: beneath ``FLOOR_ARTIFACT_DIR``

The reference host verifies custody and containment. It records the provider's network
declaration, but it does not pretend to enforce an operating-system network sandbox.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .catalog import floor_manifest_compatibility
from .model import (
    FloorError,
    FloorProtocolError,
    floor_read_json,
    floor_seal_conformance_report,
    floor_seal_invocation_receipt,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_seal_provider_result,
    floor_sha256_bytes,
    floor_sha256_file,
    floor_sha256_json,
    floor_write_json_atomic,
)

_FLOOR_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_FLOOR_PROTECTED_ENV = {
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "FLOOR_ARTIFACT_DIR",
    "FLOOR_REQUEST_SHA256",
    "FLOOR_PROVIDER_MANIFEST_SHA256",
    "FLOOR_NETWORK_POLICY",
}


def _floor_expand_token(value: str, *, manifest_dir: Path, artifact_dir: Path) -> str:
    return (
        str(value)
        .replace("${FLOOR_MANIFEST_DIR}", str(manifest_dir))
        .replace("${FLOOR_ARTIFACT_DIR}", str(artifact_dir))
        .replace("${PYTHON}", sys.executable)
    )


def _floor_resolve_argv(manifest: Mapping[str, Any], *, manifest_dir: Path, artifact_dir: Path) -> list[str]:
    entrypoint = manifest["entrypoint"]
    argv = [
        _floor_expand_token(item, manifest_dir=manifest_dir, artifact_dir=artifact_dir)
        for item in entrypoint["argv"]
    ]
    if not argv:
        raise FloorProtocolError("provider argv is empty")
    return argv


def _floor_working_directory(manifest: Mapping[str, Any], *, manifest_dir: Path, artifact_dir: Path) -> Path:
    raw = _floor_expand_token(
        str(manifest["entrypoint"].get("working_directory") or "${FLOOR_MANIFEST_DIR}"),
        manifest_dir=manifest_dir,
        artifact_dir=artifact_dir,
    )
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise FloorProtocolError(f"provider working directory does not exist: {path}")
    return path


def _floor_executable_identity(argv0: str, *, cwd: Path) -> dict[str, Any]:
    raw = str(argv0)
    candidate = Path(raw)
    resolved: Path | None = None
    if candidate.is_absolute() and candidate.is_file():
        resolved = candidate.resolve()
    elif (cwd / candidate).is_file():
        resolved = (cwd / candidate).resolve()
    else:
        found = shutil.which(raw)
        if found:
            resolved = Path(found).resolve()
    if resolved is None:
        return {"argv0": raw, "resolved_path": "", "sha256": None, "size_bytes": None}
    return {
        "argv0": raw,
        "resolved_path": str(resolved),
        "sha256": floor_sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _floor_provider_environment(
    manifest: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    manifest_dir: Path,
    artifact_dir: Path,
) -> tuple[dict[str, str], list[str]]:
    # Small cross-platform allowlist required to launch interpreters and resolve DLLs.
    inherited = (
        "PATH",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
    )
    env = {key: value for key in inherited if (value := os.environ.get(key)) is not None}
    applied: list[str] = []
    for key, raw_value in sorted(dict(manifest["entrypoint"].get("environment") or {}).items()):
        key_text = str(key).upper()
        if not _FLOOR_ENV_KEY.fullmatch(key_text):
            raise FloorProtocolError(f"provider environment key is not portable: {key!r}")
        if key_text in _FLOOR_PROTECTED_ENV or key_text.startswith("DYLD_"):
            raise FloorProtocolError(f"provider may not override protected environment key {key_text}")
        env[key_text] = _floor_expand_token(str(raw_value), manifest_dir=manifest_dir, artifact_dir=artifact_dir)
        applied.append(key_text)
    env.update(
        {
            "FLOOR_ARTIFACT_DIR": str(artifact_dir),
            "FLOOR_REQUEST_SHA256": str(request["request_sha256"]),
            "FLOOR_PROVIDER_MANIFEST_SHA256": str(manifest["manifest_sha256"]),
            "FLOOR_NETWORK_POLICY": str(request["network_policy"]),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env, applied


def _floor_verify_input_custody(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    custody: list[dict[str, Any]] = []
    for artifact in request["inputs"]:
        raw_path = str(artifact.get("path") or "")
        if not raw_path:
            raise FloorProtocolError(f"input {artifact['artifact_id']} has no local path for subprocess custody")
        path = Path(raw_path).expanduser()
        if path.is_symlink():
            raise FloorProtocolError(f"input {artifact['artifact_id']} may not be a symlink")
        path = path.resolve()
        if not path.is_file():
            raise FloorProtocolError(f"input {artifact['artifact_id']} does not exist: {path}")
        size = path.stat().st_size
        actual = floor_sha256_file(path)
        if actual != artifact["sha256"]:
            raise FloorProtocolError(
                f"input {artifact['artifact_id']} identity changed: expected {artifact['sha256']}, found {actual}"
            )
        if size != int(artifact["size_bytes"]):
            raise FloorProtocolError(
                f"input {artifact['artifact_id']} size changed: expected {artifact['size_bytes']}, found {size}"
            )
        custody.append(
            {
                "artifact_id": artifact["artifact_id"],
                "sha256": actual,
                "size_bytes": size,
                "media_kind": artifact["media_kind"],
                "path": str(path),
                "verified": True,
            }
        )
    return custody


def _floor_relative_artifact_path(raw: str) -> PurePosixPath:
    text = str(raw).replace("\\", "/")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise FloorProtocolError(f"provider artifact path is unsafe: {raw!r}")
    if ":" in pure.parts[0]:
        raise FloorProtocolError(f"provider artifact path contains a drive prefix: {raw!r}")
    return pure


def _floor_contained_path(base: Path, relative: str) -> Path:
    pure = _floor_relative_artifact_path(relative)
    candidate = base.joinpath(*pure.parts)
    current = base
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise FloorProtocolError(f"provider artifact path traverses a symlink: {relative!r}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise FloorProtocolError(f"provider artifact escapes FLOOR_ARTIFACT_DIR: {relative!r}") from exc
    return resolved


def _floor_verify_output_custody(
    raw_result: Mapping[str, Any],
    *,
    artifact_dir: Path,
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = deepcopy(dict(raw_result))
    artifacts = []
    total = 0
    raw_artifacts = list(normalized.get("artifacts") or [])
    if len(raw_artifacts) > int(request["limits"]["artifact_count"]):
        raise FloorProtocolError("provider emitted too many artifacts")
    for index, raw in enumerate(raw_artifacts):
        if not isinstance(raw, Mapping):
            raise FloorProtocolError(f"provider artifact {index} must be an object")
        row = deepcopy(dict(raw))
        relative = str(row.get("relative_path") or row.get("path") or "")
        path = _floor_contained_path(artifact_dir, relative)
        if not path.is_file():
            raise FloorProtocolError(f"provider artifact is missing: {relative}")
        if path.is_symlink():
            raise FloorProtocolError(f"provider artifact may not be a symlink: {relative}")
        size = path.stat().st_size
        total += size
        if total > int(request["limits"]["artifact_bytes"]):
            raise FloorProtocolError("provider artifacts exceed request artifact_bytes limit")
        actual = floor_sha256_file(path)
        expected = str(row.get("sha256") or "")
        if expected and actual != expected:
            raise FloorProtocolError(f"provider artifact hash mismatch for {relative}: expected {expected}, found {actual}")
        expected_size = row.get("size_bytes")
        if expected_size is not None and int(expected_size) != size:
            raise FloorProtocolError(f"provider artifact size mismatch for {relative}")
        row.pop("relative_path", None)
        row["path"] = pure_path = _floor_relative_artifact_path(relative).as_posix()
        row["sha256"] = actual
        row["size_bytes"] = size
        artifacts.append(row)
    normalized["artifacts"] = artifacts
    custody = [
        {
            "artifact_id": str(row.get("artifact_id") or ""),
            "relative_path": str(row["path"]),
            "sha256": str(row["sha256"]),
            "size_bytes": int(row["size_bytes"]),
            "media_kind": str(row.get("media_kind") or "application/octet-stream"),
            "verified": True,
        }
        for row in artifacts
    ]
    return normalized, custody


def _floor_parse_provider_stdout(stdout: bytes, limit: int) -> dict[str, Any]:
    if len(stdout) > int(limit):
        raise FloorProtocolError("provider stdout exceeds request limit")
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FloorProtocolError("provider stdout is not UTF-8") from exc
    try:
        value = json.loads(text)
    except Exception as exc:
        raise FloorProtocolError("provider stdout must contain exactly one JSON object and no logs") from exc
    if not isinstance(value, dict):
        raise FloorProtocolError("provider stdout JSON must be an object")
    return value


def _floor_manifest_from_source(
    source: str | Path | Mapping[str, Any],
    *,
    manifest_dir: str | Path | None = None,
) -> tuple[dict[str, Any], Path, str]:
    if isinstance(source, Mapping):
        sealed = floor_seal_provider_manifest(source)
        if manifest_dir is None:
            raise FloorProtocolError("manifest_dir is required when executing an in-memory manifest")
        directory = Path(manifest_dir).expanduser().resolve()
        return sealed, directory, "<memory>"
    path = Path(source).expanduser().resolve()
    sealed = floor_seal_provider_manifest(floor_read_json(path))
    return sealed, path.parent, str(path)


def floor_invoke_provider(
    manifest_source: str | Path | Mapping[str, Any],
    request_value: Mapping[str, Any],
    *,
    artifact_dir: str | Path | None = None,
    manifest_dir: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    manifest, manifest_root, manifest_label = _floor_manifest_from_source(manifest_source, manifest_dir=manifest_dir)
    request = floor_seal_provider_request(request_value)
    compatibility = floor_manifest_compatibility(manifest, request)
    if not compatibility["compatible"]:
        raise FloorProtocolError("provider is incompatible with request: " + "; ".join(compatibility["reasons"]))
    capability = compatibility["capability"]
    input_custody = _floor_verify_input_custody(request)

    created_temp = artifact_dir is None
    if artifact_dir is None:
        artifact_root = Path(tempfile.mkdtemp(prefix="earcrate-floor-artifacts-"))
    else:
        artifact_root = Path(artifact_dir).expanduser().resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        if any(artifact_root.iterdir()):
            raise FloorProtocolError(f"refusing nonempty FLOOR_ARTIFACT_DIR: {artifact_root}")
    if artifact_root.is_symlink():
        raise FloorProtocolError("FLOOR_ARTIFACT_DIR may not be a symlink")

    argv = _floor_resolve_argv(manifest, manifest_dir=manifest_root, artifact_dir=artifact_root)
    cwd = _floor_working_directory(manifest, manifest_dir=manifest_root, artifact_dir=artifact_root)
    env, provider_env_keys = _floor_provider_environment(
        manifest,
        request,
        manifest_dir=manifest_root,
        artifact_dir=artifact_root,
    )
    executable = _floor_executable_identity(argv[0], cwd=cwd)
    runtime_limit = min(
        int(request["limits"]["runtime_seconds"]),
        int(capability["max_runtime_seconds"]),
        int(timeout_seconds) if timeout_seconds is not None else 1 << 30,
    )
    request_bytes = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    started = time.monotonic()
    try:
        process = subprocess.run(
            argv,
            input=request_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            shell=False,
            timeout=runtime_limit,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FloorProtocolError(f"provider exceeded runtime limit of {runtime_limit}s") from exc
    elapsed = time.monotonic() - started
    if len(process.stderr) > int(request["limits"]["stderr_bytes"]):
        raise FloorProtocolError("provider stderr exceeds request limit")
    if process.returncode != 0:
        tail = process.stderr[-4096:].decode("utf-8", errors="replace")
        raise FloorProtocolError(f"provider exited with status {process.returncode}: {tail}")

    raw_result = _floor_parse_provider_stdout(process.stdout, int(request["limits"]["stdout_bytes"]))
    raw_result, output_custody = _floor_verify_output_custody(raw_result, artifact_dir=artifact_root, request=request)
    result = floor_seal_provider_result(raw_result, request=request, manifest=manifest)
    receipt = floor_seal_invocation_receipt(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_invocation_receipt",
            "provider_id": manifest["provider_id"],
            "provider_version": manifest["provider_version"],
            "provider_manifest_sha256": manifest["manifest_sha256"],
            "request_sha256": request["request_sha256"],
            "result_sha256": result["result_sha256"],
            "semantic_result_sha256": result["semantic_result_sha256"],
            "argv": argv,
            "working_directory": str(cwd),
            "executable": executable,
            "input_custody": input_custody,
            "output_custody": output_custody,
            "stdout": {
                "sha256": floor_sha256_bytes(process.stdout),
                "size_bytes": len(process.stdout),
            },
            "stderr": {
                "sha256": floor_sha256_bytes(process.stderr),
                "size_bytes": len(process.stderr),
            },
            "process": {
                "returncode": process.returncode,
                "elapsed_seconds": round(elapsed, 9),
                "shell": False,
            },
            "network": {
                "declared_policy": request["network_policy"],
                "host_enforcement": "declaration_only",
                "os_sandbox_proved": False,
            },
            "resource_limits": deepcopy(request["limits"]),
            "complete": True,
            "refusals": [],
            "metadata": {
                "manifest_source": manifest_label,
                "provider_environment_keys": provider_env_keys,
                "artifact_dir_temporary": created_temp,
                "quality_claimed": False,
                "canonical_authority_claimed": False,
            },
        }
    )
    return {
        "ok": True,
        "manifest": manifest,
        "request": request,
        "result": result,
        "receipt": receipt,
        "artifact_dir": str(artifact_root),
    }


def floor_conformance_run(
    manifest_source: str | Path | Mapping[str, Any],
    request_value: Mapping[str, Any],
    *,
    output_dir: str | Path,
    repeat: int = 2,
    manifest_dir: str | Path | None = None,
) -> dict[str, Any]:
    count = max(1, int(repeat))
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FloorProtocolError(f"refusing nonempty conformance output directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index in range(count):
        run_dir = destination / f"run-{index + 1:02d}" / "artifacts"
        try:
            run = floor_invoke_provider(
                manifest_source,
                request_value,
                artifact_dir=run_dir,
                manifest_dir=manifest_dir,
            )
            floor_write_json_atomic(destination / f"run-{index + 1:02d}" / "result.json", run["result"])
            floor_write_json_atomic(destination / f"run-{index + 1:02d}" / "invocation.receipt.json", run["receipt"])
            runs.append(
                {
                    "index": index,
                    "result_sha256": run["result"]["result_sha256"],
                    "semantic_result_sha256": run["result"]["semantic_result_sha256"],
                    "receipt_sha256": run["receipt"]["receipt_sha256"],
                    "artifact_dir": run["artifact_dir"],
                }
            )
        except Exception as exc:
            failures.append({"index": index, "type": type(exc).__name__, "message": str(exc)})
            break
    semantic = {row["semantic_result_sha256"] for row in runs}
    repeatable = len(runs) == count and len(semantic) == 1
    report = {
        "schema_version": 1,
        "kind": "earcrate_floor_conformance_report",
        "requested_runs": count,
        "completed_runs": len(runs),
        "runs": runs,
        "failures": failures,
        "checks": {
            "request_custody_verified": bool(runs),
            "result_schema_accepted": bool(runs),
            "output_artifacts_contained": bool(runs),
            "output_artifact_identities_verified": bool(runs),
            "repeatability_checked": count > 1,
            "semantic_result_repeatable": repeatable,
            "network_policy_declaration_checked": bool(runs),
            "os_network_sandbox_proved": False,
        },
        "complete": bool(runs) and not failures and (repeatable if count > 1 else True),
        "quality_claimed": False,
        "selection_authority": False,
    }
    report = floor_seal_conformance_report(report)
    floor_write_json_atomic(destination / "conformance.report.json", report)
    return report


__all__ = [
    "floor_invoke_provider",
    "floor_conformance_run",
]
