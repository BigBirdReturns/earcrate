from __future__ import annotations

"""Portable Floor crate and standards-facing mappings.

These mappings are intentionally modest. They export Floor evidence into familiar
shapes; they do not claim certification by JAMS, W3C PROV, ODRL, RO-Crate, C2PA,
DDEX, SPDX, or any standards body.
"""

import json
import shutil
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .model import (
    FloorError,
    floor_seal_floor_crate,
    floor_seal_invocation_receipt,
    floor_seal_provider_manifest,
    floor_seal_provider_request,
    floor_seal_provider_result,
    floor_sha256_file,
    floor_sha256_json,
    floor_write_json_atomic,
)


def _floor_jams_mapping(request: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    annotations = []
    for emission in result["emissions"]:
        if emission["kind"] not in {"observation", "candidate", "measurement"}:
            continue
        annotations.append(
            {
                "namespace": f"earcrate_floor.{emission['kind']}",
                "annotation_metadata": {
                    "curator": {"name": result["provider_id"], "email": ""},
                    "version": result["provider_version"],
                    "annotation_tools": "EarCrate Open Music Evidence Floor mapping",
                    "data_source": request["evidence_tier"],
                },
                "data": [
                    {
                        "time": 0.0,
                        "duration": 0.0,
                        "value": deepcopy(emission["payload"]),
                        "confidence": emission["confidence"],
                    }
                ],
                "sandbox": {
                    "emission_id": emission["emission_id"],
                    "subject": emission["subject"],
                    "evidence_refs": emission["evidence_refs"],
                },
            }
        )
    return {
        "file_metadata": {
            "title": str(request.get("metadata", {}).get("title") or ""),
            "identifiers": {"floor_request_sha256": request["request_sha256"]},
            "duration": None,
        },
        "annotations": annotations,
        "sandbox": {
            "mapping": "EarCrate Floor -> JAMS-like annotation document",
            "normative_floor_authority": True,
            "jams_certification_claimed": False,
        },
    }


def _floor_prov_mapping(
    manifest: Mapping[str, Any],
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    entities: dict[str, Any] = {
        f"floor:manifest:{manifest['manifest_sha256']}": {"prov:type": "floor:ProviderManifest"},
        f"floor:request:{request['request_sha256']}": {"prov:type": "floor:ProviderRequest"},
        f"floor:result:{result['result_sha256']}": {"prov:type": "floor:ProviderResult"},
        f"floor:receipt:{receipt['receipt_sha256']}": {"prov:type": "floor:InvocationReceipt"},
    }
    for artifact in request["inputs"]:
        entities[f"floor:artifact:{artifact['sha256']}"] = {
            "prov:type": "floor:InputArtifact",
            "floor:artifactId": artifact["artifact_id"],
            "floor:mediaKind": artifact["media_kind"],
        }
    for artifact in result["artifacts"]:
        entities[f"floor:artifact:{artifact['sha256']}"] = {
            "prov:type": "floor:DerivedArtifact",
            "floor:artifactId": artifact["artifact_id"],
            "floor:mediaKind": artifact["media_kind"],
        }
    activity_id = f"floor:activity:{receipt['receipt_sha256']}"
    return {
        "prefix": {
            "prov": "http://www.w3.org/ns/prov#",
            "floor": "https://earcrate.local/floor#",
        },
        "entity": entities,
        "activity": {
            activity_id: {
                "prov:type": "floor:ProviderInvocation",
                "floor:providerId": manifest["provider_id"],
                "floor:complete": receipt["complete"],
            }
        },
        "wasGeneratedBy": {
            f"_:generated_{index}": {
                "prov:entity": f"floor:artifact:{artifact['sha256']}",
                "prov:activity": activity_id,
            }
            for index, artifact in enumerate(result["artifacts"])
        },
        "used": {
            f"_:used_{index}": {
                "prov:activity": activity_id,
                "prov:entity": f"floor:artifact:{artifact['sha256']}",
            }
            for index, artifact in enumerate(request["inputs"])
        },
        "sandbox": {"w3c_prov_validation_claimed": False},
    }


def _floor_odrl_mapping(request: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    policies = []

    def add_rights(source: str, raw: Mapping[str, Any]) -> None:
        rights = dict(raw or {})
        if not rights:
            return
        policies.append(
            {
                "@type": "Set",
                "uid": f"urn:sha256:{rights.get('rights_envelope_sha256', '')}",
                "target": source,
                "permission": [{"action": action} for action in rights.get("allowed_uses") or []],
                "prohibition": [{"action": action} for action in rights.get("prohibited_uses") or []],
                "duty": deepcopy(list(rights.get("attribution") or [])),
                "floor:assertionStatus": rights.get("assertion_status", "unknown"),
                "floor:providerMayNotDecideLegality": True,
            }
        )

    for emission in result["emissions"]:
        payload = emission.get("payload")
        if isinstance(payload, Mapping):
            add_rights(emission["emission_id"], payload.get("rights") or {})
    return {
        "@context": ["http://www.w3.org/ns/odrl.jsonld", {"floor": "https://earcrate.local/floor#"}],
        "@graph": policies,
        "floor:request": request["request_sha256"],
        "floor:legalDeterminationClaimed": False,
    }


def _floor_ro_crate_mapping(files: list[dict[str, Any]]) -> dict[str, Any]:
    graph: list[dict[str, Any]] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "about": {"@id": "./"},
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": "EarCrate Open Music Evidence Floor crate",
            "hasPart": [{"@id": row["path"]} for row in files if row["path"] != "ro-crate-metadata.json"],
        },
    ]
    for row in files:
        if row["path"] == "ro-crate-metadata.json":
            continue
        graph.append(
            {
                "@id": row["path"],
                "@type": "File",
                "sha256": row["sha256"],
                "contentSize": row["size_bytes"],
            }
        )
    return {"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": graph}


def _floor_copy_derived_artifacts(
    result: Mapping[str, Any],
    *,
    artifact_root: Path,
    destination: Path,
) -> list[str]:
    copied = []
    for artifact in result["artifacts"]:
        relative = PurePosixPath(str(artifact.get("path") or ""))
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise FloorError("cannot copy an unsafe derived artifact path")
        source = artifact_root.joinpath(*relative.parts).resolve()
        try:
            source.relative_to(artifact_root.resolve())
        except ValueError as exc:
            raise FloorError("derived artifact escapes its artifact root") from exc
        if not source.is_file() or source.is_symlink():
            raise FloorError(f"derived artifact is not a regular file: {relative}")
        if floor_sha256_file(source) != artifact["sha256"]:
            raise FloorError(f"derived artifact identity changed before crate export: {relative}")
        target = destination / "derived" / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(target.relative_to(destination).as_posix())
    return copied


def floor_export_crate(
    *,
    manifest_value: Mapping[str, Any],
    request_value: Mapping[str, Any],
    result_value: Mapping[str, Any],
    receipt_value: Mapping[str, Any],
    output_dir: str | Path,
    artifact_root: str | Path | None = None,
    copy_derived: bool = False,
) -> dict[str, Any]:
    manifest = floor_seal_provider_manifest(manifest_value)
    request = floor_seal_provider_request(request_value)
    result = floor_seal_provider_result(result_value, request=request, manifest=manifest)
    receipt = floor_seal_invocation_receipt(receipt_value)
    if receipt["request_sha256"] != request["request_sha256"] or receipt["result_sha256"] != result["result_sha256"]:
        raise FloorError("crate objects do not share one invocation lineage")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FloorError(f"refusing nonempty Floor crate directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    floor_write_json_atomic(destination / "provider.manifest.json", manifest)
    floor_write_json_atomic(destination / "request.json", request)
    floor_write_json_atomic(destination / "result.json", result)
    floor_write_json_atomic(destination / "invocation.receipt.json", receipt)
    floor_write_json_atomic(destination / "annotations.jams.json", _floor_jams_mapping(request, result))
    floor_write_json_atomic(destination / "provenance.prov.json", _floor_prov_mapping(manifest, request, result, receipt))
    floor_write_json_atomic(destination / "rights.odrl.json", _floor_odrl_mapping(request, result))

    copied = []
    if copy_derived:
        if artifact_root is None:
            raise FloorError("copy_derived requires artifact_root")
        copied = _floor_copy_derived_artifacts(
            result,
            artifact_root=Path(artifact_root).expanduser().resolve(),
            destination=destination,
        )

    # RO-Crate needs the final file inventory except for itself, then is included in
    # the later checksum pass.
    current_files = []
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            current_files.append(
                {
                    "path": path.relative_to(destination).as_posix(),
                    "sha256": floor_sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    floor_write_json_atomic(destination / "ro-crate-metadata.json", _floor_ro_crate_mapping(current_files))

    files = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name not in {"checksums.sha256", "floor-crate.json"}:
            files.append(
                {
                    "path": path.relative_to(destination).as_posix(),
                    "sha256": floor_sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    checksum_text = "".join(f"{row['sha256']}  {row['path']}\n" for row in files)
    (destination / "checksums.sha256").write_text(checksum_text, encoding="utf-8", newline="\n")
    checksum_row = {
        "path": "checksums.sha256",
        "sha256": floor_sha256_file(destination / "checksums.sha256"),
        "size_bytes": (destination / "checksums.sha256").stat().st_size,
    }
    crate = {
        "schema_version": 1,
        "kind": "earcrate_floor_crate",
        "provider_manifest_sha256": manifest["manifest_sha256"],
        "request_sha256": request["request_sha256"],
        "result_sha256": result["result_sha256"],
        "invocation_receipt_sha256": receipt["receipt_sha256"],
        "files": [*files, checksum_row],
        "source_media_copied": False,
        "derived_artifacts_copied": copied,
        "standards_mappings": ["JAMS", "W3C PROV", "ODRL", "RO-Crate"],
        "standards_certification_claimed": False,
    }
    crate = floor_seal_floor_crate(crate)
    floor_write_json_atomic(destination / "floor-crate.json", crate)
    return {"ok": True, "output_dir": str(destination), "crate": crate}


__all__ = ["floor_export_crate"]
