"""Gates: the GPU work queue -- the seam that turns the box's one GPU from a
demucs-only tool into a multi-tenant accelerator (kind registry, content-
addressed cache-hits, batch-by-kind draining, interactive/warm lanes,
inspectable snapshot). Pure Python, fake runners/providers -- no torch, no
network, no CUDA needed.

``warm_stems`` equivalence (the refactor riding this queue with byte-identical
external behavior) is proven by tests/test_stem_warmer.py passing UNMODIFIED;
this file does not duplicate that gate.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.app import EarcrateCore
from earcrate.providers import get
from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.gpu_queue import GpuWorkQueue, job_artifact_key


# --------------------------------------------------------------------- #
# ArtifactStore.has() / .bin_path() -- presence without a full body read #
# --------------------------------------------------------------------- #

def test_artifact_store_has_is_a_presence_check_without_reading_bytes(tmp_path):
    store = ArtifactStore(tmp_path / "L3")
    assert store.has("k1") is False
    store.put("k1", b"payload bytes", tier="warm", provider="x", version="1")
    assert store.has("k1") is True
    # bin_path() names the on-disk blob directly, present or not.
    p = store.bin_path("k1")
    assert p.exists() and p.read_bytes() == b"payload bytes"
    assert store.bin_path("never-put").exists() is False


# --------------------------------------------------------------------- #
# Kind registration + honest unavailable-kind refusal                    #
# --------------------------------------------------------------------- #

def test_declared_kinds_refuse_enqueue_with_an_honest_reason_never_raising():
    q = GpuWorkQueue()
    for kind in ("beats", "embed", "transcribe"):
        cap = q.capability(kind)
        assert cap["available"] is False
        assert "not installed" in cap["reason"]
        res = q.enqueue(kind, "pcmX")
        assert res["ok"] is False and res["refused"] is True
        assert "not installed" in res["reason"]
    snap = q.snapshot()
    assert snap["queued_total"] == 0          # nothing was ever queued
    assert snap["refused"] == 3               # counted, not silently dropped


def test_unknown_kind_refuses_instead_of_raising():
    q = GpuWorkQueue()
    res = q.enqueue("does_not_exist", "pcmX")
    assert res["ok"] is False and res["refused"] is True
    assert "unknown job kind" in res["reason"]


def test_register_kind_wires_a_real_runner_and_makes_it_available(tmp_path):
    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    calls = []

    def runner(job):
        calls.append(job["pcm_sha"])
        return {"available": True}

    q.register_kind("beats", runner=runner)  # default probe: always-available once a runner exists
    assert q.capability("beats")["available"] is True
    res = q.enqueue("beats", "pcmX", lane="warm")
    assert res["ok"] is True
    out = q.drain()
    assert out["completed"] == 1 and calls == ["pcmX"]


def test_declared_only_probe_claiming_available_without_a_runner_is_refused():
    """A capability probe that (by mistake) reports available=True while no
    runner is registered must not be trusted -- the queue refuses rather than
    later crashing on a None runner in drain()."""
    q = GpuWorkQueue()
    q.register_kind("ghost", runner=None, capability_probe=lambda: {"available": True})
    cap = q.capability("ghost")
    assert cap["available"] is False
    assert "no registered runner" in cap["reason"]


# --------------------------------------------------------------------- #
# Content-addressed outputs: a completed job is a cache-hit forever      #
# --------------------------------------------------------------------- #

def test_cached_artifact_completes_instantly_without_running_the_job(tmp_path):
    store = ArtifactStore(tmp_path / "L3")
    q = GpuWorkQueue(store=store)
    calls = []

    def runner(job):
        calls.append(job["pcm_sha"])
        return {"data": b"beat grid bytes", "tier": "warm"}

    q.register_kind("beats", runner=runner)
    key = job_artifact_key("beats", "", "", "pcmX")
    # Pre-seed the artifact as if a prior job already produced it.
    store.put(key, b"already banked", tier="warm", provider="beats", version="")
    q.enqueue("beats", "pcmX")
    out = q.drain()
    assert out["cached"] == 1 and out["completed"] == 0
    assert out["jobs"][0]["status"] == "cached"
    assert calls == [], "a cached job must never invoke the runner"
    assert q.snapshot()["cached_hits"] == 1


def test_generic_runner_output_is_banked_under_the_job_content_address(tmp_path):
    """A tenant that returns {\"data\": bytes} gets it banked into the
    ArtifactStore under the job's (kind, model_id, model_version, pcm_sha)
    content address -- so the SAME identity is a cache hit next time."""
    store = ArtifactStore(tmp_path / "L3")
    q = GpuWorkQueue(store=store)
    runs = {"n": 0}

    def runner(job):
        runs["n"] += 1
        return {"data": b"embedding vector bytes", "tier": "warm"}

    q.register_kind("embed", runner=runner)
    q.enqueue("embed", "pcmX", model_id="clap", model_version="v1")
    first = q.drain()
    assert first["completed"] == 1 and runs["n"] == 1
    key = job_artifact_key("embed", "clap", "v1", "pcmX")
    assert store.has(key) and store.get(key)["data"] == b"embedding vector bytes"

    # Enqueue the SAME identity again: must be an instant cache hit, no 2nd run.
    q.enqueue("embed", "pcmX", model_id="clap", model_version="v1")
    second = q.drain()
    assert second["cached"] == 1 and second["completed"] == 0 and runs["n"] == 1


# --------------------------------------------------------------------- #
# Batch-by-kind draining (the VRAM policy)                               #
# --------------------------------------------------------------------- #

def test_drain_batches_one_kind_fully_before_switching(tmp_path):
    """Interleaved enqueue order (A, B, A, B, A) must still execute as
    contiguous per-kind batches: draining one kind fully before the next
    amortizes a model load across the whole batch instead of reloading it
    between every job."""
    order = []

    def make_runner(name):
        def runner(job):
            order.append((name, job["pcm_sha"]))
            return {"available": True}
        return runner

    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    q.register_kind("beats", runner=make_runner("beats"))
    q.register_kind("embed", runner=make_runner("embed"))
    for pcm in ("p1", "p2", "p3"):
        q.enqueue("beats", pcm)
        q.enqueue("embed", pcm)
    out = q.drain()
    assert out["completed"] == 6
    kinds_in_order = [k for k, _ in order]
    # Group consecutive identical kinds; batching means at most 2 groups total
    # (one full "beats" run, one full "embed" run) -- NOT 6 alternating groups.
    groups = 1
    for i in range(1, len(kinds_in_order)):
        if kinds_in_order[i] != kinds_in_order[i - 1]:
            groups += 1
    assert groups == 2, f"expected one contiguous batch per kind, got order {kinds_in_order}"


def test_drain_kinds_filter_only_touches_selected_kinds(tmp_path):
    order = []

    def make_runner(name):
        def runner(job):
            order.append(name)
            return {"available": True}
        return runner

    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    q.register_kind("beats", runner=make_runner("beats"))
    q.register_kind("embed", runner=make_runner("embed"))
    q.enqueue("beats", "p1")
    q.enqueue("embed", "p1")
    out = q.drain(kinds=("beats",))
    assert order == ["beats"]
    assert out["remaining"] == 0          # no more BEATS jobs queued
    snap = q.snapshot()
    assert snap["queued"]["embed"]["warm"] == 1   # embed job untouched


# --------------------------------------------------------------------- #
# Two priority lanes: interactive drains first, checked between jobs     #
# --------------------------------------------------------------------- #

def test_interactive_lane_runs_before_warm_even_when_enqueued_after(tmp_path):
    order = []

    def runner(job):
        order.append((job["lane"], job["pcm_sha"]))
        return {"available": True}

    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    q.register_kind("beats", runner=runner)
    q.enqueue("beats", "warm-1", lane="warm")
    q.enqueue("beats", "warm-2", lane="warm")
    q.enqueue("beats", "urgent", lane="interactive")
    out = q.drain()
    assert out["completed"] == 3
    assert order[0] == ("interactive", "urgent"), "interactive must be drained first"


def test_interactive_lane_cuts_in_mid_batch_between_jobs(tmp_path):
    """An interactive job enqueued WHILE a warm batch is draining must jump
    ahead of the rest of that batch -- checked between jobs, not preemptively
    (no job is interrupted mid-run)."""
    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    order = []

    def runner(job):
        order.append((job["lane"], job["pcm_sha"]))
        if job["pcm_sha"] == "warm-1":
            # Simulate a caller noticing new interactive work arrived while
            # this batch is draining, by enqueueing before the loop asks for
            # the next job.
            q.enqueue("beats", "urgent", lane="interactive")
        return {"available": True}

    q.register_kind("beats", runner=runner)
    q.enqueue("beats", "warm-1", lane="warm")
    q.enqueue("beats", "warm-2", lane="warm")
    out = q.drain()
    assert out["completed"] == 3
    assert [pcm for _, pcm in order] == ["warm-1", "urgent", "warm-2"]


# --------------------------------------------------------------------- #
# max_jobs / budget_s stop conditions                                    #
# --------------------------------------------------------------------- #

def test_drain_respects_max_jobs(tmp_path):
    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    q.register_kind("beats", runner=lambda job: {"available": True})
    for pcm in ("p1", "p2", "p3"):
        q.enqueue("beats", pcm)
    out = q.drain(max_jobs=2)
    assert out["completed"] == 2 and out["stopped_reason"] == "max_jobs reached"
    assert out["remaining"] == 1


def test_drain_reports_runner_errors_without_raising(tmp_path):
    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))

    def flaky(job):
        if job["pcm_sha"] == "bad":
            raise RuntimeError("boom")
        return {"available": True}

    q.register_kind("beats", runner=flaky)
    q.enqueue("beats", "good")
    q.enqueue("beats", "bad")
    out = q.drain()
    assert out["completed"] == 1
    assert len(out["errors"]) == 1 and "boom" in out["errors"][0]["error"]


# --------------------------------------------------------------------- #
# Snapshot shape                                                         #
# --------------------------------------------------------------------- #

def test_snapshot_shape_is_plain_inspectable_data(tmp_path):
    q = GpuWorkQueue(store=ArtifactStore(tmp_path / "L3"))
    q.register_kind("beats", runner=lambda job: {"available": True})
    q.enqueue("beats", "p1", lane="warm")
    q.enqueue("beats", "p2", lane="interactive")
    snap = q.snapshot()
    for key in ("queued", "queued_total", "running", "done", "errors",
                "refused", "cached_hits", "kinds"):
        assert key in snap
    assert snap["queued"]["beats"] == {"interactive": 1, "warm": 1}
    assert snap["queued_total"] == 2
    assert snap["running"] is None
    assert set(("separate", "beats", "embed", "transcribe")) <= set(snap["kinds"])
    assert snap["kinds"]["beats"]["runner_registered"] is True
    assert snap["kinds"]["embed"]["runner_registered"] is False
    q.drain()
    snap2 = q.snapshot()
    assert snap2["done"] == 2 and snap2["queued_total"] == 0


# --------------------------------------------------------------------- #
# The default (module-registered) queue and the "separate" tenant seam   #
# --------------------------------------------------------------------- #

def test_default_registry_queue_is_honest_with_no_resolver_wired():
    q = get("gpu_queue")
    cap = q.capability("separate")
    assert cap["available"] is False
    assert "resolver" in cap["reason"]


def _core(tmp_path):
    master = tmp_path / "music"; work = tmp_path / "work"; agent = tmp_path / "agent"
    for d in (master, work, agent):
        d.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        c = EarcrateCore()
        c.configure({"master_root": str(master), "working_root": str(work),
                     "agent_root": str(agent), "workers": 1})
    return c


def _isolated_l3(tmp_path):
    return patch.dict(os.environ, {"EARCRATE_L3_ROOT": str(tmp_path / "L3")})


class _FakeGPU:
    name = "fakegpu"

    def __init__(self, store, stem_bytes=1000):
        self.store = store
        self.stem_bytes = stem_bytes
        self.warm = set()
        self.separations = 0

    def has_stems(self, pcm, roles=None):
        rs = list(roles) if roles else ["vocals", "no_vocals"]
        return all((str(pcm), r) in self.warm for r in rs)

    def separate(self, pcm, path, roles=None):
        rs = list(roles) if roles else ["vocals", "no_vocals"]
        self.separations += 1
        stems = {}
        for r in rs:
            key = f"fake_{pcm}_{r}"
            self.store.put(key, b"0" * self.stem_bytes, tier="warm", provider="fakegpu", version="v")
            self.warm.add((str(pcm), r))
            stems[r] = key
        return {"available": True, "provider": "fakegpu", "pcm_sha": str(pcm), "cached": False, "stems": stems}


def test_core_gpu_queue_status_reflects_the_separate_tenant():
    core = EarcrateCore.__new__(EarcrateCore)  # avoid full configure(); only need _gpu_queue()
    core._gpu_work_queue = None
    core._resolve_stem_provider = lambda: (None, "noop")
    snap = core.gpu_queue_status()
    assert "separate" in snap["kinds"]
    assert snap["kinds"]["separate"]["runner_registered"] is True
    # noop provider on this box -> honestly unavailable, not a crash.
    assert snap["kinds"]["separate"]["capability"]["available"] is False


def test_warm_stems_genuinely_drives_the_gpu_queue_not_a_parallel_path(tmp_path):
    """warm_stems (tenant #1) must ACTUALLY route through GpuWorkQueue.drain --
    proven here by inspecting the queue's own lifetime counters after a run,
    not just warm_stems' own return dict (that equivalence is covered by the
    unmodified test_stem_warmer suite)."""
    core = _core(tmp_path)
    db = core.conn()
    p = tmp_path / "music" / "f1.wav"
    p.write_bytes(b"RIFF0000WAVEfake")
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at,present) VALUES(?,?,?,?,?,?,?)",
               ("f1", str(p.resolve()), "master", 1, 1, "now", 1))
    core._set_pcm("f1", "pcm1")
    db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
               ("l1", "f1", 0, 4, 2, "vocal", 0.9, "now"))
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,status,metrics_json,created_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("a1", "l1", "f1", "girl_talk_v1", "VOX_HOOK", "vocal", 0, 4, 2, 0.9, "approved", "{}", "now"))
    db.commit()
    with _isolated_l3(tmp_path):
        fake = _FakeGPU(get("artifacts"))
        with patch("earcrate.app.stem_capability", return_value={"ready": True}), \
             patch.object(core, "_resolve_stem_provider", return_value=(fake, "fakegpu")):
            res = core.warm_stems("girl_talk_v1")
            snap = core.gpu_queue_status()
    assert res["available"] is True and res["separated"] == 1
    assert snap["done"] == 1, "the queue's own lifetime counter must reflect the run"
    assert snap["queued_total"] == 0
