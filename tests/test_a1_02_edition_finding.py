"""Gates for A1-02's edition question, and for the answer the evidence actually gives.

The commission asks which recording the score was authored against. Tracing the score
lineage answers: none, and the absence is structural rather than a gap in the record.
The score branch is sealed as never having opened a recording, because branch
independence is what makes the later cross-modal convergence claim mean anything.

These gates keep two things true. The finding must stay checkable against the sealed
proof it came from -- not merely asserted in prose. And the audio answer key must stay
visibly unbound until an owner declares the edition and structural fit confirms it,
rather than drifting into "bound" because some file was acquired.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.album import bindings as bd  # noqa: E402
from earcrate.album import commission as cm  # noqa: E402

MANIFEST = ROOT / "configs" / "album_one" / "manifest.v1.json"
SCORE_PROOF = ROOT / "proofs" / "specimens" / "children_v1.score-side.proof.json"
SPECIMEN = ROOT / "specimens" / "children_v1.json"

EXPECTED = {
    "audio_answer_key": ("audio_recording", "answer_key"),
    "printed_score": ("printed_score", "material"),
    "symbolic_score_or_midi": ("midi", "material"),
    "performance_rack": ("rack_preset", "tooling"),
}


def _row() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(row for row in manifest["tracks"] if row["track_id"] == "A1-02")


def test_a1_02_declares_four_typed_binding_requirements():
    commission = cm.from_ledger(json.loads(MANIFEST.read_text(encoding="utf-8")), "A1-02")
    typed = {row.role: row for row in commission.typed_bindings}
    assert set(typed) == set(EXPECTED), "the four A1-02 roles are fixed by the commission"
    for role, (modality, _authority) in EXPECTED.items():
        assert typed[role].modality == modality
        assert modality in bd.MODALITIES, f"{modality} must be expressible as a binding"


def test_the_audio_answer_key_is_blocking_and_carries_an_edition_constraint():
    """A title is not an edition, and a plausible edition is not an answer key."""
    requirement = next(row for row in _row()["binding_requirements"]
                       if row["role"] == "audio_answer_key")
    assert requirement["status"] == "unbound"
    assert requirement["blocking"] is True
    constraint = requirement["edition_constraint"]
    for term in ("catalog number", "territory", "release date", "mix or version title"):
        assert term in constraint, f"the edition constraint omits {term!r}"
    assert "control candidate, not the answer key" in constraint


def test_the_edition_finding_still_matches_the_sealed_proof_it_came_from():
    """The finding is checkable, not merely asserted.

    If a future score-side proof ever *did* consult a recording, this gate fails and
    the finding has to be rewritten rather than quietly outliving its evidence.
    """
    finding = _row()["edition_finding"]
    assert finding["answer"].startswith("none")
    assert finding["candidates_in_private_custody"] == 0

    proof = json.loads(SCORE_PROOF.read_text(encoding="utf-8"))
    assert proof["boundary"]["recording_consulted_by_score_branch"] is False
    assert proof["score_branch"]["checks"]["audio_branch_consulted"] is False
    assert proof["boundary"]["independent_audio_inference_used"] is False

    # The independence rule the finding rests on must still be doctrine.
    doctrine = (ROOT / "docs" / "CHILDREN_BUFFALO_GATE.md").read_text(encoding="utf-8")
    assert "The score branch never opens the reference recording" in doctrine
    assert "A score-derived artifact in the audio ledger is an independence violation" in doctrine


def test_the_printed_tempo_is_a_page_reading_not_a_measurement():
    """130 bpm identifies the sheet, not a recording, and cannot select an edition."""
    annotations = json.loads(
        (ROOT / "specimens" / "children_v1.annotations.json").read_text(encoding="utf-8"))
    tempo = annotations["tempo"]
    assert tempo["bpm"] == 130.0
    assert "bbox" in tempo and tempo["page"] == 1, \
        "the tempo is read off the printed page; treating it as an audio measurement " \
        "would let a sheet marking pick a commercial edition"


def test_the_specimen_still_reports_the_audio_branch_unbound():
    metadata = json.loads(SPECIMEN.read_text(encoding="utf-8"))["metadata"]
    assert metadata["audio_answer_status"] == "unbound"
    assert metadata["cross_modal_gate_status"] == "pending_audio"
    assert metadata["score_answer_key_status"] == "bound"


def test_an_unestablished_edition_cannot_pass_as_a_binding():
    """The schema must keep a plausible file visibly short of an answer key."""
    candidate = bd.edition_candidate(
        "children-candidate", "audio_answer_key", "audio_recording",
        note="edition not established by the commission")
    assert not candidate.is_ready()
    assert candidate.custody_class == "unbound"

    # Even a fully identified file is not ready against this requirement until the
    # edition constraint is satisfied.
    commission = cm.from_ledger(json.loads(MANIFEST.read_text(encoding="utf-8")), "A1-02")
    requirement = commission.requirement("audio_answer_key")
    identified = bd.SourceBinding(
        source_id="children-some-cd-rip", role="audio_answer_key",
        modality="audio_recording", authority_class="answer_key",
        privacy_class="private_local", custody_class="private_custody",
        identities={"container_sha256": "a" * 64, "canonical_pcm_sha256": "b" * 64},
        edition={}, verified=True)
    problems = identified.readiness(requirement)
    assert any("edition constraint" in row for row in problems), \
        "a verified file with no established edition must not read as the answer key"
