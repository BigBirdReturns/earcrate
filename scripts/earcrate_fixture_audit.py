#!/usr/bin/env python3
"""Compare governed fixture candidates or measure one published master."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

from earcrate.judge.arc import measure_dynamic_arc
from earcrate.plan.fixture_diversity import fixture_id, fixture_projection, select_max_min


AUDIT_VERSION = "earcrate_fixture_audit_v1"


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


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


def _emit(value: Mapping[str, Any], output: Optional[Path]) -> None:
    if output is None:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return
    _write_json_atomic(output, value)
    print(str(output))


def _candidate_rows(paths: Sequence[Path]) -> List[Tuple[Mapping[str, Any], Dict[str, Any]]]:
    rows: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        candidate = _load_json(resolved)
        projection = fixture_projection(candidate)
        receipt = {
            "path": str(resolved),
            "file_sha256": _sha256_file(resolved),
            "fixture_id": fixture_id(candidate, projection),
            "semantic_fixture_identity": projection["fixture_identity"],
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
        "candidate_files": [receipt for _candidate, receipt in rows],
        "selection": selection,
    }


def arc_receipt(arrangement_path: Path, master_path: Path) -> Dict[str, Any]:
    arrangement_file = arrangement_path.expanduser().resolve()
    master_file = master_path.expanduser().resolve()
    payload = _load_json(arrangement_file)
    arrangement = payload.get("arrangement") if isinstance(payload.get("arrangement"), Mapping) else payload
    audio, sample_rate = sf.read(str(master_file), dtype="float32", always_2d=True)
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
        "arrangement_file": {
            "path": str(arrangement_file),
            "file_sha256": _sha256_file(arrangement_file),
        },
        "master_file": {
            "path": str(master_file),
            "file_sha256": _sha256_file(master_file),
            "sample_rate": int(sample_rate),
            "channel_count": int(audio.shape[1]),
            "frame_count": int(audio.shape[0]),
        },
        "measurement": measure_dynamic_arc(mono, int(sample_rate), arrangement),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earcrate_fixture_audit",
        description="Compare fixture authority or measure the dynamic arc of a governed master",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        if args.command == "diversity":
            if int(args.limit) <= 0:
                raise ValueError("--limit must be positive")
            _emit(diversity_receipt(args.candidates, int(args.limit)), args.out)
        elif args.command == "arc":
            _emit(arc_receipt(args.arrangement, args.master), args.out)
        else:  # argparse protects this branch
            raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"earcrate_fixture_audit: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
