from __future__ import annotations

import importlib.util
from pathlib import Path


def test_oss_component_and_model_ledgers_are_complete() -> None:
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "oss_audit.py"
    spec = importlib.util.spec_from_file_location("earcrate_oss_audit", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = module.audit_oss_ledgers()
    assert receipt["ok"] is True
    assert "mido" in receipt["active_components"]
    assert "basic-pitch" in receipt["active_components"]
