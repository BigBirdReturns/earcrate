#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT = ROOT / "configs" / "album_one" / "sprint-01" / "campaign.v1.json"
TRACK_IDS = tuple(f"A1-{i:02d}" for i in range(1, 8))
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STATES = {
    "campaign_task_materialized", "tool_contract_ready", "symbolic_evidence_ready",
    "performance_realization_ready", "frontier_ready", "blocked_exact_source",
    "blocked_exact_artifact_pack", "blocked_performance_realization",
    "blocked_exact_credential", "blocked_rights_or_custody", "failed",
}

CHILDREN_SCORE_ARTIFACTS = (
    "score_pdf", "score_extraction", "score_reconstruction_midi",
    "score_proof_receipt", "mix_score", "mix_execution_ledger",
)


class SprintError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sealed(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    value.pop(field, None)
    value[field] = digest(canonical(value))
    return value


def require_seal(payload: Mapping[str, Any], field: str) -> str:
    claimed = str(payload.get(field) or "").lower()
    observed = sealed(payload, field)[field]
    if not HEX64.fullmatch(claimed) or claimed != observed:
        raise SprintError(f"invalid {field}: declared {claimed}, observed {observed}")
    return claimed


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SprintError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SprintError(f"JSON object required: {path}")
    return value


def write_json(path: Path, payload: Mapping[str, Any], *, exclusive: bool = False) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise SprintError(f"refusing to overwrite {path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and path.exists():
            raise SprintError(f"refusing to overwrite {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_text(path: Path, text: str, *, exclusive: bool = False) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise SprintError(f"refusing to overwrite {path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and path.exists():
            raise SprintError(f"refusing to overwrite {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
        errors="replace", check=False, shell=False, timeout=30,
    )
    value = result.stdout.strip().lower()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SprintError(f"cannot resolve exact Git head: {result.stderr[-1000:]}")
    return value


def contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = load(path)
    if value.get("kind") != "earcrate_album_sprint_campaign":
        raise SprintError("wrong campaign kind")
    require_seal(value, "contract_sha256")
    tracks = value.get("tracks") or []
    if tuple(row.get("track_id") for row in tracks) != TRACK_IDS:
        raise SprintError("campaign must declare ordered A1-01 through A1-07")
    if (value.get("program_truth") or {}).get("active_workstreams") != list(TRACK_IDS):
        raise SprintError("all seven tracks must be active workstreams")
    budget = value.get("owner_time_budget") or {}
    if budget.get("frontiers_per_track_max") != 1 or budget.get("cuts_per_frontier_max") != 4:
        raise SprintError("owner budget must be one frontier and four cuts per track")
    if budget.get("musical_delta_disclosed") is not True:
        raise SprintError("musical delta disclosure is mandatory")
    if budget.get("shared_dominant_defect_invalidates_frontier") is not True:
        raise SprintError("shared dominant defects must invalidate frontiers")
    for row in tracks:
        if "payoff_or_release" not in (row.get("full_form") or {}).get("required_functions", []):
            raise SprintError(f"{row['track_id']} lacks full-form payoff/release")
        for evidence in row.get("repo_evidence") or []:
            if not (ROOT / evidence).is_file():
                raise SprintError(f"{row['track_id']} evidence missing: {evidence}")
    return value


def by_track(campaign: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["track_id"]): dict(row) for row in campaign["tracks"]}


def bindings_template(campaign: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "earcrate_album_sprint_private_bindings",
        "sprint_id": campaign["sprint_id"],
        "contract_sha256": campaign["contract_sha256"],
        "tracks": {
            track["track_id"]: {
                "bindings": [
                    {
                        "binding_id": item["binding_id"], "role": item["role"],
                        "artifact_path": None, "container_sha256": None,
                        "bytes": None, "visibility": "private",
                    }
                    for item in track["source_bindings"]
                ]
            }
            for track in campaign["tracks"]
        },
        "boundary": {"source_audio_copied": False, "publishable": False},
    }


def resolve_bindings(
    campaign: Mapping[str, Any], value: Mapping[str, Any], *, verify_bytes: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    if value.get("kind") != "earcrate_album_sprint_private_bindings":
        raise SprintError("wrong private bindings kind")
    if value.get("contract_sha256") != campaign["contract_sha256"]:
        raise SprintError("bindings belong to another campaign")
    declared = value.get("tracks") or {}
    resolved: dict[str, dict[str, Any]] = {}
    missing: dict[str, list[str]] = {}
    for track in campaign["tracks"]:
        tid = track["track_id"]
        expected = {item["binding_id"]: item for item in track["source_bindings"]}
        rows = {(item.get("binding_id") or ""): dict(item) for item in (declared.get(tid) or {}).get("bindings", [])}
        if set(rows) - set(expected):
            raise SprintError(f"{tid} contains unknown bindings")
        resolved[tid], missing[tid] = {}, []
        for bid, requirement in expected.items():
            item = rows.get(bid, {"binding_id": bid, "role": requirement["role"]})
            raw = item.get("artifact_path")
            if raw in {None, ""}:
                item.update({"artifact_path": None, "available": False})
            else:
                path = Path(str(raw)).expanduser().absolute()
                if path.is_symlink() or not path.exists() or (not path.is_file() and not path.is_dir()):
                    raise SprintError(f"{tid}/{bid} is not an existing regular file or directory")
                item.update({"artifact_path": str(path), "available": True})
                if path.is_file():
                    item["observed_bytes"] = path.stat().st_size
                    if verify_bytes or item.get("container_sha256"):
                        item["observed_sha256"] = file_digest(path)
                        if item.get("container_sha256") and item["observed_sha256"] != item["container_sha256"]:
                            raise SprintError(f"{tid}/{bid} SHA-256 mismatch")
            resolved[tid][bid] = item
            if requirement.get("required_for_frontier") and not item.get("available"):
                missing[tid].append(bid)
    return resolved, missing


def adapter_readiness(track: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    """Report observed adapter evidence without promoting dossier creation to readiness."""
    adapter = track.get("adapter")
    blockers: list[dict[str, Any]] = []
    symbolic = False
    if adapter == "score_audio_convergence":
        specimen = load(ROOT / "specimens" / "children_v1.json")
        external = {
            row["artifact_id"] for row in specimen.get("artifacts") or []
            if row.get("status") == "bound" and not row.get("repository_managed")
        }
        missing = [
            artifact for artifact in CHILDREN_SCORE_ARTIFACTS
            if artifact in external and not (bindings.get(artifact) or {}).get("available")
        ]
        if missing:
            blockers.append({
                "kind": "blocked_exact_artifact_pack",
                "missing_artifact_ids": missing,
                "detail": "The selected Children adapter requires the complete exact score pack.",
            })
    elif adapter == "community_symbolic_ensemble":
        witness = load(ROOT / "specimens" / "flim_bad_plus_v1.community-symbolic.json")
        symbolic = witness.get("evidence_tier") == "community_symbolic_witness"
        duration = float((witness.get("witness") or {}).get("duration_seconds") or 0)
        amplitude = float((witness.get("reproducibility") or {}).get("decoded_float_pcm_max_abs") or 0)
        minimum = float((track.get("full_form") or {}).get("minimum_seconds") or 0)
        blockers.append({
            "kind": "blocked_performance_realization",
            "observed_duration_seconds": duration,
            "minimum_duration_seconds": minimum,
            "decoded_float_pcm_max_abs": amplitude,
            "detail": "Community-symbolic evidence is silent and below the declared full-form floor.",
        })
    return {
        "campaign_task_materialized": True,
        "tool_contract_ready": adapter == "score_audio_convergence" and not blockers,
        "symbolic_evidence_ready": symbolic,
        "performance_realization_ready": False,
        "frontier_ready": False,
        "blockers": blockers,
    }


def task(campaign: Mapping[str, Any], track: Mapping[str, Any], bindings: Mapping[str, Any], missing: Sequence[str]) -> dict[str, Any]:
    can_progress = bool(track.get("machine_can_progress_without_frontier_bindings"))
    state = "campaign_task_materialized"
    return sealed({
        "schema_version": 1, "kind": "earcrate_album_sprint_track_task",
        "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
        "track_id": track["track_id"], "working_title": track["working_title"],
        "reference_class": track["reference_class"], "adapter": track["adapter"],
        "initial_state": state, "frontier_binding_missing": list(missing),
        "source_free_progress_allowed": can_progress,
        "readiness": adapter_readiness(track, bindings),
        "repo_evidence": track["repo_evidence"], "full_form": track["full_form"],
        "control": track["control"], "machine_steps": track["machine_steps"],
        "owner_delta": track["owner_delta"], "blocker_policy": track["blocker_policy"],
        "entrypoint": track.get("entrypoint") or {},
        "binding_projection": [
            {
                "binding_id": bid, "role": item.get("role"), "available": bool(item.get("available")),
                "container_sha256": item.get("observed_sha256") or item.get("container_sha256"),
                "bytes": item.get("observed_bytes") or item.get("bytes"),
            }
            for bid, item in sorted(bindings.items())
        ],
        "authority": {"owner_acceptance": False, "album_master": False, "system_reference_completed": False},
    }, "task_sha256")


def runbook(track: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(track["machine_steps"], 1))
    form = track["full_form"]
    return (
        f"# {track['track_id']} · {track['working_title']}\n\n"
        f"Initial state: `{item['initial_state']}`\n\n"
        f"Missing frontier bindings: {', '.join(item['frontier_binding_missing']) or 'none'}\n\n"
        f"Owner-visible delta: {track['owner_delta']}\n\n"
        f"Control: {track['control']}\n\n"
        f"Full form: {form['minimum_seconds']}–{form['maximum_seconds']} seconds; "
        f"{', '.join(form['required_functions'])}.\n\n"
        f"## Machine execution\n\n{steps}\n\n"
        "Return one typed result. Provider success and signal sanity are not musical acceptance.\n"
    )


def projection(campaign: Mapping[str, Any], states: Mapping[str, str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for state in states.values():
        counts[state] = counts.get(state, 0) + 1
    return sealed({
        "schema_version": 1, "kind": "earcrate_album_sprint_public_projection",
        "album_id": campaign["album_id"], "sprint_id": campaign["sprint_id"],
        "contract_sha256": campaign["contract_sha256"], "exact_branch_head": git_head(),
        "track_states": dict(states), "state_counts": counts,
        "owner_frontiers_created": sum(state == "frontier_ready" for state in states.values()),
        "accepted_album_masters": 0, "completed_system_references": 0,
        "private_paths_included": False, "source_audio_exported": False,
    }, "projection_sha256")


def prepare(campaign: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    final = workspace.expanduser().absolute()
    if final.exists():
        raise SprintError(f"workspace already exists: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{final.name}.", dir=final.parent))
    try:
        write_json(staging / "CAMPAIGN.json", campaign, exclusive=True)
        template = bindings_template(campaign)
        write_json(staging / "private/source-bindings.private.template.json", template, exclusive=True)
        resolved, missing = resolve_bindings(campaign, template, verify_bytes=False)
        states, tasks = {}, {}
        for track in campaign["tracks"]:
            tid, root = track["track_id"], staging / "tracks" / track["track_id"]
            root.mkdir(parents=True)
            item = task(campaign, track, resolved[tid], missing[tid])
            tasks[tid], states[tid] = item, item["initial_state"]
            write_json(root / "TRACK_TASK.json", item, exclusive=True)
            write_text(root / "RUNBOOK.md", runbook(track, item), exclusive=True)
            write_json(root / "TRACK_RESULT.private.template.json", {
                "schema_version": 1, "kind": "earcrate_album_sprint_track_result",
                "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
                "track_id": tid, "state": item["initial_state"], "detail": "",
                "frontier": None, "blocker": None,
            }, exclusive=True)
        queue = sealed({
            "schema_version": 1, "kind": "earcrate_album_sprint_task_queue",
            "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
            "parallel_track_lanes": 7,
            "tasks": {tid: {"task_sha256": item["task_sha256"], "adapter": item["adapter"]} for tid, item in tasks.items()},
        }, "queue_sha256")
        write_json(staging / "TASK_QUEUE.json", queue, exclusive=True)
        public = projection(campaign, states)
        write_json(staging / "PUBLIC_PROJECTION.json", public, exclusive=True)
        write_text(staging / "NEXT_ACTIONS.md",
            "# Album One Sprint 01\n\nExecute every `tracks/A1-XX/TRACK_TASK.json` to a typed result. Machine diagnostics remain private. Only full-form disclosed frontiers reach the owner.\n",
            exclusive=True)
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"ok": True, "workspace": str(final), "contract_sha256": campaign["contract_sha256"], "queue_sha256": queue["queue_sha256"], "projection_sha256": public["projection_sha256"]}


def materialize_command(track: Mapping[str, Any], root: Path, bindings: Mapping[str, Any]) -> str | None:
    template = (track.get("entrypoint") or {}).get("template")
    if not template:
        return None
    command = str(template).replace("{track_workspace}", str(root)).replace(
        "{track_binding_manifest}", str(root / "source-bindings.private.json"))
    for bid, item in bindings.items():
        token = "{binding:" + bid + "}"
        if token in command:
            if not item.get("artifact_path"):
                return None
            command = command.replace(token, str(item["artifact_path"]))
    return command


def dispatch(campaign: Mapping[str, Any], workspace: Path, bindings_path: Path, verify_bytes: bool) -> dict[str, Any]:
    root = workspace.expanduser().absolute()
    if not (root / "CAMPAIGN.json").is_file():
        raise SprintError("prepare the workspace before dispatch")
    resolved, missing = resolve_bindings(campaign, load(bindings_path), verify_bytes=verify_bytes)
    states, commands = {}, []
    for track in campaign["tracks"]:
        tid, track_root = track["track_id"], root / "tracks" / track["track_id"]
        item = task(campaign, track, resolved[tid], missing[tid])
        states[tid] = item["initial_state"]
        write_json(track_root / "TRACK_TASK.json", item)
        write_text(track_root / "RUNBOOK.md", runbook(track, item))
        write_json(track_root / "source-bindings.private.json", {
            "schema_version": 1, "kind": "earcrate_album_sprint_track_private_bindings",
            "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
            "track_id": tid, "bindings": list(resolved[tid].values()), "visibility": "private",
        })
        command = materialize_command(track, track_root, resolved[tid])
        if command and not missing[tid]:
            commands.append(tid)
            write_text(track_root / "NEXT_COMMAND.ps1", "$ErrorActionPreference = 'Stop'\n" + command + "\n")
        elif (track.get("entrypoint") or {}).get("kind") == "estate_agent_desk":
            write_text(track_root / "NEXT_COMMAND.txt", "Execute TRACK_TASK.json as a governed Estate agent desk to terminal evidence.\n")
    public = projection(campaign, states)
    write_json(root / "PUBLIC_PROJECTION.json", public)
    receipt = sealed({
        "schema_version": 1, "kind": "earcrate_album_sprint_dispatch_receipt",
        "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
        "exact_branch_head": git_head(), "track_states": states,
        "missing_frontier_bindings": missing, "commands_materialized": commands,
        "private_paths_included": True, "source_audio_copied": False,
    }, "dispatch_receipt_sha256")
    write_json(root / "private/dispatch-receipt.private.json", receipt)
    return {"ok": True, "workspace": str(root), "track_states": states, "commands_materialized": commands, "projection_sha256": public["projection_sha256"], "dispatch_receipt_sha256": receipt["dispatch_receipt_sha256"]}


def validate_frontier(track: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    cuts = [dict(row) for row in value.get("cuts") or []]
    if not 1 <= len(cuts) <= 4:
        raise SprintError(f"{track['track_id']} requires 1–4 cuts")
    if value.get("musical_delta_disclosed") is not True or value.get("shared_dominant_defect") is not False:
        raise SprintError(f"{track['track_id']} frontier is non-discriminating")
    required = set(track["full_form"]["required_functions"])
    functions = set(value.get("form_functions") or [])
    if not required.issubset(functions):
        raise SprintError(f"{track['track_id']} lacks full-form functions")
    low, high = track["full_form"]["minimum_seconds"], track["full_form"]["maximum_seconds"]
    normalized = []
    for cut in cuts:
        path = Path(str(cut.get("artifact_path") or "")).expanduser().absolute()
        duration = float(cut.get("duration_seconds") or 0)
        receipts = [str(x).lower() for x in cut.get("reproduction_receipt_sha256") or []]
        if path.is_symlink() or not path.is_file() or not low <= duration <= high:
            raise SprintError(f"{track['track_id']} invalid full-form cut")
        if len(receipts) < 2 or any(not HEX64.fullmatch(x) for x in receipts):
            raise SprintError(f"{track['track_id']} cut lacks two reproduction receipts")
        observed = file_digest(path)
        if cut.get("container_sha256") and cut["container_sha256"] != observed:
            raise SprintError(f"{track['track_id']} cut SHA-256 mismatch")
        normalized.append({"name": cut.get("name") or path.name, "duration_seconds": duration, "container_sha256": observed, "bytes": path.stat().st_size, "musical_delta": cut.get("musical_delta") or "", "reproduction_receipt_sha256": receipts})
    notes = Path(str(value.get("cut_notes_path") or "")).expanduser().absolute()
    if notes.is_symlink() or not notes.is_file():
        raise SprintError(f"{track['track_id']} CUT_NOTES required")
    return {"cuts": normalized, "cut_notes_sha256": file_digest(notes), "form_functions": sorted(functions), "musical_delta_disclosed": True, "shared_dominant_defect": False}


def status(campaign: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    root, states = workspace.expanduser().absolute(), {}
    for tid in TRACK_IDS:
        task_value = load(root / "tracks" / tid / "TRACK_TASK.json")
        require_seal(task_value, "task_sha256")
        states[tid] = task_value["initial_state"]
        result_path = root / "tracks" / tid / "PUBLIC_RESULT.json"
        if result_path.is_file():
            result = load(result_path)
            require_seal(result, "track_projection_sha256")
            states[tid] = result["state"]
    public = projection(campaign, states)
    write_json(root / "PUBLIC_PROJECTION.json", public)
    terminal = sum(s == "frontier_ready" or s.startswith("blocked_") or s == "failed" for s in states.values())
    return {"ok": True, "track_states": states, "terminal_track_count": terminal, "frontier_ready_count": sum(s == "frontier_ready" for s in states.values()), "sprint_complete": terminal == 7, "projection_sha256": public["projection_sha256"]}


def record(campaign: Mapping[str, Any], workspace: Path, result_path: Path) -> dict[str, Any]:
    root, value = workspace.expanduser().absolute(), load(result_path)
    if value.get("kind") != "earcrate_album_sprint_track_result" or value.get("contract_sha256") != campaign["contract_sha256"]:
        raise SprintError("wrong track result authority")
    tid, state = str(value.get("track_id") or ""), str(value.get("state") or "")
    track = by_track(campaign).get(tid)
    if not track or state not in STATES:
        raise SprintError("unknown track or state")
    detail: dict[str, Any] = {}
    if state == "frontier_ready":
        detail["frontier"] = validate_frontier(track, value.get("frontier") or {})
    elif state.startswith("blocked_"):
        blocker = dict(value.get("blocker") or {})
        if blocker.get("runnable_contract_ready") is not True:
            raise SprintError(f"{tid} blocker lacks runnable contract")
        missing = [str(x) for x in blocker.get("missing_binding_ids") or []]
        if state == "blocked_exact_source" and not missing:
            raise SprintError(f"{tid} exact-source blocker must name bindings")
        detail["blocker"] = {"kind": state, "missing_binding_ids": missing, "detail": blocker.get("detail") or "", "runnable_contract_ready": True}
    elif state == "failed" and not str(value.get("detail") or "").strip():
        raise SprintError(f"{tid} failure requires exact detail")
    private = root / "tracks" / tid / "TRACK_RESULT.private.json"
    write_json(private, value)
    public = sealed({
        "schema_version": 1, "kind": "earcrate_album_sprint_track_public_result",
        "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"],
        "track_id": tid, "state": state, "detail": detail,
        "private_result_sha256": file_digest(private),
        "owner_acceptance": False, "album_master": False,
        "system_reference_completed": False, "private_paths_included": False,
    }, "track_projection_sha256")
    write_json(root / "tracks" / tid / "PUBLIC_RESULT.json", public)
    status(campaign, root)
    return {"ok": True, "track_id": tid, "state": state, "track_projection_sha256": public["track_projection_sha256"]}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Album One Sprint 01")
    p.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-contract")
    x = sub.add_parser("prepare"); x.add_argument("--workspace", type=Path, required=True)
    x = sub.add_parser("dispatch"); x.add_argument("--workspace", type=Path, required=True); x.add_argument("--bindings", type=Path, required=True); x.add_argument("--verify-bytes", action="store_true")
    x = sub.add_parser("record"); x.add_argument("--workspace", type=Path, required=True); x.add_argument("--result", type=Path, required=True)
    x = sub.add_parser("status"); x.add_argument("--workspace", type=Path, required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        campaign = contract(args.contract)
        if args.command == "verify-contract":
            result = {"ok": True, "kind": campaign["kind"], "sprint_id": campaign["sprint_id"], "contract_sha256": campaign["contract_sha256"], "tracks": list(TRACK_IDS), "single_entrypoint": campaign["estate_policy"]["single_entrypoint"]}
        elif args.command == "prepare":
            result = prepare(campaign, args.workspace)
        elif args.command == "dispatch":
            result = dispatch(campaign, args.workspace, args.bindings, args.verify_bytes)
        elif args.command == "record":
            result = record(campaign, args.workspace, args.result)
        elif args.command == "status":
            result = status(campaign, args.workspace)
        else:
            raise SprintError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (SprintError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
