from __future__ import annotations

"""Local CPU/GPU/tool capability receipts and acceptance-campaign planning."""

from collections import Counter
from copy import deepcopy
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from earcrate.estate.model import (
    ESTATE_SCHEMA_VERSION,
    estate_seal,
    estate_sha256_file,
    estate_validate_seal,
    load_estate_json,
)

CommandRunner = Callable[[Sequence[str], float], dict[str, Any]]


def _estate_rig_now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _estate_default_command_runner(argv: Sequence[str], timeout: float) -> dict[str, Any]:
    try:
        process = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        return {
            "argv": list(argv),
            "returncode": int(process.returncode),
            "stdout": (process.stdout or "")[:64_000],
            "stderr": (process.stderr or "")[:64_000],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "returncode": None,
            "stdout": str(exc.stdout or "")[:64_000],
            "stderr": str(exc.stderr or "")[:64_000],
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "argv": list(argv),
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}"[:64_000],
            "timed_out": False,
        }


def _estate_total_memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except Exception:
            return None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        return page_size * pages
    except Exception:
        return None


def _estate_package_versions() -> dict[str, str | None]:
    packages = [
        "numpy",
        "scipy",
        "librosa",
        "soundfile",
        "mido",
        "torch",
        "torchaudio",
        "demucs",
        "basic-pitch",
        "allin1",
        "pyrubberband",
        "sounddevice",
        "onnxruntime",
        "onnxruntime-gpu",
        "transformers",
        "faiss-cpu",
        "faiss-gpu",
    ]
    out: dict[str, str | None] = {}
    for name in packages:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
        except Exception:
            out[name] = "unknown"
    return out


def _estate_executable_receipt(name: str, args: Sequence[str], runner: CommandRunner) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"name": name, "available": False, "path": None, "version": None}
    result = runner([path, *args], 15.0)
    text = (str(result.get("stdout") or "") + "\n" + str(result.get("stderr") or "")).strip()
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return {
        "name": name,
        "available": result.get("returncode") == 0 or bool(line),
        "path": path,
        "version": line[:500] or None,
        "returncode": result.get("returncode"),
        "timed_out": bool(result.get("timed_out")),
    }


def _estate_nvidia_receipt(runner: CommandRunner) -> dict[str, Any]:
    path = shutil.which("nvidia-smi")
    if not path:
        return {"available": False, "gpus": [], "driver": None, "cuda": None}
    query = runner(
        [
            path,
            "--query-gpu=name,uuid,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        20.0,
    )
    gpus: list[dict[str, Any]] = []
    if query.get("returncode") == 0:
        for line in str(query.get("stdout") or "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 4:
                try:
                    memory_mib: int | None = int(float(parts[2]))
                except Exception:
                    memory_mib = None
                gpus.append(
                    {
                        "name": parts[0],
                        "uuid": parts[1],
                        "memory_total_mib": memory_mib,
                        "driver_version": parts[3],
                    }
                )
    summary = runner([path], 20.0)
    text = str(summary.get("stdout") or "")
    cuda = None
    import re

    match = re.search(r"CUDA Version:\s*([0-9.]+)", text)
    if match:
        cuda = match.group(1)
    return {
        "available": bool(gpus) or summary.get("returncode") == 0,
        "path": path,
        "gpus": gpus,
        "driver": gpus[0].get("driver_version") if gpus else None,
        "cuda": cuda,
        "query_returncode": query.get("returncode"),
    }


def capture_rig_capabilities(
    *,
    roots: Iterable[str | Path] = (),
    include_audio_devices: bool = False,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    runner = command_runner or _estate_default_command_runner
    root_records: list[dict[str, Any]] = []
    for raw in roots:
        path = Path(raw).expanduser().resolve()
        record: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            try:
                usage = shutil.disk_usage(path)
                record.update(
                    {
                        "disk_total_bytes": int(usage.total),
                        "disk_used_bytes": int(usage.used),
                        "disk_free_bytes": int(usage.free),
                    }
                )
            except Exception as exc:
                record["disk_error"] = f"{type(exc).__name__}: {exc}"[:240]
        root_records.append(record)

    tools = [
        _estate_executable_receipt("python", ["--version"], runner),
        _estate_executable_receipt("ffmpeg", ["-version"], runner),
        _estate_executable_receipt("ffprobe", ["-version"], runner),
        _estate_executable_receipt("git", ["--version"], runner),
        _estate_executable_receipt("fpcalc", ["-version"], runner),
        _estate_executable_receipt("rubberband", ["--version"], runner),
    ]
    nvidia = _estate_nvidia_receipt(runner)
    packages = _estate_package_versions()

    audio: dict[str, Any] = {"requested": bool(include_audio_devices), "available": False, "devices": []}
    if include_audio_devices and packages.get("sounddevice"):
        try:
            import sounddevice  # type: ignore

            devices = sounddevice.query_devices()
            audio = {
                "requested": True,
                "available": True,
                "default_device": list(sounddevice.default.device),
                "devices": [
                    {
                        "index": index,
                        "name": str(device.get("name") or ""),
                        "max_input_channels": int(device.get("max_input_channels") or 0),
                        "max_output_channels": int(device.get("max_output_channels") or 0),
                        "default_samplerate": float(device.get("default_samplerate") or 0.0),
                    }
                    for index, device in enumerate(devices)
                ],
            }
        except Exception as exc:
            audio = {"requested": True, "available": False, "devices": [], "error": f"{type(exc).__name__}: {exc}"[:500]}

    payload: dict[str, Any] = {
        "schema_version": ESTATE_SCHEMA_VERSION,
        "kind": "earcrate_rig_capability_receipt",
        "captured_at": _estate_rig_now_utc(),
        "host": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "hostname_sha256": __import__("hashlib").sha256(platform.node().encode("utf-8")).hexdigest() if platform.node() else None,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable": sys.executable,
            "logical_cpu_count": os.cpu_count(),
            "total_memory_bytes": _estate_total_memory_bytes(),
        },
        "roots": root_records,
        "nvidia": nvidia,
        "python_packages": packages,
        "executables": tools,
        "audio_devices": audio,
        "environment_declarations": {
            "names_present": sorted(
                name
                for name in (
                    "EARCRATE_HOME",
                    "EARCRATE_CACHE_ROOT",
                    "EARCRATE_L3_ROOT",
                    "EARCRATE_STEMS",
                    "EARCRATE_BEATS",
                    "EARCRATE_TRANSFORM",
                    "EARCRATE_RANKER",
                    "CUDA_VISIBLE_DEVICES",
                )
                if name in os.environ
            ),
            "values_recorded": False,
        },
        "summary": {
            "logical_cpu_count": os.cpu_count(),
            "total_memory_bytes": _estate_total_memory_bytes(),
            "nvidia_gpu_count": len(nvidia.get("gpus") or []),
            "available_executables": sum(1 for row in tools if row.get("available")),
            "available_python_packages": sum(1 for value in packages.values() if value),
            "audio_device_count": len(audio.get("devices") or []),
            "roots": len(root_records),
        },
        "boundary": {
            "no_heavy_model_inference_run": True,
            "no_source_audio_decoded": True,
            "no_network_probe": True,
            "audio_devices_queried": bool(include_audio_devices),
            "capability_is_not_quality_acceptance": True,
        },
    }
    return estate_seal(payload)


__all__ = ["capture_rig_capabilities", "_estate_rig_now_utc", "_estate_package_versions"]
