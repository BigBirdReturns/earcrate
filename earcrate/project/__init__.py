"""EarCrate's canonical project and causal-performance engine."""

from .commands import apply_command
from .compiler import compile_project, import_legacy_arrangement, prepare_source_asset
from .export import export_project
from .lower import lower_revision, renderability_receipt
from .model import compute_revision_sha, seal_revision, summarize_revision
from .render import preview_project, render_project, verify_render
from .store import ProjectStore as LegacyProjectStore
from .gate8_store import Gate8ProjectStore
from .custody import (
    project_seed_selection_receipt,
    project_import_causal_score,
    project_verify_custody,
    project_adoption_readiness,
    project_adopt_causal_semantics,
    project_verify_semantic_adoption,
    project_render_causal_score,
)
from .library import project_real_library_handshake
from .continuation import project_extend_causal_score, project_verify_causal_continuation
from .source_execution import project_execute_registered_source_phrase

ProjectStore = Gate8ProjectStore

__all__ = [
    "ProjectStore",
    "LegacyProjectStore",
    "Gate8ProjectStore",
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
    "project_seed_selection_receipt",
    "project_import_causal_score",
    "project_verify_custody",
    "project_adoption_readiness",
    "project_adopt_causal_semantics",
    "project_verify_semantic_adoption",
    "project_render_causal_score",
    "project_real_library_handshake",
    "project_extend_causal_score",
    "project_verify_causal_continuation",
    "project_execute_registered_source_phrase",
]
