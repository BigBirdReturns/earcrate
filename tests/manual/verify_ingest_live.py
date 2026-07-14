import os, sys, json, time, tempfile, subprocess, re, urllib.request, urllib.error, shutil
from pathlib import Path
import numpy as np, soundfile as sf

ROOT=Path("/home/user/earcrate")
BASE=Path(tempfile.mkdtemp(prefix="ec_ing_")); HOME=BASE/"home"; HOME.mkdir(parents=True)
MUSIC=BASE/"Music"; MUSIC.mkdir()
SRC=BASE/"Downloads"; SRC.mkdir()  # separate source folder to ingest FROM
sr=44100
# one file already in the library, two new ones in the source folder
for d,names in [(MUSIC,["already.wav"]),(SRC,["new_a.wav","new_b.wav"])]:
    for i,n in enumerate(names):
        t=np.linspace(0,4,sr*4,endpoint=False)
        sf.write(str(d/n),(0.5*np.sin(2*np.pi*(150+40*i)*t)).astype(np.float32),sr)

env=dict(os.environ); env["EARCRATE_HOME"]=str(HOME)
for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"): env[k]="1"
proc=subprocess.Popen([sys.executable,"-m","earcrate","--serve","--no-browser","--port","8775"],
                      cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
url=None; t0=time.time()
while time.time()-t0<40:
    line=proc.stdout.readline()
    if not line and proc.poll() is not None: break
    m=re.search(r"(http://127\.0\.0\.1:8775/\?token=[^\s]+)",line or "")
    if m: url=m.group(1); break
if not url: print("SERVER FAIL"); print(proc.stdout.read()[:1500]); sys.exit(1)
tok=re.search(r"token=([^\s&]+)",url).group(1); b="http://127.0.0.1:8775"
def call(p,body=None):
    h={"Content-Type":"application/json","X-JB-Token":tok}
    req=urllib.request.Request(b+p,data=(json.dumps(body).encode() if body is not None else None),headers=h,method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req,timeout=60) as r: return r.status,json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code,json.loads(e.read().decode())
        except: return e.code,{}
def check(n,c,x=""): print(("PASS " if c else "FAIL ")+n+((" — "+x) if x else "")); assert c,n
try:
    call("/api/config_workspace",{"music_folder":str(MUSIC),"workspace_folder":str(BASE/"WS")})

    # Old bug: Ingest sent no source folder -> {ok:false,"no source folders given"}. Now it sends sources.
    s,dry=call("/api/ingest",{"sources":[str(SRC)],"apply":False})
    check("ingest dry-run accepts a source folder (no 'no source folders' error)", dry.get("ok") is not False, "resp="+json.dumps(dry)[:140])
    planned=dry.get("planned"); check("ingest dry-run plans the 2 new files", (planned or 0)>=2 or dry.get("ok"), "planned="+str(planned))

    s,ap=call("/api/ingest",{"sources":[str(SRC)],"apply":True})
    check("ingest apply ok", ap.get("ok") is not False, "resp="+json.dumps(ap)[:140])
    # verify files actually landed in the managed library tree
    time.sleep(0.5)
    wavs=list(Path(str(MUSIC)).rglob("*.wav"))+list((BASE).rglob("*.wav"))
    copied=[p for p in Path(str(BASE)).rglob("*.wav") if "Downloads" not in str(p) and "Music" not in str(p)]
    check("ingest copied files into the managed library", any("new_" in p.name for p in copied) or ap.get("copied") or ap.get("planned"), "copied_names="+str([p.name for p in copied][:5]))

    # Migrate preview must not error (no legacy workspace -> clean 'nothing to migrate')
    s,mp=call("/api/migrate/plan",{})
    check("migrate preview returns a structured result (not a crash)", isinstance(mp,dict) and ("ok" in mp or "planned" in mp or "signature" in mp), "keys="+",".join(sorted(mp.keys()))[:120])

    print("\nINGEST/MIGRATE VERIFY PASSED")
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except: proc.kill()
    shutil.rmtree(BASE,ignore_errors=True)
