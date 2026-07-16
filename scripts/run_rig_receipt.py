#!/usr/bin/env python3
"""EarCrate release-verification harness — the one-command Windows-rig receipt.

This is VERIFICATION TOOLING, not a product feature. It runs the mechanical and
rig-dependent stages that turn "engineered on the branch / green in cloud CI"
into "validated on the box against the real library", and it produces a
committable JSON receipt + a readable Markdown receipt that keep four kinds of
evidence strictly separate: cloud CI, Windows-rig mechanical proof, real-library
proof, GPU/provider proof, and a human musical verdict.

It changes NOTHING about the engine, compiler, renderer, personas, UI behavior,
defaults, or feature flags. It only reads, runs subprocesses, and writes under a
scratch directory (plus the two receipt files it is asked to commit).

One command. Checkpoints after every stage. Resumes by run_id. Refuses to append
results from a different git HEAD. See docs/RIG_RECEIPT_RUNBOOK.md.

    python scripts/run_rig_receipt.py --workspace <ws> --scratch <dir> \
        --profile remix_prettylights_v1 --real-seconds 120 --piano-iterations 3
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

RECEIPT_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]

# ---- status vocabulary (exactly these four for a stage) --------------------
PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
PENDING_MANUAL = "pending_manual"
PENDING = "pending"           # not yet run (internal)
STAGE_STATUSES = {PASSED, FAILED, SKIPPED, PENDING_MANUAL, PENDING}

# overall
COMPLETE = "complete"
INCOMPLETE = "incomplete"

# exit codes
EXIT_COMPLETE = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 2

# evidence tiers (kept separate so "code present" can never read as "done")
TIER_CLOUD = "cloud_ci_equivalent"
TIER_RIG = "rig_mechanical"
TIER_REAL = "real_library"
TIER_GPU = "gpu_provider"
TIER_HUMAN = "human_listening"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================
# Pure, unit-testable helpers
# ============================================================================
def classify_overall(stages: List[Dict[str, Any]]) -> str:
    """Reduce per-stage statuses to complete / failed / incomplete.

    - any failed -> failed
    - else any pending / pending_manual / skipped -> incomplete
    - else (every stage passed) -> complete
    Never lets skipped or pending_manual read as success.
    """
    statuses = [s.get("status") for s in stages]
    if any(s == FAILED for s in statuses):
        return FAILED
    if any(s in (PENDING, PENDING_MANUAL, SKIPPED) for s in statuses):
        return INCOMPLETE
    return COMPLETE


def exit_code_for(overall: str) -> int:
    return {COMPLETE: EXIT_COMPLETE, FAILED: EXIT_FAILED, INCOMPLETE: EXIT_INCOMPLETE}[overall]


def parse_gate_summary(text: str) -> Optional[Tuple[int, int]]:
    """Extract (passed, discovered) from a run_gates.py 'SUMMARY N/M gates passed'
    line. Returns None if not found — the count is discovered, never hardcoded."""
    m = re.search(r"SUMMARY\s+(\d+)\s*/\s*(\d+)\s+gates passed", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def redact_path(text: str, home: Optional[str]) -> str:
    """Redact user-home prefixes and token query strings for the committable
    receipt. Idempotent and safe on non-strings."""
    if not isinstance(text, str):
        return text
    out = text
    if home:
        for h in {home, home.replace("\\", "/"), home.rstrip("/\\")}:
            if h:
                out = out.replace(h, "~")
    out = re.sub(r"([?&]token=)[^&\s\"']+", r"\1REDACTED", out)
    out = re.sub(r"(/Users/|\\Users\\|/home/)[^/\\\s\"']+", r"\1USER", out)
    return out


def redact_tree(obj: Any, home: Optional[str]) -> Any:
    if isinstance(obj, dict):
        return {k: redact_tree(v, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_tree(v, home) for v in obj]
    return redact_path(obj, home)


def assert_scratch_safe(scratch: Path, music: Optional[Path]) -> None:
    """Path-safety: scratch must be explicit, absolute-resolvable, and must NOT be
    the music library or inside it (and the music library must not be inside
    scratch). Raises ValueError on any violation."""
    scratch = Path(scratch).expanduser().resolve()
    if not str(scratch):
        raise ValueError("scratch directory is required and must be explicit")
    if music is not None:
        music = Path(music).expanduser().resolve()
        if scratch == music:
            raise ValueError(f"scratch must not be the music library: {scratch}")
        if music in scratch.parents:
            raise ValueError(f"scratch must not live inside the music library: {scratch} ⊂ {music}")
        if scratch in music.parents:
            raise ValueError(f"the music library must not live inside scratch: {music} ⊂ {scratch}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git_head(cwd: Path) -> Dict[str, Any]:
    def _run(args):
        try:
            return subprocess.run(["git", "-C", str(cwd)] + args, capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except Exception:
            return ""
    head = _run(["rev-parse", "HEAD"])
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    upstream_sha = _run(["rev-parse", "@{u}"]) if upstream else ""
    dirty_out = _run(["status", "--porcelain"])
    return {"head": head, "branch": branch, "upstream": upstream,
            "upstream_sha": upstream_sha, "dirty": bool(dirty_out.strip()),
            "dirty_files": [ln for ln in dirty_out.splitlines() if ln.strip()][:50]}


# ============================================================================
# State: atomic persistence + resume
# ============================================================================
class RunState:
    """The full (unredacted) run state, persisted atomically under scratch. This
    file is NOT committed; the committable receipt is derived + redacted."""

    def __init__(self, state_path: Path, data: Dict[str, Any]):
        self.state_path = Path(state_path)
        self.data = data

    @classmethod
    def new(cls, state_path: Path, run_id: str, head: str, args_snapshot: Dict[str, Any],
            stages_meta: List[Dict[str, Any]]) -> "RunState":
        data = {
            "receipt_version": RECEIPT_VERSION,
            "run_id": run_id,
            "git_head": head,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "args": args_snapshot,
            "preflight": {},
            "environment": {},
            "log_ledger": [],
            "stages": [dict(m, status=PENDING, detail={}, started_at=None,
                            duration_s=None, error=None, logs=[]) for m in stages_meta],
        }
        return cls(state_path, data)

    @classmethod
    def load(cls, state_path: Path) -> "RunState":
        return cls(state_path, json.loads(Path(state_path).read_text(encoding="utf-8")))

    def save(self) -> None:
        """Atomic write: temp file in the same dir + os.replace (survives Ctrl+C /
        power loss mid-write)."""
        self.data["updated_at"] = utcnow()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), prefix=".state_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

    def stage(self, key: str) -> Dict[str, Any]:
        for s in self.data["stages"]:
            if s["key"] == key:
                return s
        raise KeyError(key)


# ============================================================================
# Execution context
# ============================================================================
class Ctx:
    def __init__(self, args, state: RunState, scratch: Path, logs_dir: Path):
        self.args = args
        self.state = state
        self.scratch = scratch
        self.logs_dir = logs_dir
        self.python = sys.executable
        self.root = ROOT
        self._seq = 0

    def run_subprocess(self, key: str, cmd: List[str], env: Optional[Dict[str, str]] = None,
                       cwd: Optional[Path] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Run a subprocess, writing its combined output to its own log, and record
        command / start / duration / exit code / log SHA-256 in the ledger."""
        self._seq += 1
        log_path = self.logs_dir / f"{key}.{self._seq:02d}.log"
        cmd = [str(c) for c in cmd]
        started = utcnow(); t0 = time.time()
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(f"# command: {' '.join(cmd)}\n# started: {started}\n# cwd: {cwd or self.root}\n\n")
                fh.flush()
                proc = subprocess.run(cmd, cwd=str(cwd or self.root), env=run_env,
                                      stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n# TIMEOUT after {timeout}s\n")
            exit_code = 124
        except Exception as exc:  # pragma: no cover - defensive
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n# LAUNCH ERROR: {exc}\n")
            exit_code = 127
        duration = round(time.time() - t0, 3)
        rec = {"key": key, "command": " ".join(cmd), "started_at": started,
               "duration_s": duration, "exit_code": exit_code,
               "log": str(log_path.relative_to(self.scratch)) if self.scratch in log_path.parents else str(log_path),
               "log_sha256": sha256_file(log_path) if log_path.exists() else None}
        self.state.data["log_ledger"].append(rec)
        return rec

    def tail(self, log_rel: str, n: int = 4000) -> str:
        p = self.scratch / log_rel
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")[-n:]


# ============================================================================
# Stage registry + implementations
# ============================================================================
# key, name, tier, required (must PASS for overall==complete)
STAGE_META: List[Dict[str, Any]] = [
    {"key": "gates", "name": "Full gate suite (tests/run_gates.py)", "tier": TIER_RIG, "required": True},
    {"key": "verify_package", "name": "VERIFY_PACKAGE + build single-file", "tier": TIER_RIG, "required": True},
    {"key": "workbench_dom", "name": "Workbench Playwright lifecycle (package + single-file)", "tier": TIER_RIG, "required": True},
    {"key": "acceptance", "name": "Immutable-project acceptance (scratch)", "tier": TIER_RIG, "required": True},
    {"key": "real_project", "name": "Compile + render a real-library project", "tier": TIER_REAL, "required": True},
    {"key": "real_render_verdict", "name": "Human keep/reject on the real render", "tier": TIER_HUMAN, "required": True},
    {"key": "edit_undo_redo", "name": "Edit → render → undo (PCM identity) → redo → restart", "tier": TIER_REAL, "required": True},
    {"key": "ranker", "name": "Ranker training + off/on order (real judgments)", "tier": TIER_REAL, "required": True},
    {"key": "piano", "name": "Bounded piano session", "tier": TIER_REAL, "required": True},
    {"key": "allin1", "name": "allin1 before/after on real tracks", "tier": TIER_GPU, "required": False},
    {"key": "rubberband", "name": "Rubber Band A/B render + listening verdict", "tier": TIER_GPU, "required": False},
    {"key": "techno", "name": "Techno external-vocal proof + verdict", "tier": TIER_HUMAN, "required": False},
]


def _cfg_from_workspace(ctx: Ctx) -> Dict[str, Any]:
    """Read the production workspace config (master_root, agent_root, working_root)
    via `earcrate doctor` so we can clone durable state and keep music read-only.
    Never writes to the production workspace."""
    rec = ctx.run_subprocess("cfg_probe", [ctx.python, "-m", "earcrate", "doctor"],
                             env={"EARCRATE_HOME": str(Path(ctx.args.workspace).expanduser())}, timeout=180)
    out = ctx.tail(rec["log"])
    with contextlib.suppress(Exception):
        # doctor prints a JSON object; grab the last {...} block
        start = out.rfind("{\n");
        if start < 0:
            start = out.find("{")
        blob = out[start:]
        data = json.loads(blob)
        return data.get("config") or {}
    return {}


def _prepare_scratch_workspace(ctx: Ctx) -> Dict[str, Any]:
    """Durable-state clone: copy the production analysis DB/atoms/judgments into a
    scratch workspace whose master_root points at the REAL music (read-only). This
    keeps compile/render/piano off the production workspace. Best-effort: returns
    {'ok': False, 'reason': ...} if the production state can't be located, so
    crate-dependent stages degrade to an honest skip rather than pollute or crash.
    """
    cfg = _cfg_from_workspace(ctx)
    master = cfg.get("master_root")
    agent = cfg.get("agent_root")
    if not master or not Path(master).exists():
        return {"ok": False, "reason": f"could not resolve a real music library (master_root) from --workspace ({ctx.args.workspace}); run `earcrate configure` there first"}
    ws = ctx.scratch / "ws"
    ws_home = ctx.scratch / "ws_home"
    for d in (ws / "work", ws / "agent", ws_home):
        d.mkdir(parents=True, exist_ok=True)
    # configure a fresh scratch workspace pointing at the REAL music (read-only)
    rec = ctx.run_subprocess("ws_configure",
                             [ctx.python, "-m", "earcrate", "configure", "--music", str(master), "--workspace", str(ws)],
                             env={"EARCRATE_HOME": str(ws_home)}, timeout=300)
    if rec["exit_code"] != 0:
        return {"ok": False, "reason": f"could not configure scratch workspace (see {rec['log']})", "master_root": master}
    # bring the analyzed crate across: copy the production agent DB(s) into scratch agent
    copied = []
    if agent and Path(agent).exists():
        scratch_agent = ws / "agent"
        for db in Path(agent).glob("*.sqlite*"):
            with contextlib.suppress(Exception):
                shutil.copy2(db, scratch_agent / db.name); copied.append(db.name)
        for extra in ("identify_proposals.json",):
            src = Path(agent) / extra
            if src.exists():
                with contextlib.suppress(Exception):
                    shutil.copy2(src, scratch_agent / extra)
    return {"ok": True, "home": str(ws_home), "workspace": str(ws), "master_root": master,
            "cloned_db": copied, "note": "durable-state clone; production workspace untouched; music read-only"}


def _scratch_env(ctx: Ctx) -> Optional[Dict[str, str]]:
    prep = ctx.state.data.get("scratch_workspace") or {}
    if prep.get("ok"):
        return {"EARCRATE_HOME": prep["home"]}
    return None


# ---- stage implementations: each returns (status, detail) ------------------
def stage_gates(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    rec = ctx.run_subprocess("gates", [ctx.python, "tests/run_gates.py"], timeout=3600)
    summary = parse_gate_summary(ctx.tail(rec["log"], 8000))
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "discovered": summary[1] if summary else None,
              "passed": summary[0] if summary else None}
    ok = rec["exit_code"] == 0 and summary is not None and summary[0] == summary[1] and summary[1] > 0
    return (PASSED if ok else FAILED), detail


def stage_verify_package(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    rec = ctx.run_subprocess("verify_package", [ctx.python, "VERIFY_PACKAGE.py", "--skip-gates"], timeout=1800)
    dist = ROOT / "dist" / "earcrate.py"
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "dist_sha256": sha256_file(dist) if dist.exists() else None,
              "dist_path": str(dist)}
    ok = rec["exit_code"] == 0 and dist.exists()
    return (PASSED if ok else FAILED), detail


def stage_workbench_dom(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    shots = ctx.scratch / "workbench_dom"
    shots.mkdir(parents=True, exist_ok=True)
    env = {"WB_SHOTS_DIR": str(shots)}
    if ctx.args.chromium:
        env["EARCRATE_CHROMIUM"] = ctx.args.chromium
    rec = ctx.run_subprocess("workbench_dom", [ctx.python, "tests/manual/verify_workbench_dom.py"],
                             env=env, timeout=1200)
    out = ctx.tail(rec["log"], 20000)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "screenshots": sorted(str(p.relative_to(ctx.scratch)) for p in shots.glob("*.png"))}
    # the harness exits 0 iff every mode green with zero console errors
    ok = rec["exit_code"] == 0 and "OVERALL: PASS" in out
    detail["console_errors_zero"] = "CONSOLE_ERRORS: 0" in out or "console_errors=0" in out or ok
    return (PASSED if ok else FAILED), detail


def stage_acceptance(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    dest = ctx.scratch / "acceptance"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    rec = ctx.run_subprocess("acceptance",
                             [ctx.python, "-m", "earcrate", "project", "acceptance", "--destination", str(dest)],
                             env={"EARCRATE_HOME": str(ctx.scratch / "acc_home")}, timeout=1800)
    receipt = dest / "acceptance_receipt.json"
    detail = {"log": rec["log"], "exit_code": rec["exit_code"], "destination": str(dest)}
    ok = rec["exit_code"] == 0 and receipt.exists()
    if receipt.exists():
        with contextlib.suppress(Exception):
            r = json.loads(receipt.read_text(encoding="utf-8"))
            detail["acceptance_ok"] = bool(r.get("ok"))
            ok = ok and bool(r.get("ok"))
    return (PASSED if ok else FAILED), detail


def stage_real_project(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": (ctx.state.data.get("scratch_workspace") or {}).get("reason",
                          "no durable-state clone; real-library compile skipped (configure --workspace first)")}
    name = "Rig Receipt Project"
    rec = ctx.run_subprocess("real_compile",
                             [ctx.python, "-m", "earcrate", "project", "compile",
                              "--profile", ctx.args.profile, "--seconds", str(ctx.args.real_seconds),
                              "--name", name, "--render"], env=env, timeout=3600)
    out = ctx.tail(rec["log"], 40000)
    detail = {"compile_log": rec["log"], "exit_code": rec["exit_code"]}
    proj = None
    with contextlib.suppress(Exception):
        blob = out[out.rfind("{"):] if "{" in out else ""
        proj = json.loads(blob) if blob else None
    if rec["exit_code"] != 0 or not proj or not proj.get("project_id"):
        detail["reason"] = "compile did not produce a project (crate may be stale/not ready — the log has the exact reason)"
        return FAILED, detail
    pid = proj["project_id"]
    detail.update({"project_id": pid, "initial_revision_sha": proj.get("revision_sha"),
                   "score_sha": proj.get("score_sha")})
    # render receipt (compile --render executed the manifest); read the project + runs for identities
    show = ctx.run_subprocess("real_show", [ctx.python, "-m", "earcrate", "project", "show", pid], env=env, timeout=300)
    with contextlib.suppress(Exception):
        s = json.loads(ctx.tail(show["log"], 60000)[ctx.tail(show["log"], 60000).rfind("{"):])
        detail["active_revision_sha"] = (s.get("project") or {}).get("active_revision_sha")
        detail["mastering_child_revision_sha"] = (s.get("revision") or {}).get("revision_sha")
    # explicit render to capture the WAV + report deterministically
    rr = ctx.run_subprocess("real_render", [ctx.python, "-m", "earcrate", "project", "render", pid], env=env, timeout=1800)
    with contextlib.suppress(Exception):
        rout = ctx.tail(rr["log"], 60000); rj = json.loads(rout[rout.rfind("{"):])
        detail["render_path"] = rj.get("path"); detail["report_path"] = rj.get("report")
        detail["render_type"] = rj.get("type")
        if rj.get("path") and Path(rj["path"]).exists():
            detail["render_sha256"] = sha256_file(Path(rj["path"]))
    # exports
    for fmt in ("edl", "rpp", "sheet"):
        ex = ctx.run_subprocess(f"real_export_{fmt}",
                                [ctx.python, "-m", "earcrate", "project", "export", pid], env=env, timeout=600)
        with contextlib.suppress(Exception):
            eout = ctx.tail(ex["log"], 20000); ej = json.loads(eout[eout.rfind("{"):])
            detail[f"export_{fmt}"] = ej.get(fmt)
    ctx.state.data["real_project_id"] = pid
    ok = bool(detail.get("render_type") == "render_project" and detail.get("render_path"))
    return (PASSED if ok else FAILED), detail


def stage_real_render_verdict(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    verdict = ctx.args.verdict_real_render
    detail = {"note": "human keep/reject on the real render; gate success is NEVER inferred as a keep"}
    if verdict in ("keep", "reject"):
        detail["verdict"] = verdict
        detail["render_path"] = (ctx.state.stage("real_project")["detail"] or {}).get("render_path")
        return PASSED, detail
    detail["verdict"] = None
    detail["how"] = "re-run with --verdict-real-render keep|reject (or use --interactive to be prompted)"
    return PENDING_MANUAL, detail


def stage_edit_undo_redo(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    pid = ctx.state.data.get("real_project_id")
    if env is None or not pid:
        return SKIPPED, {"reason": "no real project from the compile stage; edit/undo lifecycle skipped"}
    # Drive edit → render → undo → rerender → PCM identity → redo → restart via a
    # helper subprocess so the whole in-process lifecycle uses one EarcrateCore.
    helper = ctx.scratch / "_edit_lifecycle.py"
    helper.write_text(_EDIT_LIFECYCLE_SRC, encoding="utf-8")
    rec = ctx.run_subprocess("edit_undo_redo", [ctx.python, str(helper), pid], env=env, timeout=1800)
    out = ctx.tail(rec["log"], 20000)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"]}
    with contextlib.suppress(Exception):
        detail.update(json.loads(out[out.rfind("{"):]))
    ok = rec["exit_code"] == 0 and detail.get("pcm_identity_restored") is True and detail.get("reopened_head_matches") is True
    return (PASSED if ok else FAILED), detail


def stage_ranker(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": "no durable-state clone; ranker training skipped"}
    rec = ctx.run_subprocess("ranker_train",
                             [ctx.python, "-m", "earcrate", "train-ranker", "--profile", ctx.args.profile],
                             env=env, timeout=600)
    out = ctx.tail(rec["log"], 20000)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"]}
    res = None
    with contextlib.suppress(Exception):
        res = json.loads(out[out.rfind("{"):])
    if res is not None and res.get("ok") is False:
        # honest skip on insufficient/one-class data — NOT a pass
        detail["reason"] = res.get("reason")
        return SKIPPED, dict(detail, skipped_kind="skipped_insufficient_data")
    if rec["exit_code"] != 0 or not res or not res.get("ok"):
        return FAILED, dict(detail, reason="train-ranker did not produce a model")
    detail.update({"model_sha": res.get("model_sha"), "n_approved": res.get("n_approved"),
                   "n_rejected": res.get("n_rejected"), "note": "ranker is a proposer; membership is unchanged when enabled"})
    return PASSED, detail


def stage_piano(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": "no durable-state clone; piano session skipped"}
    rid = f"rig_{ctx.state.data['run_id']}"
    rec = ctx.run_subprocess("piano",
                             [ctx.python, "-m", "earcrate", "project", "piano",
                              "--personas", ctx.args.profile, "--iterations", str(ctx.args.piano_iterations),
                              "--run-id", rid], env=env, timeout=3600)
    out = ctx.tail(rec["log"], 40000)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"], "run_id": rid, "iteration_cap": ctx.args.piano_iterations}
    with contextlib.suppress(Exception):
        r = json.loads(out[out.rfind("{"):])
        detail.update({k: r.get(k) for k in ("attempted", "kept", "discarded", "errored", "stop_reason", "complete", "keeps")})
    ok = rec["exit_code"] == 0 and detail.get("complete") is True and (detail.get("attempted") or 0) <= ctx.args.piano_iterations
    return (PASSED if ok else FAILED), detail


def stage_allin1(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    # Probe the ACTUAL installed model; never claim the stub as validation.
    probe = ctx.run_subprocess("allin1_probe",
                               [ctx.python, "-c", "import json;from earcrate.providers.beats import beat_capability;print(json.dumps(beat_capability()))"],
                               timeout=120)
    cap = {}
    with contextlib.suppress(Exception):
        cap = json.loads(ctx.tail(probe["log"], 4000).splitlines()[-1])
    if not cap.get("ready"):
        return SKIPPED, {"reason": "allin1 not installed on this box",
                         "install": "pip install allin1",
                         "rerun": f"set EARCRATE_BEATS=allin1 and re-run: python scripts/run_rig_receipt.py --run-id {ctx.state.data['run_id']} --resume ...",
                         "note": "the stub-based gate is adapter shape only — NOT model validation"}
    # allin1 present: sample real tracks non-destructively (no cache mutation) and
    # record before/after downbeat-confidence, plus one transition feasibility flip.
    helper = ctx.scratch / "_allin1_sample.py"
    helper.write_text(_ALLIN1_SAMPLE_SRC, encoding="utf-8")
    env = _scratch_env(ctx) or {"EARCRATE_HOME": str(Path(ctx.args.workspace).expanduser())}
    rec = ctx.run_subprocess("allin1_sample", [ctx.python, str(helper), str(ctx.args.real_seconds)], env=env, timeout=3600)
    out = ctx.tail(rec["log"], 40000)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"]}
    with contextlib.suppress(Exception):
        detail.update(json.loads(out[out.rfind("{"):]))
    ok = rec["exit_code"] == 0 and detail.get("tracks_sampled", 0) > 0
    return (PASSED if ok else FAILED), detail


def stage_rubberband(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    pid = ctx.state.data.get("real_project_id")
    probe = ctx.run_subprocess("rubberband_probe",
                               [ctx.python, "-c", "import json;from earcrate.providers.transform import transform_capability;print(json.dumps(transform_capability()))"],
                               timeout=120)
    cap = {}
    with contextlib.suppress(Exception):
        cap = json.loads(ctx.tail(probe["log"], 4000).splitlines()[-1])
    if not cap.get("ready"):
        return SKIPPED, {"reason": "Rubber Band CLI / pyrubberband not installed",
                         "install": "install the 'rubberband' binary + pip install pyrubberband",
                         "note": "default transform is UNCHANGED; this script never flips the default or bumps ENGINE_VERSION"}
    if env is None or not pid:
        return SKIPPED, {"reason": "no real project to A/B; render one first"}
    out_dir = ctx.scratch / "rubberband_ab"; out_dir.mkdir(parents=True, exist_ok=True)
    default_render = ctx.run_subprocess("rb_default",
                                        [ctx.python, "-m", "earcrate", "project", "render", pid,
                                         "--dst", str(out_dir / "default.wav")], env=env, timeout=1800)
    rb_env = dict(env); rb_env["EARCRATE_TRANSFORM"] = "rubberband"   # child-process override only
    rb_render = ctx.run_subprocess("rb_rubberband",
                                   [ctx.python, "-m", "earcrate", "project", "render", pid,
                                    "--dst", str(out_dir / "rubberband.wav")], env=rb_env, timeout=1800)
    detail = {"default_log": default_render["log"], "rubberband_log": rb_render["log"],
              "note": "child-process EARCRATE_TRANSFORM override only; default not changed",
              "verdict": ctx.args.verdict_rubberband}
    for label in ("default", "rubberband"):
        p = out_dir / f"{label}.wav"
        if p.exists():
            detail[f"{label}_sha256"] = sha256_file(p)
    # A/B produced; the LISTENING verdict is human
    if ctx.args.verdict_rubberband in ("default", "rubberband", "tie"):
        return PASSED, detail
    return PENDING_MANUAL, dict(detail, how="listen to both, then --verdict-rubberband default|rubberband|tie")


def stage_techno(ctx: Ctx) -> Tuple[str, Dict[str, Any]]:
    env = _scratch_env(ctx)
    vocal = ctx.args.external_vocal
    if not vocal:
        return SKIPPED, {"reason": "no --external-vocal provided; techno external-remix proof not run",
                         "how": "re-run with --external-vocal <path to a foreign vocal>",
                         "note": "the copyrighted source is NEVER copied into the repo or receipt"}
    if not Path(vocal).exists():
        return FAILED, {"reason": f"--external-vocal path does not exist: {vocal}"}
    if env is None:
        return SKIPPED, {"reason": "no durable-state clone; techno proof needs the library bed"}
    out_dir = ctx.scratch / "techno"; out_dir.mkdir(parents=True, exist_ok=True)
    rec = ctx.run_subprocess("techno_remix",
                             [ctx.python, "-m", "earcrate", "project", "compile",
                              "--profile", "remix_techno_v1", "--seconds", str(ctx.args.real_seconds),
                              "--name", "Techno Proof", "--render"],
                             env=dict(env, EARCRATE_EXTERNAL_VOCAL=str(vocal)), timeout=3600)
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "external_vocal_basename": Path(vocal).name,   # basename only; never the full path or the file
              "note": "external source is referenced, never bundled; verdict is human",
              "verdict": ctx.args.verdict_techno}
    if ctx.args.verdict_techno in ("keep", "reject"):
        return PASSED, detail
    return PENDING_MANUAL, dict(detail, how="render/audition, then --verdict-techno keep|reject")


STAGE_FUNCS: Dict[str, Callable[[Ctx], Tuple[str, Dict[str, Any]]]] = {
    "gates": stage_gates, "verify_package": stage_verify_package, "workbench_dom": stage_workbench_dom,
    "acceptance": stage_acceptance, "real_project": stage_real_project,
    "real_render_verdict": stage_real_render_verdict, "edit_undo_redo": stage_edit_undo_redo,
    "ranker": stage_ranker, "piano": stage_piano, "allin1": stage_allin1,
    "rubberband": stage_rubberband, "techno": stage_techno,
}


# ---- helper subprocess sources (written to scratch, run in-process) ---------
_EDIT_LIFECYCLE_SRC = r'''
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf
from earcrate.app import EarcrateCore
from earcrate.project.model import ScoreRevision
pid = sys.argv[1]
core = EarcrateCore(); core.load_config_if_present()
def pcm(path):
    a,_ = sf.read(str(path), dtype="float32", always_2d=True); return __import__("hashlib").sha256(a.tobytes()).hexdigest()
r0 = core.project_render(pid); p0 = r0["path"]; sha0 = pcm(p0)
show = core.project_show(pid); rev = ScoreRevision.from_dict(show["revision"])
# pick one unlocked clip
clip = None
for t in rev.tracks:
    for c in (t.get("clips") or []):
        if not c.get("locked"): clip = c; break
    if clip: break
res = {"initial_render": p0, "initial_pcm_sha": sha0, "clip_id": clip and clip.get("clip_id")}
if clip:
    # one safe within-policy edit: nudge gain by +0.5 dB within range if possible, else set_pan small
    core.project_edit(pid, {"actor":"rig","kind":"set_pan","payload":{"clip_id":clip["clip_id"],"pan":0.05}})
    r1 = core.project_render(pid); res["edited_render"]=r1["path"]; res["edited_pcm_sha"]=pcm(r1["path"])
    core.project_undo(pid)
    r2 = core.project_render(pid); res["restored_pcm_sha"]=pcm(r2["path"])
    res["pcm_identity_restored"] = (res["restored_pcm_sha"] == sha0)
    core.project_redo(pid)
    reopened = EarcrateCore(); reopened.load_config_if_present()
    head = reopened.project_show(pid)["project"]["active_revision_sha"]
    edited_head = core.project_show(pid)["project"]["active_revision_sha"]
    res["reopened_head_matches"] = (head == edited_head)
else:
    res["pcm_identity_restored"] = False; res["reopened_head_matches"] = False; res["reason"]="no unlocked clip"
print(json.dumps(res))
'''

_ALLIN1_SAMPLE_SRC = r'''
import json, os, sys
import numpy as np
from earcrate.app import EarcrateCore
from earcrate.analyze.decode import decode_audio  # decode a bounded window
from earcrate.analyze.features import compute_pcm_features
seconds = float(sys.argv[1]) if len(sys.argv)>1 else 60.0
core = EarcrateCore(); core.load_config_if_present()
db = core.conn()
rows = db.execute("SELECT path FROM files WHERE COALESCE(present,1)=1 LIMIT 8").fetchall()
out = {"tracks_sampled":0, "librosa":[], "allin1":[], "transition_feasibility_change": None}
sr = 22050
for r in rows:
    p = r[0]
    try:
        y = decode_audio(p, sr=sr, mono=True, max_seconds=seconds)
    except Exception:
        continue
    os.environ.pop("EARCRATE_BEATS", None)
    lib = compute_pcm_features(np.asarray(y,dtype=np.float32), sr)
    os.environ["EARCRATE_BEATS"] = "allin1"
    al = compute_pcm_features(np.asarray(y,dtype=np.float32), sr)
    os.environ.pop("EARCRATE_BEATS", None)
    out["librosa"].append({"path_basename": os.path.basename(p), "bpm_conf": lib.get("bpm_confidence"), "downbeats": int(len(lib.get("downbeats") or [])), "backend": lib.get("beat_backend")})
    out["allin1"].append({"path_basename": os.path.basename(p), "bpm_conf": al.get("bpm_confidence"), "downbeats": int(len(al.get("downbeats") or [])), "backend": al.get("beat_backend")})
    out["tracks_sampled"] += 1
print(json.dumps(out))
'''


# ============================================================================
# Runner
# ============================================================================
def _stage_needs_run(prior_status: str) -> bool:
    """Resume rule: only PASSED stages are skipped (their expensive/destructive
    work is done). Everything else (pending/failed/skipped/pending_manual) re-runs
    so a resume picks up a now-available dependency or human verdict."""
    return prior_status != PASSED


def execute_stages(ctx: "Ctx", state: RunState, stage_meta: List[Dict[str, Any]],
                   stage_funcs: Dict[str, Callable[["Ctx"], Tuple[str, Dict[str, Any]]]]) -> str:
    """The stage loop, extracted so tests can drive synthetic stages and injected
    interrupts. Runs each stage that still needs running (resume rule), records its
    status, and CHECKPOINTS ATOMICALLY after every stage. A KeyboardInterrupt is
    caught, checkpointed, and re-raised so the caller returns the incomplete code.
    Returns the overall status."""
    for meta in stage_meta:
        st = state.stage(meta["key"])
        if not _stage_needs_run(st["status"]):
            continue
        st["started_at"] = utcnow(); t0 = time.time()
        try:
            status, detail = stage_funcs[meta["key"]](ctx)
        except KeyboardInterrupt:
            state.save()   # checkpoint the partial run before unwinding
            raise
        except Exception as exc:
            status, detail = FAILED, {"error": f"{type(exc).__name__}: {exc}"}
        st["status"] = status if status in STAGE_STATUSES else FAILED
        st["detail"] = detail
        st["duration_s"] = round(time.time() - t0, 3)
        st["logs"] = [r["log"] for r in state.data["log_ledger"] if r["key"].startswith(meta["key"])]
        state.save()   # atomic checkpoint after EVERY stage
    return classify_overall(state.data["stages"])


def run(args) -> int:
    scratch = Path(args.scratch).expanduser().resolve()
    run_dir = scratch / "receipt" / args.run_id
    state_path = run_dir / "state.json"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    head_now = git_head(ROOT)

    # ---- resume vs new + HEAD guard ----
    if state_path.exists():
        state = RunState.load(state_path)
        prior_head = state.data.get("git_head")
        if prior_head and prior_head != head_now["head"] and not args.allow_head_change:
            print(f"REFUSING: run {args.run_id} was created at HEAD {prior_head[:12]} but current HEAD is "
                  f"{head_now['head'][:12]}. Results from a different HEAD must not be appended. "
                  f"Use a new --run-id (or --allow-head-change only if you know what you are doing).", file=sys.stderr)
            return EXIT_FAILED
        print(f"resuming run {args.run_id} at HEAD {head_now['head'][:12]}")
    else:
        state = RunState.new(state_path, args.run_id, head_now["head"], _args_snapshot(args), STAGE_META)
        state.save()
        print(f"new run {args.run_id} at HEAD {head_now['head'][:12]}")

    # ---- preflight (recorded every run; refuses a dirty tree by default) ----
    music = None
    pf = {"git": head_now, "checked_at": utcnow()}
    try:
        cfg_probe = _preflight_env(args)
        pf["environment"] = cfg_probe
        state.data["environment"] = cfg_probe
        assert_scratch_safe(scratch, music)  # music resolved later from workspace; scratch explicitness checked
        pf["scratch_safe"] = True
    except Exception as exc:
        pf["scratch_safe"] = False; pf["scratch_error"] = str(exc)
    pf["dirty_refused"] = bool(head_now["dirty"] and not args.allow_dirty)
    state.data["preflight"] = pf
    state.save()
    if head_now["dirty"] and not args.allow_dirty:
        print("REFUSING: git working tree is dirty. Commit/stash, or pass --allow-dirty (records the dirt in the receipt).", file=sys.stderr)
        _write_receipts(state, scratch, run_dir, overall=FAILED)
        return EXIT_FAILED
    if not pf.get("scratch_safe", False):
        print(f"REFUSING: scratch path is unsafe: {pf.get('scratch_error')}", file=sys.stderr)
        _write_receipts(state, scratch, run_dir, overall=FAILED)
        return EXIT_FAILED

    ctx = Ctx(args, state, scratch, logs_dir)

    # ---- durable-state clone for crate-dependent stages (best-effort) ----
    if "scratch_workspace" not in state.data or not (state.data.get("scratch_workspace") or {}).get("ok"):
        with contextlib.suppress(Exception):
            state.data["scratch_workspace"] = _prepare_scratch_workspace(ctx)
            state.save()

    # ---- run stages in order, checkpoint after each ----
    try:
        overall = execute_stages(ctx, state, STAGE_META, STAGE_FUNCS)
    except KeyboardInterrupt:
        print("\ninterrupted — state checkpointed; resume with the same --run-id", file=sys.stderr)
        _write_receipts(state, scratch, run_dir, overall=INCOMPLETE)
        return EXIT_INCOMPLETE
    _write_receipts(state, scratch, run_dir, overall=overall)
    code = exit_code_for(overall)
    print(f"\noverall: {overall}  (exit {code})")
    print(f"receipt: {run_dir / 'receipt.json'}")
    print(f"summary: {run_dir / 'receipt.md'}")
    return code


def _args_snapshot(args) -> Dict[str, Any]:
    return {k: getattr(args, k) for k in ("workspace", "scratch", "profile", "real_seconds",
                                          "piano_iterations", "run_id", "external_vocal") if hasattr(args, k)}


def _preflight_env(args) -> Dict[str, Any]:
    def _imp(mod):
        try:
            __import__(mod); return True
        except Exception:
            return False
    env: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "rubberband_bin": bool(shutil.which("rubberband")),
        "pyrubberband": _imp("pyrubberband"),
        "allin1": _imp("allin1"),
        "demucs": _imp("demucs"),
        "playwright": _imp("playwright"),
        "torch": _imp("torch"),
    }
    with contextlib.suppress(Exception):
        import psutil  # optional
        env["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    with contextlib.suppress(Exception):
        import torch  # type: ignore
        env["cuda"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            env["gpu"] = torch.cuda.get_device_name(0)
    env["configured_providers"] = {k: os.environ.get(k) for k in
                                    ("EARCRATE_BEATS", "EARCRATE_TRANSFORM", "EARCRATE_RANKER", "EARCRATE_STEMS")}
    return env


def _write_receipts(state: RunState, scratch: Path, run_dir: Path, overall: str) -> None:
    """Committable JSON (redacted) + readable Markdown. Large artifacts stay in
    scratch; only these two files (plus selected screenshots) are meant to commit."""
    home = str(Path.home())
    stages = state.data["stages"]
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "run_id": state.data["run_id"],
        "git_head": state.data["git_head"],
        "created_at": state.data["created_at"],
        "finished_at": utcnow(),
        "overall": overall,
        "exit_code": exit_code_for(overall),
        "evidence_tiers": {
            TIER_CLOUD: "the 194/201-style cloud-CI gate numbers are NOT in this receipt; this is on-box evidence",
            TIER_RIG: "Windows-rig mechanical proof (gates, package, DOM, acceptance)",
            TIER_REAL: "real-library proof (compile/render/edit/undo/piano/ranker on the real crate)",
            TIER_GPU: "GPU/provider proof (allin1, Rubber Band)",
            TIER_HUMAN: "human listening verdicts (real render, Rubber Band A/B, techno)",
        },
        "environment": state.data.get("environment", {}),
        "preflight": {k: v for k, v in (state.data.get("preflight") or {}).items() if k != "environment"},
        "scratch_workspace": {k: v for k, v in (state.data.get("scratch_workspace") or {}).items()},
        "stages": [{"key": s["key"], "name": s["name"], "tier": s["tier"], "required": s["required"],
                    "status": s["status"], "duration_s": s["duration_s"], "detail": s["detail"],
                    "error": s.get("error")} for s in stages],
        "log_ledger": state.data.get("log_ledger", []),
    }
    receipt = redact_tree(receipt, home)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "receipt.md").write_text(_markdown(receipt), encoding="utf-8")


def _markdown(receipt: Dict[str, Any]) -> str:
    L = []
    L.append(f"# EarCrate rig receipt — `{receipt['run_id']}`\n")
    L.append(f"- **git HEAD**: `{receipt['git_head']}`")
    L.append(f"- **overall**: **{receipt['overall'].upper()}** (exit {receipt['exit_code']})")
    L.append(f"- **finished**: {receipt['finished_at']}\n")
    L.append("> Evidence tiers are SEPARATE. Code-present and cloud-CI-green are NOT on this receipt; "
             "this is on-box evidence only. A `passed` mechanical stage is not a musical verdict, and a "
             "`skipped`/`pending_manual` stage is never success.\n")
    L.append("## Stages\n")
    L.append("| # | stage | tier | required | status | secs |")
    L.append("|---|---|---|---|---|---|")
    icon = {PASSED: "✅ passed", FAILED: "❌ failed", SKIPPED: "⏭️ skipped", PENDING_MANUAL: "⏸️ pending_manual", PENDING: "… pending"}
    for i, s in enumerate(receipt["stages"], 1):
        L.append(f"| {i} | {s['name']} | {s['tier']} | {'yes' if s['required'] else 'no'} | "
                 f"{icon.get(s['status'], s['status'])} | {s['duration_s'] if s['duration_s'] is not None else '—'} |")
    L.append("")
    # per-stage notes worth surfacing
    for s in receipt["stages"]:
        d = s.get("detail") or {}
        notable = {k: d[k] for k in ("discovered", "passed", "dist_sha256", "project_id", "render_sha256",
                                     "pcm_identity_restored", "reopened_head_matches", "model_sha", "reason",
                                     "verdict", "attempted", "kept", "stop_reason", "install", "rerun", "how",
                                     "tracks_sampled") if k in d}
        if notable:
            L.append(f"- **{s['key']}** ({s['status']}): " +
                     ", ".join(f"{k}=`{v}`" for k, v in notable.items()))
    L.append("\n## What this receipt does NOT prove\n")
    pend = [s["key"] for s in receipt["stages"] if s["status"] in (PENDING_MANUAL, SKIPPED)]
    if pend:
        L.append("Outstanding (skipped or awaiting a human/dependency): " + ", ".join(f"`{k}`" for k in pend) + ".")
    else:
        L.append("All stages have a terminal status.")
    L.append("\nLog ledger (command / duration / exit / sha) is in `receipt.json`. "
             "Large audio + browser artifacts stay under scratch.")
    return "\n".join(L) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run_rig_receipt",
                                 description="EarCrate one-command Windows-rig release receipt.")
    ap.add_argument("--workspace", required=True, help="the configured EarCrate workspace/home (read-only source of the real library + durable state)")
    ap.add_argument("--scratch", required=True, help="explicit scratch dir for all outputs (must be OUTSIDE the music library)")
    ap.add_argument("--profile", default="remix_prettylights_v1", help="persona for the real-library compile")
    ap.add_argument("--real-seconds", type=float, default=120.0, dest="real_seconds")
    ap.add_argument("--piano-iterations", type=int, default=3, dest="piano_iterations")
    ap.add_argument("--run-id", default=None, help="stable run id; reuse it to RESUME. Default: derived from HEAD + scratch")
    ap.add_argument("--resume", action="store_true", help="explicit resume flag (resume is automatic if --run-id state exists)")
    ap.add_argument("--allow-dirty", action="store_true", help="do not refuse a dirty git tree (records the dirt)")
    ap.add_argument("--allow-head-change", action="store_true", help="allow appending to a run created at a different HEAD (discouraged)")
    ap.add_argument("--external-vocal", default=None, dest="external_vocal", help="path to a foreign vocal for the techno proof (never copied into the repo/receipt)")
    ap.add_argument("--chromium", default=os.environ.get("EARCRATE_CHROMIUM"), help="explicit Chromium executable for the DOM stage")
    ap.add_argument("--verdict-real-render", choices=["keep", "reject"], default=None, dest="verdict_real_render")
    ap.add_argument("--verdict-rubberband", choices=["default", "rubberband", "tie"], default=None, dest="verdict_rubberband")
    ap.add_argument("--verdict-techno", choices=["keep", "reject"], default=None, dest="verdict_techno")
    return ap


def _default_run_id(args) -> str:
    head = git_head(ROOT)["head"][:12] or "nohead"
    tag = sha256_text(str(Path(args.scratch).expanduser().resolve()))[:6]
    return f"rig_{head}_{tag}"


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.run_id:
        args.run_id = _default_run_id(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
