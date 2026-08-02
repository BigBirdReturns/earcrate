from __future__ import annotations

"""Cryptographically committed blind A/B review preparation and adjudication."""

from copy import deepcopy
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Mapping, Sequence

from earcrate.estate.homelab import record_homelab_audition
from earcrate.estate.homelab_catalog import _catalog_target
from earcrate.estate.homelab_common import (
    HOMELAB_SCHEMA_VERSION,
    _is_sha256,
    _now_utc,
    homelab_seal,
    homelab_validate_seal,
)
from earcrate.estate.model import estate_sha256_file, write_estate_json

_CHOICES = {"A", "B", "tie", "abstain"}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _submission_hmac(review_token: str, payload: Mapping[str, Any]) -> str:
    return hmac.new(review_token.encode("utf-8"), _canonical_bytes(payload), hashlib.sha256).hexdigest()


def _refuse_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked review path refused: {current}")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_text(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with contextlib.suppress(Exception):
        path.chmod(0o600)


def _copy_verified(source: Path, target: Path, expected_sha: str, expected_bytes: int) -> None:
    _refuse_symlink_components(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"review source must be a regular non-symlink file: {source}")
    before = source.stat()
    if int(before.st_size) != expected_bytes or estate_sha256_file(source) != expected_sha:
        raise ValueError(f"review source changed before copy: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _refuse_symlink_components(target.parent)
    if target.exists():
        raise ValueError(f"review target already exists: {target}")
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=4 * 1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if int(temporary.stat().st_size) != expected_bytes or estate_sha256_file(temporary) != expected_sha:
            raise ValueError("review copy did not preserve exact bytes")
        after = source.stat()
        if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(before.st_mtime_ns):
            raise ValueError(f"review source changed during copy: {source}")
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def prepare_blind_review(
    catalog: Mapping[str, Any],
    *,
    target_id: str,
    node_sha256: str,
    reviewer_id: str,
    candidate_path: str | Path,
    control_path: str | Path,
    fixture_ids: Sequence[str],
    playback_chain: Mapping[str, Any],
    public_directory: str | Path,
    private_directory: str | Path,
) -> dict[str, Any]:
    homelab_validate_seal(catalog)
    target = _catalog_target(catalog, target_id)
    audition_stages = [stage for stage in target["required_stages"] if "audition" in stage]
    if len(audition_stages) != 1:
        raise ValueError(f"target {target_id} does not define exactly one audition stage")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    if not _is_sha256(str(node_sha256)):
        raise ValueError("node_sha256 must be a SHA-256 identity")
    if not playback_chain:
        raise ValueError("playback_chain is required")
    required_fixtures = {str(value) for value in (target.get("requirements") or {}).get("fixture_ids") or []}
    fixtures = sorted(set(str(value) for value in fixture_ids))
    if required_fixtures and not required_fixtures.issubset(fixtures):
        raise ValueError("blind review must cover every fixture required by the target manifest")

    candidate = Path(candidate_path).expanduser()
    control = Path(control_path).expanduser()
    _refuse_symlink_components(candidate)
    _refuse_symlink_components(control)
    if candidate.is_symlink() or control.is_symlink() or not candidate.is_file() or not control.is_file():
        raise ValueError("candidate and control must be regular non-symlink files")
    candidate_sha = estate_sha256_file(candidate)
    control_sha = estate_sha256_file(control)
    if candidate_sha == control_sha:
        raise ValueError("candidate and control bytes must differ")
    if candidate.suffix.casefold() != control.suffix.casefold():
        raise ValueError("candidate and control must use the same container extension")
    candidate_bytes = int(candidate.stat().st_size)
    control_bytes = int(control.stat().st_size)

    public = Path(public_directory).expanduser().absolute()
    private = Path(private_directory).expanduser().absolute()
    _refuse_symlink_components(public)
    _refuse_symlink_components(private)
    public_resolved = public.resolve()
    private_resolved = private.resolve()
    if public_resolved == private_resolved or public_resolved in private_resolved.parents or private_resolved in public_resolved.parents:
        raise ValueError("public and private review directories must be disjoint")
    if public.exists() or private.exists():
        raise ValueError("public and private review directories must not already exist")
    for parent in (public.parent, private.parent):
        parent.mkdir(parents=True, exist_ok=True)
        _refuse_symlink_components(parent)

    review_token = secrets.token_urlsafe(32)
    review_token_sha = hashlib.sha256(review_token.encode("utf-8")).hexdigest()
    candidate_option = "A" if secrets.randbelow(2) == 0 else "B"
    control_option = "B" if candidate_option == "A" else "A"
    nonce = secrets.token_hex(32)
    private_authority = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_private_assignment_authority",
            "created_at": _now_utc(),
            "catalog_sha256": catalog["catalog_sha256"],
            "target_id": target_id,
            "target_manifest_sha256": target["target_manifest_sha256"],
            "node_sha256": node_sha256,
            "reviewer_id": reviewer_id.strip(),
            "fixture_ids": fixtures,
            "nonce": nonce,
            "review_token": review_token,
            "review_token_sha256": review_token_sha,
            "option_map": {candidate_option: "candidate", control_option: "control"},
            "source_artifacts": {
                "candidate": {"sha256": candidate_sha, "bytes": candidate_bytes},
                "control": {"sha256": control_sha, "bytes": control_bytes},
            },
            "boundary": {
                "private_object": True,
                "must_not_enter_public_export": True,
                "review_token_is_private_authentication_material": True,
            },
        }
    )
    extension = candidate.suffix or ".bin"
    option_sources = {
        candidate_option: (candidate, candidate_sha, candidate_bytes),
        control_option: (control, control_sha, control_bytes),
    }
    public_options = {
        option: {
            "filename": f"option-{option}{extension.lower()}",
            "sha256": option_sources[option][1],
            "bytes": option_sources[option][2],
        }
        for option in ("A", "B")
    }
    assignment = homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_review_assignment",
            "created_at": _now_utc(),
            "catalog_sha256": catalog["catalog_sha256"],
            "target_id": target_id,
            "target_manifest_sha256": target["target_manifest_sha256"],
            "node_sha256": node_sha256,
            "reviewer_id": reviewer_id.strip(),
            "stage": audition_stages[0],
            "fixture_ids": fixtures,
            "options": public_options,
            "playback_chain": deepcopy(dict(playback_chain)),
            "private_authority_sha256": private_authority["authority_sha256"],
            "review_token_sha256": review_token_sha,
            "boundary": {
                "candidate_control_roles_withheld": True,
                "option_order_randomized": True,
                "private_authority_separate": True,
            },
        }
    )

    stage_token = secrets.token_hex(10)
    staged_public = public.with_name(f".{public.name}.tmp-{os.getpid()}-{stage_token}")
    staged_private = private.with_name(f".{private.name}.tmp-{os.getpid()}-{stage_token}")
    promoted_private = False
    promoted_public = False
    try:
        staged_public.mkdir()
        staged_private.mkdir()
        for option in ("A", "B"):
            source, digest, size = option_sources[option]
            _copy_verified(source, staged_public / public_options[option]["filename"], digest, size)
        write_estate_json(staged_public / "assignment.json", assignment)
        write_estate_json(staged_private / "assignment-authority.json", private_authority)
        _write_private_text(staged_private / "review-token.txt", review_token)
        checksums = [
            f"{public_options['A']['sha256']}  {public_options['A']['filename']}",
            f"{public_options['B']['sha256']}  {public_options['B']['filename']}",
            f"{estate_sha256_file(staged_public / 'assignment.json')}  assignment.json",
        ]
        (staged_public / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        (staged_public / "README.txt").write_text(
            "EarCrate blind review\n\nListen to option A and option B under the declared playback chain. "
            "Record A, B, tie, or abstain. Candidate/control ownership is intentionally withheld.\n",
            encoding="utf-8",
        )
        _fsync_directory(staged_public)
        _fsync_directory(staged_private)
        os.replace(staged_private, private)
        promoted_private = True
        _fsync_directory(private.parent)
        os.replace(staged_public, public)
        promoted_public = True
        _fsync_directory(public.parent)
    except Exception:
        if promoted_public and public.exists():
            shutil.rmtree(public)
        if promoted_private and private.exists():
            shutil.rmtree(private)
        raise
    finally:
        for staged in (staged_public, staged_private):
            if staged.exists():
                shutil.rmtree(staged)

    return {
        "ok": True,
        "assignment": assignment,
        "private_authority": private_authority,
        "public_directory": str(public),
        "private_directory": str(private),
        "review_token": review_token,
        "review_token_file": str(private / "review-token.txt"),
        "boundary": "give only the public directory and review token to the reviewer",
    }


def record_review_submission(
    assignment: Mapping[str, Any],
    *,
    reviewer_id: str,
    review_token: str,
    choice: str,
    dimensions: Mapping[str, Any],
    notes: Sequence[str] = (),
    authentication_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    homelab_validate_seal(assignment)
    if assignment.get("kind") != "earcrate_homelab_review_assignment":
        raise ValueError("not a Homelab review assignment")
    if reviewer_id.strip() != str(assignment.get("reviewer_id") or ""):
        raise ValueError("reviewer identity does not match the assignment")
    expected = str(assignment.get("review_token_sha256") or "")
    actual = hashlib.sha256(str(review_token).encode("utf-8")).hexdigest()
    if not secrets.compare_digest(expected, actual):
        raise PermissionError("review token mismatch")
    normalized_choice = choice if choice in {"A", "B"} else choice.casefold()
    if normalized_choice not in _CHOICES:
        raise ValueError("review choice must be A, B, tie, or abstain")
    if not dimensions:
        raise ValueError("review dimensions are required")
    if authentication_receipt_sha256 is not None and not _is_sha256(authentication_receipt_sha256):
        raise ValueError("authentication receipt identity must be SHA-256")
    body: dict[str, Any] = {
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_review_submission",
        "submitted_at": _now_utc(),
        "assignment_sha256": assignment["assignment_sha256"],
        "catalog_sha256": assignment["catalog_sha256"],
        "target_id": assignment["target_id"],
        "target_manifest_sha256": assignment["target_manifest_sha256"],
        "node_sha256": assignment["node_sha256"],
        "reviewer_id": reviewer_id.strip(),
        "fixture_ids": list(assignment.get("fixture_ids") or []),
        "review_token_sha256": expected,
        "choice": normalized_choice,
        "dimensions": deepcopy(dict(dimensions)),
        "notes": [str(note) for note in notes],
        "authentication_receipt_sha256": authentication_receipt_sha256,
        "boundary": {
            "submission_does_not_contain_private_option_map": True,
            "token_possession_is_hmac_proved": True,
        },
    }
    body["submission_proof_hmac_sha256"] = _submission_hmac(str(review_token), body)
    return homelab_seal(body)


def adjudicate_review(
    catalog: Mapping[str, Any],
    assignment: Mapping[str, Any],
    private_authority: Mapping[str, Any],
    submission: Mapping[str, Any],
) -> dict[str, Any]:
    for value in (catalog, assignment, private_authority, submission):
        homelab_validate_seal(value)
    if assignment.get("kind") != "earcrate_homelab_review_assignment":
        raise ValueError("not a public review assignment")
    if private_authority.get("kind") != "earcrate_homelab_private_assignment_authority":
        raise ValueError("not a private assignment authority")
    if submission.get("kind") != "earcrate_homelab_review_submission":
        raise ValueError("not a review submission")
    if assignment["private_authority_sha256"] != private_authority["authority_sha256"]:
        raise ValueError("public assignment does not commit this private authority")
    if submission["assignment_sha256"] != assignment["assignment_sha256"]:
        raise ValueError("submission belongs to another assignment")
    fields = ("catalog_sha256", "target_id", "target_manifest_sha256", "node_sha256", "reviewer_id")
    for field in fields:
        if assignment.get(field) != private_authority.get(field) or assignment.get(field) != submission.get(field):
            raise ValueError(f"review authority mismatch on {field}")
    if list(assignment.get("fixture_ids") or []) != list(private_authority.get("fixture_ids") or []):
        raise ValueError("private authority fixture set differs from the public assignment")
    if list(assignment.get("fixture_ids") or []) != list(submission.get("fixture_ids") or []):
        raise ValueError("submission fixture set differs from the assignment")
    if catalog["catalog_sha256"] != assignment["catalog_sha256"]:
        raise ValueError("review belongs to another catalog revision")
    target = _catalog_target(catalog, str(assignment["target_id"]))
    if target["target_manifest_sha256"] != assignment["target_manifest_sha256"]:
        raise ValueError("review target manifest is stale")
    audition_stages = [stage for stage in target["required_stages"] if "audition" in stage]
    if len(audition_stages) != 1 or assignment.get("stage") != audition_stages[0]:
        raise ValueError("review assignment stage does not match the target manifest")

    token = str(private_authority.get("review_token") or "")
    token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    if not token or token_sha != private_authority.get("review_token_sha256"):
        raise ValueError("private authority does not contain valid review authentication material")
    if token_sha != assignment.get("review_token_sha256") or token_sha != submission.get("review_token_sha256"):
        raise ValueError("review authentication commitment mismatch")
    proof = str(submission.get("submission_proof_hmac_sha256") or "")
    proof_body = deepcopy(dict(submission))
    proof_body.pop("submission_sha256", None)
    proof_body.pop("submission_proof_hmac_sha256", None)
    expected_proof = _submission_hmac(token, proof_body)
    if not _is_sha256(proof) or not hmac.compare_digest(proof, expected_proof):
        raise PermissionError("review submission does not prove possession of the assigned token")

    option_map = dict(private_authority.get("option_map") or {})
    if set(option_map) != {"A", "B"} or set(option_map.values()) != {"candidate", "control"}:
        raise ValueError("invalid private A/B option map")
    artifacts = dict(private_authority.get("source_artifacts") or {})
    public_options = dict(assignment.get("options") or {})
    if set(public_options) != {"A", "B"}:
        raise ValueError("public assignment must contain exactly options A and B")
    for option, role in option_map.items():
        public_row = dict(public_options.get(option) or {})
        private_row = dict(artifacts.get(role) or {})
        if public_row.get("sha256") != private_row.get("sha256") or int(public_row.get("bytes") or -1) != int(private_row.get("bytes") or -2):
            raise ValueError(f"public option {option} does not match the committed private artifact")
    candidate_sha = str((artifacts.get("candidate") or {}).get("sha256") or "")
    control_sha = str((artifacts.get("control") or {}).get("sha256") or "")
    if not _is_sha256(candidate_sha) or not _is_sha256(control_sha) or candidate_sha == control_sha:
        raise ValueError("private authority contains invalid candidate/control identities")

    choice = str(submission.get("choice") or "")
    if choice == "abstain":
        verdict = "abstain"
    elif choice == "tie":
        verdict = "revise"
    elif option_map.get(choice) == "candidate":
        verdict = "accept"
    elif option_map.get(choice) == "control":
        verdict = "reject"
    else:
        raise ValueError("submission choice is not present in the private option map")
    ledger = record_homelab_audition(
        catalog,
        target_id=str(assignment["target_id"]),
        node_sha256=str(assignment["node_sha256"]),
        reviewer_id=str(assignment["reviewer_id"]),
        candidate_sha256=candidate_sha,
        control_sha256=control_sha,
        verdict=verdict,
        blinded=True,
        randomized=True,
        playback_chain=dict(assignment.get("playback_chain") or {}),
        dimensions=dict(submission.get("dimensions") or {}),
        fixture_ids=list(assignment.get("fixture_ids") or []),
        notes=list(submission.get("notes") or []),
        adjudication_refs={
            "assignment_sha256": assignment["assignment_sha256"],
            "private_authority_sha256": private_authority["authority_sha256"],
            "submission_sha256": submission["submission_sha256"],
        },
    )
    ledger.pop("ledger_sha256", None)
    ledger["reviewed_at"] = submission.get("submitted_at") or assignment.get("created_at")
    ledger["assignment_sha256"] = assignment["assignment_sha256"]
    ledger["private_authority_sha256"] = private_authority["authority_sha256"]
    ledger["submission_sha256"] = submission["submission_sha256"]
    ledger["review_token_sha256"] = token_sha
    ledger["submission_proof_hmac_sha256"] = proof
    ledger["authentication_receipt_sha256"] = submission.get("authentication_receipt_sha256")
    return homelab_seal(ledger)


__all__ = ["prepare_blind_review", "record_review_submission", "adjudicate_review"]
