from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "dispatch_robi_forward_phrase_resident.py"
)
SPEC = importlib.util.spec_from_file_location(
    "dispatch_robi_forward_phrase_resident", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _contract() -> dict:
    return {
        "commission_id": module.COMMISSION_ID,
        "status": "authorized_for_headless_resident_execution",
        "execution_boundary": {
            "headless": True,
            "browser_or_http_server": False,
            "ace_step": False,
            "provider_requalification": False,
            "crate_rebuild": False,
            "compatibility_graph_mutation": False,
            "global_crate_stamp_mutation": False,
            "owner_receipt_brokerage": False,
        },
    }


def test_load_contract_enforces_headless_boundary(tmp_path: Path) -> None:
    contract_path = tmp_path / module.CONTRACT_RELATIVE
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
    loaded, path, digest = module.load_contract(tmp_path)
    assert loaded["commission_id"] == module.COMMISSION_ID
    assert path == contract_path.resolve()
    assert digest == module.sha256_file(contract_path)


def test_load_contract_rejects_browser_boundary(tmp_path: Path) -> None:
    contract = _contract()
    contract["execution_boundary"]["browser_or_http_server"] = True
    contract_path = tmp_path / module.CONTRACT_RELATIVE
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    try:
        module.load_contract(tmp_path)
    except module.DispatchError as exc:
        assert "unsafe execution boundary" in str(exc)
    else:
        raise AssertionError("unsafe browser boundary was accepted")


def test_seal_contract_is_idempotent_and_refuses_different_contract(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
    digest = module.sha256_file(contract_path)
    state_dir = tmp_path / "state"
    first = module.seal_contract(
        _contract(), contract_path, digest, state_dir, {"head": "abc"}
    )
    second = module.seal_contract(
        _contract(), contract_path, digest, state_dir, {"head": "abc"}
    )
    assert first["disposition"] == "created"
    assert second["disposition"] == "already_identical"

    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps({**_contract(), "changed": True}), encoding="utf-8")
    changed_digest = module.sha256_file(changed_path)
    try:
        module.seal_contract(
            {**_contract(), "changed": True},
            changed_path,
            changed_digest,
            state_dir,
            {"head": "abc"},
        )
    except module.DispatchError as exc:
        assert "different commission" in str(exc)
    else:
        raise AssertionError("different sealed contract was accepted")


def test_terminal_state_only_admits_terminal_or_owner_review_states(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    ledger = state_dir / "LEDGER.json"
    ledger.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    assert module.terminal_state(state_dir) is None

    ledger.write_text(
        json.dumps({"status": "qualified_owner_review"}), encoding="utf-8"
    )
    terminal = module.terminal_state(state_dir)
    assert terminal is not None
    assert terminal["status"] == "qualified_owner_review"
    assert terminal["ledger_sha256"] == module.sha256_file(ledger)


def test_runner_command_passes_commission_and_headless_authority() -> None:
    runner = Path(r"C:\EarCrate\Run-EarCrate-Resident-Campaign.cmd")
    commission = Path(r"C:\EarCrate\estate\COMMISSION.json")
    command = module.runner_command(runner, commission)
    assert command[:4] == ["cmd.exe", "/d", "/c", str(runner)]
    assert "--commission" in command
    assert str(commission) in command
    assert "--headless" in command
