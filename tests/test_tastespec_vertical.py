import json, os, sqlite3, tempfile
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.app import EarcrateCore
from earcrate.tastespec import load_tastespec, profile_summary


def configured_core(tmp_path: Path) -> EarcrateCore:
    master = tmp_path / "music"; work = tmp_path / "work"; agent = tmp_path / "agent"
    master.mkdir(); work.mkdir(); agent.mkdir()
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        c = EarcrateCore()
        c.configure({"master_root": str(master), "working_root": str(work), "agent_root": str(agent), "workers": 2})
    return c


def test_profile_hash_and_plan_receipt(tmp_path):
    core = configured_core(tmp_path)
    prof = load_tastespec("girl_talk_v1")
    import re
    assert re.match(r"^\d+\.\d+\.\d+$", prof["version"]), "profile version must be semver"
    assert len(prof["hash"]) == 64
    plan = {"bpm": 124, "target_key": 0, "params": {"taste_profile": "girl_talk_v1"}, "sections": []}
    saved = core.save_plan("synthetic", plan, "girl_talk_v1")
    loaded = core.load_plan(saved["plan_hash"])
    assert loaded["plan"]["tastespec"]["hash"] == prof["hash"]
    assert Path(saved["path"]).exists()


def test_atom_and_pair_judgments_are_profile_scoped(tmp_path):
    core = configured_core(tmp_path)
    db = core.conn()
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES(?,?,?,?,?,?)", ("f1", str((tmp_path/'music'/'a.wav').resolve()), "master", 1, 1, "now"))
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at) VALUES(?,?,?,?,?,?)", ("f2", str((tmp_path/'music'/'b.wav').resolve()), "master", 1, 1, "now"))
    core._set_pcm("f1", "pcm_fixture_f1")
    core._set_pcm("f2", "pcm_fixture_f2")
    db.execute("INSERT INTO tracks(id,file_id,artist,title) VALUES(?,?,?,?)", ("t1", "f1", "A", "One"))
    db.execute("INSERT INTO tracks(id,file_id,artist,title) VALUES(?,?,?,?)", ("t2", "f2", "B", "Two"))
    db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)", ("l1", "f1", 0, 4, 2, "vocal", 0.9, "now"))
    db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)", ("l2", "f2", 0, 4, 2, "harmony", 0.8, "now"))
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("a1", "l1", "f1", "girl_talk_v1", "VOX_HOOK", "vocal", 0, 4, 2, 0.9, "{}", "now"))
    db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("a2", "l2", "f2", "girl_talk_v1", "BED_CHORD", "harmony", 0, 4, 2, 0.8, "{}", "now"))
    db.execute("INSERT INTO compatibility_edges(id,taste_profile,left_atom_id,right_atom_id,relation,score,reasons_json,created_at) VALUES(?,?,?,?,?,?,?,?)", ("e1", "girl_talk_v1", "a1", "a2", "vocal_over_bed", 0.77, '{"harmonic_score": 0.9}', "now"))
    db.commit()
    assert core.set_atom_judgment("a1", "girl_talk_v1", "approved", "VOX_VERSE", True, True, "keeper")["ok"]
    atom = db.execute("SELECT * FROM atom_judgments WHERE atom_id='a1'").fetchone()
    assert atom["status"] == "approved" and atom["relabel_role"] == "VOX_VERSE" and atom["favorite"] == 1 and atom["locked"] == 1
    pairs = core.compatible_pairs_for_atom("a1", "girl_talk_v1")
    assert pairs["items"] and pairs["items"][0]["reasons"]["harmonic_score"] == 0.9
    assert core.set_pair_judgment("e1", "girl_talk_v1", "rejected", "too masked")["ok"]
    pair = db.execute("SELECT * FROM pair_judgments WHERE edge_id='e1'").fetchone()
    assert pair["status"] == "rejected" and pair["reason"] == "too masked"
