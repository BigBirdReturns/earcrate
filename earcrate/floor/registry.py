from __future__ import annotations

"""In-process registry for Floor manifest factories.

The registry is discovery only. Registration does not imply trust, conformance,
quality, installation, or selection.
"""

from typing import Any, Callable

from .model import FloorError, floor_seal_provider_manifest

_FLOOR_MANIFEST_FACTORIES: dict[str, Callable[[], dict[str, Any]]] = {}


def floor_register_manifest(provider_id: str, factory: Callable[[], dict[str, Any]]) -> None:
    ident = str(provider_id).strip().lower()
    if not ident:
        raise FloorError("Floor provider registration requires provider_id")
    if not callable(factory):
        raise FloorError("Floor provider registration requires a callable factory")
    _FLOOR_MANIFEST_FACTORIES[ident] = factory


def floor_registered_manifest_ids() -> list[str]:
    return sorted(_FLOOR_MANIFEST_FACTORIES)


def floor_get_registered_manifest(provider_id: str) -> dict[str, Any]:
    ident = str(provider_id).strip().lower()
    factory = _FLOOR_MANIFEST_FACTORIES.get(ident)
    if factory is None:
        raise FloorError(f"no Floor manifest registered for {ident!r}")
    manifest = floor_seal_provider_manifest(factory())
    if manifest["provider_id"] != ident:
        raise FloorError("registered Floor manifest factory returned another provider_id")
    return manifest


def floor_registered_manifests() -> list[dict[str, Any]]:
    return [floor_get_registered_manifest(provider_id) for provider_id in floor_registered_manifest_ids()]


__all__ = [
    "floor_register_manifest",
    "floor_registered_manifest_ids",
    "floor_get_registered_manifest",
    "floor_registered_manifests",
]
