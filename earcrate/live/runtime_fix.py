"""Compatibility imports for the folded live runtime implementation.

Provenance-preserving event ordering and measured CPU execution now live in
``earcrate.live.runtime``. This module performs no mutation and is excluded from
the package build; downstream imports should move to the core module.
"""

from earcrate.live.runtime import (
    live_build_session,
    live_compile_cpu_program,
    live_execute_cpu_program,
    live_lower_session_to_midi,
    live_validate_cpu_execution,
    live_validate_cpu_program,
    live_validate_midi_lowering,
)

__all__ = [
    "live_build_session",
    "live_compile_cpu_program",
    "live_execute_cpu_program",
    "live_lower_session_to_midi",
    "live_validate_cpu_execution",
    "live_validate_cpu_program",
    "live_validate_midi_lowering",
]
