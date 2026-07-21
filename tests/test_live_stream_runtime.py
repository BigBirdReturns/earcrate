from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from earcrate.live.crate import live_compile_crate_atlas
from earcrate.live.engine import live_engine_new
from earcrate.live.stream import (
    LiveBlockStream,
    live_render_next_phrase,
    live_stream_capability,
)
from earcrate.midi.codec import midi_read
from earcrate.rack.render_fix import rack_render_ledger
from test_live_crate_runtime import _atoms, _write_source


def test_prepared_phrase_stream_matches_exact_rack_renderer(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    compiled = live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        taste_profile="test",
        sample_rate=8_000,
        compile_sfz=False,
    )
    crate = compiled["atlas"]
    state = live_engine_new(crate["live_material_atlas"], persona="club", seed=81)
    phrase = live_render_next_phrase(
        crate,
        state,
        controls=[
            {"command": "set_persona", "value": "pretty_lights"},
            {"command": "force_technique", "value": "hard_cut"},
        ],
        beam_width=12,
        candidate_limit=6,
        target_peak=0.90,
    )
    receipt = phrase["receipt"]
    assert receipt["complete"] is True
    assert receipt["selected_event_count"] == receipt["executed_event_count"]
    assert receipt["truncated_event_count"] == receipt["refused_event_count"] == 0
    assert receipt["materials_scanned_during_render"] == 0
    assert receipt["samples_decoded_during_callback"] == 0
    activity = receipt["activity_delta"]
    assert activity["domains"]["control"]["planning"] == 1
    assert activity["domains"]["phrase_render"]["binding"] == 1
    assert activity["domains"]["phrase_render"]["sample_decode"] > 0
    assert activity["domains"]["audio_callback"]["planning"] == 0
    assert activity["domains"]["audio_callback"]["library_search"] == 0
    assert activity["domains"]["audio_callback"]["sample_decode"] == 0
    assert activity["domains"]["audio_callback"]["binding"] == 0
    assert phrase["next_state"]["current_persona"] == "pretty_lights"
    assert all(operator == "hard_cut" for operator in receipt["operators"])

    reference_path = tmp_path / "reference.wav"
    reference = rack_render_ledger(
        phrase["midi_lowering"]["ledger"],
        phrase["binding"],
        crate["rack_revisions"],
        reference_path,
        sample_rate=8_000,
        target_peak=0.90,
    )
    assert reference["complete_execution"] is True
    reference_audio, rate = sf.read(reference_path, dtype="float32", always_2d=True)
    assert rate == receipt["sample_rate"]
    assert reference_audio.shape == phrase["audio"].shape
    assert float(np.max(np.abs(reference_audio - phrase["audio"]))) < 1e-7


def test_block_callback_copies_prepared_frames_without_planning_or_decode(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    _write_source(source)
    crate = live_compile_crate_atlas(
        midi_read(source),
        _atoms(tmp_path),
        tmp_path / "crate",
        sample_rate=8_000,
        compile_sfz=False,
    )["atlas"]
    state = live_engine_new(crate["live_material_atlas"], persona="pretty_lights", seed=9)
    phrase = live_render_next_phrase(crate, state, beam_width=10, candidate_limit=6)
    streamer = LiveBlockStream(sample_rate=8_000, block_frames=257)
    streamer.load_phrase(phrase)
    blocks = [streamer.read_block() for _ in range(math.ceil(phrase["audio"].shape[0] / 257))]
    delivered = np.concatenate(blocks, axis=0)[: phrase["audio"].shape[0]]
    assert float(np.max(np.abs(delivered - phrase["audio"]))) == 0.0
    stream_receipt = streamer.receipt()
    assert stream_receipt["underrun_count"] == 0
    assert stream_receipt["callback_planning_count"] == 0
    assert stream_receipt["callback_library_search_count"] == 0
    assert stream_receipt["callback_sample_decode_count"] == 0
    assert stream_receipt["callback_binding_count"] == 0
    assert stream_receipt["completed_phrase_count"] == 1
    assert len(stream_receipt["phrase_history"]) == 1

    underrun = streamer.read_block()
    assert not np.any(underrun)
    assert streamer.receipt()["underrun_count"] == 1

    next_phrase = live_render_next_phrase(
        crate,
        phrase["next_state"],
        controls=[{"command": "set_density", "value": 0.72}],
        beam_width=10,
        candidate_limit=6,
    )
    streamer.load_phrase(next_phrase)
    assert streamer.ready is True
    assert next_phrase["next_state"]["current_bar_index"] == 8
    assert next_phrase["next_state"]["density"] == 0.72


def test_live_stream_capability_is_local_and_callback_safe() -> None:
    capability = live_stream_capability()
    assert capability["ready"] is True
    assert capability["requires_gpu"] is False
    assert capability["requires_network"] is False
    assert capability["requires_cloud"] is False
    contract = capability["callback_contract"]
    assert sorted(contract["forbidden"]) == ["binding", "library_search", "planning", "sample_decode"]
    assert sorted(contract["allowed"]) == ["float32_frame_copy", "prepared_buffer_swap", "silence_on_underrun"]
