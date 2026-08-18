"""Gates keeping a useful control candidate from becoming the answer key.

The file measured here is real, structurally informative, and not the commissioned
delivery. Both halves of that have to stay true. The failure mode is not that someone
lies about it; it is that a well-measured object with verified identities gradually
reads as *the* recording because nothing in the repository keeps saying otherwise.

So these gates say otherwise, in six ways.
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
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

ANALYSIS = ROOT / "proofs" / "album_one" / "a1-02-audio-control-candidate.public.json"
MANIFEST = ROOT / "configs" / "album_one" / "manifest.v1.json"
SCORE_PROOF = ROOT / "proofs" / "specimens" / "children_v1.score-side.proof.json"


def _analysis() -> dict:
    return json.loads(ANALYSIS.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _row() -> dict:
    return next(r for r in _manifest()["tracks"] if r["track_id"] == "A1-02")


def test_the_control_candidate_cannot_satisfy_the_audio_answer_key():
    """Verified identities describe a real object; they do not commission it."""
    analysis = _analysis()
    assert analysis["classification"]["delivery_fit"] == "NONFIT"
    assert analysis["classification"]["audio_answer_key"] == "unbound"
    assert analysis["classification"]["admissible_as_album_source"] is False
    assert analysis["classification"]["admissible_as_structural_control"] is True
    assert analysis["classification"]["rights_eligibility"] == "not_established"

    commission = cm.from_ledger(_manifest(), "A1-02")
    requirement = commission.requirement("audio_answer_key")
    candidate = bd.SourceBinding(
        source_id="a1-02-control-candidate", role="audio_answer_key",
        modality="audio_recording", authority_class="answer_key",
        privacy_class="private_local", custody_class="private_custody",
        identities={"container_sha256": analysis["candidate"]["container_sha256"],
                    "canonical_pcm_sha256": analysis["candidate"]["canonical_pcm_sha256"]},
        edition={}, verified=True)
    problems = candidate.readiness(requirement)
    assert problems, "a measured control candidate must not read as the answer key"
    assert any("edition constraint" in row for row in problems)


def test_the_control_candidate_cannot_make_the_required_bindings_ready():
    commission = cm.from_ledger(_manifest(), "A1-02")
    analysis = _analysis()
    bound = {
        "audio_answer_key": bd.SourceBinding(
            source_id="a1-02-control-candidate", role="audio_answer_key",
            modality="audio_recording", authority_class="answer_key",
            privacy_class="private_local", custody_class="private_custody",
            identities={"container_sha256": analysis["candidate"]["container_sha256"],
                        "canonical_pcm_sha256":
                            analysis["candidate"]["canonical_pcm_sha256"]},
            edition={}, verified=True),
    }
    report = bd.readiness_report(commission, bound)
    assert report["all_required_bindings_ready"] is False
    row = next(r for r in report["requirements"] if r["role"] == "audio_answer_key")
    assert row["ready"] is False


def test_the_control_candidate_cannot_enter_the_score_realizer():
    """Branch independence, enforced structurally rather than promised.

    The score branch is sealed as never having opened a recording, and that is what
    makes the eventual convergence claim mean anything. Custody may read audio;
    anything that produces a performance may not.
    """
    analysis = _analysis()
    pcm = analysis["candidate"]["canonical_pcm_sha256"]
    audio_readers = {"custody.py"}          # the only module allowed to open audio
    package = ROOT / "earcrate" / "a1_02"

    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name not in audio_readers:
            for library in ("librosa", "soundfile", "decode_s32", "canonical_pcm("):
                assert library not in source, (
                    f"{path.name} reaches for audio via {library!r}; only "
                    f"{sorted(audio_readers)} may, or branch independence is gone")
        assert pcm not in source, f"{path.name} embeds the control candidate's PCM identity"

    # And the score-side timeline must remain derivable with no audio present at all.
    from earcrate.a1_02 import score_timeline as st
    assert len(st.performed_order()) == 105


def test_the_public_projection_carries_no_custody_location():
    analysis = load_sealed(ANALYSIS)
    assert verify_body_free(analysis) == []
    assert analysis["candidate"]["custody_location_included"] is False
    assert analysis["boundary"]["beat_arrays_included"] is False
    assert analysis["boundary"]["embedded_source_metadata_included"] is False

    text = json.dumps(analysis)
    for leak in ("Downloads", "Users", "beat_times", "youtube", "YouTube"):
        assert leak not in text, f"the public projection leaks {leak!r}"


def test_the_score_side_proof_and_provenance_are_unchanged():
    """Measuring a recording must not disturb the branch that never heard one."""
    proof = json.loads(SCORE_PROOF.read_text(encoding="utf-8"))
    assert proof["boundary"]["recording_consulted_by_score_branch"] is False
    assert proof["score_branch"]["checks"]["audio_branch_consulted"] is False
    assert proof["score_branch"]["counts"]["performed_measures"] == 105

    from earcrate.a1_07_full_form.provenance import adapter_tree_digest
    from earcrate.a1_07_master.provenance import master_tree_digest
    assert adapter_tree_digest(ROOT)["digest"] == \
        "c14de5fc93a6c02e29c85d77b9dbee868bfe280a7d215f46477793c96247dfdd"
    assert master_tree_digest(ROOT)["digest"] == \
        "24ae757819930273bb8a7e79f549a20c35ff36c8769f68b8917e0af996be17a6"


def test_the_answer_key_status_and_album_authority_did_not_move():
    row = _row()
    assert row["edition_finding"]["state_until_then"]["answer_key_status"] == \
        "edition_declared_pending_acquisition_and_structural_fit"
    requirement = next(r for r in row["binding_requirements"]
                       if r["role"] == "audio_answer_key")
    assert requirement["blocking"] is True
    assert requirement["status"] == "edition_declared_pending_acquisition"

    manifest = _manifest()
    assert manifest["completed_album_master_count"] == 1
    assert manifest["completed_system_reference_count"] == 0

    analysis = _analysis()
    assert analysis["album_authority"]["changed"] is False
    assert analysis["album_authority"]["audio_answer_key_status"] == "unbound"


def test_the_duration_fraction_is_not_called_score_coverage():
    """0.436 is an envelope, not a claim about matched material."""
    relation = _analysis()["score_side_relation"]
    assert relation["score_over_core_fraction"] == pytest.approx(0.436, abs=0.005)
    assert relation["core_over_score_ratio"] == pytest.approx(2.291, abs=0.005)
    assert "NOT matched score coverage" in relation["caution"]
    assert "twelve-anchor comparator" in relation["caution"]

    text = json.dumps(_analysis()).lower()
    assert "score coverage" not in text.replace("not matched score coverage", "")


def test_the_measurement_records_its_own_estimator_agreement():
    """Four estimators agreeing is the reason to trust 136; record it, do not assert it."""
    measurement = _analysis()["measurement"]
    assert measurement["pulse_bpm"] == 136.0
    assert measurement["estimator_agreement"] is True
    assert len(measurement["estimators"]) == 4
    values = set(measurement["estimators"].values())
    assert len(values) == 1, f"the four estimators disagree: {sorted(values)}"
    # They land on 135.999 before rounding; the reported pulse must not round away
    # from what they actually measured.
    assert round(values.pop(), 1) == measurement["pulse_bpm"]
    assert measurement["inter_beat_interval_sd_seconds"] < 0.02

    pre_roll = _analysis()["pre_roll"]
    assert pre_roll["within_one_beat"] is True
    assert abs(pre_roll["core_minus_declared_seconds"]) < 2.0

    tooling = _analysis()["tooling"]
    for key in ("librosa", "numpy", "scipy", "python", "hop_length", "segment_count"):
        assert tooling[key], f"the analysis does not record {key}"
