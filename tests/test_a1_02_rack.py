"""Gates for the rack binding and its refusals.

A rack's value is entirely in what it declines to do. Coverage that cannot name the
zone it used, a note placed by silent substitution, a sample that changed after it was
bound — each would make a render sound fine and mean nothing. So every refusal here is
constructed on purpose and asserted.

The SFZ fixtures are written in the test. No sample library is required to check that
the binding logic is right.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.performance import demand as dm  # noqa: E402
from earcrate.a1_02.performance import rack as rk  # noqa: E402
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

RECEIPT = ROOT / "proofs" / "album_one" / "a1-02-rack-realization-v1.public.json"

SFZ = """
// a two-key instrument with two velocity layers, one release sample and one pedal zone
<group> amp_veltrack=73 ampeg_release=1
<region> sample=s\\C4v1.wav lokey=59 hikey=61 lovel=1 hivel=63 pitch_keycenter=60
<region> sample=s\\C4v2.wav lokey=59 hikey=61 lovel=64 hivel=127 pitch_keycenter=60
<region> sample=s\\F4v1.wav lokey=64 hikey=66 lovel=1 hivel=63 pitch_keycenter=65
<region> sample=s\\F4v2.wav lokey=64 hikey=66 lovel=64 hivel=127 pitch_keycenter=65
<group> trigger=release volume=-4 amp_veltrack=94
<region> sample=s\\rel1.wav lokey=59 hikey=66 lovel=1 hivel=127 pitch_keycenter=60
<group> group=1 hikey=-1 lokey=-1 on_locc64=126 on_hicc64=127
<region> sample=s\\pedal.wav lokey=-1 hikey=-1
"""


def _sfz(tmp_path: Path, body: str = SFZ) -> Path:
    path = tmp_path / "instrument.sfz"
    path.write_text(body, encoding="utf-8")
    return path


def _demand(events: list[tuple[int, int]]) -> dict:
    return {
        "selected_event_count": len(events),
        "selected_event_identities": [
            {"index": index, "printed_measure": 1, "performed_occurrence": 1,
             "staff": 1, "voice": "1", "pitch": pitch, "velocity": velocity,
             "start_beat": float(index), "duration_beats": 1.0}
            for index, (pitch, velocity) in enumerate(events)],
    }


def test_release_and_pedal_zones_are_not_candidates_for_placing_a_note(tmp_path):
    """The bug this prevents refused all 1,253 events for the wrong reason.

    An SFZ carries release samples and pedal noise as regions too. Treated as note
    zones they cover every event, disagree with the real zones, and the binding
    reports ambiguity everywhere instead of binding anything.
    """
    zones, meta = rk.parse_sfz(_sfz(tmp_path))
    assert meta["region_count"] == 6
    assert meta["note_zone_count"] == 4
    assert meta["release_zone_count"] == 1
    assert meta["controller_zone_count"] == 1

    result = rk.bind(_demand([(60, 80), (65, 40)]), zones)
    assert result["all_events_bound"] is True
    assert result["refused_event_count"] == 0
    assert result["bindings"][0]["sample"] == "s/C4v2.wav"
    assert result["bindings"][1]["sample"] == "s/F4v1.wav"


def test_a_note_with_no_zone_is_refused_not_approximated(tmp_path):
    zones, _ = rk.parse_sfz(_sfz(tmp_path))
    result = rk.bind(_demand([(90, 80)]), zones)
    assert result["all_events_bound"] is False
    assert result["refused_event_count"] == 1
    assert "no zone covers" in result["refusals"][0]["reason"]


def test_excessive_transposition_is_refused(tmp_path):
    """A zone that would have to be stretched too far is not coverage."""
    body = """
<group> ampeg_release=1
<region> sample=s\\C4.wav lokey=48 hikey=72 lovel=1 hivel=127 pitch_keycenter=60
"""
    zones, _ = rk.parse_sfz(_sfz(tmp_path, body))
    close = rk.bind(_demand([(62, 80)]), zones, max_transposition=3)
    assert close["all_events_bound"] is True
    assert close["bindings"][0]["transposition_semitones"] == 2

    far = rk.bind(_demand([(70, 80)]), zones, max_transposition=3)
    assert far["refused_event_count"] == 1
    assert "more than 3 semitones" in far["refusals"][0]["reason"]


def test_zones_that_disagree_are_refused_rather_than_picked_between(tmp_path):
    body = """
<group> ampeg_release=1
<region> sample=s\\one.wav lokey=60 hikey=60 lovel=1 hivel=127 pitch_keycenter=60
<region> sample=s\\two.wav lokey=60 hikey=60 lovel=1 hivel=127 pitch_keycenter=60
"""
    zones, _ = rk.parse_sfz(_sfz(tmp_path, body))
    result = rk.bind(_demand([(60, 80)]), zones)
    assert result["refused_event_count"] == 1
    assert "disagree" in result["refusals"][0]["reason"]


def test_a_mutated_or_missing_sample_is_caught(tmp_path):
    zones, _ = rk.parse_sfz(_sfz(tmp_path))
    binding = rk.bind(_demand([(60, 80)]), zones)

    missing = rk.verify_sources(tmp_path, binding)
    assert missing["sources_intact"] is False
    assert missing["missing"] == ["s/C4v2.wav"]

    sample = tmp_path / "s" / "C4v2.wav"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"original")
    present = rk.verify_sources(tmp_path, binding)
    assert present["sources_intact"] is True

    stale = rk.verify_sources(tmp_path, binding, expected={"s/C4v2.wav": "0" * 64})
    assert stale["sources_intact"] is False
    assert stale["mutated"] == ["s/C4v2.wav"]


def test_note_names_and_numbers_both_read(tmp_path):
    body = """
<group> ampeg_release=1
<region> sample=s\\a.wav lokey=A0 hikey=B0 lovel=1 hivel=127 pitch_keycenter=A0
<region> sample=s\\b.wav lokey=D#1 hikey=E1 lovel=1 hivel=127 pitch_keycenter=D#1
"""
    zones, _ = rk.parse_sfz(_sfz(tmp_path, body))
    assert (zones[0].lokey, zones[0].root) == (21, 21)
    assert (zones[1].lokey, zones[1].root) == (27, 27)


# --- the demand ---------------------------------------------------------------------

def test_the_demand_is_compiled_before_a_rack_is_chosen():
    """Stated as a requirement, so a coverage claim cannot be written to fit a library."""
    performed = {
        "interpretation_id": "score_literal_130", "tempo_bpm": 130.0,
        "notes": [
            {"pitch": 60, "velocity": 80, "start_beat": 0.0, "duration_beats": 2.0,
             "staff": 1, "voice": "1", "printed_measure": 1, "performed_occurrence": 1,
             "tie_out": True},
            {"pitch": 64, "velocity": 40, "start_beat": 0.0, "duration_beats": 1.0,
             "staff": 2, "voice": "5", "printed_measure": 1, "performed_occurrence": 1,
             "tie_out": False},
            {"pitch": 67, "velocity": 100, "start_beat": 1.0, "duration_beats": 1.0,
             "staff": 1, "voice": "1", "printed_measure": 1, "performed_occurrence": 1,
             "tie_out": False},
        ],
    }
    demand = dm.compile_demand(performed)
    assert demand["selected_event_count"] == 3
    assert demand["pitch_range"] == [60, 67]
    assert demand["velocity_range"] == [40, 100]
    assert demand["maximum_polyphony"] == 2, "two notes overlap; the third follows"
    assert demand["sustain_required"] is True
    assert demand["instrument_policy"]["general_midi_fallback_forbidden"] is True
    assert demand["instrument_policy"]["one_coherent_instrument"] is True
    assert len(demand["selected_event_identities"]) == 3

    with pytest.raises(dm.DemandError):
        dm.compile_demand({"tempo_bpm": 130.0, "notes": []})


def test_a_demand_outside_a_piano_is_reported():
    high = {"interpretation_id": "x", "tempo_bpm": 120.0,
            "notes": [{"pitch": 120, "velocity": 80, "start_beat": 0.0,
                       "duration_beats": 1.0, "staff": 1, "voice": "1",
                       "printed_measure": 1, "performed_occurrence": 1, "tie_out": False}]}
    assert dm.within_piano_range(dm.compile_demand(high))


# --- the landed receipt -------------------------------------------------------------

def test_the_realization_receipt_accounts_for_every_event():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []

    binding = receipt["binding"]
    assert binding["selected"] == binding["bound"] == 1253
    assert binding["refused"] == 0
    assert binding["all_events_bound"] is True
    assert binding["sources_intact"] is True
    assert binding["mutated_sources"] == 0

    rack = receipt["rack"]
    assert rack["one_coherent_instrument"] is True
    assert rack["collage_used"] is False
    assert rack["general_midi_fallback_used"] is False
    assert rack["licence"] == "CC-BY"

    for label in ("130", "136"):
        row = receipt["renders"][label]
        assert row["bit_identical_across_executions"] is True
        assert row["events_rendered"] == 1253
        assert row["stem_sum_reproduces_master"] is True
        assert row["right_hand_sha256"] != row["left_hand_sha256"]

    assert receipt["renders"]["130"]["master_sha256"] != \
        receipt["renders"]["136"]["master_sha256"]
    assert receipt["independence"]["reference_recording_consulted"] is False
    assert receipt["state"]["audio_answer_key"] == "unbound"
    assert receipt["state"]["album_authority_changed"] is False


def test_the_owner_pack_is_blind_only_on_the_tempo_and_level_matched():
    receipt = load_sealed(RECEIPT)
    pack = receipt["owner_pack"]
    assert pack["blind"] == "which letter carries which tempo"
    assert pack["assignment_map_withheld"] is True
    assert len(pack["assignment_sealed_sha256"]) == 64
    assert "reproducible from the evidence" in pack["assignment_derivation"]
    assert pack["admissible_outcomes"] == ["130", "136", "tie", "reject_all"]
    assert pack["engineering_control"].startswith("disclosed")
    # Level matched to the quieter of the two, so loudness is not the difference.
    assert pack["level_matched_lufs"] == min(pack["measured_lufs"].values())


def test_the_child_rule_is_recorded_with_the_render():
    receipt = load_sealed(RECEIPT)
    rule = receipt["interpretation_rule"]
    assert rule["parent"] == "score_literal_130"
    assert rule["child"] == "performed_interpretation_136"
    assert rule["child_validated_against_parent"] is True
    assert "tempo map and derived timestamps only" in rule["child_may_change"]
    assert receipt["still_absent"], "the receipt must say what the render does not have"
