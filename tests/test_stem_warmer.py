"""Gates: background GPU stem-warming (put the idle 4060 to work banking the
expensive, reusable unit -- demucs stems -- into the NVMe cache AHEAD of render
time, so composing/rendering is a cache-hit and never blocks on separation).

The real GPU separation lives behind the (desktop-verified) DemucsStemProvider
seam; everything the warmer ORCHESTRATES -- priority queue, dedup-by-source,
skip-already-warm, budget stop, honest no-GPU degradation, progress report -- is
pure Python and fully verified here with a fake ready provider. No CUDA needed.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.app import EarcrateCore
from earcrate.providers import get
from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.stems import DemucsStemProvider, NoopStemProvider


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
    """Pin the shared artifact store to a PER-TEST L3 root. _core()'s configure runs
    inside a transient patch.dict that reverts EARCRATE_L3_ROOT on exit, so without
    this the warmer's get("artifacts") falls back to one shared dir and tests
    cross-contaminate. Wrap any body that touches get("artifacts") in this."""
    return patch.dict(os.environ, {"EARCRATE_L3_ROOT": str(tmp_path / "L3")})


def _seed_source(core, tmp_path, fid, pcm, score, atoms=1, present=1, make_file=True):
    """One source file backing `atoms` approved atoms for girl_talk_v1."""
    db = core.conn()
    p = tmp_path / "music" / (fid + ".wav")
    if make_file:
        p.write_bytes(b"RIFF0000WAVEfake")  # existence only; never decoded here
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at,present) VALUES(?,?,?,?,?,?,?)",
               (fid, str(p.resolve()), "master", 1, 1, "now", present))
    core._set_pcm(fid, pcm)
    for k in range(atoms):
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (f"l_{fid}_{k}", fid, 0, 4, 2, "vocal", score, "now"))
        db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,status,metrics_json,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"a_{fid}_{k}", f"l_{fid}_{k}", fid, "girl_talk_v1", "VOX_HOOK", "vocal", 0, 4, 2, score, "approved", "{}", "now"))
    db.commit()


class _FakeGPU:
    """Stand-in for a ready DemucsStemProvider: separate() writes stem bytes into
    the SHARED artifact store (so the warmer's budget accounting sees them) and
    remembers what it warmed (so has_stems() reflects reality) -- no torch."""
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


def test_warm_candidates_dedup_by_source_and_priority_order(tmp_path):
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fl", "pcmL", 0.5, atoms=3)   # lower best score, more atoms
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9, atoms=1)   # highest best score
    _seed_source(core, tmp_path, "fa", "pcmA", 0.7, atoms=1, present=0)  # absent -> excluded
    cands = core.stem_warm_candidates("girl_talk_v1")
    shas = [c["pcm_sha"] for c in cands]
    # One row per SOURCE (dedup), highest best-atom-score first, absent source dropped.
    assert shas == ["pcmH", "pcmL"], shas
    assert cands[0]["atom_count"] == 1 and cands[1]["atom_count"] == 3
    assert all(c["path"] and c["pcm_sha"] for c in cands)


def test_has_stems_is_a_pure_cache_lookup(tmp_path):
    store = ArtifactStore(tmp_path / "L3")
    prov = DemucsStemProvider(store=store, model_version="htdemucs")
    assert prov.has_stems("pcmX", ["vocals", "no_vocals"]) is False
    store.put(prov._artifact_key("pcmX", "vocals"), b"v", tier="warm", provider="demucs", version="htdemucs")
    assert prov.has_stems("pcmX", ["vocals", "no_vocals"]) is False  # only one role warm
    store.put(prov._artifact_key("pcmX", "no_vocals"), b"i", tier="warm", provider="demucs", version="htdemucs")
    assert prov.has_stems("pcmX", ["vocals", "no_vocals"]) is True   # both warm
    assert NoopStemProvider().has_stems("pcmX", ["vocals"]) is False


def test_warm_stems_is_an_honest_noop_without_a_ready_gpu(tmp_path):
    """No torch/CUDA in this environment -> the warmer separates nothing and says
    why, instead of pretending or crashing."""
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9)
    res = core.warm_stems("girl_talk_v1")
    assert res["available"] is False
    assert res["separated"] == 0
    assert "gpu" in res["reason"].lower()


def test_warm_stems_separates_cold_and_skips_already_warm(tmp_path):
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9)
    _seed_source(core, tmp_path, "fm", "pcmM", 0.7)
    _seed_source(core, tmp_path, "fl", "pcmL", 0.5)
    with _isolated_l3(tmp_path):
        fake = _FakeGPU(get("artifacts"))
        fake.separate("pcmH", "x")  # pre-warm the top source
        with patch("earcrate.app.stem_capability", return_value={"ready": True}), \
             patch.object(core, "_resolve_stem_provider", return_value=(fake, "fakegpu")):
            res = core.warm_stems("girl_talk_v1")
    assert res["available"] is True
    assert res["candidates"] == 3
    assert res["skipped"] == 1        # pcmH was already warm -> no GPU
    assert res["separated"] == 2      # pcmM + pcmL warmed
    assert res["stopped_reason"] == "queue drained"
    # And they are genuinely warm now (a later render is a cache-hit).
    assert fake.has_stems("pcmM") and fake.has_stems("pcmL")


def test_warm_stems_respects_max_items(tmp_path):
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9)
    _seed_source(core, tmp_path, "fm", "pcmM", 0.7)
    _seed_source(core, tmp_path, "fl", "pcmL", 0.5)
    with _isolated_l3(tmp_path):
        fake = _FakeGPU(get("artifacts"))
        with patch("earcrate.app.stem_capability", return_value={"ready": True}), \
             patch.object(core, "_resolve_stem_provider", return_value=(fake, "fakegpu")):
            res = core.warm_stems("girl_talk_v1", max_items=1)
    assert res["separated"] == 1 and res["stopped_reason"] == "max_items reached"


def test_warm_stems_stops_at_cache_budget_without_evicting_its_own_work(tmp_path):
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9)
    _seed_source(core, tmp_path, "fm", "pcmM", 0.7)
    with _isolated_l3(tmp_path):
        fake = _FakeGPU(get("artifacts"), stem_bytes=5000)  # 2 roles -> 10000 bytes/source
        with patch("earcrate.app.stem_capability", return_value={"ready": True}), \
             patch.object(core, "_resolve_stem_provider", return_value=(fake, "fakegpu")), \
             patch.object(core, "_cache_byte_budget", return_value=6000):
            res = core.warm_stems("girl_talk_v1")
    # First source overshoots the 6000-byte budget; the second is not started.
    assert res["separated"] == 1
    assert res["stopped_reason"] == "cache budget reached"


def test_warm_status_reports_render_readiness(tmp_path):
    core = _core(tmp_path)
    _seed_source(core, tmp_path, "fh", "pcmH", 0.9)
    _seed_source(core, tmp_path, "fm", "pcmM", 0.7)
    with _isolated_l3(tmp_path):
        fake = _FakeGPU(get("artifacts"))
        fake.separate("pcmH", "x")  # one of two sources warm
        with patch.object(core, "_resolve_stem_provider", return_value=(fake, "fakegpu")):
            st = core.stem_warm_status("girl_talk_v1")
    assert st["total_sources"] == 2 and st["warm"] == 1 and st["cold"] == 1
    assert st["pct_warm"] == 50.0


def test_companion_caching_one_inference_serves_the_whole_vocal_pair(tmp_path):
    """Demucs separates the full mixture regardless of stems requested, so asking
    for vocals then no_vocals must cost ONE forward pass, not two. The reviewer's
    exact contract, checked with a forward-pass-counting fake _run_demucs."""
    from earcrate.providers.stems import DemucsStemProvider

    passes = {"n": 0}

    class _Counting(DemucsStemProvider):
        def _run_demucs(self, audio_path, roles):
            passes["n"] += 1
            # Demucs would return whatever roles were asked; companion caching asks
            # for both pair members on a miss.
            return {r: (r.encode() + b"_wav") for r in roles}

    store = ArtifactStore(tmp_path / "L3")
    prov = _Counting(store=store, model_version="htdemucs")

    v = prov.separate("pcmX", "/fake.wav", ["vocals"])
    assert v["available"] and passes["n"] == 1
    # the companion was materialized by the SAME pass
    assert prov.has_stems("pcmX", ["vocals", "no_vocals"])

    nv = prov.separate("pcmX", "/fake.wav", ["no_vocals"])
    assert nv["available"] and nv["cached"] is True
    assert passes["n"] == 1, "requesting the companion must be a cache hit, not a 2nd inference"


def test_artifact_key_depends_on_the_full_recipe(tmp_path):
    """A different segment/overlap/precision must NOT collide with another recipe's
    cache. Changing a recipe field changes the key."""
    import os
    from earcrate.providers.stems import DemucsStemProvider
    prov = DemucsStemProvider(store=ArtifactStore(tmp_path / "L3"), model_version="htdemucs")
    base = prov._artifact_key("pcmX", "vocals")
    with patch.dict(os.environ, {"EARCRATE_DEMUCS_OVERLAP": "0.25"}):
        changed = prov._artifact_key("pcmX", "vocals")
    with patch.dict(os.environ, {"EARCRATE_DEMUCS_PRECISION": "amp_fp16"}):
        changed2 = prov._artifact_key("pcmX", "vocals")
    assert base != changed and base != changed2 and changed != changed2
