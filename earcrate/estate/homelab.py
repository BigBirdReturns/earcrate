from __future__ import annotations

"""MAME-like local commodity audit and acceptance lifecycle.

This module is deliberately non-executing: it inventories the current node,
checks whether a catalog target is feasible, reconciles content-addressed stage
receipts, prepares explicit work, and seals human/adoption decisions. Provider
installation, model download, service calls, source decoding, benchmarking, and
playback remain separate operators that must return evidence artifacts.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from earcrate.estate.discover import redact_estate_inventory, scan_estate
from earcrate.estate.homelab_catalog import _catalog_target, homelab_catalog
from earcrate.estate.homelab_common import (
    HOMELAB_AUDITION_VERDICTS,
    HOMELAB_HASH_FIELDS,
    HOMELAB_DECISIONS,
    HOMELAB_SCHEMA_VERSION,
    HOMELAB_STAGE_STATUSES,
    _is_sha256,
    _now_utc,
    _sha_json,
    homelab_seal,
    homelab_validate_seal,
)
from earcrate.estate.model import (
    default_estate_policy,
    estate_architecture,
    estate_sha256_file,
    estate_validate_seal,
    load_estate_json,
    write_estate_json,
)
from earcrate.estate.plan import propose_estate_plan
from earcrate.estate.rig import capture_rig_capabilities, propose_local_acceptance_campaign

_HOMELAB_KINDS = {
    "earcrate_homelab_catalog",
    "earcrate_homelab_node_receipt",
    "earcrate_homelab_audit",
    "earcrate_homelab_campaign",
    "earcrate_homelab_stage_receipt",
    "earcrate_homelab_audition_ledger",
    "earcrate_homelab_adoption_decision",
    "earcrate_homelab_fixture_binding",
    "earcrate_homelab_review_assignment",
    "earcrate_homelab_private_assignment_authority",
    "earcrate_homelab_review_submission",
}
_AUDITION_STAGES = {"blind_audition", "downstream_audition", "workflow_audition", "regression_audition"}
_TERMINAL_STAGES = {
    "adoption_decision",
    "organ_harvest_decision",
    "disposition_decision",
    "retain_or_replace_decision",
}
_FIXTURE_SENSITIVE_STAGES = {
    "real_fixture",
    "fixture_run",
    "benchmark",
    "blind_audition",
    "downstream_audition",
    "workflow_audition",
    "interoperability_trial",
    "live_request",
    "fixture_trial",
    "roundtrip",
    "external_validation",
    "regression_audition",
}
_ARTIFACT_REQUIRED_STAGES = {
    "asset_audit",
    "credential_audit",
    "local_identity_audit",
    "install_review",
    "source_review",
    "implementation_audit",
    "mapping",
    "load",
    "fixture_run",
    "real_fixture",
    "benchmark",
    "custody_review",
    "live_request",
    "fixture_trial",
    "interoperability_trial",
    "roundtrip",
    "external_validation",
}


def _package_map(rig: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, version in dict(rig.get("python_packages") or {}).items():
        if version:
            out[str(name).casefold()] = str(version)
    return out


def _executable_map(rig: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rig.get("executables") or []:
        name = str(row.get("name") or "").casefold()
        if not name:
            continue
        receipt = {
            "name": str(row.get("name") or ""),
            "available": bool(row.get("available")),
            "path": row.get("path"),
            "version": row.get("version"),
            "returncode": row.get("returncode"),
            "timed_out": bool(row.get("timed_out")),
            "bytes": None,
            "mtime_ns": None,
            "raw_sha256": None,
            "identity_status": "unavailable" if not row.get("available") else "unhashed",
        }
        raw_path = str(row.get("path") or "")
        if row.get("available") and raw_path:
            path = Path(raw_path).expanduser()
            try:
                if path.is_symlink():
                    receipt["identity_status"] = "symlink_refused"
                elif path.is_file():
                    stat = path.stat()
                    receipt["bytes"] = int(stat.st_size)
                    receipt["mtime_ns"] = int(stat.st_mtime_ns)
                    if int(stat.st_size) <= 1024 * 1024 * 1024:
                        receipt["raw_sha256"] = estate_sha256_file(path)
                        receipt["identity_status"] = "strong"
                    else:
                        receipt["identity_status"] = "skipped_size_limit"
                else:
                    receipt["identity_status"] = "not_regular_file"
            except Exception as exc:
                receipt["identity_status"] = "error"
                receipt["identity_error"] = f"{type(exc).__name__}: {exc}"[:500]
        out[name] = receipt
    return out


def capture_homelab_node(
    rig: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a non-executing estate rig receipt into a Homelab node receipt."""
    estate_validate_seal(rig)
    if rig.get("kind") != "earcrate_rig_capability_receipt":
        raise ValueError("homelab node requires an estate rig capability receipt")
    active_catalog = dict(catalog or homelab_catalog())
    homelab_validate_seal(active_catalog)
    if active_catalog.get("kind") != "earcrate_homelab_catalog":
        raise ValueError("not a homelab catalog")

    packages = _package_map(rig)
    executables = _executable_map(rig)
    host = dict(rig.get("host") or {})
    node_seed = {
        "rig_sha256": rig["rig_sha256"],
        "hostname_sha256": host.get("hostname_sha256"),
        "system": host.get("system"),
        "machine": host.get("machine"),
        "python_executable": host.get("python_executable"),
    }
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_node_receipt",
        "captured_at": _now_utc(),
        "catalog_sha256": active_catalog["catalog_sha256"],
        "rig_sha256": rig["rig_sha256"],
        "node_id": "node_" + _sha_json(node_seed)[:24],
        "host": host,
        "roots": deepcopy(list(rig.get("roots") or [])),
        "nvidia": deepcopy(dict(rig.get("nvidia") or {})),
        "audio_devices": deepcopy(dict(rig.get("audio_devices") or {})),
        "python_distributions": packages,
        "executables": executables,
        "credential_environment_names": sorted(
            str(name) for name in (rig.get("environment_declarations") or {}).get("names_present") or []
        ),
        "boundary": {
            "provider_process_executed": False,
            "model_loaded": False,
            "network_request_made": False,
            "source_audio_decoded": False,
            "capability_is_not_quality_acceptance": True,
            "credential_values_recorded": False,
        },
    }
    return homelab_seal(payload)


def _refuse_fixture_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked fixture path refused: {current}")


def bind_homelab_fixture(
    catalog: Mapping[str, Any],
    *,
    fixture_id: str,
    artifact_path: str | Path,
    bound_by: str,
    reason: str,
    decoded_pcm_sha256: str | None = None,
    media_kind: str | None = None,
) -> dict[str, Any]:
    """Bind one exact local file to a catalog fixture without copying its bytes."""
    homelab_validate_seal(catalog)
    if catalog.get("kind") != "earcrate_homelab_catalog":
        raise ValueError("fixture binding requires a HomelabCatalog")
    fixture = next((dict(row) for row in catalog.get("fixtures") or [] if row.get("fixture_id") == fixture_id), None)
    if fixture is None:
        raise ValueError(f"unknown homelab fixture: {fixture_id}")
    if not str(bound_by).strip() or not str(reason).strip():
        raise ValueError("bound_by and reason are required")
    decoded = str(decoded_pcm_sha256 or "").lower()
    if decoded and not _is_sha256(decoded):
        raise ValueError("decoded_pcm_sha256 must be a SHA-256 identity")

    source = Path(artifact_path).expanduser().absolute()
    _refuse_fixture_symlink_components(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("fixture artifact must be a regular non-symlink file")
    before = source.stat()
    artifact_sha = estate_sha256_file(source)
    after = source.stat()
    if int(before.st_size) != int(after.st_size) or int(before.st_mtime_ns) != int(after.st_mtime_ns):
        raise ValueError("fixture artifact changed while it was being hashed")

    expected = str(fixture.get("expected_sha256") or "").lower()
    expected_pcm = str(fixture.get("decoded_pcm_sha256") or "").lower()
    rule = str(fixture.get("availability_rule") or "")
    exact_container = _is_sha256(expected) and artifact_sha == expected
    exact_pcm = _is_sha256(expected_pcm) and decoded == expected_pcm
    if rule == "exact_hash_required" and not exact_container:
        raise ValueError("fixture artifact does not match the catalog's exact expected SHA-256")
    if rule == "external_pack_or_pcm_identity_required" and not (exact_container or exact_pcm):
        raise ValueError("fixture binding must match the catalog pack or decoded PCM identity")
    if _is_sha256(expected) and rule not in {"external_pack_or_pcm_identity_required"} and not exact_container:
        raise ValueError("fixture artifact does not match the catalog's expected SHA-256")

    guessed_kind = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_fixture_binding",
            "bound_at": _now_utc(),
            "catalog_sha256": catalog["catalog_sha256"],
            "fixture_id": fixture_id,
            "evidence_tier": fixture.get("evidence_tier"),
            "artifact_path": str(source),
            "artifact_sha256": artifact_sha,
            "artifact_bytes": int(after.st_size),
            "artifact_mtime_ns": int(after.st_mtime_ns),
            "decoded_pcm_sha256": decoded or None,
            "media_kind": str(media_kind or guessed_kind),
            "bound_by": str(bound_by).strip(),
            "reason": str(reason).strip(),
            "boundary": {
                "source_bytes_copied": False,
                "binding_is_not_provider_acceptance": True,
                "local_path_is_sensitive_and_must_be_redacted_for_public_export": True,
            },
        }
    )


def _inventory_items(inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in inventory.get("items") or []]


def _inventory_strong_sha_index(inventory: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index only hashes computed from bytes that were actually present."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in _inventory_items(inventory):
        raw = str(item.get("raw_sha256") or "").lower()
        status = str(item.get("hash_status") or "")
        if _is_sha256(raw) and status.startswith("strong") and item.get("file_type") == "file":
            out[raw].append(
                {
                    "item_id": str(item.get("item_id") or ""),
                    "absolute_path": str(item.get("absolute_path") or ""),
                    "bytes": int(item.get("bytes") or 0),
                }
            )
    return {key: sorted(values, key=lambda row: row["item_id"]) for key, values in out.items()}


def _inventory_declared_sha_index(inventory: Mapping[str, Any]) -> dict[str, list[str]]:
    """Retain declared identities for diagnostics; they never establish custody."""
    out: dict[str, list[str]] = defaultdict(list)
    for item in _inventory_items(inventory):
        item_id = str(item.get("item_id") or "")
        for row in (item.get("metadata") or {}).get("declared_sha256") or []:
            digest = str((row or {}).get("sha256") or "").lower()
            if _is_sha256(digest):
                out[digest].append(item_id)
    return {key: sorted(set(values)) for key, values in out.items()}


def _verified_inventory_rows(
    digest: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_bytes: int | None = None,
) -> tuple[list[str], list[str]]:
    verified: list[str] = []
    problems: list[str] = []
    for row in rows:
        item_id = str(row.get("item_id") or "")
        path_text = str(row.get("absolute_path") or "")
        if not path_text:
            problems.append(f"{item_id or 'inventory item'} has no local path")
            continue
        try:
            path = Path(path_text).expanduser().absolute()
            _refuse_fixture_symlink_components(path)
            if path.is_symlink() or not path.is_file():
                problems.append(f"{item_id or path_text} is missing or unsafe")
                continue
            before = path.stat()
            if expected_bytes is not None and int(before.st_size) != int(expected_bytes):
                problems.append(f"{item_id or path_text} size changed")
                continue
            if estate_sha256_file(path) != digest:
                problems.append(f"{item_id or path_text} bytes changed")
                continue
            after = path.stat()
            if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(before.st_mtime_ns):
                problems.append(f"{item_id or path_text} changed during verification")
                continue
            verified.append(item_id)
        except Exception as exc:
            problems.append(f"{item_id or path_text}: {type(exc).__name__}: {exc}"[:500])
    return sorted(set(verified)), sorted(set(problems))


def _verify_fixture_binding(
    binding: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    fixture: Mapping[str, Any],
    strong_index: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[bool, str, list[str]]:
    if binding.get("kind") != "earcrate_homelab_fixture_binding":
        return False, "object is not a fixture binding", []
    if binding.get("catalog_sha256") != catalog.get("catalog_sha256"):
        return False, "binding belongs to another catalog revision", []
    if binding.get("fixture_id") != fixture.get("fixture_id"):
        return False, "binding names another fixture", []
    artifact_sha = str(binding.get("artifact_sha256") or "").lower()
    if not _is_sha256(artifact_sha):
        return False, "binding has no valid artifact SHA-256", []
    expected_bytes = int(binding.get("artifact_bytes") or -1)
    evidence: list[str] = []

    path_text = str(binding.get("artifact_path") or "")
    path_verified = False
    path_problem: str | None = None
    if path_text:
        try:
            path = Path(path_text).expanduser().absolute()
            _refuse_fixture_symlink_components(path)
            if path.is_symlink() or not path.is_file():
                path_problem = "bound fixture path is missing or unsafe"
            else:
                before = path.stat()
                if int(before.st_size) != expected_bytes:
                    path_problem = "bound fixture size changed"
                elif estate_sha256_file(path) != artifact_sha:
                    path_problem = "bound fixture bytes changed"
                else:
                    after = path.stat()
                    if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(before.st_mtime_ns):
                        path_problem = "bound fixture changed during verification"
                    else:
                        path_verified = True
        except Exception as exc:
            path_problem = f"fixture path verification failed: {type(exc).__name__}: {exc}"[:500]

    candidate_rows = [row for row in strong_index.get(artifact_sha) or [] if int(row.get("bytes") or -1) == expected_bytes]
    verified_rows, row_problems = _verified_inventory_rows(
        artifact_sha, candidate_rows, expected_bytes=expected_bytes
    )
    evidence.extend(verified_rows)
    if not path_verified and not verified_rows:
        details = [value for value in [path_problem, *row_problems] if value]
        return False, "; ".join(details) or "binding artifact is not present as strongly verified bytes", []

    expected = str(fixture.get("expected_sha256") or "").lower()
    expected_pcm = str(fixture.get("decoded_pcm_sha256") or "").lower()
    decoded = str(binding.get("decoded_pcm_sha256") or "").lower()
    rule = str(fixture.get("availability_rule") or "")
    container_match = _is_sha256(expected) and artifact_sha == expected
    pcm_match = _is_sha256(expected_pcm) and decoded == expected_pcm
    if rule == "exact_hash_required" and not container_match:
        return False, "binding does not match the catalog's exact artifact identity", evidence
    if rule == "external_pack_or_pcm_identity_required" and not (container_match or pcm_match):
        return False, "binding matches neither the catalog pack nor decoded PCM identity", evidence
    if _is_sha256(expected) and rule != "external_pack_or_pcm_identity_required" and not container_match:
        return False, "binding does not match the catalog's expected artifact identity", evidence
    if path_verified:
        reason = "exact fixture binding verified against the bound local bytes"
    else:
        reason = "exact fixture binding verified through another current inventory location"
        if path_problem:
            reason += f"; original binding path is stale: {path_problem}"
    return True, reason, sorted(set(evidence))


def _fixture_status(
    catalog: Mapping[str, Any],
    inventory: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, dict[str, Any]]:
    items = _inventory_items(inventory)
    classes = Counter(str(item.get("classification") or "") for item in items)
    root_roles = Counter(str(root.get("role") or "") for root in inventory.get("roots") or [])
    strong_index = _inventory_strong_sha_index(inventory)
    declared_index = _inventory_declared_sha_index(inventory)
    any_audio_device = any(bool((node.get("audio_devices") or {}).get("available")) for node in nodes)
    bindings_by_fixture: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for binding in bindings:
        bindings_by_fixture[str(binding.get("fixture_id") or "")].append(binding)

    result: dict[str, dict[str, Any]] = {}
    for fixture in catalog.get("fixtures") or []:
        fixture_id = str(fixture["fixture_id"])
        rule = str(fixture.get("availability_rule") or "")
        expected = str(fixture.get("expected_sha256") or "").lower()
        decoded = str(fixture.get("decoded_pcm_sha256") or "").lower()
        evidence: list[str] = []
        binding_ids: list[str] = []
        invalid_bindings: list[str] = []
        invalid_strong_rows: list[str] = []
        available = False
        reason = "fixture not found"

        for binding in bindings_by_fixture.get(fixture_id) or []:
            valid, binding_reason, binding_evidence = _verify_fixture_binding(
                binding, catalog=catalog, fixture=fixture, strong_index=strong_index
            )
            if valid:
                available = True
                reason = binding_reason
                evidence.extend(binding_evidence)
                binding_ids.append(str(binding.get("binding_sha256") or ""))
            else:
                invalid_bindings.append(binding_reason)

        if not available and rule == "always_generated_by_tests":
            available = True
            reason = "synthetic fixture is generated by the executable gates"
        elif not available and _is_sha256(expected) and expected in strong_index:
            verified_rows, invalid_strong_rows = _verified_inventory_rows(expected, strong_index[expected])
            if verified_rows:
                available = True
                evidence.extend(verified_rows)
                reason = "inventory contains the exact artifact bytes and they were reverified during audit"
            else:
                reason = "inventory declared a prior strong hash, but the current bytes no longer verify"
        elif not available and rule == "inventory_contains_source_audio_and_workspace_policy":
            available = bool(classes.get("source_audio") and (root_roles.get("workspace") or classes.get("workspace_config") or classes.get("database")))
            reason = "source audio and workspace policy are present" if available else "requires source audio plus a workspace/policy authority"
        elif not available and rule == "inventory_contains_project_index_and_revision":
            available = bool(classes.get("project_index") and classes.get("project_revision"))
            reason = "project index and immutable revision are present" if available else "requires a project index and immutable revision"
        elif not available and rule == "node_receipt_contains_output_device":
            available = any_audio_device
            reason = "a node reports a physical audio device" if available else "no node reports a physical audio device"
        elif not available and (_is_sha256(expected) and expected in declared_index):
            reason = "the expected identity is only declared by metadata; the exact bytes are not strongly verified"
        elif not available and rule in {"user_supplied_exact_recording", "external_pack_or_pcm_identity_required"}:
            reason = "requires a sealed fixture binding whose exact bytes are present and verified"

        result[fixture_id] = {
            "fixture_id": fixture_id,
            "available": bool(available),
            "reason": reason,
            "evidence_item_ids": sorted(set(evidence)),
            "binding_sha256s": sorted(set(value for value in binding_ids if _is_sha256(value))),
            "invalid_binding_reasons": sorted(set(invalid_bindings)),
            "invalid_strong_identity_reasons": sorted(set(invalid_strong_rows)),
            "declared_identity_item_ids": sorted(set(declared_index.get(expected) or [])) if _is_sha256(expected) else [],
            "expected_sha256": expected or None,
            "decoded_pcm_sha256": decoded or None,
        }
    return result


def _asset_token_matches(inventory: Mapping[str, Any], token: str) -> list[str]:
    needle = str(token).casefold()
    matches: list[str] = []
    for item in _inventory_items(inventory):
        haystack = " ".join(
            (
                str(item.get("relative_path") or ""),
                str(item.get("classification") or ""),
                json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
            )
        ).casefold()
        if needle and needle in haystack:
            matches.append(str(item.get("item_id") or ""))
    return sorted(set(matches))


def _node_blockers(
    target: Mapping[str, Any],
    node: Mapping[str, Any],
    inventory: Mapping[str, Any],
    fixture_status: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str], dict[str, Any]]:
    req = dict(target.get("requirements") or {})
    packages = {str(name).casefold(): str(version) for name, version in dict(node.get("python_distributions") or {}).items() if version}
    executables = {str(name).casefold(): dict(value or {}) for name, value in dict(node.get("executables") or {}).items()}
    env_names = {str(name) for name in node.get("credential_environment_names") or []}
    blockers: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"packages": {}, "executables": {}, "assets": {}, "fixtures": {}}

    any_packages = [str(name).casefold() for name in req.get("python_any") or []]
    if any_packages:
        present = {name: packages.get(name) for name in any_packages if packages.get(name)}
        evidence["packages"].update(present)
        if not present:
            blockers.append("missing any Python distribution: " + ", ".join(any_packages))
    for name in [str(value).casefold() for value in req.get("python_all") or []]:
        if packages.get(name):
            evidence["packages"][name] = packages[name]
        else:
            blockers.append(f"missing Python distribution: {name}")

    any_exec = [str(name).casefold() for name in req.get("executables_any") or []]
    if any_exec:
        present = {name: executables[name] for name in any_exec if executables.get(name, {}).get("available")}
        evidence["executables"].update(present)
        if not present:
            blockers.append("missing any executable: " + ", ".join(any_exec))
    for name in [str(value).casefold() for value in req.get("executables_all") or []]:
        row = executables.get(name) or {}
        if row.get("available"):
            evidence["executables"][name] = row
            if row.get("identity_status") != "strong":
                warnings.append(f"executable {name} is available but lacks a strong identity")
        else:
            blockers.append(f"missing executable: {name}")

    for token in [str(value) for value in req.get("asset_tokens") or []]:
        matches = _asset_token_matches(inventory, token)
        evidence["assets"][token] = matches
        if not matches:
            blockers.append(f"missing model/provider asset token: {token}")

    for fixture_id in [str(value) for value in req.get("fixture_ids") or []]:
        row = dict(fixture_status.get(fixture_id) or {})
        evidence["fixtures"][fixture_id] = row
        if not row.get("available"):
            blockers.append(f"missing fixture {fixture_id}: {row.get('reason') or 'unavailable'}")

    for name in [str(value) for value in req.get("credentials_all") or []]:
        if name not in env_names:
            blockers.append(f"credential declaration absent: {name}")

    gpu = str(req.get("gpu") or "none")
    gpu_count = len((node.get("nvidia") or {}).get("gpus") or [])
    if gpu == "required" and not gpu_count:
        blockers.append("required NVIDIA/CUDA node is unavailable")
    elif gpu == "preferred" and not gpu_count:
        warnings.append("GPU preferred but this node has no NVIDIA device")

    if req.get("audio_device") and not (node.get("audio_devices") or {}).get("available"):
        blockers.append("physical audio device inventory is unavailable")
    if str(req.get("network") or "none") != "none":
        warnings.append("network/service availability is declared but not probed by the Homelab audit")
    if req.get("manual_probe"):
        warnings.append("target requires a manual workflow or source review")
    return blockers, warnings, evidence


def _load_existing_objects(inventory: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    objects: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in _inventory_items(inventory):
        metadata = dict(item.get("metadata") or {})
        kind = str(metadata.get("kind") or "")
        if kind not in _HOMELAB_KINDS:
            continue
        path = str(item.get("absolute_path") or "")
        if not path:
            warnings.append({"item_id": item.get("item_id"), "reason": "homelab object has no local path"})
            continue
        try:
            value = load_estate_json(path)
            homelab_validate_seal(value)
            objects.append(value)
        except Exception as exc:
            warnings.append({
                "item_id": item.get("item_id"),
                "path": path,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            })
    return objects, warnings


def _object_identity(value: Mapping[str, Any]) -> str:
    field = HOMELAB_HASH_FIELDS.get(str(value.get("kind") or ""))
    digest = str(value.get(field or "") or "")
    if not field or not _is_sha256(digest):
        raise ValueError(f"Homelab object has no valid semantic identity: {value.get('kind')!r}")
    return digest.lower()


def _latest_by_stage(objects: Iterable[Mapping[str, Any]], *, target_id: str, node_sha256: str) -> dict[str, dict[str, Any]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in objects:
        if value.get("target_id") != target_id or value.get("node_sha256") != node_sha256:
            continue
        stage = str(value.get("stage") or "")
        if stage:
            candidates[stage].append(dict(value))
    out: dict[str, dict[str, Any]] = {}
    for stage, rows in candidates.items():
        rows.sort(key=lambda row: (str(row.get("recorded_at") or row.get("reviewed_at") or ""), _object_identity(row)))
        out[stage] = rows[-1]
    return out


def _decision_for_target(
    objects: Iterable[Mapping[str, Any]],
    *,
    target_id: str,
    catalog_sha256: str,
    manifest_sha256: str,
    node_sha256: str,
) -> dict[str, Any] | None:
    """Return the latest decision for this exact target, manifest, and node."""
    rows = [
        dict(value)
        for value in objects
        if value.get("kind") == "earcrate_homelab_adoption_decision"
        and value.get("target_id") == target_id
        and value.get("catalog_sha256") == catalog_sha256
        and value.get("target_manifest_sha256") == manifest_sha256
        and value.get("assigned_node_sha256") == node_sha256
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: (str(row.get("decided_at") or ""), _object_identity(row)))
    return rows[-1]


def _validate_current_decision(
    decision: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[bool, str]:
    """Refuse a terminal decision whose node or accepted evidence is stale."""
    node_sha256 = str((selected.get("node") or {}).get("node_sha256") or "")
    if decision.get("assigned_node_sha256") != node_sha256:
        return False, "terminal decision belongs to another Homelab node"

    disposition = str(decision.get("decision") or "")
    if disposition != "accepted":
        return True, "current node-scoped terminal decision"

    if selected.get("blockers"):
        return False, "accepted decision is stale because current feasibility is blocked"
    if selected.get("failed_stages") or selected.get("refused_stages"):
        return False, "accepted decision is stale because a current stage failed or refused"

    terminal_stage = str((target.get("required_stages") or [""])[-1])
    required_nonterminal = {
        str(stage)
        for stage in target.get("required_stages") or []
        if str(stage) != terminal_stage
    }
    completed = {str(stage) for stage in selected.get("completed_stages") or []}
    missing = sorted(required_nonterminal - completed)
    if missing:
        return False, "accepted decision is stale because current stages are missing: " + ", ".join(missing)
    if target.get("audition_required") and not selected.get("audition_acceptance_present"):
        return False, "accepted decision is stale because its current audition is not accepted"

    current_evidence = {
        str(identity)
        for stage, identity in dict(selected.get("stage_evidence") or {}).items()
        if str(stage) in required_nonterminal and _is_sha256(str(identity))
    }
    supporting = {
        str(identity)
        for identity in decision.get("supporting_receipt_sha256s") or []
        if _is_sha256(str(identity))
    }
    if not current_evidence.issubset(supporting):
        return False, "accepted decision does not bind every current stage receipt"

    scope = dict(decision.get("scope") or {})
    if {str(stage) for stage in scope.get("completed_stages") or []} != completed:
        return False, "accepted decision completed-stage scope no longer matches the audit"
    required_fixtures = {
        str(value)
        for value in (target.get("requirements") or {}).get("fixture_ids") or []
    }
    if {str(value) for value in scope.get("fixture_ids") or []} != required_fixtures:
        return False, "accepted decision fixture scope no longer matches the target manifest"
    return True, "current accepted decision"


def _stage_fixture_coverage(value: Mapping[str, Any], required: Sequence[str]) -> bool:
    if not required:
        return True
    covered = {str(item) for item in value.get("fixture_ids") or []}
    return set(str(item) for item in required).issubset(covered)


def _validate_blind_audition_evidence(
    catalog: Mapping[str, Any],
    ledger: Mapping[str, Any],
    object_index: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, str]:
    refs = {
        "assignment": str(ledger.get("assignment_sha256") or ""),
        "authority": str(ledger.get("private_authority_sha256") or ""),
        "submission": str(ledger.get("submission_sha256") or ""),
    }
    if any(not _is_sha256(value) for value in refs.values()):
        return False, "blind audition ledger has no complete adjudication references"
    missing = [name for name, identity in refs.items() if identity not in object_index]
    if missing:
        return False, "blind audition source objects are missing: " + ", ".join(missing)
    try:
        from earcrate.estate.homelab_review import adjudicate_review

        recomputed = adjudicate_review(
            catalog,
            object_index[refs["assignment"]],
            object_index[refs["authority"]],
            object_index[refs["submission"]],
        )
    except Exception as exc:
        return False, f"blind audition adjudication failed: {type(exc).__name__}: {exc}"[:500]
    if recomputed.get("ledger_sha256") != ledger.get("ledger_sha256"):
        return False, "blind audition ledger does not match recomputed adjudication"
    return True, "blind audition source chain verified"


def audit_homelab(
    inventory: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit feasibility and current receipts without executing any target."""
    estate_validate_seal(inventory)
    if inventory.get("kind") != "earcrate_estate_inventory":
        raise ValueError("homelab audit requires a full estate inventory")
    active_catalog = dict(catalog or homelab_catalog())
    homelab_validate_seal(active_catalog)
    if active_catalog.get("kind") != "earcrate_homelab_catalog":
        raise ValueError("not a homelab catalog")
    if not nodes:
        raise ValueError("at least one homelab node is required")
    normalized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        value = dict(node)
        homelab_validate_seal(value)
        if value.get("kind") != "earcrate_homelab_node_receipt":
            raise ValueError("audit node is not a HomelabNodeReceipt")
        if value.get("catalog_sha256") != active_catalog["catalog_sha256"]:
            raise ValueError("homelab node belongs to another catalog revision")
        normalized_nodes.append(value)

    objects, object_warnings = _load_existing_objects(inventory)
    fixture_bindings = [value for value in objects if value.get("kind") == "earcrate_homelab_fixture_binding"]
    fixtures = _fixture_status(active_catalog, inventory, normalized_nodes, fixture_bindings)
    all_object_index = {_object_identity(value): value for value in objects}
    current_objects = [
        value for value in objects
        if value.get("kind") in {"earcrate_homelab_stage_receipt", "earcrate_homelab_audition_ledger", "earcrate_homelab_adoption_decision"}
    ]
    evidence_index = {
        _object_identity(value): {
            "kind": value.get("kind"),
            "target_id": value.get("target_id"),
            "stage": value.get("stage"),
            "status": value.get("status") or value.get("verdict") or value.get("decision"),
            "catalog_sha256": value.get("catalog_sha256"),
            "target_manifest_sha256": value.get("target_manifest_sha256"),
            "node_sha256": value.get("node_sha256") or value.get("assigned_node_sha256"),
        }
        for value in current_objects
    }

    target_rows: list[dict[str, Any]] = []
    for target in active_catalog.get("targets") or []:
        target_id = str(target["target_id"])
        manifest = str(target["target_manifest_sha256"])
        required_fixtures = [str(value) for value in (target.get("requirements") or {}).get("fixture_ids") or []]
        choices: list[dict[str, Any]] = []
        for node in normalized_nodes:
            blockers, warnings, feasibility_evidence = _node_blockers(target, node, inventory, fixtures)
            latest = _latest_by_stage(current_objects, target_id=target_id, node_sha256=str(node["node_sha256"]))
            completed: list[str] = []
            failed: list[str] = []
            refused: list[str] = []
            stage_evidence: dict[str, str] = {}
            audition_acceptance = False
            for stage, value in latest.items():
                if value.get("catalog_sha256") != active_catalog["catalog_sha256"] or value.get("target_manifest_sha256") != manifest:
                    continue
                if stage in _FIXTURE_SENSITIVE_STAGES and not _stage_fixture_coverage(value, required_fixtures):
                    warnings.append(f"stage {stage} exists but does not cover every current fixture")
                    continue
                identity = _object_identity(value)
                stage_evidence[stage] = identity
                if value.get("kind") == "earcrate_homelab_audition_ledger":
                    verdict = str(value.get("verdict") or "")
                    blind_ok = True
                    if stage == "blind_audition":
                        blind_ok, blind_reason = _validate_blind_audition_evidence(
                            active_catalog, value, all_object_index
                        )
                        if not blind_ok:
                            warnings.append(blind_reason)
                    if verdict == "accept" and blind_ok:
                        completed.append(stage)
                        audition_acceptance = True
                    elif verdict in {"reject", "revise"}:
                        failed.append(stage)
                    else:
                        refused.append(stage)
                else:
                    status = str(value.get("status") or "")
                    if status == "passed":
                        completed.append(stage)
                    elif status == "failed":
                        failed.append(stage)
                    elif status == "refused":
                        refused.append(stage)
            score = (
                len(blockers),
                -len(completed),
                len(failed),
                len(refused),
                str(node.get("node_id") or ""),
            )
            choices.append({
                "score": score,
                "node": node,
                "blockers": sorted(set(blockers)),
                "warnings": sorted(set(warnings)),
                "feasibility_evidence": feasibility_evidence,
                "completed_stages": sorted(set(completed)),
                "failed_stages": sorted(set(failed)),
                "refused_stages": sorted(set(refused)),
                "stage_evidence": stage_evidence,
                "audition_acceptance_present": audition_acceptance,
            })
        selected = sorted(choices, key=lambda row: row["score"])[0]
        decision = _decision_for_target(
            current_objects,
            target_id=target_id,
            catalog_sha256=str(active_catalog["catalog_sha256"]),
            manifest_sha256=manifest,
            node_sha256=str(selected["node"]["node_sha256"]),
        )
        if decision:
            decision_current, decision_reason = _validate_current_decision(
                decision,
                target=target,
                selected=selected,
            )
            if not decision_current:
                selected["warnings"] = sorted(set([*selected["warnings"], decision_reason]))
                decision = None
        completed = set(selected["completed_stages"])
        terminal_stage = str(target["required_stages"][-1])
        if decision:
            completed.add(terminal_stage)
        missing = [stage for stage in target["required_stages"] if stage not in completed]
        terminal = str(decision.get("decision")) if decision else None
        if terminal:
            lifecycle = terminal
        elif selected["blockers"]:
            lifecycle = "blocked_feasibility"
        elif selected["failed_stages"]:
            lifecycle = "failed_retest_or_decision_required"
        elif missing:
            lifecycle = "awaiting_" + str(missing[0])
        else:
            lifecycle = "awaiting_terminal_decision"
        target_rows.append({
            "target_id": target_id,
            "display_name": target.get("display_name"),
            "target_class": target.get("target_class"),
            "target_manifest_sha256": manifest,
            "assigned_node_id": selected["node"]["node_id"],
            "assigned_node_sha256": selected["node"]["node_sha256"],
            "feasibility": "blocked" if selected["blockers"] else "ready",
            "blockers": selected["blockers"],
            "warnings": selected["warnings"],
            "feasibility_evidence": selected["feasibility_evidence"],
            "completed_stages": sorted(completed),
            "failed_stages": selected["failed_stages"],
            "refused_stages": selected["refused_stages"],
            "missing_stages": missing,
            "stage_evidence": selected["stage_evidence"],
            "audition_required": bool(target.get("audition_required")),
            "audition_acceptance_present": bool(selected["audition_acceptance_present"]),
            "terminal_decision": terminal,
            "decision_sha256": _object_identity(decision) if decision else None,
            "lifecycle": lifecycle,
        })

    lifecycle_counts = Counter(str(row["lifecycle"]) for row in target_rows)
    terminal_count = sum(1 for row in target_rows if row.get("terminal_decision"))
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_audit",
        "audited_at": _now_utc(),
        "catalog_sha256": active_catalog["catalog_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "node_sha256s": sorted(str(node["node_sha256"]) for node in normalized_nodes),
        "fixture_status": fixtures,
        "targets": target_rows,
        "evidence_index": evidence_index,
        "object_warnings": object_warnings,
        "summary": {
            "targets": len(target_rows),
            "feasible": sum(1 for row in target_rows if row["feasibility"] == "ready"),
            "blocked_feasibility": sum(1 for row in target_rows if row["feasibility"] == "blocked"),
            "terminal_decisions": terminal_count,
            "unresolved_targets": len(target_rows) - terminal_count,
            "audition_required": sum(1 for row in target_rows if row["audition_required"]),
            "accepted_auditions": sum(1 for row in target_rows if row["audition_acceptance_present"]),
            "lifecycles": dict(sorted(lifecycle_counts.items())),
        },
        "boundary": {
            "provider_processes_executed": False,
            "models_loaded": False,
            "services_invoked": False,
            "source_audio_decoded": False,
            "feasibility_alone_can_complete_campaign": False,
            "running_without_receipts_can_complete_campaign": False,
        },
    }
    return homelab_seal(payload)


def propose_homelab_campaign(
    audit: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    homelab_validate_seal(audit)
    if audit.get("kind") != "earcrate_homelab_audit":
        raise ValueError("campaign requires a homelab audit")
    active_catalog = dict(catalog or homelab_catalog())
    homelab_validate_seal(active_catalog)
    if audit.get("catalog_sha256") != active_catalog["catalog_sha256"]:
        raise ValueError("audit belongs to another catalog revision")

    tasks: list[dict[str, Any]] = []
    for row in audit.get("targets") or []:
        target = _catalog_target(active_catalog, str(row["target_id"]))
        dependencies: list[str] = []
        for index, blocker in enumerate(row.get("blockers") or []):
            task_id = f"{target['target_id']}.prerequisite.{index:02d}"
            tasks.append({
                "task_id": task_id,
                "target_id": target["target_id"],
                "task_type": "prerequisite",
                "status": "blocked",
                "assigned_node_sha256": row["assigned_node_sha256"],
                "resource": "operator",
                "reason": blocker,
                "depends_on": [],
                "required_output_kinds": ["earcrate_homelab_stage_receipt"],
            })
            dependencies.append(task_id)
        prior_stage_task: str | None = None
        for stage in row.get("missing_stages") or []:
            task_id = f"{target['target_id']}.stage.{stage}"
            stage_dependencies = list(dependencies)
            if prior_stage_task:
                stage_dependencies.append(prior_stage_task)
            resource = "human+playback" if stage in _AUDITION_STAGES else (
                "authority" if stage in _TERMINAL_STAGES else (
                    "gpu-or-cpu" if str((target.get("requirements") or {}).get("gpu") or "none") != "none" else "cpu"
                )
            )
            tasks.append({
                "task_id": task_id,
                "target_id": target["target_id"],
                "task_type": "terminal_decision" if stage in _TERMINAL_STAGES else ("audition" if stage in _AUDITION_STAGES else "stage"),
                "stage": stage,
                "status": "blocked" if row.get("blockers") else "ready",
                "assigned_node_sha256": row["assigned_node_sha256"],
                "resource": resource,
                "reason": "required by the current target manifest",
                "depends_on": stage_dependencies,
                "required_fixture_ids": list((target.get("requirements") or {}).get("fixture_ids") or []),
                "required_output_kinds": [
                    "earcrate_homelab_adoption_decision" if stage in _TERMINAL_STAGES else (
                        "earcrate_homelab_audition_ledger" if stage in _AUDITION_STAGES else "earcrate_homelab_stage_receipt"
                    )
                ],
            })
            prior_stage_task = task_id

    status_counts = Counter(str(task["status"]) for task in tasks)
    unresolved = int((audit.get("summary") or {}).get("unresolved_targets") or 0)
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_campaign",
        "created_at": _now_utc(),
        "audit_sha256": audit["audit_sha256"],
        "catalog_sha256": active_catalog["catalog_sha256"],
        "tasks": tasks,
        "summary": {
            "tasks": len(tasks),
            "statuses": dict(sorted(status_counts.items())),
            "unresolved_targets": unresolved,
            "terminal_targets": int((audit.get("summary") or {}).get("terminal_decisions") or 0),
        },
        "completion_gate": {
            "passed": unresolved == 0,
            "every_target_has_terminal_decision": unresolved == 0,
            "accepted_targets_require_all_current_stages": True,
            "accepted_audio_targets_require_human_audition": True,
            "failed_and_deferred_targets_remain_visible": True,
            "feasibility_alone_can_complete_campaign": False,
            "running_without_receipts_can_complete_campaign": False,
        },
    }
    return homelab_seal(payload)


def _validate_catalog_and_target(catalog: Mapping[str, Any], target_id: str) -> dict[str, Any]:
    homelab_validate_seal(catalog)
    if catalog.get("kind") != "earcrate_homelab_catalog":
        raise ValueError("not a homelab catalog")
    return _catalog_target(catalog, target_id)


def _validate_fixture_ids(catalog: Mapping[str, Any], target: Mapping[str, Any], fixture_ids: Sequence[str]) -> list[str]:
    valid_catalog = {str(row["fixture_id"]) for row in catalog.get("fixtures") or []}
    required = {str(value) for value in (target.get("requirements") or {}).get("fixture_ids") or []}
    normalized = sorted(set(str(value) for value in fixture_ids))
    unknown = sorted(set(normalized) - valid_catalog)
    if unknown:
        raise ValueError("unknown homelab fixtures: " + ", ".join(unknown))
    if normalized and not set(normalized).issubset(required):
        raise ValueError("receipt names a fixture outside the target manifest")
    return normalized


def record_homelab_stage(
    catalog: Mapping[str, Any],
    *,
    target_id: str,
    stage: str,
    node_sha256: str,
    status: str,
    fixture_ids: Sequence[str] = (),
    artifact_sha256s: Sequence[str] = (),
    measurements: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    target = _validate_catalog_and_target(catalog, target_id)
    if stage not in target["required_stages"]:
        raise ValueError(f"stage {stage!r} is not required by target {target_id}")
    if stage in _AUDITION_STAGES or stage in _TERMINAL_STAGES:
        raise ValueError(f"stage {stage!r} requires its dedicated audition/decision object")
    if status not in HOMELAB_STAGE_STATUSES:
        raise ValueError(f"invalid Homelab stage status: {status}")
    if not _is_sha256(str(node_sha256)):
        raise ValueError("node_sha256 must be a SHA-256 identity")
    fixtures = _validate_fixture_ids(catalog, target, fixture_ids)
    artifacts = sorted(set(str(value).lower() for value in artifact_sha256s))
    if any(not _is_sha256(value) for value in artifacts):
        raise ValueError("every stage artifact must be a SHA-256 identity")
    if status == "passed" and stage in _ARTIFACT_REQUIRED_STAGES and not artifacts:
        raise ValueError(f"passing stage {stage} requires at least one custodied artifact identity")
    if status == "passed" and stage in _FIXTURE_SENSITIVE_STAGES:
        required = set(str(value) for value in (target.get("requirements") or {}).get("fixture_ids") or [])
        if required and not required.issubset(fixtures):
            raise ValueError(f"passing stage {stage} must cover every target fixture")
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_stage_receipt",
        "recorded_at": _now_utc(),
        "catalog_sha256": catalog["catalog_sha256"],
        "target_id": target_id,
        "target_manifest_sha256": target["target_manifest_sha256"],
        "stage": stage,
        "node_sha256": str(node_sha256),
        "status": status,
        "fixture_ids": fixtures,
        "artifact_sha256s": artifacts,
        "measurements": deepcopy(dict(measurements or {})),
        "notes": [str(note) for note in notes],
        "boundary": {
            "stage_receipt_is_not_adoption": True,
            "process_exit_without_artifacts_is_not_passage": True,
        },
    }
    return homelab_seal(payload)


def record_homelab_audition(
    catalog: Mapping[str, Any],
    *,
    target_id: str,
    node_sha256: str,
    reviewer_id: str,
    candidate_sha256: str,
    control_sha256: str,
    verdict: str,
    blinded: bool,
    randomized: bool,
    playback_chain: Mapping[str, Any],
    dimensions: Mapping[str, Any],
    fixture_ids: Sequence[str] = (),
    notes: Sequence[str] = (),
    adjudication_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    target = _validate_catalog_and_target(catalog, target_id)
    stages = [stage for stage in target["required_stages"] if stage in _AUDITION_STAGES]
    if len(stages) != 1:
        raise ValueError(f"target {target_id} does not define exactly one audition stage")
    stage = stages[0]
    if verdict not in HOMELAB_AUDITION_VERDICTS:
        raise ValueError(f"invalid audition verdict: {verdict}")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    for label, digest in (("node", node_sha256), ("candidate", candidate_sha256), ("control", control_sha256)):
        if not _is_sha256(str(digest)):
            raise ValueError(f"{label}_sha256 must be a SHA-256 identity")
    if str(candidate_sha256) == str(control_sha256):
        raise ValueError("candidate and control must be different artifacts")
    if stage == "blind_audition" and (not blinded or not randomized):
        raise ValueError("blind audition requires both blinding and randomized assignment")
    refs = {str(key): str(value) for key, value in dict(adjudication_refs or {}).items()}
    if stage == "blind_audition":
        required_refs = {"assignment_sha256", "private_authority_sha256", "submission_sha256"}
        if set(refs) != required_refs or any(not _is_sha256(value) for value in refs.values()):
            raise ValueError("blind audition requires committed assignment, private authority, and submission evidence")
    elif refs:
        raise ValueError("adjudication_refs are only valid for a blind audition")
    if not playback_chain:
        raise ValueError("playback_chain is required")
    if not dimensions:
        raise ValueError("audition dimensions are required")
    fixtures = _validate_fixture_ids(catalog, target, fixture_ids)
    required = set(str(value) for value in (target.get("requirements") or {}).get("fixture_ids") or [])
    if required and not required.issubset(fixtures):
        raise ValueError("audition must cover every fixture required by the target manifest")
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_audition_ledger",
        "reviewed_at": _now_utc(),
        "catalog_sha256": catalog["catalog_sha256"],
        "target_id": target_id,
        "target_manifest_sha256": target["target_manifest_sha256"],
        "stage": stage,
        "node_sha256": str(node_sha256),
        "reviewer_id": reviewer_id.strip(),
        "candidate_sha256": str(candidate_sha256),
        "control_sha256": str(control_sha256),
        "fixture_ids": fixtures,
        "verdict": verdict,
        "blinded": bool(blinded),
        "randomized": bool(randomized),
        "playback_chain": deepcopy(dict(playback_chain)),
        "dimensions": deepcopy(dict(dimensions)),
        "notes": [str(note) for note in notes],
        **refs,
        "boundary": {
            "audition_is_not_adoption": True,
            "audition_is_not_legal_clearance": True,
            "audition_is_not_whole_buffalo_passage": True,
        },
    }
    return homelab_seal(payload)


def decide_homelab_target(
    audit: Mapping[str, Any],
    *,
    target_id: str,
    decision: str,
    decided_by: str,
    reason: str,
    supporting_receipt_sha256s: Sequence[str] = (),
) -> dict[str, Any]:
    homelab_validate_seal(audit)
    if audit.get("kind") != "earcrate_homelab_audit":
        raise ValueError("adoption decision requires a HomelabAudit")
    if decision not in HOMELAB_DECISIONS:
        raise ValueError(f"invalid Homelab decision: {decision}")
    if not decided_by.strip() or not reason.strip():
        raise ValueError("decided_by and reason are required")
    row = next((dict(value) for value in audit.get("targets") or [] if value.get("target_id") == target_id), None)
    if row is None:
        raise ValueError(f"target {target_id!r} is absent from the audit")
    receipts = sorted(set(str(value).lower() for value in supporting_receipt_sha256s))
    if any(not _is_sha256(value) for value in receipts):
        raise ValueError("supporting receipt identities must be SHA-256 values")
    evidence_index = dict(audit.get("evidence_index") or {})
    missing_receipts = [value for value in receipts if value not in evidence_index]
    if missing_receipts:
        raise ValueError("supporting receipts are not present in the audited estate: " + ", ".join(missing_receipts))
    wrong_scope = [
        identity
        for identity in receipts
        if (
            (evidence_index.get(identity) or {}).get("target_id") != target_id
            or (evidence_index.get(identity) or {}).get("catalog_sha256") != audit.get("catalog_sha256")
            or (evidence_index.get(identity) or {}).get("target_manifest_sha256") != row.get("target_manifest_sha256")
            or (evidence_index.get(identity) or {}).get("node_sha256") != row.get("assigned_node_sha256")
        )
    ]
    if wrong_scope:
        raise ValueError(
            "supporting receipts do not belong to the current target, manifest, and node: "
            + ", ".join(wrong_scope)
        )

    if decision == "accepted":
        if row.get("feasibility") != "ready":
            raise ValueError("cannot accept a target with unresolved feasibility blockers")
        missing_nonterminal = [stage for stage in row.get("missing_stages") or [] if stage not in _TERMINAL_STAGES]
        if missing_nonterminal:
            raise ValueError("cannot accept target before required stages pass: " + ", ".join(missing_nonterminal))
        if row.get("failed_stages") or row.get("refused_stages"):
            raise ValueError("cannot accept target while the latest current stage is failed or refused")
        if row.get("audition_required") and not row.get("audition_acceptance_present"):
            raise ValueError("audio/workflow target requires an accepting human audition")
        current_stage_receipts = {
            str(identity)
            for stage, identity in dict(row.get("stage_evidence") or {}).items()
            if stage not in _TERMINAL_STAGES and _is_sha256(str(identity))
        }
        if not current_stage_receipts:
            raise ValueError("accepted target requires current stage receipt identities")
        missing_current = sorted(current_stage_receipts - set(receipts))
        if missing_current:
            raise ValueError(
                "accepted target decision must bind every current stage receipt: "
                + ", ".join(missing_current)
            )
    payload: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_adoption_decision",
        "decided_at": _now_utc(),
        "audit_sha256": audit["audit_sha256"],
        "catalog_sha256": audit["catalog_sha256"],
        "target_id": target_id,
        "target_manifest_sha256": row["target_manifest_sha256"],
        "assigned_node_sha256": row["assigned_node_sha256"],
        "decision": decision,
        "decided_by": decided_by.strip(),
        "reason": reason.strip(),
        "supporting_receipt_sha256s": receipts,
        "scope": {
            "completed_stages": list(row.get("completed_stages") or []),
            "fixture_ids": sorted((row.get("feasibility_evidence") or {}).get("fixtures") or {}),
            "warnings": list(row.get("warnings") or []),
        },
        "boundary": {
            "decision_is_target_and_manifest_scoped": True,
            "decision_is_node_and_current_evidence_scoped": True,
            "decision_is_not_legal_clearance": True,
            "decision_is_not_whole_buffalo_passage": True,
        },
    }
    return homelab_seal(payload)


def homelab_sweep(
    roots: Sequence[str | Path],
    *,
    estate_root: str | Path,
    output_dir: str | Path,
    canon_ledger_path: str | Path | None = None,
    hash_mode: str = "evidence",
    include_audio_devices: bool = False,
) -> dict[str, Any]:
    """Emit estate and Homelab reports without installing or executing providers."""
    if not roots:
        raise ValueError("at least one explicit root is required")
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"homelab sweep output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    policy = default_estate_policy()
    architecture = estate_architecture()
    rig = capture_rig_capabilities(roots=roots, include_audio_devices=include_audio_devices)
    inventory = scan_estate(roots, policy=policy, hash_mode=hash_mode, canon_ledger_path=canon_ledger_path)
    plan = propose_estate_plan(inventory, estate_root, policy=policy)
    local_campaign = propose_local_acceptance_campaign(inventory, rig, canon_ledger_path=canon_ledger_path)
    catalog = homelab_catalog()
    node = capture_homelab_node(rig, catalog=catalog)
    audit = audit_homelab(inventory, [node], catalog=catalog)
    campaign = propose_homelab_campaign(audit, catalog=catalog)

    payloads = {
        "estate.architecture.json": architecture,
        "estate.policy.json": policy,
        "estate.rig.json": rig,
        "estate.inventory.json": inventory,
        "estate.inventory.redacted.json": redact_estate_inventory(inventory),
        "estate.plan.json": plan,
        "estate.local-acceptance.campaign.json": local_campaign,
        "homelab.catalog.json": catalog,
        "homelab.node.json": node,
        "homelab.audit.json": audit,
        "homelab.campaign.json": campaign,
    }
    outputs: dict[str, str] = {}
    for name, value in payloads.items():
        outputs[name] = str(write_estate_json(output / name, value))
    manifest = {
        "schema_version": 1,
        "kind": "earcrate_homelab_sweep_manifest",
        "created_at": _now_utc(),
        "mutation": "report files only; scanned roots unchanged",
        "outputs": {
            name: {
                "path": path,
                "raw_sha256": estate_sha256_file(path),
                "bytes": int(Path(path).stat().st_size),
            }
            for name, path in sorted(outputs.items())
        },
        "catalog_sha256": catalog["catalog_sha256"],
        "node_sha256": node["node_sha256"],
        "audit_sha256": audit["audit_sha256"],
        "campaign_sha256": campaign["campaign_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "boundary": {
            "software_installed": False,
            "weights_downloaded": False,
            "provider_invoked": False,
            "source_audio_decoded": False,
            "human_audition_performed": False,
            "target_adopted": False,
        },
    }
    manifest["manifest_sha256"] = _sha_json(manifest)
    manifest_path = write_estate_json(output / "homelab.sweep.manifest.json", manifest)
    outputs["homelab.sweep.manifest.json"] = str(manifest_path)
    return {
        "ok": True,
        "kind": "earcrate_homelab_sweep",
        "output_dir": str(output),
        "outputs": outputs,
        "manifest_sha256": manifest["manifest_sha256"],
        "catalog_sha256": catalog["catalog_sha256"],
        "node_sha256": node["node_sha256"],
        "audit_sha256": audit["audit_sha256"],
        "campaign_sha256": campaign["campaign_sha256"],
        "mutation": "report files only; scanned roots unchanged",
    }


__all__ = [
    "homelab_catalog",
    "capture_homelab_node",
    "bind_homelab_fixture",
    "audit_homelab",
    "propose_homelab_campaign",
    "record_homelab_stage",
    "record_homelab_audition",
    "decide_homelab_target",
    "homelab_sweep",
    "homelab_seal",
    "homelab_validate_seal",
]
