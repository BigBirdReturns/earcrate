"""Release governance v2 layered on PR #47's sealed Floor candidates.

Builders and signal evaluators may propose and qualify artifacts. Only blinded
independent human review, a separate rights decision, and an exact content-bound
permit may authorize publication.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from earcrate.floor.model import FloorError, floor_sha256_json


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FloorError(f"{field} must be a mapping")
    return dict(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FloorError(f"{field} must be a non-empty string")
    return value


def _sha(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise FloorError(f"{field} must be a SHA-256 digest")
    return result


def _sealed(payload: Mapping[str, Any], hash_field: str) -> dict[str, Any]:
    result = dict(payload)
    claimed = result.pop(hash_field, None)
    digest = floor_sha256_json(result)
    if claimed is not None and str(claimed) != digest:
        raise FloorError(f"{hash_field} hash mismatch; sealed object is immutable")
    result[hash_field] = digest
    return result


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _signal(campaign: Mapping[str, Any]) -> dict[str, Any]:
    signal = campaign.get("signal_evaluation")
    if isinstance(signal, Sequence) and not isinstance(signal, (str, bytes, bytearray)):
        if len(signal) != 1:
            raise FloorError("release governance requires exactly one signal evaluation")
        signal = signal[0]
    return _mapping(signal, "signal_evaluation")


def _assignments(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = campaign.get("review_assignments")
    if not isinstance(rows, list):
        raise FloorError("campaign is missing review assignments")
    return [_mapping(row, "review assignment") for row in rows]


def _private_assignments(campaign: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    value = campaign.get("_private_assignment_map")
    if not isinstance(value, Mapping):
        raise FloorError("campaign is missing its private assignment map")
    return {
        str(token): {
            "reviewer_id": _text(row.get("reviewer_id"), "assigned reviewer_id")
        }
        for token, raw in value.items()
        for row in [_mapping(raw, "private assignment")]
    }


def floor_open_blind_review_campaign(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(value, "campaign")
    campaign_id = _text(raw.get("campaign_id"), "campaign_id")
    candidate = _mapping(raw.get("candidate"), "candidate")
    signal = _mapping(raw.get("signal_evaluation"), "signal_evaluation")
    control = _mapping(raw.get("control"), "control")

    candidate_sha = _sha(candidate.get("candidate_sha256"), "candidate_sha256")
    builder_id = _text(candidate.get("builder_identity_id"), "builder_identity_id")
    _sha(candidate.get("audition_sha256"), "candidate audition_sha256")
    _sha(candidate.get("master_sha256"), "candidate master_sha256")
    if _sha(signal.get("candidate_sha256"), "signal candidate_sha256") != candidate_sha:
        raise FloorError("signal evaluation belongs to another candidate")
    evaluator_id = _text(
        signal.get("evaluator_identity_id"), "signal evaluator_identity_id"
    )
    if str(signal.get("status")) != "passed":
        raise FloorError("signal evaluation has not passed")
    _sha(signal.get("signal_evaluation_sha256"), "signal_evaluation_sha256")
    _sha(control.get("control_sha256"), "control_sha256")
    _sha(control.get("audition_sha256"), "control audition_sha256")
    _sha(control.get("master_sha256"), "control master_sha256")

    reviewer_ids = raw.get("reviewer_ids")
    if not isinstance(reviewer_ids, list) or not reviewer_ids:
        raise FloorError("reviewer_ids must be a non-empty list")
    reviewers = [_text(row, "reviewer_id") for row in reviewer_ids]
    if len(set(reviewers)) != len(reviewers):
        raise FloorError("reviewer identities must be independent and unique")
    forbidden = {builder_id, evaluator_id}
    if forbidden.intersection(reviewers):
        raise FloorError(
            "reviewers must be independent of the candidate builder and signal evaluator"
        )

    minimum = raw.get("minimum_reviewers")
    if not isinstance(minimum, int) or minimum < 2 or minimum > len(reviewers):
        raise FloorError("minimum_reviewers must require at least two assigned humans")

    public: list[dict[str, Any]] = []
    private: dict[str, dict[str, str]] = {}
    for index, reviewer_id in enumerate(reviewers):
        token = floor_sha256_json(
            {
                "campaign_id": campaign_id,
                "reviewer_id": reviewer_id,
                "assignment_index": index,
            }
        )
        public.append(
            {
                "reviewer_id": reviewer_id,
                "review_token": token,
                "options": ["A", "B"],
            }
        )
        private[token] = {"reviewer_id": reviewer_id}

    campaign = {
        "campaign_id": campaign_id,
        "candidate": candidate,
        "signal_evaluation": signal,
        "control": control,
        "reviewer_ids": reviewers,
        "minimum_reviewers": minimum,
        "review_assignments": public,
        "private_option_map": {"A": "candidate", "B": "control"},
        "_private_assignment_map": private,
    }
    campaign["campaign_sha256"] = floor_sha256_json(
        {key: item for key, item in campaign.items() if not key.startswith("_")}
    )
    return campaign


def floor_review_assignments(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "reviewer_id": row["reviewer_id"],
            "review_token": row["review_token"],
            "options": list(row["options"]),
        }
        for row in _assignments(campaign)
    ]


def floor_seal_blind_review(
    campaign: Mapping[str, Any], value: Mapping[str, Any]
) -> dict[str, Any]:
    raw = _mapping(value, "blind review")
    token = _text(raw.get("review_token"), "review_token")
    reviewer_id = _text(raw.get("reviewer_id"), "reviewer_id")
    preferred = _text(raw.get("preferred_option"), "preferred_option")
    if preferred not in {"A", "B"}:
        raise FloorError("preferred_option must be A or B")

    private = _private_assignments(campaign)
    if token not in private or private[token]["reviewer_id"] != reviewer_id:
        raise FloorError("review token is not assigned to this reviewer")
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise FloorError("review dimensions must be a mapping")

    return _sealed(
        {
            "campaign_id": _text(campaign.get("campaign_id"), "campaign_id"),
            "reviewer_id": reviewer_id,
            "review_token": token,
            "preferred_option": preferred,
            "dimensions": dict(dimensions),
        },
        "review_sha256",
    ) if "review_sha256" not in raw else _sealed(raw, "review_sha256")


def floor_seal_rights_decision(
    campaign: Mapping[str, Any], value: Mapping[str, Any]
) -> dict[str, Any]:
    raw = _mapping(value, "rights decision")
    if bool(raw.get("legal_determination", False)):
        raise FloorError("rights policy may not claim a legal determination")
    authority = _text(raw.get("decided_by"), "rights decided_by")
    candidate = _mapping(campaign.get("candidate"), "candidate")
    signal = _signal(campaign)
    forbidden = {
        _text(candidate.get("builder_identity_id"), "builder_identity_id"),
        _text(signal.get("evaluator_identity_id"), "evaluator_identity_id"),
        *[_text(row, "reviewer_id") for row in campaign.get("reviewer_ids", [])],
    }
    if authority in forbidden:
        raise FloorError(
            "rights authority must be separate and independent from execution and review roles"
        )
    status = _text(raw.get("status"), "rights status")
    if status not in {"accepted_by_policy", "blocked", "expired", "not_evaluated"}:
        raise FloorError("unsupported rights status")
    policy_id = _text(raw.get("policy_id"), "rights policy_id")
    return _sealed(
        {
            "campaign_id": _text(campaign.get("campaign_id"), "campaign_id"),
            "candidate_sha256": _sha(
                candidate.get("candidate_sha256"), "candidate_sha256"
            ),
            "status": status,
            "policy_id": policy_id,
            "decided_by": authority,
            "legal_determination": False,
        },
        "rights_decision_sha256",
    ) if "rights_decision_sha256" not in raw else _sealed(raw, "rights_decision_sha256")


def floor_decide_governed_release(
    campaign: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    rights_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(reviews, Sequence):
        raise FloorError("reviews must be a sequence")
    sealed_reviews = [floor_seal_blind_review(campaign, row) for row in reviews]
    tokens = [row["review_token"] for row in sealed_reviews]
    reviewers = [row["reviewer_id"] for row in sealed_reviews]
    if len(set(tokens)) != len(tokens) or len(set(reviewers)) != len(reviewers):
        raise FloorError("duplicate token or reviewer violates one immutable review")

    minimum = int(campaign.get("minimum_reviewers") or 0)
    if len(sealed_reviews) < minimum:
        status, summary = "blocked", "review_quorum_pending"
        rights = None
    else:
        option_map = _mapping(campaign.get("private_option_map"), "private option map")
        preferences = [option_map[row["preferred_option"]] for row in sealed_reviews]
        rights = (
            None
            if rights_decision is None
            else floor_seal_rights_decision(campaign, rights_decision)
        )
        if "candidate" in preferences and "control" in preferences:
            status, summary = "blocked", "needs_arbitration"
        elif preferences and all(row == "control" for row in preferences):
            status, summary = "refused", "no_edit_preferred"
        elif preferences and all(row == "candidate" for row in preferences):
            if rights is None or rights["status"] != "accepted_by_policy":
                status, summary = "blocked", "rights_review_pending"
            else:
                status, summary = "accepted", "release_eligible"
        else:
            status, summary = "blocked", "review_quorum_pending"

    decision = {
        "campaign_id": _text(campaign.get("campaign_id"), "campaign_id"),
        "campaign_sha256": _sha(campaign.get("campaign_sha256"), "campaign_sha256"),
        "candidate_sha256": _sha(
            _mapping(campaign.get("candidate"), "candidate").get("candidate_sha256"),
            "candidate_sha256",
        ),
        "status": status,
        "summary": summary,
        "release_eligible": status == "accepted" and summary == "release_eligible",
        "review_sha256s": [row["review_sha256"] for row in sealed_reviews],
        "rights_decision_sha256": (
            None if rights is None else rights["rights_decision_sha256"]
        ),
    }
    return _sealed(decision, "decision_sha256")


def floor_issue_publish_permit(
    campaign: Mapping[str, Any],
    decision: Mapping[str, Any],
    publication_scope: Sequence[str],
) -> dict[str, Any]:
    sealed_decision = _sealed(decision, "decision_sha256")
    if not sealed_decision.get("release_eligible"):
        raise FloorError("release decision is not publish-ready")
    if sealed_decision.get("campaign_sha256") != campaign.get("campaign_sha256"):
        raise FloorError("release decision belongs to another campaign")
    if not isinstance(publication_scope, Sequence) or isinstance(
        publication_scope, (str, bytes, bytearray)
    ):
        raise FloorError("publication_scope must be a sequence")
    scope = [_text(row, "publication scope entry") for row in publication_scope]
    required = {"accepted-audition.mp3", "accepted-master.mp3"}
    if set(scope) != required:
        raise FloorError("permit scope must name the exact accepted audition and master")
    candidate = _mapping(campaign.get("candidate"), "candidate")
    return _sealed(
        {
            "campaign_id": campaign["campaign_id"],
            "campaign_sha256": campaign["campaign_sha256"],
            "decision_sha256": sealed_decision["decision_sha256"],
            "candidate_sha256": candidate["candidate_sha256"],
            "audition_sha256": candidate["audition_sha256"],
            "master_sha256": candidate["master_sha256"],
            "publication_scope": scope,
        },
        "permit_sha256",
    )


def floor_publish_release(
    permit: Mapping[str, Any],
    *,
    audition_path: Path,
    master_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    sealed_permit = _sealed(permit, "permit_sha256")
    audition = Path(audition_path)
    master = Path(master_path)
    audition_sha = _file_sha256(audition)
    master_sha = _file_sha256(master)
    if audition_sha != sealed_permit["audition_sha256"]:
        raise FloorError("reviewed audition hash mismatch; bytes mutated")
    if master_sha != sealed_permit["master_sha256"]:
        raise FloorError("reviewed master hash mismatch; bytes mutated")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    audition_out = output / "accepted-audition.mp3"
    master_out = output / "accepted-master.mp3"
    permit_out = output / "release-permit.json"
    manifest_out = output / "publication-manifest.json"
    sums_out = output / "SHA256SUMS"

    shutil.copyfile(audition, audition_out)
    shutil.copyfile(master, master_out)
    if _file_sha256(audition_out) != audition_sha or _file_sha256(master_out) != master_sha:
        raise FloorError("published bytes differ from reviewed hashes")
    permit_out.write_bytes(_canonical_json_bytes(sealed_permit))
    manifest = {
        "campaign_id": sealed_permit["campaign_id"],
        "candidate_sha256": sealed_permit["candidate_sha256"],
        "files": {
            audition_out.name: audition_sha,
            master_out.name: master_sha,
            permit_out.name: _file_sha256(permit_out),
        },
    }
    manifest_out.write_bytes(_canonical_json_bytes(manifest))
    sums = [
        (audition_out.name, _file_sha256(audition_out)),
        (master_out.name, _file_sha256(master_out)),
        (permit_out.name, _file_sha256(permit_out)),
        (manifest_out.name, _file_sha256(manifest_out)),
    ]
    sums_out.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sums),
        encoding="utf-8",
        newline="\n",
    )
    files = [
        audition_out.name,
        master_out.name,
        permit_out.name,
        manifest_out.name,
        sums_out.name,
    ]
    return {"output_dir": str(output), "files": files}


__all__ = [
    "floor_open_blind_review_campaign",
    "floor_review_assignments",
    "floor_seal_blind_review",
    "floor_seal_rights_decision",
    "floor_decide_governed_release",
    "floor_issue_publish_permit",
    "floor_publish_release",
]
