#!/usr/bin/env python3
"""Select a maximum or exact source universe from one slot-census campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from earcrate.plan.fixture_slot_qualification import (
    SOURCE_UNIVERSE_SELECTION_VERSION,
    select_planable_source_universe,
)

_REPLACE = os.replace
_PUBLICATION_CONTRACT = "receipt_atomic_commit_candidate_materialization_v1"


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
                    "selected candidate and receipt outputs must be distinct"
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


def _fsync_parent(path: Path) -> None:
    try:
        descriptor = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_bytes_atomic(path: Path, body: bytes) -> None:
    resolved, temporary = _stage_bytes(path, body)
    try:
        _REPLACE(str(temporary), str(resolved))
        _fsync_parent(resolved)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _write_bytes_atomic(path, _json_bytes(value))


_MATERIALIZE_CANDIDATE = _write_bytes_atomic


def _read_receipt(path: Path) -> Optional[Mapping[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return None
    try:
        value = json.loads(resolved.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _recovery_candidate_bytes(
    receipt: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    census: Mapping[str, Any],
    candidate_file: Mapping[str, Any],
    census_file: Mapping[str, Any],
    target_source_count: Optional[int],
    time_limit_s: float,
    output_path: Path,
) -> Optional[bytes]:
    """Validate one committed receipt against the exact current invocation."""
    if receipt.get("complete") is not True:
        return None
    if str(receipt.get("kind") or "") != (
        "earcrate_fixture_source_universe_selection_receipt"
    ):
        return None
    if str(receipt.get("version") or "") != SOURCE_UNIVERSE_SELECTION_VERSION:
        return None

    publication = receipt.get("publication")
    if not isinstance(publication, Mapping):
        return None
    if publication.get("contract") != _PUBLICATION_CONTRACT:
        return None
    if publication.get("authority") != "receipt":
        return None
    if publication.get("candidate_role") != "materialized_cache":
        return None

    if dict(receipt.get("candidate_input") or {}) != dict(candidate_file):
        return None
    if dict(receipt.get("census_input") or {}) != dict(census_file):
        return None
    if dict(receipt.get("request") or {}) != {
        "target_source_count": target_source_count,
        "time_limit_s": float(time_limit_s),
    }:
        return None

    selected = receipt.get("selected_candidate")
    selected_file = receipt.get("selected_candidate_file")
    if not isinstance(selected, Mapping) or not isinstance(
        selected_file, Mapping
    ):
        return None
    if str(selected_file.get("path") or "") != str(
        output_path.expanduser().resolve()
    ):
        return None

    selected_bytes = _json_bytes(selected)
    selected_sha = hashlib.sha256(selected_bytes).hexdigest()
    if str(selected_file.get("file_sha256") or "") != selected_sha:
        return None
    if int(selected_file.get("byte_count") or -1) != len(selected_bytes):
        return None

    selected_identity = str(selected.get("fixture_sha256") or "")
    if not selected_identity:
        return None
    if str(receipt.get("selected_fixture_identity") or "") != selected_identity:
        return None
    if str(selected_file.get("fixture_identity") or "") != selected_identity:
        return None

    from earcrate.plan.fixture_diversity import fixture_projection

    try:
        projected_identity = str(
            fixture_projection(selected)["fixture_identity"]
        )
    except Exception:
        return None
    if projected_identity != selected_identity:
        return None

    current_parent = str(
        candidate.get("fixture_sha256")
        or candidate.get("fixture_id")
        or ""
    )
    if not current_parent:
        return None
    if str(receipt.get("parent_fixture_identity") or "") != current_parent:
        return None

    selection = selected.get("fixture_source_universe_selection")
    if not isinstance(selection, Mapping):
        return None
    if str(selection.get("parent_fixture_identity") or "") != current_parent:
        return None
    if str(selection.get("census_campaign_sha256") or "") != str(
        census.get("campaign_sha256") or ""
    ):
        return None
    if int(selection.get("selected_source_count") or -1) != int(
        receipt.get("selected_source_count") or -2
    ):
        return None
    if int(selection.get("maximum_planable_source_count") or -1) != int(
        receipt.get("maximum_planable_source_count") or -2
    ):
        return None

    return selected_bytes


def _recover_committed_candidate(
    receipt_path: Path,
    *,
    candidate: Mapping[str, Any],
    census: Mapping[str, Any],
    candidate_file: Mapping[str, Any],
    census_file: Mapping[str, Any],
    target_source_count: Optional[int],
    time_limit_s: float,
    output_path: Optional[Path],
) -> bool:
    if output_path is None:
        return False
    receipt = _read_receipt(receipt_path)
    if receipt is None:
        return False
    selected_bytes = _recovery_candidate_bytes(
        receipt,
        candidate=candidate,
        census=census,
        candidate_file=candidate_file,
        census_file=census_file,
        target_source_count=target_source_count,
        time_limit_s=time_limit_s,
        output_path=output_path,
    )
    if selected_bytes is None:
        return False

    resolved = output_path.expanduser().resolve()
    current = resolved.read_bytes() if resolved.is_file() else None
    if current != selected_bytes:
        _MATERIALIZE_CANDIDATE(resolved, selected_bytes)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate_source_universe",
        description=(
            "Select the largest or an exact common source universe against one "
            "refusal-attached diagnostic slot census"
        ),
    )
    parser.add_argument(
        "candidate", type=Path, help="sealed direct fixture candidate JSON"
    )
    parser.add_argument(
        "census", type=Path, help="fresh policy-bound slot-census campaign JSON"
    )
    parser.add_argument(
        "--target-source-count",
        type=int,
        default=None,
        help="optional exact selected-source count after phase-one certification",
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
        if (
            args.target_source_count is not None
            and args.target_source_count <= 0
        ):
            raise ValueError("--target-source-count must be positive")
        _preflight_outputs(
            [args.candidate, args.census],
            [args.out_candidate, args.receipt],
        )
        candidate, candidate_file = _capture_json(args.candidate)
        census, census_file = _capture_json(args.census)

        if _recover_committed_candidate(
            args.receipt,
            candidate=candidate,
            census=census,
            candidate_file=candidate_file,
            census_file=census_file,
            target_source_count=args.target_source_count,
            time_limit_s=float(args.time_limit),
            output_path=args.out_candidate,
        ):
            print(str(args.receipt.expanduser().resolve()))
            return 0

        result = select_planable_source_universe(
            candidate,
            census,
            target_source_count=args.target_source_count,
            time_limit_s=float(args.time_limit),
        )
        receipt: Dict[str, Any] = {
            **{
                key: value
                for key, value in result.items()
                if key != "candidate"
            },
            "request": {
                "target_source_count": args.target_source_count,
                "time_limit_s": float(args.time_limit),
            },
            "candidate_input": candidate_file,
            "census_input": census_file,
            "path_semantics": "operational_only_not_fixture_identity",
        }
        if bool(result.get("complete")):
            if args.out_candidate is None:
                raise ValueError(
                    "--out-candidate is required when source-universe selection succeeds"
                )
            selected = result.get("candidate")
            if not isinstance(selected, Mapping):
                raise ValueError(
                    "complete source-universe selection has no candidate object"
                )
            selected_candidate = dict(selected)
            candidate_body = _json_bytes(selected_candidate)
            selected_identity = str(
                result.get("selected_fixture_identity")
                or selected_candidate.get("fixture_sha256")
                or ""
            )
            if not selected_identity:
                raise ValueError(
                    "complete source-universe selection has no fixture identity"
                )
            receipt["selected_candidate"] = selected_candidate
            receipt["selected_candidate_file"] = {
                "path": str(args.out_candidate.expanduser().resolve()),
                "file_sha256": hashlib.sha256(candidate_body).hexdigest(),
                "byte_count": len(candidate_body),
                "fixture_identity": selected_identity,
                "cache_role": "materialized_from_committed_receipt",
            }
            receipt["publication"] = {
                "contract": _PUBLICATION_CONTRACT,
                "authority": "receipt",
                "commit_order": [
                    "receipt_atomic_replace",
                    "candidate_cache_materialization",
                ],
                "candidate_role": "materialized_cache",
                "recovery": (
                    "exact_invocation_rehydrates_candidate_without_solver"
                ),
            }

            # One authoritative commit boundary. A process interruption after
            # this replace leaves a complete receipt from which the cache can
            # be deterministically rehydrated by the next exact invocation.
            _write_json_atomic(args.receipt, receipt)
            _MATERIALIZE_CANDIDATE(args.out_candidate, candidate_body)
        else:
            if args.out_candidate is not None:
                receipt["selected_candidate_file"] = None
            _write_json_atomic(args.receipt, receipt)
        print(str(args.receipt.expanduser().resolve()))
        return 0 if bool(result.get("complete")) else 3
    except Exception as exc:
        print(
            f"earcrate_source_universe: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
