"""Gates: the GPU work queue — the seam that turns the 4060 into a multi-tool.

The queue's POLICY is what these gates pin: content-addressed dedup (done once
is done forever), interactive lane strictly before warm lane, batch-by-kind
draining (one model resident per batch), honest per-kind capability probes
(a kind with no runner or missing deps says so), and the done-once skip via a
kind's `has` probe. Tenant #1 (the demucs stem-warmer) is proven separately by
tests/test_stem_warmer.py passing UNMODIFIED against the queue-backed warmer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.providers.workqueue import (
    GpuWorkQueue, register_kind, kind_capabilities, LANES, _WORK_KINDS,
)


def test_declared_kinds_and_honest_capability_report():
    caps = kind_capabilities()
    # The multi-tool contract: stems is live-declared; beats/embed/transcribe are
    # declared ahead of their runners so the box owner can see what an install
    # would unlock.
    assert {"stems", "beats", "embed", "transcribe"} <= set(caps)
    for name, cap in caps.items():
        assert "runner_registered" in cap and "description" in cap
        if not cap["runner_registered"]:
            # No runner -> NEVER ready, and the report says what is missing.
            assert cap.get("ready") is False
            assert any("runner" in str(m) for m in cap.get("missing", [])), name
    # stems probe is the real stem_capability shape (honest on a no-GPU box).
    assert {"torch", "demucs", "cuda", "ready"} <= set(caps["stems"])


def test_enqueue_dedup_is_content_addressed():
    register_kind("t_dedup", "test kind")
    q = GpuWorkQueue()
    a = q.enqueue("t_dedup", "pcmA", {"roles": ["vocals"]})
    dup = q.enqueue("t_dedup", "pcmA", {"roles": ["vocals"]})
    other = q.enqueue("t_dedup", "pcmA", {"roles": ["no_vocals"]})
    assert a is not None and dup is None and other is not None
    assert q.pending()["total"] == 2


def test_unknown_kind_and_lane_fail_loud():
    q = GpuWorkQueue()
    try:
        q.enqueue("never_registered", "x")
        assert False, "unknown kind must not enqueue silently"
    except KeyError:
        pass
    register_kind("t_lane", "test kind")
    try:
        q.enqueue("t_lane", "x", lane="bogus")
        assert False, "unknown lane must not enqueue silently"
    except ValueError:
        pass


def test_interactive_lane_beats_warm_and_batching_groups_by_kind():
    for k in ("t_kind_a", "t_kind_b"):
        register_kind(k, "test kind")
    q = GpuWorkQueue()
    # Interleave kinds and lanes deliberately.
    q.enqueue("t_kind_a", "w1", lane="warm")
    q.enqueue("t_kind_b", "w2", lane="warm")
    q.enqueue("t_kind_a", "w3", lane="warm")
    q.enqueue("t_kind_b", "i1", lane="interactive")
    q.enqueue("t_kind_a", "i2", lane="interactive")
    order = [(j["lane"], j["kind"], j["identity"]) for j in q.ordered_jobs()]
    # Interactive first; within each lane, all of one kind before the next
    # (kinds in first-enqueue order), preserving enqueue order inside a kind.
    assert order == [
        ("interactive", "t_kind_b", "i1"),
        ("interactive", "t_kind_a", "i2"),
        ("warm", "t_kind_a", "w1"),
        ("warm", "t_kind_a", "w3"),
        ("warm", "t_kind_b", "w2"),
    ], order


def test_drain_runs_runner_skips_done_and_records_receipts():
    ran = []
    done = {"pcmDone"}
    register_kind(
        "t_run", "test kind",
        runner=lambda job: ran.append(job["identity"]) or {"ok": True},
        has=lambda job: job["identity"] in done,
    )
    q = GpuWorkQueue()
    q.enqueue("t_run", "pcmDone")   # already materialized -> cached, no run
    q.enqueue("t_run", "pcmCold")
    res = q.drain()
    assert ran == ["pcmCold"], "done-once was violated: a warm job hit the runner"
    assert res["ran"] == 1 and res["cached"] == 1 and res["errors"] == 0
    statuses = {r["identity"]: r["status"] for r in res["receipts"]}
    assert statuses == {"pcmDone": "cached", "pcmCold": "done"}


def test_drain_surfaces_runner_errors_and_missing_runner():
    def _boom(job):
        raise RuntimeError("model exploded")
    register_kind("t_err", "test kind", runner=_boom)
    register_kind("t_norunner", "declared only")
    q = GpuWorkQueue()
    q.enqueue("t_err", "x")
    q.enqueue("t_norunner", "y")
    res = q.drain()
    assert res["errors"] == 2 and res["ran"] == 0
    details = {r["identity"]: r.get("detail", {}).get("error", "") for r in res["receipts"]}
    assert "model exploded" in details["x"]
    assert "no runner" in details["y"]


def test_drain_stop_reason_and_max_jobs():
    register_kind("t_stop", "test kind", runner=lambda job: {"ok": True})
    q = GpuWorkQueue()
    for n in range(3):
        q.enqueue("t_stop", f"p{n}")
    res = q.drain(max_jobs=1)
    assert res["ran"] == 1 and res["stopped_reason"] == "max_jobs reached"
    q2 = GpuWorkQueue()
    q2.enqueue("t_stop", "p0")
    res2 = q2.drain(should_continue=lambda job: "cache budget reached")
    assert res2["ran"] == 0 and res2["stopped_reason"] == "cache budget reached"


def test_warm_stems_draws_through_the_queue():
    """Tenant #1 is genuinely wired: the warmer enqueues into GpuWorkQueue and
    iterates the queue's policy order (its gates prove behavior is unchanged)."""
    import inspect
    from earcrate.app import EarcrateCore
    src = inspect.getsource(EarcrateCore.warm_stems)
    assert "GpuWorkQueue()" in src and "ordered_jobs()" in src, \
        "warm_stems no longer draws its work through the GPU work queue"


def _cleanup_test_kinds():
    for k in list(_WORK_KINDS):
        if k.startswith("t_"):
            _WORK_KINDS.pop(k, None)


def test_zz_cleanup_registry():
    # keep the module-level registry clean for any suite that imports after us
    _cleanup_test_kinds()
    assert not [k for k in _WORK_KINDS if k.startswith("t_")]
