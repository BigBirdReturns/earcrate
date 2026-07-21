from __future__ import annotations

import ast
import inspect
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from earcrate.live.crate import live_compile_crate_atlas
from earcrate.live.instrumentation import (
    LiveActivityRecorder,
    LiveCallbackPurityError,
    live_activity_scope,
)
from earcrate.live.model import live_new_state
from earcrate.live.performance import LivePerformanceEngine
from earcrate.live.planner import live_atlas_from_midi, live_plan_next
from earcrate.live.playback import LiveAudioCallback
from earcrate.midi.codec import midi_read
from test_live_crate_runtime import _atoms, _write_source


def test_real_calls_increment_measured_domains_and_callback_stays_zero(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    ledger = midi_read(source)
    recorder = LiveActivityRecorder(event_capacity=1024)
    atoms = _atoms(tmp_path)
    compiled = live_compile_crate_atlas(
        ledger,
        atoms,
        tmp_path / "crate",
        sample_rate=8_000,
        compile_sfz=False,
        activity_recorder=recorder,
    )
    offline = recorder.snapshot()
    assert offline["domains"]["offline_compile"]["library_search"] == 1
    assert offline["domains"]["offline_compile"]["material_scan"] == len(atoms)
    assert offline["domains"]["offline_compile"]["binding"] == 1

    host = LivePerformanceEngine(
        compiled["atlas"],
        persona="club",
        seed=101,
        block_frames=257,
        activity_event_capacity=2048,
        beam_width=10,
        candidate_limit=6,
    )
    first = host.prepare_next_phrase()
    host.callback.render_into(np.empty((257, 2), dtype=np.float32))
    second = host.prepare_next_phrase()
    host.callback.render_into(np.empty((257, 2), dtype=np.float32))
    assert first["activity_delta"]["domains"]["control"]["planning"] == 1
    assert second["activity_delta"]["domains"]["control"]["planning"] == 1
    measured = host.receipt()["activity_receipt"]
    assert measured["domains"]["control"]["planning"] == 2
    assert measured["domains"]["phrase_render"]["binding"] == 2
    assert measured["domains"]["phrase_render"]["sample_decode"] > 0
    callback = measured["domains"]["audio_callback"]
    assert callback["planning"] == 0
    assert callback["library_search"] == 0
    assert callback["sample_decode"] == 0
    assert callback["binding"] == 0


def test_instrumentation_detects_an_actual_planner_call_in_callback_domain(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    atlas = live_atlas_from_midi(midi_read(source))
    state = live_new_state(atlas, persona="club", seed=3)
    recorder = LiveActivityRecorder()
    try:
        with live_activity_scope(recorder, "audio_callback"):
            live_plan_next(atlas, state, horizon_bars=4, commit_bars=4, beam_width=4, candidate_limit=4)
    except LiveCallbackPurityError as exc:
        assert "planning" in str(exc)
    else:
        raise AssertionError("an actual planner call inside the callback domain was not detected")
    receipt = recorder.snapshot()
    assert receipt["callback_violation_count"] == 1
    assert receipt["domains"]["audio_callback"]["planning"] == 1


def test_concurrent_plans_do_not_share_risk_state(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    atlas = live_atlas_from_midi(midi_read(source))
    low = live_new_state(atlas, persona="club", seed=17, risk=0.0)
    high = live_new_state(atlas, persona="club", seed=17, risk=1.0)

    def plan(state: dict) -> tuple[str, tuple[float, ...]]:
        result = live_plan_next(
            atlas,
            state,
            horizon_bars=8,
            commit_bars=4,
            beam_width=12,
            candidate_limit=6,
        )["plan"]
        return (
            str(result["plan_sha256"]),
            tuple(float(row["score_terms"]["risk_fit"]) for row in result["decisions"]),
        )

    expected_low = plan(low)
    expected_high = plan(high)
    assert expected_low != expected_high
    inputs = [low, high] * 12
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(plan, inputs))
    assert results[0::2] == [expected_low] * 12
    assert results[1::2] == [expected_high] * 12


def test_callback_hot_path_is_a_bounded_copy_machine() -> None:
    methods = (
        LiveAudioCallback._take_next_phrase,
        LiveAudioCallback.render_into,
        LiveAudioCallback._record_completion,
    )
    forbidden_nodes = (
        ast.For,
        ast.AsyncFor,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.With,
        ast.AsyncWith,
    )
    forbidden_names = {
        "approved_atom_pool",
        "live_plan_next",
        "rack_compile_binding",
        "_load_zone_audio",
        "decode_audio",
    }
    allowed_attribute_calls = {
        "asarray",
        "fill",
        "_record_completion",
        "_take_next_phrase",
    }
    allowed_name_calls = {
        "LiveError",
        "int",
        "live_activity_pop",
        "live_activity_swap",
        "min",
        "str",
    }
    for method in methods:
        source = textwrap.dedent(inspect.getsource(method))
        tree = ast.parse(source)
        assert not any(isinstance(node, forbidden_nodes) for node in ast.walk(tree))
        assert not any(name in source for name in forbidden_names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr in allowed_attribute_calls
            elif isinstance(node.func, ast.Name):
                assert node.func.id in allowed_name_calls
            else:
                raise AssertionError(f"unsupported callback call shape: {ast.dump(node.func)}")
    callback = LiveAudioCallback(sample_rate=8_000, block_frames=64, completion_capacity=8)
    assert not hasattr(callback, "_lock")
    assert len(callback._completion_hashes) == 8
    assert callback._callback_activity_context.domain == "audio_callback"
