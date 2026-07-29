from __future__ import annotations

"""Language-independent reference-provider scaffold."""

from pathlib import Path
from typing import Any

from .model import (
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_sha256_file,
    floor_write_json_atomic,
)

_FLOOR_REFERENCE_PROVIDER = r'''#!/usr/bin/env python3
"""Third-party EarCrate Floor provider using only the Python standard library."""
import hashlib
import json
import os
import sys
from pathlib import Path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    request = json.loads(sys.stdin.read())
    if request.get("kind") != "earcrate_floor_provider_request":
        raise SystemExit(2)
    source = request["inputs"][0]
    path = Path(source["path"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != source["sha256"]:
        raise SystemExit(3)
    artifact_dir = Path(os.environ["FLOOR_ARTIFACT_DIR"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output = artifact_dir / "echo.txt"
    output.write_bytes(payload)
    result = {
        "schema_version": 1,
        "kind": "earcrate_floor_provider_result",
        "request_sha256": request["request_sha256"],
        "provider_manifest_sha256": os.environ["FLOOR_PROVIDER_MANIFEST_SHA256"],
        "provider_id": "org.earcrate.reference.echo",
        "provider_version": "1.0.0",
        "status": "success",
        "emissions": [
            {
                "kind": "measurement",
                "subject": source["artifact_id"],
                "payload": {
                    "byte_count": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "protocol": "earcrate-floor-stdio-json-v1"
                },
                "evidence_refs": [source["artifact_id"]],
                "confidence": 1.0,
                "metadata": {"third_party_imports_earcrate": False}
            }
        ],
        "artifacts": [
            {
                "artifact_id": "echo_copy",
                "relative_path": "echo.txt",
                "sha256": sha256_file(output),
                "size_bytes": output.stat().st_size,
                "media_kind": "text/plain",
                "role": "derived_echo",
                "branch": request["evidence_branch"],
                "ancestor_branches": [request["evidence_branch"]],
                "metadata": {}
            }
        ],
        "refusals": [],
        "metrics": {"bytes_copied": len(payload)},
        "metadata": {"network_used": False}
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
'''


def floor_write_reference_provider(output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"refusing nonempty reference-provider directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    provider_path = destination / "reference_provider.py"
    provider_path.write_text(_FLOOR_REFERENCE_PROVIDER, encoding="utf-8", newline="\n")
    sample_path = destination / "sample.txt"
    sample_path.write_text("EarCrate Floor reference provider\n", encoding="utf-8", newline="\n")
    manifest = floor_seal_provider_manifest(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_provider_manifest",
            "provider_id": "org.earcrate.reference.echo",
            "provider_version": "1.0.0",
            "display_name": "EarCrate Floor reference echo provider",
            "description": "Movable standard-library provider proving the stdio JSON and artifact-custody floor.",
            "protocol": {"name": "earcrate-floor-stdio-json", "version": 1},
            "entrypoint": {
                "argv": ["${PYTHON}", "${FLOOR_MANIFEST_DIR}/reference_provider.py"],
                "working_directory": "${FLOOR_MANIFEST_DIR}",
                "environment": {},
            },
            "capabilities": [
                {
                    "capability": "file.echo",
                    "input_media_kinds": ["text/plain"],
                    "result_kinds": ["measurement", "derived_artifact", "refusal"],
                    "evidence_branches": ["symbolic"],
                    "evidence_tiers": ["community_symbolic_witness"],
                    "network_policy": "forbidden",
                    "determinism": "bit_exact",
                    "max_runtime_seconds": 30,
                    "max_output_bytes": 1 << 20,
                    "parameter_schema": {},
                    "metadata": {"reference_provider": True},
                }
            ],
            "authority": {
                "may_emit": ["measurement", "derived_artifact", "refusal"],
                "may_not_emit": [],
            },
            "supply_chain": {
                "license_expression": "CC0-1.0",
                "source_uri": "",
                "artifact_sha256": floor_sha256_file(provider_path),
                "model_identities": [],
                "signatures": [],
            },
            "metadata": {
                "language": "python-standard-library",
                "third_party_imports_earcrate": False,
                "movable_directory": True,
            },
        }
    )
    manifest_path = floor_write_json_atomic(destination / "reference.floor-provider.json", manifest)
    request = floor_seal_provider_request(
        {
            "schema_version": 1,
            "kind": "earcrate_floor_provider_request",
            "capability": "file.echo",
            "evidence_branch": "symbolic",
            "evidence_tier": "community_symbolic_witness",
            "inputs": [
                {
                    "artifact_id": "sample_text",
                    "sha256": floor_sha256_file(sample_path),
                    "size_bytes": sample_path.stat().st_size,
                    "media_kind": "text/plain",
                    "role": "fixture",
                    "branch": "symbolic",
                    "ancestor_branches": ["symbolic"],
                    "path": str(sample_path),
                    "uri": "",
                    "metadata": {},
                }
            ],
            "parameters": {},
            "allowed_result_kinds": ["measurement", "derived_artifact", "refusal"],
            "network_policy": "forbidden",
            "limits": {
                "runtime_seconds": 30,
                "stdout_bytes": 1 << 20,
                "stderr_bytes": 1 << 20,
                "artifact_bytes": 1 << 20,
                "artifact_count": 8,
            },
            "context": {},
            "metadata": {"fixture": "reference_echo"},
        }
    )
    request_path = floor_write_json_atomic(destination / "request.json", request)
    readme = destination / "README.md"
    readme.write_text(
        "# EarCrate Floor reference provider\n\n"
        "This directory is movable. `reference_provider.py` imports no EarCrate code and communicates only through one JSON request on stdin, one JSON result on stdout, and derived files beneath `FLOOR_ARTIFACT_DIR`.\n\n"
        "Run from an EarCrate checkout:\n\n"
        "```bash\n"
        "python -m earcrate floor conformance reference.floor-provider.json request.json conformance --repeat 2\n"
        "```\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "ok": True,
        "output_dir": str(destination),
        "provider_path": str(provider_path),
        "manifest_path": str(manifest_path),
        "request_path": str(request_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "request_sha256": request["request_sha256"],
    }


__all__ = ["floor_write_reference_provider"]
