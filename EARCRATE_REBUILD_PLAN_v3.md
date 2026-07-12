# EARCRATE v3 — The Constitution: only new problems

**Status:** binding. Supersedes `EARCRATE_REBUILD_PLAN_v2.md` (kept for history).
Does not weaken `AGENTS.md`; where they overlap, AGENTS.md wins.

**What this document is.** Not a roadmap. A contract. Every past failure is
written down here as an *invariant* with an *executable gate*, so the next
version cannot re-commit it silently. The bar for v3 is one sentence:

> **We only get NEW problems.** Every failure mode we have already paid for is
> pinned by a test that goes red the instant it comes back.

---

## 0. Honest reckoning (say the true thing first)

The "ground-up rebuild" from v0.7.x → v0.8.x **was not a rebuild.** It was a
renovation of a ~5,000-line monolith (`EarcrateCore` in `earcrate/app.py`)
while it ran. The product got real — residents, curation, receipts, a real
5,382-track library actually cleaned end-to-end, reversibly, with zero files
lost — but the *shape* that produced the old bugs is still standing, and it
kept leaking:

- **single-file import crash** — `assess_track_audio` did an in-function
  `from earcrate.analyze.decode import decode_audio`; the single-file builder
  strips package imports, so `dist/earcrate.py` crashed with "earcrate is not a
  package." The package gate was green. Only *driving the real CLI* caught it.
- **force-rebuild cascade** — `extract_loops(force=True)` still runs
  `DELETE FROM loops WHERE file_id=? AND stem='mix'`; `ear_atoms` and
  `atom_judgments` cascade on delete. A convenience flag silently destroys
  human judgment. This is Lesson #7 from v2, *still live in the code.*
- **stray workspace pointers** — committed `earcrate_workspace.json` files (one
  pointing at `/tmp/...`) made a fresh core auto-adopt stale config → phantom
  legacy source in a gate.
- **rollback footgun** — `reorganize_source` rollback executed immediately
  instead of dry-run-default.
- **one fabricated "done"** — a v0.8.0 rescan was claimed that never ran.

Four of five are the monolith's shape (hidden coupling, mutation-without-
identity, machine-first surfaces, convenience over safety). The fifth was mine
to own, and I own it. **v3 exists so these classes of failure become
structurally impossible, not just individually patched.**

The five-layer analysis that prompted this document is correct and sharper than
the two-tier answer it replaced. It is adopted below as the architecture of
record.

---

## 1. The lessons ledger — every failure → invariant → gate

Each row is law. A row is not "done" until its gate exists and is red when the
failure is reintroduced. Rows marked ⚠ are **still open in the current code**
and are the front of the v3 work queue.

| # | What happened | Root shape-flaw | v3 invariant | Gate (executable) |
|---|---|---|---|---|
| 1 | renders 4× target length | arithmetic buried in a 2,000-line method | all composition math is pure functions in `plan/`, no I/O | `test_plan_math_*` unit gates on each formula |
| 2 | scorer blind to vocals | two vocabularies (`world` vs `role`) | ONE typed layer/role model; no legacy twins | `test_no_legacy_role_vocabulary` greps for the dead terms |
| 3 | organize made ` (2) (3)` dup trees | mutation without identity | every derived artifact has a deterministic identity; re-runs upsert | `test_organize_idempotent` — run twice, byte-identical tree |
| 4 | Browse called an endpoint that never existed | UI/API drift silently | routes come from ONE table; UI calls only named routes; e2e clicks every control | `test_route_table_is_source_of_truth` + Playwright click-all |
| 5 | personas erased each other's crates | identity missing a dimension (`UNIQUE(loop_id)`) | identity keys carry ALL dimensions from day one | `test_personas_coexist_and_adopt` |
| 6 | 2-hour re-audition | measurement conflated with judgment | measurements stored ONCE per loop, persona-free; personas store only judgments referencing them | `test_measurement_shared_across_personas` |
| 7 ⚠ | **judgments erased by regraph/force-rebuild** | derived tables deleted+recreated with random ids | human judgment tables append-only, keyed by DETERMINISTIC identity; derived data may churn, judgment never | **`test_force_rebuild_preserves_judgments`** (does NOT yet exist — see §5.1) |
| 8 | serial DSP on one core | compute paths grown ad hoc | every per-file compute goes through ONE parallel harness (decode-once, pool, ETA, receipts) | `test_single_compute_harness` |
| 9 | "is it stuck?" | status strings instead of structured progress | progress is structured `{stage,i,n,eta_s}`, never parsed from prose | `test_progress_is_structured` |
| 10 | hidden AppData outputs, unreadable receipts | machine-first surfaces | every receipt has a human sentence + WHERE + open-folder; visible paths default | `test_every_receipt_has_human_and_path` |
| 11 | version never changed / "did it land?" | provenance as an afterthought | version + content-hash stamped at build in page, header, dist; checked | `test_version_stamped_three_places` |
| 12 | two persona sources of truth diverging | constants in code AND docs AND JSON | JSON is the only source; projections + drift gate | `test_persona_json_is_only_source` |
| 13 ⚠ | **single-file crash from in-function package import** | build transform (strip imports) not modeled by the package gate | anything shipped in `dist/earcrate.py` is gated by driving the BUILT artifact, not the package | **`test_singlefile_cli_smoke`** — build, then run `dist/earcrate.py scan/analyze/...` as a subprocess |
| 14 ⚠ | **stray committed workspace pointers → phantom config** | runtime state living in the repo tree | no runtime pointer/DB/cache is ever git-tracked; fresh clone adopts NOTHING implicitly | **`test_fresh_clone_has_no_runtime_state`** — clean tree has zero `*_workspace.json`, `.db`, cache dirs; `.gitignore` covers them |
| 15 ⚠ | **rollback executed immediately (footgun)** | destructive default | EVERY reversal/mutation is dry-run-default, signature-gated, `--apply`-only | **`test_all_mutations_dry_run_default`** — each mutating endpoint returns `dry_run:true` without `apply` |
| 16 ⚠ | **fabricated "done" (v0.8.0 rescan)** | claim not bound to evidence | no "done" without a receipt on disk the gate can read back | **`test_done_requires_receipt`** — the honesty invariant, §4 |

**The current code is honest about its debt: rows 7, 13, 14, 15, 16 are the
open front.** v3's first commits close them (see §5).

---

## 2. Architecture of record — the five layers (identity-first)

The monolith's core flaw is that *measurement, judgment, and materialization
all live in one mutable table space keyed by random ids.* v3 separates them by
LAYER, and every cross-layer reference is a **deterministic identity**, never a
row-id.

```
L0  SOURCE TRUTH          immutable, never derived
    - byte_sha256(file)               ← the file as bytes
    - pcm_sha256(decoded canonical)   ← the SOUND, codec-independent
    - recording_identity (opt-in)     ← AcoustID/MBID when online-identified
    truth about "what this is." Nothing above may mutate it.

L1  UNIVERSAL MEASUREMENTS   persona-FREE, versioned, cacheable
    - beats, downbeats, key, tempo, sections, loudness, spectral shape
    - cached as <pcm_sha>-<ANALYZER_VERSION>.npz  (content-addressed)
    measured ONCE per (sound, analyzer version). Shared by everyone.

L2  TASTE PROJECTIONS        persona-DEPENDENT, append-only
    - atoms (loop candidates: RECIPES not audio — file_id+start+end+bars+role)
    - judgments (human keeps/kills/locks), keyed by DETERMINISTIC segment id
    - persona TasteSpec JSON is the ONLY source of a persona's parameters
    churns freely EXCEPT judgments, which are append-only and never cascade-die.

L3  LAZY MATERIALIZATION     expensive, recomputable, retention-tiered
    - stems (Demucs), previews, rendered layers
    - retention tier by recomputation cost: ephemeral | warm | pinned
    - every artifact carries provenance: (source_identity, provider, version)
    NOTHING here is source of truth. It can always be thrown away and rebuilt.

L4  CREATIVE RECORD          the human's work, sacred
    - plans, renders, receipts, sessions
    - a saved plan renders byte-identically or it is an INVARIANT FAILURE
    this is what the user made. It outlives every layer below it.
```

**Deterministic segment identity (the keystone).** A loop/segment is identified
by content, not by an autoincrement:

```
segment_id = SHA256( source_audio_identity ‖ analyzer_version
                     ‖ start_sample ‖ end_sample ‖ role )
```

Consequences that kill whole bug classes:
- Re-extracting the same loops yields the SAME ids → force-rebuild is an
  **upsert**, not a delete+reinsert → judgments (keyed by segment_id) survive
  (kills Lesson #7).
- The same sound in two files (dup, re-encode) shares L0/L1 → measured once.
- A judgment can never be orphaned by a regraph, because the thing it points at
  is reconstructible from content.

---

## 3. Provider seams — prepared, not promised

Every expensive or network-touching capability is a **seam with a registered
no-op default**, so future work plugs in without monolith surgery. Core never
reaches around a seam.

| Seam | Default | Plugs in |
|---|---|---|
| `LibraryProvider` | local filesystem scan | catalog/service mode |
| `MeasurementProvider` | in-process librosa | GPU/remote batch |
| `EmbeddingProvider` | none | ANN retrieval at scale |
| `VectorIndex` | linear scan | FAISS/HNSW when N is large |
| `StemProvider` | **none (⚠ to build)** | Demucs on the RTX 4060 |
| `ArtifactStore` | local dir, tiered retention | networked cache |
| `CandidateRetriever` | full-catalog scan | cascading retrieval |
| `LocalGraphBuilder` | per-query, in-memory | precomputed neighborhoods |
| `IdentifyProvider` | offline heuristics | AcoustID/MusicBrainz (opt-in, network stays OUT of core) |
| `RenderProvider` | in-process renderer | DAW/offline farm |
| `ReceiptStore` | local JSON + human sentence | shared station |

**Two modes, one codebase.** *Personal/local* (this laptop, the desktop, the
NUC — everything on disk, no network required) and *service/catalog* (scale-out,
ANN, remote stems) are the same seams wired to different providers. The
Girl-Talk-from-a-drive use case is personal mode with `StemProvider=Demucs` and
`CandidateRetriever=cascading`.

**Vector-scale honesty.** We do NOT hold cut stems for 100k–100M songs. We hold
**recipes** (L2, tiny) and **measurements** (L1, content-addressed, dedup'd by
sound). Stems (L3) are materialized lazily for the few tracks a plan actually
touches, retention-tiered by recomputation cost, and evicted. Retrieval
cascades: catalog filter → ANN shortlist → section retrieval → selective
separation → query-local graph. This is the only way the drive-scale promise is
truthful.

---

## 4. The "only new problems" contract

Three rules make the headline enforceable:

1. **No claim without a receipt.** "Done," "cleaned," "rescanned," "passing"
   must correspond to an artifact on disk (a receipt, a gate result, a journal)
   that can be read back independently. Gate `test_done_requires_receipt`
   (Lesson #16). *I broke this once; it is now law.*

2. **No mutation without dry-run + signature + `--apply`.** Every destructive or
   outward action simulates first, prints a human sentence and a signature,
   and executes only when re-invoked with `--apply` + the matching signature.
   Reversible via journal. Gate `test_all_mutations_dry_run_default`
   (Lesson #15).

3. **No ship without driving the built artifact.** Package-mode green is
   necessary, not sufficient. The single-file `dist/earcrate.py` and the web UI
   are exercised as subprocesses / real browser clicks in CI before any batch
   is called shipped. Gates `test_singlefile_cli_smoke` (Lesson #13) +
   Playwright click-all (Lesson #4).

**Acceptance bar for v3 as a whole:** all 16 ledger gates green, on the BUILT
single-file, plus a real-library soak (thousands of tracks, reversible clean,
zero files lost) — the soak the 5,382-track library already passed once, now
pinned as a fixture.

---

## 5. Build sequence — identity first, then the fun

Ordered so every step lands on the invariant beneath it. No step ships without
its gate.

### 5.1 Close the open ledger rows (the debt)  ⚠ FIRST
- **Deterministic segment identity** (§2 keystone): replace `ulidish()` loop ids
  with the content hash. Backfill existing loops by recomputing ids from their
  recipe fields; migrate `ear_atoms`/`atom_judgments` FKs to the new id.
- **Judgment-safe force**: `extract_loops(force=True)` becomes RE-MEASURE IN
  PLACE — upsert by segment_id, never `DELETE FROM loops`. Judgments keyed by
  segment_id survive by construction. Ship `test_force_rebuild_preserves_judgments`
  RED first (proving the current bug), then GREEN.
- **Built-artifact smoke** (`test_singlefile_cli_smoke`), **no-runtime-state**
  (`test_fresh_clone_has_no_runtime_state`), **dry-run-default sweep**
  (`test_all_mutations_dry_run_default`), **receipt-backed done**
  (`test_done_requires_receipt`).

### 5.2 StemProvider (the Girl-Talk enabler)
- Seam with no-op default; Demucs implementation targeting the RTX 4060.
- Materializes to L3 with provenance `(pcm_sha, "demucs", model_version)`,
  retention-tiered, evictable. Never source of truth.
- Unlocks vocal-on-instrumental layering, the actual mashup.

### 5.3 Layer separation as tables (retire the monolith's shared space)
- L1 measurements table (persona-free) as the ONLY compute output.
- L2 judgments append-only, deterministic-keyed.
- The `plan/` purification (composition math out of `EarcrateCore`, unit-gated).

### 5.4 Cascading retrieval + modes
- CandidateRetriever cascade; EmbeddingProvider/VectorIndex behind the seam so
  linear-scan (personal) and ANN (catalog) are one code path.

### 5.5 Exact renderer + cutover
- A saved plan renders byte-identically; a selected layer that cannot render is
  a FAILURE, not a skip.
- Cutover only when all 16 gates + built-artifact + browser e2e + real-library
  soak are green.

---

## 6. What v3 does NOT do

- No streaming-DJ pivot. The collage/medley/Girl-Talk compiler IS the product.
- No network in core. Identify/catalog stay opt-in, isolated behind seams.
- No new personas beyond data files.
- No rewrite of DSP that already passes gates. Working buffalo moves; it does
  not get re-shot. But a buffalo made of hidden coupling gets *rebuilt to a
  shape that stands on its own* — which is the whole point of this document.

---

*Companion docs: `AGENTS.md` (non-negotiable rules), `JUKEBREAKER_SPEC_v2_
CONSOLIDATED.md` (acceptance spec), `LIBRARY_CONTRACT.md` (the librarian seam),
`EARCRATE_REBUILD_PLAN_v2.md` (superseded, kept for lineage).*
