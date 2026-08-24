#!/usr/bin/env python3
"""Probe ordinary island slots and derive a slot-qualified fixture partition."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from earcrate.plan.fixture_slot_qualification import (
    INDETERMINATE_ACTION,
    probe_candidate_slot_census,
    qualify_fixture_candidate,
)


def _capture_json(path: Path) -> Tuple[Mapping[str, Any], bytes, str, Path]:
    resolved = path.expanduser().resolve()
    body = resolved.read_bytes()
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {resolved}")
    return value, body, hashlib.sha256(body).hexdigest(), resolved


def _same_path(left: Path, right: Path) -> bool:
    a = left.expanduser().resolve()
    b = right.expanduser().resolve()
    if a == b:
        return True
    if a.exists() and b.exists():
        try:
            return os.path.samefile(a, b)
        except OSError:
            return False
    return False


def _refuse_alias(output: Optional[Path], inputs: Sequence[Path]) -> None:
    if output is None:
        return
    for path in inputs:
        if _same_path(output, path):
            raise ValueError(f"output path aliases an input: {output}")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def census_receipt(candidate_path: Path) -> Dict[str, Any]:
    candidate, _bytes, file_sha, resolved = _capture_json(candidate_path)
    from earcrate.app import EarcrateCore

    result = dict(probe_candidate_slot_census(EarcrateCore(), candidate))
    result["candidate_file"] = {
        "path": str(resolved),
        "file_sha256": file_sha,
    }
    return result


def qualification_receipt(
    matrix_path: Path,
    candidate_path: Path,
    census_path: Path,
    *,
    max_source_events: int,
    max_anchor_rounds: int,
) -> Dict[str, Any]:
    matrix, _matrix_bytes, matrix_sha, matrix_resolved = _capture_json(matrix_path)
    candidate, _candidate_bytes, candidate_sha, candidate_resolved = _capture_json(candidate_path)
    census, _census_bytes, census_sha, census_resolved = _capture_json(census_path)
    result = dict(
        qualify_fixture_candidate(
            matrix,
            candidate,
            census,
            max_source_events=max_source_events,
            max_anchor_rounds=max_anchor_rounds,
        )
    )
    result["input_files"] = {
        "survival_matrix": {"path": str(matrix_resolved), "file_sha256": matrix_sha},
        "candidate": {"path": str(candidate_resolved), "file_sha256": candidate_sha},
        "slot_census": {"path": str(census_resolved), "file_sha256": census_sha},
    }
    result["path_semantics"] = "operational_only_not_fixture_or_slot_identity"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate_fixture_slots",
        description="Probe ordinary role slots and repartition a fixture against them",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    census = subparsers.add_parser(
        "census", help="probe every island through the ordinary composer without publishing"
    )
    census.add_argument("candidate", type=Path)
    census.add_argument("--out", type=Path, required=True)

    qualify = subparsers.add_parser(
        "qualify", help="derive one source partition against a captured slot census"
    )
    qualify.add_argument("matrix", type=Path)
    qualify.add_argument("candidate", type=Path)
    qualify.add_argument("census", type=Path)
    qualify.add_argument("--candidate-out", type=Path, required=True)
    qualify.add_argument("--receipt", type=Path, required=True)
    qualify.add_argument("--max-source-events", type=int, default=12)
    qualify.add_argument("--max-anchor-rounds", type=int, default=128)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "census":
            _refuse_alias(args.out, [args.candidate])
            result = census_receipt(args.candidate)
            _write_json_atomic(args.out, result)
            print(str(args.out.expanduser().resolve()))
            return 0
        if args.command == "qualify":
            inputs = [args.matrix, args.candidate, args.census]
            _refuse_alias(args.candidate_out, inputs)
            _refuse_alias(args.receipt, [*inputs, args.candidate_out])
            if int(args.max_source_events) <= 0:
                raise ValueError("--max-source-events must be positive")
            if int(args.max_anchor_rounds) < 0:
                raise ValueError("--max-anchor-rounds cannot be negative")
            result = qualification_receipt(
                args.matrix,
                args.candidate,
                args.census,
                max_source_events=int(args.max_source_events),
                max_anchor_rounds=int(args.max_anchor_rounds),
            )
            _write_json_atomic(args.receipt, result)
            if result.get("complete"):
                qualified = result.get("qualified_candidate")
                if not isinstance(qualified, Mapping):
                    raise RuntimeError("complete qualification has no candidate")
                _write_json_atomic(args.candidate_out, qualified)
                print(str(args.candidate_out.expanduser().resolve()))
                print(str(args.receipt.expanduser().resolve()))
                return 0
            print(str(args.receipt.expanduser().resolve()))
            if result.get("impossibility_claimed"):
                return 3
            if result.get("private_acceptance") == INDETERMINATE_ACTION:
                return 4
            return 2
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"earcrate_fixture_slots: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
