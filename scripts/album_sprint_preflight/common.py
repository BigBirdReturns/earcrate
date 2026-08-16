from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HOMELAB_REQUIRED_SWITCHES = {"Catalog", "Audit", "Bindings", "Workspace", "Profile", "Case"}
HOMELAB_FORBIDDEN_SWITCHES = {"Mode", "CaseId", "SourceBinding"}


class PreflightError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def seal(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    value.pop(field, None)
    value[field] = digest(canonical(value))
    return value


def require_seal(payload: Mapping[str, Any], field: str) -> str:
    claimed = str(payload.get(field) or "").lower()
    observed = seal(payload, field)[field]
    if not HEX64.fullmatch(claimed) or claimed != observed:
        raise PreflightError(f"invalid {field}: declared {claimed or '<missing>'}, observed {observed}")
    return claimed


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PreflightError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"JSON object required: {path}")
    return value


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_bindings(path: Path | None, campaign: Mapping[str, Any], verify_bytes: bool) -> dict[str, dict[str, dict[str, Any]]]:
    declared = {} if path is None else load(path)
    if declared and declared.get("kind") != "earcrate_album_sprint_private_bindings":
        raise PreflightError("wrong private bindings kind")
    if declared and declared.get("contract_sha256") != campaign.get("contract_sha256"):
        raise PreflightError("private bindings belong to another campaign")
    raw_tracks = declared.get("tracks") or {}
    resolved: dict[str, dict[str, dict[str, Any]]] = {}
    for track in campaign.get("tracks") or []:
        track_id = str(track["track_id"])
        rows = {
            str(row.get("binding_id") or ""): dict(row)
            for row in (raw_tracks.get(track_id) or {}).get("bindings", [])
        }
        resolved[track_id] = {}
        for binding_id, row in rows.items():
            raw = row.get("artifact_path")
            if raw in {None, ""}:
                row.update({"available": False, "artifact_path": None})
            else:
                candidate = Path(str(raw)).expanduser().absolute()
                if candidate.is_symlink() or not candidate.exists() or (not candidate.is_file() and not candidate.is_dir()):
                    raise PreflightError(f"{track_id}/{binding_id} is not an existing regular file or directory")
                row.update({"available": True, "artifact_path": str(candidate)})
                if candidate.is_file():
                    row["observed_bytes"] = candidate.stat().st_size
                    if verify_bytes or row.get("container_sha256"):
                        row["observed_sha256"] = file_digest(candidate)
                        expected = str(row.get("container_sha256") or "").lower()
                        if expected and expected != row["observed_sha256"]:
                            raise PreflightError(f"{track_id}/{binding_id} SHA-256 mismatch")
            resolved[track_id][binding_id] = row
    return resolved


def available(bindings: Mapping[str, Any], binding_id: str) -> bool:
    return bool((bindings.get(binding_id) or {}).get("available"))


def missing(bindings: Mapping[str, Any], ids: Sequence[str]) -> list[str]:
    return [binding_id for binding_id in ids if not available(bindings, binding_id)]


def blocker(kind: str, stage: str, detail: str, **evidence: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "stage": stage, "detail": detail}
    result.update(evidence)
    return result


def base_result(track_id: str, adapter: str) -> dict[str, Any]:
    return {
        "track_id": track_id, "adapter": adapter, "state": "campaign_task_materialized",
        "tool_contract_ready": False, "binding_contract_ready": False,
        "representative_invocation_ready": False, "symbolic_evidence_ready": False,
        "performance_realization_ready": False, "music_producing_path_ready": False,
        "evidence_tier": None, "observations": {}, "blockers": [],
    }


def powershell_switches(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\bparam\s*\((.*?)\)\s*\n", text, re.IGNORECASE | re.DOTALL)
    return set() if not match else set(re.findall(r"\$([A-Za-z][A-Za-z0-9_]*)", match.group(1)))


def template_switches(template: str) -> set[str]:
    marker = re.search(r"RUN_HOMELAB_FACTORY\.ps1(?P<body>.*)$", template, re.IGNORECASE)
    body = marker.group("body") if marker else template
    return set(re.findall(r"\s-([A-Za-z][A-Za-z0-9_]*)\b", body))
