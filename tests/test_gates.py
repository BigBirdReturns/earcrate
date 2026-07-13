#!/usr/bin/env python3
"""Executable gates (rebuild plan §5). Run: python tests/run_gates.py"""
import sys, random
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from earcrate.deck.transform import plan_varispeed_transform
from earcrate.deck.lattice import score_bpm_lattice
from earcrate.ear.readiness import crate_readiness_audit, girl_talk_targets, endless_sustain
from earcrate.app import EarcrateCore


def _fast_analysis_fixture(job):
    """Deterministic feature payload for gates about DB identity, not DSP quality."""
    import numpy as np
    beats = np.arange(0.0, 8.0, 0.5, dtype=np.float32).tobytes()
    downbeats = np.arange(0.0, 8.0, 2.0, dtype=np.float32).tobytes()
    return {
        "ok": True, "file_id": job["file_id"], "pcm_sha": f"pcm_{job['file_id']}",
        "features": {
            "bpm": 120.0, "bpm_confidence": 1.0, "key_root": 0, "key_mode": 1,
            "key_confidence": 1.0, "loudness_lufs": -14.0, "energy": 0.2,
            "beats": beats, "downbeats": downbeats, "sections": [], "vocal_likelihood": 0.0,
        },
    }


def _fast_crate_fixture(job):
    """Deterministic atom metrics for gates about adoption/judgment persistence."""
    metrics = {"score": 0.8, "hook_score": 0.2, "bed_score": 0.7,
               "floor_score": 0.2, "bass_score": 0.2, "spark_score": 0.4}
    return {"path": job["path"], "error": None, "results": [
        {"loop_id": lp["id"], "metrics": metrics, "ear_role": "TEXTURE",
         "render_role": "texture", "status": "approved", "preview_path": None}
        for lp in job["loops"]
    ]}

def test_budget_knob_bites():
    # 130 -> 126.05 needs ~3.1% varispeed: inside the role ceiling (6.5%), outside a 2% user budget.
    # Keys held EQUAL so this probes the varispeed knob only. The previous key pair
    # (2 -> 0) passed only because a missing pitch_distance import had silently
    # disabled all key discipline (fixed in v0.7.2); under a working planner that
    # pair violates on residual pitch, which is not what this test is about.
    tight = plan_varispeed_transform("vocal", 130.0, 126.05, 2, 2, 2.0, None)
    loose = plan_varispeed_transform("vocal", 130.0, 126.05, 2, 2, None, None)
    assert tight["violation"] is not None and loose["violation"] is None

def test_lattice_prefers_cleaner_speed():
    pool = [{"role": "drum_anchor", "bpm": 120.19, "key_root": 0, "title": "A"},
            {"role": "bass", "bpm": 132.51, "key_root": 7, "title": "A"},
            {"role": "vocal", "bpm": 126.0, "key_root": 5, "title": "B"},
            {"role": "harmony", "bpm": 136.0, "key_root": 2, "title": "C"},
            {"role": "drum_anchor", "bpm": 125.0, "key_root": 0, "title": "D"}]
    lat = score_bpm_lattice(pool, 126.05, 0, None, None)
    assert lat["lattice"] and lat["best_bpm"] > 0

def test_readiness_honest_on_40_random():
    random.seed(1)
    pool = [{"role": random.choices(["full","harmony","texture","drum_anchor","bass","vocal"],
             weights=[40,25,15,8,7,5])[0], "bpm": random.choice([120,124,126,128,132]),
             "key_root": random.randint(0,11), "title": f"song_{i}"} for i in range(40)]
    a = crate_readiness_audit(pool, 126.05, 0, None, None, 120.0)
    assert a["ready"], "40 balanced random songs must be READY for a 2-min sketch"
    assert girl_talk_targets(120.0)["sample_events"] == 11

def test_intent_flips_winner():
    core = EarcrateCore.__new__(EarcrateCore)
    def mk(bars, dyn, n=8):
        return {"sections": [{"bars": bars, "type": ("drop" if (i/n) < dyn else "sustain"),
                "target_key": i % 4, "transition_in": {"type": "beatmatch_blend", "xfade_beats": 4},
                "layers": [{"role": "drum_anchor", "world": "bed", "source_track_key": f"t{i}a"},
                           {"role": "vocal", "world": "voice", "source_track_key": f"t{i}b"}]}
               for i in range(n)], "bpm": 126.0, "params": {}}
    hi = {"chaos": 90, "drama": 90, "vocal_density": 80, "genre_whiplash": 80}
    lo = {"chaos": 10, "drama": 10, "vocal_density": 30, "genre_whiplash": 20}
    ch, ca = mk(2, 0.5), mk(8, 0.0)
    ch["params"] = hi; ca["params"] = hi
    hi_win = core.score_arrangement(ch)["total"] > core.score_arrangement(ca)["total"]
    ch["params"] = lo; ca["params"] = lo
    lo_win = core.score_arrangement(ca)["total"] > core.score_arrangement(ch)["total"]
    assert hi_win and lo_win, "sliders must flip the winner"



def test_percussion_is_keyless_but_vocals_are_not():
    """v0.6.5 regression gate: drum breaks must not be key-gated (their key is
    analyzer noise); pitched roles keep dry-deck key discipline."""
    from earcrate.deck.transform import plan_varispeed_transform
    # same tempo, maximally hostile key distance (tritone)
    drum = plan_varispeed_transform("drum_anchor", 128.0, 128.0, 0, 6, 8.5, 2)
    voc = plan_varispeed_transform("vocal", 128.0, 128.0, 0, 6, 8.5, 2)
    assert not drum.get("violation"), f"drum should be keyless, got: {drum.get('violation')}"
    assert voc.get("violation"), "vocal at a tritone with no varispeed help must violate"

def test_identity_from_folders():
    """Untagged files must inherit identity from the Artist/Album folder
    convention, and 'Title by Artist' suffixes strip ONLY for the known artist."""
    from pathlib import Path
    from earcrate.librarian.ingest import _derive_identity
    root = Path("/lib")
    # the real-world case: artist folder + 'Title by the Artist.mp3', zero tags
    i = _derive_identity(Path("/lib/The Front Bottoms/Au Revoir (Adios) by the Front Bottoms.mp3"), {}, root)
    assert i["artist"] == "The Front Bottoms" and i["title"] == "Au Revoir (Adios)", i
    # Artist/Album/NN Title.ext, zero tags
    i = _derive_identity(Path("/lib/Radiohead/OK Computer/02 Paranoid Android.mp3"), {}, root)
    assert i["artist"] == "Radiohead" and i["album"] == "OK Computer" and i["track"] == 2, i
    # 'Stand by Me' must NOT be mangled: 'Me' is not the artist
    i = _derive_identity(Path("/lib/Ben E. King/Stand by Me.mp3"), {}, root)
    assert i["title"] == "Stand by Me" and i["artist"] == "Ben E. King", i
    # generic dump folders must not become artists
    i = _derive_identity(Path("/lib/New folder/mystery.mp3"), {}, root)
    assert i["artist"] == "Unknown Artist", i
    # embedded tags always beat folders
    i = _derive_identity(Path("/lib/WrongFolder/song.mp3"), {"artist": "Portishead", "title": "Glory Box"}, root)
    assert i["artist"] == "Portishead" and i["title"] == "Glory Box", i
    # ingested copies: batch scaffolding is skipped, and a 'by X' title naming the
    # inner folder promotes that folder from album to artist
    i = _derive_identity(Path("/lib/ingested/2026-07-10-001122-ABC123/seagate2tb/The Front Bottoms/Maps by the Front Bottoms.mp3"), {}, root)
    assert i["artist"] == "The Front Bottoms" and i["title"] == "Maps" and i["album"] == "Unknown Album", i


def test_taste_duration_and_vocal_count():
    """v0.7.4 regressions: (1) a target length must render near that length, not
    4x it; (2) the scorer must count vocals placed by role, not only the legacy
    two-world 'world' tag."""
    core = EarcrateCore.__new__(EarcrateCore)
    rng = random.Random(7)
    roles = ["VOX_HOOK", "VOX_VERSE", "DRUM_BREAK", "BASS_RIFF", "BED_CHORD", "TEXTURE", "VOX_SHOUT"]
    rolemap = {"VOX_HOOK": "vocal", "VOX_VERSE": "vocal", "VOX_SHOUT": "vocal",
               "DRUM_BREAK": "drum_anchor", "BASS_RIFF": "bass", "BED_CHORD": "harmony", "TEXTURE": "texture"}
    pool = []
    n = 0
    for src in range(40):
        key = rng.randint(0, 11); bpm = rng.choice([120, 122, 124, 126])
        for r in rng.sample(roles, 4):
            n += 1
            pool.append({"id": f"L{n}", "atom_id": f"A{n}", "ear_role": r, "role": rolemap[r],
                         "key_root": key, "bpm": bpm, "score": rng.uniform(0.5, 0.9),
                         "hook_score": rng.uniform(0.4, 0.9), "title": f"song_{src}",
                         "path": f"/m/song_{src}.mp3", "high_share": 0.3, "low_share": 0.2})
    arr = core.compose_taste_arrangement(list(pool), {"taste_profile": "girl_talk_v1", "target_seconds": 120, "bpm": 124}, seed=1340)
    bpm = float(arr["bpm"]); bars = sum(s["bars"] for s in arr["sections"])
    minutes = bars * 4 / bpm   # beats / (beats per minute) = minutes
    assert 1.6 <= minutes <= 2.4, f"120s target rendered {minutes:.2f} min ({bars} bars)"
    # vocals were placed AND the scorer sees them
    placed_vocals = sum(1 for s in arr["sections"] for ly in s["layers"] if ly.get("role") == "vocal")
    assert placed_vocals > 0, "no vocal layers placed"
    sc = core.score_arrangement(arr)
    assert sc["voice_layers"] > 0 and sc["realized_vocal"] > 0.0, f"scorer blind to vocals: {sc['voice_layers']}"


def test_librarian_identity_agrees_with_earcrate():
    """The standalone crate-librarian is destined to REPLACE earcrate's inline
    identity logic (rebuild plan v2). Until cutover, both must agree byte-for-byte
    on every canonical case — a drift here means one of them regressed."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crate-librarian"))
    from crate_librarian.identity import derive_identity as lib_id
    from earcrate.librarian.ingest import _derive_identity as ec_id
    cases = [
        ("/lib/The Front Bottoms/Au Revoir (Adios) by the Front Bottoms.mp3", {}),
        ("/lib/Radiohead/OK Computer/02 Paranoid Android.mp3", {}),
        ("/lib/Ben E. King/Stand by Me.mp3", {}),
        ("/lib/New folder/mystery.mp3", {}),
        ("/x/song.mp3", {"artist": "Portishead", "title": "Glory Box"}),
        ("/x/02_-_daft_punk_-_harder_better_faster_stronger.mp3", {}),
        ("/x/track03.mp3", {"artist": "portishead", "album": "DUMMY", "title": "Track 03", "tracknumber": "3"}),
        ("/x/esom.mp3", {"artist": "Jay-Z FEAT. Alicia Keys", "album": "The Blueprint 3", "title": "Empire State Of Mind"}),
        ("/lib/ingested/2026-07-10-001122-ABC/seagate/The Front Bottoms/Maps by the Front Bottoms.mp3", {}),
    ]
    keys = ("artist", "track_artist", "album", "title", "track", "year", "compilation")
    for path, tags in cases:
        a = lib_id(Path(path), tags, Path("/lib"))
        b = ec_id(Path(path), tags, Path("/lib"))
        assert {k: a[k] for k in keys} == {k: b[k] for k in keys}, f"identity drift on {path}: {a} vs {b}"


def test_personas_coexist_and_adopt():
    """v0.7.8 schema gate: ear atoms are per-(loop,profile) — building resident B
    must not destroy resident A; B ADOPTS A's persona-independent measurements
    (instant) instead of re-measuring; a locked human call survives force."""
    import tempfile, numpy as np, soundfile as sf
    from unittest.mock import patch
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sr = 44100
    for i in range(3):
        t = np.arange(sr * 8) / sr
        sf.write(str(tmp / "music" / f"s{i}.wav"), (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
    core = EarcrateCore()
    # This gate validates profile coexistence/adoption, not the worker pool.
    # Keep it serial so repeated native-library forks cannot destabilize CI.
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                    "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 10})
    core.scan()
    with patch("earcrate.app.analyze_file_worker", side_effect=_fast_analysis_fixture):
        core.analyze(force=True)
    with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)):
        core.extract_loops(auto_approve=True, force=True)
    with patch("earcrate.app.ear_crate_file_worker", side_effect=_fast_crate_fixture):
        r1 = core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
    r2 = core.build_ear_crate(taste_profile="troubadour_v1")
    assert r1["inserted"] > 0 and r1["adopted"] == 0
    assert r2["adopted"] == r2["inserted"] and r2["adopted"] > 0, "second resident must adopt, not re-measure"
    db = core.conn()
    gt = db.execute("SELECT COUNT(*) n FROM ear_atoms WHERE taste_profile='girl_talk_v1'").fetchone()["n"]
    tb = db.execute("SELECT COUNT(*) n FROM ear_atoms WHERE taste_profile='troubadour_v1'").fetchone()["n"]
    assert gt > 0 and gt == tb, "personas must coexist"
    aid = db.execute("SELECT id FROM ear_atoms WHERE taste_profile='girl_talk_v1' LIMIT 1").fetchone()["id"]
    core.set_atom_judgment(aid, "girl_talk_v1", "approved", relabel_role="VOX_SHOUT", locked=True)
    with patch("earcrate.app.ear_crate_file_worker", side_effect=_fast_crate_fixture):
        core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
    row = db.execute("SELECT ear_role, status FROM ear_atoms WHERE id=?", (aid,)).fetchone()
    assert row["ear_role"] == "VOX_SHOUT" and row["status"] == "approved", "locked call must survive force"
    # migration: an old-schema table (UNIQUE loop_id) must migrate and then accept both profiles
    db.executescript("DROP TABLE ear_atoms;")
    db.execute("""CREATE TABLE ear_atoms(
        id TEXT PRIMARY KEY, loop_id TEXT UNIQUE REFERENCES loops(id) ON DELETE CASCADE,
        file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
        taste_profile TEXT NOT NULL DEFAULT 'girl_talk_v1', ear_role TEXT NOT NULL, render_role TEXT NOT NULL,
        start_s REAL NOT NULL, end_s REAL NOT NULL, bars INTEGER NOT NULL, bpm REAL, key_root INTEGER,
        score REAL NOT NULL, hook_score REAL DEFAULT 0, bed_score REAL DEFAULT 0, floor_score REAL DEFAULT 0,
        bass_score REAL DEFAULT 0, spark_score REAL DEFAULT 0, intelligibility REAL DEFAULT 0,
        low_share REAL DEFAULT 0, mid_share REAL DEFAULT 0, high_share REAL DEFAULT 0,
        loopability REAL DEFAULT 0, transient_density REAL DEFAULT 0, phrase_position TEXT DEFAULT 'downbeat',
        status TEXT CHECK(status IN ('candidate','approved','rejected')) DEFAULT 'candidate',
        preview_path TEXT, metrics_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""")
    lid = db.execute("SELECT id FROM loops LIMIT 1").fetchone()["id"]
    fid = db.execute("SELECT file_id FROM loops WHERE id=?", (lid,)).fetchone()["file_id"]
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,created_at) VALUES('old1',?,?,?,?,?,0,4,2,0.7,'now')", (lid, fid, "girl_talk_v1", "VOX_HOOK", "vocal"))
    core.migrate_ear_atoms_per_profile()
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,created_at) VALUES('new1',?,?,?,?,?,0,4,2,0.7,'now')", (lid, fid, "troubadour_v1", "VOX_HOOK", "vocal"))
    both = db.execute("SELECT COUNT(*) n FROM ear_atoms WHERE loop_id=?", (lid,)).fetchone()["n"]
    assert both == 2, "migrated table must accept the same loop under two profiles"


def test_curation_steers_composer():
    """Loop closure: a favorited atom outranks a slightly better stranger, and a
    human-rejected pairing is a veto the composer obeys even over a favorite."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    (tmp / "music").mkdir(); (tmp / "work").mkdir(); (tmp / "agent").mkdir()
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
    db = core.conn()
    pool = []
    floor_ids = []
    n = 0
    for srci in range(5):
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES(?,?,?,?,?,?)",
                   (f"f{srci}", str(tmp / "music" / f"s{srci}.wav"), "master", 1, 1, "now"))
        core._set_pcm(f"f{srci}", f"pcm_fixture_{srci}")
        for ear, role in (("DRUM_BREAK", "drum_anchor"), ("TEXTURE", "texture")):
            n += 1
            aid = f"a{n}"
            db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"l{n}", f"f{srci}", 0, 4, 2, role, 0.7, "now"))
            db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (aid, f"l{n}", f"f{srci}", "girl_talk_v1", ear, role, 0, 4, 2, 0.7, "{}", "now"))
            pool.append({"id": f"l{n}", "atom_id": aid, "ear_role": ear, "role": role, "key_root": 0,
                         "bpm": 124.0, "score": 0.7, "hook_score": 0.3, "title": f"song_{srci}",
                         "path": f"/m/song_{srci}.mp3", "low_share": 0.2, "high_share": 0.3})
            if ear in {"DRUM_BREAK", "TEXTURE"}:
                floor_ids.append(aid)
    vocals = {}
    for aid, srci, score in (("v_good", 0, 0.60), ("v_fav", 1, 0.55)):
        n += 1
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (f"l{n}", f"f{srci}", 4, 8, 2, "vocal", score, "now"))
        db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (aid, f"l{n}", f"f{srci}", "girl_talk_v1", "VOX_HOOK", "vocal", 4, 8, 2, score, "{}", "now"))
        vocals[aid] = {"id": f"l{n}", "atom_id": aid, "ear_role": "VOX_HOOK", "role": "vocal", "key_root": 0,
                       "bpm": 124.0, "score": score, "hook_score": 0.8, "title": f"song_{srci}",
                       "path": f"/m/song_{srci}.mp3", "low_share": 0.1, "high_share": 0.4}
        pool.append(vocals[aid])
    db.commit()
    params = {"taste_profile": "girl_talk_v1", "target_seconds": 40, "bpm": 124}

    def first_vocal(arr):
        for sec in arr["sections"]:
            for ly in sec["layers"]:
                if ly.get("role") == "vocal":
                    return ly.get("atom_id")
        return None

    base = first_vocal(core.compose_taste_arrangement(list(pool), dict(params), seed=7))
    assert base == "v_good", f"baseline should pick the higher-scored vocal, got {base}"
    # favorite flips the pick
    core.set_atom_judgment("v_fav", "girl_talk_v1", "approved", favorite=True)
    fav = first_vocal(core.compose_taste_arrangement(list(pool), dict(params), seed=7))
    assert fav == "v_fav", f"favorite must outrank a slightly better stranger, got {fav}"
    # human rejection of every (v_fav, floor) pairing is a veto that beats the favorite
    for i, fid in enumerate(floor_ids):
        db.execute("INSERT INTO compatibility_edges(id,taste_profile,left_atom_id,right_atom_id,relation,score,reasons_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (f"e{i}", "girl_talk_v1", "v_fav", fid, "vocal_over_bed", 0.8, "{}", "now"))
        db.commit()
        core.set_pair_judgment(f"e{i}", "girl_talk_v1", "rejected", "clashes")
    vetoed = core.compose_taste_arrangement(list(pool), dict(params), seed=7)
    used = {ly.get("atom_id") for sec in vetoed["sections"] for ly in sec["layers"] if ly.get("role") == "vocal"}
    assert "v_fav" not in used, "a rejected pairing must never be composed, favorite or not"
    assert "v_good" in used, "the non-vetoed vocal should carry the foreground"


def test_persona_single_source():
    """The versioned TasteSpec JSON is the ONLY source of persona numbers, and it
    must state the values the engine actually ENFORCES — a profile that promises
    looser budgets than the deck allows is a lie in both directions."""
    from earcrate.tastespec import load_tastespec, flat_profile
    from earcrate.core.deps import TASTE_PROFILES
    from earcrate.deck.transform import drydeck_transform_limits
    from earcrate.ear.readiness import GT_RANK_WEIGHTS
    from earcrate.tastespec import available_profiles
    profs = available_profiles()
    assert "girl_talk_v1" in profs and "troubadour_v1" in profs and "notorious_v1" in profs
    for pid in profs:
        prof = load_tastespec(pid)
        # 1. the runtime flat profile IS the JSON projection (no shadow literal)
        assert TASTE_PROFILES[pid] == flat_profile(prof), f"{pid} projection drift"
        assert TASTE_PROFILES[pid]["tastespec_hash"] == prof["hash"]
        # 2. profile transform budgets == enforced deck limits, role for role
        for role, decl in (prof["transform_budgets"]["roles"]).items():
            enforced = drydeck_transform_limits(role)
            assert abs(decl["varispeed_pct"] - enforced["varispeed"]) < 1e-9, f"{pid}/{role} varispeed drift"
            assert abs(decl["residual_pitch"] - enforced["residual_pitch"]) < 1e-9, f"{pid}/{role} pitch drift"
        # 3. every relation threshold == the profile's own edge floor
        for rel, spec in prof["compatibility_relations"].items():
            assert abs(spec["min_score"] - prof["min_edge_score"]) < 1e-9, f"{pid}/{rel} threshold drift"
        # 4. ranking weights carry the exact five priorities and sum to 1
        w = prof["objective_weights"]
        assert set(w) == {"recognizability", "role_clarity", "danceability", "deck_feasibility", "contrast"}, pid
        assert abs(sum(w.values()) - 1.0) < 1e-9, f"{pid} weights must sum to 1"
    # girl_talk remains the default whose weights feed the module-level ranker
    assert GT_RANK_WEIGHTS == load_tastespec("girl_talk_v1")["objective_weights"]


def test_girl_talk_ranking():
    """The persona ranker must reach for a recognizable, clean, on-tempo hook
    before a mushy off-tempo bed, and expose the five sub-scores as receipts."""
    from earcrate.ear.readiness import rank_material, GT_RANK_WEIGHTS
    assert abs(sum(GT_RANK_WEIGHTS.values()) - 1.0) < 1e-9, "rank weights must sum to 1"
    atoms = [
        {"atom_id": "hit", "ear_role": "VOX_HOOK", "hook_score": 0.95, "score": 0.9,
         "intelligibility": 0.9, "mid_share": 0.6, "bpm": 124, "key_root": 0, "energy": 0.8, "transient_density": 0.5},
        {"atom_id": "mush", "ear_role": "BED_CHORD", "hook_score": 0.2, "score": 0.4,
         "floor_score": 0.3, "bpm": 171, "key_root": 6, "energy": 0.3, "transient_density": 0.2},
        {"atom_id": "break", "ear_role": "DRUM_BREAK", "hook_score": 0.3, "score": 0.7,
         "transient_density": 0.9, "low_share": 0.5, "bpm": 124, "key_root": 0, "energy": 0.85},
    ]
    r = rank_material(atoms, tempo_islands=[124])
    assert r["ranked"][0]["atom_id"] == "hit", "recognizable hook must rank first"
    assert r["ranked"][-1]["atom_id"] == "mush", "off-tempo mush must rank last"
    assert set(r["ranked"][0]["why"]) == set(GT_RANK_WEIGHTS), "receipt must expose every sub-score"
    # deck feasibility must dominate: the off-tempo loop is unusable regardless of contrast
    assert r["ranked"][-1]["why"]["deck_feasibility"] == 0.0


def test_endless_math_is_exact():
    """Persona endless-set gate: T = min(60*S/r, E*seconds_per_event); endless
    iff T clears the recycle gap. Numbers must be exact, not vibes."""
    # 55 sources at 5.5/min = exactly 600s no-repeat; below the 900s gap -> not endless.
    e = endless_sustain(event_capacity=10_000, source_capacity=55)
    assert e["no_repeat_seconds"] == 600.0 and e["bottleneck"] == "sources" and not e["endless_ready"], e
    # the audit must state the exact source count that unlocks endless: ceil(900/60*5.5)=83
    assert e["sources_needed_for_endless"] == 83, e
    e2 = endless_sustain(event_capacity=10_000, source_capacity=83)
    assert e2["endless_ready"] and e2["no_repeat_seconds"] >= 900.0, e2
    # event-starved crate: 10 events * 11s = 110s regardless of source count
    e3 = endless_sustain(event_capacity=10, source_capacity=1000)
    assert e3["no_repeat_seconds"] == 110.0 and e3["bottleneck"] == "events", e3
    # readiness audit must carry the endless receipt
    pool = [{"role": r, "bpm": 125.0, "key_root": 0, "title": f"s{i}"}
            for i, r in enumerate(["drum_anchor", "vocal", "bass", "full", "harmony"] * 4)]
    a = crate_readiness_audit(pool, 125.0, 0, None, None, 120.0)
    assert "endless" in a and a["endless"]["no_repeat_seconds"] > 0


def test_force_rebuild_preserves_judgments():
    """v3 keystone gate (Lesson #7 — the front of the debt queue): a forced loop
    rebuild must RE-MEASURE IN PLACE, never delete+reinsert. The old
    `extract_loops(force=True)` ran `DELETE FROM loops`, which cascaded through
    `ear_atoms` into `atom_judgments` and silently destroyed human judgment — the
    locked keeps and favorites that only real listening produces. v3 gives every
    loop a DETERMINISTIC segment identity, so the id is stable across rebuilds and
    the atoms/judgments keyed off it survive by construction. This gate is red on
    the delete+reinsert code and green on the upsert code."""
    import tempfile, numpy as np, soundfile as sf
    from unittest.mock import patch
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sr = 44100
    for i in range(3):
        t = np.arange(sr * 8) / sr
        sf.write(str(tmp / "music" / f"s{i}.wav"), (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                    "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 10})

    def _fake_analysis(job):
        beats = np.arange(0.0, 8.0, 0.5, dtype=np.float32).tobytes()
        downbeats = np.arange(0.0, 8.0, 2.0, dtype=np.float32).tobytes()
        return {
            "ok": True, "file_id": job["file_id"], "pcm_sha": f"pcm_{job['file_id']}",
            "features": {
                "bpm": 120.0, "bpm_confidence": 1.0, "key_root": 0, "key_mode": 1,
                "key_confidence": 1.0, "loudness_lufs": -14.0, "energy": 0.2,
                "beats": beats, "downbeats": downbeats, "sections": [], "vocal_likelihood": 0.0,
            },
        }

    def _fake_crate(job):
        metrics = {"score": 0.8, "hook_score": 0.2, "bed_score": 0.7,
                   "floor_score": 0.2, "bass_score": 0.2, "spark_score": 0.4}
        return {"path": job["path"], "error": None, "results": [
            {"loop_id": lp["id"], "metrics": metrics, "ear_role": "TEXTURE",
             "render_role": "texture", "status": "approved", "preview_path": None}
            for lp in job["loops"]
        ]}

    core.scan()
    with patch("earcrate.app.analyze_file_worker", side_effect=_fake_analysis):
        core.analyze(force=True)
    with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)), \
         patch("earcrate.app.ear_crate_file_worker", side_effect=_fake_crate):
        core.extract_loops(auto_approve=True, force=True)
        core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
    db = core.conn()
    # identity is content-derived, not a random ulid: id == segment_id, prefix 'seg_'
    loops = db.execute("SELECT id, segment_id FROM loops").fetchall()
    assert loops, "fixture produced no loops"
    assert all(r["id"] == r["segment_id"] and str(r["id"]).startswith("seg_") for r in loops), \
        "every loop must carry a deterministic segment identity"
    # a human locks a keep, favorites it, relabels the role — the sacred call
    aid = db.execute("SELECT id FROM ear_atoms WHERE taste_profile='girl_talk_v1' LIMIT 1").fetchone()["id"]
    core.set_atom_judgment(aid, "girl_talk_v1", "approved", relabel_role="VOX_SHOUT", favorite=True, locked=True)
    j_before = db.execute("SELECT COUNT(*) n FROM atom_judgments").fetchone()["n"]
    loops_before = {r["id"] for r in db.execute("SELECT id FROM loops").fetchall()}
    assert j_before > 0
    # THE FORCE REBUILD — under delete+reinsert this cascades the judgment to death
    with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)), \
         patch("earcrate.app.ear_crate_file_worker", side_effect=_fake_crate):
        core.extract_loops(auto_approve=True, force=True)
        core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
    # no loop id was dropped: force is an upsert, so every atom's anchor still exists
    assert {r["id"] for r in db.execute("SELECT id FROM loops").fetchall()} >= loops_before, \
        "force rebuild dropped a loop id — it deleted instead of upserting"
    j_after = db.execute("SELECT COUNT(*) n FROM atom_judgments").fetchone()["n"]
    assert j_after >= j_before, f"force rebuild destroyed judgments: {j_before} -> {j_after} (Lesson #7)"
    row = db.execute("SELECT status, favorite, locked, relabel_role FROM atom_judgments WHERE atom_id=? AND taste_profile='girl_talk_v1'", (aid,)).fetchone()
    assert row is not None, "the locked human judgment was erased by force-rebuild"
    assert row["locked"] == 1 and row["favorite"] == 1 and row["status"] == "approved" and row["relabel_role"] == "VOX_SHOUT", \
        "the judgment survived but was mangled by the rebuild"
    assert db.execute("SELECT 1 FROM ear_atoms WHERE id=?", (aid,)).fetchone() is not None, \
        "the atom the judgment points at must survive with a stable id"


def test_singlefile_cli_smoke():
    """Ledger #13: the SHIPPED artifact (dist/earcrate.py), not the package, is
    the thing users run — so it must be gated by DRIVING THE BUILT FILE as a
    subprocess. The single-file builder strips only column-0 `from earcrate. ...`
    imports; an INDENTED in-function `from earcrate.<pkg> import ...` survives into
    dist and crashes at runtime with "'earcrate' is not a package" — while the
    package/self-test gates stay green (self-test never hit that content path).
    This gate builds the file and pushes it through a real audio path that
    historically crashed (deepclean -> assess_track_audio -> decode_audio):
    self-test must print SELF_TEST_OK, and configure+deepclean must exit 0 with
    NO import/package tracebacks anywhere in stdout/stderr."""
    import os, subprocess, sys, tempfile, shutil
    from pathlib import Path
    import numpy as np, soundfile as sf

    root = Path(__file__).resolve().parent.parent
    py = sys.executable
    dist = root / "dist" / "earcrate.py"
    forbidden = ("is not a package", "ModuleNotFoundError", "ImportError", "Traceback")

    def _clean(*procs):
        for p in procs:
            blob = (p.stdout or "") + (p.stderr or "")
            for bad in forbidden:
                assert bad not in blob, f"shipped artifact leaked '{bad}':\n{blob[-800:]}"

    # 1) Build the single file from the package.
    b = subprocess.run([py, str(root / "build" / "make_singlefile.py")],
                       capture_output=True, text=True, timeout=300)
    assert b.returncode == 0, f"single-file build failed: {b.stderr[-800:]}"
    assert dist.exists(), "build did not produce dist/earcrate.py"

    # 2) Self-test on the BUILT artifact must pass.
    t = subprocess.run([py, str(dist), "--self-test"],
                       capture_output=True, text=True, timeout=600)
    _clean(t)
    assert "SELF_TEST_OK" in (t.stdout + t.stderr), \
        f"built artifact self-test did not print SELF_TEST_OK:\n{(t.stdout + t.stderr)[-800:]}"

    # 3) Real content path that used to crash on an in-function import.
    d = Path(tempfile.mkdtemp())
    try:
        music = d / "music"; music.mkdir()
        ws = d / "ws"; home = d / "home"; home.mkdir()
        sr = 22050
        for i in range(2):  # tiny real songs so assess_track_audio -> decode_audio runs
            tt = np.linspace(0, 2, sr * 2, endpoint=False)
            y = (0.2 * np.sin(2 * np.pi * (220 + 40 * i) * tt)).astype("float32")
            sf.write(str(music / f"t{i}.wav"), y, sr)
        env = dict(os.environ)
        env["EARCRATE_HOME"] = str(home)  # isolate the app-global workspace pointer

        cfg = subprocess.run([py, str(dist), "configure", "--music", str(music),
                              "--workspace", str(ws), "--analysis-seconds", "8"],
                             capture_output=True, text=True, timeout=300, env=env)
        _clean(cfg)
        assert cfg.returncode == 0, f"configure exited {cfg.returncode}:\n{cfg.stderr[-800:]}"

        dc = subprocess.run([py, str(dist), "deepclean", "--root", str(music)],
                            capture_output=True, text=True, timeout=600, env=env)
        _clean(dc)
        assert dc.returncode == 0, f"deepclean exited {dc.returncode}:\n{dc.stderr[-800:]}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fresh_clone_has_no_runtime_state():
    """Ledger #14: a fresh clone must adopt NOTHING implicitly. No runtime pointer
    (earcrate_workspace.json / any *_workspace.json), no config.json, no
    .deps_installed, no *.sqlite / *.db / *.npz, and no file under
    cache|workspace|agent|dist may be git-tracked. If one were, a fresh
    EarcrateCore would auto-adopt stale config and conjure a phantom legacy source.
    .gitignore must also carry the patterns that keep these out. Red the moment any
    such file is tracked or a required ignore pattern goes missing."""
    import os, re, subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tracked = subprocess.run(["git", "-C", root, "ls-files"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    assert tracked, "git ls-files returned nothing — not a git checkout?"

    def is_runtime_state(path):
        base = os.path.basename(path)
        if base in ("earcrate_workspace.json", "config.json", ".deps_installed"):
            return True
        if base.endswith("_workspace.json"):
            return True
        if re.search(r"\.(sqlite|db|npz)$", base):
            return True
        dirs = path.split("/")[:-1]
        if any(d in ("cache", "workspace", "agent", "dist") for d in dirs):
            return True
        return False

    offenders = [p for p in tracked if is_runtime_state(p)]
    assert not offenders, \
        f"runtime-state files are git-tracked (a fresh clone would adopt them): {offenders}"

    # .gitignore must keep these out so they are never re-added by accident.
    with open(os.path.join(root, ".gitignore"), "r", encoding="utf-8") as fh:
        gi = fh.read()
    ignored = {ln.strip() for ln in gi.splitlines()
               if ln.strip() and not ln.strip().startswith("#")}
    for pat in ("earcrate_workspace.json", "config.json", "*.sqlite", "*.npz",
                "dist/", "workspace/", "agent/", ".deps_installed"):
        assert pat in ignored, f".gitignore is missing required pattern: {pat!r}"


def test_all_mutations_dry_run_default():
    """Ledger #15: EVERY mutating/reversal op is dry-run-default and apply/
    signature-gated. Calling each WITHOUT apply (and without an approved
    signature) must either report dry_run or refuse (ok False), and must NOT
    touch the music library on disk. Guards the footgun class where a reversal
    (identify-rollback) or an apply (workspace migration) fires writes
    immediately instead of previewing first."""
    import tempfile, os, json, hashlib
    from pathlib import Path
    import numpy as np, soundfile as sf
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        music = tmp / "music"; music.mkdir()
        def wav(p):
            p.parent.mkdir(parents=True, exist_ok=True)
            t = np.arange(44100) / 44100
            sf.write(str(p), (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        wav(music / "Aphex Twin - Xtal.flac")   # FLAC so tag-rewriting reversals actually bite
        wav(music / "Boards of Canada - Olson.flac")
        ext = tmp / "external"; wav(ext / "incoming.flac")  # a source folder to ingest FROM
        libfile = music / "Aphex Twin - Xtal.flac"

        def snap(d):
            out = {}
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    b = p.read_bytes()
                    out[str(p.relative_to(d))] = (len(b), hashlib.sha256(b).hexdigest())
            return out

        core = EarcrateCore()
        core.configure({"master_root": str(music), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 8})
        core.scan()
        agent = tmp / "agent"

        def refused_or_dry(resp, label):
            assert isinstance(resp, dict), f"{label}: non-dict response {resp!r}"
            assert resp.get("dry_run") or resp.get("ok") is False, \
                (f"{label}: mutating op executed WITHOUT apply/signature "
                 f"(dry_run={resp.get('dry_run')!r}, ok={resp.get('ok')!r}): {resp}")

        # ingest writes a manifest even on a dry-run -> reuse it to exercise execute_manifest
        ing = core.ingest_sources({"sources": [str(ext)], "apply": False})
        refused_or_dry(ing, "ingest_sources")
        manifest_path = ing.get("manifest")

        # a reorg journal pointing at a real library file, so its rollback has work to preview
        rj = agent / "fake_reorg.jsonl"; rj.parent.mkdir(parents=True, exist_ok=True)
        rj.write_text(json.dumps({"from": str(libfile), "restore_to": str(music / "moved_back.flac")}) + "\n", encoding="utf-8")
        # an identify journal whose replay WOULD overwrite a real library file's tags
        ij = agent / "fake_identify.jsonl"
        ij.write_text(json.dumps({"path": str(libfile), "new": {"artist": "OVERWRITTEN"},
                                  "old": {"artist": "Restored By Rollback"}}) + "\n", encoding="utf-8")
        proposals = [{"path": str(libfile), "artist": "Somebody", "title": "Something", "score": 0.99}]
        ws = str(tmp / "ws")

        before = snap(music)
        calls = [
            ("reorganize_source",        lambda: core.reorganize_source({"apply": False})),
            ("rollback_reorganize",      lambda: core.rollback_reorganize({"journal": str(rj), "apply": False})),
            ("plan_workspace_migration", lambda: core.plan_workspace_migration({"music_folder": str(music), "workspace_folder": ws, "sources": []})),
            ("apply_workspace_migration",lambda: core.apply_workspace_migration({"music_folder": str(music), "workspace_folder": ws, "sources": []})),
            ("apply_identities",         lambda: core.apply_identities({"proposals": proposals, "apply": False})),
            ("rollback_identities",      lambda: core.rollback_identities({"journal": str(ij)})),
            ("ingest_sources",           lambda: core.ingest_sources({"sources": [str(ext)], "apply": False})),
            ("organize_and_retag",       lambda: core.organize_and_retag({"apply": False})),
            ("rollback_outputs",         lambda: core.rollback_outputs()),
        ]
        if manifest_path:
            calls.append(("execute_manifest", lambda: core.execute_manifest(str(manifest_path), apply=False)))
        for label, fn in calls:
            refused_or_dry(fn(), label)

        after = snap(music)
        assert after == before, \
            f"music library was MUTATED by a dry-run/reversal call: before={before} after={after}"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_done_requires_receipt():
    """Ledger #16 (honesty invariant): a 'done/seeded' completion claim is only
    trustworthy if it leaves a RECEIPT artifact on disk that a third party can
    read back independently to confirm the claimed outcome. We exercise a real,
    bounded completion path — seed_demo_renders — which reports ok/seeded=N and
    must persist one *.render_report.json receipt per render recording the
    quality-gate result. The gate re-opens each receipt and confirms the claim;
    a done-claim with no matching receipt (regression) fails here."""
    import tempfile, os, json
    from pathlib import Path
    from earcrate.app import EarcrateCore, ENGINE_VERSION
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 2, "analysis_seconds": 8})
        res = core.seed_demo_renders(count=2, bars=2, bpm=100)
        # (1) the operation must CLAIM completion.
        assert res.get("ok") is True, res
        claimed = int(res.get("seeded") or 0)
        assert claimed == 2, res
        renders = Path(res["dir"])
        # (2) every claimed render must exist AS AUDIO on disk.
        wavs = sorted(renders.glob("*.wav"))
        assert len(wavs) == claimed, f"claimed {claimed} but {len(wavs)} wavs on disk"
        # (3) the honesty check: an INDEPENDENTLY-readable receipt per claimed render,
        # re-opened here, must corroborate the outcome the operation reported.
        receipts = sorted(renders.glob("*.render_report.json"))
        assert len(receipts) == claimed, f"claimed seeded={claimed} but {len(receipts)} receipts on disk"
        for wav in wavs:
            rp = wav.with_suffix(".render_report.json")
            assert rp.exists(), f"no receipt for claimed render {wav.name}"
            rec = json.loads(rp.read_text(encoding="utf-8"))
            assert rec.get("engine_version") == ENGINE_VERSION, rec
            assert rec.get("quality_gate", {}).get("passed") is True, rec
            assert rec.get("render_timestamp"), rec
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_plan_math_pins_composition_arithmetic():
    """§5.3 / Lesson #1: all composition math is ONE pure source in earcrate.plan.

    Pins the exact constants app.py used to inline (sources_needed, the readiness
    need{} dict, target-length bars) against known values. A wrong constant here
    fails the gate. Also proves app.py DELEGATES to these pure fns rather than
    keeping a second copy of the arithmetic, so the two can never drift.
    """
    import math
    from earcrate.plan.math import (readiness_scale, sources_needed,
                                     readiness_need, bars_exact, target_bars)

    # sources_needed = max(5, ceil(target/source)); the documented example.
    assert sources_needed(120, 11.5) == 11
    assert sources_needed(120.0, 11.5) == 11
    # floor of 5 sources for very short targets.
    assert sources_needed(10, 11.5) == 5
    assert sources_needed(240, 11.5) == 21  # ceil(240/11.5)=21
    assert sources_needed(60, 20.0) == 5    # ceil(3)=3 -> floored to 5

    # readiness scale is clamp(target/120, 0.5, 1.2).
    assert readiness_scale(120.0) == 1.0
    assert readiness_scale(30.0) == 0.5     # 0.25 clamped up
    assert readiness_scale(600.0) == 1.2    # 5.0 clamped down

    # The readiness need{} dict at the 2-min reference matches the documented
    # targets, in the contract key order the audit iterates.
    need120 = readiness_need(120.0)
    assert list(need120.keys()) == ["foreground", "floor", "bass", "spark", "sources"]
    assert need120 == {"foreground": 12, "floor": 16, "bass": 6, "spark": 12, "sources": 11}
    # A 4-min target scales the per-role needs up (scale clamped at 1.2).
    assert readiness_need(240.0) == {"foreground": 15, "floor": 20, "bass": 8, "spark": 15, "sources": 21}
    # A very short target hits the per-role floors, not zero.
    assert readiness_need(30.0) == {"foreground": 6, "floor": 8, "bass": 3, "spark": 6, "sources": 5}

    # target length -> bars: bars = target*bpm/60/4, snapped to nearest 4-bar
    # phrase with a 16-bar floor. A 120s/124bpm target maps to 64 bars.
    assert bars_exact(120.0, 124.0) == 62.0
    assert target_bars(120.0, 124.0) == 64
    assert target_bars(120.0, 126.0) == 64
    assert target_bars(10.0, 120.0) == 16   # floored to 16 bars

    # DELEGATION: app.py must import these pure fns (single source), not re-inline
    # the arithmetic. If any call site kept an inline copy, this drift-guard fails.
    import inspect
    from earcrate.app import EarcrateCore
    src = inspect.getsource(EarcrateCore)
    assert "sources_needed(" in src, "app.py no longer delegates sources_needed"
    assert "readiness_need(" in src, "app.py no longer delegates readiness_need"
    assert "target_bars(" in src, "app.py no longer delegates target_bars"
    # No stale inline copy of the source-count formula remains in the core.
    assert "math.ceil(target_seconds / float(profile" not in src, \
        "app.py still inlines the sources_needed arithmetic (drift risk)"


def test_provider_seams():
    """EARCRATE v3 §3 provider seams (§5.2 stems, §5.4 retrieval, §5.3-L3 store).

    Core reaches capability THROUGH a registered seam, never around it:
      - the DEFAULT StemProvider is a no-op that is core-safe with torch absent
        (reports unavailable, never crashes, touches no heavy deps);
      - DemucsStemProvider imports & constructs fine with torch absent and
        raises a CLEAR actionable RuntimeError (not a bare ImportError) only
        when actually used;
      - LinearScanIndex returns the TRUE nearest on a tiny labeled set;
      - FullScanRetriever returns every candidate; NoopEmbeddingProvider never
        fabricates a vector;
      - ArtifactStore.evict drops ephemeral before warm and NEVER pinned, and
        artifacts carry the provenance shape (source_identity/provider/version).
    """
    import tempfile
    from unittest.mock import patch
    import earcrate.providers as P
    from earcrate.providers import (
        ArtifactStore, StemProvider, NoopStemProvider, DemucsStemProvider,
        FullScanRetriever, NoopEmbeddingProvider, LinearScanIndex,
    )

    # --- registry hands back the right DEFAULTS ------------------------------
    assert P.default_name("stems") == "noop"
    assert P.default_name("retriever") == "fullscan"
    assert P.default_name("embedding") == "noop"
    assert P.default_name("vector_index") == "linear"
    assert P.default_name("artifacts") == "local"

    # --- §5.2 default StemProvider is no-op and core-safe --------------------
    stem = P.get("stems")
    assert isinstance(stem, NoopStemProvider) and isinstance(stem, StemProvider)
    res = stem.separate("pcm_sha_deadbeef", "/nonexistent/audio.wav", ["vocals", "drums"])
    assert res["available"] is False, "DEFAULT stem provider must report stems UNAVAILABLE"
    assert res["stems"] == {} and res["provider"] == "noop"
    assert res.get("reason"), "no-op must explain why stems are unavailable"
    assert res["pcm_sha"] == "pcm_sha_deadbeef"

    # --- §5.2 DemucsStemProvider: guarded import; clear error only on use -----
    demucs = P.get("stems", "demucs")   # constructing must NOT need torch
    assert isinstance(demucs, DemucsStemProvider)
    raised = None
    with patch.dict(sys.modules, {"torch": None, "demucs": None, "demucs.separate": None}):
        try:
            demucs.separate("pcm_sha_deadbeef", "/nonexistent/audio.wav", ["vocals"])
        except ImportError as e:  # a bare ImportError leaking out is a seam failure
            raised = ("import", e)
        except RuntimeError as e:
            raised = ("runtime", e)
    assert raised is not None and raised[0] == "runtime", \
        "Demucs use without torch must raise a CLEAR RuntimeError, not a bare ImportError"
    msg = str(raised[1]).lower()
    assert "demucs" in msg and "torch" in msg, "the error must actionably name torch+demucs"

    # --- §5.4 LinearScanIndex returns the TRUE nearest -----------------------
    idx = P.get("vector_index")
    assert isinstance(idx, LinearScanIndex)
    idx.add("east", [1.0, 0.0])
    idx.add("north", [0.0, 1.0])
    idx.add("near_east", [0.98, 0.05])
    idx.add("west", [-1.0, 0.0])
    cos = idx.query([1.0, 0.0], k=2, metric="cosine")
    assert cos[0][0] == "east" and cos[1][0] == "near_east", "cosine true nearest wrong"
    assert idx.query([1.0, 0.0], k=1, metric="cosine")[0][0] == "east"
    l2 = idx.query([0.95, 0.02], k=1, metric="l2")
    assert l2[0][0] == "near_east", "L2 true nearest wrong"

    # --- §5.4 FullScanRetriever returns all; NoopEmbedder fabricates nothing --
    retr = P.get("retriever")
    assert isinstance(retr, FullScanRetriever)
    catalog = [{"id": i} for i in range(7)]
    assert retr.retrieve(catalog) == catalog, "full scan must return EVERY candidate"
    emb = P.get("embedding")
    assert isinstance(emb, NoopEmbeddingProvider)
    assert emb.embed({"id": 1}) is None, "no-op embedder must never fabricate a vector"

    # --- §5.3 L3 ArtifactStore: provenance + tiered eviction -----------------
    store = ArtifactStore(tempfile.mkdtemp())
    store.put("eph", b"e" * 100, tier="ephemeral",
              source_identity="pcm_sha_deadbeef", provider="demucs", version="htdemucs_v4")
    store.put("wrm", b"w" * 100, tier="warm",
              source_identity="pcm_sha_deadbeef", provider="demucs", version="htdemucs_v4")
    store.put("pin", b"p" * 100, tier="pinned",
              source_identity="pcm_sha_deadbeef", provider="demucs", version="htdemucs_v4")
    assert store.total_bytes() == 300

    got = store.get("pin")
    assert got is not None and got["data"] == b"p" * 100
    meta = got["meta"]
    for field in ("source_identity", "provider", "version", "tier", "key", "bytes"):
        assert field in meta, "provenance shape missing %r" % (field,)
    assert meta["source_identity"] == "pcm_sha_deadbeef" and meta["provider"] == "demucs"
    assert meta["version"] == "htdemucs_v4"

    # Budget 150: ephemeral goes first, then warm; pinned survives.
    evicted = store.evict(150)
    assert evicted == ["eph", "wrm"], "must drop ephemeral BEFORE warm, in that order"
    assert store.get("eph") is None and store.get("wrm") is None
    assert store.get("pin") is not None, "eviction must NEVER drop a pinned artifact"

    # Even a budget below the pinned bytes leaves pinned intact (inviolable).
    assert store.evict(1) == [], "pinned is inviolable even under a tighter budget"
    assert store.get("pin") is not None


def _v3_build_render_pool(core, db, tmp, sr=44100, bpm=120.0, n=6):
    """Shared fixture for the §5.5 render gate: real decodable wavs plus a
    controlled in-DB pool (uniform bpm/key so the deck is always feasible —
    the composer's transform-feasibility gate is deterministic but sensitive to
    synthetic-audio analysis, and this gate is about RENDER identity, not the
    feasibility heuristic). Returns the composer pool list."""
    import numpy as np, soundfile as sf
    from pathlib import Path
    from earcrate.core.util import sha256_file
    pool = []
    roleplan = [("DRUM_BREAK", "drum_anchor"), ("BED_CHORD", "harmony"), ("VOX_HOOK", "vocal"),
                ("BASS_RIFF", "bass"), ("VOX_VERSE", "vocal"), ("TEXTURE", "texture")]
    for i in range(n):
        p = Path(tmp) / "music" / f"s{i}.wav"
        t = np.arange(int(sr * 8)) / sr
        sf.write(str(p), (0.3 * np.sin(2 * np.pi * (180 + 40 * i) * t)
                          + 0.2 * np.sin(2 * np.pi * 2 * (180 + 40 * i) * t)).astype(np.float32), sr)
        st = p.stat()
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,sha256,scanned_at) VALUES(?,?,?,?,?,?,?)",
                   (f"f{i}", str(p), "master", int(st.st_size), int(st.st_mtime_ns), sha256_file(p), "now"))
        core._set_pcm(f"f{i}", f"pcm_f{i}")
        db.execute("INSERT INTO features(file_id,bpm,key_root,analyzer_version,analyzed_at) VALUES(?,?,?,?,?)",
                   (f"f{i}", bpm, 0, "av", "now"))
        ear, role = roleplan[i % len(roleplan)]
        lid, aid = f"l{i}", f"a{i}"
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (lid, f"f{i}", 0.0, 4.0, 2, role, 0.7, "now"))
        db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (aid, lid, f"f{i}", "girl_talk_v1", ear, role, 0.0, 4.0, 2, 0.7, "{}", "now"))
        pool.append({"id": lid, "atom_id": aid, "ear_role": ear, "role": role, "key_root": 0, "bpm": bpm,
                     "score": 0.7, "hook_score": 0.6, "title": f"s{i}", "path": str(p),
                     "low_share": 0.2, "high_share": 0.3})
    db.commit()
    return pool


def test_saved_plan_renders_identically():
    """v3 §5.5 (exact render): a saved plan is a DETERMINISTIC contract. Compose ->
    save_plan -> load_plan must re-derive the IDENTICAL arrangement_sha (a saved
    plan that cannot reproduce its own hash is an invariant failure), the composer
    is deterministic under a fixed seed, rendering the loaded plan twice yields
    byte-identical AUDIO SAMPLES (file bytes differ only by the embedded
    render_timestamp metadata chunk, so we compare decoded samples), the render
    receipt is bound to the plan (report arrangement_sha == plan_hash), and a
    layer that CANNOT render is accounted for as a reasoned drop receipt — never a
    silent skip: a fully valid plan drops nothing, an un-renderable layer appears
    in report['drops'] with a reason."""
    import tempfile, os, json, copy
    from unittest.mock import patch
    from pathlib import Path
    import numpy as np, soundfile as sf
    from earcrate.app import EarcrateCore, ENGINE_VERSION
    from earcrate.core.util import arrangement_sha, ulidish, now_utc
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        bpm = 120.0
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 8})
        db = core.conn()
        pool = _v3_build_render_pool(core, db, tmp, bpm=bpm)
        params = {"taste_profile": "girl_talk_v1", "target_seconds": 16, "bpm": bpm, "quality_mode": "stable_deck"}

        def _assert_run_bundle(result, expected_state):
            run_dir = Path(result["run_bundle"])
            assert run_dir.parent == (tmp / "agent" / "runs").resolve()
            docs = {}
            for name in ("request.json", "plan.json", "status.json", "report.json"):
                path = run_dir / name
                assert path.is_file(), f"missing durable run artifact {path}"
                docs[name] = json.loads(path.read_text(encoding="utf-8"))
                assert docs[name]["run_id"] == result["run_id"]
            assert docs["status.json"]["state"] == expected_state
            assert docs["report.json"]["state"] == expected_state
            return docs

        # 1) The composer is deterministic under a fixed seed.
        arr = core.compose_taste_arrangement(list(pool), dict(params), seed=11)
        arr_again = core.compose_taste_arrangement(list(pool), dict(params), seed=11)
        assert arr.get("sections"), "composer produced an empty arrangement"
        # Rendering is the expensive part of this gate. Compact the deterministic
        # plan to one bar/two layers while preserving the exact same save/load and
        # selected-event contracts under test.
        for candidate in (arr, arr_again):
            candidate["sections"] = candidate["sections"][:1]
            candidate["sections"][0]["bars"] = 1
            candidate["sections"][0]["layers"] = candidate["sections"][0].get("layers", [])[:2]
            candidate.setdefault("params", {})["target_seconds"] = 2
        assert arrangement_sha(arr) == arrangement_sha(arr_again), "composer is non-deterministic under a fixed seed"

        # 2) save -> load re-derives the IDENTICAL hash (a saved plan reproduces).
        sv = core.save_plan("v3plan", arr, "girl_talk_v1")
        ld = core.load_plan(sv["plan_hash"])
        loaded_plan = ld["plan"]
        assert arrangement_sha(loaded_plan) == sv["plan_hash"], \
            "saved plan does not re-derive its own arrangement_sha (INVARIANT FAILURE)"

        # 3) render the loaded plan TWICE -> identical decoded audio samples (exact render).
        def _render(plan, tag):
            mid = ulidish()
            db.execute("INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, "m", plan.get("seed"), json.dumps(plan.get("params") or {}),
                        json.dumps(plan), None, now_utc(), ENGINE_VERSION, arrangement_sha(plan)))
            db.commit()
            dst = tmp / "work" / "renders" / f"out_{tag}.wav"
            with patch("earcrate.app.stable_presence_restore", side_effect=lambda y, _sr: y), \
                 patch("earcrate.app.integrated_lufs_normalize", side_effect=lambda y, _sr, _target: y), \
                 patch("earcrate.app.drydeck_metrics", return_value={}), \
                 patch("earcrate.app.drydeck_quality_gate", return_value={"passed": True}):
                res = core.render_mashup(mid, dst)
            return res, dst
        r1, d1 = _render(loaded_plan, "a")
        r2, d2 = _render(loaded_plan, "b")
        assert r1.get("type") == "render_mashup" and r1.get("presented") is True, r1
        success_bundle = _assert_run_bundle(r1, "succeeded")
        assert success_bundle["plan.json"]["plan_sha256"] == sv["plan_hash"]
        y1, _sr1 = sf.read(str(r1["path"])); y2, _sr2 = sf.read(str(r2["path"]))
        assert y1.shape == y2.shape and np.array_equal(y1, y2), \
            "rendering the same saved plan twice produced different audio (render is non-deterministic)"

        # 4) the render receipt is bound to the plan.
        assert r1.get("arrangement_sha") == sv["plan_hash"], "render receipt sha is not the plan's sha"
        rep1 = json.loads(Path(r1["report"]).read_text(encoding="utf-8"))
        assert rep1.get("arrangement_sha") == sv["plan_hash"]

        # 5) a fully-valid plan drops NOTHING (every selected event rendered).
        assert r1.get("drop_count") == 0 and not rep1.get("drops"), \
            f"a valid plan silently dropped selected events: {rep1.get('drops')}"

        # 6) an un-renderable layer is a FAILURE accounted for as a reasoned drop
        #    receipt, NOT a silent skip: it must surface in report['drops'] with a reason.
        bad = copy.deepcopy(loaded_plan)
        injected = None
        for sec in bad["sections"]:
            for ly in sec.get("layers", []):
                ly["loop_id"] = "seg_DOES_NOT_EXIST"
                injected = "seg_DOES_NOT_EXIST"
                break
            if injected:
                break
        assert injected, "fixture plan had no layer to corrupt"
        rb, bad_dst = _render(bad, "bad")
        assert rb.get("type") == "render_rejected" and rb.get("presented") is False, rb
        assert rb.get("failure_kind") == "selected_layer_render_failure", rb
        assert rb.get("path") is None and not bad_dst.exists(), "partial arrangement published a WAV"
        failed_bundle = _assert_run_bundle(rb, "failed")
        assert failed_bundle["report.json"]["outcome"]["failure_kind"] == "selected_layer_render_failure"
        repb = json.loads(Path(rb["report"]).read_text(encoding="utf-8"))
        assert repb.get("render_integrity", {}).get("passed") is False
        assert int(repb.get("drop_count") or 0) >= 1, "an un-renderable layer was silently skipped (no drop receipt)"
        dropped = [d for d in repb.get("drops", []) if d.get("loop_id") == injected]
        assert dropped and dropped[0].get("reason"), \
            "the un-renderable layer left no reasoned receipt in report['drops']"

        refused = core.propose_plan({"taste_profile": "profile_with_no_atoms", "target_seconds": 16, "seed": 11})
        assert refused.get("ok") is False, refused
        refused_bundle = _assert_run_bundle(refused, "failed")
        assert refused_bundle["plan.json"]["state"] == "not_created"
        assert refused_bundle["plan.json"].get("reason")
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_measurements_persona_free():
    """v3 §5.3 L1 (persona-free measurements): a file's measurement is stored ONCE
    per (file, analyzer_version) and SHARED across personas — not re-measured per
    resident. Building resident B ADOPTS resident A's persona-independent
    measurement verbatim (adopted == inserted, > 0) instead of re-running DSP, the
    file-level `features` row count stays one-per-file across a second build (no
    per-persona measurement rows), and for any loop present under both profiles the
    two atoms reference the SAME file_id and carry byte-identical persona-free
    metrics_json backed by exactly one features row."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    from unittest.mock import patch
    from earcrate.app import EarcrateCore
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        sr = 44100
        for i in range(3):
            t = np.arange(sr * 8) / sr
            sf.write(str(tmp / "music" / f"s{i}.wav"),
                     (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 10})
        core.scan()
        with patch("earcrate.app.analyze_file_worker", side_effect=_fast_analysis_fixture):
            core.analyze(force=True)
        with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)):
            core.extract_loops(auto_approve=True, force=True)
        db = core.conn()
        n_files = db.execute("SELECT COUNT(*) n FROM files WHERE root='master'").fetchone()["n"]
        assert n_files == 3, n_files
        # measured ONCE: exactly one features row per file, one analyzer_version.
        n_feat = db.execute("SELECT COUNT(*) n FROM features").fetchone()["n"]
        n_distinct = db.execute("SELECT COUNT(DISTINCT file_id) n FROM features").fetchone()["n"]
        assert n_feat == n_files == n_distinct, f"measurement not one-per-file: {n_feat} rows for {n_files} files"
        assert db.execute("SELECT COUNT(DISTINCT analyzer_version) n FROM features").fetchone()["n"] == 1

        with patch("earcrate.app.ear_crate_file_worker", side_effect=_fast_crate_fixture):
            r1 = core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
        feat_after_a = db.execute("SELECT COUNT(*) n FROM features").fetchone()["n"]
        # SECOND resident: adopts A's measurement, does not re-measure.
        r2 = core.build_ear_crate(taste_profile="troubadour_v1")
        feat_after_b = db.execute("SELECT COUNT(*) n FROM features").fetchone()["n"]
        assert r1["inserted"] > 0 and r1["adopted"] == 0, r1
        assert r2["adopted"] == r2["inserted"] and r2["adopted"] > 0, \
            f"resident B re-measured instead of adopting A's measurement: {r2}"
        # building a second resident added NO new measurement rows (shared, not per-persona).
        assert feat_after_b == feat_after_a == n_feat, \
            f"a second persona created new measurement rows: {n_feat} -> {feat_after_a} -> {feat_after_b}"

        # the two personas reference the SAME underlying measurement for a shared loop.
        lid = db.execute("SELECT id FROM loops LIMIT 1").fetchone()["id"]
        ga = db.execute("SELECT file_id, metrics_json FROM ear_atoms WHERE loop_id=? AND taste_profile='girl_talk_v1'", (lid,)).fetchone()
        tb = db.execute("SELECT file_id, metrics_json FROM ear_atoms WHERE loop_id=? AND taste_profile='troubadour_v1'", (lid,)).fetchone()
        assert ga is not None and tb is not None, "loop not present under both personas"
        assert ga["file_id"] == tb["file_id"], "personas point at different measurement rows for the same loop"
        assert ga["metrics_json"] == tb["metrics_json"], "persona-free metrics were re-measured, not shared verbatim"
        assert db.execute("SELECT COUNT(*) n FROM features WHERE file_id=?", (ga["file_id"],)).fetchone()["n"] == 1, \
            "more than one measurement row for a single file"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_judgments_append_only_deterministic():
    """v3 §5.3 L2 (append-only judgments on deterministic identity): a judgment
    keys off the CONTENT-DERIVED atom id (atm_ + sha256(segment_id | profile)),
    which is stable because the loop id IS the deterministic segment_id. A second
    judgment on the same segment UPSERTs (PK(atom_id,taste_profile)) — one row, the
    latest verdict wins, never a duplicate — and it survives a full re-derivation
    without orphaning: after extract_loops(force) + build_ear_crate(force) the
    judgment still JOINs to a live ear_atom under the same id. Complements
    test_force_rebuild_preserves_judgments by pinning the KEY (content-derived +
    stable) and the upsert property."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    from unittest.mock import patch
    from earcrate.app import EarcrateCore
    from earcrate.core.util import sha256_text
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        sr = 44100
        for i in range(3):
            t = np.arange(sr * 8) / sr
            sf.write(str(tmp / "music" / f"s{i}.wav"),
                     (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 10})
        core.scan()
        with patch("earcrate.app.analyze_file_worker", side_effect=_fast_analysis_fixture):
            core.analyze(force=True)
        with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)), \
             patch("earcrate.app.ear_crate_file_worker", side_effect=_fast_crate_fixture):
            core.extract_loops(auto_approve=True, force=True)
            core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
        db = core.conn()

        # the atom id is content-derived off the deterministic segment identity.
        row = db.execute("SELECT id, loop_id FROM ear_atoms WHERE taste_profile='girl_talk_v1' LIMIT 1").fetchone()
        atom_id, loop_id = row["id"], row["loop_id"]
        assert str(loop_id).startswith("seg_"), "loop id is not the deterministic segment id"
        assert atom_id == "atm_" + sha256_text(f"{loop_id}|girl_talk_v1")[:20], \
            "atom id is not content-derived from (segment_id | taste_profile) — judgments would orphan on rebuild"

        # a second judgment on the same segment UPSERTs — one row, latest wins.
        core.set_atom_judgment(atom_id, "girl_talk_v1", "approved", favorite=True, locked=True)
        core.set_atom_judgment(atom_id, "girl_talk_v1", "rejected", reason="changed my mind")
        n = db.execute("SELECT COUNT(*) n FROM atom_judgments WHERE atom_id=? AND taste_profile='girl_talk_v1'",
                       (atom_id,)).fetchone()["n"]
        assert n == 1, f"second judgment on the same segment duplicated instead of upserting: {n} rows"
        cur = db.execute("SELECT status, favorite, reason FROM atom_judgments WHERE atom_id=? AND taste_profile='girl_talk_v1'",
                         (atom_id,)).fetchone()
        assert cur["status"] == "rejected" and cur["reason"] == "changed my mind", dict(cur)

        # the SAME segment under a DIFFERENT profile is a distinct, independent key.
        core.build_ear_crate(taste_profile="troubadour_v1")
        tb_atom = db.execute("SELECT id FROM ear_atoms WHERE loop_id=? AND taste_profile='troubadour_v1'", (loop_id,)).fetchone()["id"]
        assert tb_atom == "atm_" + sha256_text(f"{loop_id}|troubadour_v1")[:20]
        assert tb_atom != atom_id, "distinct personas must not collide on one judgment key"

        # survives a full re-derivation without orphaning: still JOINs to a live atom.
        with patch.object(EarcrateCore, "score_loop", return_value=(0.8, "texture", 0.9)), \
             patch("earcrate.app.ear_crate_file_worker", side_effect=_fast_crate_fixture):
            core.extract_loops(auto_approve=True, force=True)
            core.build_ear_crate(taste_profile="girl_talk_v1", force=True)
        joined = db.execute(
            "SELECT j.status FROM atom_judgments j JOIN ear_atoms a ON a.id=j.atom_id "
            "WHERE j.atom_id=? AND j.taste_profile='girl_talk_v1'", (atom_id,)).fetchone()
        assert joined is not None, "judgment orphaned after re-derivation (atom id was not stable)"
        assert joined["status"] == "rejected", "the upserted verdict did not survive re-derivation"
        # re-derivation did not spawn a duplicate judgment.
        assert db.execute("SELECT COUNT(*) n FROM atom_judgments WHERE atom_id=? AND taste_profile='girl_talk_v1'",
                          (atom_id,)).fetchone()["n"] == 1, "re-derivation duplicated the judgment"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_quota_preserves_human_loop_approval():
    """Human judgment beats machine convenience. Loops have no atom_judgments row
    to carry a human call, so a human-approved loop is LOCKED on the loop itself.
    auto_approve_quota rebalances the hot pool by resetting approvals to
    candidate — but it must never demote a human-locked approval. A loop the
    human explicitly approved (even a low-scored one quota would never re-pick)
    must survive the re-run still approved and still locked."""
    import tempfile, os
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    sh = os.environ.get("HOME"); se = os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        for d in ("music", "work", "agent"):
            (tmp / d).mkdir()
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
        db = core.conn()
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES('f0',?,'master',1,1,'now')",
                   (str(tmp / "music" / "s.wav"),))
        core._set_pcm("f0", "pcm_fixture_f0")
        # a landfill of higher-scored candidates so quota fills its budget elsewhere
        for i in range(20):
            db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"l{i}", "f0", 0, 4, 2, "texture", 0.5 + i * 0.01, "now"))
        # X: a low-scored loop quota would never re-pick on its own merits
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES('X','f0',0,4,2,'texture',0.001,'now')")
        db.commit()
        # human explicitly approves X -> must lock it
        core.set_loop_status("X", "approved")
        assert db.execute("SELECT locked FROM loops WHERE id='X'").fetchone()["locked"] == 1, \
            "an explicit human approval must lock the loop"
        # machine rebalances the hot pool with a small budget
        core.auto_approve_quota(max_loops=5)
        row = db.execute("SELECT status,locked FROM loops WHERE id='X'").fetchone()
        assert row["status"] == "approved", \
            "human-locked loop approval was demoted to candidate by the quota reset"
        assert row["locked"] == 1, "the human lock was cleared by the machine reset"
        # X is part of the approved hot pool the composer draws from
        assert any(r["id"] == "X" for r in core.approved_loop_pool()), \
            "the preserved human approval must be in the approved pool"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_destructive_mutations_require_signature():
    """Ledger #24: the copy-only vs destructive divide, written down and pinned.
    ingest_sources and organize_and_retag COPY into the managed tree and never
    touch the source masters, so they apply on the `apply` flag alone (row-15
    already pins their dry-run default). Every op that MOVES, DELETES, or
    REWRITES existing files on disk -- reorganize_source (move), apply_identities
    (tag rewrite), apply_workspace_migration (move) -- gates its writes behind a
    plan SIGNATURE, and a MISSING signature is not a bypass: apply without a
    signature (or with a stale one) must refuse (ok False), report dry_run, and
    mutate NOTHING; only the exact plan signature lets it proceed. Human approval
    (the matching signature) beats the convenience of applying unsigned."""
    import tempfile, os, hashlib, sqlite3
    from pathlib import Path
    import numpy as np, soundfile as sf
    from mutagen import File as MF
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)

    def snap(d):
        out = {}
        for p in sorted(Path(d).rglob("*")):
            if p.is_file():
                b = p.read_bytes()
                out[str(p.relative_to(d))] = (len(b), hashlib.sha256(b).hexdigest())
        return out

    try:
        # ---- reorganize_source: MOVES files in place -------------------------
        src = tmp / "src"; (src / "dump").mkdir(parents=True)
        def wav(p):
            p.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(p), np.zeros((2000, 2), dtype="float32"), 44100)
        wav(src / "dump" / "Aphex Twin - Windowlicker.wav")
        wav(src / "dump" / "Boards of Canada - Roygbiv.wav")
        wav(src / "noise.wav")
        core = EarcrateCore()
        core.configure({"master_root": str(src), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent")})
        core.scan()
        plan = core.reorganize_source({"apply": False})
        assert plan["dry_run"] and plan["planned"] >= 2
        before = snap(src)
        # apply with NO signature -> must refuse and move nothing
        no_sig = core.reorganize_source({"apply": True})
        assert no_sig.get("ok") is False and no_sig.get("dry_run"), \
            f"reorganize_source moved files WITHOUT a signature: {no_sig}"
        assert snap(src) == before, "reorganize_source mutated the source with no signature"
        # apply with a STALE signature -> must refuse and move nothing
        stale = core.reorganize_source({"apply": True, "signature": "stale"})
        assert stale.get("ok") is False, f"stale signature was accepted: {stale}"
        assert snap(src) == before, "reorganize_source mutated the source with a stale signature"
        # apply with the CORRECT signature -> proceeds
        good = core.reorganize_source({"apply": True, "signature": plan["signature"]})
        assert good.get("ok") and good.get("moved", 0) >= 2, f"signed reorganize did not proceed: {good}"

        # ---- apply_identities: REWRITES tags on existing files ---------------
        lib = tmp / "lib"; lib.mkdir()
        f = lib / "track.flac"
        t = np.arange(44100 * 3) / 44100
        sf.write(str(f), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        mf = MF(str(f), easy=True); mf["artist"] = ["Original Artist"]; mf["title"] = ["orig"]; mf.save()
        core2 = EarcrateCore()
        core2.configure({"master_root": str(lib), "working_root": str(tmp / "w2"),
                         "agent_root": str(tmp / "a2")})
        core2.scan()
        fid = core2.conn().execute("SELECT id FROM files WHERE root='master' LIMIT 1").fetchone()["id"]
        props = [{"path": str(f), "file_id": fid, "artist": "New Artist",
                  "title": "New Title", "album": "New Album", "score": 0.98}]
        dry = core2.apply_identities({"proposals": props, "apply": False})
        assert dry["dry_run"] and dry["would_retag"] == 1
        before_tags = snap(lib)
        # apply with NO signature -> must refuse and rewrite nothing
        no_sig2 = core2.apply_identities({"proposals": props, "apply": True})
        assert no_sig2.get("ok") is False and no_sig2.get("dry_run"), \
            f"apply_identities rewrote tags WITHOUT a signature: {no_sig2}"
        assert MF(str(f), easy=True).get("artist")[0] == "Original Artist", \
            "apply_identities rewrote tags with no signature"
        assert snap(lib) == before_tags, "apply_identities mutated a file with no signature"
        # stale signature -> refuse
        stale2 = core2.apply_identities({"proposals": props, "apply": True, "signature": "stale"})
        assert stale2.get("ok") is False, f"stale signature accepted by apply_identities: {stale2}"
        assert MF(str(f), easy=True).get("artist")[0] == "Original Artist"
        # correct signature -> proceeds
        good2 = core2.apply_identities({"proposals": props, "apply": True, "signature": dry["signature"]})
        assert good2.get("ok") and good2.get("retagged") == 1, f"signed apply_identities did not proceed: {good2}"
        assert MF(str(f), easy=True).get("artist")[0] == "New Artist"

        # ---- apply_workspace_migration: MOVES workspace data ----------------
        mus = tmp / "music"; mus.mkdir(); wav(mus / "song.wav")
        old = tmp / "OldWorkspace"; (old / "agent" / "cache" / "analysis").mkdir(parents=True)
        db = sqlite3.connect(str(old / "agent" / "jukebreaker.sqlite"))
        db.execute("CREATE TABLE judged(id TEXT)"); db.execute("INSERT INTO judged VALUES('a')")
        db.commit(); db.close()
        (old / "agent" / "cache" / "analysis" / "x-gt-v0.6.1-earcrate-feasibility.npz").write_bytes(b"NPZ")
        core3 = EarcrateCore()
        ws = str(tmp / "NewWorkspace")
        mdata = {"music_folder": str(mus), "workspace_folder": ws, "sources": [str(old)]}
        mplan = core3.plan_workspace_migration(mdata)
        before_old = snap(old)
        # no signature -> refuse, move nothing
        m_nosig = core3.apply_workspace_migration(mdata)
        assert m_nosig.get("ok") is False and m_nosig.get("dry_run"), \
            f"apply_workspace_migration moved data WITHOUT a signature: {m_nosig}"
        assert snap(old) == before_old, "workspace migration mutated the old workspace with no signature"
        # stale -> refuse
        m_stale = core3.apply_workspace_migration({**mdata, "signature": "stale"})
        assert m_stale.get("ok") is False, f"stale workspace signature accepted: {m_stale}"
        assert snap(old) == before_old
        # correct signature -> proceeds
        m_good = core3.apply_workspace_migration({**mdata, "signature": mplan["signature"]})
        assert m_good.get("ok"), f"signed workspace migration did not proceed: {m_good}"

        # ---- copy-only exemption: ingest/organize apply on the flag alone ----
        # They must NOT demand a signature (the source is never mutated). Confirm
        # ingest copies with apply=True and no signature, leaving the source intact.
        ext = tmp / "ext"; wav(ext / "incoming.wav")
        ext_before = snap(ext)
        (tmp / "mm").mkdir(exist_ok=True)
        core4 = EarcrateCore()
        core4.configure({"master_root": str(tmp / "mm"), "working_root": str(tmp / "ww"),
                         "agent_root": str(tmp / "aa")})
        core4.scan()
        ing = core4.ingest_sources({"sources": [str(ext)], "apply": True})
        assert ing.get("ok"), f"copy-only ingest must apply on the flag alone: {ing}"
        assert ing.get("dry_run") is False, "ingest with apply=True must not be dry_run"
        assert snap(ext) == ext_before, "copy-only ingest must never mutate the source"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_steering_precedence_order():
    """PINS the full steering ladder the composer obeys when human and machine
    signals collide: pair-veto (rejected edge) > atom reject/lock > atom favorite
    > rank score > station bias. Concretely: a favorite flips the pick over a
    slightly better stranger (favorite > rank); but an atom the human later
    rejected AND locked out is dropped even though it is still flagged favorite
    and even though station fire feedback nudges the compile toward more vocals -
    no lower-precedence signal (favorite, rank, or station bias) can resurrect a
    human-vetoed/locked-out atom."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    (tmp / "music").mkdir(); (tmp / "work").mkdir(); (tmp / "agent").mkdir()
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
    db = core.conn()
    pool = []
    n = 0
    for srci in range(5):
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES(?,?,?,?,?,?)",
                   (f"f{srci}", str(tmp / "music" / f"s{srci}.wav"), "master", 1, 1, "now"))
        core._set_pcm(f"f{srci}", f"pcm_fixture_{srci}")
        for ear, role in (("DRUM_BREAK", "drum_anchor"), ("TEXTURE", "texture")):
            n += 1
            aid = f"a{n}"
            db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"l{n}", f"f{srci}", 0, 4, 2, role, 0.7, "now"))
            db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (aid, f"l{n}", f"f{srci}", "girl_talk_v1", ear, role, 0, 4, 2, 0.7, "{}", "now"))
            pool.append({"id": f"l{n}", "atom_id": aid, "ear_role": ear, "role": role, "key_root": 0,
                         "bpm": 124.0, "score": 0.7, "hook_score": 0.3, "title": f"song_{srci}",
                         "path": f"/m/song_{srci}.mp3", "low_share": 0.2, "high_share": 0.3})
    for aid, srci, score in (("v_good", 0, 0.60), ("v_fav", 1, 0.55)):
        n += 1
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (f"l{n}", f"f{srci}", 4, 8, 2, "vocal", score, "now"))
        db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (aid, f"l{n}", f"f{srci}", "girl_talk_v1", "VOX_HOOK", "vocal", 4, 8, 2, score, "{}", "now"))
        pool.append({"id": f"l{n}", "atom_id": aid, "ear_role": "VOX_HOOK", "role": "vocal", "key_root": 0,
                     "bpm": 124.0, "score": score, "hook_score": 0.8, "title": f"song_{srci}",
                     "path": f"/m/song_{srci}.mp3", "low_share": 0.1, "high_share": 0.4})
    db.commit()
    params = {"taste_profile": "girl_talk_v1", "target_seconds": 40, "bpm": 124}

    def first_vocal(arr):
        for sec in arr["sections"]:
            for ly in sec["layers"]:
                if ly.get("role") == "vocal":
                    return ly.get("atom_id")
        return None

    def used_vocals(arr):
        return {ly.get("atom_id") for sec in arr["sections"] for ly in sec["layers"] if ly.get("role") == "vocal"}

    # rank score alone: the higher-scored stranger leads the foreground.
    base = first_vocal(core.compose_taste_arrangement(list(pool), dict(params), seed=7))
    assert base == "v_good", f"baseline should lead with the higher-scored vocal, got {base}"
    # favorite > rank: the human favorite flips the lead over the better stranger.
    core.set_atom_judgment("v_fav", "girl_talk_v1", "approved", favorite=True)
    fav = first_vocal(core.compose_taste_arrangement(list(pool), dict(params), seed=7))
    assert fav == "v_fav", f"favorite must outrank a slightly better stranger, got {fav}"
    # atom reject/lock > favorite, AND station bias can't resurrect it: the human
    # rejects+locks v_fav (still flagged favorite), then station fire feedback nudges
    # the compile toward more vocals. The locked-out atom must stay out; the only
    # non-vetoed vocal carries the foreground.
    core.set_atom_judgment("v_fav", "girl_talk_v1", "rejected", favorite=True, locked=True, reason="changed my mind")
    core.station_feedback("fire"); core.station_feedback("fire")
    steered = core.compose_taste_arrangement(list(pool), dict(params), seed=7)
    used = used_vocals(steered)
    assert "v_fav" not in used, "atom reject/lock is a veto no favorite or station bias may override"
    assert "v_good" in used, "the non-vetoed vocal should carry the foreground"


def test_no_shadow_sources_of_truth():
    """§5.3 / Lessons #1, #2, #12: every formula and every shared constant has
    exactly ONE definition — no shadow copy in app.py or in providers/plan that
    could drift from its source.

    Two drift classes are gated here:

      1. The composition-math FORMULAS live only in earcrate.plan.math. app.py
         must delegate to the pure fns, never re-inline the arithmetic (the
         readiness-scale clamp, the target->bars conversion, the source-count
         ceil). Re-inlining any of them in app.py trips this gate.
      2. Any constant shared across modules is DEFINED once. The composition
         constants (DEFAULT_SOURCE_SECONDS = 11.5 fallback, the readiness scale
         reference) live only in plan.math; the runtime/vocabulary constants
         (DEFAULT_SAMPLE_RATE, ANALYZER_VERSION, the render/ear role orders) live
         only in core.deps; the retention-tier and stem-role vocabularies live
         only in their provider. A second `NAME =` anywhere trips this gate.
    """
    import re
    from pathlib import Path
    import earcrate.app as _app
    import earcrate.plan.math as _pmath

    pkg_dir = Path(_app.__file__).resolve().parent
    app_src = Path(_app.__file__).read_text(encoding="utf-8")
    math_src = Path(_pmath.__file__).read_text(encoding="utf-8")

    # --- 1. formulas live ONLY in plan.math, not re-inlined in app.py --------
    # Each fragment is a distinctive piece of one composition formula. It must be
    # present in plan.math (the source) and ABSENT from app.py (which delegates).
    formula_fragments = {
        "readiness-scale clamp": ("max(SCALE_MIN", "min(SCALE_MAX"),
        "target->bars conversion": ("/ 60.0 / 4.0",),
    }
    for label, frags in formula_fragments.items():
        for frag in frags:
            assert frag in math_src, f"{label} fragment {frag!r} missing from plan.math (source of truth)"
            assert frag not in app_src, \
                f"{label} formula {frag!r} is re-inlined in app.py — a shadow copy that will drift from plan.math"
    # The 11.5 source-seconds fallback must be the named constant, never a bare
    # literal re-inlined at the call sites (it lives once as plan.math.DEFAULT_SOURCE_SECONDS).
    assert "11.5" in math_src, "DEFAULT_SOURCE_SECONDS (11.5) must be defined in plan.math"
    assert "11.5" not in app_src, \
        "app.py inlines the bare 11.5 source-seconds fallback — reference plan.math.DEFAULT_SOURCE_SECONDS instead"
    # app.py still delegates through the pure fns (belt-and-braces with the pins gate).
    assert "from earcrate.plan.math import" in app_src and "DEFAULT_SOURCE_SECONDS" in app_src, \
        "app.py must import the composition math (incl. DEFAULT_SOURCE_SECONDS) from plan.math"

    # --- 2. every shared constant is defined exactly once in the package -----
    def _defn_files(name):
        rx = re.compile(rf"^{name}\s*=", re.M)
        return [f.name for f in sorted(pkg_dir.rglob("*.py"))
                if rx.search(f.read_text(encoding="utf-8"))]

    single_source = {
        # composition math constants -> only plan/math.py
        "DEFAULT_SOURCE_SECONDS": "math.py",
        "REFERENCE_SECONDS": "math.py",
        # runtime + vocabulary constants -> only core/deps.py
        "DEFAULT_SAMPLE_RATE": "deps.py",
        "ANALYZER_VERSION": "deps.py",
        "ENGINE_VERSION": "deps.py",
        "ROLE_ORDER": "deps.py",
        "EAR_ROLE_ORDER": "deps.py",
        "EAR_TO_RENDER_ROLE": "deps.py",
        # provider-local vocabularies -> only their own module
        "TIERS": "artifacts.py",
        "DEFAULT_ROLES": "stems.py",
    }
    for name, home in single_source.items():
        files = _defn_files(name)
        assert files == [home], \
            f"{name} must be defined ONCE in {home}, found definitions in {files} (shadow constant -> drift risk)"


def test_pcm_identity_feeds_stems():
    """One stomach, not two: the cheap laptop scan must DEPOSIT the L0 sound
    identity (pcm_sha256 of the decoded canonical PCM) that the expensive GPU stem
    pass CONSUMES. After analyze, files.audio_sha256 is populated; the SAME sound
    in two files yields the SAME id (separate once, dedup duplicates); and the
    StemProvider seam is content-addressed by exactly that id, so L1 hands off to
    L3. RED on the old code where audio_sha256 was never written."""
    import tempfile, os
    import numpy as np, soundfile as sf
    from unittest.mock import patch
    from pathlib import Path
    from earcrate.analyze.decode import decoded_audio_sha256
    from earcrate.providers import get
    from earcrate.providers.stems import DemucsStemProvider
    tmp = Path(tempfile.mkdtemp()); sh = os.environ.get("HOME"); se = os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        m = tmp / "m"; m.mkdir(); sr = 44100
        y = (0.3 * np.sin(2 * np.pi * 220 * np.arange(sr * 6) / sr)).astype(np.float32)
        sf.write(str(m / "a.wav"), y, sr)
        sf.write(str(m / "dup.wav"), y, sr)  # identical sound, different file
        core = EarcrateCore()
        core.configure({"master_root": str(m), "working_root": str(tmp / "w"),
                        "agent_root": str(tmp / "a"), "workers": 1, "analysis_seconds": 6})
        core.scan()

        def _identity_analysis(job):
            result = _fast_analysis_fixture(job)
            result["pcm_sha"] = decoded_audio_sha256(
                Path(job["path"]), int(job["sr"]), float(job.get("duration") or 0.0)
            )
            return result

        with patch("earcrate.app.analyze_file_worker", side_effect=_identity_analysis):
            core.analyze(force=True)
        shas = {Path(r["path"]).name: r["audio_sha256"]
                for r in core.conn().execute("SELECT path, audio_sha256 FROM files").fetchall()}
        # 1) the cheap scan DEPOSITED the identity (the whole point)
        assert shas.get("a.wav"), "analyze did not deposit pcm_sha (audio_sha256 null) -- cheap scan feeds the GPU nothing"
        assert len(shas["a.wav"]) == 64, "pcm_sha is not a sha256 hex digest"
        # 2) identical sound -> identical id: separate once, dedup duplicate files
        assert shas["a.wav"] == shas["dup.wav"], "same sound got different pcm_sha -- no dedup across duplicates"
        # 3) L1 -> L3 handoff: the stem seam is content-addressed by pcm_sha
        pcm = shas["a.wav"]
        res = get("stems").separate(pcm, str(m / "a.wav"), ["vocals"])  # default = no-op here
        assert res.get("available") is False and str(res.get("pcm_sha")) == pcm, \
            "the no-op StemProvider must carry the pcm_sha through (the L1->L3 key)"
        dp = DemucsStemProvider()
        assert dp._artifact_key(pcm, "vocals") == dp._artifact_key(pcm, "vocals"), "stem artifact key must be deterministic"
        assert dp._artifact_key(pcm, "vocals") != dp._artifact_key(pcm, "drums"), "role must be part of the stem key"
        assert dp._artifact_key(pcm, "vocals") != dp._artifact_key(shas["dup.wav"][::-1], "vocals"), "a different sound must key to a different stem"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_no_unfed_handoffs():
    """The UNFED-HANDOFF detector (v3 honesty invariant, born from audio_sha256).

    A producer->consumer contract must be WIRED or explicitly DEFERRED with a
    reason. Two shapes of contract:
      (a) an identity/link COLUMN a consumer keys on (must be written by a
          producer), and
      (b) a registered provider SEAM (must have a live caller).
    A silent orphan — declared, read/registered, but never fed/called — is the
    'two stomachs' bug (files.audio_sha256 was one until v0.8.22). This gate fails
    the instant a NEW orphan appears with no receipt saying why it's deferred, so
    'built the column/seam' can never masquerade as 'wired the feature'.
    """
    import re, os, tempfile
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    # Declared-on-purpose, not-yet-wired — each needs a written reason (the receipt).
    DEFERRED_COLUMNS = {}   # every identity/link column is currently fed
    # NOTE ON "wired": a live caller proves a CALL SITE exists, NOT that the
    # capability is functional. stems/artifacts have a call site in render but the
    # runtime (DemucsStemProvider._run_demucs) is an unimplemented stub, no config
    # selects demucs over the noop default, and provider/renderer would use
    # different ArtifactStores — so the feature is OFF and UNVERIFIED. It becomes
    # "functional" only with a real GPU receipt (see MILESTONES.md §1). Kept here
    # with honest reasons rather than promoted to "wired == working".
    DEFERRED_SEAMS = {
        "stems":        "StemProvider: call path complete + gated with a FAKE (config/env selection -> shared workspace L3 store -> materialize -> resolve -> cache-before-separate), but the REAL Demucs run is UNVERIFIED pending a 4060 receipt (MILESTONES.md §1).",
        "artifacts":    "ArtifactStore: now a single workspace-scoped store (EARCRATE_L3_ROOT) shared by the stems provider and the renderer's get('artifacts') -- see stems.",
        "embedding":    "EmbeddingProvider — no embeddings computed yet; ANN retrieval is v3 §5.4. Noop (returns None) is the default.",
        "vector_index": "VectorIndex — linear scan is the live path; ANN index is v3 §5.4. LinearScan is the default.",
    }

    # Introspect the REAL schema (post-migration) and the seam registry.
    d = Path(tempfile.mkdtemp()); sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(d); os.environ["EARCRATE_HOME"] = str(d)
    try:
        for x in ("m", "w", "a"): (d / x).mkdir()
        core = EarcrateCore(); core.configure({"master_root": str(d/"m"), "working_root": str(d/"w"), "agent_root": str(d/"a")})
        db = core.conn()
        cols = [(t, r[1]) for t in [x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for r in db.execute("PRAGMA table_info(%s)" % t).fetchall()]
        from earcrate.providers import _REGISTRY
        seams = list(_REGISTRY.keys())
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)

    src = "\n".join(p.read_text(encoding="utf-8") for p in (root/"earcrate").rglob("*.py"))
    live = "\n".join(p.read_text(encoding="utf-8") for p in (root/"earcrate").rglob("*.py") if "providers/" not in str(p))
    HANDOFF = re.compile(r"(_sha256|_sha|_identity|segment_id|_path|provenance|mbid|_hash)$")

    def fed_col(col):  # producer writes it: upsert, UPDATE ... SET (anywhere in list), or INSERT [OR REPLACE] column-list
        return bool(re.search(r"\b%s\s*=\s*excluded" % col, src)
                    or re.search(r"\bSET\b[^;]{0,400}\b%s\s*=" % col, src, re.S)
                    or re.search(r"INSERT(\s+OR\s+REPLACE)?\s+INTO\s+\w+\([^)]*\b%s\b" % col, src, re.S))

    # Self-check: the detector must be able to SEE an orphan (not a tautology).
    assert not fed_col("zzz_fake_never_written_sha256"), "fed-heuristic false-positives — it would miss real orphans"

    orphan_cols = [f"{t}.{c}" for t, c in cols
                   if HANDOFF.search(c) and not fed_col(c) and not DEFERRED_COLUMNS.get(f"{t}.{c}", "").strip()]
    assert not orphan_cols, f"unfed identity/link column(s) with no producer and no DEFERRED receipt: {orphan_cols}"

    orphan_seams = [k for k in seams
                    if not re.search(r'get\(\s*["\']%s["\']' % k, live) and not DEFERRED_SEAMS.get(k, "").strip()]
    assert not orphan_seams, f"registered seam(s) with no live caller and no DEFERRED receipt: {orphan_seams}"


def test_composer_uses_retriever_seam():
    """v3 §5.4: the composer's candidate atom pool must flow THROUGH the
    CandidateRetriever seam (get('retriever')), so gathering candidates is a
    LIVE call — not an in-process list built around the seam. The default
    FullScanRetriever returns the full set unchanged, so output is byte-identical
    to a direct pool. RED-first: if approved_atom_pool builds and returns its list
    without consulting get('retriever'), the spy below is never called and this
    fails."""
    import tempfile
    import earcrate.app as appmod
    from pathlib import Path
    from earcrate.providers import get as real_get, FullScanRetriever
    tmp = Path(tempfile.mkdtemp())
    for sub in ("music", "work", "agent"): (tmp / sub).mkdir()
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
    db = core.conn()
    # tiny synthesized crate: 4 approved atoms across 2 sources
    nn = 0
    for srci in range(2):
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES(?,?,?,?,?,?)",
                   (f"f{srci}", str(tmp / "music" / f"s{srci}.wav"), "master", 1, 1, "now"))
        core._set_pcm(f"f{srci}", f"pcm_fixture_{srci}")
        for ear, role in (("VOX_HOOK", "vocal"), ("DRUM_BREAK", "drum_anchor")):
            nn += 1
            db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"l{nn}", f"f{srci}", 0, 4, 2, role, 0.7, "now"))
            db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,status,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"a{nn}", f"l{nn}", f"f{srci}", "girl_talk_v1", ear, role, 0, 4, 2, 0.7, "approved", "{}", "now"))
    db.commit()

    # Spy on the retriever seam: wrap get() in the app module namespace so the
    # composer's live call is observed, and confirm FullScan passes the pool
    # through unchanged. Restore the real get() afterward no matter what.
    calls = {"retriever": 0, "seen": None}
    def spy_get(kind, name=None):
        prov = real_get(kind, name)
        if kind == "retriever":
            calls["retriever"] += 1
            assert isinstance(prov, FullScanRetriever), "default retriever must be FullScan"
            inner = prov.retrieve
            def wrapped(catalog, *a, **k):
                seen = list(catalog)
                calls["seen"] = seen
                return inner(seen, *a, **k)
            prov.retrieve = wrapped
        return prov
    appmod.get = spy_get
    try:
        pool = core.approved_atom_pool("girl_talk_v1")
    finally:
        appmod.get = real_get

    assert calls["retriever"] > 0, "approved_atom_pool must gather candidates via get('retriever') — seam not consulted"
    assert len(pool) == 4, f"expected 4 approved atoms, got {len(pool)}"
    # FullScan is a pure pass-through: the returned pool is exactly what the
    # retriever was handed — output unchanged vs the direct in-process pool.
    assert [x["atom_id"] for x in pool] == [x["atom_id"] for x in calls["seen"]], "FullScanRetriever must return the full candidate set unchanged"


def test_render_consults_stem_seam():
    """v3 §5.2 render wiring: a VOCAL layer must CONSULT the StemProvider seam
    (get("stems").separate(pcm_sha, path, ["vocals"])) BEFORE using the full mix.
    When a provider yields a real vocals stem (available=True) render uses it and
    records layer['stem_source']=='vocals'; the DEFAULT no-op reports stems
    unavailable, so render FALLS BACK to the full-mix decode (stem_source=='mix'),
    byte-identical to the pre-seam path. RED-first: if render never calls the seam
    the fake is never invoked and the 'consulted' assertion fails."""
    import tempfile, os, json
    from unittest.mock import patch
    from pathlib import Path
    import numpy as np, soundfile as sf
    from earcrate.app import EarcrateCore, ENGINE_VERSION
    from earcrate.core.util import arrangement_sha, ulidish, now_utc
    import earcrate.providers as P

    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)

    # A fake StemProvider that records every consultation and returns a REAL,
    # distinct vocals stem (a 660 Hz tone) so a successful consult visibly
    # changes the rendered audio (proving the stem is actually fed through).
    fake_vox = tmp / "music" / "fake_vocals.wav"
    _t = np.arange(int(44100 * 8)) / 44100.0
    sf.write(str(fake_vox), (0.5 * np.sin(2 * np.pi * 660.0 * _t)).astype(np.float32), 44100)

    class _FakeStems:
        name = "fake"
        calls = []
        def separate(self, pcm_sha, audio_path, roles=None):
            _FakeStems.calls.append({"pcm_sha": str(pcm_sha), "path": str(audio_path),
                                     "roles": tuple(roles or [])})
            return {"available": True, "provider": "fake", "pcm_sha": str(pcm_sha),
                    "stems": {"vocals": str(fake_vox)}}

    try:
        bpm = 120.0
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 8})
        db = core.conn()
        pool = _v3_build_render_pool(core, db, tmp, bpm=bpm)
        # Deposit the L0 sound identity (files.audio_sha256) the seam keys on.
        for i in range(len(pool)):
            core._set_pcm(f"f{i}", f"pcm_f{i}")
        db.commit()
        vocal_shas = {f"pcm_f{i}" for i in range(len(pool)) if pool[i]["role"] == "vocal"}
        assert vocal_shas, "fixture produced no vocal sources"

        params = {"taste_profile": "girl_talk_v1", "target_seconds": 16, "bpm": bpm, "quality_mode": "stable_deck"}
        arr = core.compose_taste_arrangement(list(pool), dict(params), seed=11)
        assert arr.get("sections"), "composer produced an empty arrangement"
        sv = core.save_plan("stemplan", arr, "girl_talk_v1")
        plan = core.load_plan(sv["plan_hash"])["plan"]

        def _render(tag):
            mid = ulidish()
            db.execute("INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, "m", plan.get("seed"), json.dumps(plan.get("params") or {}),
                        json.dumps(plan), None, now_utc(), ENGINE_VERSION, arrangement_sha(plan)))
            db.commit()
            dst = tmp / "work" / "renders" / f"out_{tag}.wav"
            with patch("earcrate.app.stable_presence_restore", side_effect=lambda y, _sr: y), \
                 patch("earcrate.app.integrated_lufs_normalize", side_effect=lambda y, _sr, _target: y), \
                 patch("earcrate.app.drydeck_metrics", return_value={}), \
                 patch("earcrate.app.drydeck_quality_gate", return_value={"passed": True}):
                res = core.render_mashup(mid, dst)
            rep = json.loads(Path(res["report"]).read_text(encoding="utf-8"))
            return res, rep

        # (A) DEFAULT no-op: render succeeds and every vocal layer FELL BACK to mix.
        assert P.default_name("stems") == "noop"
        res_mix, rep_mix = _render("noop")
        assert res_mix.get("type") == "render_mashup" and res_mix.get("presented") is True, res_mix
        vocal_layers_mix = [ly for ly in rep_mix["layers"] if ly.get("role") == "vocal"]
        assert vocal_layers_mix, "no vocal layer rendered — the gate would be vacuous"
        assert all(ly.get("stem_source") == "mix" for ly in vocal_layers_mix), \
            "no-op default must fall back to the full mix (stem_source != 'mix')"
        y_mix, _ = sf.read(str(res_mix["path"]))

        # (B) FAKE provider registered as default: the seam is CONSULTED and used.
        _FakeStems.calls = []
        P.register("stems", "fake", _FakeStems, default=True)
        assert P.default_name("stems") == "fake"
        res_vox, rep_vox = _render("fake")

        # (b1) the seam was consulted with the RIGHT pcm_sha and the RIGHT role per
        # layer: "vocals" (the acapella) for vocal layers, "no_vocals" (the clean
        # instrumental bed) for the drums/bass/harmony layers. Requesting the
        # instrumental is the point of separation — an acapella over a clean bed,
        # not over another song's full mix.
        assert _FakeStems.calls, "render never CONSULTED the StemProvider seam (RED)"
        assert all(c["roles"] in (("vocals",), ("no_vocals",)) for c in _FakeStems.calls), \
            "render must request either the 'vocals' (acapella) or 'no_vocals' (instrumental) stem"
        vocal_calls = [c for c in _FakeStems.calls if c["roles"] == ("vocals",)]
        inst_calls = [c for c in _FakeStems.calls if c["roles"] == ("no_vocals",)]
        assert vocal_calls, "render must request the 'vocals' stem for vocal layers"
        assert inst_calls, "render must request the 'no_vocals' (instrumental) stem for bed layers"
        assert all(c["pcm_sha"] in vocal_shas for c in vocal_calls), \
            "the vocals stem was requested for a non-vocal source"
        assert all(c["pcm_sha"] not in vocal_shas for c in inst_calls), \
            "the instrumental stem should be requested for bed (non-vocal) layers"

        # (b2) the returned stem actually FED the render (recorded + audible). The
        # fake provider only serves a vocals stem, so vocal layers ride it and the
        # bed layers gracefully fall back to the full mix (no no_vocals produced).
        vocal_layers_vox = [ly for ly in rep_vox["layers"] if ly.get("role") == "vocal"]
        assert vocal_layers_vox and all(ly.get("stem_source") == "vocals" for ly in vocal_layers_vox), \
            "a consulted, available vocals stem was not recorded as the layer source"
        bed_layers_vox = [ly for ly in rep_vox["layers"] if ly.get("role") != "vocal"]
        assert all(ly.get("stem_source") == "mix" for ly in bed_layers_vox), \
            "bed layers must fall back to mix when the provider serves no instrumental stem"
        y_vox, _ = sf.read(str(res_vox["path"]))
        assert not (y_mix.shape == y_vox.shape and np.array_equal(y_mix, y_vox)), \
            "the vocals stem was consulted but never changed the audio (seam not fed through)"

        # (C) restore the no-op default and confirm the fallback is byte-identical
        #     to (A): the seam consult is a pure no-op on this box.
        P._DEFAULTS["stems"] = "noop"
        P._REGISTRY.get("stems", {}).pop("fake", None)
        res_mix2, rep_mix2 = _render("noop2")
        y_mix2, _ = sf.read(str(res_mix2["path"]))
        assert y_mix.shape == y_mix2.shape and np.array_equal(y_mix, y_mix2), \
            "re-render under the restored no-op default is not byte-identical (fallback drifted)"
    finally:
        P._DEFAULTS["stems"] = "noop"
        P._REGISTRY.get("stems", {}).pop("fake", None)
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)

    assert P.default_name("stems") == "noop"


def test_loop_status_endpoints_contract():
    """The LATTICE 'Loop review // quota approval' card grew per-loop human review
    handles (Approve / Reject / Lock rows + a 'Reject all candidates' bulk button).
    Those buttons are dumb POSTs; the contract they lean on lives here, at the
    Python level (a browser can't be unit-gated). Three invariants the UI depends on:
      1. set_loop_status(loop,'approved') APPROVES *and* LOCKS (locked=1) — a human
         approve must survive the quota reset, so the Approve/Lock chips lock.
      2. bulk_loop_status('rejected','candidate') rejects candidates but NEVER an
         approved loop — the 'Reject all candidates' button must honor from_status.
      3. list_loops(...) returns global {counts} the card renders after each action.
    RED-first: if from_status were ignored (bulk update touching ALL rows), the
    human-approved loop would be rejected — assertion (2) catches exactly that.
    """
    import tempfile, os
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        for d in ("music", "work", "agent"):
            (tmp / d).mkdir()
        core = EarcrateCore()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
        db = core.conn()
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES('f0',?,'master',1,1,'now')",
                   (str(tmp / "music" / "s.wav"),))
        core._set_pcm("f0", "pcm_fixture_f0")
        # three candidate loops + one the human will approve
        for i in range(3):
            db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"c{i}", "f0", 0, 4, 2, "texture", 0.5 + i * 0.01, "now"))
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES('A','f0',0,4,2,'vocal',0.9,'now')")
        db.commit()

        # (1) human approve LOCKS
        core.set_loop_status("A", "approved")
        row = db.execute("SELECT status,locked FROM loops WHERE id='A'").fetchone()
        assert row["status"] == "approved", "Approve chip did not approve the loop"
        assert row["locked"] == 1, "a human approve must lock the loop (locked=1) so it survives quota"

        # counts the card renders
        before = core.list_loops()["counts"]
        assert before["candidate"] == 3 and before["approved"] == 1, f"counts wrong pre-bulk: {before}"
        # status filter is what the UI fetches for the candidate list
        cand = core.list_loops("candidate")["items"]
        assert {r["id"] for r in cand} == {"c0", "c1", "c2"}, "candidate filter returned the wrong rows"
        assert all(r["status"] == "candidate" for r in cand)

        # (2) 'Reject all candidates' -> candidates rejected, approved untouched
        res = core.bulk_loop_status("rejected", "candidate")
        assert res["updated"] == 3, f"bulk reject should have hit exactly the 3 candidates: {res}"
        assert db.execute("SELECT status FROM loops WHERE id='A'").fetchone()["status"] == "approved", \
            "bulk reject demoted an APPROVED loop — from_status was ignored (the RED case)"
        after = core.list_loops()["counts"]
        assert after["approved"] == 1 and after["rejected"] == 3 and after["candidate"] == 0, \
            f"counts wrong post-bulk: {after}"

        # server-by-design: bulk APPROVE is refused (only quota approves the hot pool)
        try:
            core.bulk_loop_status("approved", "candidate")
            assert False, "bulk approve must be disabled (quota is the sanctioned approval path)"
        except ValueError:
            pass
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_stem_path_producible():
    """v3 §5.2 stem PRODUCIBILITY — the CPU half of the GPU stem path, proven
    end-to-end with a FAKE demucs (NO torch). Exercises the wiring a real GPU
    receipt will one day light up: a workspace-scoped SHARED L3 store, a
    config-SELECTED provider that MATERIALIZES a stem, the renderer RESOLVING that
    key back through the SAME store, a CACHE-first 2nd pass that does NOT re-run
    the producer, an HONEST capability probe, a SURFACED fallback reason, and
    byte-identical behavior under the noop default.

    HONESTY: this proves the call/select/materialize/resolve/cache PATH with a
    synthetic producer. It does NOT run Demucs and does NOT touch a GPU — the real
    separation stays UNVERIFIED pending a 4060 receipt.

    RED-first: on the pre-change code the produced artifact key never resolves
    (provider and renderer used different ArtifactStores), render never selected
    the provider, the 2nd pass re-ran the producer, and stem_capability() / the
    surfaced stem_reason did not exist — so the vocal layer stays stem_source=='mix'
    (isolated proof: neutering only the EARCRATE_L3_ROOT export reproduces exactly
    the 'did not RESOLVE through the SHARED store' failure)."""
    import tempfile, os, json, io
    from unittest.mock import patch
    from pathlib import Path
    import numpy as np, soundfile as sf
    from earcrate.app import EarcrateCore as _Core, ENGINE_VERSION
    from earcrate.core.util import arrangement_sha, ulidish, now_utc
    import earcrate.providers as P
    from earcrate.providers.stems import DemucsStemProvider, stem_capability

    # (3) HONEST capability probe: this torch-absent box reports NOT ready.
    with patch.dict(sys.modules, {"torch": None, "demucs": None, "demucs.separate": None}):
        cap = stem_capability()
    assert set(cap) >= {"torch", "demucs", "cuda", "ready"}, cap
    assert cap["ready"] is False and cap["torch"] is False and cap["demucs"] is False, \
        "stem_capability() must HONESTLY report not-ready (no torch/demucs here): %r" % (cap,)

    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    sl = os.environ.get("EARCRATE_L3_ROOT")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)

    # A FAKE demucs that SUBCLASSES the real provider, so it drives the REAL
    # cache-first + L3 materialization seam, but produces a synthetic 660 Hz
    # vocals tone instead of running torch/CUDA. It counts how many times the
    # (expensive) producer actually runs.
    class _FakeDemucs(DemucsStemProvider):
        name = "fakedemucs"
        runs = 0
        def _run_demucs(self, audio_path, roles):
            type(self).runs += 1
            _sr = 44100
            _t = np.arange(int(_sr * 8)) / _sr
            wav = (0.5 * np.sin(2 * np.pi * 660.0 * _t)).astype(np.float32)
            out = {}
            for r in roles:
                buf = io.BytesIO(); sf.write(buf, wav, _sr, format="WAV"); out[r] = buf.getvalue()
            return out

    try:
        bpm = 120.0
        core = _Core()
        core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                        "agent_root": str(tmp / "agent"), "workers": 1, "analysis_seconds": 8})
        db = core.conn()
        pool = _v3_build_render_pool(core, db, tmp, bpm=bpm)
        # Deposit the L0 sound identity (files.audio_sha256) the seam keys on.
        for i in range(len(pool)):
            core._set_pcm(f"f{i}", f"pcm_f{i}")
        db.commit()
        vocal_shas = {f"pcm_f{i}" for i in range(len(pool)) if pool[i]["role"] == "vocal"}
        assert vocal_shas, "fixture produced no vocal sources"

        params = {"taste_profile": "girl_talk_v1", "target_seconds": 16, "bpm": bpm, "quality_mode": "stable_deck"}
        arr = core.compose_taste_arrangement(list(pool), dict(params), seed=11)
        assert arr.get("sections"), "composer produced an empty arrangement"
        sv = core.save_plan("stemplan", arr, "girl_talk_v1")
        plan = core.load_plan(sv["plan_hash"])["plan"]

        def _render(tag):
            mid = ulidish()
            db.execute("INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, "m", plan.get("seed"), json.dumps(plan.get("params") or {}),
                        json.dumps(plan), None, now_utc(), ENGINE_VERSION, arrangement_sha(plan)))
            db.commit()
            dst = tmp / "work" / "renders" / f"out_{tag}.wav"
            with patch("earcrate.app.stable_presence_restore", side_effect=lambda y, _sr: y), \
                 patch("earcrate.app.integrated_lufs_normalize", side_effect=lambda y, _sr, _target: y), \
                 patch("earcrate.app.drydeck_metrics", return_value={}), \
                 patch("earcrate.app.drydeck_quality_gate", return_value={"passed": True}):
                res = core.render_mashup(mid, dst)
            rep = json.loads(Path(res["report"]).read_text(encoding="utf-8"))
            return res, rep

        # (A) DEFAULT noop -> full-mix fallback baseline; reason SURFACED, not swallowed.
        assert P.default_name("stems") == "noop"
        res0, rep0 = _render("noop")
        assert res0.get("presented") is True, res0
        y0, _ = sf.read(str(res0["path"]))
        vlm0 = [ly for ly in rep0["layers"] if ly.get("role") == "vocal"]
        assert vlm0, "no vocal layer rendered — gate would be vacuous"
        assert all(ly.get("stem_source") == "mix" for ly in vlm0), \
            "noop default must fall back to the full mix"
        assert all(ly.get("stem_reason") for ly in vlm0), \
            "(5) fallback must SURFACE a stem_reason on the layer, not swallow it"

        # (B) SELECT the fake via config; it MATERIALIZES a stem into the SHARED store,
        #     and the renderer RESOLVES that key back through get('artifacts').
        P.register("stems", "fakedemucs", _FakeDemucs)
        core.config.stem_provider = "fakedemucs"
        _FakeDemucs.runs = 0
        res1, rep1 = _render("fake1")
        vlm1 = [ly for ly in rep1["layers"] if ly.get("role") == "vocal"]
        assert vlm1 and all(ly.get("stem_source") == "vocals" for ly in vlm1), \
            "materialized stem did not RESOLVE through the SHARED store (RED without EARCRATE_L3_ROOT)"
        assert _FakeDemucs.runs >= 1, "producer must run on a cold cache"
        runs_cold = _FakeDemucs.runs
        y1, _ = sf.read(str(res1["path"]))
        assert not (y0.shape == y1.shape and np.array_equal(y0, y1)), \
            "the produced stem never changed the audio (not fed through)"

        # (4) CACHE-first: a 2nd render is a cache HIT — the producer is NOT re-run.
        res2, rep2 = _render("fake2")
        vlm2 = [ly for ly in rep2["layers"] if ly.get("role") == "vocal"]
        assert vlm2 and all(ly.get("stem_source") == "vocals" for ly in vlm2), \
            "cached stem did not resolve on the 2nd render"
        assert _FakeDemucs.runs == runs_cold, \
            "(4) 2nd render RE-RAN the producer — cache-before-separate not honored"

        # (C) restore the noop default -> byte-identical to (A): the seam is a no-op here.
        core.config.stem_provider = "noop"
        P._REGISTRY.get("stems", {}).pop("fakedemucs", None)
        res3, rep3 = _render("noop2")
        y3, _ = sf.read(str(res3["path"]))
        assert y0.shape == y3.shape and np.array_equal(y0, y3), \
            "noop re-render not byte-identical — fallback drifted"

        # (3b) capability probe is still honest at the end.
        assert stem_capability()["ready"] is False
    finally:
        P._DEFAULTS["stems"] = "noop"
        P._REGISTRY.get("stems", {}).pop("fakedemucs", None)
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)
        if sl is not None: os.environ["EARCRATE_L3_ROOT"] = sl
        else: os.environ.pop("EARCRATE_L3_ROOT", None)

    assert P.default_name("stems") == "noop"


def test_identify_then_reorganize_uses_new_identity():
    """Milestone gate: a corrected identity from apply_identities must reach
    reorganize's target computation. apply_identities rewrites tags on disk AND
    must sync the DB `tags` rows that reorganize_source reads to place a file;
    when the proposal is path-only (no file_id -- a supported input form), the
    stale-DB bug left the DB carrying the OLD identity, so reorganize filed the
    track under the wrong Artist/Album (or _unsorted). After the fix the planned
    destination uses the NEW identity. Synthetic file, NOT the real 585-track run."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    from mutagen import File as MF
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        lib = tmp / "lib"
        f = lib / "Wrong Artist" / "Wrong Album" / "Wrong Artist - Wrong Song.flac"
        f.parent.mkdir(parents=True)
        t = np.arange(44100 * 2) / 44100
        sf.write(str(f), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        mf = MF(str(f), easy=True)
        mf["artist"] = ["Wrong Artist"]; mf["title"] = ["Wrong Song"]; mf["album"] = ["Wrong Album"]; mf.save()
        core = EarcrateCore()
        core.configure({"master_root": str(lib), "working_root": str(tmp / "w"),
                        "agent_root": str(tmp / "a")})
        core.scan()
        # PATH-ONLY proposal (no file_id): apply_identities supports proposals keyed
        # by path; this is exactly the input that left the DB stale before the fix.
        props = [{"path": str(f), "artist": "Real Artist", "title": "Real Song",
                  "album": "Real Album", "score": 0.98}]
        dry = core.apply_identities({"proposals": props, "apply": False})
        assert dry["dry_run"] and dry["would_retag"] == 1, dry
        applied = core.apply_identities({"proposals": props, "apply": True, "signature": dry["signature"]})
        assert applied.get("ok") and applied.get("retagged") == 1, applied
        # The DB `tags` reorganize reads MUST now carry the corrected identity.
        dbtags = dict(core.conn().execute(
            "SELECT t.key, t.value FROM tags t JOIN files fi ON fi.id=t.file_id WHERE fi.path=?",
            (str(f),)).fetchall())
        assert dbtags.get("artist") == "Real Artist" and dbtags.get("album") == "Real Album" \
            and dbtags.get("title") == "Real Song", \
            f"apply_identities left the DB tags stale -> reorganize will misfile: {dbtags}"
        # reorganize's PLANNED destination must use the NEW identity, not the stale one.
        plan = core.reorganize_source({"apply": False})
        dests = [str(s["to"]).replace("\\", "/") for s in plan.get("samples", [])]
        assert dests == ["Real Artist/Real Album/Real Song.flac"], \
            f"reorganize used the STALE identity instead of the corrected one: {dests}"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        else: os.environ.pop("HOME", None)
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_workspace_pointer_stable_across_entrypoints():
    """v0.8.26 gate (real-library defect): the app-global workspace pointer must
    resolve to the SAME location no matter which entry point starts the process.
    `python -m earcrate` sets sys.modules['__main__'].__file__ to the package
    __main__.py; the `earcrate` console script sets it to a wrapper in a bin/
    dir; the frozen single-file build sets it to the one script. The OLD
    visible_app_dir() anchored to that __main__ path (and fell back to cwd), so
    the package entry point and the CLI entry point wrote earcrate_workspace.json
    to DIFFERENT files and saw DIFFERENT workspaces. This gate simulates both
    resolution paths (and the cwd-sensitivity) and asserts they agree, with
    EARCRATE_HOME both unset and set. RED on the unfixed code: the two paths
    diverged."""
    import os, sys, tempfile
    from pathlib import Path
    from earcrate.core.util import visible_app_dir

    main = sys.modules["__main__"]
    saved_mf = getattr(main, "__file__", None)
    saved_ech = os.environ.get("EARCRATE_HOME")
    saved_cwd = os.getcwd()
    tmp = Path(tempfile.mkdtemp())
    try:
        # Two distinct, writable launcher homes: a package __main__.py and a
        # console-script wrapper living in a separate bin/ directory.
        pkg = tmp / "site-packages" / "earcrate"; pkg.mkdir(parents=True)
        binp = tmp / "venv" / "bin"; binp.mkdir(parents=True)

        def resolve_as(main_file, cwd):
            main.__file__ = str(main_file)
            os.chdir(str(cwd))
            return visible_app_dir().resolve()

        # --- EARCRATE_HOME UNSET: the divergent case on the unfixed code -------
        os.environ.pop("EARCRATE_HOME", None)
        pkg_dir = resolve_as(pkg / "__main__.py", pkg)      # `python -m earcrate`
        cli_dir = resolve_as(binp / "earcrate", binp)       # `earcrate configure ...`
        assert pkg_dir == cli_dir, (
            "workspace pointer diverges between package and CLI entry points: "
            f"{pkg_dir} != {cli_dir}")

        # cwd must not move the pointer either (it used to fall back to cwd).
        cwd_a = resolve_as(pkg / "__main__.py", pkg)
        cwd_b = resolve_as(pkg / "__main__.py", binp)
        assert cwd_a == cwd_b, f"pointer is cwd-dependent: {cwd_a} != {cwd_b}"

        # --- EARCRATE_HOME SET: honored identically by both entry points -------
        home = tmp / "explicit_home"
        os.environ["EARCRATE_HOME"] = str(home)
        h_pkg = resolve_as(pkg / "__main__.py", pkg)
        h_cli = resolve_as(binp / "earcrate", binp)
        assert h_pkg == h_cli == home.resolve(), (
            f"EARCRATE_HOME not honored consistently: {h_pkg} / {h_cli} / {home}")
    finally:
        if saved_mf is not None: main.__file__ = saved_mf
        else: main.__dict__.pop("__file__", None)
        if saved_ech is not None: os.environ["EARCRATE_HOME"] = saved_ech
        else: os.environ.pop("EARCRATE_HOME", None)
        os.chdir(saved_cwd)


def test_identify_parses_acoustid_and_guards_key():
    """v0.8.14+ gate: AcoustID response parsing picks the best-score recording and
    extracts artist/title/album/mbid; empty/error responses are handled; the
    fingerprint path works when fpcalc is present; identify refuses without a key.
    v0.8.26 hardening: realistic response shapes pin the parse/selection defects
    that produced ~0 identities, and an offline check pins the lookup request body."""
    import shutil, tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    core = EarcrateCore.__new__(EarcrateCore)
    sample = {"status": "ok", "results": [
        {"score": 0.42, "id": "x", "recordings": [{"id": "wrong", "title": "Nope", "artists": [{"name": "NopeBand"}]}]},
        {"score": 0.98, "id": "y", "recordings": [{"id": "mbid-123", "title": "Ace of Spades",
            "artists": [{"id": "a", "name": "Motörhead"}], "releasegroups": [{"id": "rg", "title": "Ace of Spades"}]}]}]}
    m = core._parse_acoustid(sample)["match"]
    assert m["artist"] == "Motörhead" and m["title"] == "Ace of Spades" and m["album"] == "Ace of Spades"
    assert m["mbid"] == "mbid-123" and m["score"] == 0.98
    assert core._parse_acoustid({"status": "ok", "results": []})["match"] is None
    assert core._parse_acoustid({"status": "error", "error": {"message": "bad"}})["ok"] is False

    # -- Realistic AcoustID shapes that pinned real defects (v0.8.26) ----------
    # (1) A single result maps to several MusicBrainz recordings; the first is a
    # bare stub with no metadata. Taking recordings[0] blindly threw the real
    # match away (a prime suspect for ~0 identities on the real run).
    stub_first = {"status": "ok", "results": [{"score": 0.91, "id": "r", "recordings": [
        {"id": "bare"},
        {"id": "real", "title": "Real Song", "artists": [{"name": "Real Artist"}],
         "releasegroups": [{"id": "g", "title": "Real Album", "type": "Album"}]}]}]}
    m1 = core._parse_acoustid(stub_first)["match"]
    assert m1 is not None and m1["title"] == "Real Song", "must not discard a match hidden behind a stub recording"
    assert m1["artist"] == "Real Artist" and m1["album"] == "Real Album" and m1["mbid"] == "real"

    # (2) Multi-artist credit must honour joinphrase, not flatten to "; ".
    feat = {"status": "ok", "results": [{"score": 0.9, "id": "r", "recordings": [
        {"id": "z", "title": "Empire State of Mind",
         "artists": [{"name": "Jay-Z", "joinphrase": " feat. "}, {"name": "Alicia Keys"}]}]}]}
    assert core._parse_acoustid(feat)["match"]["artist"] == "Jay-Z feat. Alicia Keys"
    # ...but with no joinphrases present, still degrade to a readable list.
    plain2 = {"status": "ok", "results": [{"score": 0.9, "id": "r", "recordings": [
        {"id": "z", "title": "Split", "artists": [{"name": "A"}, {"name": "B"}]}]}]}
    assert core._parse_acoustid(plain2)["match"]["artist"] == "A; B"

    # (3) Album selection must prefer the studio album over a compilation that
    # MusicBrainz happens to list first.
    rgs = {"status": "ok", "results": [{"score": 0.88, "id": "r", "recordings": [
        {"id": "z", "title": "Song", "artists": [{"name": "Band"}], "releasegroups": [
            {"id": "c", "title": "Now 50", "type": "Album", "secondarytypes": ["Compilation"]},
            {"id": "a", "title": "Debut", "type": "Album"}]}]}]}
    assert core._parse_acoustid(rgs)["match"]["album"] == "Debut", "prefer the album over the compilation"

    # (4) Missing releasegroups is fine (album None), match still stands; and a
    # low-score result loses to a high-score one even when listed first.
    norg = {"status": "ok", "results": [
        {"score": 0.97, "id": "hi", "recordings": [{"id": "h", "title": "Hit", "artists": [{"name": "Star"}]}]},
        {"score": 0.20, "id": "lo", "recordings": [{"id": "l", "title": "Demo", "artists": [{"name": "Nobody"}]}]}]}
    mn = core._parse_acoustid(norg)["match"]
    assert mn["title"] == "Hit" and mn["album"] is None and mn["score"] == 0.97

    # (5) A result whose recordings carry no title/artists is not a usable match.
    empty = {"status": "ok", "results": [{"score": 0.8, "id": "r", "recordings": [{"id": "only"}]}]}
    assert core._parse_acoustid(empty)["match"] is None

    # -- Request building (offline): the lookup body must be well-formed. The
    # bug: meta was "recordings+releasegroups+compress" with literal "+", which
    # urlencode turns into %2B so AcoustID reads ONE bogus token and returns no
    # recordings. Live-library evidence requires the recordings field alone.
    import urllib.parse as _u
    params = core._acoustid_params("AQAB_fake_fp", 213.7, "MYKEY")
    body = _u.urlencode(params)
    decoded = _u.parse_qs(body)
    meta_tokens = decoded["meta"][0].split()
    assert meta_tokens == ["recordings"], "combined AcoustID meta fields broke the real-library lookup"
    assert decoded["client"][0] == "MYKEY" and decoded["format"][0] == "json"
    assert decoded["duration"][0] == "214" and decoded["fingerprint"][0] == "AQAB_fake_fp"
    assert core.ACOUSTID_ENDPOINT.endswith("/v2/lookup")
    if shutil.which("fpcalc"):
        tmpwav = Path(tempfile.mkdtemp()) / "fp.wav"
        t = np.arange(44100 * 12) / 44100
        sf.write(str(tmpwav), (0.4 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        fp = core._fingerprint_file(tmpwav)
        assert fp.get("fingerprint") and fp.get("duration", 0) > 0, "fpcalc must yield a fingerprint"
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME"); sk = os.environ.get("EARCRATE_ACOUSTID_KEY")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp); os.environ.pop("EARCRATE_ACOUSTID_KEY", None)
    try:
        (tmp / "m").mkdir(); sf.write(str(tmp / "m" / "x.wav"), np.zeros((1000, 2), "float32"), 44100)
        c = EarcrateCore(); c.configure({"master_root": str(tmp / "m"), "working_root": str(tmp / "w"), "agent_root": str(tmp / "a")})
        assert c.identify_tracks({})["ok"] is False, "identify must refuse without an API key"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)
        if sk is not None: os.environ["EARCRATE_ACOUSTID_KEY"] = sk


def test_workspace_migration_previews_then_executes():
    """v0.8.1 migration gate: the one-time cleanup SIMULATES (touching nothing),
    a stale-plan apply refuses, and an approved apply moves reusable buffalo to
    new homes, quarantines non-conforming files under legacy/ (never deletes),
    preserves human judgments in the DB, and never touches the music library."""
    import tempfile, sqlite3, os
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    saved_home = os.environ.get("HOME"); saved_ech = os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp)          # isolate the legacy-workspace scan
    os.environ["EARCRATE_HOME"] = str(tmp) # isolate the workspace pointer (no cwd cross-test pollution)
    try:
        music = tmp / "The Sample Factory"; music.mkdir()
        (music / "song.mp3").write_bytes(b"\x00" * 512)
        before_music = {p.name for p in music.iterdir()}
        old = tmp / "old_ws"
        (old / "agent" / "cache" / "analysis").mkdir(parents=True)
        (old / "work" / "renders").mkdir(parents=True)
        (old / "agent" / "notes").mkdir(parents=True)
        db = sqlite3.connect(str(old / "agent" / "jukebreaker.sqlite"))
        db.execute("CREATE TABLE judged(id TEXT, verdict TEXT)")
        db.execute("INSERT INTO judged VALUES('atom1','approved')"); db.commit(); db.close()
        (old / "agent" / "cache" / "analysis" / "abc-gt-v0.6.1-earcrate-feasibility.npz").write_bytes(b"NPZ")
        (old / "work" / "renders" / "mix1.wav").write_bytes(b"RIFFWAVE")
        (old / "agent" / "config_pointer.json").write_text("{}")
        (old / "agent" / "notes" / "random.txt").write_text("keep me")
        core = EarcrateCore()
        ws = str(tmp / "The Sample Factory — EarCrate")
        data = {"music_folder": str(music), "workspace_folder": ws, "sources": [str(old)]}
        plan = core.plan_workspace_migration(data)
        assert plan["dry_run"] and plan["source_readonly"]
        assert len(plan["legacy_sources"]) == 1
        assert (old / "agent" / "jukebreaker.sqlite").exists(), "dry run must touch nothing"
        assert any(a["op"] == "migrate-db" for a in plan["actions"])
        assert any(a["op"] == "quarantine" and a["from"].endswith("random.txt") for a in plan["actions"])
        assert any(a["op"] == "scrub" and a["from"].endswith("config_pointer.json") for a in plan["actions"])
        assert core.apply_workspace_migration({**data, "signature": "stale"})["ok"] is False
        res = core.apply_workspace_migration({**data, "signature": plan["signature"]})
        assert res["ok"], res
        H = core._migration_homes(ws)
        nd = sqlite3.connect(str(H["db"]))
        row = nd.execute("SELECT verdict FROM judged WHERE id='atom1'").fetchone(); nd.close()
        assert row and row[0] == "approved", "human judgment must survive"
        assert (H["cache_analysis"] / "abc-gt-v0.6.1-earcrate-feasibility.npz").exists()
        assert (H["renders"] / "mix1.wav").exists()
        assert list(H["legacy"].rglob("random.txt")) and list(H["scrubbed"].rglob("config_pointer.json"))
        assert Path(res["journal"]).exists()
        assert {p.name for p in music.iterdir()} == before_music, "music library must be untouched"
    finally:
        if saved_home is not None: os.environ["HOME"] = saved_home
        if saved_ech is not None: os.environ["EARCRATE_HOME"] = saved_ech
        else: os.environ.pop("EARCRATE_HOME", None)


def test_reorganize_source_in_place_previews_and_reverses():
    """v0.8.6 gate: in-place source reorganize SIMULATES (touching nothing), a
    stale plan refuses, APPLY moves files into Artist/Album/NN-Title within the
    source (unidentifiable -> _unsorted/, DB paths follow), and ROLLBACK fully
    restores the original layout. Nothing is deleted."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    tmp = Path(tempfile.mkdtemp()); saved = os.environ.get("HOME"); os.environ["HOME"] = str(tmp)
    try:
        src = tmp / "The Sample Factory"; (src / "dump").mkdir(parents=True)
        def wav(p): sf.write(str(p), np.zeros((2000, 2), dtype='float32'), 44100)
        wav(src / "dump" / "Aphex Twin - Windowlicker.wav")
        wav(src / "dump" / "Boards of Canada - Roygbiv.wav")
        wav(src / "noise.wav")
        core = EarcrateCore()
        core.configure({"master_root": str(src), "working_root": str(tmp / "work"), "agent_root": str(tmp / "agent")})
        core.scan()
        before = set(p.relative_to(src).as_posix() for p in src.rglob("*.wav"))
        plan = core.reorganize_source({"apply": False})
        assert plan["dry_run"] and plan["planned"] >= 2 and plan["quarantined"] == 1
        assert before == set(p.relative_to(src).as_posix() for p in src.rglob("*.wav")), "dry run must not move anything"
        assert core.reorganize_source({"apply": True, "signature": "stale"})["ok"] is False
        res = core.reorganize_source({"apply": True, "signature": plan["signature"]})
        assert res["ok"] and res["moved"] >= 2
        tree = set(p.relative_to(src).as_posix() for p in src.rglob("*.wav"))
        assert "Aphex Twin/Unknown Album/Windowlicker.wav" in tree
        assert any(t.startswith("_unsorted/") for t in tree), "unidentifiable must be quarantined, not lost"
        db = core.conn()
        assert all(Path(r["path"]).exists() for r in db.execute("SELECT path FROM files WHERE root='master'").fetchall())
        rb = core.rollback_reorganize({"journal": res["journal"], "apply": True})
        assert rb["ok"] and rb["restored"] >= 2
        assert (src / "dump" / "Aphex Twin - Windowlicker.wav").exists(), "rollback must restore originals"
    finally:
        if saved is not None:
            os.environ["HOME"] = saved


def test_deep_clean_hears_junk_but_keeps_voice():
    """v0.8.11 gate: the deep-clean classifier judges by the AUDIO GRAPH, not
    tags or genre. Real music AND voice/spoken-word both pass; only silence,
    broadband static, and non-decodable/corrupt files are flagged. Empty and
    art-only folders are detected. Nothing is moved (assessment only)."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    tmp = Path(tempfile.mkdtemp()); saved = os.environ.get("HOME"); os.environ["HOME"] = str(tmp)
    try:
        root = tmp / "lib"; root.mkdir()
        sr = 22050; t = np.arange(sr * 20) / sr
        def w(n, y): sf.write(str(root / n), np.clip(y, -1, 1).astype(np.float32), sr)
        w("music.wav", 0.4 * (np.sin(2*np.pi*220*t) + 0.5*np.sin(2*np.pi*440*t)))
        env = 0.5 + 0.5*np.sin(2*np.pi*3*t)
        w("voice.wav", env * (np.sin(2*np.pi*140*t) + 0.4*np.sin(2*np.pi*280*t)) + 0.05*np.random.default_rng(1).standard_normal(t.size))
        w("silence.wav", np.zeros_like(t))
        w("static.wav", 0.3 * np.random.default_rng(2).standard_normal(t.size))
        (root / "notaudio.mp3").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + os.urandom(3000))
        (root / "Empty").mkdir()
        (root / "ArtOnly").mkdir(); (root / "ArtOnly" / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        core = EarcrateCore()
        core.configure({"master_root": str(root), "working_root": str(tmp / "w"), "agent_root": str(tmp / "a")})
        assert core.assess_track_audio(root / "music.wav")["real"] is True
        assert core.assess_track_audio(root / "voice.wav")["real"] is True, "spoken-word-like audio must NOT be junk"
        assert core.assess_track_audio(root / "silence.wav")["real"] is False
        assert core.assess_track_audio(root / "static.wav")["real"] is False
        assert core.assess_track_audio(root / "notaudio.mp3")["real"] is False
        res = core.deep_clean_scan({})
        assert res["dry_run"] and res["real_songs"] == 2 and res["junk_count"] == 3
        assert res["empty_folder_count"] >= 1 and res["art_only_folder_count"] >= 1
    finally:
        if saved is not None:
            os.environ["HOME"] = saved


def test_apply_identities_retags_and_reverses():
    """v0.8.15 gate: apply-identities rewrites tags from AcoustID proposals --
    dry-run changes nothing, --apply writes artist/title/album to disk AND the
    DB (so reorganize sees it), gated by a signature, and identify-rollback
    restores the original tags."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    from mutagen import File as MF
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        lib = tmp / "lib"; lib.mkdir()
        f = lib / "track.flac"
        t = np.arange(44100 * 3) / 44100
        sf.write(str(f), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        mf = MF(str(f), easy=True); mf["artist"] = ["BIRP Playlist"]; mf["title"] = ["wrong"]; mf.save()
        core = EarcrateCore(); core.configure({"master_root": str(lib), "working_root": str(tmp / "w"), "agent_root": str(tmp / "a")})
        core.scan()
        fid = core.conn().execute("SELECT id FROM files WHERE root='master' LIMIT 1").fetchone()["id"]
        props = [{"path": str(f), "file_id": fid, "artist": "Motörhead", "title": "Ace of Spades", "album": "Ace of Spades", "score": 0.98}]
        dry = core.apply_identities({"proposals": props, "apply": False})
        assert dry["dry_run"] and dry["would_retag"] == 1
        assert MF(str(f), easy=True).get("artist")[0] == "BIRP Playlist", "dry-run must not change tags"
        assert core.apply_identities({"proposals": props, "apply": True, "signature": "stale"})["ok"] is False
        res = core.apply_identities({"proposals": props, "apply": True, "signature": dry["signature"]})
        assert res["ok"] and res["retagged"] == 1
        assert MF(str(f), easy=True).get("artist")[0] == "Motörhead"
        assert core.conn().execute("SELECT value FROM tags WHERE file_id=? AND key='artist'", (fid,)).fetchone()["value"] == "Motörhead"
        # low-confidence proposals are skipped
        low = core.apply_identities({"proposals": [{"path": str(f), "file_id": fid, "artist": "X", "score": 0.4}], "apply": False})
        assert low["would_retag"] == 0, "sub-threshold matches must not be applied"
        assert core.rollback_identities({"journal": res["journal"]})["dry_run"], "identify-rollback must preview by default"
        assert MF(str(f), easy=True).get("artist")[0] == "Motörhead", "dry-run rollback must not restore tags"
        rb = core.rollback_identities({"journal": res["journal"], "apply": True})
        assert rb["ok"] and rb["restored"] == 1
        assert MF(str(f), easy=True).get("artist")[0] == "BIRP Playlist", "rollback must restore tags"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_acoustid_requests_recordings_meta_only():
    """v0.8.26 gate: the AcoustID lookup must request meta=recordings ALONE.
    Real-library evidence (2026-07-12): combining releasegroups into meta made
    the API return bare {id, score} results, so identify went 0/585 despite
    correct fingerprint matches. This pins the request body."""
    import urllib.parse
    from unittest.mock import patch
    core = EarcrateCore.__new__(EarcrateCore)
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"status": "ok", "results": []}'

    def fake_urlopen(req, timeout=0):
        captured["body"] = req.data.decode("utf-8")
        return _Resp()

    with patch("urllib.request.urlopen", fake_urlopen):
        res = core._acoustid_lookup("FAKEFP", 200.0, "key123")
    assert res["ok"] and res["match"] is None
    params = urllib.parse.parse_qs(captured["body"])
    assert params["meta"] == ["recordings"], f"meta must be recordings alone, got {params.get('meta')}"


def test_apply_identities_backfills_db_without_file_id():
    """v0.8.26 gate: proposals WITHOUT file_id (e.g. from an external driver)
    must still backfill the DB tag cache by path, so a following reorganize
    never plans against stale pre-retag identities."""
    import tempfile, os
    from pathlib import Path
    import numpy as np, soundfile as sf
    from mutagen import File as MF
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp)
    try:
        lib = tmp / "lib"; lib.mkdir()
        f = lib / "track.flac"
        t = np.arange(44100 * 3) / 44100
        sf.write(str(f), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
        mf = MF(str(f), easy=True); mf["artist"] = ["Wrong"]; mf["title"] = ["wrong"]; mf.save()
        core = EarcrateCore(); core.configure({"master_root": str(lib), "working_root": str(tmp / "w"), "agent_root": str(tmp / "a")})
        core.scan()
        fid = core.conn().execute("SELECT id FROM files WHERE root='master' LIMIT 1").fetchone()["id"]
        props = [{"path": str(f), "artist": "Motörhead", "title": "Ace of Spades", "score": 0.98}]  # no file_id
        dry = core.apply_identities({"proposals": props, "apply": False})
        assert dry["would_retag"] == 1
        res = core.apply_identities({"proposals": props, "apply": True, "signature": dry["signature"]})
        assert res["ok"] and res["retagged"] == 1 and res["db_unresolved"] == 0
        row = core.conn().execute("SELECT value FROM tags WHERE file_id=? AND key='artist'", (fid,)).fetchone()
        assert row and row["value"] == "Motörhead", "DB tag cache must be backfilled by path when file_id is absent"
    finally:
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_pointer_visible_beats_legacy_and_demo_seed_retires():
    """v0.8.26 gates, two related honesty fixes:
    (1) pointer resolution: a visible workspace pointer found by searching the
        legitimate locations must win over the legacy hidden AppData pointer,
        so a driver script importing the package resolves the SAME workspace
        as the CLI (the 2026-07-12 stale-legacy-workspace gotcha);
    (2) demo warm-up self-retires: once a real (non-demo) render exists,
        seed_demo_renders refuses instead of planting synthetic tracks next to
        real output."""
    import tempfile, os, json as _json
    from unittest.mock import patch
    from pathlib import Path
    import numpy as np, soundfile as sf
    tmp = Path(tempfile.mkdtemp())
    sh, se = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    cwd = os.getcwd()
    os.environ["HOME"] = str(tmp); os.environ["EARCRATE_HOME"] = str(tmp / "homeA")
    try:
        (tmp / "homeA").mkdir()
        lib = tmp / "lib"; lib.mkdir()
        sf.write(str(lib / "x.wav"), np.zeros((44100, 2), "float32"), 44100)
        core = EarcrateCore()
        core.configure({"master_root": str(lib), "working_root": str(tmp / "w"), "agent_root": str(tmp / "a")})
        real_master = str(core.config.master_root)
        # a stale legacy pointer to a DIFFERENT workspace, planted in AppData
        lib2 = tmp / "oldlib"; lib2.mkdir()
        core2 = EarcrateCore()
        core2.configure({"master_root": str(lib2), "working_root": str(tmp / "w2"), "agent_root": str(tmp / "a2")})
        legacy = core.legacy_pointer_path
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(_json.dumps({"config_json": str((tmp / "a2") / "config.json")}), encoding="utf-8")
        # restore the REAL pointer (core2.configure overwrote the shared EARCRATE_HOME one)
        (tmp / "homeA" / "earcrate_workspace.json").write_text(
            _json.dumps({"config_json": str((tmp / "a") / "config.json")}), encoding="utf-8")
        # Simulate a driver script: no EARCRATE_HOME, with the portable pointer
        # beside that script (the highest-priority legitimate visible location).
        os.environ.pop("EARCRATE_HOME", None)
        os.chdir(str(tmp / "homeA"))
        driver_dir = tmp / "driver"; driver_dir.mkdir()
        (driver_dir / "earcrate_workspace.json").write_text(
            _json.dumps({"config_json": str((tmp / "a") / "config.json")}), encoding="utf-8")
        main_mod = sys.modules["__main__"]
        saved_main_file = getattr(main_mod, "__file__", None)
        main_mod.__file__ = str(driver_dir / "driver.py")
        try:
            with patch("earcrate.app.visible_app_dir", return_value=driver_dir):
                driver = EarcrateCore()
        finally:
            if saved_main_file is not None: main_mod.__file__ = saved_main_file
        assert driver.config is not None, "driver must resolve a workspace"
        assert str(driver.config.master_root) == real_master, \
            "visible driver pointer must beat the legacy AppData pointer"
        # (2) demo seeding self-retires next to a real render
        os.environ["EARCRATE_HOME"] = str(tmp / "homeA")
        seeded = core.seed_demo_renders(count=1)
        assert seeded["seeded"] == 1, "fresh workspace must seed the demo"
        again = core.seed_demo_renders(count=1)
        assert again["seeded"] == 1, "demo-only renders must not block re-seeding"
        renders = core.config.working_root / "renders"
        sf.write(str(renders / "real.wav"), np.zeros((44100, 2), "float32"), 44100)
        (renders / "real.render_report.json").write_text(_json.dumps({"engine_version": "x", "quality_gate": {"passed": True}}), encoding="utf-8")
        after = core.seed_demo_renders(count=1)
        assert after.get("skipped") and after["seeded"] == 0, "real render present -> demo seeding must retire"
    finally:
        os.chdir(cwd)
        if sh is not None: os.environ["HOME"] = sh
        if se is not None: os.environ["EARCRATE_HOME"] = se
        else: os.environ.pop("EARCRATE_HOME", None)


def test_pointer_search_skips_invalid_candidates():
    """A stale first pointer must not mask a later valid portable workspace."""
    import json as _json
    import tempfile
    from unittest.mock import patch
    from pathlib import Path
    import earcrate.app as appmod

    tmp = Path(tempfile.mkdtemp())
    stale, valid, legacy = tmp / "stale", tmp / "valid", tmp / "legacy"
    master, work, agent = tmp / "music", tmp / "work", tmp / "agent"
    for path in (stale, valid, legacy, master, work, agent):
        path.mkdir()
    (stale / "earcrate_workspace.json").write_text(
        _json.dumps({"config_json": str(tmp / "missing-config.json")}), encoding="utf-8")
    cfg_path = valid / "config.json"
    cfg_path.write_text(_json.dumps({
        "master_root": str(master), "working_root": str(work), "agent_root": str(agent),
        "sample_rate": 44100, "workers": 1, "seed": 1337, "analysis_seconds": 8,
    }), encoding="utf-8")
    (valid / "earcrate_workspace.json").write_text(
        _json.dumps({"config_json": str(cfg_path)}), encoding="utf-8")

    old_visible = appmod.visible_app_dir
    old_search = appmod.pointer_search_dirs
    old_state = appmod.app_state_dir
    appmod.visible_app_dir = lambda: stale
    appmod.pointer_search_dirs = lambda: [stale, valid]
    appmod.app_state_dir = lambda: legacy
    try:
        core = appmod.EarcrateCore()
    finally:
        appmod.visible_app_dir = old_visible
        appmod.pointer_search_dirs = old_search
        appmod.app_state_dir = old_state
    assert core.config is not None, "valid later pointer was masked by a stale first candidate"
    assert core.config.master_root == master.resolve()
    assert core.pointer_resolved_from == valid / "earcrate_workspace.json"


def test_demucs_uses_released_model_id():
    """The real Demucs path must request an actual released model name."""
    import sys as _sys
    import types
    from unittest.mock import patch
    from earcrate.providers.stems import DemucsStemProvider

    seen = []
    demucs_pkg = types.ModuleType("demucs"); demucs_pkg.__path__ = []
    separate_mod = types.ModuleType("demucs.separate")
    pretrained_mod = types.ModuleType("demucs.pretrained")
    apply_mod = types.ModuleType("demucs.apply")
    audio_mod = types.ModuleType("demucs.audio")
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = types.SimpleNamespace(is_available=lambda: False)

    def _get_model(name):
        seen.append(name)
        raise RuntimeError("stop-after-model-selection")

    pretrained_mod.get_model = _get_model
    apply_mod.apply_model = lambda *a, **k: None
    audio_mod.AudioFile = object
    modules = {
        "torch": torch_mod,
        "demucs": demucs_pkg,
        "demucs.separate": separate_mod,
        "demucs.pretrained": pretrained_mod,
        "demucs.apply": apply_mod,
        "demucs.audio": audio_mod,
    }
    with patch.dict(_sys.modules, modules):
        try:
            DemucsStemProvider()._run_demucs("unused.wav", ["vocals"])
        except RuntimeError as exc:
            assert str(exc) == "stop-after-model-selection"
        else:
            raise AssertionError("model selection sentinel did not fire")
    assert seen == ["htdemucs"], f"invalid Demucs model requested: {seen}"


def test_pcm_identity_covers_full_track_not_analysis_prefix():
    """Tracks sharing an analyzed prefix but differing later need distinct stem keys."""
    import tempfile
    from pathlib import Path
    import numpy as np
    import soundfile as sf
    import inspect
    from earcrate.analyze.decode import decoded_audio_sha256
    from earcrate.analyze.features import analyze_file_worker

    tmp = Path(tempfile.mkdtemp())
    sr = 44100
    t = np.arange(sr, dtype=np.float32) / sr
    prefix = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    tail_a = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    tail_b = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    a, b, dup = tmp / "a.wav", tmp / "b.wav", tmp / "dup.wav"
    sf.write(str(a), np.concatenate([prefix, tail_a]), sr)
    sf.write(str(b), np.concatenate([prefix, tail_b]), sr)
    sf.write(str(dup), np.concatenate([prefix, tail_a]), sr)

    ha = decoded_audio_sha256(a, sr, 2.0)
    hb = decoded_audio_sha256(b, sr, 2.0)
    hd = decoded_audio_sha256(dup, sr, 2.0)
    assert ha != hb, "same prefix collided despite different full-track audio"
    assert ha == hd, "identical full canonical PCM did not deduplicate"
    worker_source = inspect.getsource(analyze_file_worker)
    assert "decoded_audio_sha256" in worker_source and 'pcm_scope=np.asarray("full")' in worker_source, \
        "analysis worker is not persisting the complete-PCM identity contract"


def test_full_pcm_scope_migrates_without_changing_segment_version():
    """Legacy identities migrate safely; only proven/ambiguous replacements bump."""
    import inspect
    import sqlite3
    from earcrate.app import EarcrateCore, ANALYZER_VERSION

    core = EarcrateCore.__new__(EarcrateCore)
    core.db = sqlite3.connect(":memory:")
    core.db.row_factory = sqlite3.Row
    core.db.execute(
        "CREATE TABLE files(id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,root TEXT NOT NULL,"
        "size_bytes INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,sha256 TEXT,audio_sha256 TEXT,"
        "container TEXT,codec TEXT,bitrate_kbps INTEGER,sample_rate INTEGER,channels INTEGER,"
        "duration_s REAL,scanned_at TEXT NOT NULL)"
    )
    core.create_schema()
    cols = {r["name"] for r in core.db.execute("PRAGMA table_info(files)").fetchall()}
    assert {"audio_sha256_scope", "audio_generation", "present"} <= cols, \
        "existing workspaces do not receive the identity-scope/generation migration"
    loop_cols = {r["name"] for r in core.db.execute("PRAGMA table_info(loops)").fetchall()}
    assert {"source_audio_sha256", "source_audio_generation"} <= loop_cols
    analyze_source = inspect.getsource(EarcrateCore.analyze)
    assert "audio_sha256_scope" in analyze_source and "!='full'" in analyze_source, \
        "current-version feature rows with legacy prefix hashes are not selected for repair"
    render_source = inspect.getsource(EarcrateCore.render_mashup.__wrapped__)
    assert "CASE WHEN f.audio_sha256_scope='full'" in render_source, \
        "renderer can still consume an unverified legacy prefix identity"
    assert "run Analyze before stem separation" in render_source, \
        "upgraded rows silently lose stem behavior without an actionable receipt"
    scan_source = inspect.getsource(EarcrateCore.scan)
    set_pcm_source = inspect.getsource(EarcrateCore._set_pcm)
    assert "stale_full" in scan_source and "sha256=NULL" in scan_source, \
        "a changed path is not queued for a trusted full-PCM comparison"
    assert "audio_sha256=NULL" not in scan_source, \
        "scan discarded the prior trusted identity before it could compare PCM"
    assert '{"full", "stale_full"}' in set_pcm_source and "legacy_stale" in set_pcm_source, \
        "PCM replacement does not advance the source generation after comparison"

    # Ordinary legacy scan -> analyze lineage upgrades in generation zero. If the
    # old scanner touched the path after its last analysis, provenance is unknown
    # and migration conservatively advances it rather than blessing old judgments.
    core.db.executemany(
        "INSERT INTO files(id,path,root,size_bytes,mtime_ns,sha256,audio_sha256,scanned_at) "
        "VALUES(?,?,'master',1,1,?,'legacy-prefix',?)",
        [
            ("identity-file", "/identity.wav", "safe-byte-hash", "2026-01-01T00:00:00Z"),
            ("suspect-legacy", "/suspect.wav", "stale-byte-hash", "2026-01-03T00:00:00Z"),
        ],
    )
    core.db.executemany(
        "INSERT INTO features(file_id,analyzed_at,analyzer_version) VALUES(?,?,?)",
        [
            ("identity-file", "2026-01-02T00:00:00Z", "legacy"),
            ("suspect-legacy", "2026-01-02T00:00:00Z", "legacy"),
        ],
    )
    core.create_schema()
    assert core.db.execute(
        "SELECT audio_sha256_scope FROM files WHERE id='identity-file'"
    ).fetchone()[0] is None, "ordinary legacy lineage was needlessly invalidated"
    assert core.db.execute(
        "SELECT audio_sha256_scope FROM files WHERE id='suspect-legacy'"
    ).fetchone()[0] == "legacy_stale", "old rescan/replacement provenance was trusted"
    assert core.db.execute(
        "SELECT sha256 FROM files WHERE id='suspect-legacy'"
    ).fetchone()[0] is None, "quarantined legacy row retained a stale analysis-cache key"
    assert core.db.execute(
        "SELECT sha256 FROM files WHERE id='identity-file'"
    ).fetchone()[0] == "safe-byte-hash", "ordinary legacy lineage lost its valid byte hash"
    core._set_pcm("suspect-legacy", "pcm-new")
    assert core.db.execute(
        "SELECT audio_generation FROM files WHERE id='suspect-legacy'"
    ).fetchone()[0] == 1, "ambiguous legacy replacement stayed in generation zero"

    # A retag with identical trusted PCM preserves the generation/judgments;
    # genuinely different PCM advances it.
    core._set_pcm("identity-file", "pcm-a")
    row = core.db.execute(
        "SELECT audio_sha256,audio_sha256_scope,audio_generation FROM files WHERE id='identity-file'"
    ).fetchone()
    assert row["audio_sha256"] == "pcm-a" and row["audio_sha256_scope"] == "full"
    assert row["audio_generation"] == 0, "legacy prefix migration fabricated a replacement generation"
    core.db.execute("UPDATE files SET audio_sha256_scope='stale_full' WHERE id='identity-file'")
    core._set_pcm("identity-file", "pcm-a")
    assert core.db.execute(
        "SELECT audio_generation FROM files WHERE id='identity-file'"
    ).fetchone()[0] == 0, "metadata-only retag invalidated unchanged PCM judgments"
    core.db.execute("UPDATE files SET audio_sha256_scope='stale_full' WHERE id='identity-file'")
    core._set_pcm("identity-file", "pcm-b")
    assert core.db.execute(
        "SELECT audio_generation FROM files WHERE id='identity-file'"
    ).fetchone()[0] == 1, "same-path audio replacement retained the old generation"

    core.db.execute(
        "INSERT INTO files(id,path,root,size_bytes,mtime_ns,audio_sha256,audio_sha256_scope,present,scanned_at) "
        "VALUES('stale-progress','/stale.wav','master',1,1,'old-pcm','stale_full',1,'now')"
    )
    core.db.execute(
        "INSERT INTO features(file_id,analyzed_at,analyzer_version) VALUES('stale-progress','now',?)",
        (ANALYZER_VERSION,),
    )
    core.db.execute(
        "UPDATE features SET analyzer_version=? WHERE file_id IN ('identity-file','suspect-legacy')",
        (ANALYZER_VERSION,),
    )
    assert core._trusted_analyzed_count() == 2, \
        "harvest progress counted a current-version feature row with stale PCM scope"

    core.db.execute(
        """INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,status,created_at,
                             source_audio_sha256,source_audio_generation)
           VALUES('old-loop','identity-file',0,4,2,'texture',0.9,'approved','now','pcm-a',0),
                 ('current-loop','identity-file',0,4,2,'texture',0.8,'candidate','now','pcm-b',1)"""
    )
    visible = core.list_loops()["items"]
    assert [r["id"] for r in visible] == ["current-loop"], \
        "review surface exposed a loop from the replaced sound"
    core.db.execute("UPDATE files SET present=0 WHERE id='identity-file'")
    assert core.list_loops()["items"] == [], "missing file remained active in loop review"
    core.db.execute("UPDATE files SET present=1 WHERE id='identity-file'")
    try:
        core.set_loop_status("old-loop", "rejected")
    except ValueError as exc:
        assert "stale source generation" in str(exc)
    else:
        raise AssertionError("review endpoint mutated a historical loop")
    core.auto_approve_quota(max_loops=1)
    assert [r["id"] for r in core.approved_loop_pool()] == ["current-loop"], \
        "quota/pool reactivated a historical loop"
    core.db.execute(
        """INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,
                                  start_s,end_s,bars,score,status,created_at)
           VALUES('old-atom','old-loop','identity-file','girl_talk_v1','TEXTURE','texture',
                  0,4,2,0.9,'approved','now'),
                 ('current-atom','current-loop','identity-file','girl_talk_v1','TEXTURE','texture',
                  0,4,2,0.8,'approved','now')"""
    )
    assert [r["id"] for r in core.list_ear_atoms()["items"]] == ["current-atom"], \
        "atom review exposed a judgment from the replaced sound"
    try:
        core.set_atom_judgment("old-atom", "girl_talk_v1", "rejected")
    except ValueError as exc:
        assert "current source generation" in str(exc)
    else:
        raise AssertionError("atom review endpoint mutated a historical judgment")
    core.db.execute(
        """INSERT INTO compatibility_edges(id,taste_profile,left_atom_id,right_atom_id,
                                             relation,score,reasons_json,created_at)
           VALUES('stale-edge','girl_talk_v1','current-atom','old-atom',
                  'contrast',0.7,'{}','now')"""
    )
    assert core.compatible_pairs_for_atom("current-atom")["items"] == [], \
        "pair review exposed an edge whose other endpoint is historical"
    try:
        core.set_pair_judgment("stale-edge", "girl_talk_v1", "approved")
    except ValueError as exc:
        assert "current source generation" in str(exc)
    else:
        raise AssertionError("pair review endpoint mutated a historical edge")

    extract_source = inspect.getsource(EarcrateCore.extract_loops)
    assert "source_audio_generation" in extract_source and "SELECT COUNT(*) FROM loops WHERE file_id" not in extract_source, \
        "historical loops still suppress extraction for a replacement generation"
    pool_source = inspect.getsource(EarcrateCore.approved_atom_pool)
    assert "source_audio_generation" in pool_source and "source_audio_sha256=f.audio_sha256" in pool_source, \
        "composer can select atoms measured from an older same-path sound"
    for operational in (
        EarcrateCore.list_loops,
        EarcrateCore.auto_approve_quota,
        EarcrateCore.approved_loop_pool,
        EarcrateCore.build_ear_crate,
        EarcrateCore.list_ear_atoms,
        EarcrateCore.set_atom_judgment,
        EarcrateCore.compatible_pairs_for_atom,
        EarcrateCore.set_pair_judgment,
    ):
        source = inspect.getsource(operational)
        assert ("source_audio_generation" in source and "source_audio_sha256" in source and "present" in source
                and "audio_sha256_scope='full'" in source), \
            f"{operational.__name__} still activates historical source generations"
    assert '"source_identity": stem_identity or info.get("audio_sha256")' in render_source, \
        "transform cache is not bound to the current full-track/stem identity"
    assert '"quality_mode": quality_mode' in render_source and '"transform_policy": transform_policy' in render_source, \
        "transform cache can cross incompatible render algorithms"
    assert '"source_stat"' not in render_source, \
        "metadata-only edits still invalidate transforms despite identical full PCM"


def test_scan_invalidates_before_probe_and_retires_missing(tmp_path):
    """A changed/missing path becomes inactive even when metadata probing fails."""
    import os
    import inspect
    from unittest.mock import patch
    from earcrate.app import EarcrateCore

    old_home, old_ec = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp_path)
    os.environ["EARCRATE_HOME"] = str(tmp_path)
    try:
        music, work, agent = tmp_path / "music", tmp_path / "work", tmp_path / "agent"
        for folder in (music, work, agent):
            folder.mkdir()
        source = music / "changed.wav"
        source.write_bytes(b"new bytes that deliberately fail ffprobe")
        core = EarcrateCore()
        core.configure({"master_root": str(music), "working_root": str(work), "agent_root": str(agent), "workers": 1})
        db = core.conn()
        db.execute(
            """INSERT INTO files(id,path,root,size_bytes,mtime_ns,sha256,audio_sha256,
                                  audio_sha256_scope,audio_generation,present,scanned_at)
               VALUES('f0',?,'master',1,1,'old-file-sha','old-pcm','full',0,1,'now')""",
            (str(source.resolve()),),
        )
        db.execute(
            """INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,status,created_at,
                                  source_audio_sha256,source_audio_generation)
               VALUES('l0','f0',0,1,1,'texture',0.9,'approved','now','old-pcm',0)"""
        )
        db.commit()

        with patch("earcrate.app.ffprobe_json", side_effect=RuntimeError("forced probe failure")):
            result = core.scan()
        row = db.execute(
            "SELECT sha256,audio_sha256_scope,present FROM files WHERE id='f0'"
        ).fetchone()
        assert row["sha256"] is None and row["audio_sha256_scope"] == "stale_full"
        assert row["present"] == 1 and result["failed"], "probe failure was not receipted"
        assert core.list_loops()["items"] == [], "changed source stayed active after probe failure"

        source.unlink()
        missing = core.scan()
        assert missing["missing"] == 1
        assert db.execute("SELECT present FROM files WHERE id='f0'").fetchone()[0] == 0
        analyzed = core.analyze(force=True)
        assert analyzed["analyzed"] == 0 and not analyzed["failed"], \
            "missing ledger row aborted or polluted Analyze"
        scan_source = inspect.getsource(EarcrateCore.scan)
        assert scan_source.index("stat_items.append") < scan_source.index("UPDATE files SET present=0"), \
            "scan mutates global presence before resolving per-path stat races"
        assert "db.rollback()" in scan_source, "fatal phase-1 scan errors can leak a partial presence transaction"
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_ec is not None:
            os.environ["EARCRATE_HOME"] = old_ec
        else:
            os.environ.pop("EARCRATE_HOME", None)


def test_canonical_decode_and_hash_select_same_audio_stream(tmp_path):
    """Multi-stream containers bind features/renders and identity to stream 0."""
    import hashlib
    import subprocess
    import numpy as np
    import soundfile as sf
    from earcrate.analyze.decode import decode_audio, decoded_audio_sha256

    sr = 44100
    t = np.arange(sr, dtype=np.float32) / sr
    first = tmp_path / "first.wav"
    second = tmp_path / "default-second.wav"
    container = tmp_path / "two-streams.mkv"
    sf.write(str(first), 0.2 * np.sin(2 * np.pi * 220 * t), sr, subtype="PCM_16")
    sf.write(str(second), 0.2 * np.sin(2 * np.pi * 880 * t), sr, subtype="PCM_16")
    cp = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(first), "-i", str(second),
         "-map", "0:a:0", "-map", "1:a:0", "-c:a", "copy",
         "-disposition:a:0", "0", "-disposition:a:1", "default", str(container)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert cp.returncode == 0, cp.stderr.decode("utf-8", "replace")
    expected = decode_audio(first, sr)
    decoded = decode_audio(container, sr)
    assert np.array_equal(decoded, expected), "decoder followed default stream instead of canonical 0:a:0"
    decoded_bytes = decoded.astype("<f4", copy=False).tobytes()
    assert decoded_audio_sha256(container, sr, 1.0) == hashlib.sha256(decoded_bytes).hexdigest(), \
        "full-track identity and feature/render decode selected different streams"


def test_render_preflight_rejects_post_analysis_source_mutation(tmp_path):
    """Changed source bytes fail with a durable receipt before WAV publication."""
    import json
    import os
    import numpy as np
    import soundfile as sf
    from pathlib import Path
    from unittest.mock import patch
    from earcrate.app import EarcrateCore
    from earcrate.analyze.decode import decoded_audio_sha256
    from earcrate.core.util import sha256_file

    old_home, old_ec = os.environ.get("HOME"), os.environ.get("EARCRATE_HOME")
    os.environ["HOME"] = str(tmp_path)
    os.environ["EARCRATE_HOME"] = str(tmp_path)
    try:
        music, work, agent = tmp_path / "music", tmp_path / "work", tmp_path / "agent"
        for folder in (music, work, agent):
            folder.mkdir()
        source = music / "mutated.wav"
        sr = 44100
        t = np.arange(sr, dtype=np.float32) / sr
        sf.write(str(source), 0.2 * np.sin(2 * np.pi * 220 * t), sr, subtype="PCM_16")
        st = source.stat()
        old_file_sha = sha256_file(source)
        old_pcm_sha = decoded_audio_sha256(source, sr, 1.0)
        sf.write(str(source), 0.2 * np.sin(2 * np.pi * 880 * t), sr, subtype="PCM_16")
        os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns))
        changed_stat = source.stat()
        assert changed_stat.st_size == st.st_size and changed_stat.st_mtime_ns == st.st_mtime_ns, \
            "fixture did not preserve stat while mutating source bytes"
        core = EarcrateCore()
        core.configure({"master_root": str(music), "working_root": str(work), "agent_root": str(agent), "workers": 1})
        db = core.conn()
        db.execute(
            """INSERT INTO files(id,path,root,size_bytes,mtime_ns,sha256,audio_sha256,
                                  audio_sha256_scope,audio_generation,present,scanned_at)
               VALUES('f0',?,'master',?,?,?,'pcm-current','full',0,1,'now')""",
            (str(source.resolve()), int(st.st_size), int(st.st_mtime_ns), old_file_sha),
        )
        db.execute("UPDATE files SET audio_sha256=? WHERE id='f0'", (old_pcm_sha,))
        db.execute(
            """INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,status,created_at,
                                  source_audio_sha256,source_audio_generation)
               VALUES('l0','f0',0,1,1,'texture',0.9,'approved','now',?,0)""",
            (old_pcm_sha,),
        )
        arrangement = {
            "bpm": 120,
            "seed": 1337,
            "params": {"quality_mode": "stable_deck", "post_render_gate": True},
            "sections": [{"bar_start": 0, "bars": 1, "layers": [{"loop_id": "l0", "role": "texture"}]}],
        }
        db.execute(
            "INSERT INTO mashups(id,name,seed,params_json,arrangement_json,created_at) VALUES('m0','mutation',1337,'{}',?,'now')",
            (json.dumps(arrangement),),
        )
        db.commit()
        dst = work / "must-not-exist.wav"
        before = set((agent / "runs").glob("*"))
        try:
            with patch("earcrate.app.sf.write", side_effect=AssertionError("sf.write must not run")):
                core.render_mashup("m0", dst)
        except RuntimeError as exc:
            assert "source file changed after analysis" in str(exc)
        else:
            raise AssertionError("post-analysis mutation reached render instead of preflight rejection")
        assert not dst.exists(), "preflight-rejected render published a WAV"
        created = list(set((agent / "runs").glob("*")) - before)
        assert len(created) == 1, "failed render did not leave exactly one run bundle"
        status = json.loads((created[0] / "status.json").read_text(encoding="utf-8"))
        report = json.loads((created[0] / "report.json").read_text(encoding="utf-8"))
        assert status["state"] == "failed" and status["ok"] is False
        assert report["state"] == "failed" and "source file changed" in report["outcome"]["error"]
        stale = db.execute(
            "SELECT sha256,audio_sha256_scope FROM files WHERE id='f0'"
        ).fetchone()
        assert stale["sha256"] is None and stale["audio_sha256_scope"] == "stale_full", \
            "preflight mismatch was not persisted for recovery"
        def _fake_analysis(job):
            beats = np.asarray([0.0, 0.5], dtype=np.float32).tobytes()
            return {
                "ok": True,
                "file_id": job["file_id"],
                "pcm_sha": "pcm-repaired",
                "features": {
                    "bpm": 120.0, "bpm_confidence": 1.0,
                    "key_root": 0, "key_mode": 1, "key_confidence": 1.0,
                    "loudness_lufs": -14.0, "energy": 0.2,
                    "beats": beats, "downbeats": beats,
                    "sections": [], "vocal_likelihood": 0.0,
                },
            }

        with patch("earcrate.app.analyze_file_worker", side_effect=_fake_analysis):
            repaired = core.analyze(force=False)
        current = db.execute(
            "SELECT sha256,audio_sha256_scope,audio_generation FROM files WHERE id='f0'"
        ).fetchone()
        assert repaired["analyzed"] == 1 and not repaired["failed"]
        assert current["sha256"] == sha256_file(source) and current["audio_sha256_scope"] == "full"
        assert current["audio_generation"] == 1, "Analyze did not retire loops from the replaced PCM"

        # A second mutation after the first preflight hash but after decode must
        # still be caught by the pre-publication source revalidation.
        new_pcm_sha = db.execute("SELECT audio_sha256 FROM files WHERE id='f0'").fetchone()[0]
        db.execute(
            "UPDATE loops SET source_audio_sha256=?,source_audio_generation=1 WHERE id='l0'",
            (new_pcm_sha,),
        )
        db.commit()
        third = music / "third.wav"
        sf.write(str(third), 0.2 * np.sin(2 * np.pi * 330 * t), sr, subtype="PCM_16")
        third_bytes = third.read_bytes()
        mutated = {"done": False}

        def _mutate_after_decode(path, *args, **kwargs):
            decoded = np.full(sr * 2, 0.05, dtype=np.float32)
            if Path(path).resolve() == source.resolve() and not mutated["done"]:
                mutated["done"] = True
                source.write_bytes(third_bytes)
            return decoded

        dst2 = work / "must-also-not-exist.wav"
        try:
            with patch("earcrate.app.decode_audio", side_effect=_mutate_after_decode), \
                 patch("earcrate.app.stable_presence_restore", side_effect=lambda y, _sr: y), \
                 patch("earcrate.app.integrated_lufs_normalize", side_effect=lambda y, _sr, _target: y), \
                 patch("earcrate.app.drydeck_metrics", return_value={}), \
                 patch("earcrate.app.drydeck_quality_gate", return_value={"passed": True}), \
                 patch("earcrate.app.sf.write", side_effect=AssertionError("sf.write must not run")):
                core.render_mashup("m0", dst2)
        except RuntimeError as exc:
            assert "source changed during render" in str(exc)
        else:
            raise AssertionError("mid-render mutation reached WAV publication")
        assert mutated["done"] and not dst2.exists()
        raced = db.execute(
            "SELECT sha256,audio_sha256_scope FROM files WHERE id='f0'"
        ).fetchone()
        assert raced["sha256"] is None and raced["audio_sha256_scope"] == "stale_full"
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_ec is not None:
            os.environ["EARCRATE_HOME"] = old_ec
        else:
            os.environ.pop("EARCRATE_HOME", None)


def test_rejected_plan_and_one_click_receipts_are_truthful():
    """Receipts preserve rejected plans and the exact final UI/API outcome."""
    from earcrate.app import _durable_compile_attempt, PlanRejectedError, EarcrateCore
    from earcrate.core.util import arrangement_sha
    from pathlib import Path

    class _FakeCompiler:
        def __init__(self): self.events = []
        def _run_bundle_begin(self, *a, **k): return {"run_id": "run1", "path": "/runs/run1"}
        def _run_bundle_set_plan(self, *a, **k): self.events.append(("plan", a, k))
        def _run_bundle_finish(self, *a, **k): self.events.append(("finish", a, k))

        @_durable_compile_attempt
        def compile(self, params):
            plan = {"bpm": 120, "sections": [], "params": dict(params)}
            raise PlanRejectedError("taste gate refused", plan, arrangement_sha(plan))

    fake = _FakeCompiler()
    try:
        fake.compile({"taste_profile": "girl_talk_v1"})
    except PlanRejectedError:
        pass
    else:
        raise AssertionError("rejected plan did not propagate its refusal")
    plan_event = next(e for e in fake.events if e[0] == "plan")
    assert plan_event[2].get("state") == "rejected" and isinstance(plan_event[1][1], dict)
    finish_event = next(e for e in fake.events if e[0] == "finish")
    assert finish_event[1][1] is False and finish_event[1][2]["plan_state"] == "rejected"

    core = EarcrateCore.__new__(EarcrateCore)
    captured = {}
    core._run_bundle_path = lambda *a, **k: Path("/nonexistent/run/plan.json")
    core._run_bundle_finish = lambda run_id, ok, outcome: captured.update(
        {"run_id": run_id, "ok": ok, "outcome": outcome})
    result = core._finish_one_click_result(
        {"run_id": "run2", "run_bundle": {"path": "/runs/run2"}},
        {"ok": True, "render_path": "/renders/final.wav"},
    )
    assert result["render_path"] == captured["outcome"]["render_path"]
    assert captured["ok"] is True and captured["outcome"]["run_id"] == "run2"


def test_post_render_gate_rejects_before_wav_write():
    """Static ordering gate: a known in-memory TasteSpec failure precedes sf.write."""
    import inspect
    from earcrate.app import EarcrateCore
    source = inspect.getsource(EarcrateCore.render_mashup.__wrapped__)
    quality_reject = source.index('"failure_kind": "post_render_quality_gate"')
    wav_write = source.index("sf.write(str(dst)")
    assert quality_reject < wav_write, "failed in-memory quality gate still writes a WAV before rejection"


if __name__ == "__main__":
    from run_gates import main as _run_all_gates
    raise SystemExit(_run_all_gates())


def test_arrangement_has_macro_dynamics(tmp_path):
    """The composer must give the track an ENERGY CURVE (sparse intro, builds,
    drops, breakdowns) rather than parking every 4-bar section at the same
    loudness. Flat, equal-energy sections were the real desktop failure: the
    post-render quality gate rejected the render with 'rms_std_db catastrophically
    low; render is effectively flat'. This locks in per-section loudness variance."""
    import math, statistics as st
    for d in ("music", "work", "agent"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp_path / "music"), "working_root": str(tmp_path / "work"),
                    "agent_root": str(tmp_path / "agent"), "workers": 1, "analysis_seconds": 8})
    pool = _v3_build_render_pool(core, core.conn(), tmp_path, bpm=120.0)
    arr = core.compose_taste_arrangement(list(pool),
            {"taste_profile": "girl_talk_v1", "target_seconds": 16, "bpm": 120.0, "quality_mode": "stable_deck"}, seed=7)
    secs = arr.get("sections") or []
    assert secs, "composer produced no sections"
    energies = [round(float(s.get("energy_level") or 0), 2) for s in secs]
    # The opening section eases in (was a flat 0.7); this is the tell the curve is live.
    assert abs(energies[0] - 0.32) < 0.01, f"intro must ease in at 0.32, got {energies[0]}"

    def sec_db(s):
        amp = sum(10 ** (float(ly.get("gain_db", 0)) / 20.0) for ly in s.get("layers") or []) or 1e-6
        return 20 * math.log10(amp)
    dbs = [sec_db(s) for s in secs]
    # Real loudness variance across sections — the opposite of "effectively flat".
    assert (max(dbs) - min(dbs)) >= 1.5, f"section loudness must vary; spread was only {max(dbs)-min(dbs):.1f} dB"


def test_recognizability_bias_prefers_hooks(tmp_path):
    """params['recognizability_bias'] must actually bias SELECTION toward the
    persona's high-recognizability hooks (readiness.rank_material formula), not sit
    inert in params. Cranked runs reach for the 'oh, THAT song' payoff."""
    import copy
    for d in ("music", "work", "agent"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp_path / "music"), "working_root": str(tmp_path / "work"),
                    "agent_root": str(tmp_path / "agent"), "workers": 1, "analysis_seconds": 8})
    pool = _v3_build_render_pool(core, core.conn(), tmp_path, bpm=120.0)
    proto = [a for a in pool if a.get("ear_role") == "VOX_HOOK"][0]
    for i in range(8):
        a = copy.deepcopy(proto)
        a["id"] = f"hk{i}"; a["atom_id"] = f"hk{i}"; a["source_track_key"] = f"hooksrc{i}"
        a["hook_score"] = round(0.15 + 0.8 * (i / 7), 3); a["score"] = 0.6
        pool.append(a)

    def mean_hook(bias):
        arr = core.compose_taste_arrangement(list(pool), {"taste_profile": "girl_talk_v1", "target_seconds": 48,
              "bpm": 120.0, "quality_mode": "stable_deck", "recognizability_bias": bias}, seed=5)
        hk = []
        for s in arr["sections"]:
            for ly in s["layers"]:
                if ly.get("role") == "vocal" or str(ly.get("ear_role")).startswith("VOX"):
                    m = [a for a in pool if (a.get("atom_id") or a.get("id")) == ly.get("atom_id")]
                    if m:
                        hk.append(float(m[0].get("hook_score") or 0))
        return sum(hk) / len(hk) if hk else 0.0

    neutral, cranked = mean_hook(0), mean_hook(92)
    assert cranked > neutral, f"recognizability crank must raise chosen-hook strength ({cranked:.3f} !> {neutral:.3f})"


def test_bakeoff_resolves_personas_cleanly(tmp_path):
    """The persona bake-off composes the SAME material through several personas and
    each resolves to ok / clean-skip (never a crash), so you can A/B/C how each
    taste reinterprets the library."""
    for d in ("music", "work", "agent"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp_path / "music"), "working_root": str(tmp_path / "work"),
                    "agent_root": str(tmp_path / "agent"), "workers": 1, "analysis_seconds": 8})
    _v3_build_render_pool(core, core.conn(), tmp_path, bpm=120.0)
    bo = core.bakeoff({"plan_only": True, "recognizability_bias": "max", "target_seconds": 16,
                       "personas": ["girl_talk_v1", "troubadour_v1", "notorious_v1"]})
    assert len(bo["bakeoff"]) == 3
    assert bo["recognizability_bias"] == 92, "max crank must map to a concrete bias value"
    for r in bo["bakeoff"]:
        assert "taste_profile" in r and "contract" in r and "ok" in r, f"malformed bakeoff entry: {r}"
        # every persona must be either a real compose (ok) or a clean skip — never a crash/traceback
        assert r["ok"] is True or r.get("skipped") is True or r.get("error"), f"persona neither ran nor skipped cleanly: {r}"
