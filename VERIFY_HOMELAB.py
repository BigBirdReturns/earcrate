#!/usr/bin/env python3
from __future__ import annotations

"""Verify the source-free EarCrate Homelab distribution and launch boundaries."""

import compileall
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def _run(argv: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def _json_stdout(process: subprocess.CompletedProcess[str]) -> dict:
    if process.returncode != 0:
        return {}
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    checks["estate_compiles"] = bool(compileall.compile_dir(str(ROOT / "earcrate" / "estate"), quiet=1))

    schema_path = ROOT / "schemas" / "earcrate_homelab_v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        checks["schema_loads"] = (
            schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
            and len(schema.get("oneOf") or []) >= 14
            and (((schema.get("$defs") or {}).get("catalog") or {}).get("allOf") or [None, {}])[1]
                .get("properties", {}).get("targets", {}).get("minItems") == 87
        )
    except Exception as exc:
        checks["schema_loads"] = False
        details["schema"] = f"{type(exc).__name__}: {exc}"

    with tempfile.TemporaryDirectory(prefix="earcrate-homelab-verify-") as raw_tmp:
        tmp = Path(raw_tmp)
        package_catalog = tmp / "package.catalog.json"
        package = _run([sys.executable, "-m", "earcrate", "homelab", "catalog", "--output", str(package_catalog)])
        package_payload = json.loads(package_catalog.read_text(encoding="utf-8")) if package.returncode == 0 and package_catalog.is_file() else {}
        checks["package_catalog"] = (
            package.returncode == 0
            and package_payload.get("summary", {}).get("targets") == 87
            and package_payload.get("summary", {}).get("fixtures") == 10
        )
        if not checks["package_catalog"]:
            details["package_catalog"] = {"returncode": package.returncode, "stdout": package.stdout[-2000:], "stderr": package.stderr[-2000:]}

        first = tmp / "earcrate-homelab-a.pyz"
        second = tmp / "earcrate-homelab-b.pyz"
        first_build = _run([sys.executable, "build/make_homelab_zipapp.py", "--output", str(first)])
        second_build = _run([sys.executable, "build/make_homelab_zipapp.py", "--output", str(second)])
        checks["zipapp_builds"] = first_build.returncode == 0 and second_build.returncode == 0 and first.is_file() and second.is_file()
        checks["zipapp_deterministic"] = checks["zipapp_builds"] and first.read_bytes() == second.read_bytes()
        if not checks["zipapp_builds"]:
            details["zipapp_build"] = {
                "first": (first_build.stdout + first_build.stderr)[-2000:],
                "second": (second_build.stdout + second_build.stderr)[-2000:],
            }

        if checks["zipapp_builds"]:
            with zipfile.ZipFile(first, "r") as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("HOMELAB_BUILD_MANIFEST.json"))
            forbidden_suffixes = {".wav", ".flac", ".mp3", ".m4a", ".pt", ".pth", ".ckpt", ".onnx", ".safetensors"}
            checks["zipapp_source_free"] = (
                "earcrate/estate/homelab_store.py" in names
                and "earcrate/estate/homelab_review.py" in names
                and "earcrate/estate/homelab_ops.py" in names
                and not any(Path(name).suffix.lower() in forbidden_suffixes for name in names)
                and manifest.get("boundary", {}).get("provider_binaries_bundled") is False
                and manifest.get("boundary", {}).get("model_weights_bundled") is False
                and manifest.get("boundary", {}).get("source_media_bundled") is False
            )
            zip_catalog = tmp / "zip.catalog.json"
            zip_process = _run([sys.executable, str(first), "catalog", "--output", str(zip_catalog)])
            zip_payload = json.loads(zip_catalog.read_text(encoding="utf-8")) if zip_process.returncode == 0 and zip_catalog.is_file() else {}
            checks["zipapp_catalog"] = (
                zip_process.returncode == 0
                and zip_payload.get("catalog_sha256") == package_payload.get("catalog_sha256")
                and zip_payload.get("summary", {}).get("targets") == 87
            )
            store_process = _run([sys.executable, str(first), "store-init", str(tmp / "store")])
            store_payload = _json_stdout(store_process)
            checks["zipapp_store_doctor"] = store_process.returncode == 0 and store_payload.get("ok") is True and store_payload.get("sqlite_quick_check") == "ok"
            if not checks["zipapp_catalog"]:
                details["zipapp_catalog"] = {"returncode": zip_process.returncode, "stdout": zip_process.stdout[-2000:], "stderr": zip_process.stderr[-2000:]}
            if not checks["zipapp_store_doctor"]:
                details["zipapp_store"] = {"returncode": store_process.returncode, "stdout": store_process.stdout[-2000:], "stderr": store_process.stderr[-2000:]}
        else:
            checks["zipapp_source_free"] = False
            checks["zipapp_catalog"] = False
            checks["zipapp_store_doctor"] = False

    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "details": details,
        "boundary": {
            "providers_executed": False,
            "models_loaded": False,
            "source_audio_decoded": False,
            "human_audition_performed": False,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
