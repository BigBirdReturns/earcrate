"""Gates for the autonomous 'drop an album' run (EarcrateCore.render_album +
album_readme_markdown). The render itself is box-only (real audio), so here we
gate the pure playlist builder and prove the run degrades cleanly (writes a
trace, never crashes) when there is no material.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.app import EarcrateCore, album_readme_markdown


def _core(tmp):
    for d in ("music", "work", "agent"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp)}):
        c = EarcrateCore()
        c.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                     "agent_root": str(tmp / "agent"), "workers": 1})
    return c


def test_album_readme_lists_tracks_with_gate_verdicts():
    made = [
        {"track": 1, "taste_profile": "girl_talk_v1", "seed": 7, "score": 0.8,
         "gate": {"passed": True, "warnings": ["presence low"]}, "wav": "a.wav"},
        {"track": 2, "taste_profile": "notorious_v1", "seed": 9, "score": 0.7,
         "gate": {"passed": False, "failures": ["high3000_share 0.05 catastrophically dark"]}, "wav": "b.wav"},
    ]
    skipped = [{"taste_profile": "troubadour_v1", "reason": "crate not ready: coverage"}]
    md = album_readme_markdown(made, skipped, {"personas": ["girl_talk_v1"], "target_seconds": 150,
                                               "recognizability_bias": 92}, "/x/album")
    assert "a.wav" in md and "b.wav" in md
    assert "PASS" in md and "FLAGGED" in md            # both verdicts surfaced
    assert "Skipped" in md and "troubadour_v1" in md


def test_render_album_degrades_cleanly_with_no_material(tmp_path):
    core = _core(tmp_path)
    res = core.render_album({"tracks": 3})
    assert res["ok"] is False and res["made"] == 0
    assert any("no approved atoms" in s.get("reason", "") for s in res["skipped"])
    # A trace of the attempt is written even for an empty album.
    ad = Path(res["album_dir"])
    assert (ad / "README.md").exists()
    manifest = json.loads((ad / "album_manifest.json").read_text())
    assert manifest["made"] == 0 and manifest["tracks"] == []
