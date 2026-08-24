#!/usr/bin/env python3
"""Derive governed fixtures, compare them, or measure one published master."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, BinaryIO, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

from earcrate.judge.arc import measure_dynamic_arc
from earcrate.plan.fixture_derivation import derive_fixture_candidates
from earcrate.plan.fixture_diversity import fixture_id, fixture_projection, select_max_min


AUDIT_VERSION = "earcrate_fixture_audit_v3"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_open_handle(handle: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    while True:
        block = handle.read(chunk_size)
        if not block:
            break
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest()


def _stat_identity(stat: os.stat_result) -> Tuple[int, int, int, int]:
    return (
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
        int(stat.st_size),
        int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
    )


def _capture_json(path: Path) -> Tuple[Mapping[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value, _sha256_bytes(raw)


def _capture_audio(path: Path) -> Tuple[np.ndarray, int, str]:
    """Hash and decode one stable open file, rejecting in-place mutation."""
    with path.open("rb") as handle:
        before_stat = _stat_identity(os.fstat(handle.fileno()))
        before_hash = _hash_open_handle(handle)
        audio, sample_rate = sf.read(handle, dtype="float32", always_2d=True)
        after_decode_stat = _stat_identity(os.fstat(handle.fileno()))
        after_hash = _hash_open_handle(handle)
        final_stat = _stat_identity(os.fstat(handle.fileno()))
    if before_hash != after_hash or before_stat != after_decode_stat or before_stat != final_stat:
        raise ValueError(f"audio input changed while being measured: {path}")
    return np.asarray(audio, dtype=np.float32), int(sample_rate), before_hash


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.expanduser().resolve(strict=False))))


def _same_path(left: Path, right: Path) -> bool:
    if _path_key(left) == _path_key(right):
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _reject_output_aliases(outputs: Sequence[Optional[Path]], inputs: Sequence[Path]) -> None:
    resolved_outputs = [path.expanduser().resolve(strict=False) for path in outputs if path is not None]
    resolved_inputs = [path.expanduser().resolve(strict=False) for path in inputs]
    for output in resolved_outputs:
        for input_path in resolved_inputs:
            if _same_path(output, input_path):
                raise ValueError(f"output path aliases an evidence input: {output}")
    for index, output in enumerate(resolved_outputs):
        for other in resolved_outputs[index + 1 :]:
            if _same_path(output, other):
                raise ValueError(f"two outputs alias the same path: {output}")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(path)


def _emit(
    value: Mapping[str, Any],
    output: Optional[Path],
    *,
    protected_inputs: Sequence[Path] = (),
) -> None:
    if output is None:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return
    _reject_output_aliases([output], list(protected_inputs))
    _write_json_atomic(output, value)
    print(str(output))


def derive_receipt(
    matrix_path: Path,
    output_dir: Path,
    candidate_count: Optional[int] = None,
    base_seed: Optional[int] = None,
    max_attempts: Optional[int] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    matrix_file = matrix_path.expanduser().resolve()
    output_root = output_dir.expanduser().resolve(strict=False)
    matrix, matrix_hash = _capture_json(matrix_file)
    result = derive_fixture_candidates(
        matrix,
        candidate_count=candidate_count,
        base_seed=base_seed,
        max_attempts=max_attempts,
    )
    candidates = sorted(
        list(result.get("candidates") or []),
        key=lambda candidate: str(candidate["fixture_sha256"]),
    )
    planned_outputs = [
        output_root / f"fixture-{str(candidate['fixture_sha256'])[:16]}.json"
        for candidate in candidates
    ]
    if receipt_path is not None:
        planned_outputs.append(receipt_path.expanduser().resolve(strict=False))
    _reject_output_aliases(planned_outputs, [matrix_file])

    candidate_files: List[Dict[str, Any]] = []
    for candidate, path in zip(candidates, planned_outputs[: len(candidates)]):
        semantic_identity = str(candidate["fixture_sha256"])
        _write_json_atomic(path, candidate)
        candidate_bytes = path.read_bytes()
        candidate_files.append({
            "path": str(path),
            "file_sha256": _sha256_bytes(candidate_bytes),
            "fixture_id": str(candidate["fixture_id"]),
            "semantic_fixture_identity": semantic_identity,
            "derivation_seed": int(candidate["fixture_derivation_seed"]),
            "island_count": len(candidate.get("islands") or []),
            "assigned_source_count": int(candidate["fixture_derivation"]["assigned_source_count"]),
            "net_duration_s": float(candidate["fixture_derivation"]["net_duration_s"]),
        })
    return {
        **{key: value for key, value in result.items() if key != "candidates"},
        "kind": "earcrate_fixture_derivation_receipt",
        "audit_version": AUDIT_VERSION,
        "path_semantics": "operational_only_not_fixture_or_source_identity",
        "capture_policy": "json_inputs_hashed_from_the_exact_decoded_byte_stream",
        "matrix_file": {
            "path": str(matrix_file),
            "file_sha256": matrix_hash,
            "matrix_semantic_sha256": str(result["matrix_semantic_sha256"]),
        },
        "candidate_files": candidate_files,
    }


def _candidate_rows(paths: Sequence[Path]) -> List[Tuple[Mapping[str, Any], Dict[str, Any]]]:
    rows: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        candidate, file_hash = _capture_json(resolved)
        projection = fixture_projection(candidate)
        receipt = {
            "path": str(resolved),
            "file_sha256": file_hash,
            "fixture_id": fixture_id(candidate, projection),
            "semantic_fixture_identity": projection["fixture_identity"],
            "semantic_realization_identity": projection.get("realization_identity"),
        }
        rows.append((candidate, receipt))
    rows.sort(key=lambda row: (
        row[1]["semantic_fixture_identity"],
        row[1]["file_sha256"],
        row[1]["path"],
    ))
    return rows


def diversity_receipt(paths: Sequence[Path], limit: int = 3) -> Dict[str, Any]:
    rows = _candidate_rows(paths)
    selection = select_max_min([candidate for candidate, _receipt in rows], limit=limit)
    return {
        "kind": "earcrate_fixture_diversity_receipt",
        "version": AUDIT_VERSION,
        "path_semantics": "operational_only_not_fixture_identity",
        "capture_policy": "candidate_json_hashed_from_the_exact_decoded_byte_stream",
        "candidate_files": [receipt for _candidate, receipt in rows],
        "selection": selection,
    }


def arc_receipt(arrangement_path: Path, master_path: Path) -> Dict[str, Any]:
    arrangement_file = arrangement_path.expanduser().resolve()
    master_file = master_path.expanduser().resolve()
    payload, arrangement_hash = _capture_json(arrangement_file)
    arrangement = payload.get("arrangement") if isinstance(payload.get("arrangement"), Mapping) else payload
    audio, sample_rate, master_hash = _capture_audio(master_file)
    if audio.shape[0] == 0:
        raise ValueError("the governed master is empty")
    if audio.shape[1] != 1:
        raise ValueError(
            f"dynamic-arc evidence requires the governed mono master, got {audio.shape[1]} channels"
        )
    mono = np.asarray(audio[:, 0], dtype=np.float32)
    return {
        "kind": "earcrate_dynamic_arc_receipt",
        "version": AUDIT_VERSION,
        "path_semantics": "operational_only_not_arrangement_or_pcm_identity",
        "capture_policy": (
            "arrangement_json_hashed_from_decoded_bytes; "
            "master_open_handle_hashed_before_and_after_decode"
        ),
        "arrangement_file": {
            "path": str(arrangement_file),
            "file_sha256": arrangement_hash,
        },
        "master_file": {
            "path": str(master_file),
            "file_sha256": master_hash,
            "sample_rate": int(sample_rate),
            "channel_count": int(audio.shape[1]),
            "frame_count": int(audio.shape[0]),
        },
        "measurement": measure_dynamic_arc(mono, int(sample_rate), arrangement),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate_fixture_audit",
        description="Derive fixture authority, compare it, or measure the dynamic arc of a governed master",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    derive = subparsers.add_parser(
        "derive",
        help="derive direct planner requests from a public-safe survival matrix",
    )
    derive.add_argument("matrix", type=Path, help="public-safe survival matrix JSON")
    derive.add_argument("--count", type=int, default=None, help="candidate count override")
    derive.add_argument("--base-seed", type=int, default=None, help="derivation seed override")
    derive.add_argument("--max-attempts", type=int, default=None, help="bounded attempt override")
    derive.add_argument("--out-dir", type=Path, required=True, help="candidate output directory")
    derive.add_argument("--receipt", type=Path, default=None, help="derivation receipt path")

    diversity = subparsers.add_parser(
        "diversity",
        help="compare three or more public-safe fixture candidate projections",
    )
    diversity.add_argument("candidates", type=Path, nargs="+", help="candidate JSON files")
    diversity.add_argument("--limit", type=int, default=3, help="maximum max-min shelf size")
    diversity.add_argument("--out", type=Path, default=None, help="write deterministic JSON receipt")

    arc = subparsers.add_parser(
        "arc",
        help="measure one rendered master against its whole-set arrangement",
    )
    arc.add_argument("arrangement", type=Path, help="arrangement or proposal JSON")
    arc.add_argument("master", type=Path, help="published WAV or other soundfile-readable master")
    arc.add_argument("--out", type=Path, default=None, help="write deterministic JSON receipt")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "derive":
            if args.count is not None and int(args.count) <= 0:
                raise ValueError("--count must be positive")
            if args.max_attempts is not None and int(args.max_attempts) <= 0:
                raise ValueError("--max-attempts must be positive")
            receipt_path = (
                args.receipt.expanduser().resolve(strict=False)
                if args.receipt is not None
                else (args.out_dir.expanduser().resolve(strict=False) / "DERIVATION_RECEIPT.json")
            )
            receipt = derive_receipt(
                args.matrix,
                args.out_dir,
                candidate_count=args.count,
                base_seed=args.base_seed,
                max_attempts=args.max_attempts,
                receipt_path=receipt_path,
            )
            _emit(receipt, receipt_path, protected_inputs=[args.matrix])
            return 0 if bool(receipt["complete"]) else 3
        if args.command == "diversity":
            if int(args.limit) <= 0:
                raise ValueError("--limit must be positive")
            _reject_output_aliases([args.out], list(args.candidates))
            _emit(
                diversity_receipt(args.candidates, int(args.limit)),
                args.out,
                protected_inputs=args.candidates,
            )
        elif args.command == "arc":
            _reject_output_aliases([args.out], [args.arrangement, args.master])
            _emit(
                arc_receipt(args.arrangement, args.master),
                args.out,
                protected_inputs=[args.arrangement, args.master],
            )
        else:
            raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"earcrate_fixture_audit: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
