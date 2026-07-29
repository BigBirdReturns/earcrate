from __future__ import annotations
from typing import Any
from earcrate.providers import default_name, registered
from .model import BRANCHES, TIERS, K_MANIFEST, floor_seal_provider_manifest, floor_sha256_json

CAPS={'artifacts':'artifact_store','stems':'stem_separation','notes':'note_transcription','retriever':'candidate_retrieval','embedding':'audio_embedding','vector_index':'vector_index'}
OUTPUTS={'artifacts':['derived_artifact','measurement','refusal'],'stems':['derived_artifact','measurement','refusal'],'notes':['observation','measurement','refusal'],'retriever':['candidate','measurement','refusal'],'embedding':['measurement','derived_artifact','refusal'],'vector_index':['candidate','measurement','refusal']}

def floor_manifest_for_earcrate_provider(kind:str,name:str)->dict[str,Any]:
    if kind not in CAPS: raise KeyError(kind)
    return floor_seal_provider_manifest({'schema_version':1,'kind':K_MANIFEST,'provider_id':f'earcrate.{kind}.{name}','provider_version':'current','display_name':f'EarCrate {kind}: {name}','description':'Existing in-process organ projected into the Floor catalog.','capabilities':[CAPS[kind]],'entrypoint':{'protocol':'python-in-process-v1','argv':[],'working_directory':'earcrate_process'},'runtime':{'language':'python','requires_network':False,'determinism':'unknown','timeout_seconds':300,'max_stdout_bytes':8<<20,'max_stderr_bytes':8<<20,'max_artifact_bytes':2<<30},'evidence':{'accepted_branches':list(BRANCHES),'accepted_tiers':list(TIERS)},'authority':{'may_emit':OUTPUTS[kind]},'supply_chain':{'license_expression':'SEE-REPOSITORY-LICENSE','source_uri':'https://github.com/BigBirdReturns/earcrate','source_revision':'current','model_artifacts':[],'signatures':[]},'metadata':{'earcrate_provider_kind':kind,'earcrate_provider_name':name,'earcrate_default':default_name(kind)==name,'subprocess_conformance_claimed':False}})

def floor_builtin_provider_manifests()->list[dict[str,Any]]: return [floor_manifest_for_earcrate_provider(k,n) for k in sorted(CAPS) for n in registered(k)]
def floor_builtin_provider_snapshot()->dict[str,Any]:
    rows=floor_builtin_provider_manifests(); out={'schema_version':1,'kind':'earcrate_floor_builtin_provider_snapshot','provider_count':len(rows),'providers':rows,'subprocess_conformance_claimed':False}; out['snapshot_sha256']=floor_sha256_json(out); return out
__all__=[name for name in globals() if name.startswith('floor_')]
