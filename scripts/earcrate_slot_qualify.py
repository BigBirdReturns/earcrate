#!/usr/bin/env python3
"""Repartition one fixture candidate against one observed slot-census campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from earcrate.plan.fixture_slot_qualification import qualify_fixture_candidate

_REPLACE = os.replace


def _capture_json(path: Path) -> Tuple[Mapping[str, Any], Dict[str, Any]]:
    resolved = path.expanduser().resolve()
    raw = resolved.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {resolved}")
    return value, {
        "path": str(resolved),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
        "capture_policy": "single_byte_capture_for_decode_and_digest",
    }


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


def _preflight_outputs(
    inputs: Sequence[Path], outputs: Sequence[Optional[Path]]
) -> None:
    concrete = [path for path in outputs if path is not None]
    for output in concrete:
        for input_path in inputs:
            if _same_path(output, input_path):
                raise ValueError(
                    f"receipt or candidate output aliases an input: {output}"
                )
    for index, left in enumerate(concrete):
        for right in concrete[index + 1 :]:
            if _same_path(left, right):
                raise ValueError(
                    "candidate and receipt outputs must be distinct"
                )


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _stage_bytes(path: Path, body: bytes) -> Tuple[Path, Path]:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=str(resolved.parent),
        prefix=f".{resolved.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    return resolved, temporary


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    resolved, temporary = _stage_bytes(path, _json_bytes(value))
    try:
        _REPLACE(str(temporary), str(resolved))
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_candidate_and_receipt(
    candidate_path: Path,
    candidate: Mapping[str, Any],
    receipt_path: Path,
    receipt: Mapping[str, Any],
) -> None:
    """Publish the pair or restore the candidate side to its prior bytes."""
    candidate_resolved = candidate_path.expanduser().resolve()
    previous_candidate = (
        candidate_resolved.read_bytes() if candidate_resolved.exists() else None
    )
    candidate_target, candidate_temp = _stage_bytes(
        candidate_resolved, _json_bytes(candidate)
    )
    receipt_target, receipt_temp = _stage_bytes(
        receipt_path, _json_bytes(receipt)
    )
    candidate_published = False
    try:
        _REPLACE(str(candidate_temp), str(candidate_target))
        candidate_published = True
        _REPLACE(str(receipt_temp), str(receipt_target))
    except Exception:
        if candidate_published:
            if previous_candidate is None:
                try:
                    candidate_target.unlink()
                except FileNotFoundError:
                    pass
            else:
                restore_target, restore_temp = _stage_bytes(
                    candidate_target, previous_candidate
                )
                try:
                    _REPLACE(str(restore_temp), str(restore_target))
                finally:
                    if restore_temp.exists():
                        restore_temp.unlink()
        raise
    finally:
        for temporary in (candidate_temp, receipt_temp):
            if temporary.exists():
                temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate_slot_qualify",
        description=(
            "Repartition an immutable fixture source universe against the exact "
            "slot graph observed during a refused planning attempt"
        ),
    )
    parser.add_argument(
        "candidate", type=Path, help="direct fixture candidate JSON"
    )
    parser.add_argument(
        "census", type=Path, help="fixture slot-census campaign JSON"
    )
    parser.add_argument("--out-candidate", type=Path, default=None)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=30.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.time_limit <= 0.0:
            raise ValueError("--time-limit must be positive")
        _preflight_outputs(
            [args.candidate, args.census],
            [args.out_candidate, args.receipt],
        )
        candidate, candidate_file = _capture_json(args.candidate)
        census, census_file = _capture_json(args.census)
        result = qualify_fixture_candidate(
            candidate,
            census,
            time_limit_s=float(args.time_limit),
        )
        receipt = {
            **{
                key: value
                for key, value in result.items()
                if key != "candidate"
            },
            "candidate_input": candidate_file,
            "census_input": census_file,
            "path_semantics": "operational_only_not_fixture_identity",
        }
        if bool(result.get("complete")):
            if args.out_candidate is None:
                raise ValueError(
                    "--out-candidate is required when qualification succeeds"
                )
            candidate_body = _json_bytes(result["candidate"])
            receipt["qualified_candidate_file"] = {
                "path": str(args.out_candidate.expanduser().resolve()),
                "file_sha256": hashlib.sha256(candidate_body).hexdigest(),
                "publish_policy": (
                    "candidate_and_receipt_staged_before_publish_with_candidate_rollback"
                ),
            }
            _publish_candidate_and_receipt(
                args.out_candidate,
                result["candidate"],
                args.receipt,
                receipt,
            )
        else:
            if args.out_candidate is not None:
                receipt["qualified_candidate_file"] = None
            _write_json_atomic(args.receipt, receipt)
        print(str(args.receipt.expanduser().resolve()))
        return 0 if bool(result.get("complete")) else 3
    except Exception as exc:
        print(
            f"earcrate_slot_qualify: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
