"""Canonical project score compiler public surface.

The implementation is split by concern so source identity, deck search, beam planning,
transition compilation, static gates, and legacy migration remain independently testable.
"""

from .compiler_entry import compile_project
from .compiler_gate import static_gate
from .compiler_legacy import import_legacy_arrangement
from .compiler_source_common import EAR_TO_RENDER, HARD_TECHNIQUES, prepare_source_asset
from .compiler_source_crate import prepare_crate_sources
from .compiler_source_manifest import load_source_manifest, prepare_manifest_sources

__all__ = [
    "EAR_TO_RENDER",
    "HARD_TECHNIQUES",
    "compile_project",
    "import_legacy_arrangement",
    "prepare_source_asset",
    "prepare_crate_sources",
    "load_source_manifest",
    "prepare_manifest_sources",
    "static_gate",
]
