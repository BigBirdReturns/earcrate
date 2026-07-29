from __future__ import annotations

import hashlib, json, os, shutil, subprocess, sys, time
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from .catalog import floor_manifest_compatibility
from .model import FloorError, PROTOCOL, floor_canonical_json_bytes, floor_load_provider_manifest, floor_read_json, floor_seal_invocation_receipt, floor_seal_provider_request, floor_seal_provider_result, floor_sha256_file, floor_write_json_atomic

ENV_KEYS={'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','TMPDIR','HOME','USERPROFILE','LOCALAPPDATA','APPDATA','LANG','LC_ALL','PYTHONPATH'}

def _contained(root:Path,path:Path)->bool:
    try: path.relative_to(root); return True
    except ValueError: return False

def _destination(path:str|Path,overwrite:bool)->Path:
    root=Path(path).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        if not overwrite: raise FileExistsError(f"refusing to overwrite nonempty Floor output: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True,exist_ok=True); return root

def _verify_inputs(request:Mapping[str,Any])->list[dict[str,Any]]:
    rows=[]
    for item in request['inputs']:
        path=Path(item['path']).expanduser().resolve()
        if not path.is_file() or path.is_symlink(): raise FloorError(f"input unavailable or symlinked: {item['artifact_id']}")
        size=path.stat().st_size; sha=floor_sha256_file(path)
        if size!=item['size_bytes']: raise FloorError(f"input size changed: {item['artifact_id']}")
        if sha!=item['sha256']: raise FloorError(f"input identity changed: {item['artifact_id']}")
        rows.append({'artifact_id':item['artifact_id'],'path':str(path),'size_bytes':size,'sha256':sha})
    return rows

def _argv(manifest:Mapping[str,Any],manifest_dir:Path,artifact_dir:Path)->list[str]:
    replacements={'${FLOOR_MANIFEST_DIR}':str(manifest_dir),'${FLOOR_ARTIFACT_DIR}':str(artifact_dir),'${FLOOR_PYTHON}':str(Path(sys.executable).resolve())}
    result=[]
    for token in manifest['entrypoint']['argv']:
        for marker,value in replacements.items(): token=token.replace(marker,value)
        if '${FLOOR_' in token: raise FloorError(f"unresolved Floor placeholder: {token}")
        result.append(token)
    if not Path(result[0]).is_absolute():
        result[0]=shutil.which(result[0]) or str((manifest_dir/result[0]).resolve())
    return result

def _entrypoints(argv:list[str],manifest:Mapping[str,Any])->tuple[str|None,list[dict[str,Any]]]:
    rows=[]
    for i,token in enumerate(argv):
        path=Path(token)
        if path.is_file() and not path.is_symlink(): rows.append({'argv_index':i,'path':str(path.resolve()),'sha256':floor_sha256_file(path),'size_bytes':path.stat().st_size})
    expected=manifest['supply_chain'].get('executable_sha256')
    if expected and expected not in {v['sha256'] for v in rows}: raise FloorError('entrypoint executable identity mismatch')
    return (rows[0]['sha256'] if rows else None),rows

def _env(artifact_dir:Path,manifest_dir:Path,network:bool)->dict[str,str]:
    env={k:v for k,v in os.environ.items() if k in ENV_KEYS}
    env.update({'FLOOR_PROTOCOL_VERSION':PROTOCOL,'FLOOR_ARTIFACT_DIR':str(artifact_dir),'FLOOR_MANIFEST_DIR':str(manifest_dir),'FLOOR_NETWORK_ALLOWED':'1' if network else '0','PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'})
    return env

def _artifacts(raw:dict[str,Any],artifact_dir:Path,request:Mapping[str,Any],manifest:Mapping[str,Any])->tuple[dict[str,Any],list[dict[str,Any]]]:
    enriched=deepcopy(raw); rows=[]; declared=set(); total=0; limit=min(request['artifact_policy']['max_total_bytes'],manifest['runtime']['max_artifact_bytes']); root=artifact_dir.resolve(); enriched_items=[]
    for item in raw.get('artifacts') or []:
        row=deepcopy(dict(item)); pure=PurePosixPath(str(row.get('path') or '').replace('\\','/'))
        if pure.is_absolute() or not pure.parts or any(v in {'','.','..'} for v in pure.parts): raise FloorError(f"unsafe provider artifact path: {row.get('path')!r}")
        rel=pure.as_posix()
        if rel in declared: raise FloorError('duplicate provider artifact path')
        declared.add(rel); path=(root/Path(*pure.parts)).resolve()
        if not _contained(root,path) or not path.is_file() or path.is_symlink(): raise FloorError('artifact escaped, is missing, or is symlinked')
        sha=floor_sha256_file(path); size=path.stat().st_size
        if row.get('sha256') and row['sha256']!=sha: raise FloorError(f"provider artifact hash mismatch: {rel}")
        if 'size_bytes' in row and int(row['size_bytes'])!=size: raise FloorError(f"provider artifact size mismatch: {rel}")
        total+=size
        if total>limit: raise FloorError('provider artifact limit exceeded')
        row.update({'path':rel,'sha256':sha,'size_bytes':size,'branch':row.get('branch',request['evidence']['branch']),'tier':row.get('tier',request['evidence']['tier']),'ancestor_branches':row.get('ancestor_branches',request['evidence']['ancestor_branches'])}); enriched_items.append(row); rows.append({'artifact_id':row.get('artifact_id',''),'path':rel,'sha256':sha,'size_bytes':size})
    actual=set()
    for path in root.rglob('*'):
        if path.is_symlink(): raise FloorError('artifact directory contains symlink')
        if path.is_file(): actual.add(path.relative_to(root).as_posix())
    if actual!=declared: raise FloorError(f"provider artifact inventory is not exact: unreported={sorted(actual-declared)}")
    enriched['artifacts']=enriched_items
    for output in enriched.get('outputs') or []:
        output.setdefault('branch',request['evidence']['branch']); output.setdefault('tier',request['evidence']['tier']); output.setdefault('ancestor_branches',request['evidence']['ancestor_branches'])
    return enriched,rows

def _run(manifest:Mapping[str,Any],manifest_path:Path,request:Mapping[str,Any],run_root:Path,timeout:int)->dict[str,Any]:
    artifact_dir=run_root/'artifacts'; artifact_dir.mkdir(parents=True); manifest_dir=manifest_path.parent.resolve(); argv=_argv(manifest,manifest_dir,artifact_dir); executable_sha,entrypoint_files=_entrypoints(argv,manifest)
    cwd_policy=manifest['entrypoint']['working_directory']; cwd=manifest_dir if cwd_policy=='manifest_dir' else artifact_dir if cwd_policy=='artifact_dir' else (manifest_dir/cwd_policy).resolve()
    started=time.monotonic()
    try: proc=subprocess.run(argv,input=floor_canonical_json_bytes(request)+b'\n',stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=str(cwd),env=_env(artifact_dir,manifest_dir,request['network_policy']['allowed']),shell=False,timeout=timeout)
    except subprocess.TimeoutExpired as exc: raise FloorError(f"provider timed out after {timeout}s") from exc
    stdout,stderr=bytes(proc.stdout or b''),bytes(proc.stderr or b''); (run_root/'stdout.bin').write_bytes(stdout); (run_root/'stderr.bin').write_bytes(stderr)
    if len(stdout)>manifest['runtime']['max_stdout_bytes'] or len(stderr)>manifest['runtime']['max_stderr_bytes']: raise FloorError('provider output exceeded declared limit')
    if proc.returncode: raise FloorError(f"provider exited {proc.returncode}: {stderr.decode(errors='replace')[-1000:]}")
    try: raw=json.loads(stdout.decode('utf-8'))
    except Exception as exc: raise FloorError(f"provider stdout is not one JSON object: {exc}") from exc
    if not isinstance(raw,dict): raise FloorError('provider stdout must be an object')
    raw,artifacts=_artifacts(raw,artifact_dir,request,manifest); result=floor_seal_provider_result(raw,manifest=manifest,request=request); floor_write_json_atomic(run_root/'provider.result.json',result)
    return {'result':result,'artifacts':artifacts,'argv':argv,'executable_sha256':executable_sha,'entrypoint_files':entrypoint_files,'returncode':proc.returncode,'stdout_sha256':hashlib.sha256(stdout).hexdigest(),'stderr_sha256':hashlib.sha256(stderr).hexdigest(),'duration_seconds':round(time.monotonic()-started,6)}

def floor_invoke_provider(manifest_path:str|Path,request:str|Path|Mapping[str,Any],output_dir:str|Path,*,repeat:int=1,require_repeatability:bool|None=None,timeout_seconds:int|None=None,overwrite:bool=False)->dict[str,Any]:
    manifest_path=Path(manifest_path).expanduser().resolve(); manifest=floor_load_provider_manifest(manifest_path); request=floor_seal_provider_request(floor_read_json(request) if not isinstance(request,Mapping) else request)
    compatibility=floor_manifest_compatibility(manifest,request); blocking=[v for v in compatibility['reasons'] if v!='not_subprocess_conformant']
    if blocking: raise FloorError(f"provider incompatible with request: {blocking}")
    if manifest['entrypoint']['protocol']!=PROTOCOL: raise FloorError('reference host runs stdio-json-v1 only')
    if manifest['runtime']['requires_network'] and not request['network_policy']['allowed']: raise FloorError('network policy conflict')
    repeat=int(repeat)
    if not 1<=repeat<=16: raise FloorError('repeat must be in 1..16')
    if require_repeatability is None: require_repeatability=repeat>1 and manifest['runtime']['determinism'] in {'deterministic','seeded'}
    root=_destination(output_dir,overwrite); inputs=_verify_inputs(request); floor_write_json_atomic(root/'provider.manifest.json',manifest); floor_write_json_atomic(root/'request.json',request)
    runs=[]
    for i in range(repeat):
        run_root=root/f'run-{i+1:04d}'; run_root.mkdir(); runs.append(_run(manifest,manifest_path,request,run_root,int(timeout_seconds or manifest['runtime']['timeout_seconds'])))
    semantic=[v['result']['result_semantic_sha256'] for v in runs]; signatures=[[(a['artifact_id'],a['sha256'],a['size_bytes']) for a in v['artifacts']] for v in runs]; repeat_ok=len(set(semantic))==1 and all(v==signatures[0] for v in signatures)
    if require_repeatability and not repeat_ok: raise FloorError('provider violated repeatability contract')
    first=runs[0]; result=first['result']; floor_write_json_atomic(root/'provider.result.json',result)
    receipt=floor_seal_invocation_receipt({'schema_version':1,'kind':'earcrate_floor_invocation_receipt','provider_id':manifest['provider_id'],'provider_version':manifest['provider_version'],'manifest_sha256':manifest['manifest_sha256'],'request_sha256':request['request_sha256'],'request_semantic_sha256':request['request_semantic_sha256'],'result_sha256':result['result_sha256'],'result_semantic_sha256':result['result_semantic_sha256'],'executable_sha256':first['executable_sha256'],'argv':first['argv'],'returncode':first['returncode'],'stdout_sha256':first['stdout_sha256'],'stderr_sha256':first['stderr_sha256'],'input_artifacts':inputs,'output_artifacts':first['artifacts'],'repeatability':{'run_count':repeat,'required':bool(require_repeatability),'passed':repeat_ok,'semantic_result_sha256s':semantic,'artifact_signatures':signatures},'network_policy':{'request_allowed':request['network_policy']['allowed'],'provider_requires_network':manifest['runtime']['requires_network'],'declaration_checked':True,'os_network_sandbox_enforced':False},'checks':{'input_identities_verified':True,'result_schema_verified':True,'artifact_paths_contained':True,'artifact_identities_verified':True,'authority_boundary_verified':True,'repeatability_verified':repeat_ok},'complete':True,'metadata':{'duration_seconds':sum(v['duration_seconds'] for v in runs),'entrypoint_files':first['entrypoint_files'],'reference_host':'earcrate.floor.protocol'}}); floor_write_json_atomic(root/'invocation.receipt.json',receipt)
    return {'ok':True,'complete':True,'output_dir':str(root),'manifest':manifest,'request':request,'result':result,'receipt':receipt,'result_path':str(root/'provider.result.json'),'receipt_path':str(root/'invocation.receipt.json')}

__all__=['floor_invoke_provider']
