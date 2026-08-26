from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import earcrate.plan.fixture_slot_qualification as slotq
import earcrate.plan.fixture_slot_binding as slot_binding
from earcrate.plan.fixture_slot_qualification import (
    INDETERMINATE_ACTION,
    FixtureSlotQualificationError,
    build_exact_pool_slot_census,
    install_fixture_slot_census,
    qualify_fixture_candidate,
    semantic_sha256,
)


def _atom(source, atom, role, bpm=120.0, key=0):
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
        "score": 0.8,
        "hook_score": 0.7,
        "high_share": 0.1,
        "path": f"X:/private/{source}.wav",
        "artist": "private",
        "title": source,
    }


class _CensusCore:
    def __init__(self, pool):
        self.pool = list(pool)

    def approved_atom_pool(self, _profile):
        return list(self.pool)

    def taste_feasible_pool(self, pool, bpm, key, _params):
        rows = [
            dict(item)
            for item in pool
            if abs(float(item.get("bpm") or 0.0) - float(bpm)) < 1e-9
            and int(item.get("key_root") or 0) % 12 == int(key) % 12
        ]
        return rows, {
            "pool_size": len(rows),
            "have": {
                "sources": len({row["source_track_key"] for row in rows})
            },
        }

    def atom_edge_score(self, _candidate, _counterpart, _relation, *_args):
        return 0.8, {"reason": "fixture"}

    def _ordinary_compose_taste_arrangement(self, pool, params, seed):
        by_role = {}
        for item in pool:
            by_role.setdefault(item["render_role"], item)
        layers = []
        for role in ("drum_anchor", "vocal", "bass", "texture"):
            item = by_role.get(role)
            if item is None:
                continue
            layers.append(
                {
                    "loop_id": item["id"],
                    "atom_id": item["atom_id"],
                    "ear_role": item["ear_role"],
                    "role": role,
                    "source_track_key": item["source_track_key"],
                    "bar_offset": 0,
                    "bar_len": 4,
                    "gain_db": -8.0,
                }
            )
        return {
            "bpm": float(params["exact_target_bpm"]),
            "target_key": int(params["exact_target_key"]),
            "seed": seed,
            "params": dict(params),
            "dj_compiler": {"version": "fixture"},
            "sections": [
                {
                    "bar_start": 0,
                    "bars": 4,
                    "type": "sustain",
                    "target_key": int(params["exact_target_key"]),
                    "layers": layers,
                }
            ],
        }


def _candidate():
    candidate = {
        "kind": "earcrate_fixture_candidate",
        "fixture_id": "pending",
        "fixture_sha256": "pending",
        "profile": "girl_talk_v1",
        "persona": "remix_prettylights_v1",
        "phrase_playback_law": "proof001_phrase_law",
        "source_pool_sha256": "pool",
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
        "seed": 7,
        "duration_s": 20.0,
        "islands": [
            {
                "island_id": "a",
                "deck_id": "deck-a",
                "target_bpm": 120.0,
                "target_key": 0,
                "capacity_s": 20.0,
                "allocated_duration_s": 10.0,
                "source_include_ids": ["bass", "floor"],
                "required_roles": ["foreground", "floor"],
                "min_sources": 2,
                "max_sources": 2,
            },
            {
                "island_id": "b",
                "deck_id": "deck-b",
                "target_bpm": 130.0,
                "target_key": 5,
                "capacity_s": 20.0,
                "allocated_duration_s": 10.0,
                "source_include_ids": ["spark", "vox"],
                "required_roles": ["bass", "spark"],
                "min_sources": 2,
                "max_sources": 2,
            },
        ],
        "transitions": [],
    }
    from earcrate.plan.fixture_diversity import fixture_projection

    identity = str(fixture_projection(candidate)["fixture_identity"])
    candidate["fixture_id"] = f"fixture-{identity[:12]}"
    candidate["fixture_sha256"] = identity
    return candidate


def _source_rows():
    return [
        {
            "source_id": "bass",
            "planner_role_capabilities": ["bass"],
        },
        {
            "source_id": "floor",
            "planner_role_capabilities": ["floor"],
        },
        {
            "source_id": "spark",
            "planner_role_capabilities": ["spark"],
        },
        {
            "source_id": "vox",
            "planner_role_capabilities": ["foreground"],
        },
    ]


def _seal_census(census):
    census["slot_census_sha256"] = slot_binding._census_identity(census)
    return census


def _census_campaign():
    candidate = _candidate()
    rows = [
        _seal_census(
            {
                "island_id": "a",
                "deck_id": "deck-a",
                "render_bpm": 120.0,
                "target_key": 0,
                "allocated_duration_s": 10.0,
                "max_source_events": 2,
                "candidate_required_roles": ["floor", "foreground"],
                "candidate_min_sources": 2,
                "candidate_max_sources": 2,
                "sources": copy.deepcopy(_source_rows()),
                "slots": [
                    {
                        "slot_key": [0, 0],
                        "compatible_sources": ["floor", "vox"],
                    },
                    {
                        "slot_key": [4, 0],
                        "compatible_sources": ["floor", "vox"],
                    },
                ],
            }
        ),
        _seal_census(
            {
                "island_id": "b",
                "deck_id": "deck-b",
                "render_bpm": 130.0,
                "target_key": 5,
                "allocated_duration_s": 10.0,
                "max_source_events": 2,
                "candidate_required_roles": ["bass", "spark"],
                "candidate_min_sources": 2,
                "candidate_max_sources": 2,
                "sources": copy.deepcopy(_source_rows()),
                "slots": [
                    {
                        "slot_key": [0, 0],
                        "compatible_sources": ["bass", "floor"],
                    },
                    {
                        "slot_key": [4, 0],
                        "compatible_sources": ["spark", "vox"],
                    },
                ],
            }
        ),
    ]
    campaign = {
        "candidate_fixture_sha256": candidate["fixture_sha256"],
        "source_pool_sha256": "pool",
        "source_universe_sha256": semantic_sha256(
            ["bass", "floor", "spark", "vox"]
        ),
        "source_count": 4,
        "policy_identity": slot_binding._core._policy_identity(candidate),
        "islands": rows,
    }
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    return campaign


def test_slot_census_uses_exact_assignment_reachability_and_is_public_safe():
    pool = [
        _atom("floor", "floor-a", "floor"),
        _atom("vox", "vox-a", "foreground"),
        _atom("bass", "bass-a", "bass"),
        _atom("spark", "spark-a", "spark"),
    ]
    core = _CensusCore(pool)
    arrangement = core._ordinary_compose_taste_arrangement(
        pool,
        {
            "exact_target_bpm": 120.0,
            "exact_target_key": 0,
            "target_seconds": 10.0,
            "taste_profile": "girl_talk_v1",
            "phrase_playback_law": "proof001_phrase_law",
            "stretch_budget": 8.0,
            "pitch_shift_budget": 2,
        },
        7,
    )
    census = build_exact_pool_slot_census(
        core,
        arrangement,
        pool,
        arrangement["params"],
        7,
        island_id="only",
    )
    assert census["slot_count"] == 4
    assert census["slot_count_by_role_family"] == {
        "bass": 1,
        "floor": 1,
        "foreground": 1,
        "spark": 1,
    }
    capabilities = {
        row["source_id"]: row["planner_role_capabilities"]
        for row in census["sources"]
    }
    assert capabilities["bass"] == ["BASS_RIFF", "bass"]
    text = json.dumps(census, sort_keys=True).lower()
    for forbidden in ("x:/private", '"artist"', '"title"', '"path"'):
        assert forbidden not in text


def test_slot_qualified_partition_moves_constrained_sources_and_holds_contract():
    result = qualify_fixture_candidate(_candidate(), _census_campaign())
    assert result["complete"] is True
    partition = {
        row["island_id"]: row["source_include_ids"]
        for row in result["candidate"]["islands"]
    }
    assert partition == {"a": ["floor", "vox"], "b": ["bass", "spark"]}
    assert sorted(source for rows in partition.values() for source in rows) == [
        "bass",
        "floor",
        "spark",
        "vox",
    ]
    counts = {}
    for row in result["slot_assignment"]:
        key = (row["island_id"], row["source_id"])
        counts[key] = counts.get(key, 0) + 1
    assert max(counts.values()) <= 2
    assert result["candidate"]["fixture_sha256"] != _candidate()["fixture_sha256"]
    assert result["solver"]["constraints"][-2:] == [
        "candidate_min_max_sources",
        "candidate_required_roles",
    ]


def test_required_roles_and_source_bounds_are_solver_laws():
    missing_role = _candidate()
    missing_role["islands"][0]["required_roles"] = ["bass"]
    campaign = _census_campaign()
    campaign["islands"][0]["candidate_required_roles"] = ["bass"]
    for source in campaign["islands"][0]["sources"]:
        source["planner_role_capabilities"] = ["foreground"]
    for census in campaign["islands"]:
        _seal_census(census)
    campaign["campaign_sha256"] = slot_binding._campaign_identity(campaign)
    result = qualify_fixture_candidate(missing_role, campaign)
    assert result["complete"] is False
    assert result["impossibility_claimed"] is True
    assert result["failure_class"] == "required_role_capacity"

    impossible_bound = _candidate()
    impossible_bound["islands"][0]["min_sources"] = 1
    impossible_bound["islands"][0]["max_sources"] = 1
    bound_campaign = _census_campaign()
    bound_campaign["islands"][0]["candidate_min_sources"] = 1
    bound_campaign["islands"][0]["candidate_max_sources"] = 1
    _seal_census(bound_campaign["islands"][0])
    bound_campaign["candidate_fixture_sha256"] = impossible_bound["fixture_sha256"]
    bound_campaign["campaign_sha256"] = slot_binding._campaign_identity(bound_campaign)
    result = qualify_fixture_candidate(impossible_bound, bound_campaign)
    assert result["complete"] is False
    assert result["impossibility_claimed"] is False
    assert result["private_acceptance"] == INDETERMINATE_ACTION


def test_slot_qualification_is_identical_under_equivalent_input_permutations():
    first = qualify_fixture_candidate(_candidate(), _census_campaign())
    permuted = copy.deepcopy(_census_campaign())
    permuted["islands"].reverse()
    for island in permuted["islands"]:
        island["slots"].reverse()
        island["sources"].reverse()
        for slot in island["slots"]:
            slot["compatible_sources"].reverse()
        _seal_census(island)
    permuted["campaign_sha256"] = slot_binding._campaign_identity(permuted)
    second = qualify_fixture_candidate(_candidate(), permuted)
    assert first["candidate"] == second["candidate"]
    assert first["slot_assignment"] == second["slot_assignment"]


def test_explicit_hall_deficiency_is_proof_but_solver_bound_is_not():
    impossible = copy.deepcopy(_census_campaign())
    for island in impossible["islands"]:
        for slot in island["slots"]:
            slot["compatible_sources"] = [
                source for source in slot["compatible_sources"] if source != "bass"
            ]
        _seal_census(island)
    impossible["campaign_sha256"] = slot_binding._campaign_identity(impossible)
    proof = qualify_fixture_candidate(_candidate(), impossible)
    assert proof["complete"] is False
    assert proof["impossibility_claimed"] is True
    assert proof["failure_class"] == "role_capacity"

    def bounded_solver(**_kwargs):
        return SimpleNamespace(
            status=1,
            success=False,
            message="time limit",
            fun=None,
            x=None,
        )

    bound = qualify_fixture_candidate(
        _candidate(), _census_campaign(), _solver=bounded_solver
    )
    assert bound["complete"] is False
    assert bound["impossibility_claimed"] is False
    assert bound["private_acceptance"] == INDETERMINATE_ACTION


def test_census_identity_policy_and_content_are_fail_closed():
    tampered = _census_campaign()
    tampered["islands"][0]["slots"][0]["compatible_sources"].append("bass")
    try:
        qualify_fixture_candidate(_candidate(), tampered)
    except FixtureSlotQualificationError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("tampered census was accepted")

    wrong_policy = _census_campaign()
    wrong_policy["policy_identity"] = "wrong"
    wrong_policy["campaign_sha256"] = slot_binding._campaign_identity(wrong_policy)
    try:
        qualify_fixture_candidate(_candidate(), wrong_policy)
    except FixtureSlotQualificationError as exc:
        assert "policy identity" in str(exc)
    else:
        raise AssertionError("policy drift was accepted")


def test_refusal_wrapper_attaches_and_durably_receipts_census_without_changing_successes():
    class Refusal(RuntimeError):
        def __init__(self):
            super().__init__("refused")
            self.deficiency = {"failure_class": "role_capacity"}

    class Core:
        def __init__(self):
            self.runs = []

        def propose_island_set(self, params):
            if params.get("ok"):
                return {"ok": True, "sentinel": params["ok"]}
            raise Refusal()

        def _run_bundle_begin(self, attempt_type, request):
            self.runs.append(("begin", attempt_type, request))
            return {"run_id": "census-run", "path": "S:/census-run"}

        def _run_bundle_set_plan(self, *args, **kwargs):
            self.runs.append(("plan", args, kwargs))

        def _run_bundle_finish(self, *args, **kwargs):
            self.runs.append(("finish", args, kwargs))

    original_builder = slot_binding.build_fixture_slot_census_campaign
    slot_binding.build_fixture_slot_census_campaign = lambda _self, _params: {
        "kind": "earcrate_fixture_slot_census_campaign",
        "campaign_sha256": "census",
    }
    try:
        install_fixture_slot_census(Core)
        core = Core()
        assert core.propose_island_set({"ok": "same"}) == {
            "ok": True,
            "sentinel": "same",
        }
        try:
            core.propose_island_set({})
        except Refusal as exc:
            assert exc.deficiency["fixture_slot_census_campaign"]["campaign_sha256"] == "census"
            assert exc.deficiency["fixture_slot_census_run_id"] == "census-run"
            assert exc.deficiency["fixture_slot_census_run_bundle"] == "S:/census-run"
        else:
            raise AssertionError("refusal was not preserved")
        assert [row[0] for row in core.runs] == ["begin", "plan", "finish"]
    finally:
        slot_binding.build_fixture_slot_census_campaign = original_builder


def test_slot_qualification_cli_binds_inputs_and_refuses_output_alias(tmp_path):
    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "earcrate_slot_qualify.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_earcrate_slot_qualify_gate", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    candidate_path = tmp_path / "candidate.json"
    census_path = tmp_path / "census.json"
    output_path = tmp_path / "qualified.json"
    receipt_path = tmp_path / "receipt.json"
    candidate_path.write_text(json.dumps(_candidate()), encoding="utf-8")
    census_path.write_text(json.dumps(_census_campaign()), encoding="utf-8")
    assert module.main(
        [
            str(candidate_path),
            str(census_path),
            "--out-candidate",
            str(output_path),
            "--receipt",
            str(receipt_path),
        ]
    ) == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["complete"] is True
    assert receipt["candidate_input"]["capture_policy"].startswith("single_byte")
    before = candidate_path.read_bytes()
    assert module.main(
        [
            str(candidate_path),
            str(census_path),
            "--out-candidate",
            str(output_path),
            "--receipt",
            str(candidate_path),
        ]
    ) == 2
    assert candidate_path.read_bytes() == before
