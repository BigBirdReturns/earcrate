from __future__ import annotations

"""Root, repository, and version markers for estate discovery."""

from pathlib import Path
import re
from typing import Any

from earcrate.estate.model import estate_unique_preserve

_AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma"}
_SCORE_EXTS = {".pdf", ".musicxml", ".mxl", ".mscz"}
_MIDI_EXTS = {".mid", ".midi"}
_MODEL_EXTS = {".pt", ".pth", ".ckpt", ".onnx", ".safetensors", ".pb", ".tflite"}
_DISTRIBUTION_EXTS = {".whl", ".exe", ".msi", ".dmg", ".appimage", ".tar", ".gz", ".xz", ".7z"}
_TEMP_EXTS = {".tmp", ".temp", ".part", ".partial", ".lock", ".pyc", ".pyo"}
_JSON_KINDS = {
    "earcrate_project_revision": ("project_revision", "authority"),
    "earcrate_floor_release_candidate": ("release_candidate", "review_queue"),
    "earcrate_floor_human_musical_review": ("human_review", "durable_evidence"),
    "earcrate_floor_rights_review": ("rights_record", "durable_evidence"),
    "earcrate_floor_rights_decision": ("rights_record", "durable_evidence"),
    "earcrate_floor_publication_receipt": ("proof_receipt", "durable_evidence"),
    "earcrate_floor_release_gate_receipt": ("proof_receipt", "durable_evidence"),
    "earcrate_buffalo_gate_receipt": ("proof_receipt", "durable_evidence"),
    "earcrate_specimen_manifest": ("proof_manifest", "durable_evidence"),
    "earcrate_community_symbolic_report": ("proof_manifest", "durable_evidence"),
    "earcrate_floor_provider_manifest": ("model_manifest", "durable_evidence"),
    "earcrate_floor_invocation_receipt": ("run_receipt", "durable_evidence"),
    "earcrate_floor_evaluation_ledger": ("run_receipt", "durable_evidence"),
    "earcrate_floor_tournament_report": ("run_receipt", "durable_evidence"),
    "earcrate_mix_render_receipt": ("run_receipt", "durable_evidence"),
    "earcrate_mix_execution_ledger": ("command_ledger", "durable_evidence"),
    "earcrate_homelab_catalog": ("proof_manifest", "durable_evidence"),
    "earcrate_homelab_node_receipt": ("run_receipt", "durable_evidence"),
    "earcrate_homelab_audit": ("run_receipt", "durable_evidence"),
    "earcrate_homelab_campaign": ("run_receipt", "durable_evidence"),
    "earcrate_homelab_stage_receipt": ("run_receipt", "durable_evidence"),
    "earcrate_homelab_audition_ledger": ("human_review", "durable_evidence"),
    "earcrate_homelab_adoption_decision": ("proof_receipt", "durable_evidence"),
}
_VERSION_PATTERNS = (
    re.compile(r"ENGINE_DISPLAY_VERSION\s*=\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"ENGINE_VERSION\s*=\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]"),
)


def _estate_now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _estate_detect_root_role(root: Path) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if (root / ".git").exists() and (root / "earcrate").is_dir():
        return "repository", ["contains .git and earcrate package"]
    if (root / "earcrate").is_dir() and (root / "build").is_dir() and any((root / name).is_file() for name in ("AGENTS.md", "README.md", "VERIFY_PACKAGE.py")):
        return "repository", ["contains an EarCrate source snapshot without live Git metadata"]
    if (root / "agent" / "config.json").is_file() or (root / "work").is_dir():
        return "workspace", ["contains EarCrate workspace directories"]
    if (root / "projects").is_dir() and (root / "runs").is_dir():
        return "project_store", ["contains project and run stores"]
    if (root / "models").is_dir() and (root / "cache").is_dir():
        reasons.append("contains model/cache directories")
    lowered = root.name.lower()
    if lowered in {"music", "audio", "library", "sample library", "sample factory"} or "music" in lowered:
        return "source_library", ["root name suggests source library"]
    if reasons:
        return "mixed_estate", reasons
    return "unclassified", ["no authoritative root marker"]


def _estate_git_metadata(root: Path) -> dict[str, Any]:
    git = root / ".git"
    try:
        if git.is_file():
            text = git.read_text(encoding="utf-8", errors="replace").strip()
            if text.lower().startswith("gitdir:"):
                target = Path(text.split(":", 1)[1].strip())
                git = target if target.is_absolute() else (root / target).resolve()
        if not git.is_dir():
            return {"git_status": "absent"}
        head_text = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        ref = None
        commit = None
        if head_text.startswith("ref:"):
            ref = head_text.split(":", 1)[1].strip()
            ref_path = git / ref
            if ref_path.is_file():
                commit = ref_path.read_text(encoding="utf-8", errors="replace").strip()
            elif (git / "packed-refs").is_file():
                for line in (git / "packed-refs").read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("#") or line.startswith("^") or " " not in line:
                        continue
                    sha, name = line.split(" ", 1)
                    if name.strip() == ref:
                        commit = sha.strip()
                        break
        else:
            commit = head_text
        if commit and not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            commit = None
        return {
            "git_status": "parsed",
            "git_dir": str(git),
            "head_ref": ref,
            "head_sha": commit.lower() if commit else None,
        }
    except Exception as exc:
        return {"git_status": "unreadable", "git_error": f"{type(exc).__name__}: {exc}"[:240]}


def _estate_text_version_metadata(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}
        text = path.read_text(encoding="utf-8", errors="ignore")[:2 * 1024 * 1024]
    except Exception:
        return {}
    versions = estate_unique_preserve(match.group(1) for pattern in _VERSION_PATTERNS for match in pattern.finditer(text))
    return {"declared_versions": versions[:20]} if versions else {}


def _estate_root_version_metadata(root: Path) -> dict[str, Any]:
    versions: list[str] = []
    inspected: list[str] = []
    for relative in (
        "earcrate/__init__.py",
        "earcrate/core/config.py",
        "dist/earcrate.py",
        "CHANGELOG.md",
        "PRODUCT.md",
    ):
        path = root / relative
        if not path.is_file():
            continue
        inspected.append(relative)
        metadata = _estate_text_version_metadata(path)
        versions.extend(metadata.get("declared_versions") or [])
    return {"version_files_inspected": inspected, "declared_versions": estate_unique_preserve(versions)[:50]}


__all__ = [
    "_AUDIO_EXTS", "_SCORE_EXTS", "_MIDI_EXTS", "_MODEL_EXTS",
    "_DISTRIBUTION_EXTS", "_TEMP_EXTS", "_JSON_KINDS",
    "_estate_now_utc", "_estate_detect_root_role", "_estate_git_metadata",
    "_estate_text_version_metadata", "_estate_root_version_metadata",
]
