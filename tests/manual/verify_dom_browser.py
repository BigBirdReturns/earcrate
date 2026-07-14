import os, sys, json, time, tempfile, subprocess, re, shutil
from pathlib import Path
import numpy as np, soundfile as sf
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH","/opt/pw-browsers")
from playwright.sync_api import sync_playwright

ROOT=Path("/home/user/earcrate")
BASE=Path(tempfile.mkdtemp(prefix="ec_dom_")); HOME=BASE/"home"; HOME.mkdir(parents=True)
MUSIC=BASE/"Music"; MUSIC.mkdir()
sr=44100
for i,f0 in enumerate([146,196]):
    t=np.linspace(0,6,sr*6,endpoint=False)
    sf.write(str(MUSIC/f"t{i}.wav"),(0.5*np.sin(2*np.pi*f0*t)).astype(np.float32),sr)

env=dict(os.environ); env["EARCRATE_HOME"]=str(HOME)
for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"): env[k]="1"
proc=subprocess.Popen([sys.executable,"-m","earcrate","--serve","--no-browser","--port","8770"],
                      cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
url=None; t0=time.time()
while time.time()-t0<40:
    line=proc.stdout.readline()
    if not line and proc.poll() is not None: break
    m=re.search(r"(http://127\.0\.0\.1:8770/\?token=[^\s]+)", line or "")
    if m: url=m.group(1); break
if not url: print("SERVER FAIL"); print(proc.stdout.read()[:1500]); sys.exit(1)
print("server up")

def check(n,c,extra=""): print(("PASS " if c else "FAIL ")+n+((" — "+extra) if extra else "")); assert c,n

try:
    with sync_playwright() as p:
        br=p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"]) \
            if Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").exists() else p.chromium.launch(args=["--no-sandbox"])
        pg=br.new_page()
        toasts=[]
        pg.on("console", lambda m: None)
        pg.goto(url, wait_until="networkidle")
        time.sleep(1.5)  # let boot() async run first-run routing

        # 1. First-run: should land on Setup (scr_setup visible), not Play
        setup_hidden=pg.eval_on_selector("#scr_setup","e=>e.classList.contains('hidden')")
        play_hidden=pg.eval_on_selector("#scr_play","e=>e.classList.contains('hidden')")
        check("first-run routes to Setup (setup visible)", setup_hidden is False, "setup_hidden="+str(setup_hidden))
        check("first-run: Play screen hidden", play_hidden is True)

        # 2. Setup fields present; music prefilled from defaults
        music_val=pg.input_value("#setMusic")
        check("Setup music field prefilled", bool(music_val), "music="+music_val[:40])

        # 3. Fill + Save workspace; expect a success toast
        pg.fill("#setMusic", str(MUSIC))
        pg.fill("#setWs", str(BASE/"WS"))
        pg.click("text=Save workspace")
        time.sleep(1.0)
        toast_txt=pg.eval_on_selector("#toast","e=>e.textContent")
        check("Save workspace shows success toast", "saved" in toast_txt.lower(), "toast="+toast_txt[:60])

        # 4. After save, Setup workspace field should equal the ROOT (not /work) on re-render
        pg.evaluate("renderSetup()"); time.sleep(0.6)
        ws_val=pg.input_value("#setWs")
        check("workspace field binds to ROOT after save (no /work nesting)", not ws_val.rstrip('/').endswith("work"), "ws="+ws_val)

        # 5. Heartbeat honesty: inject a status with last_error, run pollStatus, header must go to ERROR
        pg.evaluate("""window.__origFetch=window.fetch; window.fetch=async(u,o)=>{ if(String(u).includes('/api/status')) return new Response(JSON.stringify({busy:false,progress:0,message:'x',last_error:'simulated failure'}),{status:200,headers:{'Content-Type':'application/json'}}); return window.__origFetch(u,o); };""")
        pg.evaluate("pollStatus(true)"); time.sleep(0.6)
        online_txt=pg.eval_on_selector("#onlineTxt","e=>e.textContent")
        check("heartbeat reflects last_error (shows ERROR, not SYSTEM ONLINE)", online_txt.strip()=="ERROR", "onlineTxt="+online_txt)
        pg.evaluate("window.fetch=window.__origFetch")

        # 6. Global error surfacing: a rejected api() call should toast
        pg.evaluate("el('toast').textContent=''; setTimeout(()=>{ api('/api/nonexistent_endpoint_zzz'); }, 10);"); time.sleep(1.2)
        toast2=pg.eval_on_selector("#toast","e=>e.textContent")
        check("failed request surfaces a toast (not silent)", toast2.strip().startswith("✕") or "not found" in toast2.lower() or "404" in toast2, "toast="+toast2[:60])

        br.close()
    print("\nALL DOM CHECKS PASSED")
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except: proc.kill()
    shutil.rmtree(BASE, ignore_errors=True)
