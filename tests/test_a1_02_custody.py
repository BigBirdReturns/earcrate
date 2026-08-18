"""Gates for A1-02 audio custody.

Capture must describe a delivered file honestly and stop there. The failure this
guards against is small and easy: an acquisition that reads as an answer key because
nobody wrote down the difference between "we have a file" and "this is the recording
the score describes".

The fixtures are synthesized tones, never any private or commercial media.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02 import custody  # noqa: E402
from earcrate.evidence.identity import validate_seal  # noqa: E402

RATE = 44100
CHANNELS = 2


def _write_tone(path: Path, *, seconds: float, lead_silence: float = 0.0,
                codec: str = "flac") -> Path:
    """A deterministic tone with a known silent lead, synthesized by ffmpeg.

    Generated rather than sampled: no private or commercial media takes part in a test.
    """
    delay = int(round(lead_silence * 1000))
    filters = [f"volume=0.25", "aformat=channel_layouts=stereo"]
    if delay:
        filters.append(f"adelay={delay}|{delay}")
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=220:sample_rate={RATE}:duration={seconds:.4f}",
         "-af", ",".join(filters), "-ac", str(CHANNELS), "-c:a", codec, str(path)],
        capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0, result.stderr[-500:]
    return path


def _declaration() -> dict:
    manifest = json.loads(
        (ROOT / "configs/album_one/manifest.v1.json").read_text(encoding="utf-8"))
    row = next(r for r in manifest["tracks"] if r["track_id"] == "A1-02")
    return row["edition_declaration"]


def _provenance() -> dict:
    return {"source": "test fixture", "downloaded_at": "2026-08-18T00:00:00Z",
            "official_release_page": "n/a", "displayed_version_name": "fixture",
            "track_number": 1}


def test_capture_records_identity_and_measurement_without_promoting(tmp_path):
    delivered = _write_tone(tmp_path / "01 Children (Dream Version).flac",
                            seconds=426.0 - 1.5, lead_silence=1.5)
    receipt = custody.capture(delivered, declaration=_declaration(),
                              provenance=_provenance())

    assert receipt["status"] == custody.CANDIDATE_STATUS
    assert "structural comparison" in receipt["promotion_requires"]
    observed = receipt["observed"]
    assert observed["original_filename"] == delivered.name
    assert len(observed["container_sha256"]) == 64
    assert len(observed["canonical_pcm_sha256"]) == 64
    assert observed["sample_rate"] == RATE and observed["channels"] == CHANNELS
    assert observed["duration_seconds"] == pytest.approx(426.0, abs=0.5)
    assert observed["leading_silence_seconds"] == pytest.approx(1.5, abs=0.05)
    assert validate_seal(receipt, "capture_sha256") == receipt["capture_sha256"]

    # The binding it produces is a candidate, and it is not the answer key.
    assert receipt["binding"]["role"] == "audio_answer_key"
    assert receipt["binding"]["verified"] is True
    assert receipt["declared_versus_observed"]["looks_like_the_declared_object"] is True
    assert receipt["declared_versus_observed"]["obvious_mismatches"] == []
    assert "not promotion" in receipt["declared_versus_observed"]["note"]


def test_capture_never_rewrites_the_delivered_file(tmp_path):
    """No rename, no transcode: the delivered bytes are evidence."""
    delivered = _write_tone(tmp_path / "delivered.flac", seconds=420.0)
    before = delivered.read_bytes()
    custody.capture(delivered, declaration=_declaration(), provenance=_provenance())
    assert delivered.read_bytes() == before
    assert delivered.name == "delivered.flac"


def test_a_radio_length_delivery_is_reported_as_an_obvious_mismatch(tmp_path):
    """Every excluded variant is materially shorter; that is cheap to notice."""
    delivered = _write_tone(tmp_path / "short.flac", seconds=230.0)
    receipt = custody.capture(delivered, declaration=_declaration(),
                              provenance=_provenance())
    findings = receipt["declared_versus_observed"]["obvious_mismatches"]
    assert findings, "a four-minute delivery must not pass as the full-length version"
    assert any("differs from the declared" in row for row in findings)
    assert any("looks like an edit" in row for row in findings)
    assert receipt["status"] == custody.CANDIDATE_STATUS, \
        "a mismatch changes the findings, never the status"


def test_a_lossy_delivery_is_reported(tmp_path):
    delivered = _write_tone(tmp_path / "lossy.mp3", seconds=426.0, codec="libmp3lame")
    receipt = custody.capture(delivered, declaration=_declaration(),
                              provenance=_provenance())
    assert any("lossy" in row
               for row in receipt["declared_versus_observed"]["obvious_mismatches"])


def test_container_level_checks_cannot_promote_anything(tmp_path):
    """The distinction the lane depends on, stated as a gate.

    A file that passes every cheap check is still only a candidate. Promotion requires
    the score-form comparison, which no code here performs.
    """
    delivered = _write_tone(tmp_path / "plausible.flac", seconds=426.0)
    receipt = custody.capture(delivered, declaration=_declaration(),
                              provenance=_provenance())
    text = json.dumps(receipt)
    assert "answer_key_bound" not in text
    assert receipt["status"] == custody.CANDIDATE_STATUS
    for word in ("FIT", "NONFIT"):
        assert word not in receipt["status"]


def test_the_capture_carries_the_declared_edition_alongside_what_was_observed(tmp_path):
    delivered = _write_tone(tmp_path / "x.flac", seconds=426.0)
    receipt = custody.capture(delivered, declaration=_declaration(),
                              provenance=_provenance())
    declared = receipt["declared_edition"]
    assert declared["selected_version"].startswith("Children (Dream Version)")
    assert declared["track_number"] == 1
    assert declared["catalog_number"].startswith("not_assigned")
    assert receipt["acquisition_provenance"] == _provenance(), (
        "the capture must record acquisition provenance exactly as supplied")
