#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_GATE_STATES = {"qualified", "rejected", "failed", "blocked"}


class ContractError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: Mapping[str, Any], field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    data = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def beggin_review_sha256(payload: Mapping[str, Any], field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    data = (
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContractError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {path}")
    return value


def require_hex64(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not HEX64.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return text


def load_contract(path: Path) -> dict[str, Any]:
    contract = load_json(path)
    if contract.get("kind") != "earcrate_track_iteration_contract":
        raise ContractError("wrong contract kind")
    if contract.get("schema_version") != 1:
        raise ContractError("unsupported contract schema")
    if contract.get("track_id") != "A1-07":
        raise ContractError("this runner is restricted to A1-07")
    declared = require_hex64(contract.get("contract_sha256"), "contract_sha256")
    observed = canonical_sha256(contract, "contract_sha256")
    if declared != observed:
        raise ContractError(
            f"contract seal mismatch: declared {declared}, observed {observed}"
        )
    parent = dict(contract.get("parent") or {})
    require_hex64(
        parent.get("owner_review_receipt_sha256"),
        "parent.owner_review_receipt_sha256",
    )
    children = list(contract.get("children") or [])
    expected_ids = {
        "gold-v7-arc",
        "gold-v7-interplay",
        "gold-v7-production",
    }
    observed_ids = {
        str(row.get("candidate_id") or "")
        for row in children
        if isinstance(row, dict)
    }
    if observed_ids != expected_ids or len(children) != 3:
        raise ContractError("contract must declare the exact three v7 children")
    admission = dict(contract.get("machine_admission") or {})
    if int(admission.get("minimum_qualified_children", 0)) != 2:
        raise ContractError("v7 requires exactly two machine-qualified children")
    return contract


def verify_parent(contract: Mapping[str, Any], receipt: Path) -> dict[str, Any]:
    if not receipt.is_file() or receipt.is_symlink():
        raise ContractError(f"regular owner receipt required: {receipt}")
    expected = str(contract["parent"]["owner_review_receipt_sha256"])
    payload = load_json(receipt)
    declared = require_hex64(payload.get("review_sha256"), "review_sha256")
    observed = beggin_review_sha256(payload, "review_sha256")
    if declared != observed:
        raise ContractError(
            f"parent owner receipt seal mismatch: declared {declared}, observed {observed}"
        )
    if observed != expected:
        raise ContractError(
            f"wrong parent owner receipt: expected {expected}, observed {observed}"
        )
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_parent_verification",
        "contract_sha256": contract["contract_sha256"],
        "owner_review_receipt_sha256": observed,
        "owner_review_receipt_file_sha256": sha256_file(receipt),
    }


def _return_template(contract: Mapping[str, Any]) -> dict[str, Any]:
    child_ids = [row["candidate_id"] for row in contract["children"]]
    return {
        "schema_version": 1,
        "kind": "a1_07_gold_v7_estate_return",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": None,
        "parent_owner_review_receipt_sha256": contract["parent"][
            "owner_review_receipt_sha256"
        ],
        "parent_score_sha256": None,
        "parent_pcm_sha256": None,
        "child_score_sha256_by_candidate": {key: None for key in child_ids},
        "child_pcm_sha256_by_candidate": {key: None for key in child_ids},
        "reproduction_receipt_sha256_by_candidate": {
            key: None for key in child_ids
        },
        "machine_gate_result_by_candidate": {
            key: {"state": None, "reason": None} for key in child_ids
        },
        "declared_masks_by_candidate": {key: [] for key in child_ids},
        "qualified_child_count": 0,
        "owner_frontier_created": False,
        "review_public_path_or_null": None,
        "private_material_exported": False,
        "notes": [],
    }


def scaffold(contract: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().absolute()
    if workspace.exists():
        raise ContractError(f"workspace already exists: {workspace}")
    workspace.mkdir(parents=True)
    (workspace / "incumbent").mkdir()
    for child in contract["children"]:
        root = workspace / child["candidate_id"]
        (root / "authoring").mkdir(parents=True)
        (root / "render").mkdir()
        (root / "machine").mkdir()
        (root / "strategy.json").write_text(
            json.dumps(child, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (workspace / "RETURN.private.json").write_text(
        json.dumps(
            _return_template(contract),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    actions = [
        "# A1-07 gold-v7 local execution",
        "",
        f"Contract: `{contract['contract_sha256']}`",
        "",
    ]
    for index, action in enumerate(contract["estate_execution_order"], start=1):
        actions.append(f"{index}. {action}")
    actions.extend(
        [
            "",
            "Do not create an owner frontier below the two-child machine gate.",
            "Do not overwrite gold-v6 or count relative preference as acceptance.",
            "",
        ]
    )
    (workspace / "NEXT_ACTIONS.md").write_text(
        "\n".join(actions),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_workspace",
        "workspace": str(workspace),
        "contract_sha256": contract["contract_sha256"],
        "children": [row["candidate_id"] for row in contract["children"]],
        "return_ledger": str(workspace / "RETURN.private.json"),
    }


def verify_return(
    contract: Mapping[str, Any],
    ledger_path: Path,
) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    if ledger.get("kind") != "a1_07_gold_v7_estate_return":
        raise ContractError("wrong Estate return kind")
    if ledger.get("contract_sha256") != contract["contract_sha256"]:
        raise ContractError("Estate return belongs to another contract")
    expected_parent = contract["parent"]["owner_review_receipt_sha256"]
    if ledger.get("parent_owner_review_receipt_sha256") != expected_parent:
        raise ContractError("Estate return does not bind the protected parent receipt")

    required = list(contract["estate_return_contract"]["required_fields"])
    missing = [key for key in required if key not in ledger]
    if missing:
        raise ContractError("Estate return missing fields: " + ", ".join(missing))

    require_hex64(ledger.get("exact_branch_head"), "exact_branch_head")
    require_hex64(ledger.get("parent_score_sha256"), "parent_score_sha256")
    require_hex64(ledger.get("parent_pcm_sha256"), "parent_pcm_sha256")

    child_ids = [row["candidate_id"] for row in contract["children"]]
    gate_rows = dict(ledger.get("machine_gate_result_by_candidate") or {})
    if set(gate_rows) != set(child_ids):
        raise ContractError("machine gate ledger must classify every child")

    qualified: list[str] = []
    for child_id in child_ids:
        row = gate_rows.get(child_id)
        if not isinstance(row, dict):
            raise ContractError(f"machine gate row must be an object: {child_id}")
        state = str(row.get("state") or "")
        if state not in TERMINAL_GATE_STATES:
            raise ContractError(f"nonterminal machine gate state for {child_id}")
        if state == "qualified":
            qualified.append(child_id)
            for field in (
                "child_score_sha256_by_candidate",
                "child_pcm_sha256_by_candidate",
                "reproduction_receipt_sha256_by_candidate",
            ):
                values = dict(ledger.get(field) or {})
                require_hex64(values.get(child_id), f"{field}.{child_id}")

    declared_count = int(ledger.get("qualified_child_count", -1))
    if declared_count != len(qualified):
        raise ContractError(
            f"qualified_child_count is {declared_count}, observed {len(qualified)}"
        )

    minimum = int(contract["machine_admission"]["minimum_qualified_children"])
    frontier = bool(ledger.get("owner_frontier_created"))
    public_path = ledger.get("review_public_path_or_null")
    if declared_count < minimum:
        if frontier or public_path not in {None, ""}:
            raise ContractError(
                "owner frontier is prohibited below the two-child machine gate"
            )
    elif frontier and not str(public_path or "").strip():
        raise ContractError("created owner frontier requires a local public path")

    if bool(ledger.get("private_material_exported")):
        raise ContractError("private material may not be exported")

    return {
        "ok": True,
        "kind": "a1_07_gold_v7_estate_return_verification",
        "contract_sha256": contract["contract_sha256"],
        "qualified_children": qualified,
        "qualified_child_count": len(qualified),
        "owner_frontier_created": frontier,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Validate and scaffold the A1-07 gold-v7 iteration"
    )
    root.add_argument(
        "--contract",
        type=Path,
        default=Path("configs/album_one/a1-07/gold-v7-iteration.v1.json"),
    )
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("verify-contract")

    parent = sub.add_parser("verify-parent")
    parent.add_argument("--receipt", type=Path, required=True)

    workspace = sub.add_parser("scaffold")
    workspace.add_argument("--workspace", type=Path, required=True)

    returned = sub.add_parser("verify-return")
    returned.add_argument("--ledger", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.command == "verify-contract":
            result = {
                "ok": True,
                "kind": contract["kind"],
                "contract_sha256": contract["contract_sha256"],
                "track_id": contract["track_id"],
                "iteration_id": contract["iteration_id"],
                "children": [
                    row["candidate_id"] for row in contract["children"]
                ],
            }
        elif args.command == "verify-parent":
            result = verify_parent(contract, args.receipt)
        elif args.command == "scaffold":
            result = scaffold(contract, args.workspace)
        elif args.command == "verify-return":
            result = verify_return(contract, args.ledger)
        else:
            raise ContractError(f"unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ContractError as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
