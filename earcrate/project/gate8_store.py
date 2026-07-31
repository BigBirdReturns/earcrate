from __future__ import annotations

"""ProjectStore extension that preserves the legacy audio project path and adds
explicit causal-score validation for Gate 8 custody and continuation.
"""

from copy import deepcopy
from typing import Any, Mapping

from .causal_revision import (
    causal_compute_revision_sha,
    causal_seal_revision,
    causal_validate_revision,
    is_causal_revision,
)
from .model import new_project_index
from .store import ProjectStore
from .util import (
    ConcurrencyError, ProjectError, ValidationError, atomic_write_json, now_utc,
    random_id, read_json,
)


class Gate8ProjectStore(ProjectStore):
    """ProjectStore with first-class causal ProjectRevision support."""

    @staticmethod
    def _seal(revision: Mapping[str, Any]) -> dict[str, Any]:
        if is_causal_revision(revision):
            return causal_seal_revision(revision)
        from .model import seal_revision
        return seal_revision(revision)

    @staticmethod
    def _validate(revision: Mapping[str, Any], *, require_sealed: bool = True) -> None:
        if is_causal_revision(revision):
            causal_validate_revision(revision, require_sealed=require_sealed)
        else:
            from .model import validate_revision
            validate_revision(revision, require_sealed=require_sealed)

    @staticmethod
    def _compute(revision: Mapping[str, Any]) -> str:
        if is_causal_revision(revision):
            return causal_compute_revision_sha(revision)
        from .model import compute_revision_sha
        return compute_revision_sha(revision)

    def create_project(self, name: str, revision: Mapping[str, Any], project_id: str | None = None) -> dict[str, Any]:
        pid = str(project_id or revision.get("project_id") or random_id("project"))
        pdir = self.project_dir(pid)
        if pdir.exists():
            unexpected = [child for child in pdir.iterdir() if child.name != "artifacts"]
            if unexpected:
                raise ProjectError(f"project already exists: {pid}")
        pdir.mkdir(parents=True, exist_ok=True)
        for name_part in ("revisions", "exports", "runs"):
            (pdir / name_part).mkdir(parents=True, exist_ok=True)
        rev = dict(revision)
        rev["project_id"] = pid
        sealed = self._seal(rev)
        self.write_revision(pid, sealed)
        profile_id = str((((sealed.get("intent") or {}).get("taste_profile") or {}).get("id") or ""))
        index = new_project_index(pid, name, sealed["revision_sha"], profile_id)
        atomic_write_json(self.project_path(pid), index)
        self.append_event(pid, {
            "event_id": random_id("event"), "event": "project_created", "at": now_utc(),
            "project_id": pid, "revision_sha": sealed["revision_sha"], "created_by": sealed.get("created_by"),
        })
        return {"project": index, "revision": sealed, "path": str(pdir)}

    def load_revision(self, project_id: str, revision_sha: str | None = None) -> dict[str, Any]:
        project = self.load_project(project_id)
        sha = str(revision_sha or project.get("active_revision_sha") or "")
        path = self.revision_path(project_id, sha)
        if not path.exists():
            raise ProjectError(f"revision not found: {sha}")
        revision = read_json(path)
        self._validate(revision, require_sealed=True)
        if str(revision.get("project_id") or "") != project_id:
            raise ValidationError("revision belongs to another project")
        return revision

    def write_revision(self, project_id: str, revision: Mapping[str, Any]):
        sealed = self._seal(revision)
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
        self, project_id: str, revision: Mapping[str, Any], *, expected_head: str | None,
        event: str, event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.load_project(project_id)
        current = str(project.get("active_revision_sha") or "")
        if expected_head is not None and current != expected_head:
            raise ConcurrencyError(f"project head moved: expected {expected_head}, found {current}")
        rev = deepcopy(dict(revision))
        rev["project_id"] = project_id
        if rev.get("parent_revision_sha") is None:
            rev["parent_revision_sha"] = current
        if str(rev.get("parent_revision_sha") or "") != current:
            raise ValidationError("a new revision must name the current head as parent")
        sealed = self._seal(rev)
        self.write_revision(project_id, sealed)
        lineage = list(project.get("lineage") or [])
        cursor = int(project.get("cursor") or 0)
        if cursor < len(lineage) - 1:
            project.setdefault("branches", []).append({
                "from_revision_sha": current, "abandoned_lineage": lineage[cursor + 1:],
                "at": now_utc(), "reason": "new revision created after undo",
            })
            lineage = lineage[:cursor + 1]
        lineage.append(sealed["revision_sha"])
        project.update({
            "lineage": lineage, "cursor": len(lineage) - 1,
            "active_revision_sha": sealed["revision_sha"], "updated_at": now_utc(),
        })
        atomic_write_json(self.project_path(project_id), project)
        self.append_event(project_id, {
            "event_id": random_id("event"), "event": event, "at": now_utc(),
            "project_id": project_id, "base_revision_sha": current,
            "result_revision_sha": sealed["revision_sha"], "payload": dict(event_payload or {}),
            "created_by": sealed.get("created_by"),
        })
        return {"project": project, "revision": sealed}

    def validate_store(self, project_id: str) -> dict[str, Any]:
        project = self.load_project(project_id)
        missing: list[str] = []
        invalid: list[str] = []
        for sha in project.get("lineage") or []:
            path = self.revision_path(project_id, str(sha))
            if not path.exists():
                missing.append(str(sha)); continue
            try:
                revision = read_json(path)
                self._validate(revision, require_sealed=True)
                if self._compute(revision) != sha:
                    invalid.append(str(sha))
            except Exception as exc:
                invalid.append(f"{sha}: {exc}")
        return {
            "ok": not missing and not invalid, "project_id": project_id,
            "active_revision_sha": project["active_revision_sha"],
            "revision_count": len(project.get("lineage") or []),
            "missing_revisions": missing, "invalid_revisions": invalid,
            "command_events": len(self.read_events(project_id)),
        }


__all__ = ["Gate8ProjectStore"]
