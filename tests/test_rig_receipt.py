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
