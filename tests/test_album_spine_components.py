"""Gates for TrackCommission, SourceBinding, MasteringPlan and the challenge scaffold.

Two things are being protected. First, that each component enforces the invariant it
was extracted for. Second, that extracting them changed nothing about A1-07 -- the
accepted lineage is frozen, and a framework that quietly moved it would have failed
at the only job that mattered.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.album import bindings as bd  # noqa: E402
from earcrate.album import commission as cm  # noqa: E402
from earcrate.album import mastering as ms  # noqa: E402
from earcrate.album import system_reference as sr  # noqa: E402
from earcrate.album import transitions as tr  # noqa: E402

MASTER_PCM = "b467e224808285c6e0f6e1e90c8b8b3908322ffa6471472e8e01f1415ea0b785"
MASTER_CONTAINER = "f821ce65c9e406a014f56aedd50c53d67e6539cd61af5a18d0f0bf42eea0312d"
RENDER_DIGEST = "c14de5fc93a6c02e29c85d77b9dbee868bfe280a7d215f46477793c96247dfdd"
MASTER_DIGEST = "24ae757819930273bb8a7e79f549a20c35ff36c8769f68b8917e0af996be17a6"


def _ledger() -> dict:
    return json.loads((ROOT / tr.MANIFEST_RELATIVE).read_text(encoding="utf-8"))


# --- the A1-07 lineage is frozen ---------------------------------------------------

def test_the_accepted_a1_07_state_did_not_move():
    manifest = _ledger()
    assert manifest["completed_album_master_count"] == 1
    assert manifest["completed_system_reference_count"] == 0
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    assert row["status"]["album_master"] == "accepted"
    assert row["status"]["system_reference"] == "incomplete"
    assert row["accepted_master"]["canonical_pcm_sha256"] == MASTER_PCM
    assert tr.verify(ROOT) == []


def test_both_a1_07_provenance_digests_are_unchanged():
    from earcrate.a1_07_full_form.provenance import adapter_tree_digest
    from earcrate.a1_07_master.provenance import master_tree_digest

    assert adapter_tree_digest(ROOT)["digest"] == RENDER_DIGEST
    assert master_tree_digest(ROOT)["digest"] == MASTER_DIGEST


def test_the_landed_receipt_seals_still_validate():
    from earcrate.evidence.receipts import load_sealed

    for path in sorted((ROOT / "proofs" / "album_one").glob("*.public.json")):
        load_sealed(path)


# --- TrackCommission ---------------------------------------------------------------

def test_a_commission_projects_the_ledger_without_replacing_it():
    manifest = _ledger()
    commission = cm.from_ledger(manifest, "A1-07")

    assert commission.track_id == "A1-07"
    assert commission.album_id == manifest["album_id"]
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    assert commission.capability_role == row["capability_role"]
    assert commission.control_question.endswith("?")
    assert commission.commission_sha256
    assert cm.verify_against_ledger(commission, manifest) == []


def test_prose_requirements_project_as_untyped_rather_than_being_invented():
    """Most commissions carry prose today; a projection must not pretend otherwise."""
    manifest = _ledger()
    untyped = [row["track_id"] for row in manifest["tracks"]
               if not row.get("binding_requirements")]
    assert untyped, "this gate is only meaningful while some commission is still prose"

    for track_id in untyped:
        commission = cm.from_ledger(manifest, track_id)
        assert all(not row.typed for row in commission.required_bindings), track_id
        assert commission.typed_bindings == (), track_id
        assert all(row.modality == "unspecified" for row in commission.required_bindings)


def test_typed_requirements_are_used_when_a_commission_declares_them():
    manifest = _ledger()
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    row["binding_requirements"] = [
        {"role": "exact_reference_recording", "modality": "audio_recording",
         "edition_constraint": "the commission's intended edition",
         "required_identities": ["container_sha256", "canonical_pcm_sha256"]},
        {"role": "score_answer_key", "modality": "printed_score"},
    ]
    commission = cm.from_ledger(manifest, "A1-02")
    assert [item.role for item in commission.typed_bindings] == [
        "exact_reference_recording", "score_answer_key"]
    assert commission.requirement("score_answer_key").modality == "printed_score"


def test_duplicate_binding_roles_are_refused():
    manifest = _ledger()
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    row["binding_requirements"] = [{"role": "same"}, {"role": "same"}]
    with pytest.raises(cm.CommissionError, match="duplicate binding roles"):
        cm.from_ledger(manifest, "A1-02")


def test_a_commission_drifting_from_its_ledger_is_reported():
    manifest = _ledger()
    commission = cm.from_ledger(manifest, "A1-07")
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-07")
    row["capability_role"] = "something_else"
    problems = cm.verify_against_ledger(commission, manifest)
    assert any("capability_role drifted" in item for item in problems)


# --- SourceBinding -----------------------------------------------------------------

def test_identity_is_modality_specific_not_audio_shaped():
    """A canonical PCM digest is meaningless for a PDF and must not be carried."""
    score = bd.SourceBinding(
        source_id="children-score", role="score_answer_key", modality="printed_score",
        authority_class="answer_key", privacy_class="private_local",
        custody_class="private_custody",
        identities={"container_sha256": "a" * 64}, verified=True)
    assert score.is_ready()
    assert "canonical_pcm_sha256" not in score.required_identities

    with pytest.raises(bd.BindingError, match="meaningless"):
        bd.SourceBinding(
            source_id="children-score", role="score_answer_key", modality="printed_score",
            authority_class="answer_key", privacy_class="private_local",
            custody_class="private_custody",
            identities={"container_sha256": "a" * 64, "canonical_pcm_sha256": "b" * 64})

    recording = bd.SourceBinding(
        source_id="children-recording", role="exact_reference_recording",
        modality="audio_recording", authority_class="answer_key",
        privacy_class="private_local", custody_class="private_custody",
        identities={"container_sha256": "c" * 64}, verified=True)
    assert not recording.is_ready()
    assert "missing canonical_pcm_sha256" in recording.readiness()


def test_every_declared_modality_is_expressible():
    for modality in bd.MODALITIES:
        required, _ = bd.MODALITIES[modality]
        identities = {name: ("d" * 64 if name.endswith("sha256") else "rev-1")
                      for name in required}
        binding = bd.SourceBinding(
            source_id=f"x-{modality}", role="r", modality=modality,
            authority_class="material", privacy_class="private_local",
            custody_class="private_custody", identities=identities, verified=True)
        assert binding.is_ready(), f"{modality} cannot be bound"


def test_a_path_is_not_a_binding():
    binding = bd.SourceBinding(
        source_id="x", role="r", modality="audio_recording", authority_class="material",
        privacy_class="private_local", custody_class="private_custody",
        identities={"container_sha256": "a" * 64, "canonical_pcm_sha256": "b" * 64},
        verified=False, location=r"D:\somewhere\file.wav")
    assert not binding.is_ready()
    assert "not verified; a path is not a binding" in binding.readiness()
    # And custody location never reaches a public projection.
    assert "location" not in binding.public_projection()
    assert "somewhere" not in json.dumps(binding.public_projection())


def test_an_edition_candidate_is_visibly_not_a_binding():
    """An answer key cannot be authoritative if the edition was chosen after acquisition."""
    candidate = bd.edition_candidate(
        "children-candidate", "exact_reference_recording", "audio_recording",
        note="commission does not name the edition unambiguously")
    assert not candidate.is_ready()
    assert candidate.verification_note.startswith("edition_candidate:")
    assert candidate.custody_class == "unbound"


def test_readiness_reports_what_each_requirement_still_lacks():
    manifest = _ledger()
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    row["binding_requirements"] = [
        {"role": "exact_reference_recording", "modality": "audio_recording",
         "edition_constraint": "intended edition",
         "required_identities": ["container_sha256", "canonical_pcm_sha256"]},
        {"role": "score_answer_key", "modality": "printed_score"},
    ]
    commission = cm.from_ledger(manifest, "A1-02")
    bound = {
        "score_answer_key": bd.SourceBinding(
            source_id="s", role="score_answer_key", modality="printed_score",
            authority_class="answer_key", privacy_class="private_local",
            custody_class="private_custody",
            identities={"container_sha256": "a" * 64}, verified=True),
    }
    report = bd.readiness_report(commission, bound)
    assert report["all_required_bindings_ready"] is False
    rows = {item["role"]: item for item in report["requirements"]}
    assert rows["exact_reference_recording"]["problems"] == ["not bound"]
    assert rows["score_answer_key"]["ready"] is True


# --- MasteringPlan -----------------------------------------------------------------

def _executed(plan: ms.MasteringPlan, **overrides) -> dict:
    executed = {
        "stages": [stage.as_dict() for stage in plan.stages],
        "tools": dict(plan.allowed_tools),
        "dither_applied": False,
        "determinism": {"executions": 2, "canonical_pcm_equal": True,
                        "container_equal": True},
        "measurement": {"measured_by": "ffmpeg ebur128",
                        "values": {"true_peak_dbtp": -1.0, "flat_top_run_count": 0,
                                   "max_section_gain_drift_db": 0.0}},
        "refusals_exercised": list(plan.refusal_conditions),
        "output": {"canonical_pcm_sha256": MASTER_PCM,
                   "container_sha256": MASTER_CONTAINER,
                   "authority_sha256": "e" * 64},
    }
    executed.update(overrides)
    return executed


def _a1_07_plan() -> ms.MasteringPlan:
    """A1-07's chain expressed in the shared contract, after the fact.

    The fixture lives here rather than in the shared module: a shared module naming a
    track is the first step toward one branching on a track. Nothing consumes this at
    render time, and A1-07's writer is untouched.
    """
    return ms.MasteringPlan(
        track_id="A1-07",
        source_authority={"canonical_pcm_sha256":
                          "61e20e832b98e606b241d8e91bddaa4c01a7fbfbb02b77bddc86aff1c913da58"},
        stages=(ms.Stage("linear_gain", {"gain_db": 2.5}),),
        allowed_tools={"ffmpeg": "ffmpeg version 8.1.2-essentials_build"},
        sample_format={"codec": "pcm_s24le", "sample_rate": 48000, "channels": 2,
                       "bit_depth": 24},
        dither_allowed=False,
        determinism_policy="bit_exact_across_executions",
        signal_targets=(
            ms.SignalTarget("true_peak_dbtp", "<=", -1.0, tolerance=0.05),
            ms.SignalTarget("flat_top_run_count", "<=", 0),
            ms.SignalTarget("max_section_gain_drift_db", "<=", 0.35),
        ),
        refusal_conditions=("hard_clipped_source", "loudness_target_requires_limiting"),
        section_invariants={"linear_gain_moves_every_section_equally": True,
                            "max_drift_db": 0.35},
        output_identity_requirements=("canonical_pcm_sha256", "container_sha256"))


def test_the_legacy_lane_conforms_to_the_shared_contract():
    """The framework is tested against a real lane, not only against its own design."""
    plan = _a1_07_plan()
    assert ms.validate_execution(plan, _executed(plan)) == []
    assert plan.plan_sha256() == _a1_07_plan().plan_sha256()


def test_undeclared_processing_is_refused():
    plan = _a1_07_plan()
    executed = _executed(plan)
    executed["stages"] = executed["stages"] + [{"name": "limiting", "parameters": {}}]
    problems = ms.validate_execution(plan, executed)
    assert any("do not match declared" in row for row in problems)

    executed = _executed(plan, tools={"ffmpeg": "ffmpeg version 8.1.2-essentials_build",
                                      "sox": "14.4"})
    assert any("undeclared tool" in row for row in ms.validate_execution(plan, executed))


def test_stage_order_and_parameters_are_exact():
    plan = ms.MasteringPlan(
        track_id="AX", source_authority={}, allowed_tools={"ffmpeg": "v1"},
        stages=(ms.Stage("equalization", {"band": "3k"}), ms.Stage("limiting", {"ceiling": -1.0})),
        sample_format={"codec": "pcm_s24le"}, dither_allowed=False,
        determinism_policy="bit_exact_across_executions",
        signal_targets=(ms.SignalTarget("true_peak_dbtp", "<=", -1.0),),
        refusal_conditions=("clipped_source",))
    executed = _executed(plan)
    executed["stages"] = list(reversed(executed["stages"]))
    assert any("in content or order" in row for row in ms.validate_execution(plan, executed))

    executed = _executed(plan)
    executed["stages"][1]["parameters"]["ceiling"] = -0.1
    assert any("declared -1.0" in row for row in ms.validate_execution(plan, executed))


def test_the_framework_permits_chains_a1_07_forbade():
    """A future track may legitimately need EQ, compression, limiting or dither."""
    plan = ms.MasteringPlan(
        track_id="AX", source_authority={}, allowed_tools={"ffmpeg": "v1"},
        stages=(ms.Stage("equalization", {"band": "3k", "gain_db": -1.5}),
                ms.Stage("compression", {"ratio": 2.0}),
                ms.Stage("limiting", {"ceiling_dbtp": -1.0}),
                ms.Stage("dither", {"shape": "triangular"})),
        sample_format={"codec": "pcm_s16le"}, dither_allowed=True,
        determinism_policy="not_required",
        signal_targets=(ms.SignalTarget("integrated_lufs", "within", -14.0, tolerance=0.5),),
        refusal_conditions=("clipped_source",))
    executed = _executed(plan, dither_applied=True,
                         measurement={"measured_by": "ffmpeg ebur128",
                                      "values": {"integrated_lufs": -14.2}},
                         determinism={"executions": 1})
    assert ms.validate_execution(plan, executed) == []


def test_a_stochastic_stage_cannot_claim_bit_exact_reproduction():
    with pytest.raises(ms.MasteringContractError, match="stochastic"):
        ms.MasteringPlan(
            track_id="AX", source_authority={}, allowed_tools={"ffmpeg": "v1"},
            stages=(ms.Stage("dither", {}),), sample_format={}, dither_allowed=True,
            determinism_policy="bit_exact_across_executions",
            signal_targets=(), refusal_conditions=("x",))


def test_a_plan_with_no_refusal_conditions_is_refused():
    with pytest.raises(ms.MasteringContractError, match="fail closed"):
        ms.MasteringPlan(
            track_id="AX", source_authority={}, allowed_tools={"ffmpeg": "v1"},
            stages=(ms.Stage("linear_gain", {}),), sample_format={}, dither_allowed=False,
            determinism_policy="not_required", signal_targets=(), refusal_conditions=())


def test_signal_gates_must_be_measured_not_copied():
    plan = _a1_07_plan()
    executed = _executed(plan)
    executed["measurement"] = {"values": {"true_peak_dbtp": -1.0, "flat_top_run_count": 0,
                                          "max_section_gain_drift_db": 0.0}}
    problems = ms.validate_execution(plan, executed)
    assert any("were not measured" in row for row in problems)

    executed = _executed(plan)
    executed["measurement"]["values"].pop("true_peak_dbtp")
    assert any("never measured" in row for row in ms.validate_execution(plan, executed))


def test_an_unexercised_refusal_condition_is_reported():
    plan = _a1_07_plan()
    executed = _executed(plan, refusals_exercised=["hard_clipped_source"])
    problems = ms.validate_execution(plan, executed)
    assert any("never exercised" in row for row in problems)


def test_the_output_must_bind_stable_authority():
    plan = _a1_07_plan()
    executed = _executed(plan)
    executed["output"].pop("authority_sha256")
    assert any("binds no stable authority" in row
               for row in ms.validate_execution(plan, executed))


# --- SystemReferenceChallenge ------------------------------------------------------

def _challenge(**overrides) -> sr.Challenge:
    payload = dict(
        track_id="A1-07", commission_sha256="a" * 64, master_state=sr.MASTER_ACCEPTED,
        accepted_master_authority_sha256="b" * 64,
        withheld_answer_identities=("c" * 64,),
        allowed_evidence=({"kind": "contract", "sha256": "d" * 64},),
        forbidden_evidence=("the accepted score", "the accepted render"),
        execution_environment={"class": "clean_rebuild"},
        procedure="not designed yet", evaluator="owner",
        success_criteria=("an inferred candidate blindly beats the declared naive control",))
    payload.update(overrides)
    return sr.prepare(**payload)


def test_a_challenge_cannot_start_before_the_master_is_accepted():
    with pytest.raises(sr.SystemReferenceError, match="cannot precede an accepted master"):
        _challenge(master_state="master_qualified")


def test_the_withheld_answer_may_not_be_inside_the_allowed_evidence():
    with pytest.raises(sr.SystemReferenceError, match="hands over the answer"):
        _challenge(allowed_evidence=({"kind": "score", "sha256": "c" * 64},))


def test_the_state_machine_advances_in_order_and_repeats_idempotently():
    challenge = _challenge()
    assert challenge.state == sr.PREPARED
    with pytest.raises(sr.SystemReferenceError, match="not a legal move"):
        sr.advance(challenge, sr.PASSED)

    for state in (sr.ANSWER_WITHHELD, sr.EXECUTED, sr.EVALUATED):
        challenge = sr.advance(challenge, state)
    passed = sr.advance(challenge, sr.PASSED, findings=("recovered",))
    assert passed.state == sr.PASSED
    # Repeating the same result is a no-op, not an error.
    assert sr.advance(passed, sr.PASSED, findings=("recovered",)) == passed
    with pytest.raises(sr.SystemReferenceError, match="not idempotent"):
        sr.advance(passed, sr.PASSED, findings=("actually, no",))


def test_only_a_passed_challenge_may_complete_a_system_reference():
    challenge = _challenge()
    for state in (sr.ANSWER_WITHHELD, sr.EXECUTED, sr.EVALUATED):
        challenge = sr.advance(challenge, state)
    failed = sr.advance(challenge, sr.FAILED, findings=("could not recover",))

    assert sr.may_complete_system_reference(failed) is False
    receipt = sr.result_receipt(failed)
    assert receipt["verdict"] == "FAILED"
    assert receipt["state"]["system_reference_complete"] is False
    assert receipt["state"]["completed_system_references"] == 0
    # A failed challenge never revokes the accepted master.
    assert receipt["state"]["accepted_album_master_revoked"] is False

    passed = sr.advance(challenge, sr.PASSED, findings=("recovered",))
    assert sr.may_complete_system_reference(passed) is True
    assert sr.result_receipt(passed)["state"]["completed_system_references"] == 1


def test_the_result_receipt_binds_identities_and_carries_no_material():
    from earcrate.evidence.receipts import verify_body_free

    challenge = _challenge()
    for state in (sr.ANSWER_WITHHELD, sr.EXECUTED, sr.EVALUATED, sr.PASSED):
        challenge = sr.advance(challenge, state)
    receipt = sr.result_receipt(challenge)
    assert verify_body_free(receipt) == []
    assert receipt["bound_authority"]["accepted_master_authority_sha256"] == "b" * 64
    assert receipt["withheld"]["identity_count"] == 1
    assert "c" * 64 not in json.dumps(receipt), "the withheld identity is not the material"


def test_an_unevaluated_challenge_has_no_result():
    with pytest.raises(sr.SystemReferenceError, match="only an evaluated challenge"):
        sr.result_receipt(_challenge())
