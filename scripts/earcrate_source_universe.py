#!/usr/bin/env python3
"""Compatibility wrapper for the reviewed Stage 2D CLI core."""
from __future__ import annotations

from earcrate.plan import source_universe_cli_core as _core
from earcrate.plan.source_universe_cli_final_contract import (
    install_source_universe_cli_final_contract as _install_final_contract,
)

_install_final_contract(_core)

_PATCHABLE = (
    "_REPLACE",
    "_MATERIALIZE_CANDIDATE",
    "_fsync_parent",
    "select_planable_source_universe",
)
_BASELINE = {
    name: getattr(_core, name)
    for name in _PATCHABLE
    if hasattr(_core, name)
}


def __getattr__(name):
    return getattr(_core, name)


def _apply_local_overrides():
    for name in _PATCHABLE:
        if name in globals():
            setattr(_core, name, globals()[name])


def _restore_core_baseline():
    for name, value in _BASELINE.items():
        setattr(_core, name, value)


def main(argv=None) -> int:
    _apply_local_overrides()
    try:
        return _core.main(argv)
    finally:
        _restore_core_baseline()


if __name__ == "__main__":
    raise SystemExit(main())
