#!/usr/bin/env python3
"""Dispatch the Robi recognizable forward-phrase commission to the local resident.

This bridge performs no audio synthesis. It seals the public commission into the
resident estate, proves that the generic resident runner is present, and invokes
that runner headlessly with a single stable commission contract. The resident
owns source discovery, portfolio execution, checkpointing, negative-corpus use,
terminal closure, and owner-review admission.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

COMMISSION_ID = "robi_whoa_recognizable_forward_phrase_supported_record_v1"
CONTRACT_RELATIVE = Path(
    "configs/commissions/"
    "robi_whoa_recognizable_forward_phrase_supported_record.v1.json"
)
ISSUE_NUMBER = "132"
APPROVED_REMOTE = "https://github.com/BigBirdReturns/earcrate.git"


class DispatchError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        body = canonical_json_bytes(value)
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def find_project_root(explicit: str = "") -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("EARCRATE_PROJECT_ROOT"):
        candidates.append(Path(os.environ["EARCRATE_PROJECT_ROOT"]).expanduser())
    candidates.extend(
        [
            Path(r"D:\Projects\Products\EarCrate"),
            Path(r"S:\Projects\EarCrate"),
            Path.cwd(),
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        contract = resolved / CONTRACT_RELATIVE
        if contract.is_file():
            return resolved
        if resolved.name.casefold() == "main":
            parent_contract = resolved.parent / CONTRACT_RELATIVE
            if parent_contract.is_file():
                return resolved.parent
    raise DispatchError(
        "EarCrate product root containing the forward-phrase commission "
        "was not found"
    )


def load_contract(project_root: Path) -> tuple[dict[str, Any], Path, str]:
    path = (project_root / CONTRACT_RELATIVE).resolve()
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DispatchError(f"commission contract is unreadable: {exc}") from exc
    if not isinstance(contract, dict):
        raise DispatchError("commission contract is not a JSON object")
    if contract.get("commission_id") != COMMISSION_ID:
        raise DispatchError(
            f"commission id mismatch: {contract.get('commission_id')!r}"
        )
    if contract.get("status") != "authorized_for_headless_resident_execution":
        raise DispatchError("commission is not authorized for resident execution")
    boundary = contract.get("execution_boundary") or {}
    required_false = (
        "browser_or_http_server",
        "ace_step",
        "provider_requalification",
        "crate_rebuild",
        "compatibility_graph_mutation",
        "global_crate_stamp_mutation",
        "owner_receipt_brokerage",
    )
    if boundary.get("headless") is not True:
        raise DispatchError("commission does not require headless execution")
    bad = [key for key in required_false if boundary.get(key) is not False]
    if bad:
        raise DispatchError(f"unsafe execution boundary fields: {bad}")
    return contract, path, sha256_file(path)


def git_value(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def repository_context(project_root: Path) -> dict[str, Any]:
    candidates = [
        project_root / "main",
        project_root,
    ]
    repo = next((path for path in candidates if (path / ".git").exists()), None)
    if repo is None:
        return {
            "repository_found": False,
            "approved_remote": APPROVED_REMOTE,
        }
    remotes = {
        line.split()[0]: line.split()[1]
        for line in git_value(repo, "remote", "-v").splitlines()
        if len(line.split()) >= 2 and line.endswith("(fetch)")
    }
    return {
        "repository_found": True,
        "root": str(repo.resolve()),
        "branch": git_value(repo, "branch", "--show-current") or "(detached)",
        "head": git_value(repo, "rev-parse", "HEAD"),
        "dirty_porcelain": git_value(
            repo, "status", "--porcelain=v1", "--untracked-files=all"
        ),
        "remotes": remotes,
        "approved_remote": APPROVED_REMOTE,
        "approved_remote_present": APPROVED_REMOTE in remotes.values(),
    }


def locate_resident_runner(project_root: Path) -> Path:
    explicit = os.environ.get("EARCRATE_RESIDENT_RUNNER")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            project_root / "Run-EarCrate-Resident-Campaign.cmd",
            project_root / "Run-EarCrate-Resident-Campaign.ps1",
            project_root / "scripts" / "RUN_RESIDENT_CAMPAIGN.cmd",
            project_root / "scripts" / "RUN_RESIDENT_CAMPAIGN.ps1",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise DispatchError(
        "generic resident runner is missing; expected "
        "Run-EarCrate-Resident-Campaign.cmd or EARCRATE_RESIDENT_RUNNER"
    )


def resident_state_dir(project_root: Path) -> Path:
    return (
        project_root
        / "estate"
        / "runtime"
        / "resident-campaigns"
        / COMMISSION_ID
    ).resolve()


def seal_contract(
    contract: Mapping[str, Any],
    contract_path: Path,
    contract_sha: str,
    state_dir: Path,
    repo_context: Mapping[str, Any],
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    sealed_path = state_dir / "COMMISSION.json"
    sealed = {
        "kind": "earcrate_resident_commission",
        "schema_version": 1,
        "commission_id": COMMISSION_ID,
        "sealed_at": utc_now(),
        "source_contract": str(contract_path),
        "source_contract_sha256": contract_sha,
        "github_issue": ISSUE_NUMBER,
        "repository": dict(repo_context),
        "contract": dict(contract),
    }
    new_body = canonical_json_bytes(sealed)
    if sealed_path.exists():
        existing = sealed_path.read_bytes()
        try:
            existing_doc = json.loads(existing.decode("utf-8"))
        except Exception as exc:
            raise DispatchError(
                f"existing sealed commission is unreadable: {exc}"
            ) from exc
        existing_sha = str(existing_doc.get("source_contract_sha256") or "")
        if existing_sha != contract_sha:
            raise DispatchError(
                "a different commission is already sealed at "
                f"{sealed_path}; refusing overwrite"
            )
        return {
            "path": str(sealed_path),
            "sha256": sha256_file(sealed_path),
            "disposition": "already_identical",
        }
    temporary = sealed_path.with_name(f".{sealed_path.name}.tmp")
    temporary.write_bytes(new_body)
    os.replace(temporary, sealed_path)
    return {
        "path": str(sealed_path),
        "sha256": sha256_file(sealed_path),
        "disposition": "created",
    }


def terminal_state(state_dir: Path) -> dict[str, Any] | None:
    ledger_path = state_dir / "LEDGER.json"
    public_path = state_dir / "PUBLIC_STATUS.json"
    if not ledger_path.is_file():
        return None
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    status = str(ledger.get("status") or ledger.get("state") or "").casefold()
    if status not in {
        "closed",
        "terminal",
        "delivered",
        "qualified_owner_review",
        "mechanism_family_exhausted",
        "terminal_refusal",
    }:
        return None
    return {
        "ledger": str(ledger_path),
        "ledger_sha256": sha256_file(ledger_path),
        "public_status": str(public_path) if public_path.is_file() else None,
        "public_status_sha256": (
            sha256_file(public_path) if public_path.is_file() else None
        ),
        "status": status,
    }


def runner_command(runner: Path, commission_path: Path) -> list[str]:
    suffix = runner.suffix.casefold()
    arguments = [
        "--commission",
        str(commission_path),
        "--github-issue",
        ISSUE_NUMBER,
        "--headless",
    ]
    if suffix == ".cmd":
        return ["cmd.exe", "/d", "/c", str(runner), *arguments]
    if suffix == ".ps1":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            raise DispatchError("PowerShell is required for the resident runner")
        return [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
            *arguments,
        ]
    return [str(runner), *arguments]


def invoke_resident(
    project_root: Path,
    runner: Path,
    commission_path: Path,
    state_dir: Path,
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "EARCRATE_RESIDENT_COMMISSION": str(commission_path),
            "EARCRATE_RESIDENT_COMMISSION_ID": COMMISSION_ID,
            "EARCRATE_RESIDENT_GITHUB_ISSUE": ISSUE_NUMBER,
            "EARCRATE_RESIDENT_HEADLESS": "1",
            "EARCRATE_RESIDENT_OWNER_BROKERAGE": "0",
            "EARCRATE_RESIDENT_STATE_DIR": str(state_dir),
            "EARCRATE_APPROVED_EXPORT_REMOTE": APPROVED_REMOTE,
        }
    )
    command = runner_command(runner, commission_path)
    transcript = state_dir / "DISPATCH_TRANSCRIPT.txt"
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    transcript.write_text(
        "\n".join(
            [
                "command: " + subprocess.list2cmdline(command),
                f"returncode: {completed.returncode}",
                "",
                "[stdout]",
                completed.stdout,
                "",
                "[stderr]",
                completed.stderr,
            ]
        ),
        encoding="utf-8",
    )
    outcome = terminal_state(state_dir)
    if completed.returncode != 0 and outcome is None:
        raise DispatchError(
            "resident runner failed without a terminal ledger; "
            f"see {transcript}"
        )
    if outcome is None:
        raise DispatchError(
            "resident runner returned without producing a terminal or "
            "owner-review ledger"
        )
    return {
        "runner": str(runner),
        "command": command,
        "returncode": completed.returncode,
        "transcript": str(transcript),
        "transcript_sha256": sha256_file(transcript),
        "outcome": outcome,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default="")
    parser.add_argument("--seal-only", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    project_root = find_project_root(args.project_root)
    contract, contract_path, contract_sha = load_contract(project_root)
    state_dir = resident_state_dir(project_root)
    repo_context = repository_context(project_root)

    existing_terminal = terminal_state(state_dir)
    if existing_terminal is not None:
        print(
            json.dumps(
                {
                    "ok": True,
                    "commission_id": COMMISSION_ID,
                    "disposition": "already_terminal",
                    "state_dir": str(state_dir),
                    "outcome": existing_terminal,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    sealed = seal_contract(
        contract,
        contract_path,
        contract_sha,
        state_dir,
        repo_context,
    )
    dispatch = {
        "kind": "earcrate_resident_commission_dispatch",
        "schema_version": 1,
        "commission_id": COMMISSION_ID,
        "dispatched_at": utc_now(),
        "state_dir": str(state_dir),
        "contract": sealed,
        "repository": repo_context,
        "headless": True,
        "owner_receipt_brokerage": False,
        "github_issue": ISSUE_NUMBER,
    }
    atomic_json(state_dir / "DISPATCH.json", dispatch)

    if args.seal_only:
        print(json.dumps({"ok": True, **dispatch}, indent=2, sort_keys=True))
        return 0

    runner = locate_resident_runner(project_root)
    result = invoke_resident(
        project_root,
        runner,
        Path(sealed["path"]),
        state_dir,
    )
    final = {
        "ok": True,
        **dispatch,
        "resident": result,
    }
    atomic_json(state_dir / "DISPATCH_RESULT.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        refusal = {
            "ok": False,
            "commission_id": COMMISSION_ID,
            "failed_at": utc_now(),
            "failure": f"{type(exc).__name__}: {exc}",
            "browser_started": False,
            "owner_receipt_brokerage": False,
        }
        print(json.dumps(refusal, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)
