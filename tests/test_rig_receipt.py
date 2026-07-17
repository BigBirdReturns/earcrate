"""Gates for the rig-receipt harness (scripts/run_rig_receipt.py).

Hermetic and dependency-free (no pytest, no browser, no real gates, no
subprocesses, no real workspace): pins the harness's STATE MACHINE and SAFETY
logic — receipt state, resume, path safety, and failure classification — the
parts that must be trusted before the owner relies on the receipt on the box.
Runs under tests/run_gates.py (functions take no args or a single tmp_path).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_rig_receipt as R  # noqa: E402


def _raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    raise AssertionError(f"expected {exc.__name__} but no exception was raised")


# --------------------------------------------------------------------------
# failure classification + exit codes
# --------------------------------------------------------------------------
def test_classification_table():
    def stages(*ss):
        return [{"status": s} for s in ss]
    assert R.classify_overall(stages("passed", "passed")) == R.COMPLETE
    assert R.classify_overall(stages("passed", "failed", "skipped")) == R.FAILED  # failed dominates
    assert R.classify_overall(stages("passed", "skipped")) == R.INCOMPLETE
    assert R.classify_overall(stages("passed", "pending_manual")) == R.INCOMPLETE
    assert R.classify_overall(stages("passed", "pending")) == R.INCOMPLETE
    assert R.classify_overall(stages("failed")) == R.FAILED


def test_exit_codes_distinct():
    assert R.exit_code_for(R.COMPLETE) == 0
    assert R.exit_code_for(R.FAILED) == 1
    assert R.exit_code_for(R.INCOMPLETE) == 2
    assert len({R.exit_code_for(R.COMPLETE), R.exit_code_for(R.FAILED), R.exit_code_for(R.INCOMPLETE)}) == 3


def test_skipped_and_pending_never_become_success():
    assert R.classify_overall([{"status": "passed"}, {"status": "skipped"}]) != R.COMPLETE
    assert R.classify_overall([{"status": "passed"}, {"status": "pending_manual"}]) != R.COMPLETE


def test_parse_gate_summary_discovers_count():
    assert R.parse_gate_summary("noise\nSUMMARY 201/201 gates passed\n") == (201, 201)
    assert R.parse_gate_summary("SUMMARY 198/201 gates passed") == (198, 201)
    assert R.parse_gate_summary("no summary here") is None


# --------------------------------------------------------------------------
# path safety
# --------------------------------------------------------------------------
def test_scratch_must_be_outside_music(tmp_path):
    music = tmp_path / "Music"; music.mkdir()
    _raises(ValueError, R.assert_scratch_safe, music, music)                 # equal
    _raises(ValueError, R.assert_scratch_safe, music / "receipt", music)     # scratch inside music
    outer = tmp_path / "outer"; (outer / "Music").mkdir(parents=True)
    _raises(ValueError, R.assert_scratch_safe, outer, outer / "Music")       # music inside scratch
    R.assert_scratch_safe(tmp_path / "scratch", music)                       # separate -> must not raise


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------
def test_redaction_home_and_token():
    home = "/home/bob"
    s = R.redact_path("open http://127.0.0.1:9/?token=ABC123 from /home/bob/EarCrate/x.wav", home)
    assert "ABC123" not in s and "token=REDACTED" in s
    assert "/home/bob" not in s and "~/EarCrate/x.wav" in s
    # generic /Users/<name> and /home/<name> redact even without a home hint
    assert "USER" in R.redact_path("/Users/alice/thing", None)
    assert "USER" in R.redact_path("/home/carol/thing", None)


def test_redact_tree_is_deep():
    home = "/home/bob"
    tree = {"a": ["http://x/?token=T", {"b": "/home/bob/w"}], "n": 5}
    out = R.redact_tree(tree, home)
    assert out["a"][0].endswith("token=REDACTED")
    assert out["a"][1]["b"] == "~/w"
    assert out["n"] == 5  # non-strings untouched


# --------------------------------------------------------------------------
# receipt state: atomic persistence + round-trip + resume rule
# --------------------------------------------------------------------------
def _meta():
    return [{"key": "a", "name": "A", "tier": "t", "required": True},
            {"key": "b", "name": "B", "tier": "t", "required": True},
            {"key": "c", "name": "C", "tier": "t", "required": False}]


def test_run_state_roundtrip_and_atomic(tmp_path):
    sp = tmp_path / "run" / "state.json"
    st = R.RunState.new(sp, "run1", "HEADSHA", {"scratch": str(tmp_path)}, _meta())
    st.save()
    assert sp.exists()
    assert not list(sp.parent.glob(".state_*"))   # atomic write leaves no temp file
    st2 = R.RunState.load(sp)
    assert st2.data["run_id"] == "run1" and st2.data["git_head"] == "HEADSHA"
    assert [s["key"] for s in st2.data["stages"]] == ["a", "b", "c"]
    assert all(s["status"] == R.PENDING for s in st2.data["stages"])
    assert st2.stage("b")["required"] is True


def test_resume_rule_only_skips_passed():
    assert R._stage_needs_run(R.PENDING) is True
    assert R._stage_needs_run(R.FAILED) is True
    assert R._stage_needs_run(R.SKIPPED) is True
    assert R._stage_needs_run(R.PENDING_MANUAL) is True
    assert R._stage_needs_run(R.PASSED) is False


def test_interrupted_run_resumes_and_completes(tmp_path):
    sp = tmp_path / "run" / "state.json"
    meta = _meta()
    calls = {"a": 0, "b": 0, "c": 0}

    def fn_a(ctx):
        calls["a"] += 1
        return R.PASSED, {"ran": calls["a"]}

    def fn_b(ctx):
        calls["b"] += 1
        if calls["b"] == 1:
            raise KeyboardInterrupt()   # power-loss / Ctrl+C mid-run
        return R.PASSED, {"ran": calls["b"]}

    def fn_c(ctx):
        calls["c"] += 1
        return R.PASSED, {}

    funcs = {"a": fn_a, "b": fn_b, "c": fn_c}
    st = R.RunState.new(sp, "run1", "HEAD", {}, meta); st.save()

    _raises(KeyboardInterrupt, R.execute_stages, None, st, meta, funcs)
    disk = R.RunState.load(sp)   # checkpoint persisted the partial run
    assert disk.stage("a")["status"] == R.PASSED
    assert disk.stage("b")["status"] == R.PENDING
    assert disk.stage("c")["status"] == R.PENDING
    assert calls == {"a": 1, "b": 1, "c": 0}

    resumed = R.RunState.load(sp)
    overall = R.execute_stages(None, resumed, meta, funcs)
    assert calls["a"] == 1, "a already passed; resume must not redo it"
    assert calls["b"] == 2 and calls["c"] == 1
    assert overall == R.COMPLETE
    assert [s["status"] for s in resumed.data["stages"]] == [R.PASSED, R.PASSED, R.PASSED]


def test_failed_stage_is_rerun_on_resume(tmp_path):
    sp = tmp_path / "run" / "state.json"
    meta = [{"key": "x", "name": "X", "tier": "t", "required": True}]
    n = {"x": 0}

    def fn_x(ctx):
        n["x"] += 1
        return (R.FAILED, {}) if n["x"] == 1 else (R.PASSED, {})

    st = R.RunState.new(sp, "r", "HEAD", {}, meta); st.save()
    assert R.execute_stages(None, st, meta, {"x": fn_x}) == R.FAILED
    assert R.execute_stages(None, R.RunState.load(sp), meta, {"x": fn_x}) == R.COMPLETE
    assert n["x"] == 2


def test_exception_in_stage_is_classified_failed(tmp_path):
    sp = tmp_path / "run" / "state.json"
    meta = [{"key": "x", "name": "X", "tier": "t", "required": True}]

    def boom(ctx):
        raise RuntimeError("kaboom")

    st = R.RunState.new(sp, "r", "HEAD", {}, meta); st.save()
    assert R.execute_stages(None, st, meta, {"x": boom}) == R.FAILED
    assert st.stage("x")["status"] == R.FAILED
    assert "kaboom" in json.dumps(st.stage("x")["detail"])


# --------------------------------------------------------------------------
# receipt writing: redacted committable JSON + Markdown, right overall/exit
# --------------------------------------------------------------------------
def test_write_receipts_redacted_and_classified(tmp_path):
    sp = tmp_path / "run" / "state.json"
    st = R.RunState.new(sp, "runX", "abc123def456", {}, _meta())
    st.stage("a")["status"] = R.PASSED
    st.stage("b")["status"] = R.PASSED
    st.stage("c")["status"] = R.PENDING_MANUAL
    # a /Users/<name> path + a token redact regardless of the real home
    st.stage("c")["detail"] = {"render_path": "/Users/alice/EarCrate/out.wav", "how": "add a verdict"}
    st.data["log_ledger"] = [{"key": "a", "command": "python x --url=http://h/?token=SECRET",
                              "exit_code": 0, "log": "logs/a.01.log", "log_sha256": "d" * 64}]
    st.save()
    run_dir = tmp_path / "run"
    R._write_receipts(st, tmp_path, run_dir, overall=R.INCOMPLETE)

    j = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert j["overall"] == R.INCOMPLETE and j["exit_code"] == 2
    assert j["git_head"] == "abc123def456"
    blob = json.dumps(j)
    assert "SECRET" not in blob and "token=REDACTED" in blob
    assert "/Users/alice" not in blob and "/Users/USER" in blob
    md = (run_dir / "receipt.md").read_text(encoding="utf-8")
    assert "pending_manual" in md and "abc123def456" in md
    assert "does NOT prove" in md   # markdown never pretends outstanding work is done


# --------------------------------------------------------------------------
# registry integrity: every declared stage has an implementation + valid shape
# --------------------------------------------------------------------------
def test_stage_registry_is_complete():
    meta_keys = [m["key"] for m in R.STAGE_META]
    assert set(meta_keys) == set(R.STAGE_FUNCS), "every STAGE_META key needs a function and vice versa"
    assert len(meta_keys) == 14
    required = {m["key"] for m in R.STAGE_META if m["required"]}
    assert {"gates", "verify_package", "workbench_dom", "acceptance", "real_project",
            "edit_undo_redo", "ranker", "piano"} <= required
    not_required = {m["key"] for m in R.STAGE_META if not m["required"]}
    # listening-dependent stages are split into mechanical + verdict
    assert {"allin1", "rubberband_mechanical", "rubberband_verdict",
            "techno_mechanical", "techno_verdict"} <= not_required
    # verdict stages sit in the human tier; mechanical in gpu; a verdict is never required-mechanical
    tiers = {m["key"]: m["tier"] for m in R.STAGE_META}
    assert tiers["rubberband_verdict"] == R.TIER_HUMAN and tiers["rubberband_mechanical"] == R.TIER_GPU
    assert tiers["techno_verdict"] == R.TIER_HUMAN


# ==========================================================================
# STAGE-LEVEL hermetic gates (item 11): exercise the real stage BODIES with
# fake subprocess/helper results + fake artifacts. These pin the exact
# properties the read-only review flagged: a mechanical stage can never PASS
# on a nonzero exit, an unreadable machine-result, a missing artifact, a
# silent provider fallback, or a human verdict laid over a failed render.
# No real subprocess, browser, GPU, or library is touched.
# ==========================================================================
import types  # noqa: E402


class _Args(types.SimpleNamespace):
    """Argparse-shaped stand-in with the fields the stages read."""
    def __init__(self, **kw):
        base = dict(workspace="/ws", scratch="/scratch", profile="remix_prettylights_v1",
                    real_seconds=4.0, piano_iterations=3, run_id="runT", external_vocal=None,
                    chromium=None, allow_dirty=False, resume=False,
                    verdict_real_render=None, verdict_rubberband=None, verdict_techno=None)
        base.update(kw)
        super().__init__(**base)


class _FakeState:
    def __init__(self):
        self.data = {"run_id": "runT", "log_ledger": []}
        self._stages = {}

    def stage(self, key):
        return self._stages.setdefault(key, {"key": key, "detail": {}, "status": R.PENDING})


class _FakeCtx:
    """Feeds canned run_subprocess / run_helper records (keyed by stage sub-key)
    to a real stage function. Records artifacts are provided by the test on disk."""
    def __init__(self, tmp_path, args, sub=None, helper=None, scratch_ok=True, project_id=None):
        self.args = args
        self.scratch = Path(tmp_path)
        self.python = sys.executable
        self.root = Path(tmp_path)
        self.logs_dir = self.scratch / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_root = self.scratch / "runs" / "runT"
        self._sub = dict(sub or {})
        self._helper = dict(helper or {})
        self.calls = []
        self.sub_envs = {}     # key -> env passed to run_subprocess (for baseline assertions)
        self.state = _FakeState()
        self.state.data["scratch_workspace"] = ({"ok": True, "home": str(self.scratch / "home")}
                                                 if scratch_ok else {"ok": False, "reason": "no clone"})
        if project_id:
            self.state.data["real_project_id"] = project_id

    def run_dir(self, *parts):
        p = self.run_root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _rel(self, p):
        return None if p is None else str(p)

    def tail(self, log_rel, n=4000):
        return ""

    def _mk(self, key, canned, json_out):
        rec = dict(canned or {})
        rec.setdefault("key", key)
        rec.setdefault("log", f"logs/{key}.log")
        rec.setdefault("exit_code", 0)
        if json_out or "result" in rec:
            rec.setdefault("result", None)
            rec.setdefault("result_readable", rec.get("result") is not None)
            rec.setdefault("result_path", f"logs/{key}.result.json")
        return rec

    def run_subprocess(self, key, cmd, env=None, cwd=None, timeout=None, json_out=False):
        self.calls.append(("sub", key))
        self.sub_envs[key] = dict(env or {})
        return self._mk(key, self._sub.get(key), json_out)

    def run_helper(self, key, src, argv, env=None, timeout=None):
        self.calls.append(("helper", key, src))
        return self._mk(key, self._helper.get(key), True)


def _wav(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFFfakewavdata")
    return str(path)


# ---- item 3 + nested-JSON: real_project reads the --json-out result FILE, and a
# command that exits 0 with an unreadable result is an explicit FAILURE ----------
def test_real_project_reads_nested_json_and_passes(tmp_path):
    render_wav = _wav(tmp_path / "renders" / "out.wav")
    report = _wav(tmp_path / "renders" / "out.report.json")     # must exist on disk to be hashed
    edl = _wav(tmp_path / "exports" / "p.edl.json")
    rpp = _wav(tmp_path / "exports" / "p.rpp")
    sheet = _wav(tmp_path / "exports" / "p.sheet.md")
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "real_compile": {"result": {"project_id": "p1", "revision_sha": "r1", "score_sha": "s1"}},
        "real_render": {"result": {"type": "render_project", "path": render_wav, "report": report,
                                   "revision_sha": "r2", "score_sha": "s1"}},
        "real_show": {"result": {"ok": True, "project": {"active_revision_sha": "r2"}}},
        "real_export": {"result": {"ok": True, "edl": edl, "rpp": rpp, "sheet": sheet}},
    })
    status, detail = R.stage_real_project(ctx)
    assert status == R.PASSED
    assert detail["project_id"] == "p1"               # read from the result FILE, not stdout
    # report + all three exports must be present AND hashed
    assert detail["render_sha256"] and detail["report_sha256"]
    assert detail["export_edl_sha256"] and detail["export_rpp_sha256"] and detail["export_sheet_sha256"]
    assert ctx.state.data["real_project_id"] == "p1"


def test_real_project_fails_when_export_artifact_missing(tmp_path):
    render_wav = _wav(tmp_path / "renders" / "out.wav")
    report = _wav(tmp_path / "renders" / "out.report.json")
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "real_compile": {"result": {"project_id": "p1", "revision_sha": "r1", "score_sha": "s1"}},
        "real_render": {"result": {"type": "render_project", "path": render_wav, "report": report}},
        "real_show": {"result": {"ok": True, "project": {"active_revision_sha": "r2"}}},
        # export names artifacts that are NOT on disk -> the stage must FAIL
        "real_export": {"result": {"ok": True, "edl": str(tmp_path / "missing.edl"),
                                   "rpp": str(tmp_path / "missing.rpp"), "sheet": str(tmp_path / "missing.md")}},
    })
    status, detail = R.stage_real_project(ctx)
    assert status == R.FAILED and "export" in detail["reason"]


def test_real_project_fails_when_result_unreadable(tmp_path):
    # exit 0 but the --json-out receipt did not parse -> must FAIL, never silent pass
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "real_compile": {"exit_code": 0, "result": None, "result_readable": False},
    })
    status, detail = R.stage_real_project(ctx)
    assert status == R.FAILED           # exit 0 + unreadable receipt is an explicit failure
    assert detail.get("reason")         # and it says why (points at the log / crate)


def test_real_project_fails_when_render_wav_missing(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "real_compile": {"result": {"project_id": "p1", "revision_sha": "r1", "score_sha": "s1"}},
        # render claims success but the WAV path does not exist on disk
        "real_render": {"result": {"type": "render_project", "path": str(tmp_path / "nope.wav")}},
    })
    status, detail = R.stage_real_project(ctx)
    assert status == R.FAILED and "WAV" in detail["reason"]


# ---- item 3 + 7: renders are CONTAINED under working_root/renders, provider proof
# comes from the render RECEIPT (+ non-unity transform + spectral A/B), and a human
# verdict can NEVER convert a failed/unproven render into passed -------------------
def _report_file(path: Path, provider, rb_calls=0):
    inv = {"rubberband_time_stretch": rb_calls, "rubberband_pitch_shift": 0,
           "phase_vocoder_time_stretch": 0, "phase_vocoder_pitch_shift": 0,
           "near_unity_resample": 0, "dry_varispeed": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"transform_provider": provider, "transform_invocations": inv}), encoding="utf-8")
    return str(path)


def _rb_ctx(tmp_path, args, rb_default_ok, rb_rb_ok, resolved="rubberband",
            rb_calls=2, spectral_l1=0.5, def_provider="phase_vocoder", rb_provider="rubberband"):
    working_root = tmp_path / "ws_work"
    out = working_root / "renders" / "rubberband_ab"
    rep_dir = tmp_path / "reports"
    if rb_default_ok:
        _wav(out / "default.wav")
    if rb_rb_ok:
        _wav(out / "rubberband.wav")
    rep_def = _report_file(rep_dir / "def.json", def_provider)
    rep_rb = _report_file(rep_dir / "rb.json", rb_provider, rb_calls=rb_calls)
    ctx = _FakeCtx(tmp_path, args, project_id="p1",
                   sub={
                       "rb_default": {"result": {"type": "render_project", "report": rep_def}
                                      if rb_default_ok else {"type": "render_rejected"}},
                       "rb_rubberband": {"result": {"type": "render_project", "report": rep_rb}
                                         if rb_rb_ok else {"type": "render_rejected"}},
                   },
                   helper={
                       "rubberband_probe": {"result": {"ready": True}},
                       "rb_provider": {"result": {"effective": resolved}},
                       "rb_spectral": {"result": {"ok": True, "spectral_l1": spectral_l1}},
                   })
    ctx.state.data["scratch_workspace"]["working_root"] = str(working_root)
    ctx.state.data["scratch_workspace"]["agent_root"] = str(tmp_path / "ws_agent")
    return ctx


# ---- Rubber Band MECHANICAL: renders once, persists the exact A/B evidence -----
def test_rubberband_mechanical_fails_when_a_render_fails(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=False)
    status, detail = R.stage_rubberband_mechanical(ctx)
    assert status == R.FAILED and not detail.get("mechanical_ok")


def test_rubberband_mechanical_passes_and_persists_hashes(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=True)
    status, detail = R.stage_rubberband_mechanical(ctx)
    assert status == R.PASSED and detail["mechanical_ok"] is True
    assert detail["renders_contained_under"].endswith("renders/rubberband_ab")   # containment
    assert detail["rubberband_real_transform_calls"] == 2
    assert detail["default_sha256"] and detail["rubberband_sha256"]              # persisted for binding
    assert detail["report_default_sha256"] and detail["report_rubberband_sha256"]
    assert detail["cache_namespace_cleared"] in (True, False)                    # cache isolation recorded


def test_rubberband_mechanical_fails_when_receipt_provider_wrong(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=True, rb_provider="phase_vocoder")
    assert R.stage_rubberband_mechanical(ctx)[0] == R.FAILED


def test_rubberband_mechanical_fails_when_no_nonunity_transform(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=True, rb_calls=0)
    status, detail = R.stage_rubberband_mechanical(ctx)
    assert status == R.FAILED and "NON-UNITY" in detail["reason"]


def test_rubberband_mechanical_fails_when_spectral_identical(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=True, spectral_l1=0.0)
    status, detail = R.stage_rubberband_mechanical(ctx)
    assert status == R.FAILED and "spectral" in detail["reason"]


# ---- Rubber Band VERDICT: never renders; binds to the exact stored WAV pair -----
def _seed_rb_mechanical(ctx, tmp_path):
    """Run the real mechanical stage so its evidence is persisted in state."""
    status, detail = R.stage_rubberband_mechanical(ctx)
    assert status == R.PASSED
    ctx.state.stage("rubberband_mechanical")["status"] = R.PASSED
    ctx.state.stage("rubberband_mechanical")["detail"] = detail
    return detail


def test_rubberband_verdict_pending_without_verdict(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband=None), rb_default_ok=True, rb_rb_ok=True)
    _seed_rb_mechanical(ctx, tmp_path)
    status, detail = R.stage_rubberband_verdict(ctx)
    assert status == R.PENDING_MANUAL and detail["pair_unchanged"] is True


def test_rubberband_verdict_binds_to_exact_pair(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"), rb_default_ok=True, rb_rb_ok=True)
    _seed_rb_mechanical(ctx, tmp_path)
    status, detail = R.stage_rubberband_verdict(ctx)
    assert status == R.PASSED and detail["verdict"] == "rubberband"
    assert detail["verdict_bound_to"]["rubberband_sha256"] == detail["current_rubberband_sha256"]


def test_rubberband_verdict_rejects_substituted_audio(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"), rb_default_ok=True, rb_rb_ok=True)
    md = _seed_rb_mechanical(ctx, tmp_path)
    # the owner "hears" it, but overnight the rubberband WAV is replaced
    Path(md["rubberband_path"]).write_bytes(b"REPLACED-DIFFERENT-AUDIO")
    status, detail = R.stage_rubberband_verdict(ctx)
    assert status == R.FAILED and detail["pair_unchanged"] is False and detail.get("verdict") is None


def test_rubberband_verdict_skips_when_mechanical_not_green(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"), rb_default_ok=True, rb_rb_ok=True)
    # mechanical never ran (still pending) -> verdict has nothing to bind
    assert R.stage_rubberband_verdict(ctx)[0] == R.SKIPPED


# ---- techno MECHANICAL + VERDICT --------------------------------------------
def _techno_result(tmp_path, **over):
    render = _wav(tmp_path / "renders" / "techno.wav")
    report = _wav(tmp_path / "renders" / "techno.report.json")
    edl = _wav(tmp_path / "exports" / "t.edl"); rpp = _wav(tmp_path / "exports" / "t.rpp")
    sheet = _wav(tmp_path / "exports" / "t.sheet")
    import hashlib
    def sh(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    base = {"render_ok": True, "render_path": render, "render_sha256": sh(render),
            "report_path": report, "report_sha256": sh(report),
            "external_source_identity_matched": True, "identity_match_kind": "pcm_sha256",
            "external_pcm_sha256": "extpcmsha", "seed": 42,
            "export_ok": True, "export_paths": {"edl": edl, "rpp": rpp, "sheet": sheet},
            "export_sha256": {"edl": sh(edl), "rpp": sh(rpp), "sheet": sh(sheet)},
            "project_id": "pt", "revision_sha": "rv", "score_sha": "sv", "external_vocal_basename": "vox.wav"}
    base.update(over)
    return {"result": base}


def test_techno_mechanical_cannot_pass_a_failed_render(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal)),
                   helper={"techno": _techno_result(tmp_path, render_ok=False, render_sha256=None, reason="render failed")})
    status, detail = R.stage_techno_mechanical(ctx)
    assert status == R.FAILED and not detail.get("mechanical_ok")


def test_techno_mechanical_fails_on_inexact_identity(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal)),
                   helper={"techno": _techno_result(tmp_path, external_source_identity_matched=False, identity_match_kind=None)})
    assert R.stage_techno_mechanical(ctx)[0] == R.FAILED


def test_techno_mechanical_fails_when_export_incomplete(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal)),
                   helper={"techno": _techno_result(tmp_path, export_ok=False, export_sha256={"edl": "a"})})
    assert R.stage_techno_mechanical(ctx)[0] == R.FAILED


def test_techno_mechanical_records_fixed_seed(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal)), helper={"techno": _techno_result(tmp_path)})
    status, detail = R.stage_techno_mechanical(ctx)
    assert status == R.PASSED and detail["mechanical_ok"] is True
    # the fixed seed passed to the helper is derived from run_id (reproducible)
    assert isinstance(detail["seed"], int)


def _seed_techno_mechanical(ctx, tmp_path):
    status, detail = R.stage_techno_mechanical(ctx)
    assert status == R.PASSED
    ctx.state.stage("techno_mechanical")["status"] = R.PASSED
    ctx.state.stage("techno_mechanical")["detail"] = detail
    return detail


def test_techno_verdict_binds_to_render_and_exports(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": _techno_result(tmp_path),
                           "techno_reidentity": {"result": {"external_pcm_sha256": "extpcmsha"}}})
    _seed_techno_mechanical(ctx, tmp_path)
    status, detail = R.stage_techno_verdict(ctx)
    assert status == R.PASSED and detail["verdict"] == "keep"
    assert detail["render_unchanged"] and detail["exports_unchanged"]
    assert detail["external_pcm_identity_rebound"] is True


def test_techno_verdict_rejects_rerendered_wav(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": _techno_result(tmp_path),
                           "techno_reidentity": {"result": {"external_pcm_sha256": "extpcmsha"}}})
    md = _seed_techno_mechanical(ctx, tmp_path)
    Path(md["render_path"]).write_bytes(b"RERENDERED-DIFFERENT")   # a re-render overnight
    status, detail = R.stage_techno_verdict(ctx)
    assert status == R.FAILED and detail["render_unchanged"] is False and detail.get("verdict") is None


def test_techno_verdict_rejects_changed_external_identity(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": _techno_result(tmp_path),
                           "techno_reidentity": {"result": {"external_pcm_sha256": "A-DIFFERENT-SOURCE"}}})
    _seed_techno_mechanical(ctx, tmp_path)
    status, detail = R.stage_techno_verdict(ctx)
    assert status == R.FAILED and detail["external_pcm_identity_rebound"] is False


# ---- item 4: allin1 decoder signature + backend; honest metric names; a real
# transition candidate run through BOTH analyses; silent librosa fallback FAILS ---
def test_allin1_helper_uses_real_decoder_signature_and_backend_guard():
    src = R._ALLIN1_SAMPLE_SRC
    assert "duration=seconds" in src, "allin1 must call decode_audio with duration=<seconds>"
    assert "decode_audio(Path(p), sr=sr, duration=seconds)" in src
    assert "max_seconds" not in src and "mono=True" not in src   # the OLD wrong signature is gone
    # a requested allin1 backend that silently falls back to librosa must be a FAILURE
    assert 'A.get("beat_backend") != "allin1"' in src and "silent fallback" in src
    assert 'os.environ["EARCRATE_BEATS"] = "allin1"' in src
    # HONEST metric names (these are bpm_confidence, not downbeat confidence), and a
    # REAL transition candidate run through both analyses — not a delta relabelled.
    assert "librosa_bpm_confidence" in src and "allin1_bpm_confidence" in src
    assert "downbeat_conf" not in src and "transition_feasibility_change" not in src
    assert "best_transition" in src and "transition_probe" in src


def test_allin1_stage_fails_on_silent_fallback(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"capability": {"ready": True},
                              "reason": "allin1 requested but backend was librosa (silent fallback)"}}})
    status, detail = R.stage_allin1(ctx)
    assert status == R.FAILED and "fallback" in detail["reason"]


def test_allin1_stage_skips_when_not_installed(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"capability": {"ready": False}}}})
    status, detail = R.stage_allin1(ctx)
    assert status == R.SKIPPED and "install" in detail


def test_allin1_stage_passes_with_real_backend_and_transition_probe(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"ok": True, "capability": {"ready": True}, "tracks_sampled": 3,
                              "librosa_bpm_confidence": {"n": 3, "mean": 0.4},
                              "allin1_bpm_confidence": {"n": 3, "mean": 0.6},
                              "mean_bpm_confidence_delta": 0.2,
                              "transition_probe": {"pair": ["a", "b"], "changed": True,
                                                   "result": "transition_plan_changed"}}}})
    status, detail = R.stage_allin1(ctx)
    assert status == R.PASSED and detail["tracks_sampled"] == 3
    assert detail["transition_probe"]["result"] == "transition_plan_changed"


def test_allin1_stage_passes_on_measured_no_change(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"ok": True, "capability": {"ready": True}, "tracks_sampled": 2,
                              "transition_probe": {"pair": ["a", "b"], "changed": False,
                                                   "result": "measured_no_change"}}}})
    assert R.stage_allin1(ctx)[0] == R.PASSED


def test_allin1_stage_fails_without_transition_probe(tmp_path):
    # a confidence delta alone (no real transition probe) is not sufficient evidence
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"ok": True, "capability": {"ready": True}, "tracks_sampled": 3,
                              "mean_bpm_confidence_delta": 0.2}}})   # no transition_probe
    assert R.stage_allin1(ctx)[0] == R.SKIPPED   # inconclusive probe is a SKIP, never a pass


def test_allin1_one_track_sample_does_not_pass(tmp_path):
    # a one-track sample cannot form a transition pair -> SKIP, never PASS (item 5)
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"ok": True, "capability": {"ready": True}, "tracks_sampled": 1,
                              "transition_probe": {"pair": None, "result": "insufficient_tracks_for_pair"}}}})
    status, detail = R.stage_allin1(ctx)
    assert status == R.SKIPPED and status != R.PASSED


# ---- item 8: edit must be a real (non-no-op) change with undo/redo identity ----
def test_edit_helper_selects_a_differing_in_policy_value():
    src = R._EDIT_LIFECYCLE_SRC
    # picks a value guaranteed to differ from current while staying inside [lo,hi]
    assert "cand" in src and "policy_gain_bounds" in src
    assert "edited_pcm_differs" in src and "pcm_identity_restored" in src and "reopened_head_matches" in src
    assert "core.project_undo" in src and "core.project_redo" in src


def test_edit_stage_fails_on_no_op(tmp_path):
    # helper reports the edit did not change the PCM -> must FAIL (not a real edit)
    ctx = _FakeCtx(tmp_path, _Args(), project_id="p1", helper={
        "edit_undo_redo": {"result": {"new_revision": True, "edited_pcm_differs": False,
                                      "pcm_identity_restored": True, "reopened_head_matches": True}}})
    status, detail = R.stage_edit_undo_redo(ctx)
    assert status == R.FAILED


def test_edit_stage_fails_when_undo_does_not_restore(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), project_id="p1", helper={
        "edit_undo_redo": {"result": {"new_revision": True, "edited_pcm_differs": True,
                                      "pcm_identity_restored": False, "reopened_head_matches": True}}})
    assert R.stage_edit_undo_redo(ctx)[0] == R.FAILED


def test_edit_stage_passes_full_lifecycle(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), project_id="p1", helper={
        "edit_undo_redo": {"result": {"new_revision": True, "edited_pcm_differs": True,
                                      "pcm_identity_restored": True, "reopened_head_matches": True}}})
    assert R.stage_edit_undo_redo(ctx)[0] == R.PASSED


# ---- item 5: ranker must prove identical MEMBERSHIP (a pure reorder) -----------
def test_ranker_fails_when_membership_changes(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "ranker_train": {"result": {"ok": True, "model_sha": "m", "n_approved": 6, "n_rejected": 4}}},
        helper={"ranker_compare": {"result": {"ok": False, "pool_size": 10,
                                              "membership_identical": False, "order_changed": True}}})
    status, detail = R.stage_ranker(ctx)
    assert status == R.FAILED and detail["membership_identical"] is False


def test_ranker_passes_on_pure_reorder(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "ranker_train": {"result": {"ok": True, "model_sha": "m", "n_approved": 6, "n_rejected": 4}}},
        helper={"ranker_compare": {"result": {"ok": True, "pool_size": 10,
                                             "membership_identical": True, "order_changed": True}}})
    status, detail = R.stage_ranker(ctx)
    assert status == R.PASSED and detail["order_changed"] is True


def test_ranker_skips_on_insufficient_training_data(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "ranker_train": {"result": {"ok": False, "reason": "not enough labelled atoms"}}})
    status, detail = R.stage_ranker(ctx)
    assert status == R.SKIPPED


# ---- item 6: piano resume must preserve prior attempts VERBATIM ----------------
def _pattempt(i, verdict="kept"):
    # a realistic attempt-level record (project id, revision sha, path, verdict, reason)
    return {"iteration": i, "persona": "p", "seed": i, "project_id": f"pj{i}",
            "revision_sha": f"rv{i}", "path": f"/w/renders/{i}.wav", "verdict": verdict, "reason": None}


def test_piano_fails_when_prior_attempts_not_preserved(tmp_path):
    first = [_pattempt(0), _pattempt(1), _pattempt(2)]
    tampered = [_pattempt(99)] + first[1:] + [_pattempt(3), _pattempt(4)]   # rewrote attempt 0
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 3, "attempts": first}},
        "piano_2": {"result": {"complete": True, "attempted": 5, "attempts": tampered}},
    })
    status, detail = R.stage_piano(ctx)
    assert status == R.FAILED and detail["prior_attempts_preserved_verbatim"] is False


def test_piano_passes_when_resume_preserves_prior(tmp_path):
    first = [_pattempt(0), _pattempt(1), _pattempt(2)]
    resumed = first + [_pattempt(3), _pattempt(4)]
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 3, "attempts": first}},
        "piano_2": {"result": {"complete": True, "attempted": 5, "attempts": resumed}},
    })
    status, detail = R.stage_piano(ctx)
    assert status == R.PASSED and detail["prior_attempts_preserved_verbatim"] is True
    # attempt-level provenance retained verbatim in the committable receipt
    assert detail["attempts"][0]["project_id"] == "pj0" and detail["attempts"][0]["revision_sha"] == "rv0"
    assert detail["attempts"][4]["path"].endswith("4.wav")


def test_piano_fails_when_run1_exceeds_cap(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 9, "attempts": [_pattempt(i) for i in range(9)]}},
    })
    assert R.stage_piano(ctx)[0] == R.FAILED


# ---- crate-dependent stages honestly SKIP when there is no durable-state clone -
def test_crate_stages_skip_without_scratch_workspace(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), scratch_ok=False)
    for fn in (R.stage_real_project, R.stage_edit_undo_redo, R.stage_ranker, R.stage_piano):
        status, _ = fn(ctx)
        assert status == R.SKIPPED


# ---- item 1: run() checks scratch against the RESOLVED master_root and refuses
# (never reaching a crate-dependent stage) when it is unsafe or unresolvable -----
def _run_args(tmp_path, scratch):
    return _Args(workspace=str(tmp_path / "ws"), scratch=str(scratch))


def _patched_run(tmp_path, cfg, scratch, monkey):
    """Run run() with env/git/stage-exec stubbed so only the preflight logic runs."""
    saved = {name: getattr(R, name) for name in
             ("read_workspace_config_readonly", "_preflight_env", "git_head", "execute_stages",
              "_prepare_scratch_workspace", "_write_receipts")}
    ran = {"stages": False}

    def _exec(*a, **k):
        ran["stages"] = True
        return R.COMPLETE

    R.read_workspace_config_readonly = lambda workspace: cfg
    R._preflight_env = lambda args: {}
    R.git_head = lambda root: {"head": "deadbeef", "branch": "b", "upstream": "", "upstream_sha": "",
                               "dirty": False, "dirty_files": []}
    R.execute_stages = _exec
    R._prepare_scratch_workspace = lambda ctx: {"ok": True, "home": str(tmp_path / "h"),
                                                "master_root": cfg.get("master_root"), "backed_up_db": ["a.sqlite"]}
    R._write_receipts = lambda *a, **k: None
    try:
        code = R.run(_run_args(tmp_path, scratch))
    finally:
        for name, fn in saved.items():
            setattr(R, name, fn)
    return code, ran["stages"]


def test_run_refuses_when_master_root_unresolved(tmp_path):
    scratch = tmp_path / "scratch"
    code, ran = _patched_run(tmp_path, {}, scratch, None)   # cfg has NO master_root
    assert code == R.EXIT_FAILED
    assert ran is False, "no crate-dependent stage may run after an unresolved safety check"


def test_run_refuses_when_scratch_inside_music(tmp_path):
    music = tmp_path / "Music"; music.mkdir()
    scratch = music / "receipt"          # scratch INSIDE the resolved music library
    code, ran = _patched_run(tmp_path, {"master_root": str(music)}, scratch, None)
    assert code == R.EXIT_FAILED and ran is False
    # Item 2: an unsafe scratch must leave ZERO files behind — config resolution
    # and validation happen entirely in an OS-temp dir before anything is created.
    assert not scratch.exists(), "no receipt/log/state may be created under an unsafe scratch"
    leftovers = [p for p in music.rglob("*") if p.is_file()]
    assert leftovers == [], f"unsafe refusal wrote files under the music library: {leftovers}"


def test_run_proceeds_when_master_resolved_and_scratch_safe(tmp_path):
    music = tmp_path / "Music"; music.mkdir()
    scratch = tmp_path / "scratch"       # separate from music -> safe
    code, ran = _patched_run(tmp_path, {"master_root": str(music)}, scratch, None)
    assert code == R.EXIT_COMPLETE and ran is True


# ---- item 3: the --json-out contract reads the exact file (no stdout scrape) ---
def test_cli_json_out_writes_exact_result_file(tmp_path):
    # earcrate.cli._pop_json_out strips the flag; _emit writes the EXACT dict to the
    # file even though noise is also printed, so a reader never scrapes stdout.
    import importlib
    cli = importlib.import_module("earcrate.cli")
    argv = ["doctor", "--json-out", str(tmp_path / "r.json"), "--other"]
    out = cli._pop_json_out(argv)
    assert out == str(tmp_path / "r.json")
    assert argv == ["doctor", "--other"], "the flag+value are removed before argparse sees them"
    payload = {"ok": True, "nested": {"config": {"master_root": "/m"}}, "brace": "}{"}
    cli._emit(payload, out)
    assert json.loads(Path(out).read_text(encoding="utf-8")) == payload   # exact, no brace-scrape


# ==========================================================================
# Item 6: the real NVIDIA driver comes from nvidia-smi, NEVER torch.version.cuda
# ==========================================================================
def test_nvidia_smi_reports_real_driver_not_torch_toolkit():
    saved_which, saved_run = R.shutil.which, R.subprocess.run

    class _Res:
        stdout = "535.104.05, NVIDIA RTX A6000, 49140\n"
        stderr = ""

    R.shutil.which = lambda n: "/usr/bin/nvidia-smi" if n == "nvidia-smi" else None
    R.subprocess.run = lambda *a, **k: _Res()
    try:
        info = R._nvidia_smi()
    finally:
        R.shutil.which, R.subprocess.run = saved_which, saved_run
    assert info["driver_version"] == "535.104.05"      # the ACTUAL installed driver
    assert info["name"].startswith("NVIDIA")


def test_preflight_env_never_labels_torch_cuda_as_driver():
    import inspect
    src = inspect.getsource(R._preflight_env)
    # torch's cuda toolkit must be labelled honestly, never as the GPU driver.
    assert "torch_built_cuda_toolkit" in src
    assert '"driver":' not in src, "torch.version.cuda must never be stored under a 'driver' key"
    assert "_nvidia_smi()" in src


def test_nvidia_smi_absent_returns_empty():
    saved_which = R.shutil.which
    R.shutil.which = lambda n: None
    try:
        assert R._nvidia_smi() == {}
    finally:
        R.shutil.which = saved_which


# ==========================================================================
# REAL integration gates (item: canned _FakeCtx tests cannot be the sole
# authority for CLI parsing or renderer containment). These drive the ACTUAL
# earcrate CLI subprocess and the ACTUAL renderer path guard.
# ==========================================================================
import os          # noqa: E402
import subprocess  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_configure_cli_accepts_json_out_and_reopens_real_subprocess(tmp_path):
    # The scratch clone runs `earcrate configure ... --json-out <path>`. If configure
    # rejects the flag, the crate-dependent stages silently skip. Prove the REAL CLI
    # accepts it, writes the exact result file, and the workspace REOPENS.
    music = tmp_path / "music"; music.mkdir()
    ws = tmp_path / "ws"
    home = tmp_path / "home"; home.mkdir()
    out = tmp_path / "cfg.json"
    env = dict(os.environ); env["EARCRATE_HOME"] = str(home)
    proc = subprocess.run([sys.executable, "-m", "earcrate", "configure", "--music", str(music),
                           "--workspace", str(ws), "--json-out", str(out)],
                          cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"configure --json-out rejected: {proc.stderr[-800:]}"
    assert out.exists(), "configure did not write the --json-out result file"
    cfg = json.loads(out.read_text(encoding="utf-8"))
    assert cfg.get("ok") and Path(cfg["config"]["master_root"]).resolve() == music.resolve()
    # reopen: a doctor --json-out in the SAME home must resolve that config
    out2 = tmp_path / "doc.json"
    d = subprocess.run([sys.executable, "-m", "earcrate", "doctor", "--json-out", str(out2)],
                       cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert out2.exists(), f"doctor did not write result ({d.stderr[-400:]})"
    doc = json.loads(out2.read_text(encoding="utf-8"))
    assert (doc.get("config") or {}).get("master_root"), "reopened workspace did not resolve master_root"


def _renderable_project(tmp_path):
    """A real configured core + imported project on synthetic audio (reuses the
    project-gate fixtures so the render path is the genuine one)."""
    from test_projects import configured_core, _external_arrangement, _import_fixture
    core = configured_core(tmp_path)
    imported = _import_fixture(core, _external_arrangement(tmp_path))
    return core, imported["project"]["project_id"]


def test_render_report_records_transform_provider_and_invocations(tmp_path):
    # The render RECEIPT must carry the effective provider + real per-engine
    # invocation counts (evidence the rubberband stage reads instead of trusting
    # the resolver). On the default box no clip uses rubberband.
    core, pid = _renderable_project(tmp_path)
    result = core.project_render(pid)
    assert result["type"] == "render_project"
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["transform_provider"] in ("phase_vocoder", "rubberband")
    inv = report["transform_invocations"]
    assert {"rubberband_time_stretch", "rubberband_pitch_shift",
            "phase_vocoder_time_stretch", "phase_vocoder_pitch_shift"} <= set(inv)
    assert inv["rubberband_time_stretch"] == 0 and inv["rubberband_pitch_shift"] == 0   # default box


def test_project_render_dst_containment_guard_is_real(tmp_path):
    # The renderer's OWN path guard: an explicit --dst is permitted ONLY under
    # working_root/renders. The rubberband stage's old <scratch>/rubberband_ab
    # location would be REJECTED; the new working_root/renders/... is accepted.
    core, pid = _renderable_project(tmp_path)
    c = core.ensure_config()
    inside = c.working_root / "renders" / "rubberband_ab" / "rb.wav"
    r = core.project_render(pid, inside)
    assert r["type"] == "render_project" and Path(r["path"]).exists()
    outside = tmp_path / "rubberband_ab" / "escape.wav"   # NOT under working_root/renders
    _raises(ValueError, core.project_render, pid, outside)


# ==========================================================================
# FINAL PASS: verdicts are cryptographically bound to the exact audio; the
# mechanical stage is never re-run on resume; run_id is path-safe; the external
# vocal path never reaches the committable receipt; the spectral test covers the
# whole render (not a 32k prefix).
# ==========================================================================
_RB_META = [{"key": "rubberband_mechanical", "name": "rb mech", "tier": R.TIER_GPU, "required": False},
            {"key": "rubberband_verdict", "name": "rb verdict", "tier": R.TIER_HUMAN, "required": False}]
_TECHNO_META = [{"key": "techno_mechanical", "name": "tk mech", "tier": R.TIER_GPU, "required": False},
                {"key": "techno_verdict", "name": "tk verdict", "tier": R.TIER_HUMAN, "required": False}]


def _real_state(tmp_path, meta, **data):
    st = R.RunState.new(tmp_path / "state.json", "rid1", "HEAD", {}, meta)
    st.data.update(data)
    return st


def test_rubberband_mechanical_then_verdict_resume_warm_cache(tmp_path):
    # Pending mechanical run, then a verdict resume: the mechanical stage is NOT
    # re-rendered (its A/B + warm transform cache are frozen), and the verdict
    # binds to the exact stored WAV pair. (control question)
    working_root = tmp_path / "ws_work"
    out = working_root / "renders" / "rubberband_ab"
    _wav(out / "default.wav"); _wav(out / "rubberband.wav")
    rep_def = _report_file(tmp_path / "reports" / "def.json", "phase_vocoder")
    rep_rb = _report_file(tmp_path / "reports" / "rb.json", "rubberband", rb_calls=2)
    ctx = _FakeCtx(tmp_path, _Args(verdict_rubberband=None), project_id="p1",
                   sub={"rb_default": {"result": {"type": "render_project", "report": rep_def}},
                        "rb_rubberband": {"result": {"type": "render_project", "report": rep_rb}}},
                   helper={"rubberband_probe": {"result": {"ready": True}},
                           "rb_provider": {"result": {"effective": "rubberband"}},
                           "rb_spectral": {"result": {"ok": True, "spectral_l1": 0.5}}})
    st = _real_state(tmp_path, _RB_META, real_project_id="p1",
                     scratch_workspace={"ok": True, "home": str(tmp_path / "h"),
                                        "working_root": str(working_root), "agent_root": str(tmp_path / "agent")})
    ctx.state = st
    funcs = {"rubberband_mechanical": R.stage_rubberband_mechanical, "rubberband_verdict": R.stage_rubberband_verdict}

    overall1 = R.execute_stages(ctx, st, _RB_META, funcs)
    assert st.stage("rubberband_mechanical")["status"] == R.PASSED
    assert st.stage("rubberband_verdict")["status"] == R.PENDING_MANUAL
    assert overall1 == R.INCOMPLETE
    renders_after_1 = sum(1 for c in ctx.calls if c[:2] == ("sub", "rb_default"))
    assert renders_after_1 == 1

    # ---- resume tomorrow with the verdict; mechanical must NOT re-render ----
    ctx.args.verdict_rubberband = "rubberband"
    overall2 = R.execute_stages(ctx, st, _RB_META, funcs)
    renders_after_2 = sum(1 for c in ctx.calls if c[:2] == ("sub", "rb_default"))
    assert renders_after_2 == renders_after_1, "mechanical was re-rendered on resume (must be frozen)"
    vd = st.stage("rubberband_verdict")
    assert vd["status"] == R.PASSED and vd["detail"]["verdict"] == "rubberband"
    assert vd["detail"]["pair_unchanged"] is True
    assert vd["detail"]["verdict_bound_to"]["rubberband_sha256"]


def test_techno_mechanical_then_verdict_resume_identity_unchanged(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    tk = _techno_result(tmp_path)["result"]
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno=None),
                   helper={"techno": {"result": tk},
                           "techno_reidentity": {"result": {"external_pcm_sha256": tk["external_pcm_sha256"]}}})
    st = _real_state(tmp_path, _TECHNO_META,
                     scratch_workspace={"ok": True, "home": str(tmp_path / "h"),
                                        "working_root": str(tmp_path / "w"), "agent_root": str(tmp_path / "agent")})
    ctx.state = st
    funcs = {"techno_mechanical": R.stage_techno_mechanical, "techno_verdict": R.stage_techno_verdict}

    R.execute_stages(ctx, st, _TECHNO_META, funcs)
    assert st.stage("techno_mechanical")["status"] == R.PASSED
    assert st.stage("techno_verdict")["status"] == R.PENDING_MANUAL
    renders_after_1 = sum(1 for c in ctx.calls if c[:2] == ("helper", "techno"))
    prior_rev = st.stage("techno_mechanical")["detail"]["revision_sha"]
    prior_seed = st.stage("techno_mechanical")["detail"]["seed"]

    ctx.args.verdict_techno = "keep"
    R.execute_stages(ctx, st, _TECHNO_META, funcs)
    renders_after_2 = sum(1 for c in ctx.calls if c[:2] == ("helper", "techno"))
    assert renders_after_2 == renders_after_1, "techno was re-rendered/recompiled on resume"
    vd = st.stage("techno_verdict")
    assert vd["status"] == R.PASSED and vd["detail"]["verdict"] == "keep"
    assert vd["detail"]["render_unchanged"] and vd["detail"]["exports_unchanged"]
    # seed + revision identity is unchanged across the resume
    assert st.stage("techno_mechanical")["detail"]["revision_sha"] == prior_rev
    assert st.stage("techno_mechanical")["detail"]["seed"] == prior_seed


# ---- real_render_verdict is bound to real_project.render_sha256 ----------------
def _rrv_ctx(tmp_path, verdict, tamper=False):
    wav = tmp_path / "renders" / "real.wav"; _wav(wav)
    import hashlib
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    ctx = _FakeCtx(tmp_path, _Args(verdict_real_render=verdict))
    ctx.state._stages["real_project"] = {"detail": {"render_path": str(wav), "render_sha256": sha}}
    if tamper:
        wav.write_bytes(b"REPLACED-OVERNIGHT")
    return ctx


def test_real_render_verdict_binds_to_wav_hash(tmp_path):
    ctx = _rrv_ctx(tmp_path, "keep")
    status, detail = R.stage_real_render_verdict(ctx)
    assert status == R.PASSED and detail["wav_unchanged"] is True
    assert detail["verdict_bound_to_sha256"] == detail["current_render_sha256"]


def test_real_render_verdict_rejects_after_wav_replacement(tmp_path):
    ctx = _rrv_ctx(tmp_path, "keep", tamper=True)
    status, detail = R.stage_real_render_verdict(ctx)
    assert status == R.FAILED and detail["wav_unchanged"] is False and detail.get("verdict") is None


def test_real_render_verdict_pending_without_verdict(tmp_path):
    ctx = _rrv_ctx(tmp_path, None)
    assert R.stage_real_render_verdict(ctx)[0] == R.PENDING_MANUAL


# ---- item 3: run_id must be a bounded, path-safe token -------------------------
def test_validate_run_id_accepts_safe_token(tmp_path):
    d = R.validate_run_id("rig_abc123_deadbe", tmp_path)
    assert d == (tmp_path / "receipt" / "rig_abc123_deadbe").resolve()


def test_validate_run_id_rejects_traversal_and_separators(tmp_path):
    for bad in ("../escape", "a/b", "a\\b", "..", ".", "-rf", "x" * 129, "", "with space"):
        _raises(ValueError, R.validate_run_id, bad, tmp_path)


def test_validate_run_id_rejects_absolute_and_drive(tmp_path):
    for bad in ("/etc/passwd", "/abs", "C:\\Windows", "C:/Windows"):
        _raises(ValueError, R.validate_run_id, bad, tmp_path)


def test_run_refuses_unsafe_run_id_before_any_path(tmp_path):
    # run() must refuse a traversal run_id before creating anything under scratch
    scratch = tmp_path / "scratch"
    args = _Args(workspace=str(tmp_path / "ws"), scratch=str(scratch), run_id="../pwn")
    code = R.run(args)
    assert code == R.EXIT_FAILED
    assert not (scratch / "receipt").exists()


# ---- item 4: the external-vocal path never reaches receipt.json / receipt.md ---
def test_external_vocal_path_absent_from_committable_receipt(tmp_path):
    secret = "/home/owner/Music/COPYRIGHTED - Secret Vocal.wav"
    st = R.RunState.new(tmp_path / "run" / "state.json", "runV", "HEAD",
                        {"external_vocal": secret}, _meta())
    # simulate a subprocess command that captured the raw vocal path (as run_helper would)
    st.data["log_ledger"] = [{"key": "techno", "command": f"python helper.py /out {secret} 120 42",
                              "exit_code": 0, "log": "logs/techno.log", "log_sha256": "e" * 64}]
    st.stage("a")["status"] = R.PASSED
    st.stage("b")["status"] = R.PASSED
    st.stage("c")["status"] = R.SKIPPED
    st.data["stages"][0]["detail"] = {"external_vocal_basename": "COPYRIGHTED - Secret Vocal.wav"}
    st.save()
    run_dir = tmp_path / "run"
    R._write_receipts(st, tmp_path, run_dir, overall=R.INCOMPLETE, external_vocal=secret)
    j = (run_dir / "receipt.json").read_text(encoding="utf-8")
    md = (run_dir / "receipt.md").read_text(encoding="utf-8")
    # the full EXECUTION PATH (directory + file) must be gone from both artifacts
    assert secret not in j and secret not in md
    assert "/home/owner/Music" not in j and "/home/owner/Music" not in md
    assert R._EXTERNAL_VOCAL_PLACEHOLDER in j
    # a bare basename is allowed to remain for provenance; the placeholder replaces the path
    parsed = json.loads(j)
    assert parsed["external_vocal"]["basename"] == "COPYRIGHTED - Secret Vocal.wav"
    assert parsed["external_vocal"]["path"] == R._EXTERNAL_VOCAL_PLACEHOLDER


# ---- item 6: the spectral test covers a transform AFTER the first 32,768 samples
def test_spectral_ab_detects_difference_after_32768_samples(tmp_path):
    import numpy as np
    import soundfile as sf
    sr = 16000
    n = 80000               # > 32768 so the tail is beyond the old prefix window
    base = (0.2 * np.sin(2 * np.pi * 220.0 * np.arange(n) / sr)).astype(np.float32)
    a = base.copy()
    b = base.copy()
    # identical for the first 40,000 samples; a transformed clip appears AFTER that
    b[40000:] += (0.2 * np.sin(2 * np.pi * 660.0 * np.arange(n - 40000) / sr)).astype(np.float32)
    ap = tmp_path / "a.wav"; bp = tmp_path / "b.wav"
    sf.write(str(ap), a, sr); sf.write(str(bp), b, sr)
    out = tmp_path / "spec.json"
    # run the helper source exactly as the harness would (argv: out, a, b)
    import subprocess as _sp, sys as _sys
    helper = tmp_path / "spec_helper.py"; helper.write_text(R._SPECTRAL_AB_SRC, encoding="utf-8")
    _sp.run([_sys.executable, str(helper), str(out), str(ap), str(bp)], check=True)
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["ok"] and res["spectral_l1"] > 1e-4
    assert res["first_differing_chunk"] is not None
    assert res["first_differing_chunk"]["start_sample"] >= 32768   # detected in the tail, not the prefix


# ==========================================================================
# RELEASE-SAFETY PASS: read-only preflight, per-run isolation, explicit A/B
# baseline, and the execution report bound into the techno identity.
# ==========================================================================

# ---- 1. preflight config discovery is strictly read-only ----------------------
def test_preflight_config_discovery_creates_nothing_in_production(tmp_path):
    from test_projects import configured_core
    _ = configured_core(tmp_path)          # writes pointer + config.json + sqlite under tmp_path

    def snap(root):
        out = {}
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
        return out

    before = snap(tmp_path)
    cfg = R.read_workspace_config_readonly(tmp_path)   # the actual preflight discovery
    after = snap(tmp_path)
    # it found the real library WITHOUT constructing core / opening a writable DB
    assert cfg.get("master_root") and Path(cfg["master_root"]).name == "music"
    assert before == after, "read-only config discovery mutated the production workspace"
    new_files = set(after) - set(before)
    assert not new_files, f"preflight created files in production: {new_files}"
    assert not any(k.endswith(("-wal", "-shm", ".sqlite")) for k in new_files)


def test_readonly_config_returns_empty_on_missing_pointer(tmp_path):
    assert R.read_workspace_config_readonly(tmp_path / "nope") == {}
    # a pointer that names a missing config is not a valid discovery
    (tmp_path / "earcrate_workspace.json").write_text(json.dumps({"config_json": "/nope/config.json"}))
    assert R.read_workspace_config_readonly(tmp_path) == {}


def test_run_refuses_dirty_tree_before_probing_workspace(tmp_path):
    # the dirty refusal must fire BEFORE the workspace is read: prove the read-only
    # discovery is never even called when the tree is dirty.
    saved = {n: getattr(R, n) for n in ("git_head", "read_workspace_config_readonly", "_preflight_env")}
    probed = {"hit": False}

    def _probe(ws):
        probed["hit"] = True
        return {}

    R.git_head = lambda root: {"head": "h", "branch": "b", "upstream": "", "upstream_sha": "",
                               "dirty": True, "dirty_files": ["x"]}
    R.read_workspace_config_readonly = _probe
    R._preflight_env = lambda args: {}
    try:
        code = R.run(_Args(workspace=str(tmp_path / "ws"), scratch=str(tmp_path / "s"), run_id="r1"))
    finally:
        for n, f in saved.items():
            setattr(R, n, f)
    assert code == R.EXIT_FAILED
    assert probed["hit"] is False, "the production workspace was probed despite a dirty tree"


# ---- 2. two run IDs under one scratch are disjoint ----------------------------
def test_two_run_ids_have_disjoint_scratch_paths(tmp_path):
    def mk(rid):
        st = R.RunState.new(tmp_path / rid / "s.json", rid, "H", {}, _meta())
        return R.Ctx(_Args(run_id=rid), st, tmp_path, tmp_path / "logs")
    a = mk("rig_AAAA"); b = mk("rig_BBBB")
    assert a.run_root != b.run_root
    for part in ("ws", "ws_home", "acc_home", "workbench_dom"):
        assert a.run_dir(part) != b.run_dir(part)
        assert str(a.run_dir(part)).startswith(str(a.run_root))
    # a render/acceptance/cache under run A never lands under run B's root
    assert not str(a.run_root).startswith(str(b.run_root))
    assert not str(b.run_root).startswith(str(a.run_root))
    # resume of the SAME run_id keeps the SAME root
    assert mk("rig_AAAA").run_root == a.run_root


# ---- 3. the A/B transform baseline is pinned explicitly, never inherited ------
def test_ab_baseline_is_pinned_explicitly_not_inherited(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(), rb_default_ok=True, rb_rb_ok=True)
    status, _ = R.stage_rubberband_mechanical(ctx)
    assert status == R.PASSED
    assert ctx.sub_envs["rb_default"]["EARCRATE_TRANSFORM"] == "phase_vocoder"
    assert ctx.sub_envs["rb_rubberband"]["EARCRATE_TRANSFORM"] == "rubberband"


def test_parent_rubberband_cannot_contaminate_default_report(tmp_path):
    # A REAL render: the parent env starts with Rubber Band enabled, the harness pins
    # the default side to phase_vocoder, and the render RECEIPT still records
    # phase_vocoder. (item 3)
    core, pid = _renderable_project(tmp_path)
    del core
    inside = tmp_path / "work" / "renders" / "def_side.wav"
    result_json = tmp_path / "def_render.json"
    parent = dict(os.environ, EARCRATE_HOME=str(tmp_path), EARCRATE_TRANSFORM="rubberband")  # owner's shell
    child = dict(parent, EARCRATE_TRANSFORM="phase_vocoder")                                  # harness default side
    p = subprocess.run([sys.executable, "-m", "earcrate", "project", "render", pid,
                        "--dst", str(inside), "--json-out", str(result_json)],
                       cwd=str(_REPO_ROOT), env=child, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, f"render failed: {p.stderr[-800:]}"
    res = json.loads(result_json.read_text(encoding="utf-8"))
    report = json.loads(Path(res["report"]).read_text(encoding="utf-8"))
    assert report["transform_provider"] == "phase_vocoder", "parent EARCRATE_TRANSFORM contaminated the default side"


# ---- 4. the techno execution report is part of both identities ----------------
def test_techno_mechanical_fails_without_report_hash(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal)),
                   helper={"techno": _techno_result(tmp_path, report_path=None, report_sha256=None)})
    status, detail = R.stage_techno_mechanical(ctx)
    assert status == R.FAILED and detail["report_present_and_hashed"] is False


def test_techno_verdict_rejects_modified_report(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": _techno_result(tmp_path),
                           "techno_reidentity": {"result": {"external_pcm_sha256": "extpcmsha"}}})
    md = _seed_techno_mechanical(ctx, tmp_path)
    # the WAV is untouched but the execution report is edited overnight
    Path(md["report_path"]).write_text("TAMPERED REPORT", encoding="utf-8")
    status, detail = R.stage_techno_verdict(ctx)
    assert status == R.FAILED and detail["report_unchanged"] is False and detail.get("verdict") is None


def test_techno_verdict_rejects_missing_report(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": _techno_result(tmp_path),
                           "techno_reidentity": {"result": {"external_pcm_sha256": "extpcmsha"}}})
    md = _seed_techno_mechanical(ctx, tmp_path)
    Path(md["report_path"]).unlink()
    assert R.stage_techno_verdict(ctx)[0] == R.FAILED


# ==========================================================================
# FINAL VERIFICATION PASS: allin1 never falls back to production; the DOM
# harness is run-scoped and Windows-deterministic (bounded startup wait); real
# startup gates for both server forms; corrected path scoping.
# ==========================================================================

# ---- 1. allin1 without a scratch clone SKIPS and never touches production -----
def test_allin1_skips_without_scratch_and_leaves_production_untouched(tmp_path):
    from test_projects import configured_core
    core = configured_core(tmp_path)     # a REAL production workspace with a live sqlite
    core.conn().execute("SELECT 1").fetchone()   # ensure the DB file family exists
    del core

    def snap(root):
        return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sorted(Path(root).rglob("*")) if p.is_file()}

    before = snap(tmp_path)
    st = R.RunState.new(tmp_path / "rr" / "state.json", "rigX", "H", {}, R.STAGE_META)
    st.data["scratch_workspace"] = {"ok": False, "reason": "clone failed"}   # scratch NOT trusted
    args = _Args(workspace=str(tmp_path), scratch=str(tmp_path / "scr"), run_id="rigX")
    ctx = R.Ctx(args, st, tmp_path / "scr", tmp_path / "rr" / "logs")   # the REAL Ctx: a helper call would spawn a process
    status, detail = R.stage_allin1(ctx)
    assert status == R.SKIPPED and "production" in detail["reason"]
    assert st.data["log_ledger"] == [], "the allin1 helper was invoked despite no scratch clone"
    after = snap(tmp_path)
    # remove the harness's own state dir from the comparison; production must be identical
    prod_before = {k: v for k, v in before.items() if not k.startswith("rr/")}
    prod_after = {k: v for k, v in after.items() if not k.startswith(("rr/", "scr/"))}
    assert prod_before == prod_after, "production files changed (DB/WAL/SHM must be untouched)"


def test_allin1_stage_source_has_no_production_fallback():
    import inspect
    src = inspect.getsource(R.stage_allin1)
    assert "args.workspace" not in src, "stage_allin1 must never construct an env from --workspace"
    assert "SKIPPED" in src.split("run_helper")[0], "the no-clone SKIP must come before any helper call"


# ---- 2+3. DOM harness: run-scoped workspace, unbuffered, bounded startup ------
_WB_SRC = (_REPO_ROOT / "tests" / "manual" / "verify_workbench_dom.py").read_text(encoding="utf-8")


def test_workbench_harness_is_run_scoped_and_bounded():
    # workspace comes from WB_BASE_DIR when the rig harness supplies it
    assert 'os.environ.get("WB_BASE_DIR")' in _WB_SRC
    # the child server can never buffer its token line
    assert 'env["PYTHONUNBUFFERED"] = "1"' in _WB_SRC
    # bounded startup: a background reader + queue with per-get timeouts, never a
    # bare blocking readline loop
    assert "queue.Queue" in _WB_SRC and "threading.Thread" in _WB_SRC
    assert "lines.get(timeout=" in _WB_SRC
    boot_body = _WB_SRC.split("def boot(")[1].split("\ndef ")[0]
    assert "proc.stdout.readline()" not in boot_body, "boot still block-reads the pipe directly"
    # unconditional server cleanup survives
    assert "finally:" in _WB_SRC and "proc.kill()" in _WB_SRC


def test_workbench_stage_scopes_all_paths_under_run_root(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), sub={"workbench_dom": {"exit_code": 0}})
    shots = ctx.run_dir("workbench_dom")
    (shots / "receipt.json").write_text(json.dumps(
        {"modes": {"package": {"ok": True, "console_errors": []},
                   "singlefile": {"ok": True, "console_errors": []}}}), encoding="utf-8")
    _wav(shots / "package_desktop_timeline.png")
    status, detail = R.stage_workbench_dom(ctx)
    assert status == R.PASSED
    env = ctx.sub_envs["workbench_dom"]
    root = str(ctx.run_root)
    assert env["WB_SHOTS_DIR"].startswith(root), "screenshots/receipt escaped the run root"
    assert env["WB_BASE_DIR"].startswith(root), "browser workspace escaped the run root"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert str(shots).startswith(root)          # the receipt the stage reads is under the run root too


def _boot_server(cmd, port, home, timeout=45.0):
    """The same bounded startup mechanism the DOM harness uses: stdout piped,
    background reader, hard deadline. Returns (proc, url)."""
    import threading as _th, queue as _qu
    env = dict(os.environ, EARCRATE_HOME=str(home), PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd + ["--serve", "--no-browser", "--port", str(port)],
                            cwd=str(_REPO_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    q = _qu.Queue()

    def _pump(stream):
        try:
            for line in iter(stream.readline, ""):
                q.put(line)
        finally:
            q.put(None)

    _th.Thread(target=_pump, args=(proc.stdout,), daemon=True).start()
    url = None
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        try:
            line = q.get(timeout=1.0)
        except Exception:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        import re as _re
        m = _re.search(r"(http://127\.0\.0\.1:%d/\?token=[^\s]+)" % port, line)
        if m:
            url = m.group(1); break
    return proc, url


def _free_port():
    import socket as _so
    s = _so.socket(_so.AF_INET, _so.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


def _shutdown(proc):
    import signal as _sig, time as _t
    with __import__("contextlib").suppress(Exception):
        proc.send_signal(getattr(_sig, "SIGINT", _sig.SIGTERM))
    for _ in range(20):
        if proc.poll() is not None:
            return proc.returncode
        _t.sleep(0.5)
    proc.kill()
    proc.wait(timeout=10)
    return proc.returncode


def test_server_startup_package_mode_yields_token_and_terminates(tmp_path):
    port = _free_port()
    proc, url = _boot_server([sys.executable, "-m", "earcrate"], port, tmp_path / "home")
    try:
        assert url and f":{port}/?token=" in url, "package-mode server printed no token URL within the bound"
    finally:
        rc = _shutdown(proc)
    assert rc is not None, "package-mode server did not terminate cleanly"


def test_server_startup_singlefile_yields_token_and_terminates(tmp_path):
    dist = _REPO_ROOT / "dist" / "earcrate.py"
    if not dist.exists():   # deterministic build; VERIFY_PACKAGE does the same
        subprocess.run([sys.executable, str(_REPO_ROOT / "build" / "make_singlefile.py")],
                       cwd=str(_REPO_ROOT), check=True, capture_output=True, timeout=300)
    port = _free_port()
    proc, url = _boot_server([sys.executable, str(dist)], port, tmp_path / "home")
    try:
        assert url and f":{port}/?token=" in url, "single-file server printed no token URL within the bound"
    finally:
        rc = _shutdown(proc)
    assert rc is not None, "single-file server did not terminate cleanly"


# ---- the single-file bundle must contain every provider-seam module -----------
def test_singlefile_bundle_is_complete():
    # providers/transform+beats and ear/taste_ranker were once missing from the
    # bundler ORDER: package-mode gates stayed green while every single-file
    # preview/render NameError'd at call time (doctor swallowed it). Pin both the
    # rebuilt bundle's symbols and the bundler's refuse-to-build guard.
    dist = _REPO_ROOT / "dist" / "earcrate.py"
    if not dist.exists():
        subprocess.run([sys.executable, str(_REPO_ROOT / "build" / "make_singlefile.py")],
                       cwd=str(_REPO_ROOT), check=True, capture_output=True, timeout=300)
    src = dist.read_text(encoding="utf-8")
    for sym in ("def resolve_transform_provider", "def transform_capability",
                "def beat_capability", "def resolve_beat_provider",
                "def train_ranker", "def rank_pool",
                "TASTE_RANKER_FEATURES = FEATURES"):   # aliased import survives the strip
        assert sym in src, f"single-file bundle is missing: {sym}"
    builder = (_REPO_ROOT / "build" / "make_singlefile.py").read_text(encoding="utf-8")
    assert "missing from the single-file ORDER" in builder, "bundler lost its completeness guard"
