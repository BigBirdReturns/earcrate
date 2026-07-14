"""Gate: deterministic reference-study capability (earcrate/study/reference.py).

Everything here is driven by a SYNTHETIC schema-conformant fixture defined
inline — it does NOT depend on any real dataset file. The fixture's overlaps,
run lengths, density and layer count are hand-computed in the assertions below so
a regression in the measurement math trips this gate rather than silently
shifting a persona target.

Fixture (shared schema):
  Track 6, duration 60s:
    A "Cream / Sunshine"   [ 0,10]  run 10
    B "Doors / Break On"   [ 5,15]  run 10   -> A,B OVERLAP 5s (the edge)
    C "Zep / Kashmir"      [20,25]  run  5   -> touches nobody
  Track 7, duration 60s:
    D "Nas / NY State"     [ 0,30]  run 30
    E "Jay / PSA"          [30,60]  run 30   -> D,E only touch at 30 (no overlap)

  samples          = 5
  total duration   = 120s = 2.0 min  -> density 2.5 samples/min
  run lengths      = [10,10,5,30,30] -> mean 17.0, median 10.0, max 30.0
  layer integral   : t6 -> [0,5)=1,[5,10)=2,[10,15)=1,[20,25)=1  = 25 over active 20s
                      t7 -> [0,30)=1,[30,60)=1                    = 60 over active 60s
  mean_layers      = (25+60) / (20+60) = 85/80 = 1.0625
  edges            = exactly ONE (A,B in track 6, overlap 5.0)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earcrate.study.reference import (
    load_reference,
    reference_fingerprint,
    reference_edges,
    calibrate_profile,
)
from earcrate.tastespec import load_tastespec


def _fixture() -> dict:
    return {
        "album": "All Day",
        "artist": "Girl Talk",
        "sources": ["https://example.test/cream", "https://example.test/nas"],
        "tracks": [
            {
                "index": 6,
                "title": "On and On",
                "duration_s": 60,
                "samples": [
                    {"source_artist": "Cream", "source_title": "Sunshine", "start_s": 0, "end_s": 10, "role": None},
                    {"source_artist": "Doors", "source_title": "Break On", "start_s": 5, "end_s": 15, "role": "vocal"},
                    {"source_artist": "Zep", "source_title": "Kashmir", "start_s": 20, "end_s": 25, "role": None},
                ],
            },
            {
                "index": 7,
                "title": "Late",
                "duration_s": 60,
                "samples": [
                    {"source_artist": "Nas", "source_title": "NY State", "start_s": 0, "end_s": 30, "role": None},
                    {"source_artist": "Jay", "source_title": "PSA", "start_s": 30, "end_s": 60, "role": None},
                ],
            },
        ],
    }


def test_reference_fingerprint_measures_expected_numbers():
    fp = reference_fingerprint(_fixture())
    assert fp["samples_per_minute"] == 2.5, fp
    assert fp["source_seconds"] == 17.0, fp
    assert fp["median_source_run_s"] == 10.0, fp
    assert fp["max_source_run_s"] == 30.0, fp
    assert fp["mean_layers"] == 1.0625, fp
    assert fp["per_track_sample_counts"] == [
        {"track": 6, "title": "On and On", "samples": 3},
        {"track": 7, "title": "Late", "samples": 2},
    ]
    assert fp["totals"] == {"tracks": 2, "samples": 5, "timed_samples": 5, "duration_s": 120.0}
    assert fp["availability"] == {
        "samples_per_minute": True,
        "source_seconds": True,
        "max_source_run_s": True,
        "mean_layers": True,
    }


def test_reference_edges_finds_only_the_overlapping_pair():
    edges = reference_edges(_fixture())
    assert len(edges) == 1, edges
    edge = edges[0]
    assert edge["a"] == {"artist": "Cream", "title": "Sunshine"}
    assert edge["b"] == {"artist": "Doors", "title": "Break On"}
    assert edge["track"] == 6
    assert edge["overlap_s"] == 5.0
    # C (Zep) touches nobody; D/E only meet at t=30 (zero-length) -> never edges.
    artists = {edge["a"]["artist"], edge["b"]["artist"]}
    assert "Zep" not in artists and "Nas" not in artists and "Jay" not in artists


def test_calibrate_profile_replaces_numbers_without_mutating_base():
    base = load_tastespec("girl_talk_v1")
    base_snapshot = json.loads(json.dumps(base))
    fp = reference_fingerprint(_fixture())
    result = calibrate_profile(fp, base)

    # Base JSON is never mutated in place.
    assert base == base_snapshot, "calibrate_profile must not mutate the shipped profile"

    prof = result["profile"]
    # source_turnover + density_model numbers are REPLACED by the measured ones.
    assert prof["source_turnover"]["source_seconds"] == 17.0
    assert prof["source_turnover"]["max_source_run_s"] == 30.0
    assert prof["density_model"]["sources_per_minute"] == 2.5
    assert prof["density_model"]["seconds_per_event"] == 24.0  # 60 / 2.5
    # Fields the fingerprint did not touch stay as shipped.
    assert prof["density_model"]["min_layers"] == base["density_model"]["min_layers"]
    assert prof["source_turnover"]["min_feasible_sources"] == base["source_turnover"]["min_feasible_sources"]
    # A recomputed hash is required later, so the copy carries no stale hash.
    assert "hash" not in prof

    diff = result["diff"]
    changed = {(d["section"], d["field"]): (d["from"], d["to"]) for d in diff}
    assert changed[("source_turnover", "source_seconds")] == (11.5, 17.0)
    assert changed[("source_turnover", "max_source_run_s")] == (16.0, 30.0)
    assert changed[("density_model", "seconds_per_event")] == (11.0, 24.0)
    assert changed[("density_model", "sources_per_minute")] == (5.5, 2.5)
    assert len(diff) == 4


def test_missing_timing_marks_unavailable_but_keeps_density():
    """A source LIST with no timestamps still yields density (duration known) and
    per-track counts, but run length / layers / edges are honestly unavailable."""
    ds = {
        "album": "All Day", "artist": "Girl Talk", "sources": [],
        "tracks": [{
            "index": 1, "title": "No Timing", "duration_s": 120,
            "samples": [
                {"source_artist": "X", "source_title": "One", "start_s": None, "end_s": None, "role": None},
                {"source_artist": "Y", "source_title": "Two", "start_s": None, "end_s": None, "role": None},
                {"source_artist": "Z", "source_title": "Three", "start_s": None, "end_s": None, "role": None},
            ],
        }],
    }
    fp = reference_fingerprint(ds)
    assert fp["samples_per_minute"] == 1.5  # 3 samples / 2 min
    assert fp["source_seconds"] is None
    assert fp["max_source_run_s"] is None
    assert fp["mean_layers"] is None
    assert fp["availability"] == {
        "samples_per_minute": True, "source_seconds": False,
        "max_source_run_s": False, "mean_layers": False,
    }
    assert reference_edges(ds) == []

    # calibrate still replaces the density numbers it CAN measure and leaves the
    # timing-derived source_turnover fields as shipped (no diff entry for them).
    base = load_tastespec("girl_talk_v1")
    diff = calibrate_profile(fp, base)["diff"]
    fields = {(d["section"], d["field"]) for d in diff}
    assert ("density_model", "sources_per_minute") in fields
    assert ("density_model", "seconds_per_event") in fields
    assert ("source_turnover", "source_seconds") not in fields
    assert ("source_turnover", "max_source_run_s") not in fields


def test_no_density_when_durations_absent():
    ds = {
        "album": "All Day", "artist": "Girl Talk", "sources": [],
        "tracks": [{
            "index": 1, "title": "Timed But No Duration", "duration_s": None,
            "samples": [
                {"source_artist": "X", "source_title": "One", "start_s": 0, "end_s": 8, "role": None},
                {"source_artist": "Y", "source_title": "Two", "start_s": 4, "end_s": 12, "role": None},
            ],
        }],
    }
    fp = reference_fingerprint(ds)
    assert fp["samples_per_minute"] is None
    assert fp["availability"]["samples_per_minute"] is False
    # Timing IS present -> run length + one overlap edge still measured.
    assert fp["source_seconds"] == 8.0  # (8 + 8) / 2
    assert len(reference_edges(ds)) == 1


def test_deterministic_same_input_identical_output():
    ds = _fixture()
    fp1 = reference_fingerprint(ds)
    fp2 = reference_fingerprint(json.loads(json.dumps(ds)))
    assert json.dumps(fp1, sort_keys=True) == json.dumps(fp2, sort_keys=True)

    e1 = reference_edges(ds)
    e2 = reference_edges(json.loads(json.dumps(ds)))
    assert json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True)

    base = load_tastespec("girl_talk_v1")
    c1 = calibrate_profile(fp1, base)
    c2 = calibrate_profile(fp2, load_tastespec("girl_talk_v1"))
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_load_reference_from_path_and_validation(tmp_path):
    path = tmp_path / "ref.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    loaded = load_reference(str(path))
    assert loaded["album"] == "All Day"
    assert reference_fingerprint(loaded)["samples_per_minute"] == 2.5

    # A sample with only one of start_s/end_s is a schema violation.
    bad = _fixture()
    bad["tracks"][0]["samples"][0]["end_s"] = None
    try:
        load_reference(bad)
        raise AssertionError("expected ValueError for half-timed sample")
    except ValueError as exc:
        assert "both be set or both be null" in str(exc)
