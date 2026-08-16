#!/usr/bin/env python3
from __future__ import annotations

"""Verify, stage, bind, schedule, and receipt cloud-authored EarCrate specimens."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
from typing import Any

HERE = Path(__file__).resolve().parent
BUNDLE_ROOT = HERE.parent
MODULE_PATHS = (
    BUNDLE_ROOT / "overlay" / "earcrate" / "estate" / "homelab_specimens.py",
    BUNDLE_ROOT / "earcrate" / "estate" / "homelab_specimens.py",
    Path.cwd() / "earcrate" / "estate" / "homelab_specimens.py",
)


def _load_module():
    for path in MODULE_PATHS:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("earcrate_homelab_specimens_bundle", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise SystemExit("homelab_specimens.py not found in the bundle or current EarCrate checkout")


hs = _load_module()


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _policy(path: str | None = None) -> dict[str, Any]:
    target = Path(path).expanduser().resolve() if path else BUNDLE_ROOT / "suite" / "provider_role_policy.json"
    return hs.load_json(target)


def _archives(bundle_root: Path) -> tuple[list[Path], dict[str, Path]]:
    crate_dir = bundle_root / "crates"
    archives = sorted(crate_dir.glob("*.zip"))
    if not archives:
        raise SystemExit(f"no cloud specimen archives found under {crate_dir}")
    sidecars: dict[str, Path] = {}
    for archive in archives:
        candidate = archive.with_name(archive.name + ".sha256.txt")
        if not candidate.is_file():
            raise SystemExit(f"missing archive SHA-256 sidecar: {candidate}")
        sidecars[archive.name] = candidate
    return archives, sidecars


def _load_bindings(paths: list[str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            for child in sorted(path.glob("*.json")):
                value = hs.load_json(child)
                if value.get("kind") == "earcrate_homelab_specimen_source_binding":
                    values.append(value)
        else:
            values.append(hs.load_json(path))
    return values


def command_verify(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle_root).expanduser().resolve()
    archives, sidecars = _archives(bundle)
    policy = _policy(args.policy)
    suite = hs.build_specimen_suite(archives, sidecars=sidecars, role_policy=policy, suite_id=args.suite_id)
    result = {
        "ok": True,
        "suite_sha256": suite["suite_sha256"],
        "summary": suite["summary"],
        "cases": [
            {
                "case_id": row["canonical_case_id"],
                "archive": row["archive_name"],
                "archive_sha256": row["archive_sha256"],
                "source_roles": len(row.get("source_roles") or []),
                "provider_jobs": len(row.get("provider_jobs") or []),
                "auditions": len(row.get("auditions") or []),
            }
            for row in suite["cases"]
        ],
    }
    if args.output:
        hs.write_json(args.output, suite, exclusive=not args.replace)
        result["output"] = str(Path(args.output).expanduser().absolute())
    _emit(result)
    return 0


def command_stage(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle_root).expanduser().resolve()
    archives, sidecars = _archives(bundle)
    policy = _policy(args.policy)
    suite = hs.build_specimen_suite(archives, sidecars=sidecars, role_policy=policy, suite_id=args.suite_id)
    receipt = hs.stage_specimen_suite(
        suite,
        archive_directory=bundle / "crates",
        destination=args.destination,
        role_policy=policy,
    )
    verification = hs.verify_staged_directory(args.destination)
    _emit({"ok": verification["ok"], "receipt": receipt, "verification": verification})
    return 0 if verification["ok"] else 1


def command_verify_staged(args: argparse.Namespace) -> int:
    result = hs.verify_staged_directory(args.staged)
    _emit(result)
    return 0 if result["ok"] else 1


def command_binding_template(args: argparse.Namespace) -> int:
    suite = hs.load_json(args.suite)
    hs.validate_seal(suite)
    requested_cases = sorted(set(str(value) for value in args.case if str(value).strip()))
    available_cases = {str(row.get("canonical_case_id") or "") for row in suite.get("cases") or []}
    unknown = sorted(set(requested_cases) - available_cases)
    if unknown:
        raise ValueError("unknown specimen case IDs: " + ", ".join(unknown))
    requirements = [
        dict(row) for row in suite.get("source_requirements") or []
        if not requested_cases or str(row.get("case_id") or "") in set(requested_cases)
    ]
    binding_rows = [
        {
            "case_id": row["case_id"],
            "source_id": row["source_id"],
            "artist": row.get("artist"),
            "title": row.get("title"),
            "recording_role": row.get("recording_role"),
            "identity_status": row.get("identity_status"),
            "artifact_path": "",
            "bound_by": "operator:owner",
            "reason": "exact local edition supplied for cloud specimen campaign",
            "canonical_pcm_sha256": None,
        }
        for row in requirements
    ]
    template = {
        "schema": "earcrate.homelab_specimen_source_binding_manifest.v1",
        "suite_sha256": suite["suite_sha256"],
        "selected_case_ids": requested_cases or sorted(available_cases),
        "bindings": binding_rows,
        "commands": [
            {
                "case_id": row["case_id"],
                "source_id": row["source_id"],
                "output": f"bindings/{row['case_id']}--{row['source_id']}.binding.json",
                "command": (
                    "python scripts/earcrate_cloud_intake.py bind-source "
                    f"--suite <suite.json> --case-id {json.dumps(row['case_id'])} "
                    f"--source-id {json.dumps(row['source_id'])} --artifact <exact-local-file> "
                    "--bound-by operator:owner --reason \"exact local edition supplied\" "
                    f"--output bindings/{row['case_id']}--{row['source_id']}.binding.json --canonical-pcm"
                ),
            }
            for row in requirements
        ],
    }
    hs.write_json(args.output, template, exclusive=not args.replace)
    _emit({"ok": True, "output": str(Path(args.output).expanduser().absolute()), "bindings": len(template["bindings"])})
    return 0


def command_bind_source(args: argparse.Namespace) -> int:
    suite = hs.load_json(args.suite)
    binding = hs.bind_specimen_source(
        suite,
        case_id=args.case_id,
        source_id=args.source_id,
        artifact_path=args.artifact,
        bound_by=args.bound_by,
        reason=args.reason,
        canonical_pcm=args.canonical_pcm,
        ffmpeg=args.ffmpeg,
    )
    hs.write_json(args.output, binding, exclusive=not args.replace)
    _emit(
        {
            "ok": True,
            "binding_sha256": binding["binding_sha256"],
            "case_id": binding["case_id"],
            "source_id": binding["source_id"],
            "artifact_sha256": binding["artifact_sha256"],
            "canonical_pcm_sha256": binding.get("canonical_pcm_sha256"),
            "output": str(Path(args.output).expanduser().absolute()),
            "visibility": "sensitive",
        }
    )
    return 0



def command_bind_manifest(args: argparse.Namespace) -> int:
    suite = hs.load_json(args.suite)
    manifest = hs.load_json(args.manifest)
    rows = manifest.get("bindings")
    if not isinstance(rows, list) or not rows:
        raise ValueError("binding manifest requires a nonempty bindings array")
    requested_cases = sorted(set(str(value) for value in args.case if str(value).strip()))
    if requested_cases:
        rows = [row for row in rows if isinstance(row, dict) and str(row.get("case_id") or "") in set(requested_cases)]
        if not rows:
            raise ValueError("binding manifest contains no rows for the selected specimen cases")
    output_dir = Path(args.output_dir).expanduser().absolute()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"binding output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every binding manifest row must be an object")
        artifact = str(row.get("artifact_path") or "").strip()
        if not artifact:
            raise ValueError(f"artifact_path is empty for {row.get('case_id')}/{row.get('source_id')}")
        binding = hs.bind_specimen_source(
            suite,
            case_id=str(row.get("case_id") or ""),
            source_id=str(row.get("source_id") or ""),
            artifact_path=artifact,
            bound_by=str(row.get("bound_by") or args.bound_by),
            reason=str(row.get("reason") or args.reason),
            canonical_pcm=args.canonical_pcm,
            ffmpeg=args.ffmpeg,
        )
        name = f"{binding['case_id']}--{binding['source_id']}.binding.json"
        target = output_dir / name
        hs.write_json(target, binding)
        results.append({
            "case_id": binding["case_id"],
            "source_id": binding["source_id"],
            "binding_sha256": binding["binding_sha256"],
            "artifact_sha256": binding["artifact_sha256"],
            "output": str(target),
        })
    _emit({"ok": True, "bindings": results, "output_dir": str(output_dir), "visibility": "sensitive"})
    return 0

def command_compile(args: argparse.Namespace) -> int:
    suite = hs.load_json(args.suite)
    catalog = hs.load_json(args.catalog)
    audit = hs.load_json(args.audit)
    bindings = _load_bindings(args.binding)
    policy = _policy(args.policy)
    campaign = hs.compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=bindings,
        policy=policy,
        profile=args.profile,
        case_ids=args.case,
    )
    hs.write_json(args.output, campaign, exclusive=not args.replace)
    _emit(
        {
            "ok": True,
            "campaign_sha256": campaign["campaign_sha256"],
            "summary": campaign["summary"],
            "completion_gate": campaign["completion_gate"],
            "selected_case_ids": campaign.get("selected_case_ids") or [],
            "trial_readiness_summary": campaign.get("trial_readiness_summary") or {},
            "output": str(Path(args.output).expanduser().absolute()),
        }
    )
    return 0


def _parse_measurements(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if args.measurements_json:
        value = json.loads(args.measurements_json)
        if not isinstance(value, dict):
            raise SystemExit("--measurements-json must decode to an object")
        result.update(value)
    if args.measurements_file:
        value = hs.load_json(args.measurements_file)
        result.update(value)
    return result


def command_record_trial(args: argparse.Namespace) -> int:
    suite = hs.load_json(args.suite)
    campaign = hs.load_json(args.campaign)
    bindings = _load_bindings(args.binding)
    receipt = hs.record_specimen_trial(
        suite,
        campaign,
        task_id=args.task_id,
        node_sha256=args.node_sha256,
        outcome=args.outcome,
        actor_id=args.actor_id,
        actor_type=args.actor_type,
        artifacts=args.artifact,
        source_bindings=bindings,
        measurements=_parse_measurements(args),
        notes=args.note,
        candidate_sha256=args.candidate_sha256,
        control_sha256=args.control_sha256,
        human_verdict=args.human_verdict,
    )
    hs.write_json(args.output, receipt, exclusive=not args.replace)
    _emit(
        {
            "ok": True,
            "receipt_sha256": receipt["receipt_sha256"],
            "task_id": receipt["task_id"],
            "outcome": receipt["outcome"],
            "derived_artifacts": len(receipt["derived_artifacts"]),
            "output": str(Path(args.output).expanduser().absolute()),
        }
    )
    return 0



def command_complete_store_task(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "earcrate" / "estate" / "homelab_common.py").is_file():
        raise ValueError(f"EarCrate checkout not found: {repo}")
    campaign = hs.load_json(args.campaign)
    campaign_sha = hs.validate_seal(campaign)
    receipt = hs.load_json(args.receipt)
    receipt_sha = hs.validate_seal(receipt)
    if receipt.get("kind") != "earcrate_homelab_specimen_trial_receipt":
        raise ValueError("store completion requires a specimen trial receipt")
    if receipt.get("campaign_sha256") != campaign_sha or receipt.get("task_id") != args.task_id:
        raise ValueError("receipt does not match the selected campaign task")
    task = next((row for row in campaign.get("tasks") or [] if row.get("task_id") == args.task_id), None)
    if task is None:
        raise ValueError("task is not present in the campaign object")
    expected = set(str(value) for value in task.get("required_output_kinds") or [])
    if expected and receipt.get("kind") not in expected:
        raise ValueError("receipt kind does not satisfy the task evidence contract")
    commands = [
        [sys.executable, "-m", "earcrate", "homelab", "store-ingest", args.store, str(Path(args.receipt).resolve()), "--visibility", args.visibility],
        [sys.executable, "-m", "earcrate", "homelab", "task-complete", args.store, campaign_sha, args.task_id, str(Path(args.lease_token_file).resolve()), "completed", "--evidence", receipt_sha],
        [sys.executable, "-m", "earcrate", "homelab", "store-doctor", args.store],
    ]
    results = []
    for command in commands:
        process = subprocess.run(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        results.append({
            "argv": command,
            "returncode": process.returncode,
            "stdout": process.stdout[-8000:],
            "stderr": process.stderr[-8000:],
        })
        if process.returncode != 0:
            raise RuntimeError(f"store command failed: {command}: {process.stderr[-2000:]}")
    _emit({"ok": True, "campaign_sha256": campaign_sha, "task_id": args.task_id, "receipt_sha256": receipt_sha, "commands": results})
    return 0

def command_validate_object(args: argparse.Namespace) -> int:
    value = hs.load_json(args.object)
    identity = hs.validate_seal(value)
    _emit({"ok": True, "kind": value["kind"], "identity": identity, "object": str(Path(args.object).resolve())})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify-suite", help="verify every archive and emit a deterministic sealed suite")
    p.add_argument("--bundle-root", default=str(BUNDLE_ROOT))
    p.add_argument("--policy")
    p.add_argument("--suite-id", default="earcrate-cloud-organ-transplant-suite-v1")
    p.add_argument("--output")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=command_verify)

    p = sub.add_parser("stage", help="atomically extract verified source-free cases into a managed local estate directory")
    p.add_argument("--bundle-root", default=str(BUNDLE_ROOT))
    p.add_argument("--policy")
    p.add_argument("--suite-id", default="earcrate-cloud-organ-transplant-suite-v1")
    p.add_argument("--destination", required=True)
    p.set_defaults(func=command_stage)

    p = sub.add_parser("verify-staged", help="rehash a staged specimen suite")
    p.add_argument("--staged", required=True)
    p.set_defaults(func=command_verify_staged)

    p = sub.add_parser("binding-template", help="write source-binding commands for all or selected specimen cases")
    p.add_argument("--suite", required=True)
    p.add_argument("--case", action="append", default=[], help="canonical case ID; repeatable")
    p.add_argument("--output", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=command_binding_template)

    p = sub.add_parser("bind-source", help="bind exact local recording bytes without copying them")
    p.add_argument("--suite", required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--bound-by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--canonical-pcm", action="store_true")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--output", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=command_bind_source)


    p = sub.add_parser("bind-manifest", help="bind exact local sources named by a completed private manifest")
    p.add_argument("--suite", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--case", action="append", default=[], help="canonical case ID; repeatable")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--bound-by", default="operator:owner")
    p.add_argument("--reason", default="exact local edition supplied for cloud specimen campaign")
    p.add_argument("--canonical-pcm", action="store_true")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.set_defaults(func=command_bind_manifest)

    p = sub.add_parser("compile-campaign", help="join the suite to the authoritative local catalog/audit and compile a bounded tournament")
    p.add_argument("--suite", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--audit", required=True)
    p.add_argument("--binding", action="append", default=[], help="binding JSON or directory; repeatable")
    p.add_argument("--policy")
    p.add_argument("--profile", choices=["smoke", "core", "full"], default="core")
    p.add_argument("--case", action="append", default=[], help="canonical case ID; repeatable")
    p.add_argument("--output", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=command_compile)

    p = sub.add_parser("record-trial", help="seal one specimen execution, review, or assessment receipt")
    p.add_argument("--suite", required=True)
    p.add_argument("--campaign", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--node-sha256")
    p.add_argument("--outcome", choices=["passed", "failed", "refused", "accept", "reject", "revise", "abstain", "observed"], required=True)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--actor-type", choices=["machine", "human", "operator", "authority"], required=True)
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--binding", action="append", default=[])
    p.add_argument("--measurements-json")
    p.add_argument("--measurements-file")
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--candidate-sha256")
    p.add_argument("--control-sha256")
    p.add_argument("--human-verdict", choices=["accept", "reject", "revise", "abstain"])
    p.add_argument("--output", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=command_record_trial)


    p = sub.add_parser("complete-store-task", help="validate, ingest, and attach one trial receipt to a live leased Homelab task")
    p.add_argument("--repo", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--campaign", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--lease-token-file", required=True)
    p.add_argument("--receipt", required=True)
    p.add_argument("--visibility", choices=["public", "private", "sensitive"], default="private")
    p.set_defaults(func=command_complete_store_task)

    p = sub.add_parser("validate-object", help="verify one suite, binding, campaign, intake, or trial object seal")
    p.add_argument("object")
    p.set_defaults(func=command_validate_object)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, FileExistsError, KeyError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
