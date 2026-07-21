"""Compatibility import for the explicit live capability contract.

The capability report now lives in ``earcrate.live.capabilities`` and contains
no synthetic runtime counters. This module performs no mutation and is excluded
from the package build.
"""

from earcrate.live.capabilities import live_runtime_capability

__all__ = ["live_runtime_capability"]
