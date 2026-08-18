"""The only supported way to change Album One authority.

Three hand-applied transitions in one day produced three stale-copy defects: a
counter that outran its evidence, a quoted manifest seal that named a manifest that
no longer existed, and an authority block that survived the correction meant to
remove it. Every one of them was a human copying a derived value into a second
place. So derived values are no longer copied by hand.

The rules this enforces are short and worth stating plainly:

* Counters are **outputs**. They are computed from track rows and never accepted as
  inputs, so a counter cannot disagree with the states it summarizes.
* An event needs a **landed receipt** in the target tree, sealed, of the right kind,
  for the right track, naming the same object the previous state named.
* States advance in order. `system_reference` cannot precede an accepted master, an
  accepted master cannot precede a qualified one, and a qualified master cannot
  precede a selected frontier.
* Applying the same transition twice changes **zero bytes**. Idempotence is what
  makes the tool safe to re-run after fixing a document by hand.
* Nothing is written until the whole graph validates, and the result is re-read and
  re-validated afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..evidence.identity import ObjectIdentity, seal, validate_seal
from ..evidence.receipts import EvidenceError, load_sealed, verify_body_free

MANIFEST_RELATIVE = "configs/album_one/manifest.v1.json"
LEDGER_DOCUMENT = "ALBUM_ONE.md"

NONE = "none"
FRONTIER_SELECTED = "frontier_selected"
MASTER_QUALIFIED = "master_qualified"
MASTER_ACCEPTED = "master_accepted"
SYSTEM_REFERENCE_PASSED = "system_reference_passed"

STATE_ORDER = (NONE, FRONTIER_SELECTED, MASTER_QUALIFIED, MASTER_ACCEPTED,
               SYSTEM_REFERENCE_PASSED)


class LedgerTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Event:
    """One legal move, and the evidence that has to exist for it."""

    name: str
    receipt_kind: str
    requires: str
    produces: str
    verdict_field: str | None = None
    verdict_value: str | None = None


EVENTS: dict[str, Event] = {
    "frontier-selected": Event(
        "frontier-selected", "earcrate_album_one_public_frontier_receipt",
        requires=NONE, produces=FRONTIER_SELECTED),
    "master-qualified": Event(
        "master-qualified", "earcrate_album_one_public_master_receipt",
        requires=FRONTIER_SELECTED, produces=MASTER_QUALIFIED),
    "master-accepted": Event(
        "master-accepted", "earcrate_album_one_public_master_acceptance_receipt",
        requires=MASTER_QUALIFIED, produces=MASTER_ACCEPTED,
        verdict_field="verdict", verdict_value="ACCEPT_MASTER"),
    "system-reference-passed": Event(
        "system-reference-passed", "earcrate_album_one_public_system_reference_receipt",
        requires=MASTER_ACCEPTED, produces=SYSTEM_REFERENCE_PASSED,
        verdict_field="verdict", verdict_value="PASSED"),
}

# Where the counters are projected into prose. Only the number is rewritten, so the
# sentence around it stays whatever a human wrote. A file listed here is also
# verified afterwards: a projection that silently fails to match is the whole defect
# class this replaces.
COUNTER_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (LEDGER_DOCUMENT, "masters", r"(?<=- Album masters accepted: \*\*)\d+(?=/7\*\*)"),
    (LEDGER_DOCUMENT, "references", r"(?<=- System references completed: \*\*)\d+(?=/7\*\*)"),
    ("README.md", "masters", r"(?<=- \*\*)\d+(?=/7\*\* album masters are owner-accepted)"),
    ("README.md", "references", r"(?<=- \*\*)\d+(?=/7\*\* references are completed)"),
    ("PRODUCT.md", "masters", r"(?<=- Album masters accepted: \*\*)\d+(?=/7\*\*)"),
    ("PRODUCT.md", "references", r"(?<=- System references completed: \*\*)\d+(?=/7\*\*)"),
    ("PRODUCT.md", "masters", r"(?<=\| Accepted Album One masters \| \*\*)\d+(?=/7\*\*)"),
    ("PRODUCT.md", "references",
     r"(?<=\| Withheld-answer system references \| \*\*)\d+(?=/7\*\*)"),
    ("MILESTONES.md", "masters", r"(?<=\*\*)\d+(?=/7 accepted album masters\*\*)"),
    ("MILESTONES.md", "references", r"(?<=\*\*)\d+(?=/7 completed\n)"),
    ("AGENTS.md", "masters", r"(?<=\*\*)\d+(?=/7 accepted album masters\*\*)"),
    ("AGENTS.md", "references", r"(?<=\*\*)\d+(?=/7 completed\n)"),
    ("README_FIRST.txt", "masters", r"(?<=Album masters accepted:      )\d+(?=/7)"),
    ("README_FIRST.txt", "references", r"(?<=System references completed: )\d+(?=/7)"),
)

SEAL_QUOTE = re.compile(r"(?<=The manifest seal is\n`)[0-9a-f]{64}(?=`)")


@dataclass
class Plan:
    """What would change, computed before anything is written."""

    track_id: str
    event: Event
    current_state: str
    next_state: str
    idempotent: bool
    manifest: dict[str, Any]
    documents: dict[str, str] = dataclass_field(default_factory=dict)
    findings: list[str] = dataclass_field(default_factory=list)

    @property
    def changes(self) -> bool:
        return bool(self.documents) or self.manifest.get("_dirty", False)


def load_manifest(repo_root: Path) -> dict[str, Any]:
    path = Path(repo_root) / MANIFEST_RELATIVE
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_seal(value, "manifest_sha256")
    return value


def track_row(manifest: Mapping[str, Any], track_id: str) -> dict[str, Any]:
    for row in manifest.get("tracks") or []:
        if row.get("track_id") == track_id:
            return row
    raise LedgerTransitionError(f"no such commissioned track: {track_id}")


def current_state(row: Mapping[str, Any]) -> str:
    """Derived, never stored as a separate field that could disagree."""
    status = row.get("status") or {}
    if status.get("system_reference") == "complete":
        return SYSTEM_REFERENCE_PASSED
    if status.get("album_master") == "accepted":
        return MASTER_ACCEPTED
    if row.get("master_qualification"):
        return MASTER_QUALIFIED
    for relative in row.get("repo_evidence") or []:
        if str(relative).endswith("frontier.public.json"):
            return FRONTIER_SELECTED
    return NONE


def _identity_from_receipt(event: Event, receipt: Mapping[str, Any]) -> ObjectIdentity | None:
    if event.produces == MASTER_QUALIFIED:
        provenance = receipt.get("provenance") or {}
        return ObjectIdentity(
            canonical_pcm_sha256=str(provenance.get("master_canonical_pcm_sha256") or ""),
            container_sha256=str(provenance.get("master_container_sha256") or "") or None,
            role="qualified master")
    if event.produces == MASTER_ACCEPTED:
        audited = receipt.get("audited_object") or {}
        return ObjectIdentity(
            canonical_pcm_sha256=str(audited.get("canonical_pcm_sha256") or ""),
            container_sha256=str(audited.get("container_sha256") or "") or None,
            role="accepted master")
    return None


def _prior_identity(row: Mapping[str, Any], event: Event) -> ObjectIdentity | None:
    if event.produces != MASTER_ACCEPTED:
        return None
    qualification = row.get("master_qualification") or {}
    if not qualification:
        return None
    return ObjectIdentity(
        canonical_pcm_sha256=str(qualification.get("canonical_pcm_sha256") or ""),
        container_sha256=str(qualification.get("container_sha256") or "") or None,
        role="qualified master")


def _apply_to_row(row: dict[str, Any], event: Event, receipt: Mapping[str, Any],
                  relative: str) -> None:
    """The state a landed receipt implies. Track prose is left alone."""
    status = dict(row.get("status") or {})
    if event.produces == MASTER_QUALIFIED:
        provenance = receipt.get("provenance") or {}
        qualification = dict(row.get("master_qualification") or {})
        qualification.update({
            "master_id": receipt.get("master_id"),
            "master_state": MASTER_QUALIFIED,
            "canonical_pcm_sha256": provenance.get("master_canonical_pcm_sha256"),
            "container_sha256": provenance.get("master_container_sha256"),
            "source_canonical_pcm_sha256": provenance.get("source_canonical_pcm_sha256"),
            "receipt_sha256": receipt.get("receipt_sha256"),
            "owner_master_acceptance": False,
        })
        row["master_qualification"] = qualification
        status["album_master"] = "unaccepted"
        status["human_acceptance"] = False
    elif event.produces == MASTER_ACCEPTED:
        audited = receipt.get("audited_object") or {}
        chain = receipt.get("authorizing_chain") or {}
        qualification = dict(row.get("master_qualification") or {})
        qualification["master_state"] = MASTER_ACCEPTED
        qualification["owner_master_acceptance"] = True
        qualification.pop("awaiting", None)
        row["master_qualification"] = qualification
        accepted = dict(row.get("accepted_master") or {})
        accepted.update({
            "master_id": receipt.get("master_id"),
            "verdict": receipt.get("verdict"),
            "canonical_pcm_sha256": audited.get("canonical_pcm_sha256"),
            "container_sha256": audited.get("container_sha256"),
            "source_canonical_pcm_sha256": audited.get("source_canonical_pcm_sha256"),
            "acceptance_receipt_sha256": receipt.get("receipt_sha256"),
            "qualification_receipt_sha256": qualification.get("receipt_sha256"),
            "monitoring_verdict": chain.get("authorized_for_mastering_by"),
        })
        row["accepted_master"] = accepted
        status["album_master"] = "accepted"
        status["human_acceptance"] = True
    elif event.produces == SYSTEM_REFERENCE_PASSED:
        status["system_reference"] = "complete"
    row["status"] = status

    evidence = list(row.get("repo_evidence") or [])
    if relative not in evidence:
        evidence.append(relative)
    row["repo_evidence"] = evidence


def _derive_counters(manifest: dict[str, Any]) -> None:
    """Counters are outputs. This is the only place they are ever written."""
    manifest["completed_album_master_count"] = sum(
        (row.get("status") or {}).get("album_master") == "accepted"
        for row in manifest["tracks"])
    manifest["completed_system_reference_count"] = sum(
        (row.get("status") or {}).get("system_reference") == "complete"
        for row in manifest["tracks"])


def _project_documents(repo_root: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    """Rewrite only the numbers and the quoted seal; leave every sentence alone."""
    counts = {
        "masters": str(manifest["completed_album_master_count"]),
        "references": str(manifest["completed_system_reference_count"]),
    }
    updated: dict[str, str] = {}
    for relative, kind, pattern in COUNTER_PATTERNS:
        path = Path(repo_root) / relative
        text = updated.get(relative, path.read_text(encoding="utf-8"))
        replaced, count = re.subn(pattern, counts[kind], text)
        if count == 0:
            raise LedgerTransitionError(
                f"{relative}: no counter matched {pattern!r}; the projection would go stale")
        updated[relative] = replaced

    document = Path(repo_root) / LEDGER_DOCUMENT
    text = updated.get(LEDGER_DOCUMENT, document.read_text(encoding="utf-8"))
    replaced, count = SEAL_QUOTE.subn(str(manifest["manifest_sha256"]), text)
    if count != 1:
        raise LedgerTransitionError(
            f"{LEDGER_DOCUMENT} must quote exactly one manifest seal; found {count}")
    updated[LEDGER_DOCUMENT] = replaced

    return {relative: text for relative, text in updated.items()
            if text != (Path(repo_root) / relative).read_text(encoding="utf-8")}


def plan_transition(repo_root: Path, *, track_id: str, event_name: str,
                    receipt_path: Path) -> Plan:
    """Validate everything and compute the result, writing nothing."""
    repo_root = Path(repo_root)
    if event_name not in EVENTS:
        raise LedgerTransitionError(
            f"unknown event {event_name!r}; expected one of {sorted(EVENTS)}")
    event = EVENTS[event_name]

    receipt_path = Path(receipt_path)
    try:
        relative = receipt_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise LedgerTransitionError(
            f"the receipt must live inside the repository: {receipt_path}") from exc

    try:
        receipt = load_sealed(receipt_path, kind=event.receipt_kind)
    except EvidenceError as exc:
        raise LedgerTransitionError(str(exc)) from exc

    findings = verify_body_free(receipt)
    if findings:
        raise LedgerTransitionError(
            f"{receipt_path.name} is not body-free: {'; '.join(findings)}")

    if receipt.get("track_id") != track_id:
        raise LedgerTransitionError(
            f"receipt names track {receipt.get('track_id')!r}, not {track_id!r}")
    if event.verdict_field and receipt.get(event.verdict_field) != event.verdict_value:
        raise LedgerTransitionError(
            f"receipt {event.verdict_field} is {receipt.get(event.verdict_field)!r}, "
            f"which does not authorize {event.name}")
    for reopened in ("reopens_timing_law", "reopens_arrangement", "reopens_mix"):
        if (receipt.get("audition") or {}).get(reopened):
            raise LedgerTransitionError(
                f"the receipt reopens {reopened}; that is a different decision")

    manifest = load_manifest(repo_root)
    row = track_row(manifest, track_id)
    state = current_state(row)

    idempotent = state == event.produces
    if not idempotent and state != event.requires:
        raise LedgerTransitionError(
            f"{track_id} is {state!r}; {event.name} requires {event.requires!r}. "
            f"States advance in order: {' -> '.join(STATE_ORDER)}")

    prior = _prior_identity(row, event)
    incoming = _identity_from_receipt(event, receipt)
    if prior is not None and incoming is not None and not prior.matches(incoming):
        raise LedgerTransitionError(
            f"the receipt names {incoming.describe()} but the qualified object is "
            f"{prior.describe()}; a decision binds to one exact object")

    if idempotent:
        recorded = (row.get("accepted_master") or {}).get("acceptance_receipt_sha256") \
            if event.produces == MASTER_ACCEPTED else \
            (row.get("master_qualification") or {}).get("receipt_sha256")
        if recorded and recorded != receipt.get("receipt_sha256"):
            raise LedgerTransitionError(
                f"{track_id} is already {state!r} on receipt {str(recorded)[:12]}; "
                f"this one is {str(receipt.get('receipt_sha256'))[:12]}")

    _apply_to_row(row, event, receipt, relative)
    _derive_counters(manifest)
    manifest = seal({k: v for k, v in manifest.items() if k != "manifest_sha256"},
                    "manifest_sha256")
    documents = _project_documents(repo_root, manifest)

    return Plan(track_id=track_id, event=event, current_state=state,
                next_state=event.produces, idempotent=idempotent,
                manifest=manifest, documents=documents, findings=findings)


def verify(repo_root: Path) -> list[str]:
    """Everything the ledger claims must still be true. Findings, not a boolean."""
    repo_root = Path(repo_root)
    problems: list[str] = []
    try:
        manifest = load_manifest(repo_root)
    except Exception as exc:  # noqa: BLE001
        return [f"manifest does not validate its own seal: {exc}"]

    masters = sum((row.get("status") or {}).get("album_master") == "accepted"
                  for row in manifest["tracks"])
    references = sum((row.get("status") or {}).get("system_reference") == "complete"
                     for row in manifest["tracks"])
    if manifest["completed_album_master_count"] != masters:
        problems.append("accepted-master counter disagrees with the track rows")
    if manifest["completed_system_reference_count"] != references:
        problems.append("system-reference counter disagrees with the track rows")

    for row in manifest["tracks"]:
        state = current_state(row)
        status = row.get("status") or {}
        if state == SYSTEM_REFERENCE_PASSED and status.get("album_master") != "accepted":
            problems.append(f"{row['track_id']}: system reference without an accepted master")
        if state == MASTER_ACCEPTED and not row.get("master_qualification"):
            problems.append(f"{row['track_id']}: accepted master without a qualified one")
        if status.get("human_acceptance") is not (status.get("album_master") == "accepted"):
            problems.append(f"{row['track_id']}: human acceptance disagrees with album_master")

    counts = {"masters": str(masters), "references": str(references)}
    for relative, kind, pattern in COUNTER_PATTERNS:
        text = (repo_root / relative).read_text(encoding="utf-8")
        found = re.findall(pattern, text)
        if not found:
            problems.append(f"{relative}: counter projection {pattern!r} no longer matches")
        elif any(value != counts[kind] for value in found):
            problems.append(
                f"{relative}: quotes {found} {kind}, ledger derives {counts[kind]}")

    document = (repo_root / LEDGER_DOCUMENT).read_text(encoding="utf-8")
    quoted = SEAL_QUOTE.findall(document)
    if quoted != [manifest["manifest_sha256"]]:
        problems.append(f"{LEDGER_DOCUMENT} quotes {quoted}, seal is "
                        f"{manifest['manifest_sha256']}")
    return problems


def apply_transition(repo_root: Path, *, track_id: str, event_name: str,
                     receipt_path: Path) -> dict[str, Any]:
    """Validate, write, then re-read and re-validate. Idempotent by construction."""
    repo_root = Path(repo_root)
    plan = plan_transition(repo_root, track_id=track_id, event_name=event_name,
                           receipt_path=receipt_path)

    manifest_path = repo_root / MANIFEST_RELATIVE
    before = manifest_path.read_text(encoding="utf-8")
    after = json.dumps(plan.manifest, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n"
    wrote: list[str] = []
    if after != before:
        manifest_path.write_text(after, encoding="utf-8", newline="\n")
        wrote.append(MANIFEST_RELATIVE)
    for relative, text in plan.documents.items():
        (repo_root / relative).write_text(text, encoding="utf-8", newline="\n")
        wrote.append(relative)

    problems = verify(repo_root)
    if problems:
        raise LedgerTransitionError(
            "the ledger did not validate after the transition: " + "; ".join(problems))

    return {
        "track_id": track_id,
        "event": event_name,
        "from_state": plan.current_state,
        "to_state": plan.next_state,
        "idempotent_replay": plan.idempotent,
        "files_written": wrote,
        "accepted_album_masters": plan.manifest["completed_album_master_count"],
        "completed_system_references": plan.manifest["completed_system_reference_count"],
        "manifest_sha256": plan.manifest["manifest_sha256"],
    }
