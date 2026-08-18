"""Gates for A1-02's first score-derived audio.

Two things need protecting. The render must stay deterministic and stay derived only
from the score, because the moment it consults a recording the convergence claim it
exists to serve is gone. And it must keep saying what it is: a harmonic realization,
not the note-level performance, at a moment when it is the only A1-02 audio there is
and would otherwise start being described as the performance by everyone downstream.
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
from earcrate.a1_02.performance import harmony, render  # noqa: E402
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

ANNOTATIONS = ROOT / "specimens" / "children_v1.annotations.json"
RECEIPT = ROOT / "proofs" / "album_one" / "a1-02-engineering-render-v1.public.json"


def test_the_realization_follows_the_performed_traversal_exactly():
    realized = harmony.realize(ANNOTATIONS)
    assert realized["performed_measures"] == len(st.performed_order()) == 105
    assert realized["printed_measures"] == st.PRINTED_MEASURES == 69

    measures = {row["performed_measure"] for row in realized["notes"]}
    assert measures == set(range(1, 106)), "a performed measure fell silent"

    # Every note names the printed measure it came from, and that measure is reachable.
    printed = {row["printed_measure"] for row in realized["notes"]}
    assert printed <= set(range(1, 70))
    assert st.measures_never_performed(realized["tempo_bpm"]) == ()


def test_every_note_carries_the_chord_symbol_that_authorized_it():
    realized = harmony.realize(ANNOTATIONS)
    vocabulary = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))["chord_vocabulary"]
    for row in realized["notes"]:
        assert row["chord_label"] in vocabulary, row["chord_label"]
        assert row["hand"] in ("left", "right")
        assert 0 < row["pitch"] < 128
        assert 0 < row["velocity"] <= 127


def test_the_realization_refuses_to_invent_what_it_does_not_have():
    """A chord symbol says which harmony is in force, not how it was played."""
    realized = harmony.realize(ANNOTATIONS)
    assert realized["reference_pcm_used"] is False
    assert realized["reference_recording_consulted"] is False
    for absent in ("melody", "dynamics", "articulation", "pedalling", "voicing"):
        assert any(absent in row for row in realized["what_is_absent"]), absent
    assert "not the note-level performance" in realized["what_this_is"]


def test_the_render_is_bit_identical_across_executions(tmp_path):
    """A render nobody can reproduce is a file, not evidence."""
    realized = harmony.realize(ANNOTATIONS)
    # A short excerpt: the property is determinism, and it does not need three minutes.
    realized["notes"] = [row for row in realized["notes"] if row["start_beat"] < 32]

    first = render.render_engineering_audio(realized, tmp_path / "a.wav")
    second = render.render_engineering_audio(realized, tmp_path / "b.wav")
    assert first["frames"] == second["frames"]
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()
    assert first["reference_pcm_used"] is False
    assert "no randomness, no dither, no clock input" in first["deterministic"]


def test_the_render_length_matches_the_score_side_timeline(tmp_path):
    realized = harmony.realize(ANNOTATIONS)
    predicted = st.total_seconds(realized["tempo_bpm"])
    receipt = load_sealed(RECEIPT)
    rendered = receipt["audio"]["duration_seconds"]
    assert rendered == pytest.approx(predicted, abs=1.0), (
        f"the render ran {rendered}s against a score-side prediction of {predicted}s")
    assert receipt["score_timeline_agreement"]["difference_is_release_tail"] is True


def test_nothing_in_the_performance_path_can_reach_a_recording():
    package = ROOT / "earcrate" / "a1_02" / "performance"
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("audio_compare", "librosa", "soundfile", "chroma", "bar_features"):
            assert forbidden not in source, (
                f"performance/{path.name} reaches for {forbidden}; the score branch's "
                "independence is what makes convergence mean anything")


def test_the_receipt_says_what_the_render_is_not():
    receipt = load_sealed(RECEIPT)
    assert verify_body_free(receipt) == []
    assert receipt["what_this_is_not"]["note_level_performance"] is False
    assert receipt["independence"]["reference_recording_consulted"] is False
    assert receipt["independence"]["control_candidate_used"] is False
    assert receipt["determinism"]["classification"] == "bit_exact_across_executions"
    assert receipt["state"]["album_realization_readiness"] is False
    assert receipt["state"]["audio_answer_key"] == "unbound"
    assert receipt["state"]["album_authority_changed"] is False


def test_the_legacy_pack_is_classified_unavailable_rather_than_hunted():
    manifest = json.loads(
        (ROOT / "configs" / "album_one" / "manifest.v1.json").read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    legacy = row["legacy_external_pack"]

    assert legacy["classification"] == "unavailable_external_evidence"
    assert legacy["search_status"] == "closed"
    assert "does not authorize inventing bytes under the old digests" in legacy["why"]
    assert len(legacy["objects"]) == 4
    for entry in legacy["objects"]:
        assert entry["identity_known"] is True
        assert entry["bytes_available"] is False
        assert len(entry["container_sha256"]) == 64
    assert "never reused for regenerated bytes" in legacy["regeneration_rule"]

    # And the album counters did not move because audio exists.
    assert manifest["completed_album_master_count"] == 1
    assert manifest["completed_system_reference_count"] == 0
    assert row["status"]["album_master"] == "unaccepted"
