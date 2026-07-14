#!/usr/bin/env python3
"""Run every executable EarCrate gate without requiring pytest.

Discovery lives in this dedicated final-stage runner so adding a test below an
in-file ``if __name__ == '__main__'`` block can never make CI silently skip it.
"""
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
import argparse
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

for _thread_var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
):
    # A gate runner must be deterministic even when the parent shell exports a
    # high native thread count. Process-pool gates plus threaded BLAS otherwise
    # oversubscribe hard enough to segfault on ordinary CI/local machines.
    os.environ[_thread_var] = "1"

MODULES = ("test_gates", "test_tastespec_vertical", "test_first_minute_fixes", "test_reference_study", "test_stem_warmer")


def _cases():
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        found = 0
        for name, fn in sorted(vars(module).items()):
            if name.startswith("test_") and callable(fn):
                found += 1
                yield module_name, name, fn
        if not found:
            raise RuntimeError(f"gate module has no discovered tests: {module_name}")


def _invoke(fn):
    params = list(inspect.signature(fn).parameters.values())
    if not params:
        fn()
        return
    if len(params) == 1 and params[0].name == "tmp_path":
        fn(Path(tempfile.mkdtemp(prefix="earcrate-gate-")))
        return
    names = ", ".join(p.name for p in params)
    raise TypeError(f"unsupported gate fixture(s): {names}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the complete executable EarCrate gate suite")
    parser.add_argument("--list", action="store_true", help="list discovered gates without executing them")
    parser.add_argument("--start", type=int, default=0, help="zero-based discovered gate offset")
    parser.add_argument("--limit", type=int, default=0, help="maximum gates to run (0 means all remaining)")
    args = parser.parse_args(argv)
    cases = list(_cases())
    if args.list:
        for index, (module_name, name, _fn) in enumerate(cases):
            print(f"{index:03d} {module_name}.{name}")
        print(f"SUMMARY {len(cases)} gates discovered")
        return 0
    start = max(0, int(args.start))
    cases = cases[start: start + args.limit if args.limit and args.limit > 0 else None]
    if not cases:
        print("FAIL runner: selected gate range is empty", flush=True)
        return 2
    failures = 0
    for module_name, name, fn in cases:
        label = f"{module_name}.{name}"
        try:
            _invoke(fn)
            print(f"PASS {label}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"FAIL {label}: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
    print(f"SUMMARY {len(cases) - failures}/{len(cases)} gates passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
