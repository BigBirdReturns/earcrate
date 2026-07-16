from earcrate.core.deps import *
import dataclasses
from earcrate.core.util import now_utc, ulidish, fsync_append_jsonl, json_dumps, sha256_file
from earcrate.project.model import ScoreRevision, ProjectRecord, ProjectConcurrencyError, ProjectNotFoundError, ProjectValidationError


class ProjectFileLock:
    """Visible O_EXCL lock with stale recovery for one project directory."""

    def __init__(self, path: Path, timeout_s: float = 10.0, stale_s: float = 180.0):
        self.path = Path(path)
        self.timeout_s = float(timeout_s)
        self.stale_s = float(stale_s)
        self.fd: Optional[int] = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(self.fd, json_dumps({"pid": os.getpid(), "created_at": now_utc()}).encode("utf-8"))
                os.fsync(self.fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise ProjectConcurrencyError(f"project is locked: {self.path.parent.name}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def _atomic_json(path: Path, payload: Dict[str, Any]) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{ulidish()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ProjectValidationError(f"invalid project JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectValidationError(f"project JSON at {path} is not an object")
    return value


class ProjectStore:
    """Portable L4 creative record rooted at ``working_root/projects``.

    Revision JSON is the source of truth. SQLite may index projects later, but a
    project remains recoverable by copying its visible directory alone.
    """

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def initialize(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def project_dir(self, project_id: str) -> Path:
        pid = str(project_id or "")
        if not pid or not re.fullmatch(r"[A-Za-z0-9_.:-]+", pid):
            raise ProjectValidationError(f"invalid project id: {pid!r}")
        return self.root / pid

    def project_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def revision_path(self, project_id: str, revision_sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", str(revision_sha or "")):
            raise ProjectValidationError("revision SHA must be a lowercase SHA-256")
        return self.project_dir(project_id) / "revisions" / f"{revision_sha}.json"

    def _lock(self, project_id: str) -> ProjectFileLock:
        return ProjectFileLock(self.project_dir(project_id) / ".project.lock")

    def create(self, name: str, revision: ScoreRevision, metadata: Optional[Dict[str, Any]] = None) -> ProjectRecord:
        revision.validate()
        self.initialize()
        directory = self.project_dir(revision.project_id)
        if directory.exists():
            raise ProjectConcurrencyError(f"project already exists: {revision.project_id}")
        directory.mkdir(parents=True, exist_ok=False)
        for child in ("revisions", "renders", "exports", "previews", "premasters"):
            (directory / child).mkdir(parents=True, exist_ok=True)
        _atomic_json(self.revision_path(revision.project_id, revision.revision_sha), revision.to_dict())
        stamp = now_utc()
        record = ProjectRecord(
            schema_version=1,
            project_id=revision.project_id,
            name=str(name or "EarCrate Project"),
            active_revision_sha=revision.revision_sha,
            revision_history=[revision.revision_sha],
            redo_stack=[],
            created_at=stamp,
            updated_at=stamp,
            metadata=dict(metadata or {}),
        )
        _atomic_json(self.project_path(record.project_id), record.to_dict())
        fsync_append_jsonl(directory / "commands.jsonl", {
            "schema_version": 1, "command_id": ulidish(), "created_at": stamp,
            "actor": "compiler", "kind": "create_project", "from_revision_sha": None,
            "to_revision_sha": revision.revision_sha, "payload": {"name": record.name},
        })
        self.checkpoint(record.project_id, "project_created", revision.revision_sha)
        return record

    def load_project(self, project_id: str) -> ProjectRecord:
        path = self.project_path(project_id)
        if not path.exists():
            raise ProjectNotFoundError(f"project not found: {project_id}")
        record = ProjectRecord.from_dict(_read_json(path))
        if record.schema_version != 1:
            raise ProjectValidationError(f"unsupported project schema {record.schema_version}")
        return record

    def load_revision(self, project_id: str, revision_sha: Optional[str] = None) -> ScoreRevision:
        record = self.load_project(project_id)
        sha = str(revision_sha or record.active_revision_sha)
        path = self.revision_path(project_id, sha)
        if not path.exists():
            raise ProjectNotFoundError(f"revision not found: {project_id}/{sha}")
        revision = ScoreRevision.from_dict(_read_json(path))
        if revision.project_id != project_id:
            raise ProjectValidationError("revision project id does not match its directory")
        return revision

    def save_revision(self, revision: ScoreRevision, expected_head: str,
                      command: Dict[str, Any], advance_head: bool = True) -> ProjectRecord:
        revision.validate()
        with self._lock(revision.project_id):
            record = self.load_project(revision.project_id)
            if record.active_revision_sha != str(expected_head):
                raise ProjectConcurrencyError(
                    f"stale project head: expected {expected_head}, current {record.active_revision_sha}"
                )
            if revision.parent_revision_sha != str(expected_head):
                raise ProjectValidationError("new revision parent must equal the current project head")
            path = self.revision_path(revision.project_id, revision.revision_sha)
            if path.exists():
                existing = ScoreRevision.from_dict(_read_json(path))
                if existing.to_dict() != revision.to_dict():
                    raise ProjectConcurrencyError("immutable revision hash collision")
            else:
                _atomic_json(path, revision.to_dict())
            if advance_head:
                record = dataclasses.replace(
                    record,
                    active_revision_sha=revision.revision_sha,
                    revision_history=list(record.revision_history) + [revision.revision_sha],
                    redo_stack=[],
                    updated_at=now_utc(),
                )
                _atomic_json(self.project_path(record.project_id), record.to_dict())
            fsync_append_jsonl(self.project_dir(record.project_id) / "commands.jsonl", {
                "schema_version": 1,
                "command_id": str(command.get("command_id") or ulidish()),
                "created_at": str(command.get("created_at") or now_utc()),
                "actor": str(command.get("actor") or "human"),
                "kind": str(command.get("kind") or "edit"),
                "from_revision_sha": expected_head,
                "to_revision_sha": revision.revision_sha,
                "payload": dict(command.get("payload") or command),
            })
            self.checkpoint(record.project_id, str(command.get("kind") or "revision"), revision.revision_sha)
            return record

    def undo(self, project_id: str) -> ProjectRecord:
        with self._lock(project_id):
            record = self.load_project(project_id)
            if len(record.revision_history) <= 1:
                raise ProjectValidationError("nothing to undo")
            current = record.revision_history[-1]
            history = record.revision_history[:-1]
            record = dataclasses.replace(
                record,
                active_revision_sha=history[-1],
                revision_history=history,
                redo_stack=list(record.redo_stack) + [current],
                updated_at=now_utc(),
            )
            _atomic_json(self.project_path(project_id), record.to_dict())
            fsync_append_jsonl(self.project_dir(project_id) / "commands.jsonl", {
                "schema_version": 1, "command_id": ulidish(), "created_at": now_utc(),
                "actor": "human", "kind": "undo", "from_revision_sha": current,
                "to_revision_sha": record.active_revision_sha, "payload": {},
            })
            self.checkpoint(project_id, "undo", record.active_revision_sha)
            return record

    def redo(self, project_id: str) -> ProjectRecord:
        with self._lock(project_id):
            record = self.load_project(project_id)
            if not record.redo_stack:
                raise ProjectValidationError("nothing to redo")
            target = record.redo_stack[-1]
            if not self.revision_path(project_id, target).exists():
                raise ProjectValidationError(f"redo revision is missing: {target}")
            previous = record.active_revision_sha
            record = dataclasses.replace(
                record,
                active_revision_sha=target,
                revision_history=list(record.revision_history) + [target],
                redo_stack=record.redo_stack[:-1],
                updated_at=now_utc(),
            )
            _atomic_json(self.project_path(project_id), record.to_dict())
            fsync_append_jsonl(self.project_dir(project_id) / "commands.jsonl", {
                "schema_version": 1, "command_id": ulidish(), "created_at": now_utc(),
                "actor": "human", "kind": "redo", "from_revision_sha": previous,
                "to_revision_sha": target, "payload": {},
            })
            self.checkpoint(project_id, "redo", target)
            return record

    def history(self, project_id: str) -> List[Dict[str, Any]]:
        path = self.project_dir(project_id) / "commands.jsonl"
        if not path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise ProjectValidationError(f"invalid command history at line {index}: {exc}") from exc
            out.append(row)
        return out

    def list_projects(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        out: List[Dict[str, Any]] = []
        for path in sorted(self.root.glob("*/project.json")):
            try:
                out.append(ProjectRecord.from_dict(_read_json(path)).to_dict())
            except Exception as exc:
                out.append({"path": str(path), "error": str(exc)})
        return out

    def checkpoint(self, project_id: str, reason: str, revision_sha: str) -> Path:
        revision_path = self.revision_path(project_id, revision_sha)
        if not revision_path.exists():
            raise ProjectNotFoundError(f"cannot checkpoint missing revision {revision_sha}")
        return _atomic_json(self.project_dir(project_id) / "checkpoint.json", {
            "schema_version": 1, "project_id": project_id, "reason": reason,
            "revision_sha": revision_sha, "created_at": now_utc(),
            "revision_path": str(revision_path),
            "revision_file_sha256": sha256_file(revision_path),
        })
