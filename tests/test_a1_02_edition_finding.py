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


PRE_BOUND_STATES = ("unbound", "edition_declared_pending_acquisition")


def test_the_audio_answer_key_is_blocking_and_not_yet_bound():
    """A declaration is not an acquisition, and an acquisition is not an answer key."""
    requirement = next(row for row in _row()["binding_requirements"]
                       if row["role"] == "audio_answer_key")
    assert requirement["blocking"] is True
    assert requirement["status"] in PRE_BOUND_STATES, \
        "the answer key may not read as bound before acquisition and structural fit"
    assert not requirement.get("identities"), \
        "no identities can exist before the file does"

    constraint = requirement["edition_constraint"]
    for term in ("Dream Version", "full-length", "Dreamland", "track 1",
                 "control candidate, not the answer key"):
        assert term in constraint, f"the edition constraint omits {term!r}"
    for excluded in ("Radio Edit", "Message Version", "Guitar Mix", "streaming captures"):
        assert excluded in constraint, f"the constraint does not exclude {excluded!r}"
    assert requirement["acquisition_capture"], "acquisition must state what it captures"


def test_the_declaration_is_a_commission_choice_not_a_lineage_claim():
    """The distinction the whole finding rests on, kept explicit in the ledger."""
    declaration = _row()["edition_declaration"]
    assert declaration["edition_decision"] == "commissioned"
    assert declaration["declared_by"] == "owner"
    assert declaration["selected_version"].startswith("Children (Dream Version)")
    assert declaration["track_number"] == 1
    assert declaration["medium"] == "digital lossless download"
    assert declaration["catalog_number"].startswith("not_assigned"), \
        "borrowing a catalog number from a physically different release would be a lie"
    assert "derived from no recording at all" in declaration["not_a_lineage_claim"]
    assert "takes no part in version selection" in \
        declaration["sheet_tempo_excluded_from_selection"]

    excluded = declaration["excluded_versions"]
    for variant in ("Dream Radio Edit", "Radio Edit", "Eat Me Edit", "Message Version",
                    "Original Version / Original Mix", "Guitar Mix", "Dream Club Version",
                    "streaming captures", "user edits", "shortened compilations"):
        assert variant in excluded, f"{variant!r} must be excluded by name"


def test_structural_fit_is_required_and_nonfit_is_not_a_search():
    criteria = _row()["structural_fit_criteria"]
    for needle in ("full-length Dream Version identity",
                   "repeat expansion compatible with 69 printed measures becoming 105 "
                   "performed",
                   "complete ending and coda present",
                   "tempo-map compatibility"):
        assert needle in criteria["criteria"], f"the fit criteria omit {needle!r}"
    assert criteria["outcomes"]["FIT"].startswith("bind this exact downloaded object")
    assert "control candidate" in criteria["outcomes"]["NONFIT"]
    assert "do not silently select another version" in criteria["outcomes"]["NONFIT"]
    assert "aligns most prettily" in criteria["why_nonfit_is_not_a_search"]


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


def test_the_specimen_and_the_ledger_agree_that_audio_is_still_pending():
    """Two documents describing one state must not drift apart."""
    metadata = json.loads(SPECIMEN.read_text(encoding="utf-8"))["metadata"]
    assert metadata["cross_modal_gate_status"] == "pending_audio"
    assert metadata["score_answer_key_status"] == "bound"
    assert metadata["audio_answer_status"] == \
        "edition_declared_pending_acquisition_and_structural_fit"

    finding = _row()["edition_finding"]["state_until_then"]
    assert finding["answer_key_status"] == metadata["audio_answer_status"]
    assert finding["cross_modal_gate_status"] == metadata["cross_modal_gate_status"]
    assert metadata["selected_version"] == _row()["edition_declaration"]["selected_version"]


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
