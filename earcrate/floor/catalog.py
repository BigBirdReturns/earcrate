from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping, Sequence
from .model import PROTOCOL, floor_load_provider_manifest, floor_seal_provider_request, floor_sha256_json

SUFFIXES=('.floor-provider.json','.provider.floor.json','.provider.json')

def floor_manifest_compatibility(manifest:Mapping[str,Any],request:Mapping[str,Any])->dict[str,Any]:
    q=floor_seal_provider_request(request); reasons=[]
    if q['capability'] not in manifest.get('capabilities',[]): reasons.append('capability_not_declared')
    if q['evidence']['branch'] not in manifest['evidence']['accepted_branches']: reasons.append('evidence_branch_not_accepted')
    if q['evidence']['tier'] not in manifest['evidence']['accepted_tiers']: reasons.append('evidence_tier_not_accepted')
    if manifest['runtime']['requires_network'] and not q['network_policy']['allowed']: reasons.append('network_policy_conflict')
    if manifest['entrypoint']['protocol']!=PROTOCOL: reasons.append('not_subprocess_conformant')
    return {'compatible':not reasons,'reasons':reasons,'provider_id':manifest['provider_id'],'provider_version':manifest['provider_version'],'request_semantic_sha256':q['request_semantic_sha256']}

def floor_discover_provider_catalog(roots:Sequence[str|Path],*,request:Mapping[str,Any]|None=None)->dict[str,Any]:
    files=[]
    for raw in roots:
        root=Path(raw).expanduser().resolve()
        if root.is_file(): files.append(root)
        elif root.is_dir(): files.extend(p for p in root.rglob('*.json') if p.name.endswith(SUFFIXES))
        else: files.append(root)
    providers=[]; refusals=[]; seen={}
    for path in sorted(set(files),key=str):
        if not path.is_file(): refusals.append({'path':str(path),'reason':'manifest_path_missing'}); continue
        try: manifest=floor_load_provider_manifest(path)
        except Exception as exc: refusals.append({'path':str(path),'reason':'invalid_manifest','error':str(exc),'type':type(exc).__name__}); continue
        key=(manifest['provider_id'],manifest['provider_version']); old=seen.get(key)
        if old:
            refusals.append({'path':str(path),'reason':'duplicate_manifest_identity' if old[0]==manifest['manifest_sha256'] else 'conflicting_provider_identity','first_path':old[1],'provider_id':key[0],'provider_version':key[1]}); continue
        seen[key]=(manifest['manifest_sha256'],str(path)); row={'path':str(path),'manifest':manifest}
        if request is not None: row['compatibility']=floor_manifest_compatibility(manifest,request)
        providers.append(row)
    out={'schema_version':1,'kind':'earcrate_floor_provider_catalog','roots':[str(Path(v).expanduser().resolve()) for v in roots],'provider_count':len(providers),'refusal_count':len(refusals),'providers':providers,'refusals':refusals,'discovery_is_trust':False,'discovery_is_selection':False}; out['catalog_sha256']=floor_sha256_json(out); return out
__all__=['SUFFIXES','floor_manifest_compatibility','floor_discover_provider_catalog']
