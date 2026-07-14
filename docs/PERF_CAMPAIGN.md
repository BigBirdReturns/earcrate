# The performance chapter — v0.8.30

v0.8.30 landed as two commits: `v0.8.30 — rebuild the perf campaign` (single-decode
analyze, DSP dedup, deck memoization, IO/DB waste, BPM fold provenance) and
`v0.8.30 — GPU work queue seam` (`earcrate/providers/workqueue.py`, demucs warmer
promoted to tenant #1). The perf commit rebuilds work from a prior session that "died
unpushed with its container" — this document is the record that should survive the
next one. Every fix below is cited against the audit's verified findings and the
commit message's own wording; nothing here is measured on real hardware yet (see
"Not yet fixed" below).

## 1. Fixed in v0.8.30

Eight wastes, verified by the hot-path audit, fixed in the perf-campaign commit
(`5217633`) unless noted.

### Single-decode analyze
- **Waste:** `analyze_file_worker` decoded the track twice per file — once bounded
  (`decode_audio`, features) and once full-track via a separate ffmpeg hash-muxer
  pass (`decoded_audio_sha256`), just to get `pcm_sha`.
- **Fix:** `decode_audio_with_full_sha` (`earcrate/analyze/decode.py`) streams ONE
  full decode, hashing the raw decoder byte stream as it arrives while retaining
  only the bounded `keep_seconds` prefix in RAM. The digest is taken over the same
  raw bytes the old hash muxer hashed (mono f32le, stream `0:a:0`), so it is
  byte-identical — verified on FLAC and MP3 — meaning every banked `pcm_sha` and
  every L3 stem key keyed off it stays valid with zero invalidation. Wired in
  `analyze_file_worker` (`earcrate/analyze/features.py`).
- **Expected win (as stated in the commit):** ~2x analyze decode throughput.

### `local_harmony` — one chromagram, not ~59
- **Waste:** `local_harmony` (`earcrate/analyze/beat_features.py`) rebuilt a
  `chroma_cqt` from scratch for every 6-second window with a 3-second hop — ~59
  CQT builds on a 3-minute track. The audit measured this at 36% of total per-track
  analyze cost.
- **Fix:** one whole-track `chroma_cqt` (falling back to `chroma_stft` on failure),
  and each window slices the shared frame grid (`chroma_full[:, f0:f1]`) instead of
  recomputing. Krumhansl key detection runs on the slice as before.
- **Expected win:** removes the single largest per-track analyze cost line (36%
  of per-track analyze, per the audit's measurement).

### `onset_strength` — shared, not recomputed 3x
- **Waste:** `beat_novelty`, `groove_descriptor`, and the tempo/beats pass each
  called `librosa.onset.onset_strength` independently — a full mel spectrogram
  three times over on identical `(y, sr, hop)` input.
- **Fix:** `_onset_curve` (`earcrate/analyze/beat_features.py`) computes the curve
  once and callers pass it through. `beat_state_features` accepts an `onset=`
  parameter and threads it to `beat_novelty` and `groove_descriptor`;
  `compute_pcm_features` (`earcrate/analyze/features.py`) passes its own
  `onset_env` (already computed for tempo/beats) straight into
  `beat_state_features`, so the curve is now computed exactly once per track.
- **Expected win:** stated qualitatively in the commit ("computed once and shared
  across tempo/beats, novelty, and groove instead of 3x") — not separately
  quantified from the `local_harmony` number.

### `hpss` on the spectrogram already in hand
- **Waste:** the crate pass's per-clip metrics (`ear_atom_metrics`, `earcrate/app.py`
  around the spectral-share computation) called `librosa.effects.hpss(seg)`, a
  time-domain call that runs a second full STFT plus two inverse STFTs just to
  produce two scalar energy-share numbers — on top of the spectrogram
  (`S`/`S_mag`) the same function already computed for `low_share`/`mid_share`/
  `flat`, etc.
- **Fix:** switched to `librosa.decompose.hpss(S_mag)`, which median-filters the
  magnitudes already in hand and returns harmonic/percussive magnitude masks
  directly — no resynthesis, same harmonic-vs-percussive semantics, ratios read
  off `H_mag`/`P_mag` sums.
- **Expected win (as stated):** the old call was "~0.78s/clip x 12 clips/track, as
  costly as analyze itself" — the audit's own framing for how expensive this line
  was, now eliminated.

### `plan_varispeed_transform` memoization
- **Waste:** `plan_varispeed_transform` (`earcrate/deck/transform.py`) is a pure
  function called by deck search across every key x lattice-BPM x pool-loop
  combination, then again during feasibility checks — the audit measured ~4.4M
  calls per `propose`, each argument combination computed twice.
- **Fix:** the public function now delegates to `_plan_varispeed_cached`, an
  `functools.lru_cache(maxsize=1 << 18)`-wrapped `_plan_varispeed_uncached`.
  `plan_varispeed_transform` returns `dict(...)` — a fresh shallow copy of the
  cached entry — specifically so a caller that mutates/annotates the returned
  plan cannot poison the shared cache. Falls back to the uncached path on
  `TypeError`/`ValueError` (unhashable inputs).
- **Expected win (as stated):** 0.5us/call on a cache hit.

### `ArtifactStore.has()` — existence without the blob read
- **Waste:** the only way to ask "is this cached?" was `store.get(key) is not None`,
  which read the entire artifact (up to ~48MB per stem WAV) plus its meta JSON off
  disk, just to answer a boolean. `DemucsStemProvider.separate`'s cache-check and
  `has_stems` (`earcrate/providers/stems.py`) both did this per role, per probe.
- **Fix:** `ArtifactStore.has(key)` (`earcrate/providers/artifacts.py`) checks
  `bin_path.exists() and meta_path.exists()` — no read. `DemucsStemProvider`
  (`earcrate/providers/stems.py`, the cache-hit branch of `separate` and all of
  `has_stems`) switched from `store.get(k) is not None` to `store.has(k)`.
- **Expected win (as stated):** a warm-status sweep over a big library previously
  did gigabytes of IO to answer yes/no questions; now zero blob reads for existence
  checks.

### Per-render stem memoization
- **Waste:** inside `render_mashup`, the closure that resolves a stem source
  (`earcrate/app.py`, around the varispeed/section render loop) consulted the stem
  provider and decoded the full stem WAV on every layer, every section — the audit
  measured ~10GB of IO and ~80 ffmpeg spawns on one warm render.
- **Fix:** a `stem_source_cache: Dict[Tuple[str, str], ...]` keyed by
  `(pcm_sha, stem_role)` added to `render_mashup`'s local state. The stem-resolving
  closure is memoized against it — one provider consult + one decode per
  `(pcm_sha, role)` per render — before falling through to the actual resolution
  logic (renamed `_resolve_stem_source`).
- **Expected win (as stated):** ~10GB IO and ~80 ffmpeg spawns eliminated per warm
  render (replaced by one decode per unique stem role actually used).

### Read-only WAL status connection
- **Waste:** `/api/status` polls (continuous from the UI) shared the single
  `self.db` connection with background analyze/extract writers. Even with WAL mode
  and `synchronous=NORMAL` already applied, every status poll could still queue
  behind a writer's in-flight statements on the shared connection — the commit
  notes this "only removed half the lag."
- **Fix:** `EarcrateCore.read_conn()` (`earcrate/app.py`) opens a second SQLite
  connection in read-only mode (`file:%s?mode=ro`, `uri=True`) that can never take
  a write lock, cached on `self._read_db` and reopened on DB reconnect
  (`connect_db` now clears `self._read_db`). `status()`/`crate_staleness()`/
  `kv_get_json()` were given an optional `db=` parameter so status polls read
  through `read_conn()` instead of `conn()`, while writers keep using the shared
  connection. Falls back to `self.conn()` if the read-only open fails (e.g. DB
  doesn't exist yet).
- **Expected win (as stated):** status polls "never queue behind a background
  writer's statements" — the second half of the input-lag fix that
  `synchronous=NORMAL` alone didn't cover.

All eight are covered by the gate suite the commit reports as 171/171, with the
pcm-identity gate extended to assert `streaming-digest == hash-muxer digest` per
file — the correctness proof for the single-decode change specifically.

## 2. Not yet fixed / still to measure

The perf-campaign commit message is explicit that it **rebuilds from the audit's
verified findings**, not from a fresh measurement pass — the prior session's actual
before/after numbers "died unpushed with its container" and were never recovered.
Every "expected win" cited above is either the audit's original measurement (taken
against the *old*, unfixed code) or a description of the fix's shape, not a
confirmed after-number on real hardware. Concretely, still outstanding:

- **No empirical before/after baseline exists for v0.8.30.** The box has not yet
  re-run per-track analyze timing or a warm-render wall-clock against this build.
- **Per-track analyze seconds** — re-measure on the real box, same track set the
  original audit used if still available, to confirm the ~2x decode throughput and
  the disappearance of the `local_harmony`/`onset_strength` costs from the profile.
- **Warm-render wall time** — re-measure a full warm render (stems already cached)
  to confirm the ~10GB IO / ~80 ffmpeg spawn elimination actually moves wall-clock,
  not just IO-call counts.
- **UI input lag under concurrent write load** — the read-only WAL connection fix
  needs to be felt, not just reasoned about: poll `/api/status` while a background
  analyze or extract is running and confirm the poll no longer stalls.
- **`plan_varispeed_transform` cache hit rate in practice** — the 4.4M-call,
  computed-twice figure was the audit's count; confirm the LRU cache's actual hit
  rate on a real `propose` run (cache size `1 << 18` — verify it isn't thrashing on
  a large pool).

Until these are captured, treat every number in section 1 as "audit-measured on the
old code" or "the fix's mechanism," not as a verified v0.8.30 result.

## 3. GPU tenant onboarding

`earcrate/providers/workqueue.py` is the seam that turns the accelerator (the 4060
in the reference box) from a single-purpose demucs runner into a multi-tool. Before
this seam, each GPU capability was wired ad-hoc into its own orchestrator; after it,
adding a capability is an onboarding, not an engineering project.

### The mechanism

A **job kind** is a declaration: a name, a capability probe, and — once available —
a runner and a `has` (already-materialized?) check. `register_kind()` stores it in
the module-level `_WORK_KINDS` registry (named to avoid the concat collision with
`materials/regions._KINDS` in the single-file build):

```python
register_kind(name, description="", probe=None, runner=None, has=None)
```

- `probe: () -> dict` — an honest capability report: `{"ready": bool, "missing":
  [...], ...}`. A kind with no runner registered always reports `ready=False` with
  `"no runner registered for kind %r"` appended to `missing` — it never pretends.
- `runner: (job) -> result` — executes one job. Only required once the capability
  actually exists on the box.
- `has: (job) -> bool` — the done-once check: does the result already exist in the
  shared L3 `ArtifactStore` for this job's identity? This is what makes the queue
  content-addressed rather than merely deduplicated at enqueue time — a job whose
  result already exists is never handed to `runner` at all.

Callers construct a `GpuWorkQueue()`, `enqueue(kind, identity, payload, lane)` jobs,
then either call `drain()` (the queue runs everything through registered runners
and `has` checks itself) or iterate `ordered_jobs()` directly when the caller needs
bespoke accounting (budget stops, progress reporting) — exactly what the demucs
warmer does.

Three policy rules are encoded directly in `ordered_jobs()`/`enqueue()`, not left to
convention:

- **Content-addressing.** `job_id()` hashes `(kind, identity, payload-shape)`
  (`sha256_text` over a JSON-safe payload subset) via `sha256_text`. Enqueueing an
  identical job a second time returns `None` — dedup by construction, no dedup
  logic required of the caller. The intended identity for real jobs is
  `pcm_sha` (+ recipe/role where relevant), the same identity the rest of the
  engine already keys L3 artifacts by — so a kind's `has` probe and the queue's own
  dedup line up with the artifact store's own keys.
- **Lane priority.** Two lanes, `("interactive", "warm")`. `ordered_jobs()` always
  drains all of `interactive` before any of `warm` — a user waiting on a render
  outranks a background warmer, unconditionally.
- **Batch by kind.** Within a lane, jobs are grouped by kind in first-enqueue order,
  preserving enqueue order within each kind's group. This is the 8GB-VRAM policy
  made concrete: model loads dominate GPU job cost on an 8GB card, so the queue
  never interleaves kinds mid-lane — one model stays resident per batch instead of
  reloading between individual jobs.

### Worked example: the demucs warmer (tenant #1)

`stems` is the one LIVE kind. Its probe is `_stems_probe()`
(`earcrate/providers/workqueue.py`), which wraps `stem_capability()` from
`earcrate/providers/stems.py` and reports missing `torch`/`demucs`/`cuda`
explicitly. It is registered at import time:

```python
register_kind("stems", "demucs source separation into the L3 stem cache",
              probe=_stems_probe)
```

Note `stems` is registered with **no runner** at module scope — the runner is
attached per-drain because the concrete stem provider is chosen per workspace
(`self._resolve_stem_provider()` in `earcrate/app.py`), not fixed at import time.

The integration point is `EarcrateCore.warm_stems()` (`earcrate/app.py`). Per the
commit message, this is the durability rule in action — "born live": the warmer
existed before the queue, and becomes tenant #1 rather than the queue shipping
with a synthetic first user. `warm_stems` builds its priority list of cold sources
as before, then instead of processing that list directly:

```python
q = GpuWorkQueue()
for i, cand in enumerate(cands):
    q.enqueue("stems", identity=str(cand["pcm_sha"]), lane="warm",
              payload={"path": str(cand["path"]), "roles": role_list, "index": i})
...
for job in q.ordered_jobs():
    ...  # warmer's own budget/skip/progress accounting, unchanged
    q.record(job, "done" | "cached" | "error", detail)
```

The warmer iterates `ordered_jobs()` itself rather than calling `drain()`, because
it needs its own accounting: `max_items`, cache-budget stop-without-eviction, and
`set_status()` progress reporting are all warmer-specific concerns the generic
`drain()` doesn't know about. The queue supplies dedup and receipts; the warmer
keeps its bespoke stopping conditions.

The proof of the seam, stated directly in the commit: `tests/test_stem_warmer.py`
passes **unmodified** — the warmer's priority order, skip-warm behavior, budget
stop, and honest no-GPU degradation are all unchanged from the caller's point of
view, because for a single kind in a single lane, `ordered_jobs()` reduces to plain
enqueue order.

`doctor()` surfaces `kind_capabilities()` (all registered kinds' probes) as
`gpu_work_kinds` in `earcrate/app.py` — the per-kind honest report the box owner
sees.

### Onboarding a new kind

To add a real (not just declared) capability:

1. Write a probe: `() -> {"ready": bool, "missing": [...], ...}` that checks the
   actual dependency (module import, CUDA availability) — see `_stems_probe` for
   the pattern of wrapping an existing capability function.
2. Write a runner: `(job) -> result`, where `job` carries `kind`, `identity`
   (should be `pcm_sha`, optionally combined with a recipe string for jobs with
   variants), and `payload`.
3. Write a `has`: `(job) -> bool` that checks the shared L3 `ArtifactStore` for the
   result keyed by the job's identity — use `ArtifactStore.has()` (existence only,
   no blob read; see the fix in section 1) rather than `get() is not None`.
4. `register_kind(name, description, probe=..., runner=..., has=...)` — this
   replaces any prior declaration for that name, so a kind that starts
   probe-only can gain a runner later without a second registration point.
5. Enqueue jobs with `identity=pcm_sha` (matching the rest of the engine's L3
   keying) and pick a lane: `interactive` for anything blocking a user-visible
   render, `warm` for background pre-computation.

### Declared-but-unimplemented kinds

Three kinds are registered with probes only — no runner, so `probe()` always
reports `ready=False` and names exactly what installing the runner would unlock:

- **`beats`** (`_beats_probe`) — checks for `torch`; unlocks "GPU beat/downbeat
  grids replacing librosa beat_track on hard material." This is OSS-adoption
  priority #2 in `docs/PR27_REVIEW.md` (beat_this, MIT including weights) — real
  downbeats there feed sections, regions, groove, and transitions downstream.
- **`embed`** (`_embed_probe`) — checks for `torch` plus a backend
  (`laion_clap` or `openl3`); unlocks "audio embeddings for retrieval/similarity
  (fills the NoopEmbeddingProvider seam)."
- **`transcribe`** (`_transcribe_probe`) — checks for a backend (`faster_whisper`
  or `whisper`); unlocks "lyric/vocal transcription for hook selection and
  intelligibility scoring."

Each of these becomes a real tenant by following the same five steps as any new
kind — the queue itself needs no changes; only a probe upgrade (runner attached)
and, if the kind's result belongs in L3, a `has` probe against the artifact store.
