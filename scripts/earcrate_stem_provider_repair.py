#!/usr/bin/env python3
"""Prove and activate EarCrate's CUDA Demucs stem provider.

This repairs provider configuration only. It never reads Robi, starts ACE-Step,
or renders a commission. The workspace remains on its existing provider until a
current identity-bound crate source has been separated on CUDA and the emitted
instrumental stems reconcile. Failure leaves a REFUSAL.json and no authorization.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import traceback
from typing import Any, Mapping, Sequence

PROFILE = "girl_talk_v1"
PROVIDER = "demucs"
MODEL = "htdemucs"
TORCH_VERSION = "2.7.0"
DEMUCS_VERSION = "4.1.0"
ROLES = ("vocals", "drums", "bass", "other", "no_vocals")
CUDA_WHEELS = ((12.8, "cu128"), (12.6, "cu126"), (11.8, "cu118"))


class RepairError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run(argv: Sequence[str], *, cwd: Path | None = None, timeout: float = 7200,
        log: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(list(argv), cwd=str(cwd) if cwd else None, text=True,
                        capture_output=True, timeout=timeout, check=False)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("$ " + subprocess.list2cmdline(list(argv)) +
                       "\n\n[stdout]\n" + cp.stdout + "\n[stderr]\n" + cp.stderr,
                       encoding="utf-8")
    if check and cp.returncode:
        raise RepairError(f"command failed ({cp.returncode}): " +
                          subprocess.list2cmdline(list(argv)) + "\n" +
                          (cp.stderr or cp.stdout)[-2000:])
    return cp


def git_context(repo: Path) -> dict[str, Any]:
    def g(*args: str) -> str:
        cp = run(["git", "-C", str(repo), *args], timeout=60, check=False)
        return cp.stdout.strip() if cp.returncode == 0 else ""
    return {"root": str(repo), "head": g("rev-parse", "HEAD"),
            "branch": g("branch", "--show-current"),
            "dirty_porcelain": g("status", "--short")}


def parse_cuda_version(text: str) -> float | None:
    m = re.search(r"CUDA Version:\s*(\d+(?:\.\d+)?)", text, re.I)
    return float(m.group(1)) if m else None


def choose_cuda_wheel(version: float) -> str:
    for minimum, channel in CUDA_WHEELS:
        if version >= minimum:
            return channel
    raise RepairError(f"NVIDIA driver supports CUDA {version:.1f}; CUDA 11.8 or newer is required")


def nvidia_receipt() -> dict[str, Any]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        raise RepairError("nvidia-smi is unavailable")
    full = run([smi], timeout=30).stdout
    version = parse_cuda_version(full)
    if version is None:
        raise RepairError("nvidia-smi reported no CUDA compatibility version")
    rows = run([smi, "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader"], timeout=30).stdout
    return {"nvidia_smi": smi, "driver_cuda_max": version,
            "wheel_channel": choose_cuda_wheel(version),
            "gpus": [x.strip() for x in rows.splitlines() if x.strip()]}


def package_version(name: str) -> str | None:
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        return importlib.metadata.version(name)
    return None


def runtime_receipt() -> dict[str, Any]:
    out: dict[str, Any] = {"python": sys.executable, "python_version": sys.version.split()[0],
        "packages": {x: package_version(x) for x in ("torch", "torchaudio", "demucs")},
        "torch_importable": False, "demucs_importable": False, "cuda": False}
    try:
        import torch
        out.update(torch_importable=True, cuda=bool(torch.cuda.is_available()),
                   torch_cuda_version=getattr(torch.version, "cuda", None))
        if out["cuda"]:
            out["gpu_name"] = torch.cuda.get_device_name(0)
    except Exception as exc:
        out["torch_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import demucs  # noqa: F401
        import demucs.separate  # noqa: F401
        out["demucs_importable"] = True
    except Exception as exc:
        out["demucs_error"] = f"{type(exc).__name__}: {exc}"
    out["ready"] = bool(out["torch_importable"] and out["demucs_importable"] and out["cuda"])
    return out


def require_python() -> None:
    if not ((3, 10) <= sys.version_info[:2] <= (3, 13)):
        raise RepairError(f"unsupported Python {sys.version_info.major}.{sys.version_info.minor}")


def load_core(repo: Path):
    repo = repo.resolve()
    sys.path.insert(0, str(repo)) if str(repo) not in sys.path else None
    from earcrate.app import EarcrateCore
    core = EarcrateCore(); config = core.ensure_config()
    app = Path(sys.modules["earcrate.app"].__file__).resolve()
    if repo not in app.parents:
        raise RepairError(f"EarCrate imported from {app}, outside {repo}")
    return core, config, app


def live_db_path(core: Any) -> Path:
    row = core.conn().execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        raise RepairError("live SQLite path is unavailable")
    return Path(str(row[2])).resolve()


def active_source_rows(db: sqlite3.Connection, profile: str) -> list[dict[str, Any]]:
    db.row_factory = sqlite3.Row
    rows = db.execute("""SELECT f.id file_id,f.path,f.sha256 file_sha256,f.audio_sha256,
      f.duration_s,MAX(a.score) best_score,
      SUM(CASE WHEN a.ear_role='DRUM_BREAK' THEN 1 ELSE 0 END) drum_atoms,
      SUM(CASE WHEN a.ear_role='BASS_RIFF' THEN 1 ELSE 0 END) bass_atoms,
      COUNT(*) approved_atoms
      FROM ear_atoms a JOIN loops l ON l.id=a.loop_id JOIN files f ON f.id=a.file_id
      WHERE a.taste_profile=? AND a.status='approved' AND COALESCE(f.present,1)=1
        AND f.audio_sha256_scope='full' AND f.audio_sha256 IS NOT NULL
        AND COALESCE(l.source_audio_generation,0)=COALESCE(f.audio_generation,0)
        AND (l.source_audio_sha256=f.audio_sha256 OR
             (l.source_audio_sha256 IS NULL AND COALESCE(f.audio_generation,0)=0))
        AND f.duration_s BETWEEN 12 AND 180 GROUP BY f.id
      ORDER BY f.duration_s ASC LIMIT 512""", (profile,)).fetchall()
    return [dict(row) for row in rows]


def select_probe_source(db: sqlite3.Connection, profile: str, explicit: str = "") -> dict[str, Any]:
    rows = active_source_rows(db, profile)
    if explicit:
        wanted = os.path.normcase(str(Path(explicit).expanduser().resolve()))
        rows = [r for r in rows if os.path.normcase(str(Path(r["path"]).resolve())) == wanted]
    if not rows:
        raise RepairError("no eligible current approved source exists for the provider proof")
    rank = lambda r: (0 if r["drum_atoms"] and r["bass_atoms"] else 1,
                      0 if r["drum_atoms"] else 1, 0 if r["bass_atoms"] else 1,
                      float(r["duration_s"]), -int(r["approved_atoms"]),
                      -float(r["best_score"]), str(r["file_id"]))
    for row in sorted(rows, key=rank):
        path = Path(str(row["path"])).resolve()
        if not path.is_file():
            continue
        actual = sha256_file(path); expected = str(row.get("file_sha256") or "")
        if expected and actual != expected:
            continue
        selected = dict(row); selected.update(path=str(path), verified_file_sha256=actual)
        return selected
    raise RepairError("eligible source files were missing or failed exact file-hash custody")


def public_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {"file_id": source.get("file_id"), "filename": Path(str(source["path"])).name,
            "duration_s": source.get("duration_s"), "audio_sha256": source.get("audio_sha256"),
            "file_sha256": source.get("verified_file_sha256"),
            "approved_atoms": source.get("approved_atoms"),
            "drum_atoms": source.get("drum_atoms"), "bass_atoms": source.get("bass_atoms")}


def resolve_stem(provider: Any, providers: Any, ref: Any) -> tuple[bytes, dict[str, Any]]:
    path = Path(str(ref))
    if path.is_file():
        before = sha256_file(path); data = path.read_bytes(); after = sha256_file(path)
        if before != after:
            raise RepairError("stem path changed during read")
        return data, {"kind": "path", "bytes": len(data), "sha256": after}
    stores = [("provider_store", getattr(provider, "store", None))]
    with contextlib.suppress(Exception):
        stores.append(("shared_store", providers.get("artifacts")))
    for label, store in stores:
        if store is None:
            continue
        with contextlib.suppress(Exception):
            got = store.get(str(ref))
            if got and got.get("data"):
                data = bytes(got["data"])
                return data, {"kind": label, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "meta": got.get("meta") or {}}
    raise RepairError(f"unresolved stem reference {ref!r}")


def read_audio(data: bytes):
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    if not audio.size or not np.isfinite(audio).all():
        raise RepairError("provider emitted empty or non-finite audio")
    return audio.astype(np.float64), int(sr)


def rms(x: Any) -> float:
    import numpy as np
    a = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(a*a))) if a.size else 0.0


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def corr(a: Any, b: Any) -> float:
    import numpy as np
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    n = min(a.size, b.size)
    a = a[:n] - np.mean(a[:n]); b = b[:n] - np.mean(b[:n])
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / den) if den > 1e-12 else 0.0


def provider_proof(repo: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    os.environ.update(EARCRATE_STEMS=PROVIDER, EARCRATE_DEMUCS_MODEL=MODEL,
                      EARCRATE_DEMUCS_SEGMENT_S="6.0", EARCRATE_DEMUCS_OVERLAP="0.10",
                      EARCRATE_DEMUCS_SHIFTS="0", EARCRATE_DEMUCS_PRECISION="fp32")
    import numpy as np
    import torch
    core, config, app = load_core(repo)
    providers = __import__("earcrate.providers", fromlist=["get"])
    provider = providers.get("stems", PROVIDER)
    if getattr(provider, "name", "") != PROVIDER or not torch.cuda.is_available():
        raise RepairError("live interpreter did not resolve a CUDA Demucs provider")
    path = Path(str(source["path"])); before = sha256_file(path)
    torch.cuda.reset_peak_memory_stats()
    result = dict(provider.separate(str(source["audio_sha256"]), str(path), list(ROLES)) or {})
    after = sha256_file(path)
    if before != after:
        raise RepairError("probe source changed during separation")
    refs = dict(result.get("stems") or {}); missing = [r for r in ROLES if not refs.get(r)]
    if not result.get("available") or missing:
        raise RepairError("required stems missing: " + ", ".join(missing or ["provider unavailable"]))
    arrays: dict[str, Any] = {}; artifacts: dict[str, Any] = {}; rates: set[int] = set()
    for role in ROLES:
        data, receipt = resolve_stem(provider, providers, refs[role]); audio, sr = read_audio(data)
        arrays[role] = audio.mean(axis=1); rates.add(sr)
        artifacts[role] = {**receipt, "frames": int(audio.shape[0]), "channels": int(audio.shape[1]),
                           "sample_rate": sr, "rms_dbfs": round(db(rms(audio)), 3),
                           "peak": round(float(np.max(np.abs(audio))), 6)}
    if len(rates) != 1:
        raise RepairError(f"stem sample rates disagree: {sorted(rates)}")
    sr = next(iter(rates))
    from earcrate.analyze.decode import decode_audio
    mixture = decode_audio(path, sr).astype(np.float64)
    lengths = [mixture.size] + [arrays[r].size for r in ROLES]; n = min(lengths)
    if n < sr*10 or max(lengths)-min(lengths) > int(sr*.15):
        raise RepairError("provider stems are not sufficiently long and aligned")
    mixture = mixture[:n]; no_vocals = arrays["no_vocals"][:n]
    components = sum((arrays[r][:n] for r in ("drums", "bass", "other")), np.zeros(n))
    full_sum = arrays["vocals"][:n] + no_vocals
    component_db = db(rms(no_vocals-components)/max(rms(no_vocals),1e-12))
    residual_db = db(rms(mixture-full_sum)/max(rms(mixture),1e-12))
    correlation = corr(mixture, full_sum)
    audible = {r: rms(arrays[r][:n]) for r in ("drums", "bass", "other")}
    failures = []
    if component_db > -65: failures.append(f"instrumental component null {component_db:.2f} dB")
    if correlation < .90: failures.append(f"source/stem correlation {correlation:.4f}")
    if residual_db > -3: failures.append(f"source/stem residual {residual_db:.2f} dB")
    if rms(no_vocals) < 1e-8: failures.append("instrumental stem is silent")
    if sum(x >= 1e-8 for x in audible.values()) < 2: failures.append("fewer than two instrumental roles are audible")
    if torch.cuda.max_memory_allocated() <= 0 and not result.get("cached"):
        failures.append("cold separation allocated no CUDA memory")
    if failures: raise RepairError("; ".join(failures))
    return {"provider_ready": True, "provider": result.get("provider"),
            "provider_class": provider.__class__.__name__, "model_version": result.get("model_version") or MODEL,
            "cached": bool(result.get("cached")), "earcrate_app": str(app),
            "workspace_agent_root": str(config.agent_root), "artifacts": artifacts,
            "cuda": {"device": torch.cuda.get_device_name(0),
                     "torch_cuda_version": getattr(torch.version,"cuda",None),
                     "max_memory_allocated": int(torch.cuda.max_memory_allocated())},
            "metrics": {"aligned_seconds": round(n/sr,3),
                        "max_length_delta_seconds": round((max(lengths)-min(lengths))/sr,6),
                        "no_vocals_component_error_relative_db": round(component_db,3),
                        "source_sum_residual_relative_db": round(residual_db,3),
                        "source_sum_correlation": round(correlation,6)}}


def config_path(config: Any) -> Path:
    path = (Path(config.agent_root)/"config.json").resolve()
    if not path.is_file(): raise RepairError(f"workspace config missing: {path}")
    return path


def activate_provider(path: Path, archive: Path) -> dict[str, Any]:
    original = path.read_bytes(); value = json.loads(original.decode("utf-8-sig"))
    if not isinstance(value, dict): raise RepairError("workspace config is not an object")
    backup = archive/f"config-before-{stamp()}.json"; backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("xb") as f: f.write(original); f.flush(); os.fsync(f.fileno())
    before = str(value.get("stem_provider") or "noop"); value["stem_provider"] = PROVIDER
    try:
        write_json(path, value)
        return {"path": str(path), "backup": str(backup), "before": before, "after": PROVIDER,
                "before_sha256": hashlib.sha256(original).hexdigest(),
                "after_sha256": sha256_file(path)}
    except Exception:
        restore_config(path, backup)
        raise


def restore_config(path: Path, backup: Path) -> None:
    data = backup.read_bytes(); tmp = path.with_name(f".{path.name}.restore.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally: tmp.unlink(missing_ok=True)


def verify_config(repo: Path) -> dict[str, Any]:
    core, config, app = load_core(repo)
    providers = __import__("earcrate.providers", fromlist=["get","stem_capability"])
    configured = str(getattr(config,"stem_provider","noop")); provider = providers.get("stems",configured)
    capability = dict(providers.stem_capability())
    ready = bool(configured==PROVIDER and getattr(provider,"name","")==PROVIDER and capability.get("ready"))
    if not ready: raise RepairError("fresh EarCrate import did not resolve a ready Demucs provider")
    return {"provider_ready": True, "configured": configured, "resolved": getattr(provider,"name",""),
            "provider_class": provider.__class__.__name__, "capability": capability,
            "python": sys.executable, "earcrate_app": str(app)}


def install_runtime(python: Path, channel: str, receipt: Path, repo: Path) -> list[dict[str, Any]]:
    commands = [
      ("pytorch_cuda", [str(python),"-m","pip","install","--disable-pip-version-check","--upgrade",
        f"torch=={TORCH_VERSION}",f"torchaudio=={TORCH_VERSION}","--index-url",
        f"https://download.pytorch.org/whl/{channel}","--extra-index-url","https://pypi.org/simple"]),
      ("demucs", [str(python),"-m","pip","install","--disable-pip-version-check","--upgrade",
        f"demucs=={DEMUCS_VERSION}"])]
    stages=[]
    for i,(name,argv) in enumerate(commands,1):
        started=now(); cp=run(argv,cwd=repo,log=receipt/"private"/f"install-{i:02d}-{name}.log")
        stages.append({"name":name,"started_at":started,"finished_at":now(),"returncode":cp.returncode})
    return stages


def child(python: Path, script: Path, phase: str, output: Path, *args: str,
          timeout: float=7200) -> dict[str, Any]:
    cp=run([str(python),str(script),"--phase",phase,"--output",str(output),*args],
           timeout=timeout,log=output.parent/"private"/f"{phase}.log",check=False)
    if not output.is_file(): raise RepairError(f"{phase} wrote no JSON")
    value=json.loads(output.read_text(encoding="utf-8"))
    if cp.returncode or not value.get("ok",True): raise RepairError(value.get("error") or f"{phase} failed")
    return value


def refuse(receipt: Path, started: str, exc: Exception, rollback: Any=None) -> int:
    private=receipt/"private"; private.mkdir(parents=True,exist_ok=True)
    (private/"traceback.txt").write_text(traceback.format_exc(),encoding="utf-8")
    value={"kind":"earcrate_stem_provider_repair","schema_version":1,
      "result":"refused_stem_provider_activation","provider_ready":False,
      "robi_v31_authorized":False,"robi_touched":False,"ace_step_started":False,
      "started_at":started,"finished_at":now(),"failure":f"{type(exc).__name__}: {exc}",
      "configuration_rollback":rollback,"receipt_dir":str(receipt)}
    write_json(receipt/"REFUSAL.json",value); print(json.dumps(value,indent=2),file=sys.stderr); return 1


def orchestrate(ns: argparse.Namespace) -> int:
    require_python(); script=Path(__file__).resolve(); repo=Path(ns.repo).resolve() if ns.repo else script.parent.parent
    python=Path(ns.python).resolve() if ns.python else Path(sys.executable).resolve()
    if python != Path(sys.executable).resolve(): raise RepairError("run with the target EarCrate interpreter")
    if not ns.apply:
        print(json.dumps({"dry_run":True,"apply_required":True,"robi_touched":False,
          "would_install":[f"torch=={TORCH_VERSION}",f"torchaudio=={TORCH_VERSION}",f"demucs=={DEMUCS_VERSION}"],
          "would_activate":"stem_provider=demucs only after a current-source CUDA proof"},indent=2)); return 0
    receipt=Path(ns.receipt_dir).resolve() if ns.receipt_dir else Path.home()/"Downloads"/f"EarCrate-stem-provider-repair-{stamp()}"
    receipt.mkdir(parents=True,exist_ok=False); started=now()
    context={"kind":"earcrate_stem_provider_repair","schema_version":1,"started_at":started,
      "repository":git_context(repo),"python":str(python),"profile":ns.profile,
      "robi_touched":False,"ace_step_started":False}; write_json(receipt/"CONTEXT.json",context)
    try:
        core,config,app=load_core(repo); db_path=live_db_path(core); cfg=config_path(config)
        db=sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro",uri=True)
        try: source=select_probe_source(db,ns.profile,ns.source)
        finally: db.close()
        state={"repo":str(repo),"python":str(python),"config":str(cfg),"agent_root":str(config.agent_root),
               "source":source,"started_at":started,"receipt":str(receipt)}
        write_json(receipt/"private"/"state.json",state)
        context.update(earcrate_app=str(app),database=str(db_path),workspace_config=str(cfg),
                       provider_before=str(getattr(config,"stem_provider","noop")),probe_source=public_source(source))
        write_json(receipt/"CONTEXT.json",context)
        nvidia=nvidia_receipt(); write_json(receipt/"stage-01-nvidia.json",{"ok":True,**nvidia})
        before=child(python,script,"runtime",receipt/"stage-02-runtime-before.json",timeout=120)
        stages=[] if before.get("ready") else install_runtime(python,nvidia["wheel_channel"],receipt,repo)
        write_json(receipt/"stage-03-install.json",{"ok":True,"skipped":not stages,"stages":stages,
          "pinned":{"torch":TORCH_VERSION,"torchaudio":TORCH_VERSION,"demucs":DEMUCS_VERSION},
          "pytorch_wheel_channel":nvidia["wheel_channel"]})
        after=child(python,script,"runtime",receipt/"stage-04-runtime-after.json",timeout=120)
        if not after.get("ready"): raise RepairError("installed runtime is not CUDA-ready")
        proof=child(python,script,"proof",receipt/"stage-05-provider-proof.json",
                    "--state",str(receipt/"private"/"state.json"),timeout=7200)
        change=activate_provider(cfg,Path(config.agent_root)/"archive"/"stem_provider_repair")
        rollback=None
        try:
            write_json(receipt/"stage-06-config-activation.json",{"ok":True,**change})
            verification=child(python,script,"verify",receipt/"stage-07-fresh-verification.json",
                               "--repo",str(repo),timeout=300)
            result={"kind":"earcrate_stem_provider_repair","schema_version":1,
              "result":"stem_provider_activated","provider_ready":True,"robi_v31_authorized":True,
              "robi_touched":False,"ace_step_started":False,"started_at":started,"finished_at":now(),
              "repository":context["repository"],"runtime":after,"nvidia":nvidia,
              "probe_source":public_source(source),"provider_proof":{
                "provider":proof.get("provider"),"provider_class":proof.get("provider_class"),
                "model_version":proof.get("model_version"),"cached":proof.get("cached"),
                "cuda":proof.get("cuda"),"metrics":proof.get("metrics"),
                "artifact_roles":sorted((proof.get("artifacts") or {}).keys())},
              "configuration":change,"verification":verification,
              "next":"run the unchanged Robi V3.1 campaign once from a fresh extraction",
              "receipt_dir":str(receipt)}
            write_json(receipt/"RESULT.json",result); print(json.dumps(result,indent=2)); return 0
        except Exception:
            restore_config(Path(change["path"]),Path(change["backup"])); rollback={"restored":True}
            raise
    except Exception as exc:
        return refuse(receipt,started,exc,locals().get("rollback"))


def main(argv: Sequence[str]|None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--apply",action="store_true")
    p.add_argument("--repo",default=""); p.add_argument("--python",default="")
    p.add_argument("--profile",default=PROFILE); p.add_argument("--source",default="")
    p.add_argument("--receipt-dir",default=""); p.add_argument("--phase",choices=("runtime","proof","verify"))
    p.add_argument("--output",type=Path); p.add_argument("--state",type=Path)
    ns=p.parse_args(argv)
    if not ns.phase: return orchestrate(ns)
    try:
        if ns.phase=="runtime": value=runtime_receipt()
        elif ns.phase=="verify": value=verify_config(Path(ns.repo).resolve())
        else:
            state=json.loads(ns.state.read_text(encoding="utf-8")); value=provider_proof(Path(state["repo"]),state["source"])
        value={"ok":True,**value}; write_json(ns.output,value); print(json.dumps(value,indent=2)); return 0
    except Exception as exc:
        value={"ok":False,"error":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc()}
        if ns.output: write_json(ns.output,value)
        print(json.dumps(value,indent=2),file=sys.stderr); return 1


if __name__=="__main__":
    raise SystemExit(main())
