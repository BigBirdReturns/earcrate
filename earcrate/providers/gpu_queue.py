from earcrate.core.deps import *
from earcrate.core.util import sha256_text
from earcrate.providers import register, get
from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.stems import stem_capability
"""EARCRATE v3 — the GPU work queue: the seam that turns the box's one GPU from
a demucs-only tool into a multi-tenant accelerator.

Job KINDS are registered tenants (``register_kind``): ``separate`` ships fully
wired (its runner delegates to the resolved StemProvider, exactly the work the
background stem warmer did directly before this seam existed); ``beats``,
``embed`` and ``transcribe`` ship DECLARED — their default capability probe
honestly reports the model is not installed, and enqueueing an unavailable kind
returns a refusal dict (never raises, never queues). A box that installs a
tenant re-registers the kind with a real runner + probe.

Every job is content-addressed by ``(kind, model_id, model_version, pcm_sha)``
and its output goes through the L3 ArtifactStore, so a completed job is a
cache-hit forever: a job whose artifact already exists completes instantly as
``"cached"`` without touching the GPU. (The ``separate`` kind's cache predicate
is the provider's own recipe-aware ``has_stems``.)

VRAM POLICY (8GB-card story): the drain loop batches BY KIND — it drains one
kind fully before switching to the next, so at most one model family is
resident at a time and its load cost is amortized across the whole batch.
There is no fancy VRAM accounting, just serial tenancy. Two priority lanes
exist, ``"interactive"`` and ``"warm"``: between jobs (never preemptively,
mid-inference) the drain loop checks the interactive lane first, so a user
waiting on a result jumps ahead of background warming even mid-batch.

Deterministic + inspectable: queue state is plain data (``snapshot()``), and
this module starts NO threads — the caller drives ``drain(max_jobs=, budget_s=)``
from whatever thread it owns (app.py's ``run_background``, same as the stem
warmer always ran)."""

LANES = ("interactive", "warm")

# Declared-but-not-installed tenants: kind -> the model a box would install.
DECLARED_TENANTS = {
    "beats": "beat-tracking model",
    "embed": "audio-embedding model",
    "transcribe": "transcription model",
}


def job_artifact_key(kind: str, model_id: str, model_version: str, pcm_sha: str) -> str:
    """Content address of a job's banked output: same sound + same kind + same
    model identity -> same artifact, forever."""
    return "gpu_" + sha256_text("|".join(
        [str(kind), str(model_id), str(model_version), str(pcm_sha)]))


def declared_unavailable_probe(kind: str, model_desc: str):
    """Default capability probe for a DECLARED tenant: honest, actionable, and
    refusal-shaped — the kind exists as a seam, the model does not exist on this
    box yet."""
    reason = ("%s not installed; box installs the tenant and registers a runner "
              "(register_kind(%r, runner=..., capability_probe=...))"
              % (model_desc, str(kind)))

    def probe() -> Dict[str, Any]:
        return {"available": False, "reason": reason}
    return probe


class GpuWorkQueue:
    """Multi-tenant GPU work queue. PURE of app.py: takes an ArtifactStore (or a
    zero-arg factory returning one, so late env like EARCRATE_L3_ROOT is
    honored) plus provider lookups as constructor args. See module docstring
    for the batching/lane/VRAM policy."""

    def __init__(self, store: Any = None, stem_provider_resolver: Any = None,
                 stem_capability_probe: Any = None):
        self._store_arg = store
        self._lock = threading.Lock()
        self._kinds: Dict[str, Dict[str, Any]] = {}
        self._kind_order: List[str] = []
        self._queued: List[Dict[str, Any]] = []
        self._seq = 0
        self._running: Optional[Dict[str, Any]] = None
        self._done = 0            # successful runs
        self._error_count = 0     # failed runs
        self._refused = 0         # enqueues rejected by capability
        self._cached_hits = 0     # jobs completed instantly from the cache
        if stem_provider_resolver is not None:
            self.wire_stem_provider(stem_provider_resolver, stem_capability_probe)
        else:
            def _unwired() -> Dict[str, Any]:
                return {"available": False,
                        "reason": "no stem-provider resolver wired into this queue; "
                                  "construct GpuWorkQueue(stem_provider_resolver=...) or "
                                  "call wire_stem_provider(resolver)"}
            self.register_kind("separate", runner=None, capability_probe=_unwired,
                               artifact_kind="stems")
        for name, desc in DECLARED_TENANTS.items():
            self.register_kind(name, runner=None,
                               capability_probe=declared_unavailable_probe(name, desc),
                               artifact_kind=name)

    # ------------------------------------------------------------------ #
    # store / kinds                                                       #
    # ------------------------------------------------------------------ #
    def _store(self) -> ArtifactStore:
        """Resolve the L3 artifact store: an explicit instance/factory passed to
        the constructor wins; with neither, reach through the registered
        ``artifacts`` seam (``get("artifacts")``) rather than constructing
        ``ArtifactStore()`` directly, so a future re-registered default store
        implementation is honored here too."""
        s = self._store_arg
        if callable(s):
            return s()
        return s if s is not None else get("artifacts")

    def register_kind(self, name: str, runner: Any = None, capability_probe: Any = None,
                      artifact_kind: str = "", cached_probe: Any = None) -> Dict[str, Any]:
        """Register (or replace) a job kind. ``runner(job) -> dict`` does the
        work; a runner may bank its own artifacts (``separate`` does) or return
        ``{"data": bytes}`` for the queue to bank under the job's content
        address. ``capability_probe() -> {"available": bool, "reason": str}``
        gates enqueue; omitted with a runner it defaults to always-available,
        omitted without one it defaults to an honest not-installed refusal.
        ``cached_probe(job) -> bool`` overrides the default artifact-exists
        cache predicate."""
        name = str(name)
        if capability_probe is None:
            if runner is None:
                capability_probe = declared_unavailable_probe(
                    name, DECLARED_TENANTS.get(name, name + " model"))
            else:
                def capability_probe() -> Dict[str, Any]:
                    return {"available": True}
        with self._lock:
            if name not in self._kinds:
                self._kind_order.append(name)
            self._kinds[name] = {"runner": runner, "probe": capability_probe,
                                 "artifact_kind": str(artifact_kind), "cached": cached_probe}
        return {"ok": True, "kind": name, "declared_only": runner is None}

    def wire_stem_provider(self, resolver: Any, cap_probe: Any = None) -> None:
        """PUBLIC: wire (or re-wire) the ``separate`` tenant onto a StemProvider
        source. ``resolver() -> (provider, name)`` and ``cap_probe() ->
        {..., "ready": bool}`` are called on EVERY probe/enqueue/run (never
        captured once), so a caller can pass closures over its own mutable
        state (env, config, test patches) and this queue always sees the
        current answer. The runner delegates to the RESOLVED StemProvider
        exactly as the pre-queue stem warmer did; the cache predicate is the
        provider's recipe-aware ``has_stems``."""
        cap_probe = cap_probe or stem_capability

        def probe() -> Dict[str, Any]:
            prov, name = resolver()
            cap = cap_probe()
            if name == "noop" or not cap.get("ready"):
                return {"available": False, "provider": name, "capability": cap,
                        "reason": "no ready GPU stem provider on this box; nothing to "
                                  "separate (default NoopStemProvider)"}
            return {"available": True, "provider": name, "capability": cap}

        def cached(job: Dict[str, Any]) -> bool:
            prov, _ = resolver()
            roles = list(job["payload"].get("roles") or ("vocals", "no_vocals"))
            return bool(prov.has_stems(str(job["pcm_sha"]), roles))

        def runner(job: Dict[str, Any]) -> Dict[str, Any]:
            path = str(job["payload"].get("path") or "")
            if not os.path.exists(path):
                return {"available": False, "reason": "source file missing"}
            prov, _ = resolver()
            roles = list(job["payload"].get("roles") or ("vocals", "no_vocals"))
            sep = prov.separate(str(job["pcm_sha"]), path, roles)
            if sep and sep.get("available"):
                return sep
            return {"available": False,
                    "reason": str((sep or {}).get("reason") or "provider produced no stems")}

        self.register_kind("separate", runner=runner, capability_probe=probe,
                           cached_probe=cached, artifact_kind="stems")

    def capability(self, kind: str) -> Dict[str, Any]:
        """HONEST capability of a kind: never raises, never claims readiness it
        cannot prove; unknown kinds and declared-only kinds report unavailable."""
        spec = self._kinds.get(str(kind))
        if spec is None:
            return {"available": False,
                    "reason": "unknown job kind %r; registered kinds: %s"
                              % (str(kind), ", ".join(sorted(self._kinds)) or "(none)")}
        try:
            cap = spec["probe"]() or {}
        except Exception as exc:
            return {"available": False, "reason": "capability probe error: %s" % (str(exc)[:200],)}
        if not isinstance(cap, dict):
            cap = {"available": bool(cap)}
        cap.setdefault("available", False)
        if spec["runner"] is None and cap.get("available"):
            return {"available": False,
                    "reason": "kind %r is declared but has no registered runner" % (str(kind),)}
        return cap

    # ------------------------------------------------------------------ #
    # enqueue / refusal                                                   #
    # ------------------------------------------------------------------ #
    def refusal(self, kind: str) -> Dict[str, Any]:
        """Record and return an honest refusal for ``kind`` (used both by
        enqueue and by callers that gate before building a job list)."""
        cap = self.capability(kind)
        with self._lock:
            self._refused += 1
        return {"ok": False, "refused": True, "kind": str(kind),
                "reason": str(cap.get("reason") or "kind unavailable"), "capability": cap}

    def enqueue(self, kind: str, pcm_sha: str, model_id: str = "", model_version: str = "",
                payload: Optional[Dict[str, Any]] = None, lane: str = "warm") -> Dict[str, Any]:
        """Queue one job. An unavailable kind returns a refusal dict — never
        raises, never queues. Duplicate identities are NOT collapsed: content
        addressing makes a duplicate complete instantly as ``cached`` at drain
        time, which keeps counts honest for callers that expect one outcome per
        submitted item."""
        kind = str(kind)
        lane = lane if lane in LANES else "warm"
        cap = self.capability(kind)
        if not cap.get("available"):
            return self.refusal(kind)
        key = job_artifact_key(kind, model_id, model_version, pcm_sha)
        with self._lock:
            self._seq += 1
            job = {"id": "job_%06d" % self._seq, "seq": self._seq, "kind": kind, "lane": lane,
                   "model_id": str(model_id), "model_version": str(model_version),
                   "pcm_sha": str(pcm_sha), "payload": dict(payload or {}), "artifact_key": key}
            self._queued.append(job)
        return {"ok": True, "queued": True, "job_id": job["id"], "kind": kind,
                "lane": lane, "artifact_key": key}

    def cancel_pending(self, kind: Optional[str] = None, lane: Optional[str] = None) -> int:
        """Drop matching queued (not yet run) jobs; returns how many were dropped."""
        with self._lock:
            keep: List[Dict[str, Any]] = []
            dropped = 0
            for j in self._queued:
                if (kind is None or j["kind"] == kind) and (lane is None or j["lane"] == lane):
                    dropped += 1
                else:
                    keep.append(j)
            self._queued = keep
        return dropped

    # ------------------------------------------------------------------ #
    # drain                                                               #
    # ------------------------------------------------------------------ #
    def _job_cached(self, job: Dict[str, Any]) -> bool:
        spec = self._kinds.get(job["kind"]) or {}
        try:
            probe = spec.get("cached")
            if probe is not None:
                return bool(probe(job))
            store = self._store()
            has = getattr(store, "has", None)
            if callable(has):
                return bool(has(job["artifact_key"]))
            return store.get(job["artifact_key"]) is not None
        except Exception:
            return False

    def _next_job(self, current_kind: Optional[str],
                  kinds: Optional[Any]) -> Optional[Dict[str, Any]]:
        """PEEK the next job: interactive lane strictly first; within a lane,
        keep batching the current kind while it has jobs, else the kind of the
        oldest queued job (FIFO). Deterministic — pure enqueue order."""
        with self._lock:
            for lane in LANES:
                lane_jobs = [j for j in self._queued
                             if j["lane"] == lane and (kinds is None or j["kind"] in kinds)]
                if not lane_jobs:
                    continue
                if current_kind is not None:
                    same = [j for j in lane_jobs if j["kind"] == current_kind]
                    if same:
                        return same[0]
                return lane_jobs[0]
        return None

    def _pop(self, job: Dict[str, Any]) -> None:
        with self._lock:
            self._queued = [j for j in self._queued if j["id"] != job["id"]]

    def drain(self, max_jobs: int = 0, budget_s: float = 0.0, kinds: Optional[Any] = None,
              stop: Any = None, stop_reason: str = "stopped",
              progress: Any = None) -> Dict[str, Any]:
        """Run queued jobs until the queue (of the selected ``kinds``) drains,
        ``max_jobs`` SUCCESSFUL jobs have completed, the wall-clock ``budget_s``
        elapses, or the caller's ``stop()`` callable says to stop (checked
        between jobs, before each RUN; cached completions are free and always
        allowed). Returns a per-drain result dict; unfinished jobs stay queued."""
        t0 = time.monotonic()
        kind_filter = tuple(str(k) for k in kinds) if kinds else None
        res: Dict[str, Any] = {"ok": True, "ran": 0, "completed": 0, "cached": 0,
                               "errors": [], "jobs": [], "stopped_reason": "drained"}
        current_kind: Optional[str] = None
        while True:
            job = self._next_job(current_kind, kind_filter)
            if job is None:
                res["stopped_reason"] = "drained"
                break
            if max_jobs and res["completed"] >= max_jobs:
                res["stopped_reason"] = "max_jobs reached"
                break
            current_kind = job["kind"]
            if self._job_cached(job):
                self._pop(job)
                with self._lock:
                    self._cached_hits += 1
                res["cached"] += 1
                res["jobs"].append({"id": job["id"], "kind": job["kind"],
                                    "pcm_sha": job["pcm_sha"], "lane": job["lane"],
                                    "status": "cached"})
                continue
            if budget_s and (time.monotonic() - t0) >= budget_s:
                res["stopped_reason"] = "time budget reached"
                break
            if stop is not None:
                try:
                    if stop():
                        res["stopped_reason"] = str(stop_reason)
                        break
                except Exception:
                    pass
            self._pop(job)
            with self._lock:
                self._running = {"id": job["id"], "kind": job["kind"],
                                 "pcm_sha": job["pcm_sha"], "lane": job["lane"]}
            status, err = "done", None
            runner = (self._kinds.get(job["kind"]) or {}).get("runner")
            try:
                out = runner(job) if runner is not None else None
                ok = bool(out) and (not isinstance(out, dict) or bool(out.get("available", True)))
                if not ok:
                    status = "error"
                    err = str((out or {}).get("reason") or "runner produced no result") \
                        if isinstance(out, dict) else "runner produced no result"
                elif isinstance(out, dict) and isinstance(out.get("data"), (bytes, bytearray)):
                    # Generic tenants: bank the output under the job's content
                    # address with provenance, so this job is a cache-hit forever.
                    self._store().put(
                        job["artifact_key"], bytes(out["data"]),
                        tier=str(out.get("tier") or "warm"),
                        source_identity=job["pcm_sha"],
                        provider=job["model_id"] or job["kind"],
                        version=job["model_version"], extra={"kind": job["kind"]})
            except Exception as exc:
                status, err = "error", str(exc)[:200]
            with self._lock:
                self._running = None
                if status == "done":
                    self._done += 1
                else:
                    self._error_count += 1
            res["ran"] += 1
            if status == "done":
                res["completed"] += 1
            else:
                res["errors"].append({"job_id": job["id"], "kind": job["kind"],
                                      "pcm_sha": job["pcm_sha"], "error": err})
            res["jobs"].append({"id": job["id"], "kind": job["kind"],
                                "pcm_sha": job["pcm_sha"], "lane": job["lane"], "status": status})
            if progress is not None:
                try:
                    progress({"processed": len(res["jobs"]), "ran": res["ran"],
                              "ok": res["completed"], "cached": res["cached"],
                              "errors": len(res["errors"])})
                except Exception:
                    pass
        with self._lock:
            res["remaining"] = len([j for j in self._queued
                                    if kind_filter is None or j["kind"] in kind_filter])
        return res

    # ------------------------------------------------------------------ #
    # inspection                                                          #
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        """Plain-dict queue state: queued counts by kind/lane, the running job,
        lifetime done/errors/refused/cached_hits counters, and each registered
        kind's honest capability."""
        with self._lock:
            queued: Dict[str, Dict[str, int]] = {}
            for j in self._queued:
                by_lane = queued.setdefault(j["kind"], {ln: 0 for ln in LANES})
                by_lane[j["lane"]] += 1
            out: Dict[str, Any] = {
                "queued": queued, "queued_total": len(self._queued),
                "running": dict(self._running) if self._running else None,
                "done": self._done, "errors": self._error_count,
                "refused": self._refused, "cached_hits": self._cached_hits,
            }
            kind_names = list(self._kind_order)
        kinds: Dict[str, Any] = {}
        for name in kind_names:
            spec = self._kinds.get(name) or {}
            kinds[name] = {"artifact_kind": str(spec.get("artifact_kind") or ""),
                           "runner_registered": spec.get("runner") is not None,
                           "capability": self.capability(name)}
        out["kinds"] = kinds
        return out


# Default instance is HONEST: no resolver wired -> every kind refuses with a
# reason. A seam with no registered default is a bug, so register one anyway.
register("gpu_queue", "local", GpuWorkQueue, default=True)
