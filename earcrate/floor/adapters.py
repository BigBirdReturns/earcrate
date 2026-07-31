from __future__ import annotations

"""Floor manifests for EarCrate's existing provider seams.

These are honest discovery projections. They are marked ``in_process`` and therefore
do not claim subprocess conformance. The open Floor protocol gives future adapters a
portable path without forcing the existing runtime to pretend it already uses it.
"""

from copy import deepcopy
from typing import Any

from earcrate.providers import default_name, registered

from .model import floor_seal_provider_manifest

_FLOOR_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "artifacts": {
        "capability": "artifact.store",
        "input_media_kinds": ["*/*"],
        "result_kinds": ["derived_artifact", "measurement", "refusal"],
        "evidence_branches": ["performance", "review", "evolution"],
        "evidence_tiers": ["performance_realization", "human_review", "campaign_evidence"],
    },
    "stems": {
        "capability": "audio.stems",
        "input_media_kinds": ["audio/*"],
        "result_kinds": ["observation", "derived_artifact", "measurement", "refusal"],
        "evidence_branches": ["audio"],
        "evidence_tiers": ["blind_audio_inference"],
    },
    "notes": {
        "capability": "audio.notes",
        "input_media_kinds": ["audio/*"],
        "result_kinds": ["observation", "candidate", "measurement", "refusal"],
        "evidence_branches": ["audio"],
        "evidence_tiers": ["blind_audio_inference"],
    },
    "retriever": {
        "capability": "catalog.retrieve",
        "input_media_kinds": ["application/json"],
        "result_kinds": ["candidate", "measurement", "refusal"],
        "evidence_branches": ["score", "symbolic", "audio", "convergence", "performance"],
        "evidence_tiers": [
            "authoritative_score",
            "community_symbolic_witness",
            "blind_audio_inference",
            "cross_modal_accepted",
            "performance_realization",
        ],
    },
    "embedding": {
        "capability": "audio.embedding",
        "input_media_kinds": ["audio/*", "application/json"],
        "result_kinds": ["measurement", "derived_artifact", "refusal"],
        "evidence_branches": ["audio", "convergence"],
        "evidence_tiers": ["blind_audio_inference", "cross_modal_accepted"],
    },
    "vector_index": {
        "capability": "vector.search",
        "input_media_kinds": ["application/vnd.earcrate.vector+json", "application/json"],
        "result_kinds": ["candidate", "measurement", "refusal"],
        "evidence_branches": ["audio", "convergence", "performance"],
        "evidence_tiers": ["blind_audio_inference", "cross_modal_accepted", "performance_realization"],
    },
}


def floor_earcrate_provider_manifests() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for kind, contract in sorted(_FLOOR_PROVIDER_CAPABILITIES.items()):
        for name in registered(kind):
            provider_id = f"earcrate.{kind}.{name}".replace("_", "-")
            capability = deepcopy(contract)
            capability.update(
                {
                    "network_policy": "forbidden",
                    "determinism": "unknown",
                    "max_runtime_seconds": 3600,
                    "max_output_bytes": 4 << 30,
                    "parameter_schema": {},
                    "metadata": {
                        "earcrate_provider_kind": kind,
                        "earcrate_provider_name": name,
                        "earcrate_default": default_name(kind) == name,
                        "execution_mode": "in_process_adapter",
                        "subprocess_conformance_claimed": False,
                    },
                }
            )
            manifests.append(
                floor_seal_provider_manifest(
                    {
                        "schema_version": 1,
                        "kind": "earcrate_floor_provider_manifest",
                        "provider_id": provider_id,
                        "provider_version": "1",
                        "display_name": f"EarCrate {kind}/{name}",
                        "description": "Projection of an existing in-process EarCrate provider seam; not a subprocess conformance claim.",
                        "protocol": {"name": "earcrate-floor-stdio-json", "version": 1},
                        "entrypoint": {
                            "argv": ["earcrate-in-process-provider", kind, name],
                            "working_directory": "${FLOOR_MANIFEST_DIR}",
                            "environment": {},
                        },
                        "capabilities": [capability],
                        "authority": {
                            "may_emit": capability["result_kinds"],
                            "may_not_emit": [],
                        },
                        "supply_chain": {
                            "license_expression": "PolyForm-Noncommercial-1.0.0",
                            "source_uri": "https://github.com/BigBirdReturns/earcrate",
                            "model_identities": [],
                            "signatures": [],
                        },
                        "metadata": {
                            "execution_mode": "in_process_adapter",
                            "subprocess_conformance_claimed": False,
                        },
                    }
                )
            )
    return manifests


__all__ = ["floor_earcrate_provider_manifests"]
