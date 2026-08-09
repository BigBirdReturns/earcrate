from __future__ import annotations

"""Source-free cloud specimen intake for the EarCrate Homelab Provider Arcade.

This module intentionally does not execute MIR, ML, DSP, or audio providers. It
verifies cloud-authored source-free case archives, normalizes them into one sealed
specimen suite, binds exact local source bytes without copying them, compiles a
specimen-scoped campaign from the current catalog and audit, and seals trial
receipts. Trial receipts are evidence about one provider on one specimen. They are
not provider adoption decisions and cannot satisfy the existing target lifecycle
without a separate explicit promotion and review step.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
import zipfile

SCHEMA_VERSION = 1
HASH_FIELDS = {
    "earcrate_homelab_catalog": "catalog_sha256",
    "earcrate_homelab_node_receipt": "node_sha256",
    "earcrate_homelab_audit": "audit_sha256",
    "earcrate_homelab_campaign": "campaign_sha256",
    "earcrate_homelab_stage_receipt": "receipt_sha256",
    "earcrate_homelab_audition_ledger": "ledger_sha256",
    "earcrate_homelab_adoption_decision": "decision_sha256",
    "earcrate_homelab_review_assignment": "assignment_sha256",
    "earcrate_homelab_private_assignment_authority": "authority_sha256",
    "earcrate_homelab_review_submission": "submission_sha256",
    "earcrate_homelab_store_snapshot": "snapshot_sha256",
    "earcrate_homelab_backup_manifest": "manifest_sha256",
    "earcrate_homelab_restore_receipt": "receipt_sha256",
    "earcrate_homelab_public_export_manifest": "manifest_sha256",
    "earcrate_homelab_public_projection": "projection_sha256",
    "earcrate_homelab_fixture_binding": "binding_sha256",
    "earcrate_homelab_specimen_suite": "suite_sha256",
    "earcrate_homelab_specimen_intake_receipt": "receipt_sha256",
    "earcrate_homelab_specimen_source_binding": "binding_sha256",
    "earcrate_homelab_specimen_trial_receipt": "receipt_sha256",
}

FORBIDDEN_BUNDLED_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".ape", ".au", ".caf", ".flac",
    ".m4a", ".mid", ".midi", ".mp3", ".mp4", ".ogg", ".opus", ".pdf",
    ".snd", ".wav", ".wave", ".wma",
}
TEXT_EXTENSIONS = {
    ".bat", ".cmd", ".csv", ".json", ".md", ".musicxml", ".ps1", ".py",
    ".sh", ".txt", ".xml", ".yaml", ".yml",
}
_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

CASE_OVERRIDES: dict[str, dict[str, Any]] = {
    "beggin-four-seasons-x-maneskin-handoff": {
        "canonical_case_id": "beggin-four-seasons-x-maneskin-handoff",
        "specimen_class": "same_composition_different_era",
        "control_question": (
            "Does the modern percussion chassis increase physical force while Frankie Valli "
            "remains the performance authority and every terminal 'beggin you' phrase lands?"
        ),
    },
    "animal-katseye-x-britney-toxic-handoff": {
        "canonical_case_id": "animal-katseye-x-britney-toxic-handoff",
        "specimen_class": "cross_song_compatible_pop_production_grammar",
        "control_question": (
            "Does Animal's modern rhythmic body make Toxic hit harder while Toxic's negative "
            "space, punctuation, and vocal identity remain intact?"
        ),
    },
    "earcrate_sombr_yellow_handoff_prep_v0.1.0": {
        "canonical_case_id": "sombr-my-body-isnt-ready-x-coldplay-yellow-handoff",
        "specimen_class": "cross_song_arrangement_ancestry",
        "control_question": (
            "Does the target chorus acquire Yellow-like width and inevitability without "
            "becoming a Coldplay imitation?"
        ),
        "title": "sombr 'My Body Isn't Ready' × Coldplay 'Yellow' arrangement-ancestry study",
        "source_roles": [
            {
                "source_id": "sombr_my_body_isnt_ready",
                "artist": "sombr",
                "title": "My Body Isn't Ready",
                "recording_role": "target song, lead vocal, modern production body",
                "identity_status": "exact local edition unresolved until binding",
            },
            {
                "source_id": "coldplay_yellow",
                "artist": "Coldplay",
                "title": "Yellow",
                "recording_role": "arrangement-ancestry, guitar-width, live-band-lift donor",
                "identity_status": "exact local edition unresolved until binding",
            },
        ],
    },
    "sombr_yellow_v0.1.0": {
        "canonical_case_id": "sombr-my-body-isnt-ready-x-coldplay-yellow-handoff",
        "specimen_class": "cross_song_arrangement_ancestry",
        "control_question": (
            "Does the target chorus acquire Yellow-like width and inevitability without "
            "becoming a Coldplay imitation?"
        ),
    },
}

SPECIMEN_ROLE_ORDER = (
    "custody", "fingerprint", "beat_grid", "tonality", "separation",
    "drum_separation", "transcription", "structure", "stretch",
    "render_reconstruction", "signal_evaluation", "symbolic_ingest",
    "adjudication", "other",
)

ROLE_DEPENDENCIES = {
    "custody": (),
    "fingerprint": ("custody",),
    "beat_grid": ("custody",),
    "tonality": ("custody",),
    "separation": ("custody",),
    "drum_separation": ("separation",),
    "transcription": ("separation", "beat_grid"),
    "structure": ("beat_grid", "tonality"),
    "stretch": ("beat_grid", "tonality"),
    "render_reconstruction": ("beat_grid", "tonality"),
    "signal_evaluation": ("render_reconstruction",),
    "symbolic_ingest": ("custody",),
    "adjudication": ("beat_grid", "tonality"),
    "other": ("custody",),
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").lower()))


def seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HASH_FIELDS.get(kind)
    if not field:
        raise ValueError(f"unknown Homelab object kind: {kind!r}")
    value.pop(field, None)
    value[field] = sha256_bytes(canonical_json_bytes(value))
    return value


def validate_seal(payload: Mapping[str, Any]) -> str:
    value = deepcopy(dict(payload))
    kind = str(value.get("kind") or "")
    field = HASH_FIELDS.get(kind)
    if not field:
        raise ValueError(f"unknown Homelab object kind: {kind!r}")
    if int(value.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError("unsupported Homelab schema version")
    claimed = str(value.pop(field, "")).lower()
    if not is_sha256(claimed):
        raise ValueError(f"invalid or missing {field}")
    actual = sha256_bytes(canonical_json_bytes(value))
    if actual != claimed:
        raise ValueError(f"{field} mismatch: expected {claimed}, computed {actual}")
    return claimed


def write_json(path: str | Path, value: Mapping[str, Any], *, exclusive: bool = True) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "xb" if exclusive else "wb"
    body = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with target.open(mode) as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _slug(value: Any) -> str:
    normalized = _normalize_text(value).replace(" ", "-")
    return normalized.strip("-") or "unnamed"


def _tokens(value: Any) -> set[str]:
    stop = {
        "and", "or", "the", "a", "an", "plus", "optional", "available", "if",
        "provider", "providers", "inference", "family", "custom", "local", "exact",
        "candidate", "candidates", "via", "only", "with", "from", "as", "of",
    }
    return {token for token in _normalize_text(value).split() if len(token) > 1 and token not in stop}


def _parse_sidecar(sidecar: Path, archive: Path) -> str:
    line = next((line for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()), "")
    parts = line.split()
    if not parts or not is_sha256(parts[0]):
        raise ValueError(f"invalid SHA-256 sidecar: {sidecar}")
    if len(parts) > 1 and Path(parts[-1].lstrip("*")).name != archive.name:
        raise ValueError(f"sidecar names another archive: {sidecar}")
    return parts[0].lower()


def _safe_member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe ZIP member path: {name!r}")
    if path.parts and (_WINDOWS_DRIVE_RE.match(path.parts[0]) or ":" in path.parts[0]):
        raise ValueError(f"drive-qualified ZIP member path refused: {name!r}")
    return path


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _decode_text(name: str, data: bytes) -> str | None:
    if PurePosixPath(name).suffix.casefold() not in TEXT_EXTENSIONS:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"UTF-8 text expected in {name}: {exc}") from exc


def _find_one(names: set[str], root: str, candidates: Sequence[str], *, required: bool = False) -> str | None:
    for relative in candidates:
        candidate = f"{root}/{relative}"
        if candidate in names:
            return candidate
    if required:
        raise ValueError("required case member missing: " + " or ".join(candidates))
    return None


def _json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    value = json.loads(archive.read(name).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required in {name}")
    return value


def _provider_labels(job: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("provider", "providers"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            labels.append(value.strip())
        elif isinstance(value, list):
            labels.extend(str(item).strip() for item in value if str(item).strip())
    return labels


def classify_job_role(job: Mapping[str, Any]) -> str:
    haystack = _normalize_text(
        " ".join(
            str(job.get(key) or "")
            for key in ("capability", "purpose", "provider", "providers", "output", "outputs", "job_id")
        )
    )
    if any(term in haystack for term in ("human review", "adjudication", "cross modal evidence")):
        return "adjudication"
    if any(term in haystack for term in ("source preflight", "source identity", "ffprobe", "signal scan", "ebur128", "astats")):
        return "custody"
    if any(term in haystack for term in ("fingerprint", "chromaprint", "fpcalc")):
        return "fingerprint"
    if any(term in haystack for term in ("beat", "downbeat", "tempo", "meter", "rhythm")) and "stretch" not in haystack:
        return "beat_grid"
    if any(term in haystack for term in ("key", "tuning", "chroma", "chord", "pitch", "f0")) and "transcription" not in haystack:
        return "tonality"
    if any(term in haystack for term in ("drum substem", "drum separation", "percussion separation", "larsnet")):
        return "drum_separation"
    if any(term in haystack for term in ("source separation", "stem candidate", "roformer", "demucs", "audio separator")):
        return "separation"
    if any(term in haystack for term in ("transcription", "note observations", "drum event", "onset classifier", "basic pitch", "omnizart", "midi observation")):
        return "transcription"
    if any(term in haystack for term in ("structure", "section boundary", "segmentation", "recurrence")):
        return "structure"
    if any(term in haystack for term in ("stretch", "time scale", "resample", "key lock", "rubber band")):
        return "stretch"
    if any(term in haystack for term in ("rack reconstruction", "rack compiler", "render", "mix style", "dsp")):
        return "render_reconstruction"
    if any(term in haystack for term in ("signal eval", "signal evaluator", "independent evaluator", "quality metric")):
        return "signal_evaluation"
    if any(term in haystack for term in ("score", "musicxml", "symbolic", "midi ingest")):
        return "symbolic_ingest"
    return "other"


def _normalize_jobs(raw: Mapping[str, Any], case_id: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, original in enumerate(raw.get("jobs") or []):
        if not isinstance(original, Mapping):
            continue
        job = dict(original)
        labels = _provider_labels(job)
        job_id = str(job.get("job_id") or f"P{index + 1:02d}-{_slug(labels[0] if labels else job.get('purpose') or 'job')}")
        normalized.append(
            {
                "job_id": job_id,
                "case_id": case_id,
                "role": classify_job_role(job),
                "capability": str(job.get("capability") or ""),
                "purpose": str(job.get("purpose") or job.get("capability") or ""),
                "provider_labels": labels,
                "priority": str(job.get("priority") or job.get("gate") or "candidate"),
                "inputs": deepcopy(job.get("inputs") or []),
                "outputs": deepcopy(job.get("outputs") or job.get("output") or []),
                "canonical_write_allowed": bool(job.get("canonical_write", False)),
                "source_job": job,
            }
        )
    return normalized


def _load_auditions(archive: zipfile.ZipFile, names: set[str], root: str) -> list[dict[str, Any]]:
    json_name = _find_one(names, root, ("auditions/audition_matrix.json",))
    if json_name:
        value = _json_member(archive, json_name)
        return [dict(row) for row in value.get("auditions") or [] if isinstance(row, Mapping)]
    csv_name = _find_one(names, root, ("arrangement/audition_matrix.csv", "earcrate/provider_audition_matrix.csv"))
    if csv_name:
        text = archive.read(csv_name).decode("utf-8-sig")
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    hypotheses = _find_one(names, root, ("analysis/musical_hypotheses.json",))
    if hypotheses:
        value = _json_member(archive, hypotheses)
        rows = value.get("hypotheses") or value.get("auditions") or []
        return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def inspect_case_archive(
    archive_path: str | Path,
    *,
    expected_sha256: str | None = None,
    max_members: int = 5000,
    max_uncompressed_bytes: int = 100 * 1024 * 1024,
) -> dict[str, Any]:
    source = Path(archive_path).expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"archive must be a regular non-symlink file: {source}")
    archive_sha = sha256_file(source)
    if expected_sha256 and archive_sha != str(expected_sha256).lower():
        raise ValueError(f"archive SHA-256 mismatch for {source.name}")

    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > max_members:
            raise ValueError(f"invalid ZIP member count for {source.name}: {len(infos)}")
        roots: set[str] = set()
        names: set[str] = set()
        total_uncompressed = 0
        member_rows: list[dict[str, Any]] = []
        secret_hits: list[str] = []
        forbidden_media: list[str] = []
        for info in infos:
            path = _safe_member_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
            roots.add(path.parts[0])
            if _is_zip_symlink(info):
                raise ValueError(f"symlink ZIP member refused: {info.filename}")
            if info.is_dir():
                continue
            names.add(info.filename)
            total_uncompressed += int(info.file_size)
            if total_uncompressed > max_uncompressed_bytes:
                raise ValueError(f"archive exceeds uncompressed-size limit: {source.name}")
            if info.compress_size and info.file_size > max(20 * 1024 * 1024, info.compress_size * 250):
                raise ValueError(f"suspicious compression ratio in {info.filename}")
            data = archive.read(info)
            digest = sha256_bytes(data)
            suffix = PurePosixPath(info.filename).suffix.casefold()
            if suffix in FORBIDDEN_BUNDLED_EXTENSIONS:
                forbidden_media.append(info.filename)
            text = _decode_text(info.filename, data)
            if text is not None:
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(text):
                        secret_hits.append(info.filename)
                        break
                if suffix == ".json":
                    json.loads(text)
                elif suffix in {".xml", ".musicxml"}:
                    import xml.etree.ElementTree as ET
                    ET.fromstring(text)
            member_rows.append(
                {
                    "path": info.filename,
                    "sha256": digest,
                    "bytes": int(info.file_size),
                    "compressed_bytes": int(info.compress_size),
                }
            )
        if len(roots) != 1:
            raise ValueError(f"archive must contain exactly one root directory: {sorted(roots)}")
        root = next(iter(roots))
        if forbidden_media:
            raise ValueError("source-free archive contains forbidden media/score: " + ", ".join(forbidden_media))
        if secret_hits:
            raise ValueError("credential-like material detected in: " + ", ".join(sorted(set(secret_hits))))

        checksum_name = _find_one(names, root, ("checksums.sha256", "CHECKSUMS.sha256"), required=True)
        assert checksum_name is not None
        checksum_text = archive.read(checksum_name).decode("utf-8")
        declared: dict[str, str] = {}
        for line_number, line in enumerate(checksum_text.splitlines(), 1):
            if not line.strip():
                continue
            if "  " not in line:
                raise ValueError(f"invalid checksum line {line_number} in {checksum_name}")
            digest, relative = line.split("  ", 1)
            digest = digest.strip().lower()
            relative = relative.strip().lstrip("*")
            if not is_sha256(digest):
                raise ValueError(f"invalid checksum digest on line {line_number} in {checksum_name}")
            relative_path = _safe_member_path(relative)
            normalized = str(relative_path)
            if normalized in declared:
                raise ValueError(f"duplicate checksum entry: {normalized}")
            declared[normalized] = digest
        actual_by_relative = {
            str(PurePosixPath(row["path"]).relative_to(root)): row["sha256"]
            for row in member_rows
            if row["path"] != checksum_name
        }
        missing_checksums = sorted(set(actual_by_relative) - set(declared))
        unknown_checksums = sorted(set(declared) - set(actual_by_relative))
        mismatches = sorted(
            relative for relative in set(declared) & set(actual_by_relative)
            if declared[relative] != actual_by_relative[relative]
        )
        if missing_checksums or unknown_checksums or mismatches:
            raise ValueError(
                f"internal checksum failure in {source.name}: missing={missing_checksums[:5]}, "
                f"unknown={unknown_checksums[:5]}, mismatches={mismatches[:5]}"
            )

        case_name = _find_one(names, root, ("case.json", "manifest/case_manifest.json"), required=True)
        assert case_name is not None
        case = _json_member(archive, case_name)
        source_case_id = str(case.get("case_id") or root)
        override = CASE_OVERRIDES.get(source_case_id, {})
        canonical_case_id = str(override.get("canonical_case_id") or source_case_id)
        title = str(override.get("title") or case.get("title") or canonical_case_id)
        source_roles = [dict(row) for row in case.get("recordings") or [] if isinstance(row, Mapping)]
        if not source_roles:
            source_roles = deepcopy(list(override.get("source_roles") or []))
        for row in source_roles:
            if "identity_status" not in row:
                row["identity_status"] = "exact local edition unresolved until binding"

        jobs_name = _find_one(names, root, ("earcrate/provider_jobs.json", "providers/provider_jobs.json"), required=True)
        assert jobs_name is not None
        jobs = _normalize_jobs(_json_member(archive, jobs_name), canonical_case_id)
        auditions = _load_auditions(archive, names, root)
        member_manifest_sha = sha256_bytes(canonical_json_bytes(sorted(member_rows, key=lambda row: row["path"])))
        declared_created = str(case.get("created_at_utc") or case.get("created") or "2026-08-08")
        return {
            "canonical_case_id": canonical_case_id,
            "source_case_id": source_case_id,
            "title": title,
            "specimen_class": str(override.get("specimen_class") or "unclassified_cloud_specimen"),
            "control_question": str(override.get("control_question") or "Does this case produce a reviewed, identity-preserving musical improvement?"),
            "declared_created": declared_created,
            "archive_name": source.name,
            "archive_sha256": archive_sha,
            "archive_bytes": int(source.stat().st_size),
            "archive_root": root,
            "case_manifest_path": case_name,
            "provider_jobs_path": jobs_name,
            "checksum_manifest_path": checksum_name,
            "member_count": len(member_rows),
            "uncompressed_bytes": total_uncompressed,
            "member_manifest_sha256": member_manifest_sha,
            "source_free": True,
            "source_roles": source_roles,
            "provider_jobs": jobs,
            "auditions": auditions,
            "hard_constraints": deepcopy(case.get("hard_constraints") or []),
            "creative_objectives": deepcopy(case.get("creative_objectives") or []),
            "case_payload_sha256": sha256_bytes(canonical_json_bytes(case)),
            "checksum_coverage": {
                "checked_files": len(declared),
                "missing": 0,
                "unknown": 0,
                "mismatches": 0,
            },
            "boundary": {
                "source_audio_included": False,
                "copyrighted_score_pages_included": False,
                "provider_execution_performed": False,
                "human_acceptance_claimed": False,
            },
        }


def _role_policy_sha(policy: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(policy))


def build_specimen_suite(
    archives: Sequence[str | Path],
    *,
    sidecars: Mapping[str, str | Path] | None = None,
    role_policy: Mapping[str, Any],
    suite_id: str = "earcrate-cloud-organ-transplant-suite-v1",
) -> dict[str, Any]:
    if not archives:
        raise ValueError("at least one cloud specimen archive is required")
    sidecar_map = {str(key): Path(value) for key, value in dict(sidecars or {}).items()}
    cases: list[dict[str, Any]] = []
    for raw in archives:
        archive = Path(raw).expanduser().resolve()
        expected: str | None = None
        sidecar = sidecar_map.get(archive.name)
        if sidecar:
            expected = _parse_sidecar(sidecar, archive)
        cases.append(inspect_case_archive(archive, expected_sha256=expected))
    case_ids = [str(case["canonical_case_id"]) for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"duplicate canonical case IDs: {case_ids}")
    cases.sort(key=lambda row: str(row["canonical_case_id"]))
    source_requirements = [
        {
            "case_id": case["canonical_case_id"],
            "source_id": str(source.get("source_id") or source.get("id") or _slug(f"{source.get('artist')} {source.get('title')}")),
            "artist": source.get("artist"),
            "title": source.get("title"),
            "recording_role": source.get("recording_role"),
            "identity_status": source.get("identity_status"),
        }
        for case in cases
        for source in case.get("source_roles") or []
    ]
    declared_dates = sorted(str(case.get("declared_created") or "") for case in cases)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "earcrate_homelab_specimen_suite",
        "suite_id": suite_id,
        "compiled_from_declared_date": declared_dates[-1] if declared_dates else "",
        "role_policy_sha256": _role_policy_sha(role_policy),
        "cases": cases,
        "source_requirements": source_requirements,
        "summary": {
            "cases": len(cases),
            "archives": len(cases),
            "source_bindings_required": len(source_requirements),
            "provider_jobs": sum(len(case.get("provider_jobs") or []) for case in cases),
            "auditions": sum(len(case.get("auditions") or []) for case in cases),
            "archive_bytes": sum(int(case.get("archive_bytes") or 0) for case in cases),
            "archive_members": sum(int(case.get("member_count") or 0) for case in cases),
        },
        "boundary": {
            "source_free": True,
            "private_source_paths_present": False,
            "provider_processes_executed": False,
            "trial_success_is_provider_adoption": False,
            "human_review_remains_acceptance_authority": True,
            "original_archives_remain_independently_hashed": True,
        },
    }
    return seal(payload)


def _refuse_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked path component refused: {current}")


def _atomic_promote_directory(temporary: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.replace(temporary, destination)
    if os.name != "nt":
        descriptor = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def stage_specimen_suite(
    suite: Mapping[str, Any],
    *,
    archive_directory: str | Path,
    destination: str | Path,
    role_policy: Mapping[str, Any],
) -> dict[str, Any]:
    suite_identity = validate_seal(suite)
    if suite.get("kind") != "earcrate_homelab_specimen_suite":
        raise ValueError("stage requires an EarCrate Homelab specimen suite")
    if str(suite.get("role_policy_sha256")) != _role_policy_sha(role_policy):
        raise ValueError("role policy does not match the specimen suite")
    source_dir = Path(archive_directory).expanduser().resolve()
    destination_path = Path(destination).expanduser().absolute()
    _refuse_symlink_components(destination_path.parent)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise FileExistsError(f"staging destination already exists: {destination_path}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination_path.name}.tmp-", dir=destination_path.parent))
    try:
        (temporary / "archives").mkdir()
        (temporary / "cases").mkdir()
        extracted: list[dict[str, Any]] = []
        for case in suite.get("cases") or []:
            archive = source_dir / str(case["archive_name"])
            if archive.is_symlink() or not archive.is_file():
                raise ValueError(f"required archive missing or unsafe: {archive}")
            if sha256_file(archive) != case["archive_sha256"]:
                raise ValueError(f"archive changed since suite compilation: {archive.name}")
            copied = temporary / "archives" / archive.name
            shutil.copyfile(archive, copied)
            if sha256_file(copied) != case["archive_sha256"]:
                raise ValueError(f"archive copy verification failed: {archive.name}")
            case_root = temporary / "cases" / str(case["canonical_case_id"])
            case_root.mkdir()
            with zipfile.ZipFile(archive) as zipper:
                expected_root = str(case["archive_root"])
                for info in zipper.infolist():
                    if info.is_dir():
                        continue
                    member = _safe_member_path(info.filename)
                    if member.parts[0] != expected_root:
                        raise ValueError(f"unexpected archive root in {info.filename}")
                    relative = PurePosixPath(*member.parts[1:])
                    if not relative.parts:
                        continue
                    target = case_root.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise FileExistsError(target)
                    data = zipper.read(info)
                    with target.open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
            extracted.append(
                {
                    "case_id": case["canonical_case_id"],
                    "archive_sha256": case["archive_sha256"],
                    "case_relative_path": f"cases/{case['canonical_case_id']}",
                    "member_manifest_sha256": case["member_manifest_sha256"],
                }
            )
        write_json(temporary / "specimen-suite.json", suite)
        write_json(temporary / "provider-role-policy.json", role_policy)
        template = {
            "schema": "earcrate.homelab_specimen_source_binding_template.v1",
            "suite_sha256": suite_identity,
            "bindings": [
                {
                    **dict(row),
                    "artifact_path": "",
                    "bound_by": "operator:owner",
                    "reason": "exact local edition supplied for cloud specimen campaign",
                    "canonical_pcm_sha256": None,
                }
                for row in suite.get("source_requirements") or []
            ],
        }
        write_json(temporary / "source-bindings.template.json", template)
        receipt = seal(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "earcrate_homelab_specimen_intake_receipt",
                "staged_at": now_utc(),
                "suite_sha256": suite_identity,
                "destination_path": str(destination_path),
                "extracted_cases": extracted,
                "source_bindings_present": 0,
                "boundary": {
                    "source_media_copied": False,
                    "provider_processes_executed": False,
                    "absolute_destination_path_is_sensitive": True,
                    "intake_is_not_provider_acceptance": True,
                },
            }
        )
        write_json(temporary / "intake-receipt.json", receipt)
        checksums: list[str] = []
        for path in sorted((p for p in temporary.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
            relative = path.relative_to(temporary).as_posix()
            checksums.append(f"{sha256_file(path)}  {relative}")
        checks_path = temporary / "SHA256SUMS.txt"
        checks_body = ("\n".join(checksums) + "\n").encode("utf-8")
        with checks_path.open("xb") as handle:
            handle.write(checks_body)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_promote_directory(temporary, destination_path)
        return receipt
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _canonical_pcm_sha256(path: Path, *, ffmpeg: str = "ffmpeg") -> tuple[str, dict[str, Any]]:
    executable = shutil.which(ffmpeg) or ffmpeg
    process = subprocess.Popen(
        [executable, "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    digest = hashlib.sha256()
    decoded_bytes = 0
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
        decoded_bytes += len(chunk)
    stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr is not None else ""
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"ffmpeg canonical decode failed ({returncode}): {stderr[-2000:]}")
    return digest.hexdigest(), {
        "sample_rate": 48000,
        "channels": 2,
        "sample_format": "float32le",
        "decoded_bytes": decoded_bytes,
        "ffmpeg_executable": str(executable),
        "stderr_sha256": sha256_bytes(stderr.encode("utf-8")),
    }


def bind_specimen_source(
    suite: Mapping[str, Any],
    *,
    case_id: str,
    source_id: str,
    artifact_path: str | Path,
    bound_by: str,
    reason: str,
    canonical_pcm: bool = False,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    suite_sha = validate_seal(suite)
    if suite.get("kind") != "earcrate_homelab_specimen_suite":
        raise ValueError("source binding requires a specimen suite")
    requirement = next(
        (
            dict(row)
            for row in suite.get("source_requirements") or []
            if row.get("case_id") == case_id and row.get("source_id") == source_id
        ),
        None,
    )
    if requirement is None:
        raise ValueError(f"unknown specimen source requirement: {case_id}/{source_id}")
    if not str(bound_by).strip() or not str(reason).strip():
        raise ValueError("bound_by and reason are required")
    source = Path(artifact_path).expanduser().absolute()
    _refuse_symlink_components(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("specimen source must be a regular non-symlink file")
    before = source.stat()
    container_sha = sha256_file(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("specimen source changed while it was being hashed")
    pcm_sha: str | None = None
    pcm_receipt: dict[str, Any] | None = None
    if canonical_pcm:
        pcm_sha, pcm_receipt = _canonical_pcm_sha256(source, ffmpeg=ffmpeg)
    guessed = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_homelab_specimen_source_binding",
            "bound_at": now_utc(),
            "suite_sha256": suite_sha,
            "case_id": case_id,
            "source_id": source_id,
            "source_requirement": requirement,
            "artifact_path": str(source),
            "artifact_sha256": container_sha,
            "artifact_bytes": int(after.st_size),
            "artifact_mtime_ns": int(after.st_mtime_ns),
            "media_kind": guessed,
            "canonical_pcm_sha256": pcm_sha,
            "canonical_pcm_receipt": pcm_receipt,
            "bound_by": str(bound_by).strip(),
            "reason": str(reason).strip(),
            "boundary": {
                "source_bytes_copied": False,
                "local_path_is_sensitive": True,
                "binding_is_not_recording_identity_authority_without_listening": True,
                "binding_is_not_provider_acceptance": True,
            },
        }
    )


def validate_source_binding(binding: Mapping[str, Any], suite: Mapping[str, Any]) -> str:
    identity = validate_seal(binding)
    suite_sha = validate_seal(suite)
    if binding.get("kind") != "earcrate_homelab_specimen_source_binding":
        raise ValueError("not a specimen source binding")
    if binding.get("suite_sha256") != suite_sha:
        raise ValueError("source binding belongs to another specimen suite")
    path = Path(str(binding.get("artifact_path") or "")).expanduser().absolute()
    _refuse_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("bound specimen source is missing or unsafe")
    before = path.stat()
    if int(before.st_size) != int(binding.get("artifact_bytes") or -1):
        raise ValueError("bound specimen source size changed")
    if sha256_file(path) != binding.get("artifact_sha256"):
        raise ValueError("bound specimen source bytes changed")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("bound specimen source changed during revalidation")
    return identity


def _unwrap_authority(value: Mapping[str, Any], expected_kind: str) -> tuple[dict[str, Any], bool, str]:
    payload = dict(value)
    projection = False
    source_identity = ""
    if payload.get("kind") == "earcrate_homelab_public_projection":
        projection = True
        source_identity = str(payload.get("source_identity") or "")
        inner = payload.get("payload")
        if not isinstance(inner, Mapping):
            raise ValueError("public projection has no object payload")
        payload = dict(inner)
    if payload.get("kind") != expected_kind:
        raise ValueError(f"expected {expected_kind}, got {payload.get('kind')!r}")
    if not projection:
        source_identity = validate_seal(payload)
    elif not is_sha256(source_identity):
        raise ValueError("public projection has no valid source identity")
    return payload, projection, source_identity


def _catalog_target_map(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("target_id") or ""): dict(row) for row in catalog.get("targets") or [] if row.get("target_id")}


def _audit_target_map(audit: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("target_id") or ""): dict(row) for row in audit.get("targets") or [] if row.get("target_id")}


def _fixture_only_trial_readiness(audit_row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify whether one target can enter a specimen trial.

    The canonical Homelab audit is intentionally adoption-oriented: a target is
    blocked when its catalog fixtures are absent. A cloud specimen campaign has
    a narrower authority. Exact specimen source bindings may replace missing
    catalog fixtures for this one trial, but may never make the target adopted or
    satisfy its ordinary Homelab stage chain. Non-fixture blockers remain hard.
    """

    blockers = sorted(set(str(value) for value in audit_row.get("blockers") or [] if str(value).strip()))
    if str(audit_row.get("feasibility") or "") == "ready" and not blockers:
        return {
            "eligible": True,
            "mode": "audit_ready",
            "audit_blockers": [],
            "substituted_fixture_ids": [],
            "hard_blockers": [],
        }
    fixture_ids: list[str] = []
    hard: list[str] = []
    for blocker in blockers:
        match = re.match(r"^missing fixture ([^:]+):", blocker)
        if match:
            fixture_ids.append(match.group(1))
        else:
            hard.append(blocker)
    eligible = bool(blockers) and not hard and len(fixture_ids) == len(blockers)
    return {
        "eligible": eligible,
        "mode": "specimen_fixture_substitution" if eligible else "blocked",
        "audit_blockers": blockers,
        "substituted_fixture_ids": sorted(set(fixture_ids)) if eligible else [],
        "hard_blockers": hard,
    }


def _trial_readiness_summary(audit: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter()
    for row in audit.get("targets") or []:
        readiness = _fixture_only_trial_readiness(row)
        counts[str(readiness["mode"])] += 1
    return {
        "audit_ready_targets": int(counts.get("audit_ready", 0)),
        "specimen_fixture_substitutable_targets": int(counts.get("specimen_fixture_substitution", 0)),
        "hard_blocked_targets": int(counts.get("blocked", 0)),
    }


def _alias_groups_for_job(job: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    aliases = dict(policy.get("provider_aliases") or {})
    haystack = _normalize_text(" ".join(str(value) for value in job.get("provider_labels") or []))
    matched: list[str] = []
    for group, values in aliases.items():
        candidates = [group, *list(values or [])]
        if any(_normalize_text(candidate) in haystack for candidate in candidates if _normalize_text(candidate)):
            matched.append(str(group))
    return sorted(set(matched))


def _target_score(
    target: Mapping[str, Any],
    audit_row: Mapping[str, Any],
    job: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    profile: str,
) -> tuple[int, dict[str, Any]]:
    target_text = _normalize_text(
        " ".join(
            [
                str(target.get("target_id") or ""),
                str(target.get("display_name") or ""),
                " ".join(str(value) for value in target.get("capabilities") or []),
            ]
        )
    )
    provider_text = _normalize_text(" ".join(str(value) for value in job.get("provider_labels") or []))
    capability_text = _normalize_text(f"{job.get('capability','')} {job.get('purpose','')}")
    provider_overlap = _tokens(provider_text) & _tokens(target_text)
    capability_overlap = _tokens(capability_text) & _tokens(target_text)
    alias_groups = _alias_groups_for_job(job, policy)
    alias_hits: list[str] = []
    aliases = dict(policy.get("provider_aliases") or {})
    for group in alias_groups:
        terms = [group, *list(aliases.get(group) or [])]
        if any(_normalize_text(term) in target_text for term in terms if _normalize_text(term)):
            alias_hits.append(group)
    role = str(job.get("role") or "other")
    role_terms = set(_normalize_text(value) for value in (policy.get("role_capability_terms") or {}).get(role) or [])
    target_tokens = _tokens(target_text)
    role_hits = sorted(term for term in role_terms if any(token in target_tokens for token in _tokens(term)))
    score = len(alias_hits) * 300 + len(provider_overlap) * 30 + len(capability_overlap) * 12 + len(role_hits) * 15
    target_class = str(target.get("target_class") or "")
    requirements = dict(target.get("requirements") or {})
    if role in {"custody", "render_reconstruction", "signal_evaluation"} and target_class == "adopted_core":
        score += 25
    if profile != "full" and str(requirements.get("network") or "none") != "none":
        score -= 40
    if profile != "full" and bool(requirements.get("manual_probe")):
        score -= 30
    return score, {
        "score": score,
        "alias_hits": alias_hits,
        "provider_token_hits": sorted(provider_overlap),
        "capability_token_hits": sorted(capability_overlap),
        "role_hits": role_hits,
    }


def select_case_targets(
    case: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    audit: Mapping[str, Any],
    policy: Mapping[str, Any],
    profile: str,
) -> list[dict[str, Any]]:
    profiles = dict(policy.get("profiles") or {})
    if profile not in profiles:
        raise ValueError(f"unknown tournament profile: {profile}")
    caps = {str(key): int(value) for key, value in dict(profiles[profile].get("role_caps") or {}).items()}
    targets = _catalog_target_map(catalog)
    audit_rows = _audit_target_map(audit)
    selected: list[dict[str, Any]] = []
    used_by_role: dict[str, set[str]] = defaultdict(set)
    for job in case.get("provider_jobs") or []:
        role = str(job.get("role") or "other")
        cap = int(caps.get(role, 0))
        if cap <= 0:
            continue
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for target_id, target in targets.items():
            audit_row = audit_rows.get(target_id) or {}
            readiness = _fixture_only_trial_readiness(audit_row)
            if not readiness["eligible"]:
                continue
            score, evidence = _target_score(target, audit_row, job, policy, profile=profile)
            evidence["trial_readiness_mode"] = readiness["mode"]
            evidence["audit_blockers"] = readiness["audit_blockers"]
            evidence["substituted_fixture_ids"] = readiness["substituted_fixture_ids"]
            evidence["provider_trial_does_not_complete_catalog_fixture_stage"] = True
            if score > 0:
                ranked.append((score, target_id, evidence))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        if profile == "full":
            chosen_rows = ranked[:cap]
        else:
            chosen_rows = [row for row in ranked if row[1] not in used_by_role[role]][: max(0, cap - len(used_by_role[role]))]
            if not chosen_rows and ranked and len(used_by_role[role]) < cap:
                chosen_rows = ranked[:1]
        for score, target_id, evidence in chosen_rows:
            used_by_role[role].add(target_id)
            selected.append(
                {
                    "case_id": case["canonical_case_id"],
                    "job_id": job["job_id"],
                    "role": role,
                    "target_id": target_id,
                    "target_manifest_sha256": targets[target_id].get("target_manifest_sha256"),
                    "assigned_node_sha256": audit_rows.get(target_id, {}).get("assigned_node_sha256") or (audit.get("node_sha256s") or [None])[0],
                    "selection_score": score,
                    "selection_evidence": evidence,
                    "provider_job": deepcopy(job),
                }
            )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in selected:
        unique[(str(row["job_id"]), str(row["role"]), str(row["target_id"]))] = row
    return sorted(unique.values(), key=lambda row: (SPECIMEN_ROLE_ORDER.index(row["role"]) if row["role"] in SPECIMEN_ROLE_ORDER else 999, row["job_id"], row["target_id"]))


def _binding_index(bindings: Sequence[Mapping[str, Any]], suite: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in bindings:
        binding = dict(raw)
        validate_source_binding(binding, suite)
        key = (str(binding["case_id"]), str(binding["source_id"]))
        if key in result and result[key]["binding_sha256"] != binding["binding_sha256"]:
            raise ValueError(f"multiple current bindings for {key[0]}/{key[1]}")
        result[key] = binding
    return result


def compile_specimen_campaign(
    suite: Mapping[str, Any],
    *,
    catalog_object: Mapping[str, Any],
    audit_object: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    profile: str = "core",
    case_ids: Sequence[str] = (),
) -> dict[str, Any]:
    suite_sha = validate_seal(suite)
    catalog, catalog_projection, catalog_identity = _unwrap_authority(catalog_object, "earcrate_homelab_catalog")
    audit, audit_projection, audit_identity = _unwrap_authority(audit_object, "earcrate_homelab_audit")
    if str(audit.get("catalog_sha256") or "") != str(catalog.get("catalog_sha256") or catalog_identity):
        raise ValueError("audit and catalog identities do not reconcile")
    if str(suite.get("role_policy_sha256")) != _role_policy_sha(policy):
        raise ValueError("role policy does not match suite")
    available_cases = {str(row.get("canonical_case_id") or ""): dict(row) for row in suite.get("cases") or []}
    requested_case_ids = sorted(set(str(value) for value in case_ids if str(value).strip()))
    unknown_case_ids = sorted(set(requested_case_ids) - set(available_cases))
    if unknown_case_ids:
        raise ValueError("unknown specimen case IDs: " + ", ".join(unknown_case_ids))
    selected_case_set = set(requested_case_ids) if requested_case_ids else set(available_cases)
    selected_cases = [
        dict(row) for row in suite.get("cases") or []
        if str(row.get("canonical_case_id") or "") in selected_case_set
    ]
    if not selected_cases:
        raise ValueError("specimen campaign selected no cases")
    selected_bindings = [
        dict(value) for value in bindings
        if str(dict(value).get("case_id") or "") in selected_case_set
    ]
    binding_by_key = _binding_index(selected_bindings, suite)
    authority_is_projection = catalog_projection or audit_projection
    tasks: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    all_selected: list[dict[str, Any]] = []
    provider_job_dispositions: list[dict[str, Any]] = []

    for case in selected_cases:
        case_id = str(case["canonical_case_id"])
        case_slug = _slug(case_id)
        required_sources = [
            dict(row) for row in suite.get("source_requirements") or [] if row.get("case_id") == case_id
        ]
        missing_bindings: list[dict[str, Any]] = []
        current_binding_ids: list[str] = []
        prerequisite_ids: list[str] = []
        for requirement in required_sources:
            source_id = str(requirement["source_id"])
            binding = binding_by_key.get((case_id, source_id))
            if binding is None:
                task_id = f"case.{case_slug}.source.{_slug(source_id)}.bind"
                tasks.append(
                    {
                        "task_id": task_id,
                        "target_id": "cloud-specimen-intake",
                        "task_type": "specimen_prerequisite",
                        "stage": "source_binding",
                        "status": "blocked",
                        "assigned_node_sha256": None,
                        "resource": "operator",
                        "reason": f"bind exact local bytes for {case_id}/{source_id}",
                        "depends_on": [],
                        "case_id": case_id,
                        "source_id": source_id,
                        "specimen_suite_sha256": suite_sha,
                        "required_output_kinds": ["earcrate_homelab_specimen_source_binding"],
                    }
                )
                prerequisite_ids.append(task_id)
                missing_bindings.append(requirement)
            else:
                current_binding_ids.append(str(binding["binding_sha256"]))

        selected = select_case_targets(case, catalog=catalog, audit=audit, policy=policy, profile=profile)
        all_selected.extend(selected)
        selected_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            selected_by_job[str(row["job_id"])].append(row)
        profile_caps = {
            str(key): int(value)
            for key, value in dict(((policy.get("profiles") or {}).get(profile) or {}).get("role_caps") or {}).items()
        }
        for job in case.get("provider_jobs") or []:
            job_id = str(job.get("job_id") or "")
            chosen = selected_by_job.get(job_id) or []
            if chosen:
                disposition = "selected"
                reason = "one or more currently trial-eligible catalog targets matched this provider job under the active profile"
            elif int(profile_caps.get(str(job.get("role") or "other"), 0)) <= 0:
                disposition = "deferred_by_profile"
                reason = f"role {job.get('role')} is outside profile {profile}"
            else:
                disposition = "unmapped_or_not_currently_feasible"
                reason = "no currently trial-eligible catalog target survived deterministic provider/capability matching"
            provider_job_dispositions.append(
                {
                    "case_id": case_id,
                    "job_id": job_id,
                    "role": job.get("role"),
                    "provider_labels": deepcopy(job.get("provider_labels") or []),
                    "disposition": disposition,
                    "reason": reason,
                    "selected_target_ids": sorted(str(row["target_id"]) for row in chosen),
                }
            )
            if profile == "full" and disposition == "unmapped_or_not_currently_feasible":
                task_id = f"case.{case_slug}.job.{_slug(job_id)}.provider-mapping"
                tasks.append(
                    {
                        "task_id": task_id,
                        "target_id": "unresolved-provider-mapping",
                        "task_type": "specimen_prerequisite",
                        "stage": "provider_mapping",
                        "status": "blocked",
                        "assigned_node_sha256": None,
                        "resource": "operator",
                        "reason": reason,
                        "depends_on": list(prerequisite_ids),
                        "case_id": case_id,
                        "specimen_suite_sha256": suite_sha,
                        "provider_job_id": job_id,
                        "provider_role": job.get("role"),
                        "required_output_kinds": ["earcrate_homelab_specimen_trial_receipt"],
                    }
                )
        tasks_by_role: dict[str, list[str]] = defaultdict(list)
        for selection in selected:
            role = str(selection["role"])
            task_id = (
                f"case.{case_slug}.job.{_slug(selection['job_id'])}."
                f"target.{_slug(selection['target_id'])}"
            )
            dependency_roles = ROLE_DEPENDENCIES.get(role, ("custody",))
            dependencies = list(prerequisite_ids)
            for dependency_role in dependency_roles:
                dependencies.extend(tasks_by_role.get(dependency_role) or [])
            dependencies = sorted(set(dependencies))
            blocked = bool(missing_bindings or authority_is_projection)
            reason = "run the provider job against the exact bound local sources"
            if authority_is_projection:
                reason = "authoritative local catalog and audit required; cloud projection is review-only"
            elif missing_bindings:
                reason = "exact local source bindings are incomplete"
            tasks.append(
                {
                    "task_id": task_id,
                    "target_id": selection["target_id"],
                    "task_type": "specimen_trial",
                    "stage": "specimen_trial",
                    "status": "blocked" if blocked else "ready",
                    "assigned_node_sha256": selection.get("assigned_node_sha256"),
                    "resource": (
                        "gpu-exclusive" if str(((_catalog_target_map(catalog).get(selection["target_id"]) or {}).get("requirements") or {}).get("gpu") or "none") != "none"
                        else "cpu"
                    ),
                    "reason": reason,
                    "depends_on": dependencies,
                    "case_id": case_id,
                    "specimen_suite_sha256": suite_sha,
                    "provider_job_id": selection["job_id"],
                    "provider_role": role,
                    "source_binding_sha256s": sorted(current_binding_ids),
                    "target_manifest_sha256": selection.get("target_manifest_sha256"),
                    "selection_score": selection["selection_score"],
                    "selection_evidence": selection["selection_evidence"],
                    "provider_job": selection["provider_job"],
                    "required_output_kinds": ["earcrate_homelab_specimen_trial_receipt"],
                    "evidence_contract": {
                        "source_and_output_hashes_required": True,
                        "canonical_write_allowed": False,
                        "trial_success_is_not_provider_adoption": True,
                    },
                }
            )
            tasks_by_role[role].append(task_id)

        terminal_dependencies = sorted(
            task["task_id"]
            for task in tasks
            if task.get("case_id") == case_id and task.get("task_type") == "specimen_trial"
        )
        review_id = f"case.{case_slug}.human-review"
        tasks.append(
            {
                "task_id": review_id,
                "target_id": "human-musical-review",
                "task_type": "specimen_review",
                "stage": "human_review",
                "status": "blocked" if missing_bindings or authority_is_projection else "ready",
                "assigned_node_sha256": (audit.get("node_sha256s") or [None])[0],
                "resource": "human+playback",
                "reason": case.get("control_question"),
                "depends_on": terminal_dependencies,
                "case_id": case_id,
                "specimen_suite_sha256": suite_sha,
                "source_binding_sha256s": sorted(current_binding_ids),
                "auditions": deepcopy(case.get("auditions") or []),
                "required_output_kinds": ["earcrate_homelab_specimen_trial_receipt"],
                "evidence_contract": {
                    "human_actor_required": True,
                    "candidate_and_control_hashes_required": True,
                    "trial_success_is_not_provider_adoption": True,
                },
            }
        )
        assess_id = f"case.{case_slug}.campaign-assessment"
        tasks.append(
            {
                "task_id": assess_id,
                "target_id": "earcrate-musical-adjudication",
                "task_type": "specimen_assessment",
                "stage": "assessment",
                "status": "blocked" if missing_bindings or authority_is_projection else "ready",
                "assigned_node_sha256": (audit.get("node_sha256s") or [None])[0],
                "resource": "authority",
                "reason": "record what was learned, what remains bad, and the next bounded campaign",
                "depends_on": [review_id],
                "case_id": case_id,
                "specimen_suite_sha256": suite_sha,
                "required_output_kinds": ["earcrate_homelab_specimen_trial_receipt"],
                "evidence_contract": {
                    "provider_adoption_decision_allowed": False,
                    "human_musical_verdict_must_be_preserved": True,
                },
            }
        )
        case_summaries.append(
            {
                "case_id": case_id,
                "bindings_required": len(required_sources),
                "bindings_present": len(required_sources) - len(missing_bindings),
                "selected_provider_trials": len(selected),
                "provider_jobs_total": len(case.get("provider_jobs") or []),
                "provider_jobs_selected": len(selected_by_job),
                "provider_jobs_unmapped_or_deferred": len(case.get("provider_jobs") or []) - len(selected_by_job),
                "control_question": case.get("control_question"),
            }
        )

    task_ids = [str(task["task_id"]) for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        duplicates = sorted(task_id for task_id, count in Counter(task_ids).items() if count > 1)
        raise ValueError(f"duplicate specimen campaign task IDs: {duplicates}")
    known = set(task_ids)
    for task in tasks:
        unknown = sorted(set(task.get("depends_on") or []) - known)
        if unknown:
            raise ValueError(f"task {task['task_id']} has unknown dependencies: {unknown}")
    status_counts = Counter(str(task.get("status") or "") for task in tasks)
    target_counts = Counter(str(row["target_id"]) for row in all_selected)
    campaign = {
        "schema_version": SCHEMA_VERSION,
        "kind": "earcrate_homelab_campaign",
        "created_at": now_utc(),
        "audit_sha256": str(audit.get("audit_sha256") or audit_identity),
        "catalog_sha256": str(catalog.get("catalog_sha256") or catalog_identity),
        "specimen_suite_sha256": suite_sha,
        "campaign_profile": profile,
        "campaign_class": "cloud_specimen_provider_arcade",
        "selected_case_ids": [str(row["canonical_case_id"]) for row in selected_cases],
        "trial_readiness_summary": _trial_readiness_summary(audit),
        "tasks": tasks,
        "case_summaries": case_summaries,
        "provider_job_dispositions": provider_job_dispositions,
        "selected_target_counts": dict(sorted(target_counts.items())),
        "summary": {
            "tasks": len(tasks),
            "statuses": dict(sorted(status_counts.items())),
            "cases": len(case_summaries),
            "provider_trials": sum(1 for task in tasks if task.get("task_type") == "specimen_trial"),
            "provider_jobs": len(provider_job_dispositions),
            "provider_jobs_selected": sum(1 for row in provider_job_dispositions if row["disposition"] == "selected"),
            "provider_jobs_deferred_or_unmapped": sum(1 for row in provider_job_dispositions if row["disposition"] != "selected"),
            "source_bindings_missing": sum(row["bindings_required"] - row["bindings_present"] for row in case_summaries),
            "authoritative_catalog_and_audit": not authority_is_projection,
            "audit_ready_targets": _trial_readiness_summary(audit)["audit_ready_targets"],
            "specimen_fixture_substitutable_targets": _trial_readiness_summary(audit)["specimen_fixture_substitutable_targets"],
            "hard_blocked_targets": _trial_readiness_summary(audit)["hard_blocked_targets"],
            "zero_provider_trials_requires_audit_or_mapping_repair": len(all_selected) == 0,
        },
        "completion_gate": {
            "passed": False,
            "campaign_completion_means_trials_accounted_for": True,
            "campaign_completion_means_provider_adoption": False,
            "human_review_required_for_audio_affecting_outputs": True,
            "provider_adoption_remains_separate_existing_lifecycle": True,
            "catalog_fixture_substitution_is_trial_scoped_only": True,
            "all_source_bindings_current": all(row["bindings_required"] == row["bindings_present"] for row in case_summaries),
            "authoritative_catalog_and_audit": not authority_is_projection,
            "at_least_one_provider_trial_selected": len(all_selected) > 0,
        },
        "boundary": {
            "catalog_projection_used": catalog_projection,
            "audit_projection_used": audit_projection,
            "cloud_projection_can_schedule_authoritative_execution": False,
            "trial_receipt_can_satisfy_provider_adoption_stage": False,
            "catalog_fixture_blockers_may_be_substituted_only_by_exact_specimen_bindings": True,
            "source_media_embedded": False,
        },
    }
    return seal(campaign)


def _artifact_receipts(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).expanduser().absolute()
        _refuse_symlink_components(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"trial artifact must be a regular non-symlink file: {path}")
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"trial artifact changed while hashing: {path}")
        receipts.append(
            {
                "name": path.name,
                "sha256": digest,
                "bytes": int(after.st_size),
                "media_kind": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            }
        )
    return sorted(receipts, key=lambda row: (row["name"], row["sha256"]))


def _campaign_task(campaign: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    return next((dict(row) for row in campaign.get("tasks") or [] if row.get("task_id") == task_id), None) or (_ for _ in ()).throw(KeyError(task_id))


def record_specimen_trial(
    suite: Mapping[str, Any],
    campaign: Mapping[str, Any],
    *,
    task_id: str,
    node_sha256: str | None,
    outcome: str,
    actor_id: str,
    actor_type: str,
    artifacts: Sequence[str | Path] = (),
    source_bindings: Sequence[Mapping[str, Any]] = (),
    measurements: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
    candidate_sha256: str | None = None,
    control_sha256: str | None = None,
    human_verdict: str | None = None,
) -> dict[str, Any]:
    suite_sha = validate_seal(suite)
    campaign_sha = validate_seal(campaign)
    if campaign.get("kind") != "earcrate_homelab_campaign" or campaign.get("specimen_suite_sha256") != suite_sha:
        raise ValueError("campaign is not bound to this specimen suite")
    task = _campaign_task(campaign, task_id)
    if not str(actor_id).strip() or actor_type not in {"machine", "human", "operator", "authority"}:
        raise ValueError("valid actor_id and actor_type are required")
    allowed_outcomes = {"passed", "failed", "refused", "accept", "reject", "revise", "abstain", "observed"}
    if outcome not in allowed_outcomes:
        raise ValueError(f"invalid specimen trial outcome: {outcome}")
    if task.get("task_type") == "specimen_review":
        if actor_type != "human":
            raise ValueError("specimen review receipt requires a human actor")
        if human_verdict not in {"accept", "reject", "revise", "abstain"}:
            raise ValueError("specimen review receipt requires an explicit human verdict")
        if not (is_sha256(candidate_sha256) and is_sha256(control_sha256)):
            raise ValueError("specimen review requires candidate and control SHA-256 identities")
    binding_ids: list[str] = []
    for binding in source_bindings:
        binding_ids.append(validate_source_binding(binding, suite))
    required_bindings = set(str(value) for value in task.get("source_binding_sha256s") or [])
    if required_bindings and not required_bindings.issubset(set(binding_ids)):
        raise ValueError("trial receipt does not cover every source binding required by the task")
    artifact_rows = _artifact_receipts(artifacts)
    if outcome in {"passed", "accept", "observed"} and not artifact_rows and not measurements and task.get("task_type") == "specimen_trial":
        raise ValueError("successful specimen trial requires a derived artifact or measurements")
    return seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_homelab_specimen_trial_receipt",
            "recorded_at": now_utc(),
            "suite_sha256": suite_sha,
            "campaign_sha256": campaign_sha,
            "task_id": task_id,
            "case_id": task.get("case_id"),
            "target_id": task.get("target_id"),
            "target_manifest_sha256": task.get("target_manifest_sha256"),
            "provider_job_id": task.get("provider_job_id"),
            "provider_role": task.get("provider_role"),
            "node_sha256": node_sha256,
            "outcome": outcome,
            "actor": {"actor_id": str(actor_id).strip(), "actor_type": actor_type},
            "source_binding_sha256s": sorted(set(binding_ids)),
            "derived_artifacts": artifact_rows,
            "measurements": deepcopy(dict(measurements or {})),
            "notes": [str(value) for value in notes],
            "candidate_sha256": candidate_sha256,
            "control_sha256": control_sha256,
            "human_verdict": human_verdict,
            "authority": {
                "canonical_musical_write": False,
                "provider_adoption_decision": False,
                "release_decision": False,
                "whole_organism_passage": False,
            },
            "boundary": {
                "local_artifact_paths_recorded": False,
                "source_media_embedded": False,
                "trial_receipt_is_not_provider_stage_receipt": True,
                "explicit_promotion_required_before_provider_lifecycle_use": True,
            },
        }
    )


def verify_staged_directory(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    checks = root / "SHA256SUMS.txt"
    if not checks.is_file():
        raise ValueError("staged suite has no SHA256SUMS.txt")
    failures: list[str] = []
    checked = 0
    for line in checks.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        target = root / relative
        if target.is_symlink() or not target.is_file():
            failures.append(f"missing_or_unsafe:{relative}")
        elif sha256_file(target) != digest:
            failures.append(f"hash_mismatch:{relative}")
        checked += 1
    suite = load_json(root / "specimen-suite.json")
    suite_sha = validate_seal(suite)
    receipt = load_json(root / "intake-receipt.json")
    validate_seal(receipt)
    if receipt.get("suite_sha256") != suite_sha:
        failures.append("intake_receipt_suite_mismatch")
    return {
        "ok": not failures,
        "root": str(root),
        "checked_files": checked,
        "failures": failures,
        "suite_sha256": suite_sha,
        "intake_receipt_sha256": receipt.get("receipt_sha256"),
    }


__all__ = [
    "HASH_FIELDS",
    "bind_specimen_source",
    "build_specimen_suite",
    "compile_specimen_campaign",
    "inspect_case_archive",
    "load_json",
    "record_specimen_trial",
    "seal",
    "select_case_targets",
    "sha256_file",
    "stage_specimen_suite",
    "validate_seal",
    "validate_source_binding",
    "verify_staged_directory",
    "write_json",
]
