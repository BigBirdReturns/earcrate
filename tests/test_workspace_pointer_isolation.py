"""Regression: a temp/test workspace must never clobber the real app-global pointer.

Incident 2026-07-20 — the operator's real pointer
(``S:\\EarCrate-Workspace\\earcrate_workspace.json``, reached via a real
``EARCRATE_HOME``) was overwritten with ``{"config_json":
"S:\\Temp\\tmpgrmogjz9\\agent\\config.json"}`` by a test run. Every subsequent
CLI invocation resolved an empty database and reported 0 approved atoms instead
of 216,034, with no error raised anywhere.

These tests pin the two halves of the fix. Neither writes outside tmp_path.
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from earcrate.app import EarcrateCore


def _mk_workspace(root: Path) -> dict:
    music = root / "music"
    music.mkdir(parents=True, exist_ok=True)
    (music / "track.txt").write_text("not really audio", encoding="utf-8")
    return {"music_folder": str(music), "workspace_folder": str(root / "ws")}


def test_temp_workspace_does_not_clobber_a_real_pointer(tmp_path):
    """The exact incident shape: real (non-temp) EARCRATE_HOME + temp workspace.

    The pointer must be left untouched and the refusal must be reported, not
    silent. Before the fix, the pointer body became the temp config path.
    """
    real_home = tmp_path / "real_home"  # non-temp *relative to tempdir* is what
    real_home.mkdir()                    # matters; we fake that below via patch
    pointer = real_home / "earcrate_workspace.json"
    original = json.dumps({"config_json": str(real_home / "agent" / "config.json")}, indent=2)
    pointer.write_text(original, encoding="utf-8")

    temp_workspace_root = Path(tempfile.mkdtemp(prefix="earcrate_regression_ws_"))

    # Make the assertion honest: tmp_path itself lives under the temp dir on
    # most CI machines, so point gettempdir() at the workspace root only. That
    # makes `real_home` non-temp and the workspace temp — the incident shape.
    # app_state_dir is redirected into tmp_path so the machine's real legacy
    # pointer cannot participate; legacy adoption has its own test below.
    with patch.dict(os.environ, {"EARCRATE_HOME": str(real_home)}), \
         patch("earcrate.app.app_state_dir", return_value=tmp_path / "no_legacy"), \
         patch("earcrate.app.tempfile.gettempdir", return_value=str(temp_workspace_root)):
        core = EarcrateCore()
        result = core.configure_workspace(_mk_workspace(temp_workspace_root))

    assert result["ok"] is True
    assert result.get("pointer_written") is False
    assert "refused" in result.get("pointer_skipped_reason", "")
    assert pointer.read_text(encoding="utf-8") == original, \
        "the real app-global pointer was overwritten by a temp workspace"


def test_sandboxed_run_still_writes_its_own_pointer(tmp_path):
    """A temp workspace under a temp EARCRATE_HOME is a properly sandboxed run:
    the pointer must still be written, or every isolated test loses its config."""
    sandbox = Path(tempfile.mkdtemp(prefix="earcrate_regression_home_"))
    with patch.dict(os.environ, {"EARCRATE_HOME": str(sandbox)}):
        core = EarcrateCore()
        result = core.configure_workspace(_mk_workspace(sandbox))

    assert result["ok"] is True
    assert result.get("pointer_written") is not False
    pointer = sandbox / "earcrate_workspace.json"
    assert pointer.exists(), "a sandboxed run must still persist its own pointer"
    assert str(sandbox) in json.loads(pointer.read_text(encoding="utf-8"))["config_json"]


def test_legacy_pointer_holding_a_temp_workspace_is_not_adopted(tmp_path):
    """Second clobber vector, found live on the operator's machine 2026-07-20.

    The legacy pointer lives at ``app_state_dir()``, which EARCRATE_HOME does
    not sandbox — so a test run leaves its mkdtemp workspace there. While that
    temp dir survives, the legacy pointer validates, and the adoption branch
    copies it verbatim over the real pointer. It must be refused instead.
    """
    legacy_dir = tmp_path / "legacy_state"
    legacy_dir.mkdir()
    temp_ws = Path(tempfile.mkdtemp(prefix="earcrate_regression_legacy_"))
    agent = temp_ws / "agent"
    agent.mkdir(parents=True)
    music = temp_ws / "oldlib"
    music.mkdir()
    cfg = agent / "config.json"
    cfg.write_text(json.dumps({
        "master_root": str(music), "working_root": str(temp_ws / "w"),
        "stems_root": str(temp_ws / "w" / "stems"),
        "playlists_root": str(temp_ws / "w" / "playlists"),
        "agent_root": str(agent),
    }), encoding="utf-8")
    (legacy_dir / "config_pointer.json").write_text(
        json.dumps({"config_json": str(cfg)}), encoding="utf-8")

    real_home = tmp_path / "real_home"
    real_home.mkdir()
    pointer = real_home / "earcrate_workspace.json"

    with patch.dict(os.environ, {"EARCRATE_HOME": str(real_home)}), \
         patch("earcrate.app.app_state_dir", return_value=legacy_dir):
        core = EarcrateCore()

    assert not pointer.exists(), \
        "a legacy pointer holding a temp workspace was promoted to the real pointer"
    assert core.config is None or not str(core.config.agent_root).startswith(str(temp_ws)), \
        "a temp workspace was adopted as the live config"


def test_conftest_forces_a_temp_earcrate_home():
    """The session sandbox itself: EARCRATE_HOME must never be the machine's
    real one while tests run, regardless of what the machine has set."""
    home = Path(os.environ["EARCRATE_HOME"]).resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    assert home == tmp_root or tmp_root in home.parents, \
        f"EARCRATE_HOME={home} is not under the temp dir; the session sandbox is not active"
