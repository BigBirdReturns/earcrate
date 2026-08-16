from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, MutableMapping

SCHEMA_VERSION = 1
HASH_FIELDS = {
    "earcrate_generation_provider_catalog": "catalog_sha256",
    "earcrate_generation_provider_probe": "probe_sha256",
    "earcrate_generation_request": "request_sha256",
    "earcrate_generation_campaign": "campaign_sha256",
    "earcrate_generation_run_receipt": "receipt_sha256",
    "earcrate_generated_material": "material_sha256",
    "earcrate_generation_frontier": "frontier_sha256",
    "earcrate_generation_public_projection": "projection_sha256",
}
TASK_MODES = {
    "text_to_music", "lyrics_to_song", "cover", "remix", "repaint", "extend", "retake",
    "complete", "vocal_to_bgm", "lego", "extract", "separate", "bgm_only", "vocal_only",
    "dual_track", "reference_conditioned", "single_track_icl", "dual_track_icl", "continuation",
    "melody_conditioned_cover", "segment_generation",
}
PROVIDER_CLASSES = {
    "foundation_model", "specialist_model", "codec", "embedding_model", "transcription_model",
    "commodity_host", "commercial_comparator",
}
AUTHORITY_LIMITS = {
    "canonical_musical_write": False,
    "provider_adoption": False,
    "human_acceptance": False,
    "rights_decision": False,
    "publication_decision": False,
}
AUDIO_SUFFIXES = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus"}
_SECRET_KEY_FRAGMENTS = (
    "token", "password", "secret", "credential", "api_key", "cookie", "authorization",
    "lyrics", "prompt", "caption", "conditioning", "source_text",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|file://)", re.IGNORECASE)


class GenerativeFloorError(RuntimeError):
    """Base class for explicit generative-floor refusals."""


class ValidationError(GenerativeFloorError):
    """Raised when an authority object violates the contract."""


class ProviderUnavailable(GenerativeFloorError):
    """Raised when a provider is not executable on the current estate."""


@dataclass(frozen=True)
class ArtifactIdentity:
    name: str
    sha256: str
    bytes: int
    media_kind: str


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").lower()))


def seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HASH_FIELDS.get(kind)
    if not field:
        raise ValidationError(f"unknown generative-floor kind: {kind!r}")
    value.pop(field, None)
    value[field] = sha256_bytes(canonical_json_bytes(value))
    return value


def validate_seal(payload: Mapping[str, Any], *, kind: str | None = None) -> str:
    value = deepcopy(dict(payload))
    actual_kind = str(value.get("kind") or "")
    if kind and actual_kind != kind:
        raise ValidationError(f"expected {kind}, got {actual_kind!r}")
    field = HASH_FIELDS.get(actual_kind)
    if not field:
        raise ValidationError(f"unknown generative-floor kind: {actual_kind!r}")
    if int(value.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValidationError("unsupported generative-floor schema version")
    claimed = str(value.pop(field, "")).lower()
    if not is_sha256(claimed):
        raise ValidationError(f"invalid or missing {field}")
    computed = sha256_bytes(canonical_json_bytes(value))
    if computed != claimed:
        raise ValidationError(f"{field} mismatch: expected {claimed}, computed {computed}")
    return claimed


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"JSON object required: {path}")
    return value


def atomic_write(path: str | Path, data: bytes, *, exclusive: bool = False) -> Path:
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and target.exists():
        raise FileExistsError(target)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and target.exists():
            raise FileExistsError(target)
        os.replace(temporary, target)
        if os.name != "nt":
            directory = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def write_json(path: str | Path, value: Mapping[str, Any], *, exclusive: bool = False) -> Path:
    return atomic_write(
        path,
        (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        exclusive=exclusive,
    )


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def looks_like_local_path(text: str) -> bool:
    stripped = text.strip()
    if _ABSOLUTE_PATH_RE.match(stripped):
        return True
    return stripped.startswith("/") and not stripped.startswith(("/api/", "/health", "/docs"))


def require_portable(value: Mapping[str, Any], *, label: str) -> None:
    leaked = [text for text in _walk_strings(value) if looks_like_local_path(text)]
    if leaked:
        raise ValidationError(f"{label} contains local or absolute paths: {leaked[:3]}")


def require_nonempty(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{label} is required")
    return text


def artifact_identity(path: str | Path) -> ArtifactIdentity:
    source = Path(path).expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise ValidationError(f"regular non-symlink artifact required: {source}")
    before = source.stat()
    digest = sha256_file(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValidationError(f"artifact changed while hashing: {source}")
    return ArtifactIdentity(
        name=source.name,
        sha256=digest,
        bytes=int(after.st_size),
        media_kind=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
    )


def redact(value: Any, *, key: str | None = None, counters: MutableMapping[str, int] | None = None) -> Any:
    counters = counters if counters is not None else {"paths": 0, "secrets": 0}
    normalized = str(key or "").casefold()
    if any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS) and not normalized.endswith("sha256"):
        counters["secrets"] = counters.get("secrets", 0) + 1
        return "redacted"
    if isinstance(value, Mapping):
        return {str(child_key): redact(child, key=str(child_key), counters=counters) for child_key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, key=key, counters=counters) for child in value]
    if isinstance(value, str) and looks_like_local_path(value):
        counters["paths"] = counters.get("paths", 0) + 1
        return "redacted:sha256:" + sha256_bytes(value.encode("utf-8"))
    return value
