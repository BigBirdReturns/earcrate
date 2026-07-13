import os, sys, json, time, tempfile, subprocess, re, urllib.request, urllib.error, shutil
from pathlib import Path
import numpy as np, soundfile as sf

ROOT = Path("/home/user/earcrate")
BASE = Path(tempfile.mkdtemp(prefix="ec_http_"))
HOME = BASE/"home"; HOME.mkdir(parents=True)
MUSIC = BASE/"Music"; MUSIC.mkdir()
sr=44100
for i,f0 in enumerate([146,196,110]):
    t=np.linspace(0,10,sr*10,endpoint=False); env=0.5+0.5*np.sign(np.sin(2*np.pi*2*t))
    y=(0.6*np.sin(2*np.pi*f0*t)+0.2*np.random.RandomState(f0).randn(len(t)))*env
    sf.write(str(MUSIC/f"t{i}.wav"),(0.9*y/np.max(np.abs(y))).astype(np.float32),sr)

env=dict(os.environ); env["EARCRATE_HOME"]=str(HOME)
for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"): env[k]="1"
proc=subprocess.Popen([sys.executable,"-m","earcrate","--serve","--no-browser","--port","8765"],
                      cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
url=None
t0=time.time()
while time.time()-t0<40:
    line=proc.stdout.readline()
    if not line:
        if proc.poll() is not None: break
        continue
    if "running on" in line:
        m=re.search(r"(http://127\.0\.0\.1:\d+/\?token=[^\s]+)", line);
        if m: url=m.group(1); break
if not url:
    print("SERVER FAILED TO START"); print(proc.stdout.read()[:2000]); sys.exit(1)
token=re.search(r"token=([^\s&]+)", url).group(1)
b="http://127.0.0.1:8765"
print("server up; token captured")

def call(path, body=None, tok=token):
    headers={"Content-Type":"application/json"}
    if tok is not None: headers["X-JB-Token"]=tok
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(b+path, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ")+name+((" — "+extra) if extra else ""));
    assert cond, name

try:
    st,page=(None,None)
    req=urllib.request.Request(b+"/");
    with urllib.request.urlopen(req,timeout=20) as r: html=r.read().decode(); pst=r.status
    check("GET / serves the app", pst==200 and "EarCrate" in html or "earcrate" in html.lower())

    s,dp=call("/api/defaults")
    check("first-run: /api/defaults configured is null", dp.get("configured") is None, "configured="+str(dp.get('configured')))
    check("first-run: configured_workspace null pre-config", dp.get("configured_workspace") is None)
    check("defaults exposes configured_workspace key", "configured_workspace" in dp)

    s,noauth=call("/api/status", tok=None)  # GET with no token: status is not under /api guard? check token gate on a POST
    s2,badtok=call("/api/doctor", {}, tok="wrong")
    check("token gate: bad token on POST /api/doctor -> 403", s2==403, "status="+str(s2))

    s,cw=call("/api/config_workspace", {"music_folder":str(MUSIC),"workspace_folder":str(BASE/"WS")})
    check("config_workspace saves", cw.get("ok") is True and cw.get("config"), "resp="+json.dumps(cw)[:120])
    wr=cw["config"]["working_root"]

    s,dp2=call("/api/defaults")
    check("after save: configured set", dp2.get("configured") is not None)
    check("after save: configured_workspace = workspace ROOT (not /work)",
          dp2.get("configured_workspace")==str(Path(wr).parent), "cw="+str(dp2.get('configured_workspace')))

    # re-save via the /work subdir (old frontend bug) must NOT nest
    s,cw2=call("/api/config_workspace", {"music_folder":str(MUSIC),"workspace_folder":wr})
    check("re-save with /work does not nest", cw2["config"]["working_root"]==wr, "wr2="+cw2["config"]["working_root"])

    s,doc=call("/api/doctor", {})
    checks=doc.get("checks") or []
    ff=[c for c in checks if "ffmpeg" in (c.get("name","").lower())]
    check("doctor returns checks[] with ffmpeg", bool(ff), "ffmpeg ok="+str(ff[0].get('ok') if ff else None))

    s,pf=call("/api/preflight", {"taste_profile":"girl_talk_v1","target_seconds":120})
    check("preflight exposes ready + warnings (frontend contract)", ("ready" in pf and "warnings" in pf) or pf.get("ok") is False, "keys="+",".join(sorted(pf.keys()))[:120])

    s,pl=call("/api/playlist/propose", {"name":"http pl","query":"","target_minutes":30})
    check("playlist entries is int", isinstance(pl.get("entries"), int), "entries="+str(pl.get('entries')))

    s,stt=call("/api/status")
    check("/api/status has busy+last_error fields", "busy" in stt and "last_error" in stt, "busy="+str(stt.get('busy')))

    print("\nALL HTTP CHECKS PASSED")
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except: proc.kill()
    shutil.rmtree(BASE, ignore_errors=True)
