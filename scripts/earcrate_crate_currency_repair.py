#!/usr/bin/env python3
"""Repair EarCrate's current crate projection without rendering music.

Default mode is read-only. ``--apply`` makes a SQLite backup, then asks the live
checkout to scan, analyze, extract current-generation loops, rebuild one profile's
EarAtoms, and rebuild its compatibility graph. Historical atoms are measured in the
receipt but are never promoted or rebound by this script.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import traceback
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACTIVE = """
COALESCE(f.present,1)=1
AND f.audio_sha256_scope='full'
AND f.audio_sha256 IS NOT NULL
AND COALESCE(l.source_audio_generation,0)=COALESCE(f.audio_generation,0)
AND (l.source_audio_sha256=f.audio_sha256
     OR (l.source_audio_sha256 IS NULL AND COALESCE(f.audio_generation,0)=0))
"""


def write_json(path: Path, value: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False,
                       default=str) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return hashlib.sha256(data).hexdigest()


def tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def scalar(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = db.execute(sql, params).fetchone()
    return int((row[0] if row else 0) or 0)


def audit(db: sqlite3.Connection, profile: str) -> dict[str, Any]:
    present_tables = tables(db)
    required = {"files", "features", "loops", "ear_atoms"}
    report: dict[str, Any] = {
        "profile": profile,
        "integrity_check": [str(r[0]) for r in db.execute("PRAGMA integrity_check")],
        "required_tables_missing": sorted(required - present_tables),
        "counts": {},
        "active_role_counts": {},
        "currency_ready": False,
        "blocker": None,
    }
    if report["required_tables_missing"]:
        report["blocker"] = "schema_missing_required_tables"
        return report

    fcols, lcols = columns(db, "files"), columns(db, "loops")
    identity_schema = (
        {"present", "audio_sha256_scope", "audio_sha256", "audio_generation"} <= fcols
        and {"source_audio_sha256", "source_audio_generation"} <= lcols
    )
    report["active_generation_predicate_supported"] = identity_schema
    c = report["counts"]
    c["files_total"] = scalar(db, "SELECT COUNT(*) FROM files")
    c["files_present"] = scalar(db, "SELECT COUNT(*) FROM files WHERE COALESCE(present,1)=1")
    c["features_total"] = scalar(db, "SELECT COUNT(*) FROM features")
    c["loops_total"] = scalar(db, "SELECT COUNT(*) FROM loops")
    c["atoms_profile_total"] = scalar(
        db, "SELECT COUNT(*) FROM ear_atoms WHERE taste_profile=?", (profile,))
    c["atoms_profile_approved_total"] = scalar(
        db, "SELECT COUNT(*) FROM ear_atoms WHERE taste_profile=? AND status='approved'",
        (profile,))

    if identity_schema:
        c["files_present_full_identity"] = scalar(db, """
            SELECT COUNT(*) FROM files WHERE COALESCE(present,1)=1
            AND audio_sha256_scope='full' AND audio_sha256 IS NOT NULL""")
        c["loops_active_generation"] = scalar(
            db, f"SELECT COUNT(*) FROM loops l JOIN files f ON f.id=l.file_id WHERE {ACTIVE}")
        c["atoms_profile_active"] = scalar(db, f"""
            SELECT COUNT(*) FROM ear_atoms a JOIN loops l ON l.id=a.loop_id
            JOIN files f ON f.id=l.file_id WHERE a.taste_profile=? AND {ACTIVE}""", (profile,))
        c["atoms_profile_active_approved"] = scalar(db, f"""
            SELECT COUNT(*) FROM ear_atoms a JOIN loops l ON l.id=a.loop_id
            JOIN files f ON f.id=l.file_id WHERE a.taste_profile=?
            AND a.status='approved' AND {ACTIVE}""", (profile,))
        c["active_approved_source_files"] = scalar(db, f"""
            SELECT COUNT(DISTINCT a.file_id) FROM ear_atoms a
            JOIN loops l ON l.id=a.loop_id JOIN files f ON f.id=l.file_id
            WHERE a.taste_profile=? AND a.status='approved' AND {ACTIVE}""", (profile,))
        report["active_role_counts"] = {
            str(row[0]): int(row[1]) for row in db.execute(f"""
                SELECT a.ear_role,COUNT(*) FROM ear_atoms a
                JOIN loops l ON l.id=a.loop_id JOIN files f ON f.id=l.file_id
                WHERE a.taste_profile=? AND a.status='approved' AND {ACTIVE}
                GROUP BY a.ear_role ORDER BY a.ear_role""", (profile,))
        }
    else:
        for key in ("files_present_full_identity", "loops_active_generation",
                    "atoms_profile_active", "atoms_profile_active_approved",
                    "active_approved_source_files"):
            c[key] = None

    if report["integrity_check"] != ["ok"]:
        blocker = "sqlite_integrity_failure"
    elif not identity_schema:
        blocker = "identity_schema_too_old_for_active_generation_proof"
    elif c["files_present"] == 0:
        blocker = "no_present_source_files"
    elif c["files_present_full_identity"] == 0:
        blocker = "no_present_full_identity_files"
    elif c["loops_active_generation"] == 0:
        blocker = "no_loops_on_active_source_generation"
    elif c["atoms_profile_active"] == 0:
        blocker = "no_profile_atoms_on_active_source_generation"
    elif c["atoms_profile_active_approved"] == 0:
        blocker = "active_profile_atoms_exist_but_none_are_approved"
    else:
        blocker = None
    report["blocker"] = blocker
    report["currency_ready"] = blocker is None
    return report


def database_path(db: sqlite3.Connection) -> Path:
    row = next((r for r in db.execute("PRAGMA database_list") if str(r[1]) == "main"), None)
    if row is None or not row[2]:
        raise RuntimeError("SQLite did not report the live main database path")
    return Path(str(row[2])).resolve()


def backup(db: sqlite3.Connection, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    target = sqlite3.connect(str(destination))
    try:
        db.backup(target)
        target.commit()
        check = [str(r[0]) for r in target.execute("PRAGMA integrity_check")]
    finally:
        target.close()
    if check != ["ok"]:
        raise RuntimeError(f"backup integrity_check failed: {check}")
    return {"path": str(destination), "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "integrity_check": check}


def git_context() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                                    text=True, timeout=30, check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
    return {"root": str(ROOT), "head": run("rev-parse", "HEAD"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_porcelain": run("status", "--porcelain")}


def call(receipt: Path, number: int, name: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    started = dt.datetime.now(dt.timezone.utc)
    stage: dict[str, Any] = {"name": name, "started_at": started.isoformat(), "ok": False}
    try:
        result = fn(*args, **kwargs)
        stage["result"] = result
        stage["ok"] = not (isinstance(result, Mapping) and result.get("ok") is False)
        if not stage["ok"]:
            stage["error"] = str(result.get("error") or f"{name} returned ok=false")
    except Exception as exc:
        stage.update(error=str(exc), exception_type=type(exc).__name__,
                     traceback=traceback.format_exc())
    finished = dt.datetime.now(dt.timezone.utc)
    stage["finished_at"] = finished.isoformat()
    stage["elapsed_seconds"] = round((finished - started).total_seconds(), 3)
    write_json(receipt / f"stage-{number:02d}-{name}.json", stage)
    if not stage["ok"]:
        raise RuntimeError(stage.get("error") or f"{name} failed")
    return stage.get("result")


def default_output() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / "Downloads" / f"EarCrate-crate-currency-repair-{stamp}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="girl_talk_v1")
    parser.add_argument("--target-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-analysis", action="store_true")
    parser.add_argument("--force-loops", action="store_true")
    parser.add_argument("--force-crate", action="store_true")
    args = parser.parse_args(argv)
    out = (args.output or default_output()).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    stages: list[str] = []

    try:
        from earcrate.app import EarcrateCore
        core = EarcrateCore()
        cfg = core.ensure_config()
        db = core.conn()
        live_db = database_path(db)
        write_json(out / "CONTEXT.json", {
            "mode": "apply" if args.apply else "audit_only",
            "profile": args.profile,
            "target_seconds": args.target_seconds,
            "python": sys.executable,
            "earcrate_import_path": str(Path(inspect.getfile(EarcrateCore)).resolve()),
            "repository": git_context(),
            "database_path": str(live_db),
            "config": {"master_root": str(cfg.master_root),
                       "working_root": str(cfg.working_root),
                       "agent_root": str(cfg.agent_root)},
            "methods": {name: callable(getattr(core, name, None)) for name in
                        ("doctor", "scan", "analyze", "extract_loops",
                         "build_ear_crate", "taste_readiness",
                         "build_compatibility_graph")},
        })
        before = audit(db, args.profile)
        write_json(out / "AUDIT_BEFORE.json", before)
        if not args.apply:
            result = {"result": "audit_complete", "receipt_dir": str(out),
                      "apply_required": not before["currency_ready"], "audit": before}
            write_json(out / "RESULT.json", result)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if before["currency_ready"] else 2

        if bool(getattr(core, "status", {}).get("busy")):
            raise RuntimeError("EarCrate reports a busy background job; stop it first")
        missing = [name for name in ("scan", "analyze", "extract_loops", "build_ear_crate")
                   if not callable(getattr(core, name, None))]
        if missing:
            raise RuntimeError("live EarcrateCore lacks repair method(s): " + ", ".join(missing))

        bdir = Path(cfg.agent_root) / "archive" / "crate_currency_repair"
        bpath = bdir / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{live_db.name}"
        backup_receipt = backup(db, bpath)
        write_json(out / "DATABASE_BACKUP.json", backup_receipt)

        n = 0
        if callable(getattr(core, "doctor", None)):
            n += 1; stages.append("doctor"); call(out, n, "doctor", core.doctor)
        n += 1; stages.append("scan"); call(out, n, "scan", core.scan)
        n += 1; stages.append("analyze"); call(
            out, n, "analyze", core.analyze, limit=0, force=bool(args.force_analysis))
        n += 1; stages.append("extract_loops"); call(
            out, n, "extract_loops", core.extract_loops, limit=0,
            auto_approve=False, force=bool(args.force_loops))
        n += 1; stages.append("build_ear_crate"); call(
            out, n, "build_ear_crate", core.build_ear_crate, limit=0,
            force=bool(args.force_crate), taste_profile=args.profile, write_previews=False)

        after = audit(db, args.profile)
        write_json(out / "AUDIT_AFTER.json", after)
        if not after["currency_ready"]:
            raise RuntimeError("crate remains noncurrent after repair: " + str(after["blocker"]))

        readiness = graph = None
        if callable(getattr(core, "taste_readiness", None)):
            n += 1; stages.append("taste_readiness"); readiness = call(
                out, n, "taste_readiness", core.taste_readiness,
                args.profile, float(args.target_seconds))
        if callable(getattr(core, "build_compatibility_graph", None)):
            n += 1; stages.append("build_compatibility_graph"); graph = call(
                out, n, "build_compatibility_graph", core.build_compatibility_graph,
                args.profile, float(args.target_seconds), 0.0)

        ready = after["currency_ready"]
        if isinstance(readiness, Mapping):
            ready = ready and bool(readiness.get("ready"))
        if isinstance(graph, Mapping) and "edges" in graph:
            ready = ready and int(graph.get("edges") or 0) > 0
        result = {"result": "crate_currency_recovered", "receipt_dir": str(out),
                  "database_backup": backup_receipt, "before": before, "after": after,
                  "readiness": readiness, "graph": graph, "commission_ready": ready,
                  "next": ("rerun unchanged Robi V3.1 from a fresh extraction" if ready else
                           "currency recovered, but the 30-second profile contract is still unready")}
        write_json(out / "RESULT.json", result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if ready else 3
    except Exception as exc:
        refusal = {"result": "refused_crate_currency_repair", "error": str(exc),
                   "exception_type": type(exc).__name__, "traceback": traceback.format_exc(),
                   "receipt_dir": str(out), "completed_stages": stages}
        with contextlib.suppress(Exception):
            write_json(out / "REFUSAL.json", refusal)
        print(json.dumps(refusal, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
