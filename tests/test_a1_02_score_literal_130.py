"""Gates for score authority v2 and the 130-to-136 ordering rule.

The score is now derived from a recovered PDF rather than from a vanished pipeline, so
what needs protecting has changed. The extraction must satisfy the printed contract;
the performed compilation must follow the traversal and refuse any tempo but the
printed one; and a faster reading must be a child that re-times its parent rather than
an edit that quietly corrects it with the commercial recording's pulse.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02 import score_timeline as st  # noqa: E402
from earcrate.a1_02.score_v2 import compile_midi as cm  # noqa: E402
from earcrate.a1_02.score_v2 import extraction as ex  # noqa: E402
from earcrate.a1_02.score_v2 import interpretation as itp  # noqa: E402
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

MIGRATION = ROOT / "proofs" / "album_one" / "a1-02-score-authority-v2-migration.public.json"
RECONCILIATION = ROOT / "proofs" / "album_one" / "a1-02-note-reconciliation-v1.public.json"


def _extraction() -> dict:
    """A synthetic extraction shaped like the real one, so gates need no private files."""
    notes = []
    for measure in range(1, st.PRINTED_MEASURES + 1):
        for beat in range(4):
            for staff, pitch in ((1, 65 + beat), (2, 41 + beat)):
                notes.append({
                    "index": len(notes), "page": 1 + (measure - 1) // 18, "staff": staff,
                    "printed_measure": measure, "voice": "1", "beat_offset": float(beat),
                    "duration_beats": 1.0, "pitch": pitch, "step": "F", "octave": 4,
                    "alter": 0, "tie_in": False, "tie_out": False, "in_chord": False,
                    "dynamic": "mf"})
    return {
        "kind": "earcrate_a1_02_score_extraction_v2", "schema_version": 1,
        "status": "omr_candidate_unreviewed", "source_pdf_sha256": "e" * 64,
        "printed_measures_seen": st.PRINTED_MEASURES,
        "printed_measure_range": [1, st.PRINTED_MEASURES],
        "printed_note_count": len(notes),
        "performed_note_count": 0,
        "notes_per_printed_measure": {m: 8 for m in range(1, st.PRINTED_MEASURES + 1)},
        "staff_distribution": {"1": len(notes) // 2, "2": len(notes) // 2},
        "voice_distribution": {"1": len(notes)},
        "tie_counts": {"tie_in": 0, "tie_out": 0}, "chord_member_count": 0,
        "dynamics_seen": ["mf"], "notes": notes,
    }


# --- the printed contract ----------------------------------------------------------

def test_the_hard_gate_holds_the_extraction_to_the_printed_score():
    annotations = json.loads(
        (ROOT / "specimens" / "children_v1.annotations.json").read_text(encoding="utf-8"))
    good = _extraction()
    assert ex.hard_gate(good, annotations) == []

    short = _extraction()
    short["printed_measures_seen"] = 68
    short["printed_measure_range"] = [1, 68]
    assert ex.hard_gate(short, annotations)

    one_staff = _extraction()
    one_staff["staff_distribution"] = {"1": 100}
    assert any("two staves" in row for row in ex.hard_gate(one_staff, annotations))

    silent = _extraction()
    silent["notes_per_printed_measure"][17] = 0
    assert any("carries no notes" in row for row in ex.hard_gate(silent, annotations))


def test_the_compilation_follows_the_traversal_and_names_its_source_notes():
    performed = cm.compile_performed(_extraction())
    assert performed["performed_measures"] == 105
    assert performed["interpretation_id"] == "score_literal_130"
    assert performed["tempo_bpm"] == 130.0
    assert performed["reference_recording_consulted"] is False
    assert performed["measured_control_pulse_used"] is False

    occurrences = {row["performed_occurrence"] for row in performed["notes"]}
    assert occurrences == set(range(1, 106)), "a performed occurrence fell silent"
    for row in performed["notes"]:
        assert "source_note_index" in row, "a performed note cannot name its printed note"
        assert row["printed_measure"] in range(1, 70)


def test_the_compilation_refuses_any_tempo_but_the_printed_one():
    """The measured 136 may not enter score extraction by the back door."""
    with pytest.raises(cm.CompileError, match="child interpretation"):
        cm.compile_performed(_extraction(), tempo_bpm=136.0)


def test_the_midi_ledger_seals_with_this_repositorys_own_semantics():
    from earcrate.midi.model import midi_validate_ledger

    ledger = cm.to_midi_ledger(cm.compile_performed(_extraction()))
    midi_validate_ledger(ledger)              # raises if the schema or seal is wrong
    assert cm.semantic_identity(ledger) == ledger["semantic_sha256"]
    assert [track["name"] for track in ledger["tracks"]] == [
        "score_literal_130", "Right Hand", "Left Hand"]


# --- the 130 to 136 ordering rule --------------------------------------------------

def test_a_child_may_retime_its_parent_and_nothing_else():
    parent = cm.compile_performed(_extraction())
    child = itp.derive_child(parent, tempo_bpm=136.0,
                             interpretation_id="performed_interpretation_136",
                             rationale="the control recording's measured pulse")

    assert itp.validate_child(child, parent) == []
    assert child["parent"] == "score_literal_130"
    assert child["tempo_bpm"] == 136.0
    assert child["parent_tempo_bpm"] == 130.0
    # Musical position is unchanged; only clock time moves.
    assert child["notes"][0]["start_beat"] == parent["notes"][0]["start_beat"]
    assert "start_seconds" in child["notes"][0]
    assert child["notes"][-1]["end_seconds"] < parent["performed_measures"] * 4 * 60 / 130


def test_a_child_that_edits_a_note_is_refused():
    parent = cm.compile_performed(_extraction())
    for field, value in (("pitch", 60), ("duration_beats", 2.0), ("staff", 2),
                         ("velocity", 100), ("start_beat", 4.0), ("printed_measure", 9)):
        child = itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x",
                                 rationale="test")
        child["notes"] = [dict(row) for row in child["notes"]]
        child["notes"][0][field] = value
        problems = itp.validate_child(child, parent)
        assert any(field in row for row in problems), f"editing {field} was not refused"


def test_a_child_may_not_change_the_traversal_or_lose_a_note():
    parent = cm.compile_performed(_extraction())

    dropped = itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x",
                               rationale="test")
    dropped["notes"] = dropped["notes"][:-1]
    assert any("note count changed" in row for row in itp.validate_child(dropped, parent))

    renavigated = itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x",
                                   rationale="test")
    renavigated["navigation"] = ["8->5 repeat"]
    assert any("navigation" in row for row in itp.validate_child(renavigated, parent))


def test_a_child_cannot_precede_its_parent_being_sealed():
    parent = cm.compile_performed(_extraction())
    child = itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x",
                             rationale="test")

    allowed, problems = itp.may_seal_child(False, child, parent)
    assert allowed is False
    assert any("cannot precede" in row for row in problems)

    allowed, problems = itp.may_seal_child(True, child, parent)
    assert allowed is True and problems == []


def test_a_child_of_a_child_is_refused():
    parent = cm.compile_performed(_extraction())
    child = itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x",
                             rationale="test")
    with pytest.raises(itp.InterpretationError, match="not "):
        itp.derive_child(child, tempo_bpm=140.0, interpretation_id="y", rationale="test")


def test_an_unexplained_retiming_is_refused():
    """An undeclared tempo change is a correction wearing an interpretation's clothes."""
    parent = cm.compile_performed(_extraction())
    with pytest.raises(itp.InterpretationError, match="why"):
        itp.derive_child(parent, tempo_bpm=136.0, interpretation_id="x", rationale="  ")


# --- the landed receipts -----------------------------------------------------------

def test_the_migration_records_new_identities_and_reuses_no_legacy_one():
    receipt = load_sealed(MIGRATION)
    assert verify_body_free(receipt) == []
    assert receipt["source_score"]["exact_historical_pdf"] is True
    assert receipt["source_score"]["observed_sha256"] == \
        receipt["source_score"]["expected_sha256"]
    assert receipt["legacy"]["midi_bytes_available"] is False
    assert "never reused for regenerated bytes" in receipt["legacy"]["rule"]

    identities = receipt["new_identities"]
    legacy_midi = "7d7342ef9d60228be4f94464764edf293b2052f6667676cdaf137a1dc94655c9"
    assert identities["midi_container_sha256"] != legacy_midi
    assert identities["midi_container_stable_across_writes"] is True
    assert identities["performance_bit_identical_across_renders"] is True

    assert receipt["derivation"]["audio_reference_consulted"] is False
    assert receipt["derivation"]["measured_control_pulse_used"] is False
    assert receipt["derivation"]["tempo_bpm"] == 130.0
    assert receipt["omr"]["omr_is_the_evidence_object"] is True
    assert receipt["omr"]["raw_export_self_authorizing"] is False
    assert receipt["child_interpretations"]["performed_interpretation_136"].startswith(
        "not created")


def test_the_reconciliation_treats_1257_as_a_witness_not_a_quota():
    receipt = load_sealed(RECONCILIATION)
    assert verify_body_free(receipt) == []
    assert receipt["witness"]["historical_performed_note_count"] == 1257
    assert receipt["witness"]["role"].startswith("comparison witness")
    assert receipt["v2"]["performed_note_count"] == 1253
    assert receipt["difference"]["notes"] == -4
    assert "was never the printed count" in receipt["finding"]
    assert "does not exist" in receipt["why_the_gap_cannot_be_diffed_note_by_note"]
    assert receipt["internal_audit"]["ties_balanced"] is True
    assert "Nothing was added, split, merged or suppressed" in receipt["no_adjustment_made"]


def test_the_omr_toolchain_is_bound_by_identity():
    receipt = load_sealed(ROOT / "proofs" / "album_one" / "a1-02-omr-toolchain-v1.public.json")
    assert receipt["tool"]["reported_version"] == "5.11.0"
    assert len(receipt["tool"]["installer_sha256"]) == 64
    assert len(receipt["tool"]["executable_sha256"]) == 64
    assert receipt["authority"]["omr_output_is_score_authority"] is False
    assert receipt["authority"]["raw_export_self_authorizing"] is False
