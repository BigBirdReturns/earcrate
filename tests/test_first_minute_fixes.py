"""Regression gates for the first-minute reliability fixes.

These lock in the behaviours verified end-to-end (core + live HTTP + real browser
DOM) so they cannot silently regress:

  * first-run is detectable (default_paths().configured is None) and default_paths
    exposes the workspace ROOT (configured_workspace), so the Setup field binds to
    the root instead of the /work subdir;
  * re-saving the workspace is idempotent and never nests /work/work (which
    orphaned the DB/cache/judgments);
  * a saved config resolves from a brand-new core instance (the "not configured"
    bork / pointer trap);
  * run_background always finalises status (never wedges "busy" forever) and
    surfaces both {ok:false} payloads and raised exceptions as last_error;
  * preflight exposes ready+warnings (not the never-present `failures`) and
    propose_playlist returns entries as an int count.

Hermetic: each test isolates state under EARCRATE_HOME=tmp_path and never runs the
audio analyzer, so it stays fast and does not depend on librosa/numba.
"""
import json
import os
import time
from pathlib import Path


def _core(tmp_path):
    os.environ["EARCRATE_HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    music = tmp_path / "Music"
    music.mkdir(exist_ok=True)
    from earcrate.app import EarcrateCore
    core = EarcrateCore()
    return core, music


def test_first_run_signal_and_configured_workspace(tmp_path):
    core, _ = _core(tmp_path)
    dp = core.default_paths()
    assert dp["configured"] is None, "fresh box must report configured=None (first-run signal)"
    assert "configured_workspace" in dp, "default_paths must expose configured_workspace"
    assert dp["configured_workspace"] is None, "configured_workspace is None before configure"


def test_configure_workspace_is_idempotent_no_nesting(tmp_path):
    core, music = _core(tmp_path)
    ws = str(tmp_path / "WS")
    r1 = core.configure_workspace({"music_folder": str(music), "workspace_folder": ws})
    wr1 = r1["config"]["working_root"]
    assert wr1 == str(Path(ws) / "work")

    dp = core.default_paths()
    assert dp["configured_workspace"] == ws, "configured_workspace must be the ROOT the user picked"

    # Old frontend bug: field held working_root (.../work). Re-saving that must NOT nest.
    r2 = core.configure_workspace({"music_folder": str(music), "workspace_folder": wr1})
    assert r2["config"]["working_root"] == wr1, "re-save of the /work subdir must not nest one level deeper"

    # Saving via configured_workspace (what the fixed frontend sends) is stable too.
    r3 = core.configure_workspace({"music_folder": str(music), "workspace_folder": dp["configured_workspace"]})
    assert r3["config"]["working_root"] == wr1


def test_saved_config_resolves_in_fresh_core(tmp_path):
    core, music = _core(tmp_path)
    r = core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    wr = r["config"]["working_root"]

    from earcrate.app import EarcrateCore
    fresh = EarcrateCore()  # simulates the next launch / a different entry point
    assert fresh.config is not None, "a saved workspace must resolve from a fresh core (no 'not configured' bork)"
    assert str(fresh.config.working_root) == wr


def test_run_background_finalizes_status_on_all_paths(tmp_path):
    core, _ = _core(tmp_path)

    def wait_idle():
        for _ in range(200):
            with core.status_lock:
                if not core.status.get("busy"):
                    return dict(core.status)
            time.sleep(0.02)
        raise AssertionError("run_background WEDGED: status stayed busy")

    core.run_background(lambda: {"ok": True, "message": "did it"})
    s = wait_idle()
    assert s["busy"] is False and not s.get("last_error"), "success must clear busy and leave no error"

    core.run_background(lambda: {"ok": False, "error": "AcoustID API key required"})
    s = wait_idle()
    assert s["busy"] is False and s.get("last_error") and "AcoustID" in s["last_error"], \
        "an {ok:false} payload must be surfaced as last_error, not reported as success"

    def _raiser():
        raise RuntimeError("boom")
    core.run_background(_raiser)
    s = wait_idle()
    assert s["busy"] is False and s.get("last_error") and "boom" in s["last_error"], \
        "a raised exception must be surfaced as last_error"


def test_preflight_exposes_ready_and_warnings(tmp_path):
    core, music = _core(tmp_path)
    core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    pf = core.preflight({"taste_profile": "girl_talk_v1", "target_seconds": 120})
    # Empty pool path still must carry the fields the frontend reads. The OLD frontend
    # read pf.failures (never present) and so always claimed "READY to render".
    assert "ready" in pf, "preflight must expose 'ready' (frontend readiness contract)"
    assert "warnings" in pf, "preflight must expose 'warnings'"
    assert "failures" not in pf, "preflight has no 'failures' key — the old frontend read a phantom field"
    assert pf.get("ready") is False and pf.get("pool_size") == 0


def test_playlist_entries_is_int(tmp_path):
    core, music = _core(tmp_path)
    core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    pl = core.propose_playlist("gate pl", "", 30)
    assert isinstance(pl.get("entries"), int), "entries must be an int count (old frontend did .length -> undefined -> 0)"
