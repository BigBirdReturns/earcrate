from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.generative_floor import load_json, validate_provider_catalog


def test_midi_sag_is_a_distinct_compositional_accompaniment_organ() -> None:
    catalog = load_json(ROOT / "configs" / "generative_floor" / "providers.v1.json")
    assert validate_provider_catalog(catalog) == catalog["catalog_sha256"]
    provider = next(row for row in catalog["providers"] if row["provider_id"] == "midi-sag")
    assert provider["provider_class"] == "specialist_model"
    assert "vocal_to_bgm" in provider["capabilities"]
    assert provider["model_families"] == [
        "Singing-Vocal-Beat-Tracking",
        "GAME",
        "AccoMontage2",
        "MuseControlLite",
        "Stable-Audio-Open-1.0",
    ]
    assert provider["authority"]["canonical_musical_write"] is False
    assert provider["authority"]["human_acceptance"] is False


def test_beggin_campaign_compares_black_box_and_compositional_accompaniment() -> None:
    campaign = load_json(ROOT / "configs" / "generative_floor" / "beggin-suno-bones.v1.json")
    assert len(campaign["tasks"]) == 12
    task = next(row for row in campaign["tasks"] if row["task_id"] == "GF12-midi-sag-compositional-bgm")
    assert task["task_mode"] == "vocal_to_bgm"
    assert task["provider_candidates"] == ["midi-sag", "ace-step-1.5"]
    assert task["conditioning_source_ids"] == ["four_seasons_vocals", "beggin_section_plan"]
    assert "traceable intermediate usefulness" in task["acceptance_dimensions"]
