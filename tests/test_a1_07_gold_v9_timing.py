from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from earcrate import reference_zero as rz
from earcrate.a1_07_gold_v8 import common as c

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "earcrate_a1_07_gold_v9.py"
CONTRACT = ROOT / "configs" / "album_one" / "a1-07" / "gold-v9-timing-laws.v1.json"

_spec = importlib.util.spec_from_file_location("earcrate_a1_07_gold_v9_script", SCRIPT)
assert _spec and _spec.loader
v9 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v9)


def _source(source_id: str, role: str) -> dict:
    return {
        "source_id": source_id,
        "role": role,
        "container_sha256": (source_id[0] if source_id[0] in "abcdef" else "a") * 64,
        "canonical_pcm_sha256": None,
        "bytes": 123,
    }


def _clip(clip_id: str, source_id: str, source_start: int, target: int, scale: float) -> dict:
    return {
        "clip_id": clip_id,
        "source_id": source_id,
        "source_start_sample": source_start,
        "source_end_sample": source_start + 1000,
        "target_start_sample": target,
        "tempo_scale": scale,
        "pitch_semitones": 0.0,
        "gain_db": 0.0,
        "pan": 0.0,
        "fade_in_samples": 0,
        "fade_out_samples": 0,
        "musical_function": "test",
        "occurrence_id": clip_id,
        "locked": True,
    }


def _parent_score() -> dict:
    slots = [0, 900, 1800, 2750, 3650, 4550, 5500, 6400]
    scales = [1.111111, 1.111111, 1.052632, 1.111111, 1.111111, 1.052632, 1.111111, 1.0]
    band_tracks = []
    for track_id, source_id in (("band-drums", "band_drums"), ("band-bass", "band_bass")):
        band_tracks.append(
            {
                "track_id": track_id,
                "role": "donor_band",
                "ownership": "donor",
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [
                    _clip(f"{track_id}-{index}", source_id, index * 1000, target, scales[index])
                    for index, target in enumerate(slots)
                ],
            }
        )
    score = {
        "schema_version": 1,
        "kind": "earcrate_performance_score",
        "created_at": "2026-08-13T00:00:00Z",
        "score_id": "synthetic-gold-v6",
        "title": "synthetic",
        "timeline": {
            "sample_rate": 8000,
            "channels": 2,
            "duration_samples": 8000,
            "shared_events": [],
        },
        "sources": [
            _source("four_seasons_vocals", "lead_vocal"),
            _source("band_drums", "donor_drums"),
            _source("band_bass", "donor_bass"),
        ],
        "tracks": [
            {
                "track_id": "frankie-lead",
                "role": "lead_vocal",
                "ownership": "frankie",
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [
                    {
                        **_clip("frankie", "four_seasons_vocals", 0, 0, 1.0),
                        "source_end_sample": 7000,
                    }
                ],
            },
            *band_tracks,
        ],
        "master": {
            "gain_db": 0.0,
            "peak_limit_dbfs": -2.0,
            "codec": "pcm_s24le",
        },
        "invariants": {
            "renderer_may_invent_decisions": False,
            "source_mutation_forbidden": True,
            "all_selected_clips_must_render": True,
            "human_command_history_append_only": True,
        },
        "authority": {
            "status": "manual_gold_candidate",
            "allow_unused_sources": False,
        },
        "command_history": [],
    }
    return rz.seal(score)


def _band_scales(score: dict) -> list[list[float]]:
    sources = {str(row["source_id"]): dict(row) for row in score["sources"]}
    result = []
    for track in score["tracks"]:
        if not v9.is_frankie_track(track, sources):
            result.append([float(clip["tempo_scale"]) for clip in track["clips"]])
    return result


def test_contract_discloses_differences_and_seals() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c.validate_seal(contract, "contract_sha256") == (
        "d2b5926f3bb3f460d2f98f0b46e2f0929c075e46456f14aa200a4cc743780711"
    )
    assert contract["owner_signal"]["v8_frontier_disposition"] == (
        "non_discriminating_no_ranking_required"
    )
    assert contract["review_policy"]["roles_withheld"] is False
    assert contract["review_policy"]["cut_notes_required"] is True


def test_timing_laws_change_the_band_not_frankie() -> None:
    parent = _parent_score()
    sources = {str(row["source_id"]): dict(row) for row in parent["sources"]}
    parent_frankie = [
        dict(track) for track in parent["tracks"] if v9.is_frankie_track(track, sources)
    ]

    single, single_facts = v9.build_child_score(parent, law_id="gold-v9-single-speed")
    native, native_facts = v9.build_child_score(parent, law_id="gold-v9-native-pocket")
    phrase, phrase_facts = v9.build_child_score(parent, law_id="gold-v9-phrase-reset")

    for child in (single, native, phrase):
        child_sources = {str(row["source_id"]): dict(row) for row in child["sources"]}
        child_frankie = [
            dict(track)
            for track in child["tracks"]
            if v9.is_frankie_track(track, child_sources)
        ]
        assert c.canonical_json_bytes(child_frankie) == c.canonical_json_bytes(parent_frankie)

    assert all(len(set(scales)) == 1 for scales in _band_scales(single))
    assert single_facts["law"] == "single-speed"
    assert all(set(scales) == {1.0} for scales in _band_scales(native))
    assert native_facts["law"] == "native-pocket"

    phrase_scales = _band_scales(phrase)
    assert all(scales[:4].count(scales[0]) == 4 for scales in phrase_scales)
    assert all(scales[4:].count(scales[4]) == 4 for scales in phrase_scales)
    assert phrase_facts["phrase_slots"] == 4
    assert len(phrase_facts["tempo_scales"]) == 2


def test_transparent_cut_notes_name_the_exact_delta(tmp_path: Path) -> None:
    path = tmp_path / "CUT_NOTES.md"
    v9.write_cut_notes(
        path,
        [
            {
                "filename": "A_single-speed.flac",
                "label": "single-speed",
                "mechanism": "one constant donor-band tempo scale",
                "why": "test a single pitch-fader setting",
                "facts": {"tempo_scales": [1.01], "phase_resets": []},
            }
        ],
        common_note="Only the timing law changes.",
    )
    text = path.read_text(encoding="utf-8")
    assert "not being asked to guess what changed" in text
    assert "A_single-speed.flac" in text
    assert "one constant donor-band tempo scale" in text
    assert "Only the timing law changes." in text
