from __future__ import annotations

"""Conservative file classification and hash-selection policy."""

from pathlib import Path
from typing import Any, Mapping

from earcrate.estate.markers import (
    _AUDIO_EXTS, _DISTRIBUTION_EXTS, _MIDI_EXTS, _MODEL_EXTS,
    _SCORE_EXTS, _TEMP_EXTS,
)

def _estate_classify_path(path: Path, relative: str, root_role: str) -> tuple[str, str, list[str]]:
    lower = relative.lower().replace("\\", "/")
    parts = tuple(part for part in lower.split("/") if part)
    name = path.name.lower()
    suffix = path.suffix.lower()
    reasons: list[str] = []

    if path.is_symlink():
        return "unknown", "manual_review", ["symlink recorded but not followed"]
    if name == "earcrate_workspace.json" or name == "config_pointer.json":
        return "workspace_pointer", "authority", ["workspace pointer breadcrumb"]
    if name in {"config.json", "config.toml", "machine_defaults.json"} and (
        "agent" in parts or "earcrate" in lower or "workspace" in lower
    ):
        return "workspace_config", "authority", ["workspace or machine configuration"]
    if name == "project.json" and "projects" in parts:
        return "project_index", "authority", ["project active-head index"]
    if suffix == ".json" and "revisions" in parts and "projects" in parts:
        return "project_revision", "authority", ["immutable project revision candidate"]
    if name == "commands.jsonl" or name.endswith(".events.json"):
        return "command_ledger", "authority", ["append-only or event execution ledger"]
    if suffix in {".sqlite", ".sqlite3", ".db"} or name in {"earcrate.sqlite", "jukebreaker.sqlite"}:
        return "database", "authority", ["SQLite authority candidate; never auto-merged"]
    if name.endswith((".sqlite-wal", ".sqlite-shm", ".db-wal", ".db-shm")):
        return "temporary", "temporary", ["SQLite side file"]
    if suffix in _MODEL_EXTS:
        return "model_weight", "durable_evidence", ["model-weight extension"]
    if name in {"components.lock.json", "models.lock.json"} or "model" in name and suffix == ".json":
        return "model_manifest", "durable_evidence", ["model/component identity ledger"]
    if suffix == ".json" and ("schemas" in parts or name.endswith(".schema.json")):
        return "schema", "durable_evidence", ["versioned schema"]
    if name.endswith(".meta.json") and ("l3" in parts or "cache" in parts):
        return "artifact_metadata", "derived_rebuildable", ["derived artifact metadata"]
    if suffix == ".bin" and ("l3" in parts or "cache" in parts):
        return "artifact_blob", "derived_rebuildable", ["derived artifact blob"]
    if suffix in {".npz", ".npy"} and ("analysis" in parts or "cache" in parts):
        return "analysis_cache", "derived_rebuildable", ["rebuildable analysis cache"]
    if "cache" in parts or "__pycache__" in parts:
        return "temporary", "temporary", ["cache or interpreter residue"]
    if name.startswith("gate-ledger-") or name.startswith("package-verifier-") or "song-reader-tests-" in name:
        return "ci_ledger", "durable_evidence", ["retained gate/package workflow artifact"]
    if suffix == ".zip" and any(token in name for token in ("proof", "evidence", "receipt", "buffalo", "floor", "release")):
        return "proof_pack", "durable_evidence", ["proof/evidence archive by name"]
    if suffix == ".json" and any(token in name for token in ("manifest", "proof", "receipt", "ledger", "report")):
        klass = "proof_receipt" if any(token in name for token in ("receipt", "proof", "ledger", "report")) else "proof_manifest"
        return klass, "durable_evidence", ["evidence JSON by name"]
    if suffix in _MIDI_EXTS:
        return "midi", "durable_evidence", ["symbolic performance file"]
    if suffix in _SCORE_EXTS:
        return "source_score", "external_source_reference", ["score/source document"]
    if suffix in _AUDIO_EXTS:
        if "stem" in parts or "stems" in parts or any(token in name for token in (".piano.", ".bass.", ".drums.", ".deck_a", ".deck_b")):
            return "stem_audio", "derived_rebuildable", ["stem or deck audio"]
        if any(token in lower for token in ("audition", "listen", "reference_then", "candidate", "human_review")):
            return "audition_audio", "review_queue", ["listening or release candidate audio"]
        if any(token in lower for token in ("render", "master", "continuation", "proof", "output", "target_to_adjacent")) and root_role != "source_library":
            return "render_audio", "derived_rebuildable", ["rendered or proof audio"]
        return "source_audio", "external_source_reference", ["audio source candidate"]
    if suffix in _DISTRIBUTION_EXTS or name.startswith("dist-earcrate") or (name == "earcrate.py" and "dist" in parts):
        return "distribution", "historical_archive", ["built distribution or package"]
    if suffix in _TEMP_EXTS or name.startswith(".") and any(token in name for token in ("tmp", "probe", "partial")):
        return "temporary", "temporary", ["temporary or probe file"]
    if suffix in {".md", ".rst", ".txt"} or "docs" in parts:
        return "documentation", "historical_archive", ["documentation or session record"]
    if name == "head" and ".git" in parts:
        return "repository", "historical_archive", ["repository HEAD metadata"]
    if root_role == "repository":
        return "repository", "historical_archive", ["file inside repository checkout"]
    return "unknown", "manual_review", ["no safe classification rule"]


def _estate_should_hash(item: Mapping[str, Any], policy: Mapping[str, Any], hash_mode: str) -> bool:
    if item.get("file_type") != "file":
        return False
    size = int(item.get("bytes") or 0)
    scan = dict(policy.get("scan") or {})
    if size > int(scan.get("max_hash_bytes_per_file") or 0):
        return False
    if hash_mode == "all":
        return True
    if hash_mode == "none" or hash_mode == "duplicates":
        return False
    extension = str(item.get("extension") or "").lower()
    if extension in set(scan.get("hash_extensions") or []):
        return True
    return item.get("classification") in {
        "workspace_pointer",
        "workspace_config",
        "project_index",
        "project_revision",
        "command_ledger",
        "schema",
        "proof_manifest",
        "proof_receipt",
        "proof_pack",
        "ci_ledger",
        "release_candidate",
        "human_review",
        "rights_record",
        "run_receipt",
        "distribution",
    }


__all__ = ["_estate_classify_path", "_estate_should_hash"]
