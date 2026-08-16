from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPRINT = ROOT / "ALBUM_SPRINT_01.md"


def test_album_sprint_requires_parallel_full_form_owner_work():
    text = SPRINT.read_text(encoding="utf-8")
    assert "seven track lanes in parallel" in text
    assert "musical proposition, not a unit test" in text
    assert "at most one owner frontier" in text
    assert "at most four cuts" in text
    assert "musical delta between cuts must be disclosed" in text
    assert "share one dominant audible defect is invalid" in text


def test_album_sprint_exit_requires_every_track_or_exact_blocker():
    text = SPRINT.read_text(encoding="utf-8")
    assert "Every track must reach one of two states" in text
    assert "machine-qualified full-form owner frontier" in text
    assert "exact irreducible blocker" in text
