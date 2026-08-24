from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from earcrate.plan.fixture_diversity import fixture_projection
from earcrate.plan.fixture_slot_qualification import probe_candidate_slot_census
from earcrate.plan.islands import source_pool_identity


def _atom(source, atom, role, bpm, key):
    ear = {
        "foreground": "VOX_HOOK",
        "floor": "DRUM_BREAK",
        "bass": "BASS_RIFF",
        "spark": "DROP_HIT",
    }[role]
    render = {
        "foreground": "vocal",
        "floor": "drum_anchor",
        "bass": "bass",
        "spark": "texture",
    }[role]
    return {
        "id": atom,
        "atom_id": atom,
        "source_track_key": source,
        "ear_role": ear,
        "render_role": render,
        "role": render,
        "bpm": bpm,
        "key_root": key,
        "bars": 4,
        "start_s": 0.0,
        "end_s": 10.0,
        "source_audio_sha256": source + "-pcm",
        "score": 0.8,
        "hook_score": 0.8,
    }


class _Core:
    def __init__(self, pool):
        self.pool = list(pool)
        self.raw_calls = 0
        self.exact_calls = 0

    def approved_atom_pool(self, _profile):
        return list(self.pool)

    def taste_feasible_pool(self, pool, bpm, key, _params):
        out = [
            dict(item)
            for item in pool
            if abs(float(item["bpm"]) - float(bpm)) < 1e-9
            and int(item["key_root"]) % 12 == int(key) % 12
        ]
        return out, {
            "have": {"sources": len({item["source_track_key"] for item in out})}
        }

    def compose_taste_arrangement(self, *_args, **_kwargs):
        self.exact_calls += 1
        raise AssertionError("slot probe invoked exact-pool publication")

    def _ordinary_compose_taste_arrangement(self, pool, params, seed):
        owner = getattr(self, "_core", self)
        owner.raw_calls += 1
        deck = self.choose_taste_deck(pool, params)
        return {
            "bpm": deck["render_bpm"],
            "target_key": deck["target_key"],
            "seed": seed,
            "params": dict(params),
            "dj_compiler": {"version": "probe"},
            "sections": [
                {
                    "bar_start": index * 4,
                    "bars": 4,
                    "type": "sustain",
                    "layers": [
                        {
                            "role": item["render_role"],
                            "source_track_key": item["source_track_key"],
                            "atom_id": item["atom_id"],
                        }
                    ],
                }
                for index, item in enumerate(deck["pool"])
            ],
        }

    def choose_taste_deck(self, pool, _params):
        return {"pool": list(pool), "render_bpm": 0.0, "target_key": 0}


def _probe_fixture():
    pool = []
    islands = []
    cursor = 0.0
    for index, bpm, key in ((0, 120.0, 0), (1, 130.0, 5)):
        sources = []
        for role in ("foreground", "floor", "bass", "spark"):
            source = f"s{index}-{role}"
            sources.append(source)
            pool.append(_atom(source, f"a{index}-{role}", role, bpm, key))
        islands.append(
            {
                "island_id": f"island-{index}",
                "deck_id": f"deck-{index}",
                "target_bpm": bpm,
                "target_key": key,
                "capacity_s": 40.0,
                "allocated_duration_s": 40.0,
                "start_s": cursor,
                "end_s": cursor + 40.0,
                "source_include_ids": sources,
                "required_roles": ["foreground", "floor", "bass", "spark"],
            }
        )
        cursor += 40.0
    candidate = {
        "kind": "earcrate_fixture_candidate",
        "profile": "girl_talk_v1",
        "persona": "remix_prettylights_v1",
        "phrase_playback_law": "proof001_phrase_law",
        "source_pool_sha256": source_pool_identity(pool),
        "source_exclude_ids": [],
        "transform_policy": {
            "unchanged": True,
            "stretch_budget": 8.0,
            "pitch_shift_budget": 2,
        },
        "turnover_policy": {"unchanged": True},
        "transition": {
            "technique": "equal_power",
            "phrase_boundary_required": True,
        },
        "duration_s": 55.0,
        "phrase_bars": 4,
        "seed": 7,
        "islands": islands,
        "transitions": [],
    }
    candidate["fixture_sha256"] = fixture_projection(candidate)["fixture_identity"]
    candidate["fixture_id"] = "probe-" + candidate["fixture_sha256"][:12]
    return pool, candidate


def test_slot_probe_uses_the_ordinary_composer_and_never_publishes():
    pool, candidate = _probe_fixture()
    core = _Core(pool)
    receipt = probe_candidate_slot_census(core, candidate)
    assert core.raw_calls == 2
    assert core.exact_calls == 0
    assert len(receipt["islands"]) == 2
    assert all(row["slot_count"] == 4 for row in receipt["islands"])
    assert receipt["publication_authority"] is False
    text = json.dumps(receipt, sort_keys=True)
    assert "source_track_key" not in text
    assert "atom_id" not in text


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "earcrate_fixture_slots.py"
    spec = importlib.util.spec_from_file_location("_earcrate_fixture_slots_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _matrix_and_candidate():
    from test_fixture_slot_qualification import (
        _census,
        _matrix,
        _repair_fixture,
    )

    decks, candidate, census = _repair_fixture()
    return _matrix(decks), candidate, census


def test_slot_qualification_cli_refuses_alias_and_writes_a_bound_candidate(tmp_path):
    cli = _load_cli()
    matrix, candidate, census = _matrix_and_candidate()
    matrix_path = tmp_path / "matrix.json"
    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    census_path.write_text(json.dumps(census), encoding="utf-8")
    before = candidate_path.read_bytes()
    assert (
        cli.main(
            [
                "qualify",
                str(matrix_path),
                str(candidate_path),
                str(census_path),
                "--candidate-out",
                str(candidate_path),
                "--receipt",
                str(tmp_path / "receipt.json"),
            ]
        )
        == 2
    )
    assert candidate_path.read_bytes() == before
    output = tmp_path / "qualified.json"
    receipt_path = tmp_path / "receipt.json"
    assert (
        cli.main(
            [
                "qualify",
                str(matrix_path),
                str(candidate_path),
                str(census_path),
                "--candidate-out",
                str(output),
                "--receipt",
                str(receipt_path),
                "--max-source-events",
                "3",
            ]
        )
        == 0
    )
    qualified = json.loads(output.read_text(encoding="utf-8"))
    report = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert report["complete"] is True
    assert qualified["fixture_sha256"] == report["qualified_fixture_sha256"]
    assert report["qualified_candidate_file"]["file_sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    for name, path in (
        ("survival_matrix", matrix_path),
        ("candidate", candidate_path),
        ("slot_census", census_path),
    ):
        assert report["input_files"][name]["file_sha256"] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
