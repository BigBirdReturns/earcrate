from earcrate.core.deps import *
from earcrate.core.util import json_dumps, sha256_text
from earcrate.providers.stems import stem_capability
from earcrate.providers.beats import beat_capability
"""EARCRATE — the GPU work queue: ONE seam for every expensive accelerator job.

The 4060 is a multi-tool, but until now each GPU capability (demucs stems) was
wired ad-hoc into its own orchestrator. This module is the seam that makes the
NEXT capability an onboarding, not an engineering project: declare a job KIND
(name + capability probe + runner), register it, and the queue does the rest —
dedup, prioritization, batching, and receipts.

Design rules (the 8 GB VRAM policy, encoded):

  * BATCH BY KIND. Model loads dominate GPU job cost on an 8 GB card (one model
    resident at a time). The queue drains ALL pending jobs of one kind before
    switching kinds, so a model is loaded once per batch, not once per job.
  * INTERACTIVE LANE BEATS WARM LANE. A user waiting on a render outranks a
    background warmer, always. Within a lane, enqueue order is preserved
    (deterministic, receipts reproducible).
  * CONTENT-ADDRESSED, DONE-ONCE. A job's id is a digest of (kind, identity,
    payload-shape); results land in the shared L3 ArtifactStore keyed by the
    same identities the rest of the engine uses (pcm_sha + recipe), so a job
    done once is done forever — the queue consults the kind's ``has`` probe and
    skips warm work without touching the GPU.
  * HONEST CAPABILITY. Every declared kind carries a probe that answers "could
    THIS box run it, and if not, what exactly is missing?" — a kind with no
    registered runner or missing deps reports that, it does not pretend.

Tenant #1 is the demucs stem-warmer (EarcrateCore.warm_stems): it was born
before the queue and its gates are the proof of the seam — the warmer now draws
its work through the queue and its behavior (priority order, skip-warm, budget
stop, honest no-GPU degradation) is unchanged, gate-verified. ``beats``,
``embed`` and ``transcribe`` are declared kinds awaiting runners; their probes
tell the box owner what a `pip install` would unlock.
"""

LANES = ("interactive", "warm")


def _module_available(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


class JobKind:
    """A declared GPU job kind: what it is, whether this box could run it, and
    (when a runner is registered) how to run one job of it."""

    def __init__(self, name: str, description: str = "",
                 probe: Optional[Any] = None, runner: Optional[Any] = None,
                 has: Optional[Any] = None):
        self.name = str(name)
        self.description = str(description)
        self._probe = probe
        self.runner = runner
        self.has = has  # callable(job) -> bool: result already materialized?

    def probe(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {"kind": self.name, "description": self.description,
                                "runner_registered": bool(self.runner)}
        try:
            cap = self._probe() if callable(self._probe) else {}
        except Exception as exc:
            cap = {"ready": False, "error": str(exc)[:200]}
        out = dict(cap or {})
        out.update(base)
        if not self.runner:
            out["ready"] = False
            missing = list(out.get("missing") or [])
            missing.append("no runner registered for kind %r" % (self.name,))
            out["missing"] = missing
        return out


# kind name -> JobKind. Module-level so tenants register once at import.
_WORK_KINDS: Dict[str, JobKind] = {}


def register_kind(name: str, description: str = "", probe: Optional[Any] = None,
                  runner: Optional[Any] = None, has: Optional[Any] = None) -> JobKind:
    """Declare (or re-declare) a job kind. Re-registration replaces the entry —
    a tenant that gains a runner upgrades its own declaration."""
    k = JobKind(name, description=description, probe=probe, runner=runner, has=has)
    _WORK_KINDS[str(name)] = k
    return k


def kind_capabilities() -> Dict[str, Dict[str, Any]]:
    """Honest per-kind capability report: what the box COULD run and exactly
    what is missing for the rest. This is the queue's doctor() surface."""
    return {name: k.probe() for name, k in sorted(_WORK_KINDS.items())}


class GpuWorkQueue:
    """In-process, deterministic accelerator work queue.

    Not a daemon: an orchestrator (the warmer, a render, a future scheduler)
    constructs one, enqueues, and drains — the queue's value is the POLICY it
    encodes (lanes, batch-by-kind, dedup, done-once) plus the receipts ledger."""

    def __init__(self):
        self._jobs: List[Dict[str, Any]] = []
        self._ids: set = set()
        self.receipts: List[Dict[str, Any]] = []

    @staticmethod
    def job_id(kind: str, identity: str, payload: Optional[Dict[str, Any]] = None) -> str:
        shape = {k: v for k, v in (payload or {}).items()
                 if isinstance(v, (str, int, float, bool, type(None), list, tuple))}
        return sha256_text(json_dumps({"kind": str(kind), "identity": str(identity), "payload": shape}))

    def enqueue(self, kind: str, identity: str, payload: Optional[Dict[str, Any]] = None,
                lane: str = "warm") -> Optional[Dict[str, Any]]:
        """Add one job. Returns the job dict, or None when an identical job
        (same content-addressed id) is already pending — dedup by construction."""
        if lane not in LANES:
            raise ValueError("unknown lane %r (want one of %r)" % (lane, LANES))
        if kind not in _WORK_KINDS:
            raise KeyError("unknown job kind %r; register_kind() it first" % (kind,))
        jid = self.job_id(kind, identity, payload)
        if jid in self._ids:
            return None
        job = {"id": jid, "kind": str(kind), "identity": str(identity),
               "lane": lane, "payload": dict(payload or {}), "seq": len(self._jobs)}
        self._ids.add(jid)
        self._jobs.append(job)
        return job

    def pending(self) -> Dict[str, Any]:
        by = {}
        for j in self._jobs:
            key = (j["lane"], j["kind"])
            by[key] = by.get(key, 0) + 1
        return {"total": len(self._jobs),
                "by_lane_kind": {f"{l}/{k}": n for (l, k), n in sorted(by.items())}}

    def ordered_jobs(self) -> List[Dict[str, Any]]:
        """Drain order: interactive lane strictly before warm lane; within a
        lane, jobs are BATCHED BY KIND (kinds in first-enqueue order, jobs in
        enqueue order) so one model stays resident per batch."""
        out: List[Dict[str, Any]] = []
        for lane in LANES:
            lane_jobs = [j for j in self._jobs if j["lane"] == lane]
            kind_order: List[str] = []
            for j in lane_jobs:
                if j["kind"] not in kind_order:
                    kind_order.append(j["kind"])
            for kind in kind_order:
                out.extend(j for j in lane_jobs if j["kind"] == kind)
        return out

    def record(self, job: Dict[str, Any], status: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = {"id": job["id"], "kind": job["kind"], "identity": job["identity"],
             "lane": job["lane"], "status": str(status)}
        if detail:
            r["detail"] = detail
        self.receipts.append(r)
        return r

    def drain(self, should_continue: Optional[Any] = None,
              max_jobs: int = 0) -> Dict[str, Any]:
        """Generic executor for registered runners: skip jobs whose result is
        already materialized (done-once), run the rest in policy order, record a
        receipt per job. ``should_continue(job) -> Optional[str]`` may stop the
        drain with a reason (budget, shutdown). Orchestrators with bespoke
        accounting (the stem warmer) iterate ``ordered_jobs()`` themselves."""
        ran = 0
        cached = 0
        errors = 0
        stopped_reason = "queue drained"
        for job in self.ordered_jobs():
            if max_jobs and ran >= max_jobs:
                stopped_reason = "max_jobs reached"
                break
            if should_continue is not None:
                reason = should_continue(job)
                if reason:
                    stopped_reason = str(reason)
                    break
            kind = _WORK_KINDS.get(job["kind"])
            try:
                if kind and callable(kind.has) and kind.has(job):
                    cached += 1
                    self.record(job, "cached")
                    continue
                if not kind or not callable(kind.runner):
                    errors += 1
                    self.record(job, "error", {"error": "no runner registered for kind %r" % (job["kind"],)})
                    continue
                result = kind.runner(job)
                ran += 1
                self.record(job, "done", {"result": result} if isinstance(result, dict) else None)
            except Exception as exc:
                errors += 1
                self.record(job, "error", {"error": str(exc)[:300]})
        return {"ran": ran, "cached": cached, "errors": errors,
                "stopped_reason": stopped_reason, "receipts": list(self.receipts)}


# ---------------------------------------------------------------------------
# Declared kinds. `stems` is LIVE (tenant #1: the demucs warmer draws through
# the queue; runner attached per-drain by the orchestrator because the provider
# is chosen per workspace). The rest are declared with honest probes so the
# capability report tells the box owner exactly what an install would unlock.
# ---------------------------------------------------------------------------

def _stems_probe() -> Dict[str, Any]:
    cap = dict(stem_capability())
    missing = [dep for dep in ("torch", "demucs") if not cap.get(dep)]
    if not cap.get("cuda"):
        missing.append("cuda")
    cap["missing"] = missing
    return cap


def _beats_probe() -> Dict[str, Any]:
    # Delegate to the BeatProvider seam so this work-queue kind and
    # `beat_capability()` (and doctor) never disagree about readiness.
    try:
        cap = beat_capability()
        return {"ready": bool(cap.get("ready")), "torch": bool(cap.get("torch")),
                "backend": "allin1" if cap.get("allin1") else None,
                "missing": [] if cap.get("ready") else ["allin1 (pip install allin1)"],
                "unlocks": "real beat/downbeat/section grids (allin1) replacing librosa beat_track"}
    except Exception:
        have_torch = _module_available("torch")
        return {"ready": False, "torch": have_torch, "missing": ["allin1"],
                "unlocks": "real beat/downbeat/section grids replacing librosa beat_track"}


def _embed_probe() -> Dict[str, Any]:
    have_torch = _module_available("torch")
    backend = next((m for m in ("laion_clap", "openl3") if _module_available(m)), None)
    missing = ([] if have_torch else ["torch"]) + ([] if backend else ["laion_clap or openl3"])
    return {"ready": False, "torch": have_torch, "backend": backend, "missing": missing,
            "unlocks": "audio embeddings for retrieval/similarity (fills the NoopEmbeddingProvider seam)"}


def _transcribe_probe() -> Dict[str, Any]:
    backend = next((m for m in ("faster_whisper", "whisper") if _module_available(m)), None)
    return {"ready": False, "backend": backend,
            "missing": [] if backend else ["faster_whisper or whisper"],
            "unlocks": "lyric/vocal transcription for hook selection and intelligibility scoring"}


register_kind("stems", "demucs source separation into the L3 stem cache",
              probe=_stems_probe)
register_kind("beats", "GPU beat/downbeat tracking", probe=_beats_probe)
register_kind("embed", "audio embedding extraction", probe=_embed_probe)
register_kind("transcribe", "vocal transcription", probe=_transcribe_probe)
