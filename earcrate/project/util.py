from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class ProjectError(RuntimeError):
    """Base class for project/score failures."""


class ValidationError(ProjectError):
    """Raised when a project, revision, source, or render program is invalid."""


class ConcurrencyError(ProjectError):
    """Raised when a command targets a stale project head."""


class SourceChangedError(ProjectError):
    """Raised when a source no longer matches the identity sealed in a revision."""


class RenderError(ProjectError):
    """Raised when an exact render program cannot be executed completely."""


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Stable JSON used for every content identity in the project engine."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def deep_copy_json(value: Any) -> Any:
    """Copy a JSON-compatible value without retaining object aliases."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def stable_id(prefix: str, value: Any, length: int = 20) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", prefix).strip("-") or "id"
    return f"{safe}:{sha256_json(value)[:length]}"


def random_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def ensure_within(path: Path, root: Path) -> Path:
    rp = path.expanduser().resolve()
    rr = root.expanduser().resolve()
    try:
        rp.relative_to(rr)
    except ValueError as exc:
        raise ValidationError(f"path escapes project root: {rp}") from exc
    return rp


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
        _fsync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return path


def atomic_write_text(path: Path, text: str) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> Path:
    return atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def append_jsonl_fsync(path: Path, value: Mapping[str, Any]) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(dict(value)))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_dir(path.parent)
    return path


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValidationError(f"invalid JSON at {path}: {exc}") from exc


def require_keys(mapping: Mapping[str, Any], keys: Iterable[str], where: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValidationError(f"{where} missing required field(s): {', '.join(missing)}")


def finite_number(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{where} must be numeric") from exc
    if not (-float("inf") < number < float("inf")):
        raise ValidationError(f"{where} must be finite")
    return number


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def merge_dict(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = merge_dict(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out
