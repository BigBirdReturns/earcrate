"""Gate: MusicBrainz enrichment parsing (network injected, deterministic)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.study.musicbrainz import extract_relationships, enrich_track, has_any_relationship


def test_extract_relationships_parses_samples_remix_cover():
    rec = {"relations": [
        {"type": "samples material", "direction": "forward", "work": {"title": "Amen, Brother"}},
        {"type": "samples material", "direction": "backward", "recording": {"title": "Straight Outta Compton"}},
        {"type": "remix", "direction": "backward", "recording": {"title": "Song (Skrillex Remix)"}},
        {"type": "cover", "direction": "forward", "work": {"title": "Hallelujah"}},
        {"type": "performance", "direction": "forward", "work": {"title": "ignore-me"}},  # not relevant
    ]}
    r = extract_relationships(rec)
    assert r["sample_of"] == ["Amen, Brother"]
    assert r["sampled_by"] == ["Straight Outta Compton"]
    assert r["remixed_by"] == ["Song (Skrillex Remix)"]
    assert r["covers"] == ["Hallelujah"]
    assert r["remix_of"] == []


def test_enrich_track_with_injected_fetch():
    calls = []
    def fake(url):
        calls.append(url)
        if "recording?query=" in url:
            return {"recordings": [{"id": "mbid-1", "title": "Amen, Brother"}]}
        return {"relations": [{"type": "samples material", "direction": "backward",
                               "recording": {"title": "Famous Breakbeat Track"}}]}
    res = enrich_track("The Winstons", "Amen, Brother", fetch=fake)
    assert res["matched"] and res["mbid"] == "mbid-1"
    assert res["sampled_by"] == ["Famous Breakbeat Track"]
    assert has_any_relationship(res) and len(calls) == 2


def test_enrich_track_handles_no_match():
    res = enrich_track("Nobody", "Nothing", fetch=lambda u: {"recordings": []})
    assert res["matched"] is False and not has_any_relationship(res)
