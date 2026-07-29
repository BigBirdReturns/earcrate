from __future__ import annotations

"""Movable, standard-library-only reference provider and end-to-end demo."""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .interop import floor_export_crate
from .model import (
    FloorError,
    K_MANIFEST,
    K_REQUEST,
    PROTOCOL,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_sha256_file,
    floor_write_json_atomic,
)
from .protocol import floor_invoke_provider

REFERENCE_PROVIDER_ID = "org.earcrate.reference.text"
REFERENCE_PROVIDER_VERSION = "1.0.0"
REFERENCE_CAPABILITY = "text_measurement"
REFERENCE_MANIFEST_NAME = "reference.floor-provider.json"
REFERENCE_SCRIPT_NAME = "reference_provider.py"

_REFERENCE_SCRIPT = r'''#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

PROVIDER_ID = "org.earcrate.reference.text"
PROVIDER_VERSION = "1.0.0"
MANIFEST_NAME = "reference.floor-provider.json"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    raw = sys.stdin.buffer.read()
    request = json.loads(raw.decode("utf-8"))
    manifest_dir = Path(os.environ["FLOOR_MANIFEST_DIR"]).resolve()
    artifact_dir = Path(os.environ["FLOOR_ARTIFACT_DIR"]).resolve()
    manifest = json.loads((manifest_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    source = Path(request["inputs"][0]["path"]).resolve()
    data = source.read_bytes()
    text = data.decode("utf-8")
    summary = {
        "artifact_id": request["inputs"][0]["artifact_id"],
        "bytes": len(data),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "source_sha256": sha256(data),
    }
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output = artifact_dir / "summary.json"
    output.write_bytes(payload)
    result = {
        "schema_version": 1,
        "kind": "earcrate_floor_provider_result",
        "provider_id": PROVIDER_ID,
        "provider_version": PROVIDER_VERSION,
        "manifest_sha256": manifest["manifest_sha256"],
        "request_sha256": request["request_sha256"],
        "request_semantic_sha256": request["request_semantic_sha256"],
        "status": "ok",
        "outputs": [
            {
                "output_id": "text_measurement",
                "output_kind": "measurement",
                "confidence": 1.0,
                "evidence_refs": [request["inputs"][0]["artifact_id"]],
                "payload": summary,
                "metadata": {"implementation": "stdlib_reference"},
            },
            {
                "output_id": "summary_artifact",
                "output_kind": "derived_artifact",
                "confidence": 1.0,
                "evidence_refs": [request["inputs"][0]["artifact_id"]],
                "payload": {
                    "artifact_id": "summary_json",
                    "media_type": "application/json",
                    "source_media_copied": False,
                },
            },
        ],
        "artifacts": [
            {
                "artifact_id": "summary_json",
                "path": "summary.json",
                "sha256": sha256(payload),
                "size_bytes": len(payload),
                "media_type": "application/json",
                "role": "measurement_summary",
            }
        ],
        "diagnostics": {"network_used": False},
        "metadata": {
            "canonical_authority": False,
            "source_media_copied": False,
        },
    }
    sys.stdout.buffer.write(canonical(result))
    sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _empty_or_replace(path: Path, overwrite: bool) -> Path:
    root = path.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite nonempty reference-provider directory: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def floor_write_reference_provider(output_dir: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Write a provider that can run without importing EarCrate."""

    root = _empty_or_replace(Path(output_dir), overwrite)
    script = root / REFERENCE_SCRIPT_NAME
    script.write_text(_REFERENCE_SCRIPT, encoding="utf-8", newline="\n")
    try:
        script.chmod(0o755)
    except OSError:
        pass
    script_sha = floor_sha256_file(script)
    manifest = floor_seal_provider_manifest(
        {
            "schema_version": 1,
            "kind": K_MANIFEST,
            "provider_id": REFERENCE_PROVIDER_ID,
            "provider_version": REFERENCE_PROVIDER_VERSION,
            "display_name": "EarCrate Floor standard-library reference provider",
            "description": "Measures one UTF-8 text artifact and emits a derived JSON summary.",
            "capabilities": [REFERENCE_CAPABILITY],
            "entrypoint": {
                "protocol": PROTOCOL,
                "argv": ["${FLOOR_PYTHON}", "${FLOOR_MANIFEST_DIR}/reference_provider.py"],
                "working_directory": "manifest_dir",
            },
            "runtime": {
                "language": "python-stdlib",
                "requires_network": False,
                "determinism": "deterministic",
                "timeout_seconds": 30,
                "max_stdout_bytes": 1 << 20,
                "max_stderr_bytes": 1 << 20,
                "max_artifact_bytes": 1 << 20,
            },
            "evidence": {
                "accepted_branches": ["symbolic"],
                "accepted_tiers": ["community_symbolic_witness"],
            },
            "authority": {
                "may_emit": ["measurement", "derived_artifact"],
            },
            "supply_chain": {
                "license_expression": "CC0-1.0",
                "source_uri": "https://github.com/BigBirdReturns/earcrate",
                "source_revision": "reference-provider-v1",
                "executable_sha256": script_sha,
                "model_artifacts": [],
                "signatures": [],
            },
            "metadata": {
                "portable": True,
                "imports_earcrate": False,
                "reference_only": True,
            },
        }
    )
    manifest_path = root / REFERENCE_MANIFEST_NAME
    floor_write_json_atomic(manifest_path, manifest)
    sample = root / "sample.txt"
    sample.write_text("EarCrate Floor lets organs contribute evidence without becoming the organism.\n", encoding="utf-8", newline="\n")
    request = floor_seal_provider_request(
        {
            "schema_version": 1,
            "kind": K_REQUEST,
            "capability": REFERENCE_CAPABILITY,
            "evidence": {
                "branch": "symbolic",
                "tier": "community_symbolic_witness",
                "ancestor_branches": ["symbolic"],
                "prohibited_inputs": ["score answer keys", "commercial recording bytes"],
            },
            "inputs": [
                {
                    "artifact_id": "sample_text",
                    "path": str(sample.resolve()),
                    "sha256": floor_sha256_file(sample),
                    "size_bytes": sample.stat().st_size,
                    "media_type": "text/plain; charset=utf-8",
                    "role": "source_text",
                    "branch": "symbolic",
                    "tier": "community_symbolic_witness",
                    "ancestor_branches": ["symbolic"],
                    "metadata": {"fixture": True},
                }
            ],
            "parameters": {"operation": "count"},
            "seed": 0,
            "network_policy": {"allowed": False, "declared_hosts": []},
            "artifact_policy": {
                "output_dir": "",
                "max_total_bytes": 1 << 20,
                "allow_source_media_copy": False,
            },
            "metadata": {"reference_fixture": True},
        }
    )
    request_path = root / "request.json"
    floor_write_json_atomic(request_path, request)
    readme = root / "README.md"
    readme.write_text(
        "# EarCrate Floor reference provider\n\n"
        "This provider uses only the Python standard library and does not import EarCrate.\n"
        "It reads one sealed request from stdin and writes one provider result to stdout.\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "ok": True,
        "complete": True,
        "output_dir": str(root),
        "manifest_path": str(manifest_path),
        "request_path": str(request_path),
        "script_path": str(script),
        "sample_path": str(sample),
        "manifest": manifest,
        "request": request,
    }


def floor_run_reference_demo(output_dir: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Scaffold, invoke twice, and export a portable Floor crate."""

    root = _empty_or_replace(Path(output_dir), overwrite)
    provider = floor_write_reference_provider(root / "provider")
    invocation = floor_invoke_provider(
        provider["manifest_path"],
        provider["request_path"],
        root / "invocation",
        repeat=2,
        require_repeatability=True,
    )
    first_artifacts = Path(invocation["output_dir"]) / "run-0001" / "artifacts"
    crate = floor_export_crate(
        manifest=invocation["manifest"],
        request=invocation["request"],
        result=invocation["result"],
        receipt=invocation["receipt"],
        output_dir=root / "crate",
        artifact_root=first_artifacts,
        include_derived_artifacts=True,
    )
    if crate["crate"].get("source_media_copied") is not False:
        raise FloorError("reference demo copied source media")
    return {
        "ok": True,
        "complete": True,
        "output_dir": str(root),
        "provider": provider,
        "invocation": invocation,
        "crate": crate,
        "repeatability_passed": bool(invocation["receipt"]["repeatability"].get("passed")),
        "source_media_copied": False,
        "mapping_status": crate["crate"]["mapping_status"],
    }


__all__ = [
    "REFERENCE_PROVIDER_ID",
    "REFERENCE_PROVIDER_VERSION",
    "REFERENCE_CAPABILITY",
    "floor_write_reference_provider",
    "floor_run_reference_demo",
]
