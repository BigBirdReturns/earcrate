"""EarCrate's canonical project and causal-performance engine."""

import sys as _project_sys
from typing import Any, Mapping

from .commands import apply_command
from .compiler import compile_project, import_legacy_arrangement, prepare_source_asset
from .export import export_project
from .lower import lower_revision, renderability_receipt
from .model import compute_revision_sha, seal_revision, summarize_revision
from .render import preview_project, render_project, verify_render
from .store import ProjectStore as LegacyProjectStore
from .gate8_store import Gate8ProjectStore
from .causal_revision import causal_seal_revision
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
from .util import ValidationError


def _compatible_score_family(score: Mapping[str, Any]) -> str:
    """Recognize both sealed and early structural DJ stage-score artifacts.

    The first director artifacts predate the explicit ``schema`` string but already
    contain the complete version-1 stage-score contract. Historical custody must
    preserve those exact bytes rather than rewriting them just to add a marker.
    """
    if str(score.get("schema") or "") == "earcrate/dj-stage-score@1":
        return "dj_stage_score"
    if (
        int(score.get("schema_version") or 0) == 1
        and str(score.get("stage_id") or "")
        and isinstance(score.get("sections"), list)
        and isinstance(score.get("events"), list)
        and int(score.get("ticks_per_beat") or 0) > 0
        and int(score.get("total_ticks") or 0) > 0
    ):
        return "dj_stage_score"
    if str(score.get("kind") or "") == "earcrate_player_piano_composition":
        return "player_piano_composition"
    raise ValidationError("unsupported causal-score artifact family")


# Package mode and the generated single-file project bootstrap both load these
# modules before this facade. Patch the exact historical compatibility seams in
# the already-loaded modules so every surface uses the same authorities.
_custody_module = _project_sys.modules.get(__name__ + ".custody")
if _custody_module is not None:
    setattr(_custody_module, "_score_family", _compatible_score_family)

_continuation_module = _project_sys.modules.get(__name__ + ".continuation")
if _continuation_module is not None:
    # Causal-score continuations are not audio-clip revisions. Using the generic
    # clip validator incorrectly demands a compiled TasteSpec policy and rejects a
    # semantically adopted causal child. Preserve the causal authority instead.
    setattr(_continuation_module, "seal_revision", causal_seal_revision)

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
    "causal_seal_revision",
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
