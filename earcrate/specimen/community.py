from __future__ import annotations

"""Custody for community-symbolic witness packs.

Community transcriptions can establish an editable, playable witness and can seed
composition. They are not blind audio inference. This module binds an exact proof
pack, validates its declared evidence contract, and preserves that distinction in a
machine-readable receipt.
"""

import stat
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .model import (
    SpecimenError,
    specimen_normalize_manifest,
    specimen_sha256_file,
    specimen_sha256_json,
    specimen_write_json_atomic,
)

COMMUNITY_REPORT_SCHEMA_VERSION = 1
COMMUNITY_REPORT_KIND = "earcrate_community_symbolic_report"
COMMUNITY_PACK_RECEIPT_SCHEMA_VERSION = 1
COMMUNITY_PACK_RECEIPT_KIND = "earcrate_community_symbolic_pack_receipt"
COMMUNITY_EVIDENCE_TIER = "community_symbolic_witness"


def _require_positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SpecimenError(f"{field} must be an integer") from exc
    if number <= 0:
        raise SpecimenError(f"{field} must be positive")
    return number


def _require_nonnegative_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SpecimenError(f"{field} must be an integer") from exc
    if number < 0:
        raise SpecimenError(f"{field} must be nonnegative")
    return number


def _require_zero(value: Any, field: str, tolerance: float = 1e-12) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SpecimenError(f"{field} must be numeric") from exc
    if abs(number) > float(tolerance):
        raise SpecimenError(f"{field} must reconcile to zero")
    return number


def community_validate_report(report: Mapping[str, Any], *, specimen_id: str | None = None) -> dict[str, Any]:
    """Validate and seal one reported community-symbolic proof contract."""
    raw = deepcopy(dict(report))
    if int(raw.get("schema_version") or 0) != COMMUNITY_REPORT_SCHEMA_VERSION:
        raise SpecimenError("unsupported community-symbolic report schema")
    if str(raw.get("kind") or "") != COMMUNITY_REPORT_KIND:
        raise SpecimenError("unsupported community-symbolic report kind")
    if str(raw.get("evidence_tier") or "") != COMMUNITY_EVIDENCE_TIER:
        raise SpecimenError("community report must declare the community-symbolic evidence tier")
    actual_specimen = str(raw.get("specimen_id") or "").strip()
    if not actual_specimen:
        raise SpecimenError("community report requires specimen_id")
    if specimen_id is not None and actual_specimen != str(specimen_id):
        raise SpecimenError("community report belongs to another specimen")

    target = dict(raw.get("target") or {})
    if not str(target.get("title") or "") or not str(target.get("performer") or ""):
        raise SpecimenError("community report requires a target title and performer")
    tempo = float(target.get("tempo_bpm") or 0.0)
    meter = dict(target.get("meter") or {})
    if tempo <= 0.0 or int(meter.get("numerator") or 0) <= 0 or int(meter.get("denominator") or 0) <= 0:
        raise SpecimenError("community report requires positive tempo and meter")

    witness = dict(raw.get("witness") or {})
    witness_counts = dict(witness.get("event_counts") or {})
    piano = _require_nonnegative_int(witness_counts.get("piano"), "witness piano events")
    bass = _require_nonnegative_int(witness_counts.get("acoustic_bass"), "witness acoustic-bass events")
    drums = _require_nonnegative_int(witness_counts.get("drums"), "witness drum events")
    total = _require_positive_int(witness.get("total_midi_note_ons"), "witness total MIDI note-ons")
    if piano + bass + drums != total:
        raise SpecimenError("witness event counts do not sum to total MIDI note-ons")
    _require_positive_int(witness.get("bars"), "witness bars")
    _require_positive_int(witness.get("beats"), "witness beats")
    _require_positive_int(witness.get("midi_tracks"), "witness MIDI tracks")
    if float(witness.get("duration_seconds") or 0.0) <= 0.0:
        raise SpecimenError("witness duration must be positive")
    _require_zero(witness.get("master_stem_max_abs"), "witness master/stem error")

    adjacent = dict(raw.get("adjacent_move") or {})
    adjacent_counts = dict(adjacent.get("event_counts") or {})
    continuation_total = _require_positive_int(adjacent.get("total_midi_note_ons"), "continuation total MIDI note-ons")
    continuation_sum = sum(
        _require_nonnegative_int(adjacent_counts.get(name), f"continuation {name} events")
        for name in ("piano", "bass", "drums")
    )
    if continuation_sum != continuation_total:
        raise SpecimenError("continuation event counts do not sum to total MIDI note-ons")
    progression = [str(value) for value in adjacent.get("progression") or []]
    if len(progression) != int(adjacent.get("bars") or 0) or not progression:
        raise SpecimenError("continuation progression must name one harmony per bar")
    _require_positive_int(adjacent.get("transition_proof_count"), "continuation transition proofs")
    _require_zero(adjacent.get("master_stem_max_abs"), "continuation master/stem error")
    if bool(adjacent.get("generated_events_claim_source_transcription", True)):
        raise SpecimenError("generated continuation events may not claim source-transcribed identity")

    transport = dict(raw.get("transport") or {})
    operations = [str(value) for value in transport.get("operations") or []]
    if not operations or len(operations) != _require_positive_int(transport.get("selected_event_count"), "transport selected events"):
        raise SpecimenError("transport operation count does not match selected events")
    if int(transport.get("selected_event_count") or 0) != int(transport.get("executed_event_count") or -1):
        raise SpecimenError("transport did not execute every selected event")
    if _require_nonnegative_int(transport.get("refused_event_count"), "transport refused events") != 0:
        raise SpecimenError("transport contains refused events")
    _require_zero(transport.get("master_stem_max_abs"), "transport master/stem error")

    boundary = dict(raw.get("boundary") or {})
    required_false = (
        "target_recording_bytes_used",
        "blind_audio_inference_used",
        "cephalopod_reader_used",
        "community_sources_withheld",
    )
    if any(bool(boundary.get(field)) for field in required_false):
        raise SpecimenError("community-symbolic report launders recording inference or withholds its evidence tier")
    if not bool(boundary.get("community_symbolic_sources_used")):
        raise SpecimenError("community-symbolic report must name its symbolic evidence dependency")
    if bool(boundary.get("whole_organism_passed")):
        raise SpecimenError("community-symbolic evidence cannot claim whole-organism passage")

    supplied = str(raw.pop("report_sha256", "") or "")
    raw["report_sha256"] = specimen_sha256_json(raw)
    if supplied and supplied != raw["report_sha256"]:
        raise SpecimenError("report_sha256 does not match community-symbolic report")
    return raw


def community_zip_inventory(pack_path: str | Path, *, max_uncompressed_bytes: int = 4 << 30) -> list[dict[str, Any]]:
    """Return a safe, deterministic ZIP inventory without extracting its members."""
    source = Path(pack_path).expanduser().resolve()
    if not source.is_file():
        raise SpecimenError(f"community proof pack does not exist: {source}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise SpecimenError("community proof pack is not a valid ZIP archive") from exc
    with archive:
        for info in archive.infolist():
            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
                raise SpecimenError(f"unsafe community proof-pack path: {info.filename!r}")
            normalized = pure.as_posix()
            if normalized in seen:
                raise SpecimenError(f"duplicate community proof-pack member: {normalized}")
            seen.add(normalized)
            mode = (int(info.external_attr) >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise SpecimenError(f"community proof pack may not contain symlinks: {normalized}")
            if info.is_dir():
                continue
            total += int(info.file_size)
            if total > int(max_uncompressed_bytes):
                raise SpecimenError("community proof pack exceeds the uncompressed custody limit")
            rows.append(
                {
                    "path": normalized,
                    "basename": pure.name,
                    "size": int(info.file_size),
                    "compressed_size": int(info.compress_size),
                    "crc32": f"{int(info.CRC) & 0xffffffff:08x}",
                }
            )
    rows.sort(key=lambda row: row["path"])
    return rows


def community_bind_pack(
    *,
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    pack_path: str | Path,
    required_basenames: Sequence[str],
    output_path: str | Path | None = None,
    pack_artifact_id: str = "community_proof_pack",
) -> dict[str, Any]:
    """Bind one exact proof pack to its manifest and reported evidence contract."""
    normalized = specimen_normalize_manifest(manifest)
    sealed_report = community_validate_report(report, specimen_id=str(normalized["specimen_id"]))
    artifact = next(
        (row for row in normalized["artifacts"] if str(row["artifact_id"]) == str(pack_artifact_id)),
        None,
    )
    if artifact is None:
        raise SpecimenError(f"manifest has no {pack_artifact_id} artifact")
    if str(artifact.get("branch") or "") != "symbolic":
        raise SpecimenError("community proof pack must belong to the symbolic branch")
    expected = str(artifact.get("expected_sha256") or "")
    actual = specimen_sha256_file(pack_path)
    if not expected or actual != expected:
        raise SpecimenError(f"community proof-pack identity changed: expected {expected}, found {actual}")

    inventory = community_zip_inventory(pack_path)
    by_basename: dict[str, list[dict[str, Any]]] = {}
    for row in inventory:
        by_basename.setdefault(str(row["basename"]), []).append(row)
    selected: list[dict[str, Any]] = []
    for basename in required_basenames:
        matches = by_basename.get(str(basename), [])
        if len(matches) != 1:
            raise SpecimenError(
                f"community proof pack requires exactly one {basename!r} member; found {len(matches)}"
            )
        selected.append(deepcopy(matches[0]))

    receipt = {
        "schema_version": COMMUNITY_PACK_RECEIPT_SCHEMA_VERSION,
        "kind": COMMUNITY_PACK_RECEIPT_KIND,
        "specimen_id": str(normalized["specimen_id"]),
        "evidence_tier": COMMUNITY_EVIDENCE_TIER,
        "manifest_sha256": str(normalized["manifest_sha256"]),
        "report_sha256": str(sealed_report["report_sha256"]),
        "pack_artifact_id": str(pack_artifact_id),
        "pack_sha256": actual,
        "pack_member_count": len(inventory),
        "required_members": selected,
        "checks": {
            "exact_pack_identity": True,
            "report_contract_valid": True,
            "required_members_present": True,
            "path_traversal_refused": True,
            "symlinks_refused": True,
            "target_recording_bytes_used": False,
            "blind_audio_inference_used": False,
            "community_symbolic_sources_used": True,
        },
        "passed_organs": [
            "community_symbolic_target_witness",
            "symbolic_harmony_map",
            "proof_carrying_adjacent_move",
            "mixscore_source_transport_handoff",
        ],
        "blocked_organs": [
            "cephalopod_blind_audio_inference",
            "cross_modal_score_audio_convergence",
            "rights_eligible_private_library_realization",
            "review_patch_circulation",
            "campaign_evolution",
        ],
        "whole_organism_passed": False,
        "verification_mode": "exact proof-pack identity plus machine-checked report contract; no blind-audio inference",
    }
    receipt["receipt_sha256"] = specimen_sha256_json(receipt)
    if output_path not in {None, ""}:
        specimen_write_json_atomic(output_path, receipt)
    return receipt


__all__ = [
    "COMMUNITY_REPORT_SCHEMA_VERSION",
    "COMMUNITY_REPORT_KIND",
    "COMMUNITY_PACK_RECEIPT_SCHEMA_VERSION",
    "COMMUNITY_PACK_RECEIPT_KIND",
    "COMMUNITY_EVIDENCE_TIER",
    "community_validate_report",
    "community_zip_inventory",
    "community_bind_pack",
]
