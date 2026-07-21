from __future__ import annotations

import numpy as np

from earcrate.live.instrumentation import (
    LiveActivityRecorder,
    live_activity_delta,
    live_activity_scope,
    live_record_activity,
)
from earcrate.live.playback import (
    LiveAudioCallback,
    LiveSoundDevicePlayer,
    live_audio_device_capability,
)
from earcrate.live.stream import LIVE_PHRASE_BUFFER_KIND, LIVE_PHRASE_BUFFER_SCHEMA_VERSION
from earcrate.midi.model import midi_sha256_json


def _phrase(frames: int, value: float, *, sample_rate: int = 8_000, ordinal: int = 0) -> dict:
    audio = np.full((frames, 2), value, dtype=np.float32)
    recorder = LiveActivityRecorder()
    before = recorder.snapshot()
    with live_activity_scope(recorder, "control"):
        live_record_activity("planning", detail={"synthetic_phrase": ordinal})
    with live_activity_scope(recorder, "phrase_render"):
        live_record_activity("binding", detail={"synthetic_phrase": ordinal})
        live_record_activity("sample_decode", detail={"synthetic_phrase": ordinal})
    receipt = {
        "schema_version": LIVE_PHRASE_BUFFER_SCHEMA_VERSION,
        "kind": LIVE_PHRASE_BUFFER_KIND,
        "complete": True,
        "sample_rate": sample_rate,
        "channels": 2,
        "frames": frames,
        "selected_event_count": 1,
        "executed_event_count": 1,
        "truncated_event_count": 0,
        "refused_event_count": 0,
        "ordinal": ordinal,
        "activity_delta": live_activity_delta(before, recorder.snapshot()),
    }
    receipt["phrase_sha256"] = midi_sha256_json(receipt)
    return {"audio": audio, "receipt": receipt}


def test_audio_callback_crosses_prepared_phrase_boundary_without_work(tmp_path) -> None:
    callback = LiveAudioCallback(sample_rate=8_000, block_frames=8, maximum_queued_phrases=2)
    first = _phrase(10, 0.25, ordinal=1)
    second = _phrase(7, -0.5, ordinal=2)
    callback.queue_phrase(first)
    callback.queue_phrase(second)

    one = np.empty((8, 2), dtype=np.float32)
    two = np.empty((8, 2), dtype=np.float32)
    three = np.empty((8, 2), dtype=np.float32)
    callback.render_into(one)
    callback.render_into(two)
    callback.render_into(three)

    delivered = np.concatenate([one, two, three], axis=0)
    expected = np.concatenate([first["audio"], second["audio"], np.zeros((7, 2), dtype=np.float32)], axis=0)
    assert delivered.shape == expected.shape
    assert float(np.max(np.abs(delivered - expected))) == 0.0
    receipt = callback.receipt()
    assert receipt["frames_from_phrases"] == 17
    assert receipt["silence_frames"] == 7
    assert receipt["underrun_count"] == 1
    assert receipt["callback_planning_count"] == 0
    assert receipt["callback_library_search_count"] == 0
    assert receipt["callback_sample_decode_count"] == 0
    assert receipt["callback_binding_count"] == 0
    assert receipt["completed_phrase_count"] == 2
    assert len(receipt["completed_phrases"]) == 2


def test_audio_callback_refuses_queue_overflow() -> None:
    callback = LiveAudioCallback(sample_rate=8_000, block_frames=8, maximum_queued_phrases=1)
    callback.queue_phrase(_phrase(8, 0.1))
    try:
        callback.queue_phrase(_phrase(8, 0.2, ordinal=2))
    except Exception as exc:
        assert "full" in str(exc)
    else:
        raise AssertionError("live audio callback accepted a queue overflow")


class _FakeOutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_optional_device_host_is_a_thin_callback_wrapper() -> None:
    callback = LiveAudioCallback(sample_rate=8_000, block_frames=8)
    callback.queue_phrase(_phrase(8, 0.4))
    created = []

    def factory(**kwargs):
        stream = _FakeOutputStream(**kwargs)
        created.append(stream)
        return stream

    player = LiveSoundDevicePlayer(callback, output_stream_factory=factory)
    assert len(created) == 1
    stream = created[0]
    assert stream.kwargs["samplerate"] == 8_000
    assert stream.kwargs["blocksize"] == 8
    assert stream.kwargs["channels"] == 2
    outdata = np.empty((8, 2), dtype=np.float32)
    stream.kwargs["callback"](outdata, 8, None, None)
    assert float(np.max(np.abs(outdata - 0.4))) == 0.0
    player.start()
    player.stop()
    player.close()
    assert stream.started and stream.stopped and stream.closed


def test_audio_device_capability_is_optional_and_local() -> None:
    capability = live_audio_device_capability()
    assert capability["requires_gpu"] is False
    assert capability["requires_network"] is False
    assert capability["requires_cloud"] is False
    assert capability["queue_model"] == "single_producer_single_consumer_fixed_ring"
    assert capability["completion_history"] == "fixed_ring"
