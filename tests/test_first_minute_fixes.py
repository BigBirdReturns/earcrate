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


def test_identify_apply_and_rollback_round_trip(tmp_path):
    import numpy as np, soundfile as sf
    from mutagen import File as MF
    core, music = _core(tmp_path)
    core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    p = music / "song.flac"
    t = np.linspace(0, 2, 44100 * 2, endpoint=False)
    sf.write(str(p), (0.4 * np.sin(2 * np.pi * 200 * t)).astype("float32"), 44100, format="FLAC")
    m = MF(str(p), easy=True); m["artist"] = ["Old Artist"]; m.save()

    proposals = [{"path": str(p), "artist": "New Artist", "title": "New Title", "score": 0.99}]
    dry = core.apply_identities({"apply": False, "proposals": proposals})
    assert dry.get("signature") and dry.get("would_retag", 0) >= 1, "dry-run must return a signature to echo back"
    ap = core.apply_identities({"apply": True, "proposals": proposals, "signature": dry["signature"]})
    assert ap.get("ok") is not False, f"apply failed: {ap}"
    assert (MF(str(p), easy=True).get("artist") or [""])[0] == "New Artist"

    js = core.identify_journals()
    assert js.get("items"), "identify_journals must list the journal (backs the Undo button)"
    # Rollback with no journal arg defaults to the newest journal.
    rb = core.rollback_identities({"apply": True})
    assert rb.get("ok") is not False, f"rollback failed: {rb}"
    assert (MF(str(p), easy=True).get("artist") or [""])[0] == "Old Artist", "rollback must restore the original tag"


def test_render_plan_refuses_cleanly(tmp_path):
    core, music = _core(tmp_path)
    core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    # No arrangement -> clean error, never a crash.
    e1 = core.render_plan({})
    assert e1.get("ok") is False and "arrangement" in (e1.get("error") or "")
    # An empty/failing plan is refused by the pre-render gate, never rendered as theater.
    e2 = core.render_plan({"arrangement": {"sections": [{"bars": 8, "layers": []}], "bpm": 120, "params": {"seed": 7}}})
    assert isinstance(e2, dict) and (e2.get("ok") is False or "render" in e2)


def test_machine_capabilities_probe_and_degrade(tmp_path):
    """The capability probe must report the real box (never assume one) and derive
    settings that degrade gracefully: no CUDA -> noop stems; the recommended stem
    provider must match the probed GPU."""
    core, _ = _core(tmp_path)
    cap = core.machine_capabilities()  # config-optional
    assert cap.get("ok") and cap.get("cpu_cores", 0) >= 1
    rec = cap.get("recommended") or {}
    assert rec.get("stem_provider") in ("noop", "demucs")
    # capability-aware, not hardcoded: demucs iff a CUDA GPU was actually probed
    assert (rec["stem_provider"] == "demucs") == bool(cap.get("gpu", {}).get("cuda")), \
        "stem provider recommendation must follow the probed GPU, not a hardcoded assumption"
    assert rec.get("workers", 0) >= 1 and rec.get("tier")


def test_cache_root_redirects_to_fast_disk(tmp_path):
    """The hot cache (L3 stems + transforms) must follow EARCRATE_CACHE_ROOT so it
    can live on a fast NVMe independent of the workspace, and default to
    agent_root/cache when unset (no regression)."""
    import os
    core, music = _core(tmp_path)
    core.configure_workspace({"music_folder": str(music), "workspace_folder": str(tmp_path / "WS")})
    agent = str(core.config.agent_root)

    os.environ.pop("EARCRATE_CACHE_ROOT", None)
    core._export_l3_root()
    assert agent in os.environ["EARCRATE_L3_ROOT"], "default cache must live under agent_root"

    nvme = tmp_path / "nvme"; nvme.mkdir()
    os.environ["EARCRATE_CACHE_ROOT"] = str(nvme)
    try:
        core._export_l3_root()
        assert str(nvme) in os.environ["EARCRATE_L3_ROOT"], "EARCRATE_CACHE_ROOT must redirect the cache"
        assert str(nvme) in str(core._cache_root())
    finally:
        os.environ.pop("EARCRATE_CACHE_ROOT", None)


def test_machine_defaults_auto_seed(tmp_path):
    """A committed machine preset auto-configures on first run (no manual POST),
    routing the cache to the preset's fast disk — but ONLY when the library exists,
    so it's a safe no-op on any other box."""
    import json
    from earcrate.app import EarcrateCore
    home = tmp_path / "home"; home.mkdir()
    os.environ["EARCRATE_HOME"] = str(home)
    for k in ("EARCRATE_CACHE_ROOT", "EARCRATE_STEMS", "EARCRATE_DEFAULTS"):
        os.environ.pop(k, None)
    master = tmp_path / "D_music"; master.mkdir()
    ws = tmp_path / "D_ws"; cache = tmp_path / "S_cache"
    (home / "machine_defaults.json").write_text(json.dumps({
        "master_root": str(master), "workspace_folder": str(ws),
        "cache_root": str(cache), "stem_provider": "demucs", "workers": 0}))
    c = EarcrateCore()
    assert c.config is not None, "preset must auto-configure when the library exists"
    assert str(c.config.master_root) == str(master)
    assert os.environ.get("EARCRATE_CACHE_ROOT") == str(cache), "cache must route to the preset's disk"

    # missing library drive -> safe no-op
    home2 = tmp_path / "home2"; home2.mkdir()
    os.environ["EARCRATE_HOME"] = str(home2); os.environ.pop("EARCRATE_CACHE_ROOT", None)
    (home2 / "machine_defaults.json").write_text(json.dumps({
        "master_root": str(tmp_path / "does_not_exist"), "workspace_folder": str(ws)}))
    assert EarcrateCore().config is None, "must not configure against a missing library"


def test_relocate_workspace_preserves_db(tmp_path):
    """Relocating a workspace (e.g. off C: onto D:) preserves the analyzed DB and
    renders (no re-analyze), drops the stale config, and does NOT move the
    regenerable cache (it rebuilds on the NVMe)."""
    core, _ = _core(tmp_path)
    old = tmp_path / "C_ws"; new = tmp_path / "D_ws"
    (old / "agent").mkdir(parents=True); (old / "work" / "renders").mkdir(parents=True)
    (old / "agent" / "cache" / "L3").mkdir(parents=True)
    (old / "agent" / "earcrate.sqlite").write_text("DB")
    (old / "agent" / "config.json").write_text('{"master_root":"C:/stale"}')
    (old / "work" / "renders" / "set.wav").write_text("WAV")
    (old / "agent" / "cache" / "L3" / "stem.bin").write_text("STEM")

    assert core.relocate_workspace({"old": str(old), "new": str(new)}).get("dry_run") is True
    res = core.relocate_workspace({"old": str(old), "new": str(new), "apply": True})
    assert res.get("ok")
    assert (new / "agent" / "earcrate.sqlite").read_text() == "DB", "DB must survive (no re-analyze)"
    assert (new / "work" / "renders" / "set.wav").exists()
    assert not (new / "agent" / "config.json").exists(), "stale config must be dropped"
    assert not (new / "agent" / "cache").exists(), "regenerable cache must not be moved"
    assert not (old / "agent").exists(), "old tree must be moved out"
