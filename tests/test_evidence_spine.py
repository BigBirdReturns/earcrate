"""Gates for the shared evidence spine.

The spine was extracted because these invariants had already survived more than one
concrete use. That is also why the duplication it replaces cannot simply be deleted:
`a1_07_gold_v8.common` sits inside the render provenance path set, so editing it
would move the digest identifying the code that produced A1-07's accepted render.
The two implementations therefore coexist, and this file is what stops them drifting.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_07_gold_v8 import common as frozen  # noqa: E402
from earcrate.evidence import identity, receipts  # noqa: E402

PAYLOADS = (
    {"kind": "x", "n": 1},
    {"b": [1, 2, {"c": None}], "a": "unicode: Måneskin Beggin’"},
    {"nested": {"deep": {"deeper": [True, False, 0.5]}}, "zero": 0},
)


def test_the_extracted_seal_agrees_with_the_frozen_one_byte_for_byte():
    """If these ever disagree, every seal in the repository means two things."""
    for payload in PAYLOADS:
        assert identity.canonical_json_bytes(payload) == frozen.canonical_json_bytes(payload)
        assert identity.sha256_bytes(b"abc") == frozen.sha256_bytes(b"abc")
        assert identity.seal(payload, "receipt_sha256") == frozen.seal(payload,
                                                                      "receipt_sha256")
        sealed = identity.seal(payload, "receipt_sha256")
        assert identity.validate_seal(sealed, "receipt_sha256") == \
            frozen.validate_seal(sealed, "receipt_sha256")


def test_the_extracted_file_digest_agrees_with_the_frozen_one(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"beggin" * 100_000)
    assert identity.sha256_file(path) == frozen.sha256_file(path)


def test_a_mutated_body_fails_its_seal():
    sealed = identity.seal({"kind": "x", "value": 1}, "receipt_sha256")
    sealed["value"] = 2
    with pytest.raises(identity.IdentityError, match="mismatch"):
        identity.validate_seal(sealed, "receipt_sha256")


def test_an_object_identity_binds_both_pcm_and_container():
    """A decision binds to one exact object, which is a decode *and* a file."""
    pcm, container = "a" * 64, "b" * 64
    first = identity.ObjectIdentity(canonical_pcm_sha256=pcm, container_sha256=container)
    assert first.matches(identity.ObjectIdentity(pcm, container))
    # Same audio, different file: not the same delivered object.
    assert not first.matches(identity.ObjectIdentity(pcm, "c" * 64))
    # Container unknown on one side is not a match unless the caller says so.
    assert not first.matches(identity.ObjectIdentity(pcm))
    assert first.matches(identity.ObjectIdentity(pcm), require_container=False)

    for bad in ("", "not-a-digest", "A" * 64, "a" * 63):
        with pytest.raises(identity.IdentityError):
            identity.ObjectIdentity(canonical_pcm_sha256=bad)


def test_loading_a_receipt_refuses_the_wrong_kind_and_a_broken_seal(tmp_path):
    good = identity.seal({"kind": "earcrate_test_receipt", "n": 1}, "receipt_sha256")
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(good), encoding="utf-8")

    assert receipts.load_sealed(path, kind="earcrate_test_receipt")["n"] == 1
    with pytest.raises(receipts.EvidenceError, match="kind"):
        receipts.load_sealed(path, kind="earcrate_other_receipt")
    with pytest.raises(receipts.EvidenceError, match="does not exist"):
        receipts.load_sealed(tmp_path / "absent.json")

    broken = dict(good)
    broken["n"] = 2
    (tmp_path / "broken.json").write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(receipts.EvidenceError, match="mismatch"):
        receipts.load_sealed(tmp_path / "broken.json")


def test_the_body_free_check_names_what_leaked_and_where():
    leaky = {
        "kind": "x",
        "source": {"artifact_path": r"D:\Projects\private\render-a.wav"},
        "boundary": {"private_paths_included": True},
    }
    findings = receipts.verify_body_free(leaky)
    assert any("artifact_path" in row for row in findings)
    assert any("private_paths_included" in row for row in findings)
    # Prose may legitimately contain words the naive substring check would flag.
    assert receipts.verify_body_free(
        {"kind": "x", "note": "two required executions agreed"}) == []


def test_every_landed_album_receipt_is_body_free_under_the_shared_checker():
    landed = sorted((ROOT / "proofs" / "album_one").glob("*.public.json"))
    assert landed, "the album program lands its public evidence here"
    for path in landed:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert receipts.verify_body_free(value) == [], f"{path.name} leaks"
        # And each still validates under the extracted implementation, not just the
        # frozen one that wrote it.
        receipts.load_sealed(path)


DEFERRED = ("ArrangementGraph", "PerformanceRealizer", "FrontierBuilder")
SHARED_PACKAGES = ("earcrate/evidence", "earcrate/album")


def test_the_deferred_musical_abstractions_are_not_shared_types():
    """A1-02 exists to challenge A1-07's musical assumptions; do not freeze them first.

    Extracting these from one implementation would encode recorded-clip placement,
    phrase-map form, timing-law frontier construction and protected PCM regions as if
    they were universal properties of a track lane. A1-02 has none of them.
    """
    for package in SHARED_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
            body = code.split('"""')
            executable = "".join(body[::2]) if len(body) > 1 else code
            for name in DEFERRED:
                assert name not in executable, (
                    f"{path.relative_to(ROOT)} introduces {name} as shared machinery; "
                    "see docs/EXTRACTION_BOUNDARY.md")


def test_the_shared_spine_does_not_branch_on_a_track_id():
    """Track-id branching is the evidence that an invariant has not been found yet."""
    import re

    pattern = re.compile(r"""["']A1-0\d["']""")
    for package in SHARED_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
            body = code.split('"""')
            executable = "".join(body[::2]) if len(body) > 1 else code
            assert not pattern.search(executable), (
                f"{path.relative_to(ROOT)} hardcodes a track id; the shared spine must "
                "work for a commission it has never seen")


def test_the_extraction_boundary_is_written_down():
    text = (ROOT / "docs" / "EXTRACTION_BOUNDARY.md").read_text(encoding="utf-8")
    for name in DEFERRED:
        assert name in text, f"{name} is deferred but the boundary does not say so"
    for threshold in ("two materially different concrete implementations",
                      "no track-id branching",
                      "source-modality"):
        assert threshold.lower() in text.lower(), f"the threshold omits {threshold!r}"
