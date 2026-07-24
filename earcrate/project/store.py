from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from .model import compute_revision_sha, new_project_index, seal_revision, validate_revision
from .util import (
    ConcurrencyError,
    ProjectError,
    ValidationError,
    append_jsonl_fsync,
    atomic_write_json,
    sha256_file,
    ensure_within,
    now_utc,
    random_id,
    read_json,
)


class ProjectStore:
    """Visible, file-backed L4 creative record.

    Revisions are immutable content-addressed JSON. ``project.json`` contains only the
    active-head/navigation pointers. ``commands.jsonl`` is append-only and records every
    compile, edit, undo, redo, render-finalization, and export action.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.projects_root = self.root / "projects"
        self.runs_root = self.root / "runs"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def artifacts_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_path(self, project_id: str, relative_path: str) -> Path:
        raw = str(relative_path or "")
        if not raw or Path(raw).is_absolute():
            raise ValidationError("artifact path must be project-relative")
        return ensure_within(self.project_dir(project_id) / raw, self.project_dir(project_id))

    def import_artifact(self, project_id: str, source: str | Path, *, label: str = "artifact") -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ProjectError(f"artifact not found: {source_path}")
        digest = sha256_file(source_path)
        suffix = "".join(source_path.suffixes)[-24:] or ".bin"
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label)).strip("_") or "artifact"
        name = f"{safe_label}-{digest[:16]}{suffix}"
        destination = ensure_within(self.artifacts_dir(project_id) / name, self.artifacts_dir(project_id))
        if destination.exists():
            if sha256_file(destination) != digest:
                raise ValidationError(f"artifact collision at {destination}")
        else:
            temporary = destination.with_name(f".{destination.name}.{random_id('tmp')}")
            try:
                with source_path.open("rb") as source_handle, temporary.open("xb") as handle:
                    shutil.copyfileobj(source_handle, handle, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                if sha256_file(temporary) != digest:
                    raise ValidationError("artifact changed while being imported")
                os.replace(temporary, destination)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()
        relative = destination.relative_to(self.project_dir(project_id)).as_posix()
        return {
            "relative_path": relative,
            "raw_sha256": digest,
            "bytes": int(destination.stat().st_size),
            "original_name": source_path.name,
            "original_path": str(source_path),
        }

    def resolve_artifact(self, project_id: str, artifact: Mapping[str, Any]) -> Path:
        path = self.artifact_path(project_id, str(artifact.get("relative_path") or ""))
        if not path.is_file():
            raise ProjectError(f"project artifact is missing: {path}")
        expected = str(artifact.get("raw_sha256") or "")
        actual = sha256_file(path)
        if expected and expected != actual:
            raise ValidationError(f"project artifact hash mismatch: {path}")
        return path

    def project_dir(self, project_id: str) -> Path:
        if not project_id or any(ch in project_id for ch in "/\\:"):
            raise ValidationError(f"invalid project id: {project_id!r}")
        return ensure_within(self.projects_root / project_id, self.projects_root)

    def project_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def revisions_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "revisions"

    def revision_path(self, project_id: str, revision_sha: str) -> Path:
        if not revision_sha or len(revision_sha) != 64 or any(c not in "0123456789abcdef" for c in revision_sha.lower()):
            raise ValidationError(f"invalid revision sha: {revision_sha!r}")
        return ensure_within(self.revisions_dir(project_id) / f"{revision_sha}.json", self.revisions_dir(project_id))

    def commands_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "commands.jsonl"

    def exports_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_runs_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_project(self, name: str, revision: Mapping[str, Any], project_id: str | None = None) -> dict[str, Any]:
        pid = str(project_id or revision.get("project_id") or random_id("project"))
        pdir = self.project_dir(pid)
        if pdir.exists():
            unexpected = [child for child in pdir.iterdir() if child.name != "artifacts"]
            if unexpected:
                raise ProjectError(f"project already exists: {pid}")
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "revisions").mkdir(parents=True, exist_ok=True)
        (pdir / "exports").mkdir(parents=True, exist_ok=True)
        (pdir / "runs").mkdir(parents=True, exist_ok=True)
        rev = dict(revision)
        rev["project_id"] = pid
        sealed = seal_revision(rev)
        self.write_revision(pid, sealed)
        profile_id = str((((sealed.get("intent") or {}).get("taste_profile") or {}).get("id") or ""))
        index = new_project_index(pid, name, sealed["revision_sha"], profile_id)
        atomic_write_json(self.project_path(pid), index)
        self.append_event(pid, {
            "event_id": random_id("event"),
            "event": "project_created",
            "at": now_utc(),
            "project_id": pid,
            "revision_sha": sealed["revision_sha"],
            "created_by": sealed.get("created_by"),
        })
        return {"project": index, "revision": sealed, "path": str(pdir)}

    def list_projects(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.projects_root.glob("*/project.json")):
            with contextlib.suppress(Exception):
                data = read_json(path)
                data["path"] = str(path.parent)
                items.append(data)
        items.sort(key=lambda item: (str(item.get("updated_at") or ""), str(item.get("project_id") or "")), reverse=True)
        return items

    def load_project(self, project_id: str) -> dict[str, Any]:
        path = self.project_path(project_id)
        if not path.exists():
            raise ProjectError(f"project not found: {project_id}")
        data = read_json(path)
        if str(data.get("project_id") or "") != project_id:
            raise ValidationError(f"project index id mismatch at {path}")
        lineage = list(data.get("lineage") or [])
        cursor = int(data.get("cursor") or 0)
        if not lineage or cursor < 0 or cursor >= len(lineage):
            raise ValidationError(f"project {project_id} has invalid lineage/cursor")
        if data.get("active_revision_sha") != lineage[cursor]:
            raise ValidationError(f"project {project_id} active revision does not match cursor")
        return data

    def load_revision(self, project_id: str, revision_sha: str | None = None) -> dict[str, Any]:
        project = self.load_project(project_id)
        sha = str(revision_sha or project.get("active_revision_sha") or "")
        path = self.revision_path(project_id, sha)
        if not path.exists():
            raise ProjectError(f"revision not found: {sha}")
        revision = read_json(path)
        validate_revision(revision, require_sealed=True)
        if str(revision.get("project_id") or "") != project_id:
            raise ValidationError("revision belongs to another project")
        return revision

    def write_revision(self, project_id: str, revision: Mapping[str, Any]) -> Path:
        sealed = seal_revision(revision)
        if sealed.get("project_id") != project_id:
            raise ValidationError("cannot write revision under a different project")
        path = self.revision_path(project_id, sealed["revision_sha"])
        if path.exists():
            existing = read_json(path)
            if existing != sealed:
                raise ValidationError(f"immutable revision collision at {path}")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write_json(path, sealed)

    def commit_revision(
        self,
        project_id: str,
        revision: Mapping[str, Any],
        *,
        expected_head: str | None,
        event: str,
        event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.load_project(project_id)
        current = str(project.get("active_revision_sha") or "")
        if expected_head is not None and current != expected_head:
            raise ConcurrencyError(f"project head moved: expected {expected_head}, found {current}")
        rev = dict(revision)
        rev["project_id"] = project_id
        if rev.get("parent_revision_sha") is None:
            rev["parent_revision_sha"] = current
        if str(rev.get("parent_revision_sha") or "") != current:
            raise ValidationError("a new revision must name the current head as parent")
        sealed = seal_revision(rev)
        self.write_revision(project_id, sealed)

        lineage = list(project.get("lineage") or [])
        cursor = int(project.get("cursor") or 0)
        if cursor < len(lineage) - 1:
            abandoned = lineage[cursor + 1 :]
            project.setdefault("branches", []).append({
                "from_revision_sha": current,
                "abandoned_lineage": abandoned,
                "at": now_utc(),
                "reason": "new revision created after undo",
            })
            lineage = lineage[: cursor + 1]
        lineage.append(sealed["revision_sha"])
        project["lineage"] = lineage
        project["cursor"] = len(lineage) - 1
        project["active_revision_sha"] = sealed["revision_sha"]
        project["updated_at"] = now_utc()
        atomic_write_json(self.project_path(project_id), project)
        self.append_event(project_id, {
            "event_id": random_id("event"),
            "event": event,
            "at": now_utc(),
            "project_id": project_id,
            "base_revision_sha": current,
            "result_revision_sha": sealed["revision_sha"],
            "payload": dict(event_payload or {}),
            "created_by": sealed.get("created_by"),
        })
        return {"project": project, "revision": sealed}

    def undo(self, project_id: str, expected_head: str | None = None) -> dict[str, Any]:
        project = self.load_project(project_id)
        current = str(project["active_revision_sha"])
        if expected_head is not None and expected_head != current:
            raise ConcurrencyError(f"project head moved: expected {expected_head}, found {current}")
        cursor = int(project["cursor"])
        if cursor <= 0:
            raise ProjectError("nothing to undo")
        project["cursor"] = cursor - 1
        project["active_revision_sha"] = project["lineage"][cursor - 1]
        project["updated_at"] = now_utc()
        atomic_write_json(self.project_path(project_id), project)
        self.append_event(project_id, {
            "event_id": random_id("event"),
            "event": "undo",
            "at": now_utc(),
            "from_revision_sha": current,
            "to_revision_sha": project["active_revision_sha"],
        })
        return {"project": project, "revision": self.load_revision(project_id)}

    def redo(self, project_id: str, expected_head: str | None = None) -> dict[str, Any]:
        project = self.load_project(project_id)
        current = str(project["active_revision_sha"])
        if expected_head is not None and expected_head != current:
            raise ConcurrencyError(f"project head moved: expected {expected_head}, found {current}")
        cursor = int(project["cursor"])
        if cursor >= len(project["lineage"]) - 1:
            raise ProjectError("nothing to redo")
        project["cursor"] = cursor + 1
        project["active_revision_sha"] = project["lineage"][cursor + 1]
        project["updated_at"] = now_utc()
        atomic_write_json(self.project_path(project_id), project)
        self.append_event(project_id, {
            "event_id": random_id("event"),
            "event": "redo",
            "at": now_utc(),
            "from_revision_sha": current,
            "to_revision_sha": project["active_revision_sha"],
        })
        return {"project": project, "revision": self.load_revision(project_id)}

    def append_event(self, project_id: str, event: Mapping[str, Any]) -> Path:
        return append_jsonl_fsync(self.commands_path(project_id), dict(event))

    def read_events(self, project_id: str) -> list[dict[str, Any]]:
        path = self.commands_path(project_id)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception as exc:
                raise ValidationError(f"invalid command log line {number}: {exc}") from exc
        return out

    def new_run(self, project_id: str, revision_sha: str, kind: str) -> dict[str, Any]:
        run_id = random_id("run")
        global_dir = ensure_within(self.runs_root / run_id, self.runs_root)
        global_dir.mkdir(parents=True, exist_ok=False)
        project_dir = self.project_runs_dir(project_id) / run_id
        project_dir.mkdir(parents=True, exist_ok=False)
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "kind": str(kind),
            "project_id": project_id,
            "revision_sha": revision_sha,
            "state": "running",
            "started_at": now_utc(),
            "global_path": str(global_dir),
            "project_path": str(project_dir),
        }
        atomic_write_json(global_dir / "status.json", receipt)
        atomic_write_json(project_dir / "status.json", receipt)
        self.append_event(project_id, {"event_id": random_id("event"), "event": "run_started", **receipt})
        return receipt

    def write_run_artifact(self, run: Mapping[str, Any], name: str, value: Any) -> list[str]:
        if not name or "/" in name or "\\" in name:
            raise ValidationError(f"invalid run artifact name: {name}")
        paths: list[str] = []
        for key in ("global_path", "project_path"):
            base = Path(str(run[key])).resolve()
            path = ensure_within(base / name, base)
            if isinstance(value, (dict, list)):
                atomic_write_json(path, value)
            elif isinstance(value, bytes):
                from .util import atomic_write_bytes
                atomic_write_bytes(path, value)
            else:
                from .util import atomic_write_text
                atomic_write_text(path, str(value))
            paths.append(str(path))
        return paths

    def finish_run(self, run: Mapping[str, Any], ok: bool, outcome: Mapping[str, Any]) -> dict[str, Any]:
        status = dict(run)
        status.update({
            "state": "succeeded" if ok else "failed",
            "ok": bool(ok),
            "finished_at": now_utc(),
            "outcome": dict(outcome),
        })
        for key in ("global_path", "project_path"):
            base = Path(str(run[key])).resolve()
            atomic_write_json(base / "status.json", status)
            atomic_write_json(base / "report.json", status)
        self.append_event(str(run["project_id"]), {
            "event_id": random_id("event"),
            "event": "run_finished",
            "at": now_utc(),
            "run_id": run["run_id"],
            "revision_sha": run["revision_sha"],
            "ok": bool(ok),
            "outcome": dict(outcome),
        })
        return status

    def record_last_render(self, project_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        project = self.load_project(project_id)
        project["last_render"] = dict(receipt)
        project["updated_at"] = now_utc()
        atomic_write_json(self.project_path(project_id), project)
        return project

    def validate_store(self, project_id: str) -> dict[str, Any]:
        project = self.load_project(project_id)
        missing: list[str] = []
        invalid: list[str] = []
        for sha in project.get("lineage") or []:
            path = self.revision_path(project_id, str(sha))
            if not path.exists():
                missing.append(str(sha))
                continue
            try:
                revision = read_json(path)
                validate_revision(revision, require_sealed=True)
                if compute_revision_sha(revision) != sha:
                    invalid.append(str(sha))
            except Exception as exc:
                invalid.append(f"{sha}: {exc}")
        return {
            "ok": not missing and not invalid,
            "project_id": project_id,
            "active_revision_sha": project["active_revision_sha"],
            "revision_count": len(project.get("lineage") or []),
            "missing_revisions": missing,
            "invalid_revisions": invalid,
            "command_events": len(self.read_events(project_id)),
        }
