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


def test_export_library_manifest_is_public_safe(tmp_path):
    """The manifest carries artist/title/album/year/genre for cross-referencing
    against sample sites -- but NO file paths or machine identifiers (public repo)."""
    import json
    core = _core(tmp_path)
    db = core.conn()
    db.execute("INSERT INTO files(id,path,root,size_bytes,mtime_ns,scanned_at,present) VALUES(?,?,?,?,?,?,1)",
               ("f1", "/Users/secret/Music/x.wav", "master", 1, 1, "now"))
    db.execute("INSERT INTO tracks(id,file_id,artist,album,title,year) VALUES(?,?,?,?,?,?)",
               ("t1", "f1", "Cream", "Disraeli Gears", "Sunshine of Your Love", 1967))
    db.commit()
    res = core.export_library_manifest(str(tmp_path / "man.json"))
    assert res["ok"] and res["count"] == 1
    text = (tmp_path / "man.json").read_text()
    man = json.loads(text)
    row = man["tracks"][0]
    assert row["artist"] == "Cream" and row["title"] == "Sunshine of Your Love" and row["year"] == 1967
    assert "/Users/secret" not in text and "path" not in row      # no path/machine leak


def test_untimed_dataset_uses_cooccurrence_pairings():
    """Producer sample maps (Donuts-style) have no timestamps, so 'proven pairing'
    = sources the producer COMBINED in the same track. Recall must switch to that
    notion automatically."""
    from earcrate.study.reference import reference_pairings, recall_report, source_key as sk
    ds = {"album": "Donuts", "artist": "J Dilla", "sources": [], "tracks": [
        {"index": 1, "title": "t1", "duration_s": None, "samples": [
            {"source_artist": "A", "source_title": "x", "start_s": None, "end_s": None, "role": None},
            {"source_artist": "B", "source_title": "y", "start_s": None, "end_s": None, "role": None},
            {"source_artist": "C", "source_title": "z", "start_s": None, "end_s": None, "role": None}]}]}
    edges, mode = reference_pairings(ds)
    assert mode == "same_track_cooccurrence"
    assert len(edges) == 3                       # A-B, A-C, B-C
    present = {sk("A", "x"), sk("B", "y"), sk("C", "z")}
    recovered = {frozenset((sk("A", "x"), sk("B", "y")))}
    rep = recall_report(ds, present, recovered)
    assert rep["pairing_mode"] == "same_track_cooccurrence"
    assert rep["recoverable"] == 3 and rep["recovered"] == 1


def test_real_donuts_answer_key_loads_and_pairs():
    """The committed J Dilla Donuts answer key is well-formed and yields co-use
    pairings the engine can be graded against."""
    from earcrate.study.reference import load_reference, reference_pairings, reference_source_keys
    ds = load_reference("earcrate/reference/donuts_samples.json")
    assert len(ds["tracks"]) == 31
    assert len(reference_source_keys(ds)) > 50
    edges, mode = reference_pairings(ds)
    assert mode == "same_track_cooccurrence" and len(edges) > 40


def test_material_coverage_counts_owned_source_artists():
    from earcrate.study.reference import answer_key_material_coverage, artist_key
    ds = {"album": "X", "artist": "P", "sources": [], "tracks": [
        {"index": 1, "title": "t", "duration_s": None, "samples": [
            {"source_artist": "The Beatles", "source_title": "a", "start_s": None, "end_s": None, "role": None},
            {"source_artist": "Kool & the Gang", "source_title": "b", "start_s": None, "end_s": None, "role": None}]}]}
    rep = answer_key_material_coverage(ds, {artist_key("The Beatles")})
    assert rep["source_artists_total"] == 2 and rep["source_artists_owned"] == 1
    assert rep["artist_coverage"] == 0.5
    assert "The Beatles" in rep["owned"] and "Kool & the Gang" in rep["missing"]
    assert artist_key("The Beatles") == artist_key("beatles")   # 'the' + case normalized
