"""Seal the comparator before the preferred answer key is ever tested.

The anti-tuning guarantee is not a promise that nobody will adjust a threshold. It is
a digest, taken over the implementation and its constants, recorded together with how
the comparator behaved on deliberately broken inputs, *before* the commissioned
delivery exists. If the frozen digest and the running code disagree later, the run is
refused rather than reported.

Adverse controls are what make the thresholds mean something. A threshold derived only
from the candidate it will judge is a description of that candidate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ...evidence.identity import seal, sha256_bytes
from .align import Thresholds

COMPARATOR_ID = "a1-02-audio-comparator-v1"

# Files whose contents can change a verdict. Nothing else belongs here.
VERDICT_AFFECTING = (
    "earcrate/a1_02/audio_compare/align.py",
    "earcrate/a1_02/audio_compare/anchors.py",
    "earcrate/a1_02/audio_compare/features.py",
    "earcrate/a1_02/score_timeline.py",
)

# What each adverse control must do to the verdict. Written before the controls were
# generated, so a control that behaves differently is a finding about the comparator.
EXPECTED_ADVERSE_BEHAVIOUR: dict[str, dict[str, str]] = {
    "pre_roll_removed": {
        "arrangement_family_identity": "SUPPORTED",
        "why": "removing a production intro must not change what the arrangement is"},
    "time_stretched": {
        "arrangement_family_identity": "SUPPORTED",
        "why": "bar-normalized correspondence must survive a moderate tempo change"},
    "coda_removed": {
        "coda_correspondence": "FAIL",
        "why": "the mandatory coda anchor has nothing to match"},
    "radio_truncated": {
        "coda_correspondence": "FAIL",
        "why": "a four-minute edit cannot carry the complete form"},
    "sections_shuffled": {
        "ordered_thematic_correspondence": "FAIL",
        "why": "monotonic ordering must break when sections are reordered"},
    "pitch_shifted": {
        "tonal_correspondence": "FAIL",
        "why": "a transposed object is not this score's recording"},
    "section_replaced": {
        "arrangement_family_identity": "SUPPORTED",
        "why": "one replaced span should surface as unmatched or ambiguous anchors "
               "rather than collapsing the whole reading"},
}


class FreezeError(RuntimeError):
    pass


def implementation_digest(repo_root: Path,
                          paths: Sequence[str] = VERDICT_AFFECTING) -> dict[str, Any]:
    """Digest the code that can change a verdict, by content."""
    rows: list[tuple[str, str]] = []
    for relative in sorted(paths):
        path = Path(repo_root) / relative
        if not path.is_file():
            raise FreezeError(f"verdict-affecting file is missing: {relative}")
        rows.append((relative, sha256_bytes(path.read_bytes())))
    payload = "\n".join(f"{name}:{digest}" for name, digest in rows).encode("utf-8")
    return {"members": [{"file": name, "sha256": digest} for name, digest in rows],
            "digest": sha256_bytes(payload)}


def build(repo_root: Path, *, thresholds: Thresholds, anchors: Sequence[Any],
          adverse_results: Mapping[str, Any],
          feature_definition: Mapping[str, Any]) -> dict[str, Any]:
    """The sealed comparator: what it is, what it decides with, and how it broke."""
    missing = set(EXPECTED_ADVERSE_BEHAVIOUR) - set(adverse_results)
    if missing:
        raise FreezeError(f"adverse controls not run: {sorted(missing)}")

    disagreements: list[str] = []
    for name, expectation in EXPECTED_ADVERSE_BEHAVIOUR.items():
        observed = (adverse_results[name] or {}).get("results") or {}
        for key, wanted in expectation.items():
            if key == "why":
                continue
            if observed.get(key) != wanted:
                disagreements.append(
                    f"{name}: {key} was {observed.get(key)!r}, expected {wanted!r}")

    return seal({
        "kind": "earcrate_a1_02_frozen_comparator",
        "schema_version": 1,
        "comparator_id": COMPARATOR_ID,
        "frozen_before": "the commissioned Bandcamp delivery exists",
        "implementation": implementation_digest(repo_root),
        "thresholds": thresholds.as_dict(),
        "feature_definition": dict(feature_definition),
        "mandatory_anchors": [anchor.anchor_id for anchor in anchors if anchor.mandatory],
        "anchor_order": [anchor.anchor_id for anchor in anchors],
        "laws": [
            "score-anchor order is immutable",
            "skipped audio is allowed and recorded as production-only",
            "skipped score anchors are explicit unmatched findings",
            "section reordering is forbidden",
            "a global stretch is forbidden; an N-bar anchor matches an N-bar window",
            "local beat normalization is allowed because both sides are bar-quantized",
            "transposition is forbidden; rotation is measured, never applied",
            "multiple plausible matches are retained as ambiguous",
        ],
        "adverse_controls": {
            name: {
                "expected": {k: v for k, v in EXPECTED_ADVERSE_BEHAVIOUR[name].items()
                             if k != "why"},
                "why": EXPECTED_ADVERSE_BEHAVIOUR[name]["why"],
                "observed": (adverse_results[name] or {}).get("results"),
                "counts": (adverse_results[name] or {}).get("counts"),
            }
            for name in sorted(EXPECTED_ADVERSE_BEHAVIOUR)
        },
        "adverse_controls_behaved_as_specified": not disagreements,
        "adverse_disagreements": disagreements,
        "anti_tuning_note": (
            "Thresholds were fixed against deliberately broken inputs before the "
            "commissioned delivery existed. When that delivery arrives it is judged by "
            "this exact implementation digest or not at all."),
        "boundary": {"private_paths_included": False, "source_audio_exported": False},
    }, "frozen_sha256")


def verify_unchanged(repo_root: Path, frozen: Mapping[str, Any]) -> list[str]:
    """Findings if the code has moved since it was frozen."""
    observed = implementation_digest(repo_root)
    declared = (frozen.get("implementation") or {}).get("digest")
    if observed["digest"] != declared:
        return [f"comparator implementation changed since freezing: declared "
                f"{str(declared)[:12]}, observed {observed['digest'][:12]}"]
    return []
