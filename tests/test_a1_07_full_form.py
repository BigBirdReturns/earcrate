"""Gates for the A1-07 full-form adapter and the preflight that consumes it.

These protect the two properties the lane exists to guarantee: that a candidate is
a real full form built from the qualified parent, and that the preflight reports
what actually ran rather than a hardcoded verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from earcrate.a1_07_full_form import contract as ct
from earcrate.a1_07_full_form import score as sc
from earcrate.a1_07_full_form.contract import FullFormError
from earcrate.a1_07_gold_v8 import common as c


def _contract() -> dict:
    return ct.load_contract(ct.contract_path(ROOT))


def test_full_form_contract_seals_and_declares_a_complete_form():
    value = _contract()
    form = value["form"]
    assert 45.0 <= float(form["declared_total_seconds"]) <= 120.0, \
        "the declared form must sit inside the album full-form window"
    sections = [row["section_id"] for row in form["sections"]]
    assert sections == ["setup", "body", "payoff"], \
        f"a full form is setup, body then payoff; got {sections}"
    # Sections must tile the form without a gap or an overlap.
    rows = form["sections"]
    assert float(rows[0]["start_seconds"]) == 0.0
    for left, right in zip(rows, rows[1:]):
        assert abs(float(left["end_seconds"]) - float(right["start_seconds"])) < 1e-6, \
            "form sections must be contiguous"
    assert abs(float(rows[-1]["end_seconds"]) - float(form["declared_total_seconds"])) < 1e-6


def test_full_form_contract_rejects_a_broken_seal():
    path = ct.contract_path(ROOT)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["form"]["declared_total_seconds"] = 61.0
    try:
        ct.load_contract  # noqa: B018 - referenced for clarity
        from earcrate.a1_07_gold_v8.common import validate_seal
        validate_seal(value, "contract_sha256")
    except Exception:
        return
    raise AssertionError("a mutated contract must not validate its seal")


def test_phrase_map_is_explicit_for_every_vocal_phrase():
    value = _contract()
    phrases = value["phrase_map"]["vocal_phrases"]
    assert phrases, "the descent must carry an explicit phrase map"
    required = ("source_window_seconds", "destination_anchor_seconds", "timing_law",
                "reset_behaviour", "gain_db", "transition_treatment")
    for phrase in phrases:
        for key in required:
            assert key in phrase, f"phrase {phrase['phrase_id']} hides {key}"
        low, high = phrase["source_window_seconds"]
        assert high > low, "a phrase source window must be positive"
    assert value["phrase_map"]["vocal_invariants"]["frankie_time_stretch_forbidden"] is True


def test_every_timing_law_stays_inside_the_duration_preservation_cap():
    """The rejected ancestor reached atempo 0.334; nothing here may do that again."""
    value = _contract()
    low, high = value["machine_gate"]["band_tempo_scale_bounds"]
    grid = [
        {"target": 0, "window": (0, 88_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": 86_000},
        {"target": 86_000, "window": (88_000, 176_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": 86_000},
        {"target": 172_000, "window": (176_000, 264_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": 300_000},
        {"target": 472_000, "window": (264_000, 352_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": None},
    ]
    for row in value["timing_laws"]:
        _, facts = sc.schedule(grid, str(row["candidate_id"]), bounds=(low, high))
        for scale in facts["tempo_scales"]:
            assert low <= scale <= high, \
                f"{row['candidate_id']} produced an uncapped scale {scale}"


def test_native_pocket_never_retimes_the_band():
    value = _contract()
    low, high = value["machine_gate"]["band_tempo_scale_bounds"]
    grid = [
        {"target": 0, "window": (0, 88_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": 80_000},
        {"target": 80_000, "window": (88_000, 176_000), "source_duration": 88_000,
         "parent_duration": 88_000, "advance_gap": None},
    ]
    _, facts = sc.schedule(grid, "full-form-v1-native-pocket", bounds=(low, high))
    assert facts["tempo_scales"] == [1.0], \
        "the native pocket must keep the donor's own tempo exactly"


def test_band_grid_rejects_donor_tracks_that_disagree_on_a_bar():
    tracks = [
        {"track_id": "a", "clips": [
            {"target_start_sample": 0, "source_start_sample": 0, "source_end_sample": 100},
            {"target_start_sample": 100, "source_start_sample": 100, "source_end_sample": 200}]},
        {"track_id": "b", "clips": [
            {"target_start_sample": 0, "source_start_sample": 7, "source_end_sample": 100}]},
    ]
    try:
        sc.band_grid(tracks)
    except FullFormError:
        return
    raise AssertionError("a slot whose donors disagree on the source window is not one bar")


def test_band_grid_accepts_progressive_entry():
    """The retained positive arc enters harmonics, then bass, then drums."""
    tracks = [
        {"track_id": "other", "clips": [
            {"target_start_sample": 0, "source_start_sample": 0, "source_end_sample": 100},
            {"target_start_sample": 100, "source_start_sample": 100, "source_end_sample": 200},
            {"target_start_sample": 200, "source_start_sample": 200, "source_end_sample": 300}]},
        {"track_id": "bass", "clips": [
            {"target_start_sample": 100, "source_start_sample": 100, "source_end_sample": 200},
            {"target_start_sample": 200, "source_start_sample": 200, "source_end_sample": 300}]},
        {"track_id": "drums", "clips": [
            {"target_start_sample": 200, "source_start_sample": 200, "source_end_sample": 300}]},
    ]
    grid = sc.band_grid(tracks)
    assert [row["target"] for row in grid] == [0, 100, 200]
    assert [len(row["occupants"]) for row in grid] == [1, 2, 3], \
        "tracks must be allowed to occupy different subsets of the shared grid"


def test_preflight_refuses_a_manifest_from_another_head():
    from album_sprint_preflight.secondary import beggin

    spec = {"adapter": "full_form_v1", "minimum_seconds": 45.0, "maximum_seconds": 120.0,
            "required_bindings": ["a1_07_gold_v7_workspace", "beggin_core_private_store",
                                  "a1_07_full_form_execution_manifest"]}
    result = beggin("A1-07", spec, {})
    assert result["representative_invocation_ready"] is False, \
        "an unbound manifest can never be a representative invocation"
    assert result["full_form_adapter_ready"] is False
    assert any(row["kind"] == "blocked_representative_invocation" for row in result["blockers"])


def test_preflight_never_reports_human_acceptance_from_machine_qualification():
    from album_sprint_preflight.secondary import beggin

    spec = {"adapter": "full_form_v1", "minimum_seconds": 45.0, "maximum_seconds": 120.0,
            "required_bindings": []}
    result = beggin("A1-07", spec, {})
    assert result["observations"]["accepted_album_master"] is False
    assert result["observations"]["human_acceptance"] is False


def test_no_tracked_artifact_declares_a_candidate_to_letter_mapping():
    """A tracked label table is not a blind.

    If the contract, the manifest or the runner names which letter carries which
    timing law, anyone holding the repository can decode the verdict -- and a
    stale table decodes it *wrongly*, which is worse than no blind at all.
    """
    value = _contract()
    for row in value["timing_laws"]:
        assert "review_label" not in row, \
            f"{row['candidate_id']} declares a review label in tracked configuration"
        for field in ("frontier_role", "label", "why"):
            text = str(row.get(field) or "")
            assert not re.match(r"^\s*[ABC]\s*[:\-]", text), \
                f"{row['candidate_id']}.{field} leaks a review letter: {text!r}"

    runner = (ROOT / "scripts" / "RUN_A1_07_FULL_FORM_V1.ps1").read_text(encoding="utf-8")
    assert not re.search(r"\bA\s*=\s*\w+.*\bB\s*=\s*\w+", runner), \
        "the runner must not print a candidate-to-letter table"

    # Machine evidence and the CLI must not carry a review label at all: the pack
    # permutes them, so a label recorded upstream can only ever go stale and
    # decode a verdict to the wrong timing law.
    for name in ("build.py", "cli.py"):
        source = (ROOT / "earcrate" / "a1_07_full_form" / name).read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        assert "review_label" not in code, \
            f"{name} carries a review label; only the private pack authority may"


def test_review_labels_are_permuted_under_a_private_nonce():
    from earcrate.a1_07_full_form.review import _assign_letters

    ids = ["full-form-v1-single-speed", "full-form-v1-native-pocket",
           "full-form-v1-phrase-reset"]
    first = _assign_letters(ids, "nonce-one")
    second = _assign_letters(ids, "nonce-two")
    for mapping in (first, second):
        assert sorted(mapping.values()) == ["A", "B", "C"], "every candidate needs one label"
        assert len(set(mapping.values())) == 3, "labels must be unique"
    assert first != second, "a different nonce must yield a different permutation"


def test_adapter_tree_digest_covers_everything_that_can_change_the_audio():
    """Provenance must bind to the render-affecting code, not to a commit counter."""
    from earcrate.a1_07_full_form.provenance import ADAPTER_PATHS, adapter_tree_digest

    for entry in ("earcrate/a1_07_full_form", "earcrate/a1_07_gold_v8",
                  "earcrate/reference_zero.py",
                  "configs/album_one/a1-07/full-form-v1.v1.json"):
        assert entry in ADAPTER_PATHS, f"{entry} can change a render but is not covered"
    # A file that cannot change the audio must NOT be covered, or every unrelated
    # edit would force a re-render to re-prove something it could not have touched.
    for entry in ("CHANGELOG.md", "scripts/BUILD_A1_07_BLIND_BUNDLE.ps1",
                  "scripts/album_sprint_preflight/secondary.py"):
        assert entry not in ADAPTER_PATHS, f"{entry} cannot change a render; do not gate on it"

    first = adapter_tree_digest(ROOT)
    assert first["member_count"] > 0
    assert first["digest"] == adapter_tree_digest(ROOT)["digest"], "digest must be stable"


def test_full_form_adapter_surface_exists():
    for relative in ("scripts/RUN_A1_07_FULL_FORM_V1.ps1",
                     "scripts/earcrate_a1_07_full_form_v1.py",
                     "earcrate/a1_07_full_form/build.py",
                     "earcrate/a1_07_full_form/score.py",
                     "earcrate/a1_07_full_form/review.py",
                     "configs/album_one/a1-07/full-form-v1.v1.json"):
        assert (ROOT / relative).is_file(), f"the adapter is incomplete: {relative} is missing"


def test_loudness_summary_is_read_from_the_summary_block_not_the_frame_trace():
    """A quiet intro must not be reported as near-silence.

    ebur128 prints a running `I:` per frame and a track that opens quietly reports
    the -70 LUFS floor for its first frames. Reading the trace instead of the
    trailing summary made level_match clamp on peak instead of loudness.
    """
    import re
    from earcrate.a1_07_gold_v8 import review as v8review

    source = Path(v8review.__file__).read_text(encoding="utf-8")
    body = source.split("def measure_loudness", 1)[1].split("\ndef ", 1)[0]
    # Comments in this function quote the old pattern to explain the bug, so the
    # check reads executable lines only.
    code = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    assert "Summary:" in code, "measure_loudness must anchor on the ebur128 summary block"
    assert not re.search(r"I:.*\.\*\?.*Peak:", code), \
        "an unanchored I:...Peak: match pairs the first frame with the final summary"
