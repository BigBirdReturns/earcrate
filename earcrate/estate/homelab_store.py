from __future__ import annotations

"""Durable Homelab object store and recoverable campaign scheduler.

Sealed JSON remains evidence authority. SQLite is an index, dependency scheduler,
lease manager, and append-only event journal. A corrupt index never upgrades a
provider result: doctor fails closed and every JSON object remains independently
verifiable.
"""

from contextlib import contextmanager, suppress
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from typing import Any, Iterator, Mapping, Sequence

from earcrate.estate.homelab_common import HOMELAB_HASH_FIELDS, _is_sha256, _now_utc, _sha_json, homelab_seal, homelab_validate_seal

STORE_SCHEMA_VERSION = 1
_VISIBILITIES = {"public", "private", "sensitive"}
_TASK_STATES = {"blocked", "queued", "leased", "completed", "failed", "refused", "cancelled"}
_TERMINAL_TASK_STATES = {"completed", "failed", "refused", "cancelled"}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object_identity(value: Mapping[str, Any]) -> str:
    field = HOMELAB_HASH_FIELDS.get(str(value.get("kind") or ""))
    if not field:
        raise ValueError(f"unsupported Homelab object kind: {value.get('kind')!r}")
    digest = str(value.get(field) or "")
    if not _is_sha256(digest):
        raise ValueError(f"invalid or missing Homelab object identity field {field}")
    return digest


def _refuse_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked Homelab store path refused: {current}")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _resource_group(resource: str) -> str | None:
    value = str(resource).casefold()
    if "gpu" in value:
        return "gpu"
    if "audio-device" in value or "physical-audio" in value:
        return "audio-device"
    return None


class HomelabStore:
    """SQLite-backed index and scheduler for sealed Homelab JSON objects."""

    def __init__(self, root: str | Path):
        raw = Path(root).expanduser()
        _refuse_symlink_components(raw.absolute())
        self.root = raw.resolve()
        self.objects_root = self.root / "objects"
        self.database_dir = self.root / "db"
        self.database_path = self.database_dir / "homelab.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.database_dir.mkdir(parents=True, exist_ok=True)
        _refuse_symlink_components(self.root)
        self._write_lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "HomelabStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                if self._connection.in_transaction:
                    self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    with suppress(Exception):
                        self._connection.execute("ROLLBACK")
                raise

    def _migrate(self) -> None:
        """Create schema outside the normal transaction wrapper.

        sqlite3.executescript() owns its transaction boundary, so wrapping it in
        BEGIN/COMMIT produces a false ``no transaction is active`` failure.
        Metadata initialization is then performed in an ordinary explicit
        transaction.
        """
        with self._write_lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS objects (
                    identity TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    visibility TEXT NOT NULL CHECK (visibility IN ('public','private','sensitive')),
                    relative_path TEXT NOT NULL UNIQUE,
                    raw_sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL CHECK (bytes >= 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    previous_event_sha256 TEXT,
                    event_type TEXT NOT NULL,
                    object_sha256 TEXT,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_sha256 TEXT PRIMARY KEY REFERENCES objects(identity) ON DELETE RESTRICT,
                    catalog_sha256 TEXT NOT NULL,
                    audit_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active','completed','cancelled','superseded')),
                    created_at TEXT NOT NULL,
                    superseded_by TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    campaign_sha256 TEXT NOT NULL REFERENCES campaigns(campaign_sha256) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    stage TEXT,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('blocked','queued','leased','completed','failed','refused','cancelled')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    resource TEXT NOT NULL,
                    assigned_node_sha256 TEXT,
                    dependencies_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
                    available_at REAL NOT NULL DEFAULT 0,
                    lease_token_sha256 TEXT,
                    leased_by TEXT,
                    lease_expires_at REAL,
                    last_error TEXT,
                    evidence_sha256 TEXT REFERENCES objects(identity) ON DELETE RESTRICT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (campaign_sha256, task_id)
                );
                CREATE INDEX IF NOT EXISTS tasks_ready_idx
                    ON tasks(campaign_sha256, status, available_at, priority DESC, task_id);
                CREATE INDEX IF NOT EXISTS tasks_lease_idx
                    ON tasks(status, lease_expires_at, resource);
                """
            )
        with self._transaction() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                connection.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(STORE_SCHEMA_VERSION),))
                connection.execute("INSERT INTO meta(key,value) VALUES('event_chain_head','')")
            elif int(row["value"]) != STORE_SCHEMA_VERSION:
                raise ValueError(f"unsupported Homelab store schema {row['value']}; expected {STORE_SCHEMA_VERSION}")
            if connection.execute("SELECT 1 FROM meta WHERE key='event_chain_head'").fetchone() is None:
                connection.execute("INSERT INTO meta(key,value) VALUES('event_chain_head','')")

    def _append_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        *,
        object_sha256: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        head_row = connection.execute("SELECT value FROM meta WHERE key='event_chain_head'").fetchone()
        previous = str(head_row["value"] or "") if head_row else ""
        occurred_at = _now_utc()
        event_payload = deepcopy(dict(payload or {}))
        event_body = {
            "event_type": str(event_type),
            "object_sha256": object_sha256,
            "occurred_at": occurred_at,
            "payload": event_payload,
            "previous_event_sha256": previous or None,
        }
        digest = _sha_json(event_body)
        connection.execute(
            "INSERT INTO events(event_sha256,previous_event_sha256,event_type,object_sha256,occurred_at,payload_json) VALUES(?,?,?,?,?,?)",
            (
                digest,
                previous or None,
                str(event_type),
                object_sha256,
                occurred_at,
                json.dumps(event_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('event_chain_head',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (digest,),
        )
        return digest

    def _object_relative_path(self, kind: str, identity: str, visibility: str) -> str:
        safe_kind = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kind).strip("_")
        return f"objects/{visibility}/{safe_kind}/{identity[:2]}/{identity}.json"

    def ingest_object(self, value: Mapping[str, Any], *, visibility: str = "public") -> dict[str, Any]:
        payload = deepcopy(dict(value))
        homelab_validate_seal(payload)
        if visibility not in _VISIBILITIES:
            raise ValueError(f"invalid Homelab object visibility: {visibility}")
        identity = _object_identity(payload)
        kind = str(payload["kind"])
        body = _canonical_bytes(payload)
        raw_sha = _sha256_bytes(body)
        relative = self._object_relative_path(kind, identity, visibility)
        target = (self.root / relative).resolve()
        if self.root not in target.parents:
            raise ValueError("Homelab object path escaped the store")
        target.parent.mkdir(parents=True, exist_ok=True)
        _refuse_symlink_components(target.parent)
        created_file = False
        temporary: Path | None = None
        try:
            with self._transaction() as connection:
                existing = connection.execute("SELECT * FROM objects WHERE identity=?", (identity,)).fetchone()
                if existing is not None:
                    existing_path = (self.root / str(existing["relative_path"])).resolve()
                    if existing_path.is_symlink() or not existing_path.is_file():
                        raise ValueError(f"indexed Homelab object file is missing or unsafe: {identity}")
                    current = existing_path.read_bytes()
                    if _sha256_bytes(current) != str(existing["raw_sha256"]) or current != body:
                        raise ValueError(f"Homelab object identity collision: {identity}")
                    if str(existing["kind"]) != kind or str(existing["visibility"]) != visibility:
                        raise ValueError(f"Homelab object visibility/kind collision: {identity}")
                    return {
                        "ok": True,
                        "identity": identity,
                        "kind": kind,
                        "visibility": visibility,
                        "relative_path": str(existing["relative_path"]),
                        "created": False,
                    }

                if target.exists():
                    if target.is_symlink() or not target.is_file() or target.read_bytes() != body:
                        raise ValueError(f"unindexed Homelab object collision: {target}")
                else:
                    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
                    with temporary.open("xb") as handle:
                        handle.write(body)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if _sha256_bytes(temporary.read_bytes()) != raw_sha:
                        raise ValueError("Homelab object changed during materialization")
                    os.replace(temporary, target)
                    created_file = True
                    _fsync_directory(target.parent)

                connection.execute(
                    "INSERT INTO objects(identity,kind,visibility,relative_path,raw_sha256,bytes,created_at) VALUES(?,?,?,?,?,?,?)",
                    (identity, kind, visibility, relative, raw_sha, len(body), _now_utc()),
                )
                self._append_event(
                    connection,
                    "object_ingested",
                    object_sha256=identity,
                    payload={"kind": kind, "visibility": visibility, "relative_path": relative, "raw_sha256": raw_sha},
                )
            return {
                "ok": True,
                "identity": identity,
                "kind": kind,
                "visibility": visibility,
                "relative_path": relative,
                "created": True,
            }
        except Exception:
            if temporary is not None:
                with suppress(FileNotFoundError):
                    temporary.unlink()
            if created_file:
                row = self._connection.execute("SELECT 1 FROM objects WHERE identity=?", (identity,)).fetchone()
                if row is None:
                    with suppress(FileNotFoundError):
                        target.unlink()
                    with suppress(Exception):
                        _fsync_directory(target.parent)
            raise

    def load_object(self, identity: str, *, allow_private: bool = False, allow_sensitive: bool = False) -> dict[str, Any]:
        row = self._connection.execute("SELECT * FROM objects WHERE identity=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        visibility = str(row["visibility"])
        if visibility == "private" and not allow_private:
            raise PermissionError("private Homelab object requires explicit access")
        if visibility == "sensitive" and not allow_sensitive:
            raise PermissionError("sensitive Homelab object requires explicit access")
        path = (self.root / str(row["relative_path"])).resolve()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Homelab object file is missing or unsafe: {identity}")
        body = path.read_bytes()
        if _sha256_bytes(body) != str(row["raw_sha256"]):
            raise ValueError(f"Homelab object raw hash mismatch: {identity}")
        value = json.loads(body.decode("utf-8"))
        homelab_validate_seal(value)
        if _object_identity(value) != identity:
            raise ValueError(f"Homelab object identity mismatch: {identity}")
        return value

    def register_campaign(self, campaign: Mapping[str, Any], *, priority: int = 0, max_attempts: int = 3) -> dict[str, Any]:
        payload = deepcopy(dict(campaign))
        homelab_validate_seal(payload)
        if payload.get("kind") != "earcrate_homelab_campaign":
            raise ValueError("register_campaign requires a HomelabCampaign")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        ingestion = self.ingest_object(payload, visibility="public")
        identity = str(payload["campaign_sha256"])
        tasks = [dict(task) for task in payload.get("tasks") or []]
        ids = [str(task.get("task_id") or "") for task in tasks]
        if not ids or len(ids) != len(set(ids)) or any(not value for value in ids):
            raise ValueError("campaign task IDs must be nonempty and unique")
        known = set(ids)
        for task in tasks:
            unknown = sorted(set(str(value) for value in task.get("depends_on") or []) - known)
            if unknown:
                raise ValueError(f"task {task['task_id']} has unknown dependencies: {unknown}")
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM campaigns WHERE campaign_sha256=?", (identity,)).fetchone()
            if existing is not None:
                return {"ok": True, "campaign_sha256": identity, "created": False, "tasks": len(tasks)}
            connection.execute(
                "INSERT INTO campaigns(campaign_sha256,catalog_sha256,audit_sha256,state,created_at) VALUES(?,?,?,?,?)",
                (identity, payload["catalog_sha256"], payload["audit_sha256"], "active", _now_utc()),
            )
            for task in tasks:
                initial = "queued" if str(task.get("status") or "") == "ready" else "blocked"
                connection.execute(
                    """
                    INSERT INTO tasks(
                        campaign_sha256,task_id,target_id,stage,task_type,status,priority,resource,
                        assigned_node_sha256,dependencies_json,attempts,max_attempts,available_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        identity,
                        str(task["task_id"]),
                        str(task.get("target_id") or ""),
                        str(task.get("stage") or "") or None,
                        str(task.get("task_type") or "stage"),
                        initial,
                        int(task.get("priority") if task.get("priority") is not None else priority),
                        str(task.get("resource") or "cpu"),
                        str(task.get("assigned_node_sha256") or "") or None,
                        json.dumps(list(task.get("depends_on") or []), sort_keys=True, separators=(",", ":")),
                        0,
                        int(task.get("max_attempts") or max_attempts),
                        0.0,
                        _now_utc(),
                    ),
                )
            self._append_event(connection, "campaign_registered", object_sha256=identity, payload={"tasks": len(tasks)})
        return {"ok": True, "campaign_sha256": identity, "created": bool(ingestion["created"]), "tasks": len(tasks)}

    def _recover_expired(self, connection: sqlite3.Connection, now: float) -> int:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE status='leased' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            if attempts >= max_attempts:
                status = "failed"
                available_at = now
                error = "lease expired after maximum attempts"
            else:
                status = "queued"
                available_at = now + min(3600.0, 30.0 * (2 ** max(0, attempts - 1)))
                error = "lease expired; task requeued"
            connection.execute(
                """
                UPDATE tasks SET status=?,available_at=?,lease_token_sha256=NULL,leased_by=NULL,
                    lease_expires_at=NULL,last_error=?,updated_at=?
                WHERE campaign_sha256=? AND task_id=?
                """,
                (status, available_at, error, _now_utc(), row["campaign_sha256"], row["task_id"]),
            )
            self._append_event(
                connection,
                "task_lease_expired",
                payload={"campaign_sha256": row["campaign_sha256"], "task_id": row["task_id"], "new_status": status},
            )
        return len(rows)

    def _dependencies_complete(self, connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
        for task_id in json.loads(str(row["dependencies_json"] or "[]")):
            dependency = connection.execute(
                "SELECT status FROM tasks WHERE campaign_sha256=? AND task_id=?",
                (row["campaign_sha256"], str(task_id)),
            ).fetchone()
            if dependency is None or str(dependency["status"]) != "completed":
                return False
        return True

    def _resource_available(self, connection: sqlite3.Connection, resource: str) -> bool:
        group = _resource_group(resource)
        if group is None:
            return True
        for row in connection.execute("SELECT resource FROM tasks WHERE status='leased'").fetchall():
            if _resource_group(str(row["resource"])) == group:
                return False
        return True

    def lease_next(
        self,
        *,
        worker_id: str,
        resources: Sequence[str] = (),
        lease_seconds: int = 900,
        campaign_sha256: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 30 or lease_seconds > 24 * 3600:
            raise ValueError("lease_seconds must be between 30 and 86400")
        selected_resources = {str(value) for value in resources}
        current = float(time.time() if now is None else now)
        with self._transaction() as connection:
            self._recover_expired(connection, current)
            query = (
                "SELECT t.* FROM tasks t JOIN campaigns c ON c.campaign_sha256=t.campaign_sha256 "
                "WHERE c.state='active' AND t.status='queued' AND t.available_at<=?"
            )
            params: list[Any] = [current]
            if campaign_sha256:
                query += " AND t.campaign_sha256=?"
                params.append(campaign_sha256)
            query += " ORDER BY t.priority DESC,t.campaign_sha256,t.task_id"
            chosen: sqlite3.Row | None = None
            for row in connection.execute(query, params).fetchall():
                resource = str(row["resource"])
                if selected_resources and resource not in selected_resources:
                    continue
                if not self._dependencies_complete(connection, row):
                    continue
                if not self._resource_available(connection, resource):
                    continue
                chosen = row
                break
            if chosen is None:
                return None
            token = secrets.token_urlsafe(32)
            token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
            expires = current + float(lease_seconds)
            cursor = connection.execute(
                """
                UPDATE tasks SET status='leased',attempts=attempts+1,lease_token_sha256=?,leased_by=?,
                    lease_expires_at=?,updated_at=? WHERE campaign_sha256=? AND task_id=? AND status='queued'
                """,
                (token_sha, worker_id.strip(), expires, _now_utc(), chosen["campaign_sha256"], chosen["task_id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("task lease lost a concurrent race")
            self._append_event(
                connection,
                "task_leased",
                payload={
                    "campaign_sha256": chosen["campaign_sha256"],
                    "task_id": chosen["task_id"],
                    "worker_id": worker_id.strip(),
                    "lease_expires_at": expires,
                },
            )
            return {
                "campaign_sha256": chosen["campaign_sha256"],
                "task_id": chosen["task_id"],
                "target_id": chosen["target_id"],
                "stage": chosen["stage"],
                "task_type": chosen["task_type"],
                "resource": chosen["resource"],
                "assigned_node_sha256": chosen["assigned_node_sha256"],
                "attempt": int(chosen["attempts"]) + 1,
                "max_attempts": int(chosen["max_attempts"]),
                "lease_token": token,
                "lease_expires_at": expires,
            }

    def _leased_task(self, connection: sqlite3.Connection, campaign_sha256: str, task_id: str, lease_token: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM tasks WHERE campaign_sha256=? AND task_id=?",
            (campaign_sha256, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown Homelab task {campaign_sha256}:{task_id}")
        if str(row["status"]) != "leased":
            raise ValueError("Homelab task is not currently leased")
        expected = str(row["lease_token_sha256"] or "")
        actual = hashlib.sha256(str(lease_token).encode("utf-8")).hexdigest()
        if not secrets.compare_digest(expected, actual):
            raise PermissionError("Homelab task lease token mismatch")
        return row

    def heartbeat(
        self,
        campaign_sha256: str,
        task_id: str,
        lease_token: str,
        *,
        extend_seconds: int = 900,
        now: float | None = None,
    ) -> dict[str, Any]:
        if extend_seconds < 30 or extend_seconds > 24 * 3600:
            raise ValueError("extend_seconds must be between 30 and 86400")
        current = float(time.time() if now is None else now)
        with self._transaction() as connection:
            row = self._leased_task(connection, campaign_sha256, task_id, lease_token)
            if row["lease_expires_at"] is not None and float(row["lease_expires_at"]) <= current:
                raise ValueError("Homelab task lease has expired")
            expires = current + float(extend_seconds)
            connection.execute(
                "UPDATE tasks SET lease_expires_at=?,updated_at=? WHERE campaign_sha256=? AND task_id=?",
                (expires, _now_utc(), campaign_sha256, task_id),
            )
            self._append_event(connection, "task_heartbeat", payload={"campaign_sha256": campaign_sha256, "task_id": task_id, "lease_expires_at": expires})
        return {"ok": True, "campaign_sha256": campaign_sha256, "task_id": task_id, "lease_expires_at": expires}

    def complete_task(
        self,
        campaign_sha256: str,
        task_id: str,
        lease_token: str,
        *,
        outcome: str,
        evidence_sha256: str | None = None,
        error: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"completed", "failed", "refused", "cancelled"}:
            raise ValueError(f"invalid task outcome: {outcome}")
        current = float(time.time() if now is None else now)
        with self._transaction() as connection:
            row = self._leased_task(connection, campaign_sha256, task_id, lease_token)
            if row["lease_expires_at"] is not None and float(row["lease_expires_at"]) <= current:
                raise ValueError("Homelab task lease has expired")
            if outcome == "completed":
                if not evidence_sha256 or not _is_sha256(str(evidence_sha256)):
                    raise ValueError("completed Homelab task requires an evidence object identity")
                if connection.execute("SELECT 1 FROM objects WHERE identity=?", (evidence_sha256,)).fetchone() is None:
                    raise ValueError("completed task evidence has not been ingested into this store")
                status = "completed"
                available_at = current
                last_error = None
            elif outcome == "failed" and int(row["attempts"]) < int(row["max_attempts"]):
                status = "queued"
                available_at = current + min(3600.0, 30.0 * (2 ** max(0, int(row["attempts"]) - 1)))
                last_error = str(error or "task failed; retry scheduled")[:4000]
                evidence_sha256 = None
            else:
                status = outcome
                available_at = current
                last_error = str(error or "")[:4000] or None
                evidence_sha256 = evidence_sha256 if evidence_sha256 and _is_sha256(str(evidence_sha256)) else None
            connection.execute(
                """
                UPDATE tasks SET status=?,available_at=?,lease_token_sha256=NULL,leased_by=NULL,
                    lease_expires_at=NULL,last_error=?,evidence_sha256=?,updated_at=?
                WHERE campaign_sha256=? AND task_id=?
                """,
                (status, available_at, last_error, evidence_sha256, _now_utc(), campaign_sha256, task_id),
            )
            self._append_event(
                connection,
                "task_completed" if status == "completed" else ("task_requeued" if status == "queued" else "task_terminal"),
                object_sha256=evidence_sha256,
                payload={"campaign_sha256": campaign_sha256, "task_id": task_id, "outcome": outcome, "new_status": status},
            )
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE campaign_sha256=? AND status NOT IN ('completed','failed','refused','cancelled')",
                (campaign_sha256,),
            ).fetchone()
            if remaining and int(remaining["count"]) == 0:
                connection.execute("UPDATE campaigns SET state='completed' WHERE campaign_sha256=?", (campaign_sha256,))
                self._append_event(connection, "campaign_completed", object_sha256=campaign_sha256)
        return {"ok": True, "campaign_sha256": campaign_sha256, "task_id": task_id, "status": status, "evidence_sha256": evidence_sha256}

    def cancel_campaign(self, campaign_sha256: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("campaign cancellation reason is required")
        with self._transaction() as connection:
            row = connection.execute("SELECT state FROM campaigns WHERE campaign_sha256=?", (campaign_sha256,)).fetchone()
            if row is None:
                raise KeyError(campaign_sha256)
            connection.execute("UPDATE campaigns SET state='cancelled' WHERE campaign_sha256=?", (campaign_sha256,))
            connection.execute(
                """
                UPDATE tasks SET status='cancelled',lease_token_sha256=NULL,leased_by=NULL,
                    lease_expires_at=NULL,last_error=?,updated_at=?
                WHERE campaign_sha256=? AND status NOT IN ('completed','failed','refused','cancelled')
                """,
                (reason[:4000], _now_utc(), campaign_sha256),
            )
            self._append_event(connection, "campaign_cancelled", object_sha256=campaign_sha256, payload={"reason": reason})
        return {"ok": True, "campaign_sha256": campaign_sha256, "state": "cancelled"}

    def list_objects(self, *, visibility: str | None = None) -> list[dict[str, Any]]:
        if visibility is not None and visibility not in _VISIBILITIES:
            raise ValueError(f"invalid Homelab visibility: {visibility}")
        query = "SELECT * FROM objects"
        params: tuple[Any, ...] = ()
        if visibility is not None:
            query += " WHERE visibility=?"
            params = (visibility,)
        query += " ORDER BY identity"
        return [dict(row) for row in self._connection.execute(query, params).fetchall()]

    def snapshot(self, *, include_private_counts: bool = False) -> dict[str, Any]:
        object_rows = []
        for row in self._connection.execute(
            "SELECT visibility,kind,COUNT(*) AS count,SUM(bytes) AS bytes FROM objects GROUP BY visibility,kind ORDER BY visibility,kind"
        ).fetchall():
            if not include_private_counts and str(row["visibility"]) != "public":
                continue
            object_rows.append({
                "visibility": row["visibility"],
                "kind": row["kind"],
                "count": int(row["count"]),
                "bytes": int(row["bytes"] or 0),
            })
        tasks = self._connection.execute("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status ORDER BY status").fetchall()
        campaigns = self._connection.execute("SELECT state,COUNT(*) AS count FROM campaigns GROUP BY state ORDER BY state").fetchall()
        head = self._connection.execute("SELECT value FROM meta WHERE key='event_chain_head'").fetchone()
        return homelab_seal({
            "schema_version": STORE_SCHEMA_VERSION,
            "kind": "earcrate_homelab_store_snapshot",
            "captured_at": _now_utc(),
            "schema_version_store": STORE_SCHEMA_VERSION,
            "objects": object_rows,
            "tasks": {str(row["status"]): int(row["count"]) for row in tasks},
            "campaigns": {str(row["state"]): int(row["count"]) for row in campaigns},
            "event_chain_head": str(head["value"] or "") if head else "",
        })

    def doctor(self, *, verify_objects: bool = True) -> dict[str, Any]:
        problems: list[dict[str, Any]] = []
        quick = str(self._connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick != "ok":
            problems.append({"check": "sqlite_quick_check", "error": quick})
        schema = self._connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if schema is None or int(schema["value"]) != STORE_SCHEMA_VERSION:
            problems.append({"check": "schema_version", "error": None if schema is None else schema["value"]})

        previous = ""
        for row in self._connection.execute("SELECT * FROM events ORDER BY sequence"):
            payload = json.loads(str(row["payload_json"]))
            body = {
                "event_type": str(row["event_type"]),
                "object_sha256": row["object_sha256"],
                "occurred_at": str(row["occurred_at"]),
                "payload": payload,
                "previous_event_sha256": previous or None,
            }
            actual = _sha_json(body)
            if str(row["previous_event_sha256"] or "") != previous:
                problems.append({"check": "event_chain_previous", "sequence": int(row["sequence"])})
            if actual != str(row["event_sha256"]):
                problems.append({"check": "event_chain_hash", "sequence": int(row["sequence"])})
            previous = str(row["event_sha256"])
        head = self._connection.execute("SELECT value FROM meta WHERE key='event_chain_head'").fetchone()
        if str(head["value"] or "") if head else "" != previous:
            current_head = str(head["value"] or "") if head else ""
            if current_head != previous:
                problems.append({"check": "event_chain_head", "expected": previous, "actual": current_head})

        indexed_paths: set[str] = set()
        if verify_objects:
            for row in self._connection.execute("SELECT * FROM objects ORDER BY identity"):
                relative = str(row["relative_path"])
                indexed_paths.add(relative)
                path = (self.root / relative).resolve()
                try:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError("missing or symlinked")
                    body = path.read_bytes()
                    if len(body) != int(row["bytes"]) or _sha256_bytes(body) != str(row["raw_sha256"]):
                        raise ValueError("raw hash or size mismatch")
                    value = json.loads(body.decode("utf-8"))
                    homelab_validate_seal(value)
                    if _object_identity(value) != str(row["identity"]):
                        raise ValueError("semantic identity mismatch")
                except Exception as exc:
                    problems.append({"check": "object", "identity": row["identity"], "error": f"{type(exc).__name__}: {exc}"})
            for path in self.objects_root.rglob("*.json"):
                relative = path.relative_to(self.root).as_posix()
                if relative not in indexed_paths:
                    problems.append({"check": "unindexed_object", "relative_path": relative})

        task_rows = self._connection.execute("SELECT * FROM tasks ORDER BY campaign_sha256,task_id").fetchall()
        task_keys = {(str(row["campaign_sha256"]), str(row["task_id"])) for row in task_rows}
        now = time.time()
        expired = 0
        for row in task_rows:
            if str(row["status"]) not in _TASK_STATES:
                problems.append({"check": "task_status", "task_id": row["task_id"]})
            for dependency in json.loads(str(row["dependencies_json"] or "[]")):
                if (str(row["campaign_sha256"]), str(dependency)) not in task_keys:
                    problems.append({"check": "task_dependency", "task_id": row["task_id"], "missing": dependency})
            leased = str(row["status"]) == "leased"
            lease_fields = bool(row["lease_token_sha256"] and row["leased_by"] and row["lease_expires_at"] is not None)
            if leased != lease_fields:
                problems.append({"check": "task_lease_consistency", "task_id": row["task_id"]})
            if leased and float(row["lease_expires_at"]) <= now:
                expired += 1
            if row["evidence_sha256"] and self._connection.execute("SELECT 1 FROM objects WHERE identity=?", (row["evidence_sha256"],)).fetchone() is None:
                problems.append({"check": "task_evidence", "task_id": row["task_id"], "missing": row["evidence_sha256"]})
        return {
            "ok": not problems,
            "root": str(self.root),
            "database": str(self.database_path),
            "schema_version": STORE_SCHEMA_VERSION,
            "sqlite_quick_check": quick,
            "problems": problems,
            "expired_leases": expired,
            "object_count": int(self._connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0]),
            "campaign_count": int(self._connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]),
            "task_count": len(task_rows),
            "event_count": int(self._connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "event_chain_head": previous or None,
        }


__all__ = ["STORE_SCHEMA_VERSION", "HomelabStore"]
