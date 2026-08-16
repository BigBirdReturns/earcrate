#!/usr/bin/env python3
"""Run every executable EarCrate gate without requiring pytest.

Discovery lives in this dedicated final-stage runner so adding a test below an
in-file ``if __name__ == '__main__'`` block can never make CI silently skip it.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest

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
    os.environ[_thread_var] = "1"

os.environ["EARCRATE_HOME"] = tempfile.mkdtemp(prefix="earcrate_gates_home_")

# Every executable test module is discovered from disk. Adding a new test_*.py
# file therefore changes the gate count automatically; hardware/private-library
# or destructive suites require an explicit exclusion ledger.
EXCLUDED_MODULES: dict[str, str] = {}


def _module_names() -> tuple[str, ...]:
    discovered = tuple(path.stem for path in sorted(TESTS.glob("test_*.py")))
    if not discovered:
        raise RuntimeError("no executable gate modules discovered")
    unknown_exclusions = sorted(set(EXCLUDED_MODULES) - set(discovered))
    if unknown_exclusions:
        raise RuntimeError("gate exclusion names missing modules: " + ", ".join(unknown_exclusions))
    return tuple(name for name in discovered if name not in EXCLUDED_MODULES)


MODULES = _module_names()


def _run_unittest_case(case_class: type[unittest.TestCase], method_name: str) -> None:
    """Execute one unittest method while preserving its fixture lifecycle."""
    result = unittest.TestResult()
    case_class(method_name).run(result)
    problems: list[str] = []
    for test, detail in result.failures:
        problems.append(f"failure in {test}:\n{detail}")
    for test, detail in result.errors:
        problems.append(f"error in {test}:\n{detail}")
    for test, reason in result.skipped:
        problems.append(f"skipped gate {test}: {reason}")
    if problems or result.testsRun != 1:
        if result.testsRun != 1:
            problems.insert(0, f"expected one unittest gate, executed {result.testsRun}")
        raise AssertionError("\n".join(problems))


def _unittest_runner(case_class: type[unittest.TestCase], method_name: str):
    def run() -> None:
        _run_unittest_case(case_class, method_name)

    run.__name__ = f"{case_class.__name__}.{method_name}"
    return run


def _cases():
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        found = 0
        for name, fn in sorted(vars(module).items()):
            if name.startswith("test_") and callable(fn):
                found += 1
                yield module_name, name, fn
        for class_name, case_class in sorted(vars(module).items()):
            if not inspect.isclass(case_class):
                continue
            if case_class is unittest.TestCase or not issubclass(case_class, unittest.TestCase):
                continue
            if case_class.__module__ != module.__name__:
                continue
            for method_name in unittest.defaultTestLoader.getTestCaseNames(case_class):
                found += 1
                yield module_name, f"{class_name}.{method_name}", _unittest_runner(case_class, method_name)
        if not found:
            raise RuntimeError(f"gate module has no discovered tests: {module_name}")


# Vars app code may mutate while constructing EarcrateCore. Restore them between
# gates so discovery order cannot change unrelated provider behavior.
_LEAKY_VARS = ("EARCRATE_STEMS", "EARCRATE_CACHE_ROOT", "EARCRATE_DEFAULTS", "EARCRATE_HOME")


def _is_earcrate_module(name: str) -> bool:
    """Recognize package modules and named runpy copies of the single-file build."""
    return name == "earcrate" or name.startswith("earcrate.") or name.startswith("earcrate_")


def _snapshot_earcrate_modules() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Snapshot module identities and dictionaries before one executable gate.

    Several historical standalone gates deliberately execute ``dist/earcrate.py``
    with ``runpy`` so they can inspect its flattened namespace. The standalone
    bootstrap creates package-shaped import views. Without restoration, those
    views can replace package globals such as exception classes or private helper
    functions, making later package gates depend on discovery order. A shallow
    module-dictionary snapshot is sufficient because the failure mode is namespace
    replacement; existing mutable application state remains governed by its own
    gate fixtures and environment reset.
    """
    modules = {
        name: module
        for name, module in list(sys.modules.items())
        if _is_earcrate_module(name) and module is not None and hasattr(module, "__dict__")
    }
    dictionaries = {name: dict(module.__dict__) for name, module in modules.items()}
    return modules, dictionaries


def _restore_earcrate_modules(
    modules: dict[str, object],
    dictionaries: dict[str, dict[str, object]],
) -> None:
    # Remove package-shaped modules introduced only by the gate.
    for name in list(sys.modules):
        if _is_earcrate_module(name) and name not in modules:
            sys.modules.pop(name, None)

    # Restore both the original module object and its exact global bindings. Code
    # objects retain a reference to the module dictionary, so clear/update repairs
    # helpers and exception identities even when a standalone run mutated them.
    for name, module in modules.items():
        sys.modules[name] = module  # type: ignore[assignment]
        namespace = module.__dict__  # type: ignore[attr-defined]
        namespace.clear()
        namespace.update(dictionaries[name])


def _invoke(fn):
    saved = {key: os.environ.get(key) for key in _LEAKY_VARS}
    modules, dictionaries = _snapshot_earcrate_modules()
    try:
        params = list(inspect.signature(fn).parameters.values())
        if not params:
            fn()
            return
        if len(params) == 1 and params[0].name == "tmp_path":
            fn(Path(tempfile.mkdtemp(prefix="earcrate-gate-")))
            return
        names = ", ".join(p.name for p in params)
        raise TypeError(f"unsupported gate fixture(s): {names}")
    finally:
        _restore_earcrate_modules(modules, dictionaries)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
