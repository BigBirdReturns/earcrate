#!/usr/bin/env python3
"""Prove and select a real local stem provider without touching Robi.

The configured EarCrate environment may intentionally be lean while a separate
machine Python owns torch, Demucs, and CUDA. This repair discovers those local
interpreters, binds one by SHA-256, proves a real four-stem GPU separation plus a
cache hit on one current approved estate source, then atomically selects the
``demucs_process`` provider. It never installs packages, starts ACE-Step, renders
a commission, changes atom status, or edits human judgments.

Dry-run is the default. ``--apply`` is required to separate audio and change the
private workspace provider configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.app import EarcrateCore  # noqa: E402
from earcrate.providers import get  # noqa: E402
from earcrate.providers.artifacts import ArtifactStore  # noqa: E402
from earcrate.providers.stems import (  # noqa: E402
    DemucsProcessStemProvider,
    probe_demucs_process_python,
)

PROFILE = "girl_talk_v1"
ROLES = ("drums", "bass", "other")
PROVIDER_ID = "demucs_process"
SETTINGS_SCHEMA = 1


class RepairError(RuntimeError):
    pass


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write_json(path: Path, value: Any) -> str:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json_bytes(value)
    tmp = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    try:
        with tmp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return hashlib.sha256(payload).hexdigest()


def write_receipt(run_dir: Path, name: str, value: Any) -> Path:
    path = run_dir / name
    atomic_write_json(path, value)
    return path


def _dedupe_paths(values: Iterable[Any]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        try:
            path = Path(str(value).strip().strip('"')).expanduser().resolve()
        except Exception:
            continue
        key = os.path.normcase(str(path))
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        out.append(path)
    return out


def discover_python_candidates() -> list[Path]:
    values: list[Any] = []
    values.append(os.environ.get("EARCRATE_DEMUCS_PYTHON"))
    values.append(getattr(sys, "_base_executable", None))
    values.append(Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python"))
    values.append(sys.executable)

    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["py", "-0p"], check=False, capture_output=True, text=True, timeout=30
            )
            for line in completed.stdout.splitlines():
                match = re.search(r"([A-Za-z]:\\.*?python(?:w)?\.exe)\s*$", line.strip(), re.I)
                if match:
                    values.append(match.group(1))
        except Exception:
            pass
        try:
            completed = subprocess.run(
                ["where", "python"], check=False, capture_output=True, text=True, timeout=30
            )
            values.extend(line.strip() for line in completed.stdout.splitlines())
        except Exception:
            pass
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        values.extend(sorted((local / "Programs/Python").glob("Python*/python.exe"), reverse=True))
        for root in (Path("C:/"), Path("D:/")):
            if root.exists():
                values.extend(sorted(root.glob("Python*/python.exe"), reverse=True))
    else:
        for command in (("which", "-a", "python3"), ("which", "-a", "python")):
            try:
                completed = subprocess.run(
                    list(command), check=False, capture_output=True, text=True, timeout=30
                )
                values.extend(line.strip() for line in completed.stdout.splitlines())
            except Exception:
                pass
    return _dedupe_paths(values)


def exact_config_path(core: EarcrateCore) -> Path:
    pointer = getattr(core, "pointer_resolved_from", None)
    if pointer:
        pointer_path = Path(pointer).expanduser().resolve()
        try:
            body = json.loads(pointer_path.read_text(encoding="utf-8"))
            raw = Path(str(body["config_json"])).expanduser()
            path = raw if raw.is_absolute() else pointer_path.parent / raw
            if path.is_file():
                return path.resolve()
        except Exception:
            pass
    candidate = core.ensure_config().agent_root / "config.json"
    if not candidate.is_file():
        raise RepairError("could not resolve the configured workspace JSON")
    return candidate.resolve()


def repository_context() -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(ROOT), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return completed.stdout.strip() if completed.returncode == 0 else ""
        except Exception:
            return ""

    import earcrate.app as app_module

    return {
        "root": str(ROOT),
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "dirty_porcelain": git("status", "--porcelain"),
        "python": sys.executable,
        "python_sha256": sha256_file(Path(sys.executable).resolve()),
        "earcrate_import_path": str(Path(app_module.__file__).resolve()),
    }


def select_proof_source(core: EarcrateCore, profile: str) -> dict[str, Any]:
    db = core.conn()
    row = db.execute(
        """SELECT f.id AS file_id, f.path, f.sha256 AS file_sha256,
                  f.audio_sha256 AS pcm_sha256, f.duration_s,
                  MAX(a.score) AS best_atom_score, COUNT(*) AS approved_atoms
             FROM ear_atoms a
             JOIN loops l ON l.id=a.loop_id
             JOIN files f ON f.id=a.file_id
            WHERE a.taste_profile=? AND a.status='approved'
              AND COALESCE(f.present,1)=1
              AND f.audio_sha256_scope='full' AND f.audio_sha256 IS NOT NULL
              AND f.sha256 IS NOT NULL
              AND COALESCE(l.source_audio_generation,0)=COALESCE(f.audio_generation,0)
              AND (l.source_audio_sha256=f.audio_sha256
                   OR (l.source_audio_sha256 IS NULL
                       AND COALESCE(f.audio_generation,0)=0))
              AND COALESCE(f.duration_s,0) BETWEEN 8.0 AND 90.0
            GROUP BY f.id
            ORDER BY ABS(COALESCE(f.duration_s,20.0)-20.0) ASC,
                     best_atom_score DESC, f.id ASC
            LIMIT 1""",
        (profile,),
    ).fetchone()
    if row is None:
        raise RepairError("no short current approved estate source is available for a provider proof")
    result = dict(row)
    source = Path(str(result["path"])).expanduser().resolve()
    if not source.is_file():
        raise RepairError("selected provider-proof source is missing")
    actual = sha256_file(source)
    if actual != str(result["file_sha256"]):
        raise RepairError("selected provider-proof source changed after estate analysis")
    result["path"] = str(source)
    result["actual_file_sha256"] = actual
    return result


def validate_artifacts(store: ArtifactStore, result: dict[str, Any],
                       pcm_sha: str) -> dict[str, Any]:
    import io
    import soundfile as sf

    if not result.get("available") or result.get("provider") != PROVIDER_ID:
        raise RepairError("provider did not return an available demucs_process result")
    refs = dict(result.get("stems") or {})
    if set(refs) != set(ROLES):
        raise RepairError("provider proof did not return the exact instrumental role set")
    measured: dict[str, Any] = {}
    for role in ROLES:
        held = store.get(str(refs[role]))
        if not held or not held.get("data") or not held.get("meta"):
            raise RepairError("provider proof artifact is absent for %s" % role)
        data = bytes(held["data"])
        meta = dict(held["meta"])
        info = sf.info(io.BytesIO(data))
        if info.frames <= 0 or info.samplerate <= 0:
            raise RepairError("provider proof artifact has no audio for %s" % role)
        if str(meta.get("source_identity") or "") != str(pcm_sha):
            raise RepairError("provider proof artifact has the wrong source identity")
        if str(meta.get("provider") or "") != PROVIDER_ID:
            raise RepairError("provider proof artifact has the wrong provider provenance")
        measured[role] = {
            "artifact_key": str(refs[role]),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "frames": int(info.frames),
            "sample_rate": int(info.samplerate),
            "channels": int(info.channels),
            "provider": meta.get("provider"),
            "version": meta.get("version"),
        }
    return measured


def build_settings(probe: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "schema_version": SETTINGS_SCHEMA,
        "provider_id": PROVIDER_ID,
        "python_path": str(Path(probe["python_path"]).resolve()),
        "python_sha256": str(probe["python_sha256"]),
        "python_version": probe.get("python_version"),
        "torch_version": probe.get("torch_version"),
        "demucs_version": probe.get("demucs_version"),
        "gpu_name": probe.get("gpu_name"),
        "model": model,
        "shifts": 0,
        "overlap": 0.10,
        "segment_seconds": 6.0,
        "probe_timeout_seconds": 120.0,
        "separation_timeout_seconds": 7200.0,
        "created_at": now_utc(),
        "authority": "local heavy-runtime binding proven by real separation and cache hit",
    }


def restore_file(target: Path, backup: Path | None, existed: bool) -> None:
    if existed:
        if backup is None or not backup.is_file():
            raise RepairError("rollback backup is missing for %s" % target)
        shutil.copy2(backup, target)
    else:
        target.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    downloads = Path(args.receipt_root or (Path.home() / "Downloads")).expanduser().resolve()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = downloads / ("EarCrate-stem-provider-repair-" + stamp)
    run_dir.mkdir(parents=True, exist_ok=False)

    outcome: dict[str, Any] = {
        "kind": "earcrate_stem_provider_repair",
        "schema_version": 1,
        "started_utc": now_utc(),
        "mode": "apply" if args.apply else "plan",
        "profile": args.profile,
        "status": "running",
    }
    try:
        core = EarcrateCore()
        config = core.ensure_config()
        config_path = exact_config_path(core)
        context = {
            "repository": repository_context(),
            "config_path": str(config_path),
            "agent_root": str(config.agent_root),
            "working_root": str(config.working_root),
            "master_root": str(config.master_root),
            "current_stem_provider": str(config.stem_provider),
            "l3_root": os.environ.get("EARCRATE_L3_ROOT"),
        }
        write_receipt(run_dir, "CONTEXT.json", context)
        imported = Path(context["repository"]["earcrate_import_path"])
        try:
            imported.relative_to(ROOT)
        except ValueError:
            raise RepairError("Python imported EarCrate outside the selected repository")

        candidates = discover_python_candidates()
        probes: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        for candidate in candidates:
            probe = probe_demucs_process_python(candidate)
            probe["is_earcrate_python"] = os.path.normcase(str(candidate)) == os.path.normcase(str(Path(sys.executable).resolve()))
            probes.append(probe)
            if selected is None and probe.get("ready"):
                selected = probe
        write_receipt(run_dir, "INTERPRETER_PROBES.json", {
            "candidates": probes,
            "selected_python_sha256": selected.get("python_sha256") if selected else None,
        })
        if selected is None:
            raise RepairError("no installed local Python exposes torch+demucs on CUDA")

        proof_source = select_proof_source(core, args.profile)
        write_receipt(run_dir, "PROOF_SOURCE.private.json", proof_source)
        settings = build_settings(selected, args.model)
        settings_candidate = run_dir / "stem_provider.candidate.private.json"
        atomic_write_json(settings_candidate, settings)

        plan = {
            "selected_interpreter": {
                key: selected.get(key) for key in (
                    "python_path", "python_sha256", "python_version", "torch_version",
                    "demucs_version", "gpu_name", "ready"
                )
            },
            "proof_source": {
                "file_id": proof_source["file_id"],
                "duration_s": proof_source["duration_s"],
                "pcm_sha256": proof_source["pcm_sha256"],
                "container_sha256": proof_source["actual_file_sha256"],
                "approved_atoms": proof_source["approved_atoms"],
            },
            "provider": PROVIDER_ID,
            "roles": list(ROLES),
            "model": args.model,
            "would_write": [
                str(config.agent_root / "stem_provider.json"),
                str(config_path),
            ],
            "would_not": [
                "install packages", "start ACE-Step", "render Robi", "change atom status",
                "change human judgments", "return the source mix as a stem",
            ],
        }
        write_receipt(run_dir, "PLAN.json", plan)
        if not args.apply:
            outcome.update({
                "status": "planned",
                "provider_ready": False,
                "robi_rerun_authorized": False,
                "selected_interpreter_ready": True,
                "next": "rerun with --apply to require a real separation and cache hit",
            })
            outcome["finished_utc"] = now_utc()
            write_receipt(run_dir, "RESULT.json", outcome)
            print(json.dumps({"ok": True, "planned": True, "receipt_dir": str(run_dir)}, indent=2))
            return 0

        lock_path = (config.agent_root / "stem_provider_repair.lock").resolve()
        lock_handle = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = lock_path.open("x", encoding="utf-8")
            lock_handle.write(json.dumps({"pid": os.getpid(), "started": now_utc()}))
            lock_handle.flush()
            os.fsync(lock_handle.fileno())
        except FileExistsError:
            raise RepairError("another stem-provider repair lock already exists")

        backup_dir = config.agent_root / "archive" / "stem_provider_repair" / stamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        config_backup = backup_dir / "config.json"
        shutil.copy2(config_path, config_backup)
        final_settings_path = (config.agent_root / "stem_provider.json").resolve()
        settings_existed = final_settings_path.exists()
        settings_backup: Path | None = None
        if settings_existed:
            settings_backup = backup_dir / "stem_provider.json"
            shutil.copy2(final_settings_path, settings_backup)
        backup_receipt = {
            "config": {
                "path": str(config_backup),
                "sha256": sha256_file(config_backup),
            },
            "settings": None if settings_backup is None else {
                "path": str(settings_backup),
                "sha256": sha256_file(settings_backup),
            },
        }
        write_receipt(run_dir, "BACKUP.json", backup_receipt)

        store = ArtifactStore()
        provider = DemucsProcessStemProvider(
            store=store,
            settings_path=settings_candidate,
        )
        first = provider.separate(
            str(proof_source["pcm_sha256"]),
            str(proof_source["path"]),
            list(ROLES),
        )
        artifacts = validate_artifacts(store, first, str(proof_source["pcm_sha256"]))
        second = provider.separate(
            str(proof_source["pcm_sha256"]),
            str(proof_source["path"]),
            list(ROLES),
        )
        if not second.get("available") or not second.get("cached"):
            raise RepairError("the second provider call was not a verified cache hit")
        if dict(second.get("stems") or {}) != dict(first.get("stems") or {}):
            raise RepairError("cache-hit artifact identities changed")
        proof = {
            "provider": PROVIDER_ID,
            "first_call_cached": bool(first.get("cached")),
            "second_call_cached": bool(second.get("cached")),
            "settings_identity": first.get("settings_identity"),
            "python_sha256": first.get("python_sha256"),
            "capability": first.get("capability"),
            "artifacts": artifacts,
            "source_container_reverified": sha256_file(Path(proof_source["path"])) == proof_source["actual_file_sha256"],
        }
        write_receipt(run_dir, "PROVIDER_PROOF.private.json", proof)
        if not proof["source_container_reverified"]:
            raise RepairError("provider-proof source changed before configuration commit")

        mutation_started = False
        try:
            mutation_started = True
            atomic_write_json(final_settings_path, settings)
            current_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(current_config, dict):
                raise RepairError("workspace config is not a JSON object")
            current_config["stem_provider"] = PROVIDER_ID
            atomic_write_json(config_path, current_config)

            core2 = EarcrateCore()
            selected_config = core2.ensure_config()
            if str(selected_config.stem_provider) != PROVIDER_ID:
                raise RepairError("EarCrate did not reload demucs_process from the committed config")
            resolved = get("stems", PROVIDER_ID)
            if getattr(resolved, "name", None) != PROVIDER_ID:
                raise RepairError("provider registry did not resolve demucs_process")
            final = resolved.separate(
                str(proof_source["pcm_sha256"]),
                str(proof_source["path"]),
                list(ROLES),
            )
            final_artifacts = validate_artifacts(
                getattr(resolved, "store"), final, str(proof_source["pcm_sha256"])
            )
            if not final.get("cached"):
                raise RepairError("committed provider did not resolve the proven cache")
            if {role: item["sha256"] for role, item in final_artifacts.items()} != {
                role: item["sha256"] for role, item in artifacts.items()
            }:
                raise RepairError("committed provider resolved different stem bytes")
        except Exception:
            if mutation_started:
                restore_file(config_path, config_backup, True)
                restore_file(final_settings_path, settings_backup, settings_existed)
            raise

        outcome.update({
            "status": "stem_provider_recovered",
            "provider_ready": True,
            "robi_rerun_authorized": True,
            "selected_provider": PROVIDER_ID,
            "selected_python_sha256": selected["python_sha256"],
            "gpu_name": selected.get("gpu_name"),
            "model": args.model,
            "proof_artifacts": artifacts,
            "config_before_sha256": sha256_file(config_backup),
            "config_after_sha256": sha256_file(config_path),
            "settings_sha256": sha256_file(final_settings_path),
            "backup": backup_receipt,
            "next": "run the unchanged Robi V3.1 campaign exactly once from a fresh extraction",
        })
        outcome["finished_utc"] = now_utc()
        write_receipt(run_dir, "RESULT.json", outcome)
        print(json.dumps({
            "ok": True,
            "provider_ready": True,
            "robi_rerun_authorized": True,
            "receipt_dir": str(run_dir),
        }, indent=2))
        return 0
    except Exception as exc:
        outcome.update({
            "status": "refused",
            "provider_ready": False,
            "robi_rerun_authorized": False,
            "failure": "%s: %s" % (type(exc).__name__, exc),
            "finished_utc": now_utc(),
        })
        write_receipt(run_dir, "REFUSAL.json", outcome)
        print(json.dumps({
            "ok": False,
            "error": outcome["failure"],
            "receipt_dir": str(run_dir),
        }, indent=2), file=sys.stderr)
        return 1
    finally:
        try:
            if "lock_handle" in locals() and lock_handle is not None:
                lock_handle.close()
            if "lock_path" in locals():
                Path(lock_path).unlink(missing_ok=True)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="prove a real separation and atomically select the provider")
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--receipt-root", default="")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(args.model)):
        parser.error("--model contains unsafe characters")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
