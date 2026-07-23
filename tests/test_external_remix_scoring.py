from earcrate.app import EarcrateCore


def _held_external_vocal_arrangement(external_mode: bool) -> dict:
    sections = []
    for idx in range(15):
        sections.append({
            "type": "verse",
            "bar_start": idx * 4,
            "bars": 4,
            "target_key": 0,
            "transition_in": {"type": "beatmatch_blend"},
            "layers": [
                {
                    "loop_id": "external::target",
                    "source_track_key": "external::target",
                    "external_ref": {"path": "target.wav", "pcm_sha": "abc"},
                    "world": "taste",
                    "role": "vocal",
                    "ear_role": "VOX_VERSE",
                    "bar_offset": 0,
                    "bar_len": 4,
                },
                {
                    "loop_id": f"bed:{idx}",
                    "source_track_key": f"bed:{idx}",
                    "world": "taste",
                    "role": "harmony",
                    "ear_role": "BED_CHORD",
                    "bar_offset": 0,
                    "bar_len": 4,
                },
            ],
        })
    params = {"chaos": 55, "drama": 70, "genre_whiplash": 55, "vocal_density": 70}
    if external_mode:
        params["external_foreground"] = {"external_ref": "external:target"}
    return {"params": params, "sections": sections}


def test_external_identity_anchor_is_not_counted_as_library_source_reuse():
    core = EarcrateCore()

    external_score = core.score_arrangement(_held_external_vocal_arrangement(True))
    ordinary_score = core.score_arrangement(_held_external_vocal_arrangement(False))

    assert external_score["source_reuse_scope"] == "library_bed"
    assert external_score["max_source_reuse"] == 1
    assert external_score["source_diversity"] == 1.0
    assert external_score["veto"] is False

    assert ordinary_score["source_reuse_scope"] == "all_layers"
    assert ordinary_score["max_source_reuse"] == 15
    assert ordinary_score["veto"] is True


def test_external_identity_anchor_is_not_a_turnover_warning():
    core = EarcrateCore()

    external_gate = core.taste_arrangement_gate(_held_external_vocal_arrangement(True))
    ordinary_gate = core.taste_arrangement_gate(_held_external_vocal_arrangement(False))

    assert external_gate["metrics"]["source_turnover_scope"] == "library_bed"
    assert external_gate["metrics"]["max_source_run_s"] == 8.0
    assert not any("one source dominates" in warning for warning in external_gate["warnings"])

    assert ordinary_gate["metrics"]["source_turnover_scope"] == "all_layers"
    assert ordinary_gate["metrics"]["max_source_run_s"] == 120.0
    assert any("one source dominates" in warning for warning in ordinary_gate["warnings"])
