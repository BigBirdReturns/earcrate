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
    assert len(meta_keys) == 12
    required = {m["key"] for m in R.STAGE_META if m["required"]}
    assert {"gates", "verify_package", "workbench_dom", "acceptance", "real_project",
            "edit_undo_redo", "ranker", "piano"} <= required
    not_required = {m["key"] for m in R.STAGE_META if not m["required"]}
    assert {"allin1", "rubberband", "techno"} <= not_required


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
        self._sub = dict(sub or {})
        self._helper = dict(helper or {})
        self.calls = []
        self.state = _FakeState()
        self.state.data["scratch_workspace"] = ({"ok": True, "home": str(self.scratch / "home")}
                                                 if scratch_ok else {"ok": False, "reason": "no clone"})
        if project_id:
            self.state.data["real_project_id"] = project_id

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
    ctx = _FakeCtx(tmp_path, _Args(), sub={
        "real_compile": {"result": {"project_id": "p1", "revision_sha": "r1", "score_sha": "s1"}},
        "real_render": {"result": {"type": "render_project", "path": render_wav, "report": "rep.json",
                                   "revision_sha": "r2", "score_sha": "s1"}},
        "real_show": {"result": {"ok": True, "project": {"active_revision_sha": "r2"}}},
        "real_export": {"result": {"edl": "a.edl", "rpp": "b.rpp", "sheet": "c.json"}},
    })
    status, detail = R.stage_real_project(ctx)
    assert status == R.PASSED
    assert detail["project_id"] == "p1"               # read from the result FILE, not stdout
    assert detail["render_sha256"] and detail["export_edl"] == "a.edl"
    assert ctx.state.data["real_project_id"] == "p1"


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


# ---- item 7: a human verdict can NEVER convert a failed render into passed -----
def _rb_ctx(tmp_path, args, rb_default_ok, rb_rb_ok, resolved="rubberband"):
    out = tmp_path / "rubberband_ab"
    if rb_default_ok:
        _wav(out / "default.wav")
    if rb_rb_ok:
        _wav(out / "rubberband.wav")
    return _FakeCtx(tmp_path, args, project_id="p1",
                    sub={
                        "rb_default": {"result": {"type": "render_project", "report": "d.json"}
                                       if rb_default_ok else {"type": "render_rejected"}},
                        "rb_rubberband": {"result": {"type": "render_project", "report": "r.json"}
                                          if rb_rb_ok else {"type": "render_rejected"}},
                    },
                    helper={
                        "rubberband_probe": {"result": {"ready": True}},
                        "rb_provider": {"result": {"effective": resolved}},
                    })


def test_rubberband_verdict_cannot_pass_a_failed_render(tmp_path):
    # rubberband render FAILED (no artifact) but a keep-verdict is supplied
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"), rb_default_ok=True, rb_rb_ok=False)
    status, detail = R.stage_rubberband(ctx)
    assert status == R.FAILED, "a supplied verdict must not rescue a failed render"
    assert detail.get("verdict") is None


def test_rubberband_pending_without_verdict_when_mechanical_green(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband=None), rb_default_ok=True, rb_rb_ok=True)
    status, detail = R.stage_rubberband(ctx)
    assert status == R.PENDING_MANUAL and detail["provider_resolved_in_env"] == "rubberband"


def test_rubberband_verdict_completes_green_mechanical(tmp_path):
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"), rb_default_ok=True, rb_rb_ok=True)
    status, detail = R.stage_rubberband(ctx)
    assert status == R.PASSED and detail["verdict"] == "rubberband"
    assert detail["hashes_differ"] in (True, False)   # both hashes recorded


def test_rubberband_fails_when_provider_did_not_resolve(tmp_path):
    # both renders succeed, verdict supplied, but the env did NOT resolve to rubberband
    ctx = _rb_ctx(tmp_path, _Args(verdict_rubberband="rubberband"),
                  rb_default_ok=True, rb_rb_ok=True, resolved="default")
    status, detail = R.stage_rubberband(ctx)
    assert status == R.FAILED


def test_techno_verdict_cannot_pass_a_failed_render(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": {"result": {"render_ok": False, "external_vocal_in_registry": True,
                                                  "reason": "render failed"}}})
    status, detail = R.stage_techno(ctx)
    assert status == R.FAILED and detail.get("verdict") is None


def test_techno_fails_when_external_vocal_not_in_registry(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": {"result": {"render_ok": True, "external_vocal_in_registry": False}}})
    status, detail = R.stage_techno(ctx)
    assert status == R.FAILED


def test_techno_verdict_completes_green_mechanical(tmp_path):
    vocal = tmp_path / "vox.wav"; vocal.write_bytes(b"vox")
    ctx = _FakeCtx(tmp_path, _Args(external_vocal=str(vocal), verdict_techno="keep"),
                   helper={"techno": {"result": {"render_ok": True, "external_vocal_in_registry": True,
                                                 "project_id": "pt", "revision_sha": "rv",
                                                 "external_vocal_basename": "vox.wav"}}})
    status, detail = R.stage_techno(ctx)
    assert status == R.PASSED and detail["verdict"] == "keep"


# ---- item 4: allin1 decoder signature + backend; silent librosa fallback FAILS -
def test_allin1_helper_uses_real_decoder_signature_and_backend_guard():
    src = R._ALLIN1_SAMPLE_SRC
    assert "duration=seconds" in src, "allin1 must call decode_audio with duration=<seconds>"
    assert "decode_audio(Path(p), sr=sr, duration=seconds)" in src
    assert "max_seconds" not in src and "mono=True" not in src   # the OLD wrong signature is gone
    # a requested allin1 backend that silently falls back to librosa must be a FAILURE
    assert 'A.get("beat_backend") != "allin1"' in src and "silent fallback" in src
    assert 'os.environ["EARCRATE_BEATS"] = "allin1"' in src


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


def test_allin1_stage_passes_with_real_backend(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(), helper={
        "allin1": {"result": {"ok": True, "capability": {"ready": True}, "tracks_sampled": 3,
                              "mean_conf_delta": 0.1, "transition_feasibility_change": {"direction": "up"}}}})
    status, detail = R.stage_allin1(ctx)
    assert status == R.PASSED and detail["tracks_sampled"] == 3


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
def test_piano_fails_when_prior_attempts_not_preserved(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 3, "attempts": ["a", "b", "c"]}},
        # resume REWROTE the first attempts instead of preserving them
        "piano_2": {"result": {"complete": True, "attempted": 5, "attempts": ["x", "b", "c", "d", "e"]}},
    })
    status, detail = R.stage_piano(ctx)
    assert status == R.FAILED and detail["prior_attempts_preserved_verbatim"] is False


def test_piano_passes_when_resume_preserves_prior(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 3, "attempts": ["a", "b", "c"]}},
        "piano_2": {"result": {"complete": True, "attempted": 5, "attempts": ["a", "b", "c", "d", "e"]}},
    })
    status, detail = R.stage_piano(ctx)
    assert status == R.PASSED and detail["prior_attempts_preserved_verbatim"] is True


def test_piano_fails_when_run1_exceeds_cap(tmp_path):
    ctx = _FakeCtx(tmp_path, _Args(piano_iterations=3), sub={
        "piano_1": {"result": {"complete": True, "attempted": 9, "attempts": list("abcdefghi")}},
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
             ("resolve_workspace_config", "_preflight_env", "git_head", "execute_stages",
              "_prepare_scratch_workspace", "_write_receipts")}
    ran = {"stages": False}

    def _exec(*a, **k):
        ran["stages"] = True
        return R.COMPLETE

    R.resolve_workspace_config = lambda ctx: cfg
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
