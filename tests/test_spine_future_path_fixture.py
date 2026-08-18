"""One synthetic lane walked end to end on the v2 model.

This is the path a *future* commission takes, proved once so the spine is exercised as
a whole rather than only as separate units:

```text
TrackCommission
  -> modality-appropriate SourceBindings
  -> MasteringPlan qualification
  -> AcceptanceReceipt validation
  -> prepared SystemReferenceChallenge
```

It is deliberately not A1-02 and not any commissioned track. `AX-01` exists only
inside this file, in an in-memory ledger, and the test asserts that walking it creates
no album authority: nothing is written, and the real ledger's counters are exactly
where they were.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.album import acceptance as acc  # noqa: E402
from earcrate.album import bindings as bd  # noqa: E402
from earcrate.album import commission as cm  # noqa: E402
from earcrate.album import mastering as ms  # noqa: E402
from earcrate.album import system_reference as sr  # noqa: E402
from earcrate.album import transitions as tr  # noqa: E402
from earcrate.evidence import manifest as ev  # noqa: E402

SYNTHETIC_PCM = "1" * 64
SYNTHETIC_CONTAINER = "2" * 64
SOURCE_PCM = "3" * 64


def _synthetic_ledger() -> dict:
    """An album that does not exist, so nothing here can become album authority."""
    return {
        "kind": "earcrate_album_program",
        "album_id": "earcrate-fixture-album",
        "tracks": [{
            "track_id": "AX-01",
            "capability_role": "fixture_only_not_a_commission",
            "control_question": "Does the shared spine carry a lane end to end?",
            "binding_requirements": [
                {"role": "exact_reference_recording", "modality": "audio_recording",
                 "edition_constraint": "the intended edition",
                 "required_identities": ["container_sha256", "canonical_pcm_sha256"]},
                {"role": "score_answer_key", "modality": "printed_score"},
                {"role": "symbolic_realization", "modality": "midi"},
                {"role": "realization_rack", "modality": "rack_preset"},
            ],
            "status": {"album_master": "unaccepted", "system_reference": "incomplete"},
        }],
        "completion_model": {
            "album_master": ["owner accepts the music"],
            "system_reference": ["the gold decisions are withheld"],
        },
    }


def _bindings() -> dict[str, bd.SourceBinding]:
    return {
        "exact_reference_recording": bd.SourceBinding(
            source_id="fixture-recording", role="exact_reference_recording",
            modality="audio_recording", authority_class="answer_key",
            privacy_class="private_local", custody_class="private_custody",
            identities={"container_sha256": "a" * 64, "canonical_pcm_sha256": SOURCE_PCM},
            edition={"catalog_number": "FIXTURE-001", "territory": "XX"},
            verified=True, verification_note="fixture"),
        "score_answer_key": bd.SourceBinding(
            source_id="fixture-score", role="score_answer_key", modality="printed_score",
            authority_class="answer_key", privacy_class="private_local",
            custody_class="private_custody",
            identities={"container_sha256": "b" * 64}, verified=True),
        "symbolic_realization": bd.SourceBinding(
            source_id="fixture-midi", role="symbolic_realization", modality="midi",
            authority_class="material", privacy_class="private_local",
            custody_class="private_custody",
            identities={"container_sha256": "c" * 64, "content_sha256": "d" * 64},
            verified=True),
        "realization_rack": bd.SourceBinding(
            source_id="fixture-rack", role="realization_rack", modality="rack_preset",
            authority_class="material", privacy_class="private_local",
            custody_class="private_custody",
            identities={"container_sha256": "e" * 64}, verified=True),
    }


def test_the_future_path_walks_end_to_end_on_the_v2_model():
    ledger = _synthetic_ledger()

    # 1. Commission -------------------------------------------------------------
    commission = cm.from_ledger(ledger, "AX-01")
    assert commission.commission_sha256
    assert len(commission.typed_bindings) == 4
    assert cm.verify_against_ledger(commission, ledger) == []

    # 2. Modality-appropriate bindings ------------------------------------------
    bound = _bindings()
    report = bd.readiness_report(commission, bound)
    assert report["all_required_bindings_ready"] is True
    modalities = {row["role"]: row["modality"] for row in report["requirements"]}
    assert modalities == {
        "exact_reference_recording": "audio_recording",
        "score_answer_key": "printed_score",
        "symbolic_realization": "midi",
        "realization_rack": "rack_preset",
    }

    # 3. Mastering qualification -------------------------------------------------
    plan = ms.MasteringPlan(
        track_id=commission.track_id,
        source_authority={"canonical_pcm_sha256": SOURCE_PCM},
        stages=(ms.Stage("equalization", {"band": "200", "gain_db": -1.0}),
                ms.Stage("limiting", {"ceiling_dbtp": -1.0})),
        allowed_tools={"ffmpeg": "ffmpeg version 8.1.2-essentials_build"},
        sample_format={"codec": "pcm_s24le", "sample_rate": 48000, "channels": 2},
        dither_allowed=False,
        determinism_policy="pcm_exact_container_may_differ",
        signal_targets=(ms.SignalTarget("true_peak_dbtp", "<=", -1.0, tolerance=0.05),),
        refusal_conditions=("hard_clipped_source",),
        output_identity_requirements=("canonical_pcm_sha256", "container_sha256"))

    authority = {
        "track_id": commission.track_id,
        "commission_sha256": commission.commission_sha256,
        "source_bindings": {role: item.identity_digest() for role, item in bound.items()},
        "mastering_plan_sha256": plan.plan_sha256(),
        "audio_affecting_tree_digest": "f" * 64,
        "canonical_pcm_sha256": SYNTHETIC_PCM,
        "container_sha256": SYNTHETIC_CONTAINER,
        "determinism": {"classification": "pcm_exact_container_may_differ"},
    }
    manifest = ev.build("earcrate_fixture_master_manifest", authority,
                        {"earcrate_git_head": "0" * 40, "rendered_at": "2026-08-17T00:00:00Z"})
    ev.validate(manifest)

    executed = {
        "stages": [stage.as_dict() for stage in plan.stages],
        "tools": dict(plan.allowed_tools),
        "dither_applied": False,
        "determinism": {"executions": 2, "canonical_pcm_equal": True,
                        "container_equal": False},
        "measurement": {"measured_by": "ffmpeg ebur128",
                        "values": {"true_peak_dbtp": -1.02}},
        "refusals_exercised": list(plan.refusal_conditions),
        "output": {"canonical_pcm_sha256": SYNTHETIC_PCM,
                   "container_sha256": SYNTHETIC_CONTAINER,
                   "authority_sha256": manifest["authority_sha256"]},
    }
    assert ms.validate_execution(plan, executed) == []

    # 4. Acceptance --------------------------------------------------------------
    verdict = {
        "kind": "earcrate_fixture_master_acceptance_verdict",
        "track_id": commission.track_id,
        "verdict": acc.ACCEPT,
        "audited": {"canonical_pcm_sha256": SYNTHETIC_PCM,
                    "container_sha256": SYNTHETIC_CONTAINER},
        "authority": {"human_review": True, "reopens_timing_law": False,
                      "reopens_arrangement": False, "reopens_mix": False},
    }
    receipt = acc.build_receipt(track_id=commission.track_id, descent="fixture",
                                master_id="fixture-master-v1", verdict=verdict,
                                manifest=manifest)
    assert receipt["state"]["accepted_album_master"] is True
    assert receipt["bound_authority"]["authority_sha256"] == manifest["authority_sha256"]
    assert receipt["provenance_context"]["event_sha256"] == manifest["event_sha256"]

    # 5. A prepared challenge, and nothing more ----------------------------------
    challenge = sr.prepare(
        track_id=commission.track_id, commission_sha256=commission.commission_sha256,
        master_state=sr.MASTER_ACCEPTED,
        accepted_master_authority_sha256=manifest["authority_sha256"],
        withheld_answer_identities=(SYNTHETIC_PCM,),
        allowed_evidence=({"kind": "commission", "sha256": commission.commission_sha256},),
        forbidden_evidence=("the accepted master",),
        execution_environment={"class": "clean_rebuild"},
        procedure="undesigned; the scaffold binds authority, not a recovery method",
        evaluator="owner",
        success_criteria=("an inferred candidate blindly beats the declared naive control",))
    assert challenge.state == sr.PREPARED
    assert sr.may_complete_system_reference(challenge) is False


def test_the_fixture_creates_no_album_authority():
    """Walking the future path must not touch the real ledger in any way."""
    before = (ROOT / tr.MANIFEST_RELATIVE).read_bytes()
    test_the_future_path_walks_end_to_end_on_the_v2_model()
    after = (ROOT / tr.MANIFEST_RELATIVE).read_bytes()
    assert before == after, "the fixture wrote to the album ledger"

    manifest = json.loads(after.decode("utf-8"))
    assert manifest["completed_album_master_count"] == 1
    assert manifest["completed_system_reference_count"] == 0
    assert [row["track_id"] for row in manifest["tracks"]] == [
        "A1-01", "A1-02", "A1-03", "A1-04", "A1-05", "A1-06", "A1-07"]
    assert "AX-01" not in after.decode("utf-8")
    assert tr.verify(ROOT) == []
