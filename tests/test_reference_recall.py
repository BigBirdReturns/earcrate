"""Gate: the answer-key recall benchmark (study.reference.recall_report +
EarcrateCore.reference_recall). Measures how many of a master's DOCUMENTED
pairings our engine independently rediscovers from our library, and surfaces the
gap (what it SHOULD have found but didn't).
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.study.reference import recall_report, source_key, reference_source_keys


def _fixture():
    # two proven overlapping pairs across two tracks
    return {
        "album": "All Day", "artist": "Girl Talk", "sources": [],
        "tracks": [
            {"index": 1, "title": "t1", "duration_s": 60, "samples": [
                {"source_artist": "Cream", "source_title": "Sunshine", "start_s": 0, "end_s": 10, "role": None},
                {"source_artist": "The Doors", "source_title": "Break On Through", "start_s": 5, "end_s": 15, "role": None},
            ]},
            {"index": 2, "title": "t2", "duration_s": 60, "samples": [
                {"source_artist": "Nas", "source_title": "N.Y. State of Mind", "start_s": 0, "end_s": 12, "role": None},
                {"source_artist": "Jay-Z", "source_title": "PSA", "start_s": 6, "end_s": 18, "role": None},
            ]},
        ],
    }


def _k(a, t):
    return source_key(a, t)


def test_source_key_normalizes_noise():
    assert _k("The Doors", "Break On Through") == _k("the doors", "break on through!")
    assert _k("Jay-Z feat. Nas", "PSA") == _k("Jay-Z", "PSA")  # feat. dropped


def test_recall_counts_recoverable_recovered_and_missed():
    ds = _fixture()
    cream = _k("Cream", "Sunshine"); doors = _k("The Doors", "Break On Through")
    nas = _k("Nas", "N.Y. State of Mind"); jay = _k("Jay-Z", "PSA")
    # We own all four sources; the engine recovered ONLY the Cream/Doors pairing.
    present = {cream, doors, nas, jay}
    recovered = {frozenset((cream, doors))}
    rep = recall_report(ds, present, recovered)
    assert rep["proven_pairs_total"] == 2
    assert rep["recoverable"] == 2 and rep["recovered"] == 1
    assert rep["recall"] == 0.5
    # the Nas/Jay pairing is the documented discovery we MISSED
    missed = {frozenset((_k(m["a"]["artist"], m["a"]["title"]), _k(m["b"]["artist"], m["b"]["title"]))) for m in rep["missed"]}
    assert frozenset((nas, jay)) in missed and frozenset((cream, doors)) not in missed


def test_pairing_is_not_recoverable_without_owning_both_sources():
    ds = _fixture()
    cream = _k("Cream", "Sunshine")  # we own only one side of the Cream/Doors pair
    rep = recall_report(ds, {cream}, set())
    assert rep["sources_in_library"] == 1
    assert rep["recoverable"] == 0        # can't rediscover a pairing whose material we lack
    assert rep["recall"] is None


def _core(tmp):
    for d in ("music", "work", "agent"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp)}):
        from earcrate.app import EarcrateCore
        c = EarcrateCore()
        c.configure({"master_root": str(tmp / "music"), "working_root": str(tmp / "work"),
                     "agent_root": str(tmp / "agent"), "workers": 1})
    return c


def test_reference_recall_end_to_end_on_a_seeded_library(tmp_path):
    """Seed a library owning both Cream and The Doors, plus an ENGINE compatibility
    edge between their atoms -> the Cream/Doors proven pairing is recovered."""
    import json
    core = _core(tmp_path)
    db = core.conn()
    for fid, art, ttl in (("f1", "Cream", "Sunshine"), ("f2", "The Doors", "Break On Through")):
        db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at,present) VALUES(?,?,?,?,?,?,1)",
                   (fid, f"/m/{fid}.wav", "master", 1, 1, "now"))
        db.execute("INSERT INTO tracks(id,file_id,artist,title) VALUES(?,?,?,?)", ("t" + fid, fid, art, ttl))
        db.execute("INSERT INTO loops(id,file_id,start_s,end_s,bars,role,score,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("l" + fid, fid, 0, 4, 2, "vocal", 0.9, "now"))
        db.execute("INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,score,status,metrics_json,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("a" + fid, "l" + fid, fid, "girl_talk_v1", "VOX_HOOK", "vocal", 0, 4, 2, 0.9, "approved", "{}", "now"))
    db.execute("INSERT INTO compatibility_edges(id,taste_profile,left_atom_id,right_atom_id,relation,score,reasons_json,created_at) "
               "VALUES(?,?,?,?,?,?,?,?)", ("e1", "girl_talk_v1", "af1", "af2", "vocal_over_bed", 0.8, "{}", "now"))
    db.commit()
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps(_fixture()), encoding="utf-8")
    rep = core.reference_recall(str(ref), "girl_talk_v1")
    assert rep["sources_in_library"] == 2       # own Cream + Doors (not Nas/Jay)
    assert rep["recoverable"] == 1 and rep["recovered"] == 1 and rep["recall"] == 1.0
    assert rep["engine_edges"] == 1
