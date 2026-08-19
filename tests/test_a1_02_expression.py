"""Gates for the expressive reading.

An expressive performance is an interpretation of this score. A reading that quietly
drops, moves or repitches a note is a different score, so the boundary is what these
protect: velocity, sounding length and micro-timing may move, and nothing else may.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.performance import expression as ex  # noqa: E402
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-02-expressive-performance-v1.public.json"


def _flat(measures: int = 4) -> dict:
    """A flat performance: one bass note and a three-note right-hand chord per bar."""
    notes = []
    for measure in range(measures):
        base = measure * 4.0
        notes.append({"source_note_index": len(notes), "printed_measure": measure + 1,
                      "performed_occurrence": measure + 1, "section": "A" if measure < 2 else "B",
                      "page": 1, "staff": 2, "voice": "5", "start_beat": base,
                      "duration_beats": 4.0, "pitch": 41, "velocity": 72,
                      "tie_in": False, "tie_out": False})
        # Spread across the bar, not stacked on the downbeat: a cadence ramp has
        # nothing to ramp across when every note starts at the same instant, which is
        # a property of the fixture rather than of the music.
        for beat, chord in enumerate(((60, 64, 67), (60, 64, 67),
                                      (59, 62, 67), (59, 62, 67))):
            for pitch in chord:
                notes.append({"source_note_index": len(notes),
                              "printed_measure": measure + 1,
                              "performed_occurrence": measure + 1,
                              "section": "A" if measure < 2 else "B",
                              "page": 1, "staff": 1, "voice": "1",
                              "start_beat": base + beat, "duration_beats": 1.0,
                              "pitch": pitch, "velocity": 72,
                              "tie_in": False, "tie_out": False})
    return {"kind": "earcrate_a1_02_performed_score_v2_child",
            "interpretation_id": "performed_interpretation_136", "tempo_bpm": 136.0,
            "navigation": [], "notes": notes}


def test_the_top_right_hand_note_is_voiced_as_the_tune():
    shaped = ex.shape(_flat())
    roles = {}
    for note in shaped["notes"]:
        roles.setdefault(note["expression"]["role"], []).append(note)

    assert roles["melody"], "no note was treated as the tune"
    for note in roles["melody"]:
        assert note["staff"] == 1
        assert note["pitch"] == 67, "the tune is the top of the right hand, not any of it"
    assert len(roles["melody"]) < len(roles["inner"]),         "one note per onset is the tune; the rest accompany it"
    # The tune sits above its own accompaniment.
    melody = sum(n["velocity"] for n in roles["melody"]) / len(roles["melody"])
    inner = sum(n["velocity"] for n in roles["inner"]) / len(roles["inner"])
    assert melody > inner + 10, f"melody {melody} is not voiced above inner {inner}"


def test_a_cadence_relaxes_in_time_and_in_level():
    shaped = ex.shape(_flat())
    cadence = [n for n in shaped["notes"] if n["expression"]["cadence"]]
    assert cadence, "no cadence was shaped"
    assert any(n["expression"]["timing_delay_beats"] > 0 for n in cadence)
    for note in cadence:
        assert note["expression"]["velocity_delta"] < 20


def test_expression_widens_what_the_instrument_is_asked_for():
    """The flat reading uses six velocities; a played one should use many more."""
    flat = _flat(8)
    shaped = ex.shape(flat)
    before = {note["velocity"] for note in flat["notes"]}
    after = {note["velocity"] for note in shaped["notes"]}
    assert len(after) > len(before) * 3, f"{len(before)} -> {len(after)} velocities"


def test_the_reading_may_not_change_which_notes_they_are():
    flat = _flat()
    shaped = ex.shape(flat)
    assert ex.validate(shaped, flat) == []

    for field, value in (("pitch", 61), ("printed_measure", 9), ("staff", 1),
                         ("voice", "9"), ("performed_occurrence", 4)):
        broken = ex.shape(flat)
        broken["notes"] = [dict(row) for row in broken["notes"]]
        broken["notes"][0][field] = value
        problems = ex.validate(broken, flat)
        assert any(field in row for row in problems), f"changing {field} was not refused"


def test_a_dropped_note_and_a_retiming_are_both_refused():
    flat = _flat()
    dropped = ex.shape(flat)
    dropped["notes"] = dropped["notes"][:-1]
    assert any("note count changed" in row for row in ex.validate(dropped, flat))

    retimed = ex.shape(flat)
    retimed["tempo_bpm"] = 144.0
    assert any("may not also re-time" in row for row in ex.validate(retimed, flat))


def test_micro_timing_may_bend_but_not_reorder():
    flat = _flat()
    shaped = ex.shape(flat)
    starts = [row["start_beat"] for row in shaped["notes"]]
    assert starts == sorted(starts)

    scrambled = ex.shape(flat)
    scrambled["notes"] = [dict(row) for row in scrambled["notes"]]
    scrambled["notes"][0]["start_beat"] = 99.0
    problems = ex.validate(scrambled, flat)
    assert any("reordered" in row or "rewriting" in row for row in problems)


def test_shaping_nothing_is_refused():
    with pytest.raises(ex.ExpressionError):
        ex.shape({"tempo_bpm": 136.0, "notes": []})


# --- the landed receipt --------------------------------------------------------------

def test_the_receipt_records_the_tempo_decision_and_its_parent():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []
    decision = receipt["tempo_decision"]
    assert decision["production_interpretation"] == "performed_interpretation_136"
    assert decision["score_literal_parent"] == "score_literal_130"
    assert decision["further_tempo_families"] == "not authorized"


def test_both_renders_are_deterministic_and_account_for_every_note():
    receipt = load_sealed(RECEIPT)
    for label in ("flat", "expressive"):
        row = receipt["renders"][label]
        assert row["events_rendered"] == 1253
        assert row["bit_identical_across_executions"] is True
        assert row["stem_sum_reproduces_master"] is True

    flat, played = receipt["renders"]["flat"], receipt["renders"]["expressive"]
    assert flat["master_sha256"] != played["master_sha256"]
    assert played["distinct_velocities"] > flat["distinct_velocities"] * 5
    assert played["rack_samples_used"] > flat["rack_samples_used"], \
        "a played reading should reach more of the instrument than a triggered one"


def test_the_pack_is_blind_only_on_which_is_played():
    receipt = load_sealed(RECEIPT)
    pack = receipt["owner_pack"]
    assert pack["blind"] == "which letter is played and which is triggered"
    assert pack["assignment_map_withheld"] is True
    assert pack["control"].startswith("score_literal_130")
    assert pack["admissible_outcomes"] == ["A", "B", "tie", "reject_both"]
    assert pack["revisions_remaining_after_verdict"] == 1
    assert pack["level_matched_lufs"] == min(pack["measured_lufs"].values())
    assert "no framework" in receipt["no_new_organs"]


def test_the_negative_result_is_recorded_without_being_promoted():
    """A tie is a result. The gate is that it is kept as one, not quietly upgraded."""
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    verdict = receipt["verdict"]
    assert verdict["outcome"] == "tie"
    assert verdict["authority"] == "owner"
    assert verdict["incumbent_retained"] == "flat 136 performance"
    assert verdict["further_expression_trials"] == "not authorized"
    assert receipt["state"]["expressive_performance"] == \
        "preserved as a negative result, not promoted"
    assert receipt["state"]["album_authority_changed"] is False

    # The tie is about the bundle, and the receipt has to say so: seven mechanisms moved
    # at once, so nothing here licenses a claim about any one of them.
    assert "at once" in receipt["experimental_weakness"]
    assert "individually useless" in receipt["what_the_tie_does_not_show"]


def test_the_disclosed_assignment_is_the_one_the_audio_forced():
    """Disclosure after a verdict is worth nothing if the map could have been chosen after it."""
    import hashlib

    receipt = load_sealed(RECEIPT)
    renders = receipt["renders"]
    nonce = hashlib.sha256((renders["flat"]["master_sha256"]
                            + renders["expressive"]["master_sha256"]).encode()).hexdigest()
    first = "expressive" if int(nonce[:8], 16) % 2 == 0 else "flat"

    disclosed = receipt["verdict"]["assignment_disclosed_after_verdict"]
    assert disclosed["A"] == first, "the disclosed map is not the one the render digests force"
    assert disclosed["B"] != disclosed["A"]
