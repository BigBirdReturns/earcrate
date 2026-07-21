from __future__ import annotations

from pathlib import Path

import numpy as np

from earcrate.live.crate import live_compile_crate_atlas
from earcrate.live.performance import LivePerformanceEngine
from earcrate.midi.codec import midi_read
from test_live_crate_runtime import _atoms, _write_source


def _crate(tmp_path: Path) -> dict:
    source = tmp_path / "source.mid"
    _write_source(source)
    return live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        sample_rate=8_000,
        compile_sfz=False,
    )["atlas"]


def test_integrated_host_prepares_persona_switched_phrases_and_callback_only_copies(tmp_path: Path) -> None:
    host = LivePerformanceEngine(
        _crate(tmp_path),
        persona="club",
        seed=37,
        block_frames=257,
        maximum_queued_phrases=2,
        beam_width=10,
        candidate_limit=6,
    )
    host.queue_control({"command": "set_persona", "value": "pretty_lights"})
    first = host.prepare_next_phrase()
    assert first["absolute_start_bar_index"] == 0
    assert first["persona"] == "pretty_lights"
    assert first["library_materials_scanned"] == 0

    # The audio callback starts consuming phrase one while the control thread
    # prepares phrase two. No planner or library lookup is called here.
    outdata = np.empty((257, 2), dtype=np.float32)
    host.callback.render_into(outdata)
    assert np.isfinite(outdata).all()

    host.queue_control({"command": "set_persona", "value": "girl_talk"})
    host.queue_control({"command": "force_technique", "value": "hard_cut"})
    second = host.prepare_next_phrase()
    assert second["absolute_start_bar_index"] == 4
    assert second["persona"] == "girl_talk"
    assert all(operator == "hard_cut" for operator in second["operators"])

    receipt = host.receipt()
    assert receipt["planning_count"] == 2
    assert receipt["current_state"]["current_bar_index"] == 8
    assert receipt["current_state"]["current_persona"] == "girl_talk"
    assert receipt["library_scans_after_initialization"] == 0
    assert receipt["gpu_calls_after_initialization"] == 0
    assert receipt["network_calls_after_initialization"] == 0
    assert receipt["cloud_calls_after_initialization"] == 0
    assert receipt["audio_callback"]["callback_planning_count"] == 0
    assert receipt["audio_callback"]["callback_library_search_count"] == 0
    assert receipt["audio_callback"]["callback_sample_decode_count"] == 0
    assert receipt["audio_callback"]["callback_binding_count"] == 0


def test_prepare_is_transactional_when_audio_queue_is_full(tmp_path: Path) -> None:
    host = LivePerformanceEngine(
        _crate(tmp_path),
        persona="club",
        seed=5,
        maximum_queued_phrases=1,
        beam_width=8,
        candidate_limit=5,
    )
    host.prepare_next_phrase()
    state_before = host.state["state_sha256"]
    host.queue_control({"command": "set_persona", "value": "minimal"})
    try:
        host.prepare_next_phrase()
    except Exception as exc:
        assert "full" in str(exc)
    else:
        raise AssertionError("live host advanced despite a full audio queue")
    assert host.state["state_sha256"] == state_before
    assert len(host.pending_controls) == 1
    assert host.planning_count == 1
