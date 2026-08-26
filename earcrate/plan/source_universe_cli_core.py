#!/usr/bin/env python3
"""Select a maximum or exact source universe from one slot-census campaign."""
from __future__ import annotations

import argparse
from collections import Counter
import errno
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
    semantic_sha256,
)

_REPLACE = os.replace
_PUBLICATION_CONTRACT = "receipt_atomic_commit_candidate_materialization_v1"

_UNSUPPORTED_DIRECTORY_SYNC_ERRNOS = {
    errno.EINVAL,
    errno.ENOSYS,
}
for _name in ("ENOTSUP", "EOPNOTSUPP"):
    _value = getattr(errno, _name, None)
    if _value is not None:
        _UNSUPPORTED_DIRECTORY_SYNC_ERRNOS.add(_value)
_UNSUPPORTED_WINDOWS_DIRECTORY_SYNC_WINERRORS = {
    1,
    5,
    50,
    87,
}


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


def _directory_sync_unsupported(error: OSError) -> bool:
    if error.errno in _UNSUPPORTED_DIRECTORY_SYNC_ERRNOS:
        return True
    if os.name == "nt":
        if getattr(error, "winerror", None) in (
            _UNSUPPORTED_WINDOWS_DIRECTORY_SYNC_WINERRORS
        ):
            return True
        if error.errno in {errno.EACCES, errno.EPERM}:
            return True
    return False


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(path.parent), flags)
    except OSError as error:
        if _directory_sync_unsupported(error):
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if _directory_sync_unsupported(error):
                return
            raise
    finally:
        os.close(descriptor)


def _restore_visible_bytes(path: Path, previous: Optional[bytes]) -> None:
    """Best-effort visible-state rollback after a failed directory sync."""
    resolved = path.expanduser().resolve()
    if previous is None:
        try:
            resolved.unlink()
        except FileNotFoundError:
            pass
        return
    restore_target, restore_temp = _stage_bytes(resolved, previous)
    try:
        _REPLACE(str(restore_temp), str(restore_target))
    finally:
        if restore_temp.exists():
            restore_temp.unlink()


def _write_bytes_atomic(path: Path, body: bytes) -> None:
    resolved = path.expanduser().resolve()
    previous = resolved.read_bytes() if resolved.is_file() else None
    resolved, temporary = _stage_bytes(resolved, body)
    replaced = False
    try:
        _REPLACE(str(temporary), str(resolved))
        replaced = True
        _fsync_parent(resolved)
    except BaseException:
        if replaced:
            _restore_visible_bytes(resolved, previous)
        raise
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


def _source_ids(candidate: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(source_id)
            for island in candidate.get("islands") or []
            if isinstance(island, Mapping)
            for source_id in island.get("source_include_ids") or []
        }
    )


def _require_json_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ValueError(
            f"committed source-universe receipt has non-integer {field}"
        )
    return value


def _recovery_evidence_projection(
    receipt: Mapping[str, Any],
) -> Dict[str, Any]:
    publication = dict(receipt.get("publication") or {})
    publication.pop("recovery_evidence_sha256", None)
    return {
        "kind": receipt.get("kind"),
        "version": receipt.get("version"),
        "complete": receipt.get("complete"),
        "impossibility_claimed": receipt.get("impossibility_claimed"),
        "parent_fixture_identity": receipt.get("parent_fixture_identity"),
        "selected_fixture_identity": receipt.get("selected_fixture_identity"),
        "parent_source_count": receipt.get("parent_source_count"),
        "maximum_planable_source_count": receipt.get(
            "maximum_planable_source_count"
        ),
        "selected_source_count": receipt.get("selected_source_count"),
        "dropped_source_count": receipt.get("dropped_source_count"),
        "dropped_source_ids": receipt.get("dropped_source_ids"),
        "selected_source_universe_sha256": receipt.get(
            "selected_source_universe_sha256"
        ),
        "census_campaign_sha256": receipt.get("census_campaign_sha256"),
        "slot_assignment": receipt.get("slot_assignment"),
        "slot_assignment_sha256": receipt.get("slot_assignment_sha256"),
        "solver": receipt.get("solver"),
        "request": receipt.get("request"),
        "candidate_input": receipt.get("candidate_input"),
        "census_input": receipt.get("census_input"),
        "selected_candidate": receipt.get("selected_candidate"),
        "selected_candidate_file": receipt.get("selected_candidate_file"),
        "publication": publication,
    }


def _receipt_contradiction(reason: str) -> ValueError:
    return ValueError(
        f"committed source-universe receipt contradiction: {reason}"
    )


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

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise _receipt_contradiction(reason)

    require(
        str(receipt.get("kind") or "")
        == "earcrate_fixture_source_universe_selection_receipt",
        "kind",
    )
    require(
        str(receipt.get("version") or "")
        == SOURCE_UNIVERSE_SELECTION_VERSION,
        "version",
    )
    require(receipt.get("impossibility_claimed") is False, "impossibility flag")

    publication = receipt.get("publication")
    require(isinstance(publication, Mapping), "publication object")
    assert isinstance(publication, Mapping)
    require(
        publication.get("contract") == _PUBLICATION_CONTRACT,
        "publication contract",
    )
    require(publication.get("authority") == "receipt", "publication authority")
    require(
        publication.get("candidate_role") == "materialized_cache",
        "candidate cache role",
    )

    require(
        dict(receipt.get("candidate_input") or {}) == dict(candidate_file),
        "candidate input capture",
    )
    require(
        dict(receipt.get("census_input") or {}) == dict(census_file),
        "census input capture",
    )
    require(
        dict(receipt.get("request") or {})
        == {
            "target_source_count": target_source_count,
            "time_limit_s": float(time_limit_s),
        },
        "request",
    )

    selected = receipt.get("selected_candidate")
    selected_file = receipt.get("selected_candidate_file")
    require(isinstance(selected, Mapping), "embedded selected candidate")
    require(isinstance(selected_file, Mapping), "selected candidate file record")
    assert isinstance(selected, Mapping)
    assert isinstance(selected_file, Mapping)
    require(
        str(selected_file.get("path") or "")
        == str(output_path.expanduser().resolve()),
        "candidate cache path",
    )

    selected_bytes = _json_bytes(selected)
    selected_sha = hashlib.sha256(selected_bytes).hexdigest()
    require(
        str(selected_file.get("file_sha256") or "") == selected_sha,
        "candidate byte digest",
    )
    require(
        _require_json_int(selected_file.get("byte_count"), "candidate byte count")
        == len(selected_bytes),
        "candidate byte count",
    )

    selected_identity = str(selected.get("fixture_sha256") or "")
    require(bool(selected_identity), "selected fixture identity")
    require(
        str(receipt.get("selected_fixture_identity") or "")
        == selected_identity,
        "top-level selected fixture identity",
    )
    require(
        str(selected_file.get("fixture_identity") or "")
        == selected_identity,
        "candidate-file fixture identity",
    )

    from earcrate.plan.fixture_diversity import fixture_projection

    try:
        projected_identity = str(
            fixture_projection(selected)["fixture_identity"]
        )
    except Exception as error:
        raise _receipt_contradiction(
            f"selected candidate projection: {type(error).__name__}"
        ) from error
    require(
        projected_identity == selected_identity,
        "selected candidate semantic identity",
    )

    current_parent = str(
        candidate.get("fixture_sha256")
        or candidate.get("fixture_id")
        or ""
    )
    require(bool(current_parent), "current parent fixture identity")
    require(
        str(receipt.get("parent_fixture_identity") or "") == current_parent,
        "top-level parent fixture identity",
    )

    selection = selected.get("fixture_source_universe_selection")
    require(isinstance(selection, Mapping), "embedded selection ledger")
    assert isinstance(selection, Mapping)
    require(
        str(selection.get("parent_fixture_identity") or "")
        == current_parent,
        "selection parent fixture identity",
    )

    current_census_identity = str(census.get("campaign_sha256") or "")
    require(bool(current_census_identity), "current census identity")
    require(
        str(selection.get("census_campaign_sha256") or "")
        == current_census_identity,
        "selection census identity",
    )
    require(
        str(receipt.get("census_campaign_sha256") or "")
        == current_census_identity,
        "top-level census identity",
    )

    parent_ids = _source_ids(candidate)
    selected_ids = _source_ids(selected)
    require(bool(parent_ids), "parent source universe")
    require(bool(selected_ids), "selected source universe")
    require(
        set(selected_ids).issubset(parent_ids),
        "selected source outside parent universe",
    )
    dropped_ids = sorted(set(parent_ids) - set(selected_ids))

    parent_count = len(parent_ids)
    selected_count = len(selected_ids)
    dropped_count = len(dropped_ids)
    selection_parent_count = _require_json_int(
        selection.get("parent_source_count"), "selection parent source count"
    )
    top_parent_count = _require_json_int(
        receipt.get("parent_source_count"), "top-level parent source count"
    )
    require(
        selection_parent_count == parent_count
        and top_parent_count == parent_count,
        "parent source count",
    )

    maximum_count = _require_json_int(
        selection.get("maximum_planable_source_count"),
        "selection maximum source count",
    )
    require(
        _require_json_int(
            receipt.get("maximum_planable_source_count"),
            "top-level maximum source count",
        )
        == maximum_count,
        "maximum source count",
    )
    require(
        selected_count <= maximum_count <= parent_count,
        "maximum source count bounds",
    )
    require(
        _require_json_int(
            selection.get("selected_source_count"),
            "selection selected source count",
        )
        == selected_count
        and _require_json_int(
            receipt.get("selected_source_count"),
            "top-level selected source count",
        )
        == selected_count,
        "selected source count",
    )
    require(
        _require_json_int(
            selection.get("dropped_source_count"),
            "selection dropped source count",
        )
        == dropped_count
        and _require_json_int(
            receipt.get("dropped_source_count"),
            "top-level dropped source count",
        )
        == dropped_count,
        "dropped source count",
    )
    require(
        list(selection.get("dropped_source_ids") or []) == dropped_ids
        and list(receipt.get("dropped_source_ids") or []) == dropped_ids,
        "dropped source identities",
    )

    parent_universe_sha = semantic_sha256(parent_ids)
    selected_universe_sha = semantic_sha256(selected_ids)
    require(
        str(selection.get("parent_source_universe_sha256") or "")
        == parent_universe_sha,
        "parent source-universe digest",
    )
    require(
        str(selection.get("selected_source_universe_sha256") or "")
        == selected_universe_sha,
        "selection source-universe digest",
    )
    require(
        str(receipt.get("selected_source_universe_sha256") or "")
        == selected_universe_sha,
        "top-level source-universe digest",
    )

    assignment = receipt.get("slot_assignment")
    require(isinstance(assignment, list), "slot assignment")
    assert isinstance(assignment, list)
    canonical_assignment: list[Dict[str, Any]] = []
    slot_keys: set[tuple[str, int, int]] = set()
    represented_sources: set[str] = set()
    event_counts: Counter[tuple[str, str]] = Counter()
    selected_id_set = set(selected_ids)
    for row in assignment:
        require(isinstance(row, Mapping), "slot assignment row")
        assert isinstance(row, Mapping)
        source_id = str(row.get("source_id") or "")
        island_id = str(row.get("island_id") or "")
        require(source_id in selected_id_set, "slot source outside selected universe")
        require(bool(island_id), "slot island identity")
        bar_start = _require_json_int(row.get("bar_start"), "slot bar_start")
        layer_index = _require_json_int(
            row.get("layer_index"), "slot layer_index"
        )
        slot_key = (island_id, bar_start, layer_index)
        require(slot_key not in slot_keys, "duplicate slot assignment")
        slot_keys.add(slot_key)
        represented_sources.add(source_id)
        event_counts[(island_id, source_id)] += 1
        canonical_assignment.append(
            {
                "island_id": island_id,
                "bar_start": bar_start,
                "layer_index": layer_index,
                "source_id": source_id,
            }
        )
    require(
        represented_sources == selected_id_set,
        "selected source missing from slot assignment",
    )
    max_events = _require_json_int(
        selection.get("max_source_events"), "selection event cap"
    )
    require(max_events > 0, "selection event cap")
    require(
        all(count <= max_events for count in event_counts.values()),
        "slot assignment exceeds event cap",
    )
    assignment_sha = semantic_sha256(canonical_assignment)
    require(
        str(selection.get("slot_assignment_sha256") or "")
        == assignment_sha,
        "selection slot-assignment digest",
    )
    require(
        str(receipt.get("slot_assignment_sha256") or "")
        == assignment_sha,
        "top-level slot-assignment digest",
    )

    top_solver = receipt.get("solver")
    selection_solver = selection.get("solver")
    require(isinstance(top_solver, Mapping), "top-level solver receipt")
    require(isinstance(selection_solver, Mapping), "selection solver receipt")
    assert isinstance(top_solver, Mapping)
    assert isinstance(selection_solver, Mapping)
    require(dict(top_solver) == dict(selection_solver), "solver receipt equality")
    for phase_name, expected_count in (
        ("phase_one", maximum_count),
        ("phase_two", selected_count),
    ):
        phase = selection_solver.get(phase_name)
        require(isinstance(phase, Mapping), f"{phase_name} solver receipt")
        assert isinstance(phase, Mapping)
        require(phase.get("success") is True, f"{phase_name} success")
        require(
            _require_json_int(phase.get("status"), f"{phase_name} status") == 0,
            f"{phase_name} status",
        )
        require(
            _require_json_int(
                phase.get("selected_source_count"),
                f"{phase_name} selected source count",
            )
            == expected_count,
            f"{phase_name} selected source count",
        )

    expected_evidence_sha = semantic_sha256(
        _recovery_evidence_projection(receipt)
    )
    require(
        str(publication.get("recovery_evidence_sha256") or "")
        == expected_evidence_sha,
        "recovery evidence digest",
    )
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
            selection = selected_candidate.get(
                "fixture_source_universe_selection"
            )
            if not isinstance(selection, Mapping):
                raise ValueError(
                    "selected candidate has no source-universe selection ledger"
                )
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
            receipt["selected_source_universe_sha256"] = str(
                selection.get("selected_source_universe_sha256") or ""
            )
            receipt["census_campaign_sha256"] = str(
                selection.get("census_campaign_sha256") or ""
            )
            receipt["slot_assignment_sha256"] = str(
                selection.get("slot_assignment_sha256") or ""
            )
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
                "directory_sync_policy": (
                    "propagate_real_errors_suppress_only_explicit_unsupported"
                ),
            }
            receipt["publication"]["recovery_evidence_sha256"] = (
                semantic_sha256(_recovery_evidence_projection(receipt))
            )

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
