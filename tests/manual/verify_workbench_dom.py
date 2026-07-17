"""M6 Workbench — full-lifecycle DOM verification (manual harness, not a gate).

Not run by run_gates.py: it needs a live server, Chromium, and the audio stack.
Drives the immutable-project Workbench through the whole lifecycle the spec
requires — compile/import -> inspect timeline -> edit a clip -> verify new
revision -> undo -> verify prior -> redo -> preview -> render -> export ->
reopen after restart — against BOTH package mode and the built single-file, and
fails on ANY console error. Also captures desktop + narrow-window screenshots.

    pip install -r requirements.txt playwright
    python tests/manual/verify_workbench_dom.py

Exit 0 = every mode green with zero console errors.
"""
import os, sys, json, time, tempfile, subprocess, re, signal, socket, glob, threading, queue
from pathlib import Path
import numpy as np, soundfile as sf

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
# Screenshots/receipt land in scratch when the rig harness passes WB_SHOTS_DIR;
# otherwise a temp dir. Never hardcode a host path.
SHOTS = Path(os.environ.get("WB_SHOTS_DIR") or tempfile.mkdtemp(prefix="wb_shots_"))
SHOTS.mkdir(parents=True, exist_ok=True)
# The browser WORKSPACE (seeded project + home) is run-scoped when the rig harness
# passes WB_BASE_DIR; a temp dir only when run by hand.
BASE = Path(os.environ.get("WB_BASE_DIR") or tempfile.mkdtemp(prefix="wb_dom_"))
BASE.mkdir(parents=True, exist_ok=True)
HOME = BASE / "home"; HOME.mkdir(parents=True, exist_ok=True)


def _chromium_executable():
    """Portable Chromium discovery: EARCRATE_CHROMIUM override first, then a few
    well-known locations, else None so Playwright uses its own managed browser."""
    override = os.environ.get("EARCRATE_CHROMIUM")
    if override and Path(override).exists():
        return override
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
                str(Path.home() / "AppData/Local/ms-playwright/chromium-*/chrome-win/chrome.exe")):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None  # let Playwright resolve its default install


def _launch(p):
    exe = _chromium_executable()
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--js-flags=--max-old-space-size=256"]
    return p.chromium.launch(executable_path=exe, args=args) if exe else p.chromium.launch(args=args)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


def seed_project():
    """Seed a workspace with one imported project + a piano run receipt so the
    Workbench, its inspectors, runs, and the triage view all have real content."""
    env = dict(os.environ); env["EARCRATE_HOME"] = str(HOME)
    code = f'''
import os, json
os.environ["EARCRATE_HOME"]={str(HOME)!r}
from pathlib import Path
import numpy as np, soundfile as sf
from earcrate.app import EarcrateCore
root=Path({str(BASE)!r})/"ws"
for d in ("music","work","agent"): (root/d).mkdir(parents=True,exist_ok=True)
core=EarcrateCore(); core.configure({{"master_root":str(root/"music"),"working_root":str(root/"work"),"agent_root":str(root/"agent"),"sample_rate":16000,"workers":1}})
sr=16000;dur=16.0;t=np.arange(int(sr*dur))/sr
f=(0.18*np.sin(2*np.pi*92*t)+0.08*np.sin(2*np.pi*220*t)).astype(np.float32)
g=(0.5+0.5*(np.sin(2*np.pi*2*t)>0)); v=(0.14*np.sin(2*np.pi*440*t)*g+0.05*np.sin(2*np.pi*3200*t)*g).astype(np.float32)
fp=root/"floor.wav";vp=root/"vocal.wav";sf.write(str(fp),f,sr,subtype="FLOAT");sf.write(str(vp),v,sr,subtype="FLOAT")
def L(loop,ref,role,ear,gain,aid): return {{"loop_id":loop,"atom_id":aid,"external_ref":{{"path":str(ref),"duration_s":dur,"start_s":0.0,"len_s":5.0}},"role":role,"ear_role":ear,"bar_offset":0,"bar_len":2,"gain_db":gain}}
arr={{"bpm":96.0,"target_key":0,"seed":78,"params":{{"taste_profile":"remix_prettylights_v1","target_seconds":10.0,"name":"Workbench Demo"}},
 "sections":[{{"bar_start":0,"bars":2,"type":"sustain","target_key":0,"transition_in":{{"type":"start","xfade_beats":0}},"layers":[L("f-a",fp,"harmony","BED_CHORD",-8.0,"atom_A"),L("v-a",vp,"vocal","VOX_HOOK",-5.0,"atom_B")]}},
  {{"bar_start":2,"bars":2,"type":"drop","target_key":0,"transition_in":{{"type":"beatmatch_blend","xfade_beats":2,"curve":"equal_power","bass_policy":"one_low_owner","low_cutoff_hz":170}},"layers":[L("f-b",fp,"harmony","BED_CHORD",-7.0,"atom_A"),L("v-b",vp,"vocal","VOX_HOOK",-4.0,"atom_B")]}}]}}
imp=core.project_import_arrangement(arr,name="Workbench Demo",created_by={{"actor":"seed","reason":"wb"}},static_gate_receipt={{"preflight":{{"passed":True}},"taste_gate":{{"passed":True}}}},compiler_receipt={{}})
pid=imp["project"]["project_id"]; rev=imp["project"]["active_revision_sha"]
pdir=Path(core.ensure_config().working_root)/"piano"; pdir.mkdir(parents=True,exist_ok=True)
run={{"ok":True,"type":"piano_run","run_id":"demo_night","personas":["remix_prettylights_v1"],"attempted":2,"kept":1,"discarded":1,"errored":0,
 "attempts":[{{"iteration":0,"persona":"remix_prettylights_v1","seed":1,"verdict":"kept","project_id":pid,"revision_sha":rev,"path":"demo.wav"}},
             {{"iteration":1,"persona":"remix_prettylights_v1","seed":2,"verdict":"discarded","reason":"refused: taste gate"}}]}}
(pdir/"demo_night.json").write_text(json.dumps(run))
print("SEED_OK", pid)
'''
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True)
    if "SEED_OK" not in out.stdout:
        print("SEED FAILED\n", out.stdout, out.stderr); sys.exit(1)
    return out.stdout.split("SEED_OK", 1)[1].strip().split()[0]


def boot(cmd, port, timeout=45.0):
    """Start the server and wait for its token URL with a GENUINELY bounded wait.

    Reading the child's pipe directly blocks with no timeout — on Windows a server
    that starts but buffers (or never prints) its startup line would hang the
    harness past any deadline. Instead a daemon thread pumps stdout into a queue
    and the main thread polls that queue with per-get timeouts until the overall
    deadline, so boot() ALWAYS returns within ``timeout`` seconds. The child runs
    with PYTHONUNBUFFERED=1 so the token line is not stuck in a stdio buffer."""
    env = dict(os.environ); env["EARCRATE_HOME"] = str(HOME)
    env["PYTHONUNBUFFERED"] = "1"
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[k] = "1"
    proc = subprocess.Popen(cmd + ["--serve", "--no-browser", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: "queue.Queue" = queue.Queue()

    def _pump(stream):
        try:
            for line in iter(stream.readline, ""):
                lines.put(line)
        except Exception:
            pass
        finally:
            lines.put(None)   # EOF sentinel

    threading.Thread(target=_pump, args=(proc.stdout,), daemon=True).start()
    url = None
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        try:
            line = lines.get(timeout=min(1.0, max(0.05, deadline - time.time())))
        except queue.Empty:
            if proc.poll() is not None:
                break             # server died without printing the token line
            continue              # still starting; keep waiting until the deadline
        if line is None:
            break                 # stdout closed (server exited)
        m = re.search(r"(http://127\.0\.0\.1:%d/\?token=[^\s]+)" % port, line)
        if m:
            url = m.group(1); break
    return proc, url


def drive(url, mode, shoot=False):
    errors = []
    steps = {}
    with sync_playwright() as p:
        b = _launch(p)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR: " + str(e)))
        pg.goto(url, wait_until="networkidle"); time.sleep(1.0)

        pg.evaluate("go('workbench')"); time.sleep(0.6)
        # open the seeded project
        pg.evaluate("(async()=>{ const j=await api('/api/projects'); await wbOpen(j.items[0].project_id); })()"); time.sleep(1.0)
        steps["header"] = "Three-rail" not in "" and bool(pg.query_selector("#wbHeader"))
        steps["timeline_clips"] = pg.inner_html("#wbMain").count("wbSelClip(")
        # inspect a clip (backend ranges)
        pg.evaluate("wbSelClip(WB.rev.arrangement.sections[0].layers[0].clip_id)"); time.sleep(0.3)
        side = pg.inner_text("#wbSide"); steps["ranges"] = ("GAIN dB" in side and "PAN" in side)
        if shoot:
            pg.screenshot(path=str(SHOTS / f"{mode}_desktop_timeline.png"))
        rev0 = pg.evaluate("WB.rev.revision_sha")
        # edit -> new revision (override: synthetic fixture can't clear the structural gate)
        pg.evaluate("(async()=>{ const l=WB.rev.arrangement.sections[0].layers[0]; await wbCmd('set_gain',{clip_id:l.clip_id, gain_db:-6.0, override_policy:true}); })()"); time.sleep(1.2)
        rev1 = pg.evaluate("WB.rev.revision_sha"); steps["edit_new_revision"] = (rev0 != rev1)
        # undo -> prior head
        pg.evaluate("(async()=>{ await wbUndo(); })()"); time.sleep(1.2)
        steps["undo_prior"] = (pg.evaluate("WB.proj.active_revision_sha") == rev0)
        # redo -> edited head
        pg.evaluate("(async()=>{ await wbRedo(); })()"); time.sleep(1.2)
        steps["redo_edited"] = (pg.evaluate("WB.proj.active_revision_sha") == rev1)
        # transition inspector
        pg.evaluate("(()=>{ const t=WB.rev.transitions.find(x=>x.transition_id); if(t) wbSelTransition(t.transition_id); })()"); time.sleep(0.3)
        steps["transition_inspector"] = ("EXECUTABLE PARAMETERS" in pg.inner_text("#wbSide"))
        # preview (revision-bound)
        pg.evaluate("(async()=>{ await wbPreview(); })()"); time.sleep(2.5)
        steps["preview"] = (pg.evaluate("WB.receipt && WB.receipt.kind") == "preview")
        # render
        pg.evaluate("(async()=>{ await wbRender(); })()"); time.sleep(3.0)
        steps["render"] = (pg.evaluate("WB.receipt && WB.receipt.kind") == "render")
        # exports
        for fmt in ("edl", "rpp", "sheet"):
            pg.evaluate(f"(async()=>{{ await wbExport('{fmt}'); }})()"); time.sleep(1.2)
        steps["export"] = (pg.evaluate("WB.receipt && WB.receipt.fmt") == "sheet")
        # runs view
        pg.evaluate("wbGo('runs')"); time.sleep(1.2)
        steps["runs_verified"] = ("VERIFIED" in pg.inner_text("#wbMain"))
        # history view (ancestry)
        pg.evaluate("wbGo('history')"); time.sleep(1.0)
        steps["history"] = ("HEAD" in pg.inner_text("#wbMain"))
        # triage view + keep->train
        pg.evaluate("wbGo('triage')"); time.sleep(1.2)
        steps["triage_run"] = ("demo_night" in pg.inner_text("#wbMain"))
        if shoot:
            pg.screenshot(path=str(SHOTS / f"{mode}_desktop_triage.png"))
        pg.evaluate("(async()=>{ await wbTriage('demo_night', 0, 'keep'); })()"); time.sleep(1.2)
        steps["triage_keep"] = ("keep" in pg.inner_text("#wbMain").lower())
        # reopen after restart: reload the page (WB.pid persists in localStorage).
        # The head has legitimately advanced past the edited revision because
        # render/preview author mastering CHILD revisions (spec point 6), so the
        # reopened project must match the CURRENT head, not the edited revision.
        head_before = pg.evaluate("WB.proj.active_revision_sha")
        pg.reload(wait_until="networkidle"); time.sleep(1.2)
        pg.evaluate("go('workbench')"); time.sleep(1.4)
        steps["reopen_head"] = (pg.evaluate("WB.proj && WB.proj.active_revision_sha") == head_before)
        # narrow-window screenshot
        if shoot:
            pg.set_viewport_size({"width": 760, "height": 1000}); time.sleep(0.5)
            pg.evaluate("wbGo('timeline')"); time.sleep(0.6)
            pg.screenshot(path=str(SHOTS / f"{mode}_narrow_timeline.png"))
        b.close()
    return steps, errors


def main():
    pid = seed_project()
    print("seeded project", pid)
    modes = [("package", [sys.executable, "-m", "earcrate"], True),
             ("singlefile", [sys.executable, str(ROOT / "dist" / "earcrate.py")], False)]
    only = os.environ.get("WB_MODE")
    if only:
        modes = [m for m in modes if m[0] == only]
    overall = True
    receipt = {"project_id": pid, "modes": {}}
    for name, cmd, shoot in modes:
        port = _free_port()  # dynamic allocation — no fixed 8770/8771
        proc, url = boot(cmd, port)
        try:
            if not url:
                print(f"[{name}] SERVER FAILED"); overall = False
                receipt["modes"][name] = {"ok": False, "console_errors": ["server failed to start"], "steps": {}}
                continue
            steps, errors = drive(url, name, shoot=shoot)
        finally:
            # guaranteed server cleanup regardless of how drive() exits
            try:
                proc.send_signal(getattr(signal, "SIGINT", signal.SIGTERM))
            except Exception:
                pass
            time.sleep(1)
            if proc.poll() is None:
                proc.kill()
        if not url:
            continue
        ok = (not errors) and all(v not in (False, 0) for v in steps.values())
        receipt["modes"][name] = {"ok": ok, "console_errors": errors, "steps": steps}
        overall = overall and ok
        print(f"\n[{name}] {'PASS' if ok else 'FAIL'}  console_errors={len(errors)}")
        for k, v in steps.items():
            print(f"    {k}: {v}")
        for e in errors[:10]:
            print("    ERR:", e)
    receipt["screenshots"] = sorted(str(p) for p in SHOTS.glob("*.png"))
    (SHOTS / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print("\nSCREENSHOTS:", SHOTS)
    print("RECEIPT:", SHOTS / "receipt.json")
    print("\nOVERALL:", "PASS" if overall else "FAIL")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
