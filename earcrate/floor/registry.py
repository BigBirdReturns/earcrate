from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping
from .model import FloorError, floor_seal_provider_manifest, floor_sha256_json

_REGISTRY: dict[tuple[str,str],dict[str,Any]]={}

def floor_register_manifest(manifest:Mapping[str,Any],*,replace:bool=False)->dict[str,Any]:
    row=floor_seal_provider_manifest(manifest); key=(row['provider_id'],row['provider_version']); old=_REGISTRY.get(key)
    if old and old['manifest_sha256']!=row['manifest_sha256'] and not replace: raise FloorError(f"conflicting provider manifest identity for {key[0]}@{key[1]}")
    _REGISTRY[key]=deepcopy(row); return deepcopy(row)

def floor_get_registered_manifest(provider_id:str,provider_version:str)->dict[str,Any]: return deepcopy(_REGISTRY[(str(provider_id),str(provider_version))])
def floor_registered_provider_keys()->list[str]: return [f"{a}@{b}" for a,b in sorted(_REGISTRY)]
def floor_clear_registry()->None: _REGISTRY.clear()
def floor_registry_snapshot()->dict[str,Any]:
    out={'schema_version':1,'kind':'earcrate_floor_registry_snapshot','providers':[deepcopy(_REGISTRY[k]) for k in sorted(_REGISTRY)],'registration_is_trust':False,'registration_is_conformance':False,'registration_is_selection':False}; out['provider_count']=len(out['providers']); out['snapshot_sha256']=floor_sha256_json(out); return out
__all__=[name for name in globals() if name.startswith('floor_')]
