"""Gates for the audio comparator and the boundary it must not cross.

The comparator is landed unfrozen and unqualified, because the adverse controls
showed it cannot discriminate on this work. These gates protect two things: that the
one-way dependency boundary holds, and that an unqualified instrument cannot quietly
start acting like a qualified one.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.audio_compare import align, anchors as an  # noqa: E402
from earcrate.a1_02.audio_compare.features import BarFeatures  # noqa: E402
from earcrate.evidence.receipts import load_sealed, verify_body_free  # noqa: E402

QUALIFICATION = ROOT / "proofs" / "album_one" / "a1-02-comparator-qualification-v1.public.json"
ANNOTATIONS = ROOT / "specimens" / "children_v1.annotations.json"


def _bars(chromas, *, bar_seconds: float = 1.7647) -> list[BarFeatures]:
    return [BarFeatures(index=i, start_seconds=i * bar_seconds,
                        end_seconds=(i + 1) * bar_seconds, chroma=tuple(c),
                        onset_density=0.5, energy=0.5)
            for i, c in enumerate(chromas)]


# --- the one-way dependency boundary ------------------------------------------------

def test_audio_compare_may_not_be_imported_by_anything_that_makes_a_performance():
    """The comparison may judge a performance later. It may never be how one was made."""
    package = ROOT / "earcrate" / "a1_02"
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        if relative.startswith("audio_compare/"):
            continue
        source = path.read_text(encoding="utf-8")
        assert "audio_compare" not in source, (
            f"{relative} imports the audio comparison; a performance path that can see "
            "audio makes the convergence claim circular")

    # And the comparison may not write score or performance authority.
    for path in sorted((package / "audio_compare").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("performed_midi", "realize", "render_performance"):
            assert forbidden not in source, (
                f"audio_compare/{path.name} reaches into performance generation")


def test_the_score_side_stays_derivable_with_no_audio_present():
    from earcrate.a1_02 import score_timeline as st

    assert len(st.performed_order()) == 105
    assert an.score_anchors(ANNOTATIONS)[-1].label == "Coda"


def test_the_twelve_anchors_tile_the_performed_order():
    anchors = an.score_anchors(ANNOTATIONS)
    assert len(anchors) == 12
    assert sum(anchor.bars for anchor in anchors) == 105
    assert [anchor.order for anchor in anchors] == list(range(12))
    assert [anchor.mandatory for anchor in anchors].count(True) == 1
    assert anchors[-1].mandatory is True, "the coda is the mandatory anchor"


# --- the laws, exercised on synthetic material --------------------------------------

def test_alignment_is_monotonic_and_never_reorders():
    """A synthetic file where the score's material appears in order."""
    anchors = an.score_anchors(ANNOTATIONS)
    rng = np.random.default_rng(0)
    background = [rng.dirichlet(np.ones(12)) for _ in range(40)]
    planted = [list(anchor.chroma) for anchor in anchors]
    chromas = list(background)
    for block in planted:
        chromas.extend(block)
        chromas.extend(rng.dirichlet(np.ones(12)) for _ in range(3))

    verdict = align.compare(anchors, _bars(chromas))
    placed = [row for row in verdict["anchors"] if row["matched_audio_bars"]]
    starts = [row["matched_audio_bars"][0] for row in placed]
    assert starts == sorted(starts), "anchor matches ran backwards"


def test_transposition_is_measured_and_never_applied():
    """A rotated copy must not be rescued by rotating the template to meet it."""
    anchors = an.score_anchors(ANNOTATIONS)
    rng = np.random.default_rng(1)
    chromas = [rng.dirichlet(np.ones(12)) for _ in range(20)]
    for anchor in anchors:
        chromas.extend(np.roll(np.array(row), 2) for row in anchor.chroma)

    verdict = align.compare(anchors, _bars(chromas))
    assert verdict["results"]["tonal_correspondence"] == "FAIL", \
        "a two-semitone shift must fail tonal correspondence"


def test_no_criterion_passes_when_nothing_was_placed():
    """The defect the pitch-shift control caught: an empty frontier passing a check."""
    anchors = an.score_anchors(ANNOTATIONS)
    rng = np.random.default_rng(2)
    flat = [np.full(12, 1 / 12) + rng.normal(0, 1e-6, 12) for _ in range(200)]
    verdict = align.compare(anchors, _bars(flat))
    results = verdict["results"]
    assert results["tonal_correspondence"] == "FAIL"
    assert results["coda_correspondence"] == "FAIL"
    assert results["exact_delivery_identity"] == "NOT_DECIDED_BY_COMPARATOR"


def test_the_comparator_never_decides_delivery_identity():
    """Delivery identity is a custody fact. An acoustic instrument cannot grant it."""
    source = (ROOT / "earcrate" / "a1_02" / "audio_compare" / "align.py").read_text("utf-8")
    assert '"exact_delivery_identity": "NOT_DECIDED_BY_COMPARATOR"' in source
    for verdict_word in ("FIT", "answer_key", "bind"):
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        body = code.split('"""')
        executable = "".join(body[::2])
        assert f'"{verdict_word}"' not in executable


# --- the qualification finding -------------------------------------------------------

def test_the_comparator_is_landed_unfrozen_and_unqualified():
    receipt = load_sealed(QUALIFICATION)
    assert receipt["frozen"] is False
    assert receipt["verdict"] == "NOT_QUALIFIED_FOR_THIS_WORK"
    assert receipt["state"]["comparator_executed_as_authority"] is False
    assert receipt["state"]["audio_answer_key"] == "unbound"
    assert receipt["state"]["album_authority_changed"] is False
    assert verify_body_free(receipt) == []

    # No frozen comparator may exist while the instrument is unqualified.
    assert not (ROOT / "proofs" / "album_one" /
                "a1-02-frozen-comparator-v1.public.json").exists()


def test_the_finding_carries_the_measurement_that_produced_it():
    receipt = load_sealed(QUALIFICATION)
    measured = receipt["why"]["measured_on_the_control_candidate"]
    assert measured["distant_bar_pair_chroma_cosine_mean"] > 0.85, \
        "the point is that unrelated bars already look alike"
    assert receipt["why"]["measured_on_the_score_side"][
        "distinct_chroma_templates_across_105_bars"] == 11
    assert len(receipt["what_the_adverse_controls_caught_first"]) == 3
    for row in receipt["what_the_adverse_controls_caught_first"]:
        assert row["defect"] and row["detail"] and row["repair"]


def test_the_album_ledger_did_not_move():
    manifest = json.loads(
        (ROOT / "configs" / "album_one" / "manifest.v1.json").read_text(encoding="utf-8"))
    assert manifest["completed_album_master_count"] == 1
    assert manifest["completed_system_reference_count"] == 0
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    assert row["edition_finding"]["state_until_then"]["answer_key_status"] == \
        "edition_declared_pending_acquisition_and_structural_fit"
