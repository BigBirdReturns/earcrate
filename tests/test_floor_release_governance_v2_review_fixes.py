from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from earcrate.floor.model import floor_sha256_json
from earcrate.floor.release_governance import (
    floor_decide_governed_release,
    floor_issue_publish_permit,
    floor_publish_release,
    floor_release_governance_schema_bundle,
    floor_review_assignments,
    floor_seal_arbitration_assignment,
    floor_seal_arbitration_review,
    floor_verify_published_release,
)
from test_floor_release_governance_v2 import (
    _campaign,
    _eligible,
    _raises,
    _review,
    _rights,
)


def _reseal(value: dict, hash_field: str) -> dict:
    result = deepcopy(value)
    result.pop(hash_field, None)
    result[hash_field] = floor_sha256_json(result)
    return result


def test_governed_decision_revalidates_the_embedded_arbitration_assignment() -> None:
    campaign, _, _ = _campaign()
    packets = floor_review_assignments(campaign)
    split = [
        _review(campaign, packets[0], "candidate"),
        _review(campaign, packets[1], "control"),
    ]
    assignment = floor_seal_arbitration_assignment(
        campaign,
        split,
        {
            "arbitrator_id": "human.arbitrator",
            "authentication_sha256": "d" * 64,
            "reason": "split review",
        },
    )
    arbitration = floor_seal_arbitration_review(
        campaign,
        assignment,
        {
            "arbitrator_id": "human.arbitrator",
            "authentication_sha256": "d" * 64,
            "verdict": "candidate",
            "notes": ["candidate preserves the phrase transition"],
        },
    )
    assert (
        arbitration["arbitration_assignment_sha256"]
        == arbitration["arbitration_assignment"]["arbitration_assignment_sha256"]
    )

    eligible = floor_decide_governed_release(
        campaign,
        split,
        _rights(campaign),
        as_of="2026-07-31T00:00:00Z",
        arbitration_review=arbitration,
    )
    assert eligible["status"] == "eligible"
    assert (
        eligible["arbitration_assignment_sha256"]
        == assignment["arbitration_assignment_sha256"]
    )

    assignment_free = deepcopy(arbitration)
    assignment_free.pop("arbitration_assignment")
    assignment_free = _reseal(
        assignment_free,
        "arbitration_review_sha256",
    )
    with _raises("assignment"):
        floor_decide_governed_release(
            campaign,
            split,
            _rights(campaign),
            as_of="2026-07-31T00:00:00Z",
            arbitration_review=assignment_free,
        )

    forged_assignment = deepcopy(assignment)
    forged_assignment["arbitrator_id"] = "org.earcrate.builder"
    forged_assignment = _reseal(
        forged_assignment,
        "arbitration_assignment_sha256",
    )
    forged_review = floor_seal_arbitration_review(
        campaign,
        forged_assignment,
        {
            "arbitrator_id": "org.earcrate.builder",
            "authentication_sha256": "d" * 64,
            "verdict": "candidate",
            "notes": ["self-sealed but not independently assigned"],
        },
    )
    with _raises("independent"):
        floor_decide_governed_release(
            campaign,
            split,
            _rights(campaign),
            as_of="2026-07-31T00:00:00Z",
            arbitration_review=forged_review,
        )


def test_publish_permit_cannot_predate_the_governed_decision() -> None:
    campaign, _, _ = _campaign()
    decision = _eligible(campaign)
    scope = [
        {
            "artifact_id": "candidate_wav",
            "role": "authoritative_master",
            "output_name": "master.wav",
        }
    ]
    with _raises("before the governed release decision"):
        floor_issue_publish_permit(
            campaign,
            decision,
            scope,
            issued_at="2026-07-30T23:59:59Z",
            expires_at="2026-08-15T00:00:00Z",
        )

    permit = floor_issue_publish_permit(
        campaign,
        decision,
        scope,
        issued_at="2026-07-31T00:00:00Z",
        expires_at="2026-08-15T00:00:00Z",
    )
    assert permit["issued_at"] == decision["as_of"]


def test_publication_verifier_rejects_directory_aliases_and_undeclared_trees(
    tmp_path: Path,
) -> None:
    campaign, audition, master = _campaign()
    permit = floor_issue_publish_permit(
        campaign,
        _eligible(campaign),
        [
            {
                "artifact_id": "candidate_mp3_30s",
                "role": "reviewed_audition",
                "output_name": "audition.mp3",
            },
            {
                "artifact_id": "candidate_wav",
                "role": "authoritative_master",
                "output_name": "master.wav",
            },
        ],
        issued_at="2026-07-31T01:00:00Z",
        expires_at="2026-08-15T00:00:00Z",
    )
    audition_path = tmp_path / "candidate-audition"
    master_path = tmp_path / "candidate-master"
    audition_path.write_bytes(audition)
    master_path.write_bytes(master)
    output = tmp_path / "published"
    floor_publish_release(
        permit,
        artifact_paths={
            "candidate_mp3_30s": audition_path,
            "candidate_wav": master_path,
        },
        output_dir=output,
        published_at="2026-07-31T02:00:00Z",
    )

    alias = tmp_path / "published-alias"
    try:
        alias.symlink_to(output, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with _raises("symlink"):
            floor_verify_published_release(alias)

    extra = output / "extra"
    extra.mkdir()
    (extra / "payload.bin").write_bytes(b"undeclared")
    with _raises("undeclared|non-file"):
        floor_verify_published_release(output)


def test_governance_schemas_require_runtime_review_bindings() -> None:
    schemas = floor_release_governance_schema_bundle()

    blind = schemas[
        "earcrate_floor_blind_human_review_v2.schema.json"
    ]
    required = {
        "campaign_core_sha256",
        "public_assignment_sha256",
        "review_token",
        "dimensions",
        "notes",
        "machine_generated",
    }
    assert required.issubset(set(blind["required"]))
    assert blind["additionalProperties"] is False
    assert blind["properties"]["dimensions"]["type"] == "object"
    assert blind["properties"]["review_token"]["pattern"] == "^[0-9a-f]{64}$"
    assert blind["properties"]["machine_generated"]["const"] is False

    assignment = schemas[
        "earcrate_floor_arbitration_assignment_v2.schema.json"
    ]
    assert "reason" in assignment["required"]
    assert assignment["additionalProperties"] is False

    arbitration = schemas[
        "earcrate_floor_arbitration_review_v2.schema.json"
    ]
    assert {
        "arbitration_assignment",
        "notes",
    }.issubset(set(arbitration["required"]))
    assert arbitration["additionalProperties"] is False
