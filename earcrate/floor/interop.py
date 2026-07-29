from __future__ import annotations
import shutil
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from .model import K_CRATE, FloorError, floor_read_json, floor_seal_invocation_receipt, floor_seal_provider_manifest, floor_seal_provider_request, floor_seal_provider_result, floor_sha256_file, floor_sha256_json, floor_write_json_atomic

def _value(v): return floor_read_json(v) if not isinstance(v,Mapping) else deepcopy(dict(v))
def _inside(root:Path,path:Path)->bool:
    try: path.relative_to(root); return True
    except ValueError: return False

def floor_export_crate(*,manifest,request,result,receipt,output_dir,artifact_root=None,include_derived_artifacts=False,overwrite=False)->dict[str,Any]:
    m=floor_seal_provider_manifest(_value(manifest)); q=floor_seal_provider_request(_value(request)); r=floor_seal_provider_result(_value(result),manifest=m,request=q); x=floor_seal_invocation_receipt(_value(receipt))
    if (x['manifest_sha256'],x['request_sha256'],x['result_sha256'])!=(m['manifest_sha256'],q['request_sha256'],r['result_sha256']): raise FloorError('crate inputs are not bound by one receipt')
    root=Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        if not overwrite: raise FileExistsError(f"refusing to overwrite nonempty Floor crate: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    floor_write_json_atomic(root/'provider.manifest.json',m); floor_write_json_atomic(root/'request.json',q); floor_write_json_atomic(root/'provider.result.json',r); floor_write_json_atomic(root/'invocation.receipt.json',x)
    jams={'sandbox':{'request_semantic_sha256':q['request_semantic_sha256'],'result_semantic_sha256':r['result_semantic_sha256'],'mapping_status':'informative_not_certified'},'file_metadata':{'identifiers':{v['artifact_id']:v['sha256'] for v in q['inputs']}},'annotations':[{'namespace':f"earcrate_floor.{v['output_kind']}",'annotation_metadata':{'branch':v['branch'],'tier':v['tier'],'evidence_refs':v['evidence_refs']},'data':[{'time':0,'duration':0,'value':v['payload'],'confidence':v['confidence']}]} for v in r['outputs']]}; floor_write_json_atomic(root/'annotations.jams.json',jams)
    activity=f"floor:invocation:{x['receipt_semantic_sha256']}"; prov={'prefix':{'floor':'https://earcrate.local/floor#','prov':'http://www.w3.org/ns/prov#'},'activity':{activity:{'prov:type':'floor:ProviderInvocation'}},'agent':{f"floor:provider:{m['provider_id']}@{m['provider_version']}":{'prov:type':'prov:SoftwareAgent'}},'entity':{f"floor:artifact:{v['sha256']}":{'floor:artifactId':v['artifact_id']} for v in [*q['inputs'],*r['artifacts']]},'floor:mappingStatus':'informative_not_certified'}; floor_write_json_atomic(root/'provenance.prov.json',prov)
    rights=[]
    for output in r['outputs']:
        if isinstance(output['payload'],Mapping) and isinstance(output['payload'].get('rights'),Mapping): rights.append(output['payload']['rights'])
    floor_write_json_atomic(root/'rights.odrl.json',{'@context':'http://www.w3.org/ns/odrl.jsonld','@type':'Set','uid':f"urn:sha256:{q['request_semantic_sha256']}",'permission':[],'prohibition':[],'obligation':[],'earcrate:rightsAssertions':rights,'earcrate:legalDetermination':False,'earcrate:mappingStatus':'informative_not_certified'})
    floor_write_json_atomic(root/'ro-crate-metadata.json',{'@context':'https://w3id.org/ro/crate/1.1/context','@graph':[{'@id':'ro-crate-metadata.json','@type':'CreativeWork','about':{'@id':'./'},'conformsTo':{'@id':'https://w3id.org/ro/crate/1.1'}},{'@id':'./','@type':'Dataset','name':'EarCrate Floor invocation','identifier':x['receipt_semantic_sha256'],'hasPart':[{'@id':v} for v in ['provider.manifest.json','request.json','provider.result.json','invocation.receipt.json']]}]})
    copied=[]
    if include_derived_artifacts:
        if not artifact_root: raise FloorError('artifact_root required')
        source_root=Path(artifact_root).expanduser().resolve(); target_root=root/'derived'; target_root.mkdir()
        for artifact in r['artifacts']:
            pure=PurePosixPath(artifact['path'].replace('\\','/'))
            if pure.is_absolute() or any(v in {'','.','..'} for v in pure.parts): raise FloorError('unsafe derived path')
            source=(source_root/Path(*pure.parts)).resolve(); target=(target_root/Path(*pure.parts)).resolve()
            if not _inside(source_root,source) or not source.is_file() or source.is_symlink() or floor_sha256_file(source)!=artifact['sha256']: raise FloorError('derived artifact custody failed')
            if not _inside(target_root.resolve(),target): raise FloorError('derived destination escaped crate')
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target); copied.append({'artifact_id':artifact['artifact_id'],'path':target.relative_to(root).as_posix()})
    files=[{'path':p.relative_to(root).as_posix(),'sha256':floor_sha256_file(p),'size_bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file() and p.name not in {'floor-crate.json','checksums.sha256'}]
    crate={'schema_version':1,'kind':K_CRATE,'files':files,'source_media_copied':False,'derived_artifacts_copied':bool(copied),'copied_derived_artifacts':copied,'mapping_status':'informative_not_certified','metadata':{'request_semantic_sha256':q['request_semantic_sha256'],'result_semantic_sha256':r['result_semantic_sha256'],'standards_mappings':['JAMS','W3C PROV','ODRL','RO-Crate']}}; crate['crate_sha256']=floor_sha256_json(crate); floor_write_json_atomic(root/'floor-crate.json',crate)
    rows=[f"{floor_sha256_file(p)}  {p.relative_to(root).as_posix()}" for p in sorted(root.rglob('*')) if p.is_file() and p.name!='checksums.sha256']; (root/'checksums.sha256').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    return {'ok':True,'complete':True,'output_dir':str(root),'crate':crate,'crate_path':str(root/'floor-crate.json')}
__all__=['floor_export_crate']
