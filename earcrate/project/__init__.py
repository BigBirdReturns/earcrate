"""EarCrate's canonical CLI-first project/score engine.

The package is independent of ``EarcrateCore``. Existing analysis, region, transform,
transition, judge, and provider modules are consumed through explicit buffalo adapters;
projects, revisions, commands, lowering, rendering, mastering, and exports live here.
"""

from .commands import apply_command
from .compiler import compile_project, import_legacy_arrangement, prepare_source_asset
from .export import export_project
from .lower import lower_revision, renderability_receipt
from .model import compute_revision_sha, seal_revision, summarize_revision
from .render import preview_project, render_project, verify_render
from .store import ProjectStore

__all__ = [
    "ProjectStore",
    "apply_command",
    "compile_project",
    "import_legacy_arrangement",
    "prepare_source_asset",
    "lower_revision",
    "renderability_receipt",
    "preview_project",
    "render_project",
    "verify_render",
    "export_project",
    "compute_revision_sha",
    "seal_revision",
    "summarize_revision",
]
