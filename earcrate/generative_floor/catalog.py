from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence
from urllib import request as urllib_request

from .core import (
    AUTHORITY_LIMITS,
    PROVIDER_CLASSES,
    SCHEMA_VERSION,
    TASK_MODES,
    ProviderUnavailable,
    ValidationError,
    artifact_identity,
    is_sha256,
    now_utc,
    require_nonempty,
    require_portable,
    seal,
    sha256_file,
    validate_seal,
)


def validate_provider_catalog(catalog: Mapping[str, Any]) -> str:
    catalog_sha = validate_seal(catalog, kind="earcrate_generation_provider_catalog")
    require_portable(catalog, label="provider catalog")
    providers = [dict(row) for row in catalog.get("providers") or []]
    if not providers:
        raise ValidationError("provider catalog requires providers")
    seen: set[str] = set()
    for provider in providers:
        provider_id = require_nonempty(provider.get("provider_id"), label="provider_id")
        if provider_id in seen:
            raise ValidationError(f"duplicate provider_id: {provider_id}")
        seen.add(provider_id)
        provider_class = require_nonempty(provider.get("provider_class"), label=f"{provider_id}.provider_class")
        if provider_class not in PROVIDER_CLASSES:
            raise ValidationError(f"{provider_id} has unsupported provider_class {provider_class!r}")
        repository = dict(provider.get("repository") or {})
        require_nonempty(repository.get("url"), label=f"{provider_id}.repository.url")
        require_nonempty(repository.get("revision_policy"), label=f"{provider_id}.repository.revision_policy")
        capabilities = set(str(value) for value in provider.get("capabilities") or [])
        unknown = sorted(capabilities - TASK_MODES)
        if unknown:
            raise ValidationError(f"{provider_id} has unknown task modes: {unknown}")
        if not capabilities and provider_class in {"foundation_model", "specialist_model"}:
            raise ValidationError(f"{provider_id} requires at least one task capability")
        authority = dict(provider.get("authority") or {})
        for key, expected in AUTHORITY_LIMITS.items():
            if authority.get(key, expected) is not expected:
                raise ValidationError(f"{provider_id} exceeds authority: {key}")
    return catalog_sha


def validate_generation_request(request: Mapping[str, Any]) -> str:
    request_sha = validate_seal(request, kind="earcrate_generation_request")
    require_portable(request, label="generation request")
    task_mode = require_nonempty(request.get("task_mode"), label="task_mode")
    if task_mode not in TASK_MODES:
        raise ValidationError(f"unsupported task_mode: {task_mode}")
    require_nonempty(request.get("provider_id"), label="provider_id")
    model = dict(request.get("model") or {})
    require_nonempty(model.get("repository"), label="model.repository")
    require_nonempty(model.get("revision"), label="model.revision")
    assets = [dict(row) for row in model.get("assets") or []]
    if not assets:
        raise ValidationError("generation request must pin at least one model or codec asset")
    for index, asset in enumerate(assets):
        if not is_sha256(asset.get("sha256")):
            raise ValidationError(f"model.assets[{index}] requires sha256")
        if int(asset.get("bytes") or 0) <= 0:
            raise ValidationError(f"model.assets[{index}] requires positive bytes")
    seed = request.get("seed")
    if seed is None or isinstance(seed, bool):
        raise ValidationError("generation request requires an explicit integer seed")
    try:
        int(seed)
    except (TypeError, ValueError) as exc:
        raise ValidationError("generation request seed must be an integer") from exc
    for index, row in enumerate(request.get("conditioning") or []):
        require_nonempty(row.get("source_id"), label=f"conditioning[{index}].source_id")
        if not is_sha256(row.get("container_sha256")):
            raise ValidationError(f"conditioning[{index}] requires container_sha256")
    rights = dict(request.get("rights_scope") or {})
    if rights.get("private_local_analysis") is not True:
        raise ValidationError("rights_scope.private_local_analysis must be true")
    if rights.get("public_upload_allowed") not in {False, None}:
        raise ValidationError("public upload cannot be assumed")
    authority = dict(request.get("authority") or {})
    for key, expected in AUTHORITY_LIMITS.items():
        if authority.get(key, expected) is not expected:
            raise ValidationError(f"generation request exceeds authority: {key}")
    return request_sha


def provider_map(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    validate_provider_catalog(catalog)
    return {str(row["provider_id"]): dict(row) for row in catalog.get("providers") or []}


def build_generation_request(
    *,
    provider_id: str,
    task_mode: str,
    model_repository: str,
    model_revision: str,
    model_assets: Sequence[Mapping[str, Any]],
    seed: int,
    prompt: Mapping[str, Any],
    conditioning: Sequence[Mapping[str, Any]] = (),
    output_contract: Mapping[str, Any] | None = None,
    rights_scope: Mapping[str, Any] | None = None,
    parent_request_sha256: str | None = None,
) -> dict[str, Any]:
    request = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_request",
            "created_at": now_utc(),
            "provider_id": provider_id,
            "task_mode": task_mode,
            "model": {
                "repository": model_repository,
                "revision": model_revision,
                "assets": [deepcopy(dict(row)) for row in model_assets],
            },
            "seed": int(seed),
            "prompt": deepcopy(dict(prompt)),
            "conditioning": [deepcopy(dict(row)) for row in conditioning],
            "output_contract": deepcopy(
                dict(
                    output_contract
                    or {
                        "audio_required": True,
                        "separate_tracks_preferred": True,
                        "sample_rate_minimum": 32000,
                        "duration_seconds": None,
                    }
                )
            ),
            "rights_scope": deepcopy(
                dict(
                    rights_scope
                    or {
                        "private_local_analysis": True,
                        "public_upload_allowed": False,
                        "publication_permission": False,
                    }
                )
            ),
            "parent_request_sha256": parent_request_sha256,
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )
    validate_generation_request(request)
    return request


def _resolve_executable(name: str) -> str | None:
    value = shutil.which(name)
    return str(Path(value).resolve()) if value else None


def _hash_declared_asset(path: str | Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    identity = artifact_identity(path)
    if identity.sha256 != str(expected.get("sha256") or "").lower():
        raise ProviderUnavailable(f"model asset hash mismatch for {identity.name}")
    if identity.bytes != int(expected.get("bytes") or 0):
        raise ProviderUnavailable(f"model asset byte count mismatch for {identity.name}")
    return {"name": identity.name, "sha256": identity.sha256, "bytes": identity.bytes, "media_kind": identity.media_kind}


def probe_provider(
    provider: Mapping[str, Any],
    *,
    local_override: Mapping[str, Any] | None = None,
    node_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider_id = require_nonempty(provider.get("provider_id"), label="provider_id")
    override = deepcopy(dict(local_override or {}))
    blockers: list[str] = []
    evidence: dict[str, Any] = {}
    adapter = deepcopy(dict(override.get("adapter") or provider.get("default_adapter") or {}))
    adapter_kind = str(adapter.get("kind") or "unconfigured")
    evidence["adapter_kind"] = adapter_kind
    if adapter_kind == "command":
        argv = [str(value) for value in adapter.get("argv") or []]
        if not argv:
            blockers.append("command adapter has no argv")
        else:
            executable = _resolve_executable(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
            if not executable or not Path(executable).is_file():
                blockers.append(f"command executable unavailable: {argv[0]}")
            else:
                evidence["executable"] = {
                    "name": Path(executable).name,
                    "sha256": sha256_file(executable),
                    "bytes": Path(executable).stat().st_size,
                }
    elif adapter_kind == "http_json":
        base_url = str(adapter.get("base_url") or "")
        if not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            blockers.append("http_json adapter must target a loopback local service")
        evidence["base_url_class"] = "loopback" if not blockers else "invalid"
        if not blockers:
            health_endpoint = str(adapter.get("health_endpoint") or "/health")
            try:
                with urllib_request.urlopen(base_url.rstrip("/") + health_endpoint, timeout=int(adapter.get("probe_timeout_seconds") or 5)) as response:
                    evidence["health"] = {"http_status": int(response.status), "bytes": len(response.read())}
                if evidence["health"]["http_status"] >= 400:
                    blockers.append(f"local service health returned {evidence['health']['http_status']}")
            except Exception as exc:
                blockers.append(f"local service unavailable: {type(exc).__name__}: {exc}")
    elif adapter_kind == "unconfigured":
        blockers.append("no local adapter configured")
    else:
        blockers.append(f"unsupported adapter kind: {adapter_kind}")

    assets = []
    for row in override.get("model_assets") or []:
        try:
            assets.append(_hash_declared_asset(str(row.get("path") or ""), row))
        except Exception as exc:
            blockers.append(f"model asset unavailable: {type(exc).__name__}: {exc}")
    evidence["model_assets"] = assets
    node = deepcopy(dict(node_identity or {}))
    node.pop("absolute_paths", None)
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_provider_probe",
            "recorded_at": now_utc(),
            "provider_id": provider_id,
            "provider_repository": deepcopy(dict(provider.get("repository") or {})),
            "node": node,
            "ready": not blockers,
            "blockers": blockers,
            "evidence": evidence,
            "capabilities": list(provider.get("capabilities") or []),
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )


def compile_generation_campaign(
    *,
    catalog: Mapping[str, Any],
    campaign_spec: Mapping[str, Any],
    provider_probes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    catalog_sha = validate_provider_catalog(catalog)
    require_portable(campaign_spec, label="campaign specification")
    providers = provider_map(catalog)
    probes = {str(row.get("provider_id")): dict(row) for row in provider_probes}
    tasks: list[dict[str, Any]] = []
    for raw in campaign_spec.get("tasks") or []:
        task = deepcopy(dict(raw))
        task_id = require_nonempty(task.get("task_id"), label="campaign task_id")
        task_mode = require_nonempty(task.get("task_mode"), label=f"{task_id}.task_mode")
        selected: str | None = None
        candidate_rows: list[dict[str, Any]] = []
        for provider_id in [str(value) for value in task.get("provider_candidates") or []]:
            provider = providers.get(provider_id)
            probe = probes.get(provider_id)
            reason = None
            if provider is None:
                reason = "not catalogued"
            elif task_mode not in set(provider.get("capabilities") or []):
                reason = "capability absent"
            elif probe is None:
                reason = "not probed"
            elif probe.get("ready") is not True:
                reason = "; ".join(str(value) for value in probe.get("blockers") or []) or "probe not ready"
            elif selected is None:
                selected = provider_id
            candidate_rows.append({"provider_id": provider_id, "eligible": reason is None, "reason": reason})
        tasks.append(
            {
                "task_id": task_id,
                "strategy_family": task.get("strategy_family"),
                "task_mode": task_mode,
                "purpose": task.get("purpose"),
                "conditioning_source_ids": list(task.get("conditioning_source_ids") or []),
                "selected_provider_id": selected,
                "provider_candidates": candidate_rows,
                "status": "ready" if selected else "blocked",
                "blocked_reason": None if selected else "no ready provider supports the requested operation",
                "requested_variants": int(task.get("requested_variants") or 1),
                "acceptance_dimensions": list(task.get("acceptance_dimensions") or []),
                "authority": deepcopy(AUTHORITY_LIMITS),
            }
        )
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_generation_campaign",
            "created_at": now_utc(),
            "catalog_sha256": catalog_sha,
            "campaign_id": campaign_spec.get("campaign_id"),
            "fixture_id": campaign_spec.get("fixture_id"),
            "facts": deepcopy(dict(campaign_spec.get("facts") or {})),
            "controls": deepcopy(list(campaign_spec.get("controls") or [])),
            "tasks": tasks,
            "summary": {
                "tasks": len(tasks),
                "ready": sum(1 for row in tasks if row["status"] == "ready"),
                "blocked": sum(1 for row in tasks if row["status"] == "blocked"),
                "providers_ready": sum(1 for row in provider_probes if row.get("ready") is True),
            },
            "frontier_policy": deepcopy(dict(campaign_spec.get("frontier_policy") or {})),
            "authority": deepcopy(AUTHORITY_LIMITS),
        }
    )
