from __future__ import annotations

"""The Bad Plus / Aphex Twin ``Flim`` community-symbolic acceptance specimen."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .community import community_bind_pack, community_validate_report
from .model import SpecimenError, specimen_normalize_manifest

FLIM_SPECIMEN_ID = "flim_bad_plus_v1"
FLIM_PROOF_PACK_SHA256 = "a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52"
FLIM_REQUIRED_PACK_MEMBERS = (
    "Flim_Bad_Plus_symbolic_witness.mid",
    "Flim_adjacent_continuation.mid",
    "flim_observation_ledger.json",
    "flim_performance_score.json",
    "flim_harmony_map.json",
    "flim_adjacent_continuation.performance_score.json",
    "flim_target_to_adjacent.mixscore.json",
    "flim_target_to_adjacent.events.json",
    "flim_proof_receipt.json",
    "VALIDATION.md",
    "build_flim_proof.py",
)

# The single-file builder seeds this module with the same embedded specimen table
# used by children.py. Package mode falls back to repository-managed JSON files.
EMBEDDED_SPECIMENS: dict[str, str] = dict(globals().get("EMBEDDED_SPECIMENS", {}))


def flim_repository_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "flim.py" and here.parent.name == "specimen":
        return here.parents[2]
    return here.parent


def _normalized_text_identity(text: str) -> str:
    """Hash UTF-8 text after universal newline normalization.

    Git may materialize repository text as LF or CRLF. Those delivery bytes are not
    evidence drift, while every other textual change remains identity-significant.
    The report's own ``report_sha256`` separately seals its canonical JSON meaning.
    """
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def flim_load_builtin() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_name = f"{FLIM_SPECIMEN_ID}.json"
    report_name = f"{FLIM_SPECIMEN_ID}.community-symbolic.json"
    if manifest_name in EMBEDDED_SPECIMENS and report_name in EMBEDDED_SPECIMENS:
        manifest_text = EMBEDDED_SPECIMENS[manifest_name]
        report_text = EMBEDDED_SPECIMENS[report_name]
    else:
        root = flim_repository_root() / "specimens"
        manifest_text = (root / manifest_name).read_text(encoding="utf-8")
        report_text = (root / report_name).read_text(encoding="utf-8")
    try:
        manifest = json.loads(manifest_text)
        report = json.loads(report_text)
    except Exception as exc:
        raise SpecimenError(f"cannot read built-in Flim specimen JSON: {exc}") from exc

    normalized = specimen_normalize_manifest(manifest)
    if str(normalized["specimen_id"]) != FLIM_SPECIMEN_ID:
        raise SpecimenError("built-in Flim manifest belongs to another specimen")
    evidence_tier = str((normalized.get("metadata") or {}).get("evidence_tier") or manifest.get("evidence_tier") or "")
    if evidence_tier != "community_symbolic_witness":
        raise SpecimenError("Flim must remain in the community-symbolic evidence tier")

    report_artifact = next(
        row for row in normalized["artifacts"] if row["artifact_id"] == "community_symbolic_report"
    )
    if str(report_artifact.get("expected_sha256") or "") != _normalized_text_identity(report_text):
        raise SpecimenError("Flim repository-managed report identity drifted from its manifest")
    sealed_report = community_validate_report(report, specimen_id=FLIM_SPECIMEN_ID)

    pack = next(row for row in normalized["artifacts"] if row["artifact_id"] == "community_proof_pack")
    if str(pack.get("expected_sha256") or "") != FLIM_PROOF_PACK_SHA256:
        raise SpecimenError("Flim proof-pack identity drifted from the supplied report")
    return normalized, sealed_report


def flim_bind_proof_pack(pack_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    manifest, report = flim_load_builtin()
    return community_bind_pack(
        manifest=manifest,
        report=report,
        pack_path=pack_path,
        required_basenames=FLIM_REQUIRED_PACK_MEMBERS,
        output_path=output_path,
    )


def flim_capability() -> dict[str, Any]:
    manifest, report = flim_load_builtin()
    return {
        "schema_version": 1,
        "kind": "earcrate_flim_community_symbolic_capability",
        "ready": True,
        "specimen_id": FLIM_SPECIMEN_ID,
        "evidence_tier": str((manifest.get("metadata") or {}).get("evidence_tier") or "community_symbolic_witness"),
        "proof_pack_sha256": FLIM_PROOF_PACK_SHA256,
        "report_sha256": str(report["report_sha256"]),
        "report_identity": {
            "artifact": "UTF-8 text with universal newlines",
            "meaning": "canonical JSON report_sha256",
        },
        "witness_note_count": int(report["witness"]["total_midi_note_ons"]),
        "continuation_note_count": int(report["adjacent_move"]["total_midi_note_ons"]),
        "transport_operation_count": int(report["transport"]["selected_event_count"]),
        "blind_audio_inference_used": False,
        "whole_organism_passed": False,
    }


__all__ = [
    "FLIM_SPECIMEN_ID",
    "FLIM_PROOF_PACK_SHA256",
    "FLIM_REQUIRED_PACK_MEMBERS",
    "flim_load_builtin",
    "flim_bind_proof_pack",
    "flim_capability",
]
