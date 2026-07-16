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
                       cwd: Optional[Path] = None, timeout: Optional[int] = None,
                       json_out: bool = False) -> Dict[str, Any]:
        """Run a subprocess, writing its combined output to its own log, recording
        command / start / duration / exit code / log SHA-256 in the ledger.

        When ``json_out`` is set, ``--json-out <path>`` is appended and the exact
        result file is read back into rec['result'] (rec['result_readable'] flags
        whether it parsed). Stages NEVER scrape stdout: a command that exits 0 but
        leaves an unreadable result must be treated as a failure by the caller.
        """
        self._seq += 1
        log_path = self.logs_dir / f"{key}.{self._seq:02d}.log"
        cmd = [str(c) for c in cmd]
        result_path: Optional[Path] = None
        if json_out:
            result_path = self.logs_dir / f"{key}.{self._seq:02d}.result.json"
            cmd = cmd + ["--json-out", str(result_path)]
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
        rec: Dict[str, Any] = {"key": key, "command": " ".join(cmd), "started_at": started,
                               "duration_s": duration, "exit_code": exit_code,
                               "log": self._rel(log_path),
                               "log_sha256": sha256_file(log_path) if log_path.exists() else None}
        if json_out:
            rec["result"] = None
            rec["result_readable"] = False
            rec["result_path"] = self._rel(result_path)
            if result_path and result_path.exists():
                try:
                    rec["result"] = json.loads(result_path.read_text(encoding="utf-8"))
                    rec["result_readable"] = True
                except Exception as exc:
                    rec["result_error"] = str(exc)
        self.state.data["log_ledger"].append(rec)
        return rec

    def _rel(self, p: Optional[Path]) -> Optional[str]:
        if p is None:
            return None
        return str(p.relative_to(self.scratch)) if self.scratch in p.parents else str(p)

    def run_helper(self, key: str, src: str, argv: List[str], env: Optional[Dict[str, str]] = None,
                   timeout: Optional[int] = None) -> Dict[str, Any]:
        """Run an in-repo helper script that writes its result JSON to a path we
        pass as its FIRST argv. Same no-stdout-scrape contract: rec['result'] is the
        parsed file or None (rec['result_readable'])."""
        self._seq += 1
        helper = self.logs_dir / f"{key}.{self._seq:02d}.helper.py"
        helper.write_text(src, encoding="utf-8")
        out_path = self.logs_dir / f"{key}.{self._seq:02d}.result.json"
        rec = self.run_subprocess(key, [self.python, str(helper), str(out_path)] + [str(a) for a in argv],
                                  env=env, timeout=timeout)
        rec["result"] = None
        rec["result_readable"] = False
        rec["result_path"] = self._rel(out_path)
        if out_path.exists():
            try:
                rec["result"] = json.loads(out_path.read_text(encoding="utf-8"))
                rec["result_readable"] = True
            except Exception as exc:
                rec["result_error"] = str(exc)
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


def resolve_workspace_config(ctx: Ctx) -> Dict[str, Any]:
    """Read the production workspace config (master_root, agent_root, working_root)
    via `earcrate doctor --json-out` — the exact result file, never a stdout
    scrape. Never writes to the production workspace. Returns {} on any failure."""
    rec = ctx.run_subprocess("cfg_probe", [ctx.python, "-m", "earcrate", "doctor"],
                             env={"EARCRATE_HOME": str(Path(ctx.args.workspace).expanduser())},
                             timeout=180, json_out=True)
    if rec.get("result_readable") and isinstance(rec.get("result"), dict):
        return rec["result"].get("config") or {}
    return {}


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Consistent online SQLite backup (Connection.backup) — NOT a raw file copy.
    This is WAL-safe even if the production app has the DB open, so the scratch
    copy is never a torn/partial database."""
    import sqlite3
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)         # atomic, consistent snapshot
            # verify the copy opens and passes an integrity check before use
            row = dst_conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise RuntimeError(f"backup integrity_check failed for {dst.name}: {row}")
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _prepare_scratch_workspace(ctx: Ctx) -> Dict[str, Any]:
    """Durable-state clone: consistent SQLite backup of the production analysis DB
    into a scratch workspace whose master_root points at the REAL music
    (read-only). Keeps compile/render/piano off the production workspace. Returns
    {'ok': False, 'reason': ...} if the real music/DB can't be resolved, so
    crate-dependent stages degrade to an honest skip rather than pollute or crash.
    """
    cfg = ctx.state.data.get("workspace_config") or {}
    master = cfg.get("master_root")
    agent = cfg.get("agent_root")
    if not master or not Path(master).expanduser().exists():
        return {"ok": False, "reason": f"could not resolve a real music library (master_root) from --workspace ({ctx.args.workspace}); run `earcrate configure` there first"}
    ws = ctx.scratch / "ws"
    ws_home = ctx.scratch / "ws_home"
    for d in (ws, ws_home):
        d.mkdir(parents=True, exist_ok=True)
    rec = ctx.run_subprocess("ws_configure",
                             [ctx.python, "-m", "earcrate", "configure", "--music", str(master), "--workspace", str(ws)],
                             env={"EARCRATE_HOME": str(ws_home)}, timeout=300, json_out=True)
    if rec["exit_code"] != 0 or not rec.get("result_readable"):
        return {"ok": False, "reason": f"could not configure scratch workspace (see {rec['log']})", "master_root": master}
    scratch_agent = ws / "agent"; scratch_agent.mkdir(parents=True, exist_ok=True)
    backed_up: List[str] = []
    backup_errors: List[str] = []
    if agent and Path(agent).exists():
        # back up only the primary DB files (skip -wal/-shm; .backup captures WAL)
        for db in Path(agent).glob("*.sqlite"):
            try:
                _backup_sqlite(db, scratch_agent / db.name); backed_up.append(db.name)
            except Exception as exc:
                backup_errors.append(f"{db.name}: {exc}")
    if not backed_up:
        return {"ok": False, "master_root": master,
                "reason": "no analysis DB could be consistently backed up from the production agent dir "
                          + (f"(errors: {backup_errors})" if backup_errors else f"(looked in {agent})")}
    return {"ok": True, "home": str(ws_home), "workspace": str(ws), "master_root": master,
            "backed_up_db": backed_up, "backup_errors": backup_errors,
            "note": "durable-state clone via consistent SQLite backup; production workspace untouched; music read-only"}


def _scratch_env(ctx: Ctx) -> Optional[Dict[str, str]]:
    prep = ctx.state.data.get("scratch_workspace") or {}
    if prep.get("ok"):
        return {"EARCRATE_HOME": prep["home"]}
    return None


# ---- stage implementations: each returns (status, detail) ------------------
# Contract: a mechanical stage NEVER passes on a nonzero exit, an unreadable
# machine result, a missing artifact, or a silent provider fallback. Human
# verdicts sit ON TOP of a mechanical gate and can only complete an already-green
# mechanical result — never convert a failed render into a pass.

def _fail_if_unreadable(rec, extra=None):
    """A command that exits 0 but leaves an unreadable --json-out result is a
    FAILURE, never a silent pass."""
    d = {"log": rec["log"], "exit_code": rec["exit_code"], "result_path": rec.get("result_path")}
    if extra:
        d.update(extra)
    if rec["exit_code"] != 0:
        return (FAILED, dict(d, reason="command exited nonzero"))
    if not rec.get("result_readable"):
        return (FAILED, dict(d, reason="command exited 0 but its --json-out result was missing/unreadable",
                             result_error=rec.get("result_error")))
    return (None, d)


def stage_gates(ctx):
    rec = ctx.run_subprocess("gates", [ctx.python, "tests/run_gates.py"], timeout=3600)
    summary = parse_gate_summary(ctx.tail(rec["log"], 8000))
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "discovered": summary[1] if summary else None,
              "passed": summary[0] if summary else None}
    ok = rec["exit_code"] == 0 and summary is not None and summary[0] == summary[1] and summary[1] > 0
    return (PASSED if ok else FAILED), detail


def stage_verify_package(ctx):
    rec = ctx.run_subprocess("verify_package", [ctx.python, "VERIFY_PACKAGE.py", "--skip-gates"], timeout=1800)
    dist = ROOT / "dist" / "earcrate.py"
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "dist_sha256": sha256_file(dist) if dist.exists() else None, "dist_path": str(dist)}
    ok = rec["exit_code"] == 0 and dist.exists()
    return (PASSED if ok else FAILED), detail


def stage_workbench_dom(ctx):
    shots = ctx.scratch / "workbench_dom"
    shots.mkdir(parents=True, exist_ok=True)
    env = {"WB_SHOTS_DIR": str(shots)}
    if ctx.args.chromium:
        env["EARCRATE_CHROMIUM"] = ctx.args.chromium
    rec = ctx.run_subprocess("workbench_dom", [ctx.python, "tests/manual/verify_workbench_dom.py"],
                             env=env, timeout=1200)
    receipt = shots / "receipt.json"
    detail = {"log": rec["log"], "exit_code": rec["exit_code"],
              "screenshots": sorted(ctx._rel(p) for p in shots.glob("*.png"))}
    if rec["exit_code"] != 0 or not receipt.exists():
        return FAILED, dict(detail, reason="DOM harness exited nonzero or wrote no receipt.json")
    try:
        r = json.loads(receipt.read_text(encoding="utf-8"))
    except Exception as exc:
        return FAILED, dict(detail, reason=f"unreadable DOM receipt: {exc}")
    modes = r.get("modes") or {}
    detail["modes"] = {m: {"ok": v.get("ok"), "console_errors": len(v.get("console_errors") or [])}
                       for m, v in modes.items()}
    need = {"package", "singlefile"}
    ok = need <= set(modes) and all(v.get("ok") and not (v.get("console_errors") or []) for v in modes.values())
    return (PASSED if ok else FAILED), dict(detail, reason=None if ok else "a mode was missing / not ok / had console errors")


def stage_acceptance(ctx):
    dest = ctx.scratch / "acceptance"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    rec = ctx.run_subprocess("acceptance",
                             [ctx.python, "-m", "earcrate", "project", "acceptance", "--destination", str(dest)],
                             env={"EARCRATE_HOME": str(ctx.scratch / "acc_home")}, timeout=1800, json_out=True)
    fail, d = _fail_if_unreadable(rec, {"destination": str(dest)})
    if fail:
        return fail, d
    receipt = dest / "acceptance_receipt.json"
    acc_ok = bool((rec["result"] or {}).get("ok")) and receipt.exists()
    return (PASSED if acc_ok else FAILED), dict(d, acceptance_ok=bool((rec["result"] or {}).get("ok")),
                                                reason=None if acc_ok else "acceptance did not report ok / wrote no receipt")


def stage_real_project(ctx):
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": (ctx.state.data.get("scratch_workspace") or {}).get(
            "reason", "no durable-state clone; real-library compile skipped (configure --workspace first)")}
    rec = ctx.run_subprocess("real_compile",
                             [ctx.python, "-m", "earcrate", "project", "compile", "--profile", ctx.args.profile,
                              "--seconds", str(ctx.args.real_seconds), "--name", "Rig Receipt Project", "--render"],
                             env=env, timeout=3600, json_out=True)
    fail, d = _fail_if_unreadable(rec)
    if fail:
        return fail, dict(d, reason="compile failed or crate not ready — the log has the exact reason")
    proj = rec["result"] or {}
    pid = proj.get("project_id")
    if not pid:
        return FAILED, dict(d, reason="compile produced no project_id")
    ctx.state.data["real_project_id"] = pid
    d.update({"project_id": pid, "initial_revision_sha": proj.get("revision_sha"), "score_sha": proj.get("score_sha")})
    rr = ctx.run_subprocess("real_render", [ctx.python, "-m", "earcrate", "project", "render", pid],
                            env=env, timeout=1800, json_out=True)
    f2, d2 = _fail_if_unreadable(rr)
    if f2:
        return f2, dict(d, render=d2, reason="render did not return a readable result")
    rj = rr["result"] or {}
    d.update({"render_type": rj.get("type"), "render_path": rj.get("path"), "report_path": rj.get("report"),
              "mastering_child_revision_sha": rj.get("revision_sha"), "render_score_sha": rj.get("score_sha")})
    if rj.get("type") != "render_project" or not rj.get("path") or not Path(rj["path"]).exists():
        return FAILED, dict(d, reason="render did not publish a WAV")
    d["render_sha256"] = sha256_file(Path(rj["path"]))
    show = ctx.run_subprocess("real_show", [ctx.python, "-m", "earcrate", "project", "show", pid],
                              env=env, timeout=300, json_out=True)
    if show.get("result_readable"):
        d["active_revision_sha"] = ((show["result"] or {}).get("project") or {}).get("active_revision_sha")
    ex = ctx.run_subprocess("real_export", [ctx.python, "-m", "earcrate", "project", "export", pid],
                            env=env, timeout=600, json_out=True)
    if ex.get("result_readable"):
        for fmt in ("edl", "rpp", "sheet"):
            d[f"export_{fmt}"] = (ex["result"] or {}).get(fmt)
    return PASSED, d


def stage_real_render_verdict(ctx):
    rp = ctx.state.stage("real_project")["detail"] or {}
    render_path = rp.get("render_path")
    present = bool(render_path) and Path(render_path).exists()
    detail = {"render_path": render_path, "render_present": present,
              "note": "human keep/reject on the real render; gate success is NEVER inferred as a keep"}
    if not present:
        return FAILED, dict(detail, reason="no real render to judge (real_project did not publish a WAV)")
    v = ctx.args.verdict_real_render
    if v in ("keep", "reject"):
        return PASSED, dict(detail, verdict=v)
    return PENDING_MANUAL, dict(detail, verdict=None, how="re-run with --verdict-real-render keep|reject")


def stage_edit_undo_redo(ctx):
    env = _scratch_env(ctx)
    pid = ctx.state.data.get("real_project_id")
    if env is None or not pid:
        return SKIPPED, {"reason": "no real project from the compile stage; edit lifecycle skipped"}
    rec = ctx.run_helper("edit_undo_redo", _EDIT_LIFECYCLE_SRC, [pid], env=env, timeout=1800)
    if rec["exit_code"] != 0 or not rec.get("result_readable"):
        return FAILED, {"log": rec["log"], "reason": "edit helper failed or wrote no readable result"}
    r = rec["result"] or {}
    ok = bool(r.get("new_revision")) and bool(r.get("edited_pcm_differs")) and \
        bool(r.get("pcm_identity_restored")) and bool(r.get("reopened_head_matches"))
    return (PASSED if ok else FAILED), dict(r, reason=None if ok else "edit was a no-op, PCM did not change, undo did not restore, or head did not reopen")


def stage_ranker(ctx):
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": "no durable-state clone; ranker training skipped"}
    tr = ctx.run_subprocess("ranker_train", [ctx.python, "-m", "earcrate", "train-ranker", "--profile", ctx.args.profile],
                            env=env, timeout=600, json_out=True)
    if tr["exit_code"] != 0 or not tr.get("result_readable"):
        return FAILED, {"log": tr["log"], "reason": "train-ranker failed or unreadable result"}
    res = tr["result"] or {}
    if res.get("ok") is False:
        return SKIPPED, {"reason": res.get("reason"), "skipped_kind": "skipped_insufficient_data"}
    cmp = ctx.run_helper("ranker_compare", _RANKER_COMPARE_SRC, [ctx.args.profile], env=env, timeout=600)
    if cmp["exit_code"] != 0 or not cmp.get("result_readable"):
        return FAILED, {"log": cmp["log"], "reason": "ranker off/on comparison helper failed"}
    c = cmp["result"] or {}
    detail = {"model_sha": res.get("model_sha"), "n_approved": res.get("n_approved"), "n_rejected": res.get("n_rejected"),
              "pool_size": c.get("pool_size"), "membership_identical": c.get("membership_identical"),
              "order_changed": c.get("order_changed"), "off_order_head": c.get("off_order_head"),
              "on_order_head": c.get("on_order_head")}
    ok = bool(c.get("ok")) and c.get("membership_identical") is True and (c.get("pool_size") or 0) > 0
    return (PASSED if ok else FAILED), dict(detail, reason=None if ok else "ranker changed pool MEMBERSHIP (must be a pure reorder) or pool was empty")


def stage_piano(ctx):
    env = _scratch_env(ctx)
    if env is None:
        return SKIPPED, {"reason": "no durable-state clone; piano session skipped"}
    rid = f"rig_{ctx.state.data['run_id']}"
    cap = int(ctx.args.piano_iterations)
    r1 = ctx.run_subprocess("piano_1", [ctx.python, "-m", "earcrate", "project", "piano", "--personas", ctx.args.profile,
                                        "--iterations", str(cap), "--run-id", rid], env=env, timeout=3600, json_out=True)
    if r1["exit_code"] != 0 or not r1.get("result_readable"):
        return FAILED, {"log": r1["log"], "reason": "piano run 1 failed or unreadable"}
    a = r1["result"] or {}
    if not (a.get("complete") and (a.get("attempted") or 0) <= cap):
        return FAILED, {"reason": f"piano run 1 not bounded/complete: attempted={a.get('attempted')} cap={cap}",
                        "run1": {k: a.get(k) for k in ("attempted", "complete", "stop_reason")}}
    first = a.get("attempts") or []
    r2 = ctx.run_subprocess("piano_2", [ctx.python, "-m", "earcrate", "project", "piano", "--personas", ctx.args.profile,
                                        "--iterations", str(cap + 2), "--run-id", rid], env=env, timeout=3600, json_out=True)
    if r2["exit_code"] != 0 or not r2.get("result_readable"):
        return FAILED, {"log": r2["log"], "reason": "piano resume run failed or unreadable"}
    b = r2["result"] or {}
    second = b.get("attempts") or []
    preserved = second[:len(first)] == first
    detail = {"run_id": rid, "cap": cap,
              "run1": {k: a.get(k) for k in ("attempted", "kept", "discarded", "errored", "stop_reason", "complete")},
              "resume": {k: b.get(k) for k in ("attempted", "kept", "discarded", "errored", "stop_reason", "complete")},
              "prior_attempts_preserved_verbatim": preserved,
              "resumed_at_least_prior": (b.get("attempted") or 0) >= (a.get("attempted") or 0)}
    ok = bool(b.get("complete")) and preserved and (b.get("attempted") or 0) <= cap + 2
    return (PASSED if ok else FAILED), dict(detail, reason=None if ok else "resume did not preserve prior attempts verbatim, or exceeded the cap")


def stage_allin1(ctx):
    env = _scratch_env(ctx) or {"EARCRATE_HOME": str(Path(ctx.args.workspace).expanduser())}
    rec = ctx.run_helper("allin1", _ALLIN1_SAMPLE_SRC, [str(ctx.args.real_seconds)], env=env, timeout=3600)
    if not rec.get("result_readable"):
        return FAILED, {"log": rec["log"], "reason": "allin1 helper wrote no readable result"}
    r = rec["result"] or {}
    cap = r.get("capability") or {}
    if not cap.get("ready"):
        return SKIPPED, {"reason": "allin1 not installed on this box", "install": "pip install allin1",
                         "rerun": "install allin1, then re-run with the SAME --run-id to resume this stage",
                         "note": "the stub-based gate is adapter SHAPE only — NOT model validation"}
    if r.get("reason"):   # e.g. silent librosa fallback detected
        return FAILED, {"reason": r["reason"], "log": rec["log"]}
    ok = bool(r.get("ok")) and (r.get("tracks_sampled") or 0) > 0
    return (PASSED if ok else FAILED), {k: r.get(k) for k in
            ("tracks_sampled", "librosa_downbeat_conf", "allin1_downbeat_conf", "mean_conf_delta",
             "transition_feasibility_change")}


def stage_rubberband(ctx):
    env = _scratch_env(ctx)
    pid = ctx.state.data.get("real_project_id")
    cap_rec = ctx.run_helper("rubberband_probe", _CAP_SRC, ["transform"], timeout=120)
    cap = cap_rec.get("result") or {}
    if not cap.get("ready"):
        return SKIPPED, {"reason": "Rubber Band CLI / pyrubberband not installed",
                         "install": "install the 'rubberband' binary + pip install pyrubberband",
                         "note": "default transform is UNCHANGED; this script never flips the default or bumps ENGINE_VERSION"}
    if env is None or not pid:
        return SKIPPED, {"reason": "no real project to A/B; render one first"}
    out_dir = ctx.scratch / "rubberband_ab"; out_dir.mkdir(parents=True, exist_ok=True)
    dflt = out_dir / "default.wav"; rbw = out_dir / "rubberband.wav"
    r_def = ctx.run_subprocess("rb_default", [ctx.python, "-m", "earcrate", "project", "render", pid, "--dst", str(dflt)],
                               env=env, timeout=1800, json_out=True)
    rb_env = dict(env); rb_env["EARCRATE_TRANSFORM"] = "rubberband"   # child-process override only
    r_rb = ctx.run_subprocess("rb_rubberband", [ctx.python, "-m", "earcrate", "project", "render", pid, "--dst", str(rbw)],
                              env=rb_env, timeout=1800, json_out=True)
    prov = ctx.run_helper("rb_provider", _RESOLVE_TRANSFORM_SRC, [], env=rb_env, timeout=120)
    resolved = (prov.get("result") or {}).get("effective")
    detail = {"default_log": r_def["log"], "rubberband_log": r_rb["log"],
              "note": "child-process EARCRATE_TRANSFORM override only; default not changed",
              "provider_resolved_in_env": resolved,
              "report_default": (r_def.get("result") or {}).get("report"),
              "report_rubberband": (r_rb.get("result") or {}).get("report")}

    def rok(rec, dst):
        return bool(rec.get("result_readable") and (rec["result"] or {}).get("type") == "render_project" and Path(dst).exists())

    if dflt.exists():
        detail["default_sha256"] = sha256_file(dflt)
    if rbw.exists():
        detail["rubberband_sha256"] = sha256_file(rbw)
    detail["hashes_differ"] = detail.get("default_sha256") != detail.get("rubberband_sha256")
    mech = rok(r_def, dflt) and rok(r_rb, rbw) and resolved == "rubberband"
    if not mech:
        return FAILED, dict(detail, reason="a render failed, an artifact/report was missing, or the provider did not resolve to rubberband")
    v = ctx.args.verdict_rubberband
    if v in ("default", "rubberband", "tie"):
        return PASSED, dict(detail, verdict=v)   # verdict ON TOP of a green mechanical result
    return PENDING_MANUAL, dict(detail, verdict=None, how="listen to both, then --verdict-rubberband default|rubberband|tie")


def stage_techno(ctx):
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
    rec = ctx.run_helper("techno", _TECHNO_SRC, [str(vocal), str(ctx.args.real_seconds)], env=env, timeout=3600)
    if not rec.get("result_readable"):
        return FAILED, {"log": rec["log"], "reason": "techno helper wrote no readable result"}
    r = rec["result"] or {}
    detail = {"external_vocal_basename": r.get("external_vocal_basename"),   # basename only; never the path/file
              "external_vocal_in_registry": r.get("external_vocal_in_registry"),
              "render_type": r.get("render_type"), "render_ok": r.get("render_ok"),
              "project_id": r.get("project_id"), "revision_sha": r.get("revision_sha"),
              "note": "external source referenced, never bundled"}
    mech = bool(r.get("render_ok")) and bool(r.get("external_vocal_in_registry"))
    if not mech:
        return FAILED, dict(detail, reason=r.get("reason") or "render failed, or the external vocal is not present in the revision's source registry")
    v = ctx.args.verdict_techno
    if v in ("keep", "reject"):
        return PASSED, dict(detail, verdict=v)
    return PENDING_MANUAL, dict(detail, verdict=None, how="audition, then --verdict-techno keep|reject")


STAGE_FUNCS = {
    "gates": stage_gates, "verify_package": stage_verify_package, "workbench_dom": stage_workbench_dom,
    "acceptance": stage_acceptance, "real_project": stage_real_project,
    "real_render_verdict": stage_real_render_verdict, "edit_undo_redo": stage_edit_undo_redo,
    "ranker": stage_ranker, "piano": stage_piano, "allin1": stage_allin1,
    "rubberband": stage_rubberband, "techno": stage_techno,
}


# ---- helper subprocess sources: each writes its result JSON to argv[1] -------
_CAP_SRC = r'''
import json, sys
which = sys.argv[2] if len(sys.argv) > 2 else "transform"
if which == "transform":
    from earcrate.providers.transform import transform_capability as cap
else:
    from earcrate.providers.beats import beat_capability as cap
open(sys.argv[1], "w").write(json.dumps(cap()))
'''

_RESOLVE_TRANSFORM_SRC = r'''
import json, sys
from earcrate.providers.transform import resolve_transform_provider
open(sys.argv[1], "w").write(json.dumps({"effective": resolve_transform_provider(None)}))
'''

_EDIT_LIFECYCLE_SRC = r'''
import json, sys, hashlib
from pathlib import Path
import soundfile as sf
from earcrate.app import EarcrateCore
from earcrate.project.model import ScoreRevision
from earcrate.project.policy import compile_taste_policy, policy_gain_bounds
out_path, pid = sys.argv[1], sys.argv[2]
res = {"ok": False}
def pcm(path):
    a, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return hashlib.sha256(a.tobytes()).hexdigest()
try:
    core = EarcrateCore(); core.load_config_if_present()
    r0 = core.project_render(pid); sha0 = pcm(r0["path"])
    show = core.project_show(pid); rev = ScoreRevision.from_dict(show["revision"])
    base_sha = show["revision"]["revision_sha"]
    clip = None; rail = None
    for t in rev.tracks:
        for c in (t.get("clips") or []):
            if not c.get("locked"):
                clip = c; rail = (c.get("rail") or "floor"); break
        if clip:
            break
    if not clip:
        res["reason"] = "no unlocked clip"; Path(out_path).write_text(json.dumps(res)); sys.exit(0)
    pol = compile_taste_policy(rev.intent["taste_profile"]["id"])
    lo, tgt, hi = policy_gain_bounds(pol, rail)
    cur = float(clip.get("gain_db") or 0.0)
    # a value guaranteed to DIFFER from cur while staying inside [lo, hi]
    cand = cur + 1.0 if (cur + 1.0) <= hi else cur - 1.0
    if abs(cand - cur) < 1e-9 or not (lo <= cand <= hi):
        cand = (lo + hi) / 2.0
        if abs(cand - cur) < 1e-6:
            cand = cur - 1.0 if (cur - 1.0) >= lo else cur + 1.0
    res.update({"clip_id": clip.get("clip_id"), "rail": rail, "gain_from": cur, "gain_to": cand, "gain_range": [lo, hi]})
    edit = core.project_edit(pid, {"actor": "rig", "kind": "set_gain",
                                   "payload": {"clip_id": clip["clip_id"], "gain_db": cand}})
    edited_sha = edit["revision"]["revision_sha"]
    res["new_revision"] = edited_sha != base_sha
    r1 = core.project_render(pid); sha1 = pcm(r1["path"])
    res.update({"initial_pcm": sha0, "edited_pcm": sha1, "edited_pcm_differs": sha1 != sha0})
    core.project_undo(pid)
    r2 = core.project_render(pid); sha2 = pcm(r2["path"])
    res.update({"restored_pcm": sha2, "pcm_identity_restored": sha2 == sha0})
    core.project_redo(pid)
    reopened = EarcrateCore(); reopened.load_config_if_present()
    res["reopened_head_matches"] = reopened.project_show(pid)["project"]["active_revision_sha"] == edited_sha
    res["ok"] = bool(res["new_revision"] and res["edited_pcm_differs"] and res["pcm_identity_restored"] and res["reopened_head_matches"])
except Exception as exc:
    import traceback
    res["error"] = f"{type(exc).__name__}: {exc}"; res["trace"] = traceback.format_exc()[-2000:]
Path(out_path).write_text(json.dumps(res))
'''

_RANKER_COMPARE_SRC = r'''
import json, os, sys
from earcrate.app import EarcrateCore
out, profile = sys.argv[1], sys.argv[2]
res = {"ok": False}
def ids(pool):
    return [str(a.get("id") or a.get("atom_id") or "") for a in pool]
try:
    core = EarcrateCore(); core.load_config_if_present()
    os.environ.pop("EARCRATE_RANKER", None)
    off = ids(core.approved_atom_pool(profile))
    os.environ["EARCRATE_RANKER"] = "on"
    on = ids(core.approved_atom_pool(profile))
    os.environ.pop("EARCRATE_RANKER", None)
    res["pool_size"] = len(off)
    res["membership_identical"] = sorted(off) == sorted(on)
    res["order_changed"] = off != on
    res["off_order_head"] = off[:8]; res["on_order_head"] = on[:8]
    res["ok"] = res["membership_identical"] and len(off) > 0
except Exception as exc:
    import traceback
    res["error"] = f"{type(exc).__name__}: {exc}"
open(out, "w").write(json.dumps(res))
'''

_ALLIN1_SAMPLE_SRC = r'''
import json, os, sys, statistics as st
from pathlib import Path
import numpy as np
from earcrate.app import EarcrateCore
from earcrate.analyze.decode import decode_audio
from earcrate.analyze.features import compute_pcm_features
from earcrate.providers.beats import beat_capability
out = sys.argv[1]; seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
res = {"ok": False, "tracks_sampled": 0}
def dist(v):
    return {"n": len(v), "mean": round(st.mean(v), 4), "min": round(min(v), 4),
            "max": round(max(v), 4), "stdev": round(st.pstdev(v), 4)} if v else None
try:
    cap = beat_capability(); res["capability"] = cap
    if not cap.get("ready"):
        res["reason"] = "allin1 not ready"; Path(out).write_text(json.dumps(res)); sys.exit(0)
    core = EarcrateCore(); core.load_config_if_present()
    rows = core.conn().execute("SELECT path FROM files WHERE COALESCE(present,1)=1 LIMIT 8").fetchall()
    sr = 22050; lib = []; al = []
    for r in rows:
        p = r[0]
        try:
            y = decode_audio(Path(p), sr=sr, duration=seconds)   # REAL signature: duration=
        except Exception:
            continue
        os.environ.pop("EARCRATE_BEATS", None)
        L = compute_pcm_features(np.asarray(y, dtype=np.float32), sr)
        os.environ["EARCRATE_BEATS"] = "allin1"
        A = compute_pcm_features(np.asarray(y, dtype=np.float32), sr)
        os.environ.pop("EARCRATE_BEATS", None)
        if A.get("beat_backend") != "allin1":   # silent librosa fallback -> FAIL the stage
            res["reason"] = f"allin1 requested but backend was {A.get('beat_backend')} (silent fallback)"
            Path(out).write_text(json.dumps(res)); sys.exit(0)
        lib.append(float(L.get("bpm_confidence") or 0.0)); al.append(float(A.get("bpm_confidence") or 0.0))
        res["tracks_sampled"] += 1
    if res["tracks_sampled"] == 0:
        res["reason"] = "no decodable tracks in the library sample"; Path(out).write_text(json.dumps(res)); sys.exit(0)
    res["librosa_downbeat_conf"] = dist(lib); res["allin1_downbeat_conf"] = dist(al)
    res["mean_conf_delta"] = round(st.mean(al) - st.mean(lib), 4)
    res["transition_feasibility_change"] = ("no_change" if abs(res["mean_conf_delta"]) < 1e-6 else
        {"direction": "up" if res["mean_conf_delta"] > 0 else "down", "mean_downbeat_conf_delta": res["mean_conf_delta"],
         "basis": "downbeat-confidence distribution shift (the input to transition feasibility)"})
    res["ok"] = True
except Exception as exc:
    import traceback
    res["error"] = f"{type(exc).__name__}: {exc}"
Path(out).write_text(json.dumps(res))
'''

_TECHNO_SRC = r'''
import json, os, sys
from pathlib import Path
from earcrate.app import EarcrateCore
out, vocal = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
res = {"ok": False}
try:
    core = EarcrateCore(); core.load_config_if_present()
    prop = core.propose_external_remix({"target_path": vocal, "taste_profile": "remix_techno_v1",
                                        "target_seconds": seconds})
    arr = prop.get("arrangement") or prop.get("plan")
    if not arr:
        res["reason"] = f"external remix produced no arrangement (keys: {list(prop.keys())})"
        Path(out).write_text(json.dumps(res)); sys.exit(0)
    imp = core.project_import_arrangement(arr, name="Techno Proof",
                                          created_by={"actor": "rig", "reason": "techno_proof"})
    pid = imp["project"]["project_id"]
    show = core.project_show(pid)
    reg = show["revision"].get("source_registry") or {}
    base = os.path.basename(vocal)
    in_registry = any(base in str(v.get("path", "")) for v in reg.values())
    rr = core.project_render(pid)
    res.update({"project_id": pid, "revision_sha": imp["project"]["active_revision_sha"],
                "external_vocal_basename": base, "external_vocal_in_registry": in_registry,
                "render_type": rr.get("type"), "render_path": rr.get("path"),
                "render_ok": rr.get("type") == "render_project" and bool(rr.get("path")) and Path(str(rr.get("path"))).exists()})
    if not in_registry:
        res["reason"] = "external vocal not found in the imported revision's source registry"
    res["ok"] = bool(res["render_ok"]) and in_registry
except Exception as exc:
    import traceback
    res["error"] = f"{type(exc).__name__}: {exc}"; res["trace"] = traceback.format_exc()[-2000:]
Path(out).write_text(json.dumps(res))
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

    # ---- resume vs new + HEAD guard (a different HEAD ALWAYS needs a new run_id) ----
    if state_path.exists():
        state = RunState.load(state_path)
        prior_head = state.data.get("git_head")
        if prior_head and prior_head != head_now["head"]:
            print(f"REFUSING: run {args.run_id} was created at HEAD {prior_head[:12]} but current HEAD is "
                  f"{head_now['head'][:12]}. A different HEAD requires a NEW --run-id — results from another "
                  f"commit must never be appended to this receipt.", file=sys.stderr)
            return EXIT_FAILED
        print(f"resuming run {args.run_id} at HEAD {head_now['head'][:12]}")
    else:
        state = RunState.new(state_path, args.run_id, head_now["head"], _args_snapshot(args), STAGE_META)
        state.save()
        print(f"new run {args.run_id} at HEAD {head_now['head'][:12]}")

    ctx = Ctx(args, state, scratch, logs_dir)

    # ---- preflight: env, then RESOLVE master_root and check scratch safety against
    # the ACTUAL music library. The safety check is only meaningful once the real
    # music root is known, so an UNRESOLVED master_root is a refusal — we never
    # accept a scratch path (or run any crate-dependent stage) on an unverified
    # location. Item 1: resolve master_root, run assert_scratch_safe against it,
    # refuse if it cannot be resolved, and never reach execute_stages otherwise. ----
    state.data["environment"] = _preflight_env(args)
    cfg = resolve_workspace_config(ctx)
    state.data["workspace_config"] = cfg
    master = cfg.get("master_root")
    master_resolved = bool(master) and Path(master).expanduser().exists()
    pf: Dict[str, Any] = {"git": head_now, "checked_at": utcnow(), "master_root_resolved": master_resolved}
    scratch_safe = None    # tri-state: only True/False once we have a real music root to check against
    if master_resolved:
        scratch_safe = True
        try:
            assert_scratch_safe(scratch, Path(master))    # against the ACTUAL music library
        except Exception as exc:
            scratch_safe = False; pf["scratch_error"] = str(exc)
    pf["scratch_safe"] = scratch_safe
    pf["dirty_refused"] = bool(head_now["dirty"] and not args.allow_dirty)
    state.data["preflight"] = pf
    state.save()

    if head_now["dirty"] and not args.allow_dirty:
        print("REFUSING: git working tree is dirty. Commit/stash, or pass --allow-dirty.", file=sys.stderr)
        return EXIT_FAILED
    if not master_resolved:
        print("REFUSING: could not resolve master_root (the real music library) from --workspace "
              f"({args.workspace}). The scratch-safety check cannot run against an unknown music root, "
              "so no crate-dependent stage may proceed. Run `earcrate configure --music <folder>` in "
              "that workspace first, then re-run.", file=sys.stderr)
        return EXIT_FAILED
    if not scratch_safe:
        print(f"REFUSING: scratch is unsafe vs the resolved music library: {pf.get('scratch_error')}", file=sys.stderr)
        return EXIT_FAILED

    # ---- durable-state clone (consistent SQLite backup). master_root unresolved ->
    # ok=False -> crate-dependent stages skip honestly rather than pollute/crash. ----
    if not (state.data.get("scratch_workspace") or {}).get("ok"):
        try:
            state.data["scratch_workspace"] = _prepare_scratch_workspace(ctx)
        except Exception as exc:
            state.data["scratch_workspace"] = {"ok": False, "reason": f"prep error: {exc}"}
        state.save()

    # ---- run stages in order, checkpoint atomically after each ----
    try:
        overall = execute_stages(ctx, state, STAGE_META, STAGE_FUNCS)
    except KeyboardInterrupt:
        # No OFFICIAL receipt on interrupt: state.json holds the resume checkpoint,
        # and the committable receipt is only written once every stage has a
        # terminal (documented) status.
        print("\ninterrupted — checkpoint saved in state.json; resume with the same --run-id", file=sys.stderr)
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


def _bin_version(name: str) -> Dict[str, Any]:
    """Executable PATH + version string (booleans alone don't satisfy the receipt)."""
    path = shutil.which(name)
    if not path:
        return {"path": None, "version": None}
    version = None
    for flag in ("-version", "--version", "-V"):
        with contextlib.suppress(Exception):
            out = subprocess.run([name, flag], capture_output=True, text=True, timeout=15)
            text = (out.stdout or out.stderr or "").strip()
            if text:
                version = text.splitlines()[0].strip()
                break
    return {"path": path, "version": version}


def _pkg_version(mod: str) -> Optional[str]:
    try:
        m = __import__(mod)
        return getattr(m, "__version__", None) or "installed"
    except Exception:
        return None


def _preflight_env(args) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ffmpeg": _bin_version("ffmpeg"),
        "ffprobe": _bin_version("ffprobe"),
        "rubberband": _bin_version("rubberband"),
        "packages": {m: _pkg_version(m) for m in
                     ("pyrubberband", "allin1", "demucs", "playwright", "torch",
                      "numpy", "scipy", "librosa", "soundfile", "mutagen")},
        "configured_providers": {k: os.environ.get(k) for k in
                                 ("EARCRATE_BEATS", "EARCRATE_TRANSFORM", "EARCRATE_RANKER", "EARCRATE_STEMS")},
    }
    with contextlib.suppress(Exception):
        import psutil  # optional
        env["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    with contextlib.suppress(Exception):
        import torch  # type: ignore
        env["cuda"] = {"available": bool(torch.cuda.is_available()),
                       "cuda_version": getattr(getattr(torch, "version", None), "cuda", None)}
        if torch.cuda.is_available():
            env["gpu"] = {"name": torch.cuda.get_device_name(0),
                          "driver": getattr(getattr(torch, "version", None), "cuda", None)}
    return env


def _write_receipts(state: RunState, scratch: Path, run_dir: Path, overall: str) -> None:
    """Committable JSON (redacted) + readable Markdown. Uses ONLY the four
    documented statuses — any stray internal `pending` is mapped to `skipped`
    (not reached) so `pending` never leaks into an official receipt. Large
    artifacts stay in scratch; only these files (+ selected screenshots) commit."""
    home = str(Path.home())
    committable_stages = []
    for s in state.data["stages"]:
        st = s["status"]
        detail = dict(s["detail"] or {})
        if st == PENDING:
            st = SKIPPED
            detail = dict(detail, not_reached="run did not reach this stage")
        committable_stages.append({"key": s["key"], "name": s["name"], "tier": s["tier"],
                                   "required": s["required"], "status": st,
                                   "duration_s": s["duration_s"], "detail": detail, "error": s.get("error")})
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "run_id": state.data["run_id"],
        "git_head": state.data["git_head"],
        "created_at": state.data["created_at"],
        "finished_at": utcnow(),
        "overall": overall,
        "exit_code": exit_code_for(overall),
        "status_vocabulary": [PASSED, FAILED, SKIPPED, PENDING_MANUAL],
        "evidence_tiers": {
            TIER_CLOUD: "cloud-CI gate numbers are NOT in this receipt; this is on-box evidence only",
            TIER_RIG: "Windows-rig mechanical proof (gates, package, DOM, acceptance)",
            TIER_REAL: "real-library proof (compile/render/edit/undo/piano/ranker on the real crate)",
            TIER_GPU: "GPU/provider proof (allin1, Rubber Band)",
            TIER_HUMAN: "human listening verdicts (real render, Rubber Band A/B, techno)",
        },
        "environment": state.data.get("environment", {}),
        "workspace_config": {k: (state.data.get("workspace_config") or {}).get(k)
                             for k in ("master_root", "working_root", "agent_root", "stem_provider")},
        "preflight": {k: v for k, v in (state.data.get("preflight") or {}).items()},
        "scratch_workspace": {k: v for k, v in (state.data.get("scratch_workspace") or {}).items()},
        "stages": committable_stages,
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
    icon = {PASSED: "✅ passed", FAILED: "❌ failed", SKIPPED: "⏭️ skipped", PENDING_MANUAL: "⏸️ pending_manual"}
    for i, s in enumerate(receipt["stages"], 1):
        L.append(f"| {i} | {s['name']} | {s['tier']} | {'yes' if s['required'] else 'no'} | "
                 f"{icon.get(s['status'], s['status'])} | {s['duration_s'] if s['duration_s'] is not None else '—'} |")
    L.append("")
    for s in receipt["stages"]:
        d = s.get("detail") or {}
        notable = {k: d[k] for k in ("discovered", "passed", "dist_sha256", "project_id", "render_sha256",
                                     "edited_pcm_differs", "pcm_identity_restored", "reopened_head_matches",
                                     "model_sha", "membership_identical", "order_changed", "prior_attempts_preserved_verbatim",
                                     "provider_resolved_in_env", "external_vocal_in_registry", "tracks_sampled",
                                     "reason", "verdict", "install", "how") if k in d}
        if notable:
            L.append(f"- **{s['key']}** ({s['status']}): " + ", ".join(f"{k}=`{v}`" for k, v in notable.items()))
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
