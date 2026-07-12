#!/usr/bin/env python3
"""Executable gates (rebuild plan §5). Run: python tests/test_gates.py"""
import sys, random
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from earcrate.deck.transform import plan_varispeed_transform
from earcrate.deck.lattice import score_bpm_lattice
from earcrate.ear.readiness import crate_readiness_audit, girl_talk_targets, endless_sustain
from earcrate.app import EarcrateCore

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
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sr = 44100
    for i in range(3):
        t = np.arange(sr * 8) / sr
        sf.write(str(tmp / "music" / f"s{i}.wav"), (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                    "agent_root": str(tmp / "agent"), "workers": 2, "analysis_seconds": 10})
    core.scan(); core.analyze(force=True); core.extract_loops(auto_approve=True, force=True)
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
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    for d in ("music", "work", "agent"): (tmp / d).mkdir()
    sr = 44100
    for i in range(3):
        t = np.arange(sr * 8) / sr
        sf.write(str(tmp / "music" / f"s{i}.wav"), (0.3 * np.sin(2 * np.pi * (130 * (i + 2)) * t)).astype(np.float32), sr)
    core = EarcrateCore()
    core.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                    "agent_root": str(tmp / "agent"), "workers": 2, "analysis_seconds": 10})
    core.scan(); core.analyze(force=True); core.extract_loops(auto_approve=True, force=True)
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


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        try:
            fn(); print(f"PASS {name}")
        except AssertionError as e:
            fails += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)


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


def test_identify_parses_acoustid_and_guards_key():
    """v0.8.14 gate: AcoustID response parsing picks the best-score recording and
    extracts artist/title/album/mbid; empty/error responses are handled; the
    fingerprint path works when fpcalc is present; identify refuses without a key."""
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
