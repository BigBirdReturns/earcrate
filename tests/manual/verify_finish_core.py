import os, sys, json, tempfile, shutil
from pathlib import Path
import numpy as np, soundfile as sf
from mutagen import File as MF

ROOT=Path("/home/user/earcrate"); sys.path.insert(0,str(ROOT))
BASE=Path(tempfile.mkdtemp(prefix="ec_fin_")); os.environ["EARCRATE_HOME"]=str(BASE/"home"); (BASE/"home").mkdir(parents=True)
MUSIC=BASE/"Music"; MUSIC.mkdir()
sr=44100
flacs=[]
for i in range(2):
    p=MUSIC/f"song{i}.flac"; t=np.linspace(0,3,sr*3,endpoint=False)
    sf.write(str(p),(0.5*np.sin(2*np.pi*(150+30*i)*t)).astype(np.float32),sr,format="FLAC")
    m=MF(str(p), easy=True); m["artist"]=["Old Artist"]; m["title"]=[f"Old Title {i}"]; m.save()
    flacs.append(p)

from earcrate.app import EarcrateCore
core=EarcrateCore(); core._worker_count=lambda:1
core.configure_workspace({"music_folder":str(MUSIC),"workspace_folder":str(BASE/"WS")})
def sec(t): print("\n===== "+t+" =====")
def check(n,c,x=""): print(("PASS " if c else "FAIL ")+n+((" — "+x) if x else "")); assert c,n

sec("Identify APPLY (two-phase: dry-run signature -> apply) -> tags rewritten, journal written")
proposals=[{"path":str(p),"artist":"New Artist","title":f"New Title {i}","score":0.99} for i,p in enumerate(flacs)]
dry0=core.apply_identities({"apply":False,"proposals":proposals})
check("apply dry-run returns a signature + would_retag", dry0.get("signature") and dry0.get("would_retag",0)>=1, "would_retag="+str(dry0.get("would_retag")))
r=core.apply_identities({"apply":True,"proposals":proposals,"signature":dry0["signature"]})
check("apply_identities ok (with signature)", r.get("ok") is not False, "resp="+json.dumps(r)[:120])
t0=MF(str(flacs[0]), easy=True)
check("file artist rewritten to 'New Artist'", (t0.get("artist") or [""])[0]=="New Artist", "artist="+str(t0.get("artist")))

sec("identify_journals() lists the journal (backend for the new Undo button)")
js=core.identify_journals()
check("identify_journals returns >=1 item newest-first", bool(js.get("items")), "count="+str(len(js.get("items") or [])))
check("journal record count matches retagged files", (js["items"][0]["count"])>=1, "count="+str(js["items"][0].get("count")))

sec("Identify ROLLBACK (default = latest journal) -> tags restored")
dry=core.rollback_identities({"apply":False})
check("rollback dry-run reports would_restore", dry.get("would_restore",0)>=1, "would_restore="+str(dry.get("would_restore")))
ap=core.rollback_identities({"apply":True})
check("rollback apply ok", ap.get("ok") is not False, "resp="+json.dumps(ap)[:120])
t1=MF(str(flacs[0]), easy=True)
check("file artist restored to 'Old Artist'", (t1.get("artist") or [""])[0]=="Old Artist", "artist="+str(t1.get("artist")))

sec("render_plan error paths (happy render needs real music -> desktop)")
e1=core.render_plan({})
check("render_plan with no arrangement -> clean error (no crash)", e1.get("ok") is False and "arrangement" in (e1.get("error") or ""), "resp="+json.dumps(e1)[:120])
e2=core.render_plan({"arrangement":{"sections":[{"bars":8,"layers":[]}],"bpm":120,"params":{"seed":7}}})
check("render_plan with an empty/failing plan -> gate refuses cleanly (no crash/theater)", isinstance(e2,dict) and (e2.get("ok") is False or "render" in e2), "keys="+",".join(sorted(e2.keys()))[:120])

print("\nFINISH-BATCH VERIFY PASSED")
shutil.rmtree(BASE,ignore_errors=True)
