#!/usr/bin/env python3
"""Validate EarCrate's OSS component and model governance ledgers."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_PATH = ROOT / "third_party" / "components.lock.json"
MODELS_PATH = ROOT / "third_party" / "models.lock.json"
REQUIREMENTS_PATH = ROOT / "requirements.txt"
ACTIVE_STATUSES = {"runtime", "optional-provider"}
APPROVED_MODEL_STATUSES = {"approved", "bundled"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _require_text(row: dict[str, Any], field: str, label: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"{label} is missing {field}")
    return value


def audit_oss_ledgers() -> dict[str, Any]:
    components = _load(COMPONENTS_PATH)
    models = _load(MODELS_PATH)
    requirements_text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    if int(components.get("schema_version") or 0) != 1:
        raise ValueError("unsupported components.lock.json schema")
    if int(models.get("schema_version") or 0) != 1:
        raise ValueError("unsupported models.lock.json schema")

    seen_components: set[str] = set()
    active_components: list[str] = []
    for index, row in enumerate(components.get("components") or []):
        if not isinstance(row, dict):
            raise ValueError(f"component row {index} is not an object")
        name = _require_text(row, "name", f"component row {index}")
        if name in seen_components:
            raise ValueError(f"duplicate component: {name}")
        seen_components.add(name)
        status = _require_text(row, "status", name)
        version = _require_text(row, "version_range", name)
        _require_text(row, "license_spdx", name)
        _require_text(row, "distribution_class", name)
        _require_text(row, "source", name)
        _require_text(row, "authority", name)
        if any(token in version.lower() for token in ("latest", "main", "master", "head")):
            raise ValueError(f"mutable version label forbidden for {name}: {version}")
        if status in ACTIVE_STATUSES:
            active_components.append(name)

    if "mido>=1.3,<2" not in requirements_text.replace(" ", ""):
        raise ValueError("requirements.txt must pin the active Mido commodity range mido>=1.3,<2")

    seen_models: set[str] = set()
    approved_models: list[str] = []
    for index, row in enumerate(models.get("models") or []):
        if not isinstance(row, dict):
            raise ValueError(f"model row {index} is not an object")
        model_id = _require_text(row, "model_id", f"model row {index}")
        if model_id in seen_models:
            raise ValueError(f"duplicate model: {model_id}")
        seen_models.add(model_id)
        status = _require_text(row, "status", model_id)
        _require_text(row, "provider", model_id)
        _require_text(row, "source", model_id)
        _require_text(row, "license_spdx", model_id)
        if status in APPROVED_MODEL_STATUSES:
            digest = _require_text(row, "sha256", model_id)
            if not SHA256_RE.fullmatch(digest):
                raise ValueError(f"approved model {model_id} has no valid SHA-256")
            approved_models.append(model_id)

    return {
        "ok": True,
        "components_sha256": _sha256(COMPONENTS_PATH),
        "models_sha256": _sha256(MODELS_PATH),
        "component_count": len(seen_components),
        "active_components": sorted(active_components),
        "model_count": len(seen_models),
        "approved_models": sorted(approved_models),
    }


def main() -> int:
    try:
        print(json.dumps(audit_oss_ledgers(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
