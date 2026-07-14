"""Gates for the external-target remix path: anchor inversion, a pinned continuous
vocal over a library bed, and the honest bed-feasibility verdict."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.app import EarcrateCore
from earcrate.remix.external import (remix_anchor, external_foreground_atom,
                                     external_vocal_window, external_remix_feasibility)


def configured_core(tmp_path: Path) -> EarcrateCore:
    master = tmp_path / "music"; work = tmp_path / "work"; agent = tmp_path / "agent"
    master.mkdir(); work.mkdir(); agent.mkdir()
    with patch.dict(os.environ, {"EARCRATE_HOME": str(tmp_path)}):
        c = EarcrateCore()
        c.configure({"master_root": str(master), "working_root": str(work), "agent_root": str(agent), "workers": 2})
    return c


# ---- pure: anchor is READ off the target, folded into a sane render range ----

def test_remix_anchor_reads_and_guards_tempo():
    a = remix_anchor({"bpm": 148.0, "key_root": 7, "key_mode": 0, "key_confidence": 0.8})
    assert a["bpm"] == 148.0 and a["key_root"] == 7 and a["key_mode"] == 0
    # A garbage/near-zero tempo (arrhythmic acapella) falls back to a usable grid.
    assert remix_anchor({"bpm": 0.0, "key_root": 13})["bpm"] == 120.0
    assert remix_anchor({"bpm": 0.0, "key_root": 13})["key_root"] == 1  # 13 % 12


# ---- pure: the dropped vocal atom is shaped so the composer rails it, and its
#      OWN transform is identity (bpm/key ARE the anchor) so it never degrades ----

def test_external_foreground_atom_is_identity_and_marked():
    anchor = remix_anchor({"bpm": 100.0, "key_root": 5, "key_mode": 1})
    atom = external_foreground_atom("My Take", anchor, 90.0, "abc123def456", "/x/take.wav")
    assert atom["is_external"] and atom["ear_role"] == "VOX_HOOK" and atom["role"] == "vocal"
    assert atom["bpm"] == 100.0 and atom["key_root"] == 5  # == anchor -> identity transform
    assert atom["external_ref"]["path"] == "/x/take.wav"
    assert atom["external_ref"]["duration_s"] == 90.0
    assert atom["low_share"] < 0.34  # keeps the bass gate open under the acapella
    # Deterministic: same inputs -> same atom id.
    again = external_foreground_atom("My Take", anchor, 90.0, "abc123def456", "/x/take.wav")
    assert again["id"] == atom["id"]


# ---- pure: the continuous take plays front-to-back; windows advance, clamp, and
#      go None once the vocal is spent (bed continues instrumental) ----

def test_external_vocal_window_advances_and_ends():
    bpm = 120.0  # 1 bar = 2s, 4 bars = 8s
    w0 = external_vocal_window(0, 4, bpm, 30.0)
    w1 = external_vocal_window(4, 4, bpm, 30.0)
    assert w0 == (0.0, 8.0) and w1 == (8.0, 8.0)  # linear progression
    # Section straddling the end gets only the remaining audio.
    tail = external_vocal_window(12, 4, bpm, 30.0)  # starts at 24s of a 30s vocal
    assert tail is not None and abs(tail[0] - 24.0) < 1e-6 and abs(tail[1] - 6.0) < 1e-6
    # Past the end -> None: the bed plays on with no vocal.
    assert external_vocal_window(16, 4, bpm, 30.0) is None


# ---- pure: honest buildability at the anchor ----

def test_external_remix_feasibility_verdict():
    ok = external_remix_feasibility({"have": {"floor": 6, "bass": 2, "spark": 3, "sources": 4},
                                     "render_bpm": 100.0, "target_key": 5}, needed_sources=3)
    assert ok["buildable"] and any("bed OK" in r for r in ok["reasons"])
    no_floor = external_remix_feasibility({"have": {"floor": 0, "bass": 1, "spark": 0, "sources": 3}}, 2)
    assert not no_floor["buildable"] and any("no structural bed" in r for r in no_floor["reasons"])
    thin = external_remix_feasibility({"have": {"floor": 3, "bass": 0, "spark": 0, "sources": 1}}, 4)
    assert not thin["buildable"] and any("drone" in r for r in thin["reasons"])


# ---- integration: the composer pins the anchor to the target, rides ONE external
#      vocal across the whole track, and pulls the bed from the library pool ----

def _bed(atom_id, ear_role, role, key_root, bpm, artist):
    return {"id": atom_id, "atom_id": atom_id, "ear_role": ear_role, "role": role,
            "render_role": role, "key_root": key_root, "key_mode": 1, "bpm": bpm,
            "score": 0.8, "hook_score": 0.4, "high_share": 0.3, "low_share": 0.4,
            "artist": artist, "title": ear_role.lower()}


def test_compose_pins_anchor_and_rides_external_vocal(tmp_path):
    core = configured_core(tmp_path)
    anchor = remix_anchor({"bpm": 96.0, "key_root": 2, "key_mode": 1})
    ext = external_foreground_atom("Dropped", anchor, 40.0, "sha_ext_0001", str(tmp_path / "v.wav"))
    # A library bed at the SAME anchor (identity-playable): two distinct floor sources
    # + a bass, so feasibility clears and the composer has real material to rotate.
    pool = [
        _bed("b1", "DRUM_BREAK", "drum_anchor", 2, 96.0, "LibA"),
        _bed("b2", "BED_CHORD", "harmony", 2, 96.0, "LibB"),
        _bed("b3", "BASS_RIFF", "bass", 2, 96.0, "LibC"),
    ]
    params = {"taste_profile": "girl_talk_v1", "target_seconds": 40.0,
              "pin_bpm": anchor["bpm"], "pin_key": anchor["key_root"],
              "external_foreground": ext}
    arr = core.compose_taste_arrangement(pool, params, seed=7)
    # Anchor inversion: the render is pinned to the target, not searched.
    assert abs(arr["bpm"] - 96.0) < 1e-6 and arr["target_key"] == 2
    secs = arr["sections"]
    assert secs, "must produce sections"
    # Every section (while the 40s vocal lasts) carries the external vocal, and only
    # ONE external source is ever used (it never rotates to a library vox).
    vocal_secs = [s for s in secs if any(l.get("external_ref") for l in s["layers"])]
    assert len(vocal_secs) >= 1
    starts = []
    for s in vocal_secs:
        vlayers = [l for l in s["layers"] if l.get("external_ref")]
        assert len(vlayers) == 1
        er = vlayers[0]["external_ref"]
        assert er["path"] == str(tmp_path / "v.wav") and "start_s" in er and "len_s" in er
        starts.append(er["start_s"])
    assert starts == sorted(starts), "vocal windows advance monotonically front-to-back"
    # The bed under the vocal is LIBRARY material, never the external source.
    bed_layers = [l for s in secs for l in s["layers"] if not l.get("external_ref")]
    assert bed_layers, "library bed must play under the vocal"
    assert all(l.get("source_track_key") != "external" for l in bed_layers)
